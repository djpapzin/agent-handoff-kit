# Architecture Proposal — Agent Handoff Kit (revision 2)

> **Design-only document.** Everything described here is a proposal for human
> review. No application code exists; no behaviour described below has been
> verified by tests or execution.
>
> **Revision notes (vs. revision 1):**
> (C1) Action keys are now derived from stable logical identity, not claim-time
> epoch — keys survive lease transfer and replay unchanged.
> (C2) Claim/transfer guards now cover all non-terminal expired states
> (PENDING, CLAIMED, RUNNING, CHECKPOINT), not just PENDING and CHECKPOINT.
> (C3) Receipt insertion, existing-receipt verification, and lease validity are
> all checked inside the same transaction; renewal cannot resurrect an expired
> lease; external side effects are explicitly excluded from SQLite-level
> exactly-once claims.
> (C4) Input-hash mismatch is checked before any mutation to the existing job.
> (C5) NEEDS_REVIEW requires verifiable reconciliation evidence, not
> acknowledgement alone.
> (C6) Transition table and clock section are corrected and extended; lease
> timestamp is sampled after acquiring the write lock; forward/backward
> wall-clock jump behaviour and same-owner expiry are described; unsupported
> clock/rowid claims are removed.
> (C7) Test cases are updated to expose the new fault classes.

---

## 1. Scope recap

One job, two local worker processes (A and B), one recovery path.  
Stack: Python 3.9 standard library + SQLite 3 (shipped with CPython).  
No third-party packages, no paid providers, no cloud.  
Telegram integration is deferred; notes appear in §11.

---

## 2. Database schema

One SQLite file: `handoff.db`.

```sql
-- 2.1  Jobs
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,   -- stable, caller-assigned, e.g. "job-001"
    input_hash   TEXT NOT NULL,      -- SHA-256 hex of the canonical input blob
    status       TEXT NOT NULL
                 CHECK(status IN (
                   'PENDING','CLAIMED','RUNNING',
                   'CHECKPOINT','DONE','FAILED','NEEDS_REVIEW')),
    owner        TEXT,               -- worker id holding the lease, or NULL
    lease_expiry REAL,               -- Unix timestamp (float); NULL when unclaimed
    generation   INTEGER NOT NULL DEFAULT 0,
                                     -- monotone fence counter; incremented on every
                                     --   successful claim or transfer
    checkpoint   TEXT,               -- JSON blob: last safely committed step state
    step_cursor  TEXT,               -- name of the last completed step, or NULL
    failure_msg  TEXT,               -- set on FAILED or NEEDS_REVIEW
    evidence_ref TEXT,               -- JSON array of {path, sha256} objects
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

-- 2.2  Transition history (append-only; never updated or deleted)
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

-- 2.3  Local action receipts (idempotency fence)
CREATE TABLE receipts (
    action_key   TEXT PRIMARY KEY,   -- deterministic key: see §8.1
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    step_name    TEXT NOT NULL,
    payload_hash TEXT NOT NULL,      -- SHA-256 of serialised action inputs
    result       TEXT,               -- serialised result (NOT NULL after commit)
    committed_at REAL NOT NULL
);
```

### Append-only enforcement for `history`

A SQLite `BEFORE UPDATE` and `BEFORE DELETE` trigger on `history` that raises
an error is the preferred enforcement mechanism; application-layer checks alone
are insufficient because the same database file may be opened by multiple
processes or tools:

```sql
CREATE TRIGGER history_no_update
    BEFORE UPDATE ON history
BEGIN
    SELECT RAISE(ABORT, 'history rows are immutable');
END;

CREATE TRIGGER history_no_delete
    BEFORE DELETE ON history
BEGIN
    SELECT RAISE(ABORT, 'history rows are immutable');
END;
```

### Index hints (deferred until implementation)

```sql
CREATE INDEX ix_jobs_status       ON jobs(status);
CREATE INDEX ix_jobs_expiry       ON jobs(lease_expiry) WHERE status NOT IN ('DONE','FAILED','NEEDS_REVIEW');
CREATE INDEX ix_history_job       ON history(job_id, seq);
CREATE INDEX ix_receipts_job_step ON receipts(job_id, step_name);
```

---

## 3. Status model and allowed transitions

```
              ┌──────────┐
              │  PENDING │  (job created, or reset to restart)
              └────┬─────┘
                   │ claim()
                   ▼
              ┌──────────┐
              │  CLAIMED │  (lease held, no step executing yet)
              └────┬─────┘
                   │ begin_step()
                   ▼
              ┌──────────┐
              │  RUNNING │  (a step executing under current lease)
              └──┬──┬────┘
     checkpoint()│  │fail() / unknown_outcome()
                 ▼  ▼────────────────────────────────────┐
          ┌────────────┐                      ┌──────────────────┐
          │ CHECKPOINT │                      │     FAILED /     │
          └─────┬──────┘                      │  NEEDS_REVIEW    │
                │ claim() on expired lease     └──────┬───────────┘
                │ (any non-terminal expired           │ reconcile() with
                │  state is claimable)                │ verifiable evidence
                ▼                                     ▼
             RUNNING ──── complete() ──────► DONE   PENDING (retry)
                                                    DONE    (reconcile-done)
                                                    FAILED  (reconcile-failed)
```

### Transition table (complete)

| From         | To           | Trigger               | Guards (all must hold)                                                   |
|--------------|--------------|-----------------------|--------------------------------------------------------------------------|
| PENDING      | CLAIMED      | `claim()`             | status = PENDING                                                         |
| CLAIMED      | CLAIMED      | `claim()` (takeover)  | status = CLAIMED **and** lease_expiry < lock_time                        |
| RUNNING      | CLAIMED      | `claim()` (takeover)  | status = RUNNING **and** lease_expiry < lock_time                        |
| CHECKPOINT   | CLAIMED      | `claim()` (takeover)  | status = CHECKPOINT **and** lease_expiry < lock_time                     |
| CLAIMED      | RUNNING      | `begin_step()`        | status = CLAIMED, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time  |
| RUNNING      | CHECKPOINT   | `checkpoint()`        | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time  |
| CHECKPOINT   | RUNNING      | `resume()`            | status = CHECKPOINT, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time |
| RUNNING      | DONE         | `complete()`          | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time  |
| RUNNING      | FAILED       | `fail()`              | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time  |
| RUNNING      | NEEDS_REVIEW | `unknown_outcome()`   | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time  |
| CHECKPOINT   | NEEDS_REVIEW | `schema_mismatch()`   | status = CHECKPOINT, owner = my_id, gen = my_gen, lease_expiry ≥ lock_time |
| NEEDS_REVIEW | DONE         | `reconcile(verdict=done)`   | verifiable external evidence supplied; operator-initiated only     |
| NEEDS_REVIEW | FAILED       | `reconcile(verdict=failed)` | verifiable external evidence supplied; operator-initiated only     |
| NEEDS_REVIEW | PENDING      | `reconcile(verdict=retry)`  | verifiable external evidence supplied; operator-initiated only     |
| FAILED       | PENDING      | `reset()`             | operator-initiated; no evidence requirement                              |

**lock_time** = the Unix timestamp captured *inside* the `BEGIN IMMEDIATE`
transaction, after the write lock is acquired (see §4 and §10).

**Terminal states awaiting operator action**: DONE (final), FAILED (requires
`reset()`), NEEDS_REVIEW (requires `reconcile()` with evidence).  
No automatic retry from any terminal state.

---

## 4. Atomic claim, renewal, transfer, and checkpoint updates

All mutating operations are single SQLite `BEGIN IMMEDIATE` transactions.
The generation column is the fencing token.

### 4.1 Claim and takeover (unified)

The same transaction covers fresh claims (PENDING) and expired-lease takeovers
(CLAIMED, RUNNING, or CHECKPOINT whose `lease_expiry` has passed). The
timestamp used in the expiry check is read **inside** the transaction after the
write lock is acquired; this eliminates the race where a process reads
`now()` outside the lock and the clock advances before the write.

```
BEGIN IMMEDIATE;

-- Sample the authoritative timestamp under the write lock
now = SELECT unixepoch('now','subsec');   -- or equivalent float

SELECT id, generation AS old_gen, status AS old_status, lease_expiry
  FROM jobs
 WHERE id = ?
   AND (
         status = 'PENDING'
      OR (status IN ('CLAIMED','RUNNING','CHECKPOINT') AND lease_expiry < now)
   );
-- If no row: abort (job does not exist, is terminal, or lease is still live)

new_gen = old_gen + 1
new_expiry = now + LEASE_TTL

UPDATE jobs
   SET status = 'CLAIMED',
       owner  = my_id,
       lease_expiry = new_expiry,
       generation   = new_gen,
       updated_at   = now
 WHERE id = ? AND generation = old_gen;   -- fencing guard
-- If rowcount != 1: abort (concurrent writer won)

INSERT INTO history(job_id, from_status, to_status, worker, generation, note, recorded_at)
VALUES (?, old_status, 'CLAIMED', my_id, new_gen, NULL, now);

COMMIT;
-- Returns (new_gen, new_expiry) to caller as the lease token
```

A calller holds the lease as long as it presents `(job_id, my_id, my_gen)` and
`lease_expiry` has not passed at the time the next write lock is acquired.

### 4.2 Renewal

Renewal extends an **existing, unexpired** lease. It must not resurrect a lease
that has already passed — a worker that has been paused longer than LEASE_TTL
must re-claim (obtaining a new generation) rather than renew.

```
BEGIN IMMEDIATE;

now = SELECT unixepoch('now','subsec');

UPDATE jobs
   SET lease_expiry = now + LEASE_TTL,
       updated_at   = now
 WHERE id          = ?
   AND owner       = my_id
   AND generation  = my_gen
   AND status      IN ('CLAIMED','RUNNING','CHECKPOINT')
   AND lease_expiry >= now;   -- must still be live; no resurrection

-- If rowcount != 1: lease is expired or stolen; caller must halt
COMMIT;
```

Workers renew on a heartbeat interval ≤ `LEASE_TTL / 3`.

### 4.3 Transfer

Transfer is a claim where the takeover guard (`lease_expiry < now`) fires
for CLAIMED or RUNNING, not only CHECKPOINT. The unified claim transaction
in §4.1 handles this without a separate code path.

### 4.4 Guarded checkpoint

The local receipt insertion, checkpoint update, and history append are committed
in a **single transaction** whenever possible — specifically when the receipt
has not previously been committed (first run). This makes the local effect,
checkpoint advance, and audit record atomic:

```
BEGIN IMMEDIATE;

now = SELECT unixepoch('now','subsec');

-- Lease validity guard (inside the lock)
SELECT 1 FROM jobs
 WHERE id = ? AND owner = my_id AND generation = my_gen
   AND status = 'RUNNING' AND lease_expiry >= now;
-- If no row: abort

-- Attempt receipt insert (idempotent key)
INSERT INTO receipts(action_key, job_id, step_name, payload_hash, result, committed_at)
VALUES (?, ?, ?, ?, ?, now);
-- On UNIQUE conflict: see §6 (existing-receipt reconciliation path)

-- Advance checkpoint
UPDATE jobs
   SET status       = 'CHECKPOINT',
       checkpoint   = new_cp_json,
       step_cursor  = step_name,
       updated_at   = now
 WHERE id = ? AND owner = my_id AND generation = my_gen AND status = 'RUNNING';
-- If rowcount != 1: abort

INSERT INTO history(job_id, from_status, to_status, worker, generation, note, recorded_at)
VALUES (?, 'RUNNING', 'CHECKPOINT', my_id, my_gen, step_name, now);

COMMIT;
```

When the receipt already exists (recovery path), the reconciliation procedure
in §6 applies instead.

---

## 5. Fencing and stale-write rejection

Every mutating SQL statement includes `WHERE generation = my_gen` (and
`owner = my_id` where applicable). Python's `cursor.rowcount` is checked
immediately; zero means the lease was stolen or generation has advanced, and
the calling worker **must halt** — it must not execute further steps, write
additional receipts, or call any external side effect.

**Stale write sequence:**

```
Worker A:  claim → gen=1, expiry=T+30
                         [A pauses for 40 s]
                         Worker B: takeover → gen=2, expiry=T+70
Worker A:  checkpoint(gen=1) → rowcount=0 → ABORT; Worker A halts
```

Because `BEGIN IMMEDIATE` acquires the write lock before reading `now`, there
is no window in which two concurrent writers both observe `rowcount=1` for the
same generation.

---

## 6. Guarded checkpoints and crash-safe replay

### 6.1 Checkpoint JSON structure

```json
{
  "step_cursor": "validate",
  "completed_steps": ["validate"],
  "partial_output": null,
  "schema_version": 1
}
```

`schema_version` allows future evolution. A worker reading an unknown version
must call `schema_mismatch()` (→ NEEDS_REVIEW) rather than proceeding.

### 6.2 Action key stability across lease transfers

Action keys are derived from stable logical identity — they **do not** embed
claim-time or lease metadata. The format is:

```
{job_id}:{step_name}:{input_hash_prefix_8}
```

where `input_hash_prefix_8` is the first 8 hex characters of
`jobs.input_hash`. This key is:

- **stable** across recovery and replay: if Worker B picks up after Worker A,
  it produces the identical key for the same step on the same input.
- **unique per job+step+input** combination: a legitimately different input
  (after `reset()` with a new input blob) yields a different `input_hash` and
  therefore a different key, exposing any silent input substitution.
- **disclosed**: any new run that intentionally uses a different input must
  pass a new input blob through `create` and get a new `job_id`, not reuse
  the existing job's identity.

**Limitation**: this scheme does not distinguish two independent attempts at the
same step on the same input (e.g. after `reset()` with identical input). If
disambiguation of such re-runs is required, a `run_id` (UUID set at job-create
time and preserved across lease transfers, reset on `reset()`) may be appended.
This is listed as open question Q1.

### 6.3 Crash between receipt-commit and checkpoint-commit

This is the critical gap. If the receipt and checkpoint are committed in the
same transaction (§4.4), this gap is closed for the common case. The gap only
exists if a crash occurs partway through a transaction that could not be made
fully atomic (e.g. a future multi-step batch). For the present single-step
design, the combined transaction is preferred.

Recovery path when the receipt already exists (UNIQUE fires on INSERT):

```
BEGIN IMMEDIATE;

now = SELECT unixepoch('now','subsec');

-- Lease and status guard
SELECT 1 FROM jobs
 WHERE id = ? AND owner = my_id AND generation = my_gen
   AND status IN ('RUNNING','CLAIMED') AND lease_expiry >= now;
-- abort if no row

-- Read existing receipt
SELECT payload_hash, result FROM receipts WHERE action_key = ?;

-- Verify payload hash matches current inputs
IF existing.payload_hash != sha256(current_inputs):
    -- The stored receipt was for different inputs under the same key.
    -- This must not happen given the key includes input_hash_prefix;
    -- treat as a logic error → transition to FAILED.
    UPDATE jobs SET status='FAILED',
        failure_msg='receipt payload_hash mismatch on replay: action_key collision',
        updated_at=now
     WHERE id=? AND owner=my_id AND generation=my_gen;
    INSERT INTO history ...;
    COMMIT;
    ABORT caller;

-- payload_hash matches: safe to reuse result
result = existing.result

-- Advance checkpoint using the recovered result
UPDATE jobs SET status='CHECKPOINT', checkpoint=new_cp_json,
    step_cursor=step_name, updated_at=now
 WHERE id=? AND owner=my_id AND generation=my_gen AND status IN ('RUNNING','CLAIMED');

INSERT INTO history ...;
COMMIT;
```

### 6.4 Prohibition on external side effects inside exactly-once claims

The SQLite UNIQUE constraint on `receipts.action_key` guarantees that the
**local database write** of the receipt is not repeated. It makes no guarantee
about external calls (network, file system, OS signals). Workers must never
treat the receipt guard as proof that an external side effect was or was not
executed. Any step with an external side effect whose outcome cannot be verified
locally must use `unknown_outcome()` (§9).

---

## 7. Append-only transition history

`history` rows are never updated or deleted; this is enforced by the database
triggers defined in §2. Every status change — including failed attempts that
transition back to CLAIMED via takeover — writes exactly one history row within
the same transaction as the jobs UPDATE. Workers never read history for control
flow; it is observational only.

---

## 8. Unique local action keys and input-hash mismatch rejection

### 8.1 Action key format (revised — see also §6.2)

```
{job_id}:{step_name}:{input_hash_prefix_8}
```

Examples:
- `job-001:validate:a3f82c91`
- `job-001:create_receipt:a3f82c91`

The key is deterministic and stable. It changes only if `jobs.input_hash`
changes, which can only happen if the job is deleted and recreated with
different input — the existing `jobs` row's `input_hash` is immutable after
creation.

### 8.2 Input-hash mismatch rejection (non-mutating check first)

When a worker claims a job it **reads** `jobs.input_hash` from the row before
calling `begin_step()`. It then computes `SHA-256(canonical(presented_input))`
and compares. If the hashes differ:

1. **No mutation is made to the existing job row, its history, or its
   receipts.** The original state is preserved intact.
2. The worker logs a local error and aborts without calling `begin_step()`.
3. The lease it already holds (from `claim()`) is left to expire naturally,
   or the worker may call `fail()` to explicitly mark the job FAILED with the
   mismatch message. Calling `fail()` does mutate the job, but only to FAILED —
   it does not alter history rows prior to the claim, nor existing receipts.

This prevents a scenario where a resumed worker silently processes a different
input than the one the job was originally created with, while also preserving
the complete audit trail of what happened before the mismatch was detected.

---

## 9. NEEDS_REVIEW for uncertain external outcomes

Any step with an external side effect whose outcome cannot be locally verified
must call `unknown_outcome()` rather than `complete()` or `fail()`.

```
BEGIN IMMEDIATE;
now = SELECT unixepoch('now','subsec');
UPDATE jobs
   SET status = 'NEEDS_REVIEW',
       failure_msg = reason,
       updated_at  = now
 WHERE id = ? AND owner = my_id AND generation = my_gen
   AND status = 'RUNNING' AND lease_expiry >= now;
INSERT INTO history ...;
COMMIT;
```

A job in NEEDS_REVIEW is **never** auto-retried. Clearing NEEDS_REVIEW requires
an operator-supplied `reconcile()` call that includes **verifiable evidence** of
the external outcome — not an acknowledgement alone. Acceptable evidence forms
(design intent; exact validation deferred):

- A receipt or confirmation ID from the external system.
- A file path + SHA-256 hash of an external log or response artifact.
- An explicit `verdict` of `done`, `failed`, or `retry`.

`reconcile()` stores the evidence in `jobs.evidence_ref` (as a JSON array of
`{source, id_or_path, sha256}` objects) and transitions the job accordingly:

| Verdict  | New status | Generation change |
|----------|------------|-------------------|
| `done`   | DONE       | unchanged         |
| `failed` | FAILED     | unchanged         |
| `retry`  | PENDING    | +1 (new action key scope for fresh claim) |

**Bounded suppression claim**: the `receipts` table suppresses duplicate *local*
database writes within this SQLite file. It makes no claim about suppressing
duplicate calls to systems outside this database. If a step that reached
NEEDS_REVIEW was an external call, there is no guarantee that the call did or
did not execute; reconciliation evidence must come from the external system.

---

## 10. Clock assumptions and lease TTL

All timestamps use `unixepoch('now','subsec')` evaluated **inside the
`BEGIN IMMEDIATE` transaction**, after SQLite has acquired the write lock.
This is the `lock_time` referenced in the transition table. Using the
in-transaction timestamp prevents a class of race conditions where a caller
reads `time.time()` before the lock and the clock or lease state changes before
the write.

**LEASE_TTL**: suggested 30 s for the demo. Heartbeat interval: ≤ 10 s
(i.e. ≤ LEASE_TTL / 3). These are configurable parameters, not hard-coded
constants.

**Forward wall-clock jump** (e.g. NTP step-forward): a job's `lease_expiry`
may suddenly appear already expired from the holder's perspective. On the next
heartbeat the holder will see rowcount=0 and halt; another worker will claim.
This is safe — the generation guard prevents the original holder from writing
after the takeover.

**Backward wall-clock jump** (e.g. NTP step-back): `now` inside the
transaction may be less than a stored `lease_expiry`, causing a lease to appear
unexpired when clock-wall time says otherwise. For a single-machine demo this
is an edge case that should be documented as a known limitation. No mitigation
is proposed at this stage.

**Same-owner expiry**: if a worker's own lease_expiry has passed (perhaps it
was paused), its next write will see rowcount=0 from the `lease_expiry >= now`
guard. The worker must not re-extend its own expired lease via `renewal()`; it
must use `claim()` (obtaining a new generation) or halt and let another worker
take over. The renewal guard `AND lease_expiry >= now` enforces this.

**No cross-process clock skew**: for the local single-machine demo both workers
read the same OS clock. Clock skew is therefore zero in this design; the
assumption must be revisited before any network distribution.

---

## 11. Demo walkthrough (design-level)

The following describes the *intended* sequence; it has not been executed.

```
Step 0:  Operator creates job
         insert_job(job_id="job-001", input_blob=b"...", status='PENDING')
         → input_hash = sha256(canonical(input_blob)) = "a3f82c91..."
         → action_key prefix = "job-001:<step>:a3f82c91"

Step 1:  Worker A claims
         claim("job-001") → generation=1, lease_expiry=T+30

Step 2:  Worker A runs "validate" step
         action_key = "job-001:validate:a3f82c91"
         Transaction: INSERT receipts + UPDATE jobs(CHECKPOINT) + INSERT history
         → all committed atomically

Step 3:  Worker A simulates crash (process killed after step 2 commit)
         lease_expiry passes after 30 s

Step 4:  Worker B polls, sees CHECKPOINT + lease_expiry < now
         claim("job-001") → generation=2

Step 5:  Worker B reads checkpoint: step_cursor="validate"
         Attempts "validate" again with action_key="job-001:validate:a3f82c91"
         → UNIQUE fires → reads existing receipt → verifies payload_hash matches
         → reuses result, advances cursor (no external call repeated)

Step 6:  Worker B runs "create_receipt" step
         action_key = "job-001:create_receipt:a3f82c91"
         → new receipt inserted, checkpoint advanced to step_cursor="create_receipt"

Step 7:  Worker B runs "summarize" step → complete()
         BEGIN IMMEDIATE; UPDATE jobs SET status='DONE' ... generation=2; COMMIT

Step 8:  Operator inspects
         cli status job-001
         → owner=worker-B, status=DONE, step_cursor=summarize, generation=2
         history: PENDING→CLAIMED(gen1), CLAIMED→RUNNING(gen1),
                  RUNNING→CHECKPOINT(gen1), CHECKPOINT→CLAIMED(gen2),
                  CLAIMED→RUNNING(gen2), RUNNING→CHECKPOINT(gen2, create_receipt),
                  CHECKPOINT→RUNNING(gen2), RUNNING→DONE(gen2)
```

---

## 12. Fault-injection test cases

These are acceptance criteria for a future test suite. None have been run.

| # | Scenario | Setup | Expected outcome |
|---|----------|-------|------------------|
| T1 | **Process death before combined receipt+checkpoint transaction** | Worker A claims and begins a step but dies before the transaction commits | Job remains RUNNING, lease expires; Worker B takes over (gen+1), executes the step, receipt insert succeeds (no prior commit), checkpoint written atomically |
| T2 | **Process death after combined receipt+checkpoint transaction** | Worker A commits the atomic receipt+checkpoint, then dies | Job in CHECKPOINT; Worker B takes over (gen+1), finds receipt via action key, verifies payload_hash, skips re-execution, advances from checkpoint cursor |
| T3 | **Concurrent claimants on PENDING** | Two workers simultaneously call `claim()` on a PENDING job | Only one wins (exclusive write lock + generation guard); the other sees rowcount=0 and backs off |
| T4 | **Concurrent claimants on expired RUNNING** | Two workers simultaneously detect expired RUNNING lease and call `claim()` | Same as T3 — only one succeeds; the other's fencing guard fires |
| T5 | **Stale write after transfer** | Worker A resumes after long pause; Worker B already holds gen+1 | Worker A's next write (`lease_expiry >= now` and `generation = my_gen` both fail) → rowcount=0 → Worker A halts; Worker B undisturbed |
| T6 | **Stale worker receipt attempt after takeover** | Worker A (expired) attempts to insert a receipt with its old gen | `BEGIN IMMEDIATE` lease guard fails (`lease_expiry < now` or wrong gen); receipt is not inserted; Worker A halts |
| T7 | **Input hash mismatch at resume** | Job created with input X; Worker B presents input Y at `begin_step()` | Worker B detects hash mismatch before any step executes; original job row, history, and receipts are unmodified; Worker B aborts or calls `fail()` with mismatch message |
| T8 | **Receipt replay with payload_hash verification** | Worker B recovers and finds an existing receipt for the step | Worker B reads receipt, verifies `payload_hash` matches current inputs; on match, reuses result; on mismatch, transitions to FAILED (logic error / collision) |
| T9 | **Duplicate receipt across recovery** | Worker A commits receipt; Worker B (different gen) attempts the same action_key | UNIQUE fires; Worker B reads existing receipt, verifies hash, reuses result — no duplicate local execution |
| T10 | **NEEDS_REVIEW path — no evidence** | Operator calls `reconcile()` with verdict but without verifiable evidence | Transition is rejected; job remains NEEDS_REVIEW; history row not written |
| T11 | **NEEDS_REVIEW path — with evidence** | Operator supplies external confirmation ID + verdict=done | `reconcile()` stores evidence in `evidence_ref`, transitions to DONE, writes history row |
| T12 | **Lease renewal keeps lease alive** | Worker A heartbeats within LEASE_TTL | `lease_expiry` advances; Worker B polling sees non-expired lease and does not attempt takeover |
| T13 | **Renewal after expiry is rejected** | Worker A's lease passes; A calls `renewal()` | `AND lease_expiry >= now` guard fails → rowcount=0 → Worker A must halt; lease is not resurrected |
| T14 | **Expired lease transfer from CLAIMED** | Worker A claims but never calls `begin_step()`; lease expires | Worker B detects expired CLAIMED, takes over (gen+1); Worker A's next renewal sees rowcount=0 and halts |
| T15 | **Checkpoint schema version mismatch** | Checkpoint written with schema_version=2; recovering worker only understands version 1 | Worker calls `schema_mismatch()` → NEEDS_REVIEW with message "unknown checkpoint schema_version 2"; operator intervention required |
| T16 | **History immutability** | Any process issues UPDATE or DELETE on a history row | Database trigger raises ABORT; write is rejected; history row unchanged |
| T17 | **Reconcile NEEDS_REVIEW → PENDING (retry)** | Operator supplies evidence + verdict=retry | `generation` increments, status=PENDING; next claim produces a new generation; action keys are stable (same input_hash) so existing receipts still block re-execution of already-committed steps |
| T18 | **Forward clock jump mid-lease** | OS clock steps forward 60 s while Worker A holds a 30 s lease | Worker A's next heartbeat reads `now` inside lock; `lease_expiry < now` → renewal guard fails → rowcount=0 → Worker A halts; Worker B may now take over |
| T19 | **Same-owner expired lease write attempt** | Worker A pauses > LEASE_TTL; resumes and tries `checkpoint()` | `lease_expiry >= now` guard inside `BEGIN IMMEDIATE` fails → rowcount=0 → Worker A must re-claim (new gen) or halt |

---

## 13. CLI surface (design sketch)

All subcommands use `--db FILE` (default: `handoff.db` in CWD).

```
cli.py create    --job-id JOB --input-file FILE
cli.py claim     --job-id JOB --worker-id WORKER
cli.py run       --job-id JOB --worker-id WORKER   # step loop + heartbeat + receipt guard
cli.py status    --job-id JOB
cli.py recover   --job-id JOB --worker-id WORKER   # claim on any expired non-terminal state
cli.py reconcile --job-id JOB --verdict (done|failed|retry) --evidence-file FILE
cli.py reset     --job-id JOB
cli.py history   --job-id JOB [--last N]
```

`reconcile` now requires `--evidence-file` (a JSON file containing the
external evidence record). The file's SHA-256 is stored in `evidence_ref`.

---

## 14. Open questions for human review

1. **run_id for same-input re-runs**: the current action key
   `{job_id}:{step_name}:{input_hash_prefix_8}` cannot distinguish two
   legitimate re-runs of the same step with identical input (e.g. after
   `reset()` with the same input blob). Should a `run_id` UUID (set at
   job-create time, reset on `reset()`) be appended to the key to allow this?
2. **Step catalogue**: what are the exact named steps for the demo?
   The walkthrough uses `validate`, `create_receipt`, `summarize` as
   placeholders.
3. **Lease TTL and heartbeat interval**: suggested 30 s / 10 s. Should
   these be configurable via CLI flag (`--lease-ttl`, `--heartbeat`)?
4. **Heartbeat thread vs. main thread**: using a daemon thread for renewal is
   simpler but harder to test deterministically. A subprocess with its own
   connection is testable but more complex. Which is preferred?
5. **Evidence format for reconcile**: the design proposes a JSON file of
   `{source, id_or_path, sha256}` objects. Should the CLI validate the
   structure, or accept any non-empty JSON?
6. **Reconcile authorisation**: any caller can reconcile in the local demo.
   If Telegram is later added, only the numeric owner chat ID may reconcile.
   Confirm scope before Telegram integration.
7. **Evidence references on `jobs`**: `evidence_ref` is proposed as a JSON
   array. Should it be a separate `evidence` table to avoid unbounded JSON
   growth on jobs with many reconciliation cycles?
8. **SQLite WAL mode**: `PRAGMA journal_mode=WAL` allows one writer and
   multiple concurrent readers, reducing contention between `cli.py status`
   and active workers. Worth enabling by default?
9. **`unixepoch('now','subsec')` availability**: this SQLite function requires
   SQLite ≥ 3.38.0. The Python 3.9.6 on this machine ships with SQLite; the
   exact version should be verified at implementation time. Fallback:
   pass `time.time()` as a bound parameter from Python rather than calling
   the SQLite function.

---

## 15. What this document does not cover

- Application Python source code (not yet written).
- Dependency installation (none planned; Python 3.9 stdlib only).
- Deployment, publishing, or submission.
- Telegram integration (deferred; see §11 and open question Q6).
- Production use or universal exactly-once external execution.  
  The bounded suppression claim applies only to local SQLite writes within
  this database for the transactional demo.
- Prior Bob session evidence (none yet; first real session is this task).
