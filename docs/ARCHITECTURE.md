# Architecture Proposal — Agent Handoff Kit

> **Design-only document.** Everything described here is a proposal for human
> review. No application code exists; no behaviour described below has been
> verified by tests or execution.

---

## 1. Scope recap

One job, two local worker processes (A and B), one recovery path.  
Stack: Python 3.9 standard library + SQLite 3 (shipped with CPython).  
No third-party packages, no paid providers, no cloud.  
Telegram integration is deferred; notes appear in §10.

---

## 2. Database schema

One SQLite file: `handoff.db`. All tables use `WITHOUT ROWID` only where
noted; otherwise the implicit rowid is the internal cursor and the named
`id` column is the business key.

```sql
-- 2.1  Jobs
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,      -- stable, caller-assigned, e.g. "job-001"
    input_hash   TEXT NOT NULL,         -- SHA-256 hex of the canonical input blob
    status       TEXT NOT NULL          -- see §3 for allowed values
                 CHECK(status IN (
                   'PENDING','CLAIMED','RUNNING',
                   'CHECKPOINT','DONE','FAILED','NEEDS_REVIEW')),
    owner        TEXT,                  -- worker id currently holding the lease, or NULL
    lease_expiry REAL,                  -- Unix timestamp (float); NULL when unclaimed
    generation   INTEGER NOT NULL DEFAULT 0,  -- monotone fence counter; increments on every claim/transfer
    checkpoint   TEXT,                  -- JSON blob: last completed step state, or NULL
    step_cursor  TEXT,                  -- name of the last completed step, or NULL
    failure_msg  TEXT,                  -- human-readable, set on FAILED or NEEDS_REVIEW
    evidence_ref TEXT,                  -- opaque reference to evidence (file path, hash, etc.)
    created_at   REAL NOT NULL,         -- Unix timestamp
    updated_at   REAL NOT NULL          -- Unix timestamp; updated on every write
);

-- 2.2  Transition history (append-only)
CREATE TABLE history (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    worker       TEXT,
    generation   INTEGER NOT NULL,
    note         TEXT,
    recorded_at  REAL NOT NULL
);

-- 2.3  Local action receipts (unique-keyed, idempotent)
CREATE TABLE receipts (
    action_key   TEXT PRIMARY KEY,      -- caller-assigned idempotency key, e.g. "job-001:step-validate:attempt-1"
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    step_name    TEXT NOT NULL,
    payload_hash TEXT NOT NULL,         -- SHA-256 of serialised action inputs
    result       TEXT,                  -- serialised result or NULL while pending
    committed_at REAL NOT NULL
);
```

### Index hints (deferred until implementation)

```sql
CREATE INDEX ix_jobs_status  ON jobs(status);
CREATE INDEX ix_history_job  ON history(job_id, seq);
CREATE INDEX ix_receipts_job ON receipts(job_id);
```

---

## 3. Status model and allowed transitions

```
              ┌──────────┐
              │  PENDING │   (job created, never claimed)
              └────┬─────┘
                   │ claim()
                   ▼
              ┌──────────┐
              │  CLAIMED │   (lease held, work not yet started)
              └────┬─────┘
                   │ begin_step()
                   ▼
              ┌──────────┐
              │  RUNNING │   (a step is executing under this lease)
              └────┬─────┘
           ┌───────┴──────────┐
           │ checkpoint()     │ fail()/unknown()
           ▼                  ▼
    ┌────────────┐      ┌─────────────┐
    │ CHECKPOINT │      │   FAILED    │
    └─────┬──────┘      └─────────────┘
          │ claim() / resume()
          ▼                           ┌──────────────┐
       RUNNING ──── unknown() ───────►│ NEEDS_REVIEW │
          │                           └──────────────┘
          │ complete()
          ▼
       ┌──────┐
       │ DONE │
       └──────┘
```

### Transition table

| From         | To           | Trigger            | Guard                                |
|--------------|--------------|--------------------|--------------------------------------|
| PENDING      | CLAIMED      | `claim()`          | status == PENDING                    |
| CLAIMED      | RUNNING      | `begin_step()`     | status == CLAIMED, gen matches       |
| RUNNING      | CHECKPOINT   | `checkpoint()`     | status == RUNNING, gen matches       |
| RUNNING      | FAILED       | `fail()`           | status == RUNNING, gen matches       |
| RUNNING      | NEEDS_REVIEW | `unknown_outcome()`| status == RUNNING, gen matches       |
| CHECKPOINT   | CLAIMED      | `claim()` / expire | status == CHECKPOINT, lease expired  |
| CLAIMED      | CLAIMED      | `transfer()`       | old lease expired, gen matches + 1   |
| CHECKPOINT   | RUNNING      | `resume()`         | status == CHECKPOINT, gen matches    |
| RUNNING      | DONE         | `complete()`       | status == RUNNING, gen matches       |
| NEEDS_REVIEW | PENDING      | `reconcile()`      | manual operator acknowledgement      |
| FAILED       | PENDING      | `reset()`          | manual operator acknowledgement      |

**Terminal statuses**: DONE, FAILED (awaiting reset), NEEDS_REVIEW (awaiting
reconcile). No automatic retry from either.

---

## 4. Atomic claim, renewal, transfer, and checkpoint updates

All mutating operations are single SQLite transactions. The generation column
is the fencing token.

### 4.1 Claim (PENDING → CLAIMED or CHECKPOINT → CLAIMED after expiry)

```
BEGIN IMMEDIATE;
SELECT generation, status, lease_expiry FROM jobs WHERE id = ? AND (
    status = 'PENDING'
    OR (status = 'CHECKPOINT' AND lease_expiry < now())
);
-- abort if no row returned
new_gen = old_gen + 1
UPDATE jobs
   SET status='CLAIMED', owner=my_id, lease_expiry=now()+LEASE_TTL,
       generation=new_gen, updated_at=now()
 WHERE id=? AND generation=old_gen;   -- fencing guard
-- abort if rowcount != 1
INSERT INTO history(job_id, from_status, to_status, worker, generation,
                    note, recorded_at)
VALUES (?, old_status, 'CLAIMED', my_id, new_gen, NULL, now());
COMMIT;
```

The caller receives `(job_id, new_gen)` as its lease token. Every subsequent
write must supply this generation; a mismatched generation is rejected
immediately.

### 4.2 Renewal

```
BEGIN IMMEDIATE;
UPDATE jobs
   SET lease_expiry=now()+LEASE_TTL, updated_at=now()
 WHERE id=? AND owner=my_id AND generation=my_gen
       AND status IN ('CLAIMED','RUNNING','CHECKPOINT');
-- abort if rowcount != 1 (another worker stole the lease)
COMMIT;
```

Workers renew on a short heartbeat interval (suggested: `LEASE_TTL / 3`).

### 4.3 Transfer (expired lease takeover)

Recovery worker B observes `lease_expiry < now()` and `status` not DONE/FAILED/
NEEDS_REVIEW. It performs the same claim transaction as §4.1; the `generation`
guard ensures A cannot write after B has claimed.

### 4.4 Guarded checkpoint

```
BEGIN IMMEDIATE;
UPDATE jobs
   SET status='CHECKPOINT', checkpoint=new_cp_json,
       step_cursor=step_name, updated_at=now()
 WHERE id=? AND owner=my_id AND generation=my_gen AND status='RUNNING';
-- abort if rowcount != 1
INSERT INTO history(...) VALUES (...);
COMMIT;
```

The checkpoint JSON must be written atomically with the status change.
A worker that dies between two separate writes (status flip, then checkpoint)
would leave the job in RUNNING with stale checkpoint data — the guarded single
transaction prevents this class of corruption.

---

## 5. Fencing and stale-write rejection

Every write carries `WHERE generation = my_gen`. SQLite's `changes()` (or
Python's `cursor.rowcount`) is checked after every UPDATE; a zero rowcount
means the lease was stolen or the generation advanced, and the operation is
aborted. The worker MUST NOT continue executing steps after a failed lease
check.

**Stale write sequence:**

```
Worker A:  claim(gen=1)
                         Worker B: expire+claim(gen=2)  [A died / hung]
Worker A:  checkpoint(gen=1)  → rowcount=0 → ABORT, worker A halts
```

Since SQLite uses exclusive write locks and the generation is checked inside
`BEGIN IMMEDIATE`, there is no window for two writers to both see rowcount=1
for the same generation.

---

## 6. Guarded checkpoints and crash-safe replay

A checkpoint stores the minimal state needed to resume without re-executing
committed steps. Proposed structure:

```json
{
  "step_cursor": "validate",
  "completed_steps": ["validate"],
  "partial_output": null,
  "schema_version": 1
}
```

### Crash between action-commit and checkpoint-commit

This is the most dangerous gap. Sequence:

```
1. Worker A inserts receipt (action committed in receipts table)  ← SQLite COMMIT
2. Worker A crashes
3. Worker B claims the job (generation advances)
4. Worker B reads checkpoint: step_cursor still points to the step BEFORE validate
5. Worker B attempts to run "validate" again
6. Worker B inserts into receipts with the same action_key → UNIQUE constraint fires
7. Worker B catches the IntegrityError, reads the existing receipt, and uses its result
8. Worker B writes the checkpoint to advance the cursor past "validate"
```

The `receipts` table's `PRIMARY KEY (action_key)` is the idempotency fence.
The action key must encode job id, step name, and attempt scope so that a
legitimate re-run of the same step after an explicit `reset()` can use a
different key.

### Checkpoint schema version

The `schema_version` field allows future migration. If a worker reads a
checkpoint with an unknown version it must transition the job to NEEDS_REVIEW
rather than proceeding blindly.

---

## 7. Append-only transition history

`history` rows are never updated or deleted. Every status change writes one
row. This provides:

- A full audit trail for post-mortem analysis.
- Evidence that a claimed job passed through expected states.
- A monotone generation record that can be replayed to verify consistency.

Workers do not read history for control flow; it is purely observational.
The CLI `status` subcommand may render the last N rows for a job.

---

## 8. Unique local action keys and input-hash mismatch rejection

### 8.1 Action key format

```
{job_id}:{step_name}:{run_epoch_int}
```

`run_epoch_int` is the Unix second at which the job was first claimed in
this run. It changes after every transfer/recovery, scoping idempotency
keys per run while remaining stable within one lease lifetime.

### 8.2 Input-hash mismatch rejection

When a worker claims a job it computes `SHA-256(canonical(input_blob))` and
compares with `jobs.input_hash`. If the hashes differ the worker must:

1. Leave the job in CLAIMED (do not begin_step).
2. Transition to FAILED with `failure_msg = "input_hash mismatch: expected X got Y"`.
3. Write a history row.

This prevents a scenario where a job was created with one input, the caller
accidentally passes different data during resume, and the worker silently
processes an inconsistent input.

---

## 9. NEEDS_REVIEW for uncertain external outcomes

Any step whose result cannot be verified locally (simulated "external provider
call" in the demo) must be wrapped:

```
try:
    result = call_external(...)
    # result is verifiable → proceed
except UnknownOutcomeError:
    unknown_outcome(job_id, my_gen, step_name, reason)
    # transitions job to NEEDS_REVIEW, logs history row
    return
```

`unknown_outcome()` uses the same fencing guard as all writes:

```
BEGIN IMMEDIATE;
UPDATE jobs SET status='NEEDS_REVIEW', failure_msg=reason, updated_at=now()
 WHERE id=? AND owner=my_id AND generation=my_gen AND status='RUNNING';
INSERT INTO history(...);
COMMIT;
```

A job in NEEDS_REVIEW is never auto-retried. An operator must call
`reconcile(job_id, verdict)` which either:
- Sets `status = 'DONE'` if the external action succeeded, or
- Sets `status = 'FAILED'` if it failed, or
- Sets `status = 'PENDING'` (with incremented generation) to allow a fresh
  claim with a new action key scope.

**Bounded suppression claim**: the `receipts` table suppresses duplicate
*local* action execution within one SQLite database. It makes no claim about
suppressing duplicate calls to systems outside this database.

---

## 10. Clock assumptions and lease TTL

- All timestamps use `time.time()` (Unix float seconds, UTC-equivalent).
- There is no wall-clock synchronisation between worker processes; both read
  from the same OS clock on the same machine, so clock skew is zero for the
  local demo.
- **`LEASE_TTL`**: suggested 30 s for the demo. Workers heartbeat every 10 s.
  A job whose `lease_expiry < now()` and whose status is not terminal may be
  claimed by any other worker.
- SQLite's `WITHOUT ROWID` mode is not used on `jobs` to preserve
  `last_insert_rowid()` compatibility; monotone clock assumptions replace the
  need for external sequence servers.
- Time is never stored in human-readable format in the database; float seconds
  allow arithmetic comparisons without parsing.
- If the clock jumps backward (e.g. NTP correction), a lease may appear not
  expired when it is. This is acceptable for a single-machine demo and must
  be documented as a known limitation if the design is later distributed.

---

## 11. Demo walkthrough (design-level)

The following describes the *intended* sequence; it has not been executed.

```
Step 0:  Operator creates job
         insert_job(job_id="job-001", input_blob=b"...", status='PENDING')
         → computes input_hash, writes jobs row

Step 1:  Worker A claims
         claim("job-001") → generation=1, lease_expiry=now()+30

Step 2:  Worker A runs "validate" step
         action_key = "job-001:validate:RUN_EPOCH"
         INSERT INTO receipts ... ON CONFLICT ABORT (first run succeeds)
         → result = {"valid": true}

Step 3:  Worker A writes checkpoint (step_cursor="validate")
         BEGIN IMMEDIATE; UPDATE jobs ... generation=1; INSERT history; COMMIT

Step 4:  Worker A simulates crash (process killed)
         lease_expiry passes after 30 s

Step 5:  Worker B polls, sees CHECKPOINT + expired lease
         claim("job-001") → generation=2

Step 6:  Worker B reads checkpoint: step_cursor="validate"
         Attempts to run "validate" again with same action_key
         → UNIQUE constraint fires → reads existing receipt → skips execution

Step 7:  Worker B runs "create_receipt" step (next uncompleted)
         action_key = "job-001:create_receipt:RUN_EPOCH_B" (new epoch)
         INSERT INTO receipts → success

Step 8:  Worker B runs "summarize" step → complete()
         BEGIN IMMEDIATE; UPDATE jobs SET status='DONE' ... generation=2; COMMIT

Step 9:  Operator inspects
         cli status job-001
         → owner=worker-B, status=DONE, step_cursor=summarize, generation=2
         history rows: PENDING→CLAIMED(gen1), CLAIMED→RUNNING, RUNNING→CHECKPOINT,
                       CHECKPOINT→CLAIMED(gen2), CLAIMED→RUNNING, RUNNING→DONE
```

---

## 12. Fault-injection test cases

These are acceptance criteria for a future test suite. None have been run.

| # | Scenario | Setup | Expected outcome |
|---|----------|-------|-----------------|
| T1 | **Process death before checkpoint** | Worker A claims, runs step, dies before checkpoint write | Job remains RUNNING, lease expires; Worker B claims (gen+1), step re-executes, action_key UNIQUE prevents duplicate insert, checkpoint written, job completes |
| T2 | **Process death after checkpoint** | Worker A claims, checkpoints, dies | Job in CHECKPOINT, lease expires; Worker B claims (gen+1), resumes from checkpoint cursor, skips completed steps via receipt lookup |
| T3 | **Concurrent claimants** | Two workers simultaneously attempt claim on PENDING job | Only one wins (SQLite exclusive write lock + generation guard); other sees rowcount=0 and backs off |
| T4 | **Stale write after transfer** | Worker A resumes after long pause; Worker B already holds gen+1 | Worker A's checkpoint or complete write sees rowcount=0, aborts silently; Worker B continues undisturbed |
| T5 | **Input hash mismatch** | Job created with input X; worker passes input Y at claim-time | Worker detects hash mismatch, transitions to FAILED, no steps executed |
| T6 | **Receipt replay** | Worker B re-runs a step that Worker A already committed to receipts | IntegrityError on receipts.action_key; worker reads existing result, does not execute action again |
| T7 | **NEEDS_REVIEW path** | External step raises UnknownOutcomeError | Job transitions to NEEDS_REVIEW; no retry attempted; history row written; reconcile() required before any further progress |
| T8 | **Lease renewal keeps lease alive** | Worker A heartbeats within TTL | lease_expiry advances; Worker B polling sees non-expired lease and does not attempt transfer |
| T9 | **Expired lease transfer** | Worker A heartbeat stops; lease_expiry passes | Worker B detects expiry, claims with gen+1; Worker A's subsequent renewal sees rowcount=0 and halts |
| T10 | **Checkpoint schema version mismatch** | Checkpoint written with schema_version=2; worker only knows version 1 | Worker transitions job to NEEDS_REVIEW with message "unknown checkpoint schema version 2" |
| T11 | **History append-only** | Any worker attempts to UPDATE or DELETE a history row | SQLite trigger (or application-level enforcement) rejects the write; history rows are immutable |
| T12 | **Crash between action-commit and checkpoint** | SQLite COMMIT for receipt succeeds; process dies before checkpoint transaction | On recovery, Worker B's receipt lookup returns the committed result; checkpoint advances past that step without re-executing the action |
| T13 | **Reconcile from NEEDS_REVIEW → PENDING** | Operator calls reconcile with verdict=retry | generation increments, status=PENDING, new run produces a fresh action key scope |

---

## 13. CLI surface (design sketch)

All subcommands operate against a single `--db` path argument defaulting to
`handoff.db` in the current directory.

```
cli.py create  --job-id JOB --input-file FILE
cli.py claim   --job-id JOB --worker-id WORKER
cli.py run     --job-id JOB --worker-id WORKER     # executes steps, heartbeats, checkpoints
cli.py status  --job-id JOB
cli.py recover --job-id JOB --worker-id WORKER     # alias for claim on expired lease
cli.py reconcile --job-id JOB --verdict (done|failed|retry)
cli.py history --job-id JOB [--last N]
```

The `run` subcommand encapsulates the step loop, heartbeat thread, receipt
guard, and checkpoint writes. It is the only entrypoint that calls
`begin_step()`, `checkpoint()`, `complete()`, `fail()`, and
`unknown_outcome()`.

---

## 14. Open questions for human review

1. **Step catalogue**: What are the exact named steps for the demo? The
   walkthrough uses `validate`, `create_receipt`, `summarize` as placeholders.
2. **Lease TTL**: 30 s is workable for manual testing but may be too long for
   an automated demo. Should it be configurable via CLI flag?
3. **Heartbeat thread vs. main thread**: Using a daemon thread for renewal is
   simpler but harder to test. An alternative is a subprocess with its own
   SQLite connection. Which is preferred?
4. **History trigger vs. application enforcement**: A `BEFORE DELETE/UPDATE`
   trigger on `history` would enforce append-only at the SQLite level without
   trusting the application layer. Acceptable complexity increase?
5. **Reconcile authorisation**: For the local demo, any caller can reconcile.
   If Telegram is later added, only the numeric owner chat ID may reconcile —
   noted in BRIEF.md; confirm scope before Telegram integration.
6. **`run_epoch_int` scoping**: Using the Unix second of first claim as the
   run epoch is simple but could collide if a job is reset and re-claimed
   within the same second. A UUID suffix would be safer; is this needed?
7. **Evidence references**: `jobs.evidence_ref` is currently an opaque string.
   Should it be a structured JSON array of file path + SHA-256 pairs to support
   multi-file evidence?
8. **SQLite WAL mode**: Enabling `PRAGMA journal_mode=WAL` would allow one
   writer and multiple concurrent readers. Needed only if the status CLI query
   must not wait for an active write transaction. Worth enabling by default?

---

## 15. What this document does not cover

- Application Python source code (not yet written).
- Dependency installation (none planned; Python 3.9 stdlib only).
- Deployment, publishing, or submission.
- Telegram integration (deferred; see §10 and open question 5).
- Production use: bounded local duplicate suppression is claimed only for the
  transactional local demo, not universal exactly-once external execution.
- Prior Bob session evidence (none exists yet; first real session is this task).
