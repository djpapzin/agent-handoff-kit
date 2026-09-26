# Architecture Proposal — Agent Handoff Kit (revision 3)

> **Design-only document.** Everything described here is a proposal for human
> review. No application code exists; no behaviour described below has been
> verified by tests or execution.
>
> **Revision notes (vs. revision 2):**
> (R1) Action identity is the tuple `(job_id, step_id, full_input_hash)`;
>   no prefix truncation; stable across all lease transfers, recovery, and
>   replay without any reset/delete/recreate path.
> (R2) Input-hash check runs inside `BEGIN IMMEDIATE` before any claim
>   mutation or history write; mismatch rolls back everything.
> (R3) Clock: one `time.time()` sample passed as a bound parameter after
>   Python acquires the write lock; lease valid iff `expiry > now`,
>   expired iff `expiry <= now`; `unixepoch()` dependency removed.
> (R4) Every worker write checks owner + generation + active status + unexpired
>   lease in the same WHERE clause; failed guards roll back all changes
>   including history and receipt writes.
> (R5) Steps are frozen: `validate → create_receipt → summarize`; exactly
>   one business receipt produced by `create_receipt`.
> (R6) Receipt, checkpoint, and history are always committed atomically;
>   non-atomic combinations from older designs are rejected as legacy; any
>   partial/inconsistent state fails closed to NEEDS_REVIEW.
> (R7) Recovery requires complete, ordered checkpoint and full receipt
>   identity/payload/result verification; progress never regresses.
> (R8) DONE replay is read-only; no write path revisits a DONE job.
> (R9) NEEDS_REVIEW is terminal in MVP; no external adapters exist; verdict
>   + file/hash alone is insufficient evidence; only a clearly marked
>   synthetic fixture (test-only) may demonstrate independently verified
>   outcome; production reconcile is out of scope.
> (R10) Heartbeat runs in the main loop (no thread); 30 s TTL / 10 s interval
>   suffices for tiny local steps.
> (R11) Operational-error/busy handling and rollback path are defined.
> (R12) Contradictory claims and stale open questions removed.

---

## 1. Scope and fixed decisions

One job, two local worker processes (A and B), one recovery path.  
Stack: Python 3.9 standard library + SQLite 3 (shipped with CPython).  
No third-party packages, no paid providers, no cloud, no threads, no Telegram
(deferred).

**Frozen step sequence**: `validate → create_receipt → summarize`.  
`create_receipt` is the only step that produces the one business receipt per
job. `validate` and `summarize` produce local-only receipts recording their
execution but have no external business side effect.

**Immutable job contract**: once a job row is created, `id` and `input_hash`
never change. A deliberately different execution (different input) requires a
distinct `job_id`. There is no delete, replace, or reassign path in this design.

---

## 2. Database schema

One SQLite file: `handoff.db`. Opened with `PRAGMA journal_mode=WAL` for
concurrent read safety.

```sql
-- 2.1  Jobs
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,   -- stable, caller-assigned, e.g. "job-001"
    input_hash   TEXT NOT NULL,      -- full SHA-256 hex of canonical JSON input
    status       TEXT NOT NULL
                 CHECK(status IN (
                   'PENDING','CLAIMED','RUNNING',
                   'CHECKPOINT','DONE','FAILED','NEEDS_REVIEW')),
    owner        TEXT,               -- worker id holding the lease, or NULL
    lease_expiry REAL,               -- Unix float (time.time()); NULL when unclaimed
    generation   INTEGER NOT NULL DEFAULT 0,
                                     -- monotone fence counter; incremented on
                                     --   every successful claim or expired takeover
    checkpoint   TEXT,               -- JSON blob: last atomically committed step state
    step_cursor  TEXT,               -- step_id of the last completed step, or NULL
    failure_msg  TEXT,               -- set on FAILED or NEEDS_REVIEW
    evidence_ref TEXT,               -- JSON array of {source, ref, sha256} objects
    created_at   REAL NOT NULL,      -- time.time() at job creation
    updated_at   REAL NOT NULL       -- time.time() at last mutation
);

-- 2.2  Transition history (append-only; immutable after insert)
CREATE TABLE history (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    from_status  TEXT,               -- NULL for initial PENDING creation
    to_status    TEXT NOT NULL,
    worker       TEXT,               -- NULL for operator-initiated transitions
    generation   INTEGER NOT NULL,
    note         TEXT,               -- step_id, error message, or other context
    recorded_at  REAL NOT NULL       -- same time.time() sample as jobs.updated_at
);

-- 2.3  Local action receipts (idempotency fence)
CREATE TABLE receipts (
    action_key   TEXT PRIMARY KEY,   -- (job_id, step_id, input_hash) formatted as
                                     --   "{job_id}:{step_id}:{input_hash_full}"
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    step_id      TEXT NOT NULL,      -- one of: validate, create_receipt, summarize
    payload_hash TEXT NOT NULL,      -- full SHA-256 of the serialised step inputs
    result       TEXT NOT NULL,      -- serialised step result; never NULL after commit
    committed_at REAL NOT NULL
);
```

### 2.4 Append-only enforcement for `history`

Triggers enforce immutability at the database level, independent of the
application layer:

```sql
CREATE TRIGGER history_no_update
    BEFORE UPDATE ON history
BEGIN SELECT RAISE(ABORT, 'history rows are immutable'); END;

CREATE TRIGGER history_no_delete
    BEFORE DELETE ON history
BEGIN SELECT RAISE(ABORT, 'history rows are immutable'); END;
```

### 2.5 Index hints (deferred until implementation)

```sql
CREATE INDEX ix_jobs_status       ON jobs(status);
CREATE INDEX ix_jobs_expiry       ON jobs(lease_expiry)
    WHERE status NOT IN ('DONE','FAILED','NEEDS_REVIEW');
CREATE INDEX ix_history_job       ON history(job_id, seq);
CREATE INDEX ix_receipts_job_step ON receipts(job_id, step_id);
```

---

## 3. Status model and allowed transitions

```
              ┌──────────┐
              │  PENDING │  (job created; also entered via create_job)
              └────┬─────┘
                   │ claim()
                   ▼
              ┌──────────┐
              │  CLAIMED │  (lease held; input verified; no step executing)
              └────┬─────┘
                   │ begin_step()
                   ▼
              ┌──────────┐
              │  RUNNING │  (one step executing under current lease)
              └──┬───────┘
                 │ commit_step()  ────────────────────────────┐
                 ▼                                            │ if next step exists
           ┌────────────┐   complete() (all steps done)       │
           │ CHECKPOINT │ ───────────────────────────► ┌──────▼──┐
           └─────┬──────┘                              │  DONE   │ (terminal; read-only)
                 │ claim() takeover (expiry <= now)     └─────────┘
                 ▼
              CLAIMED  → RUNNING → … (Worker B continues)

    From RUNNING or CHECKPOINT, on guard failure or unknown outcome:
              ┌──────────┐        ┌──────────────┐
              │  FAILED  │        │ NEEDS_REVIEW │ (terminal in MVP)
              └──────────┘        └──────────────┘
```

### 3.1 Transition table (complete)

| From         | To           | Trigger                   | Guards (all must hold inside `BEGIN IMMEDIATE`)                            |
|--------------|--------------|---------------------------|----------------------------------------------------------------------------|
| —            | PENDING      | `create_job()`            | job_id does not already exist                                              |
| PENDING      | CLAIMED      | `claim()`                 | status = PENDING; input_hash verified = stored (see §4.1)                 |
| CLAIMED      | CLAIMED      | `claim()` takeover        | status = CLAIMED **and** lease_expiry <= now                               |
| RUNNING      | CLAIMED      | `claim()` takeover        | status = RUNNING **and** lease_expiry <= now                               |
| CHECKPOINT   | CLAIMED      | `claim()` takeover        | status = CHECKPOINT **and** lease_expiry <= now                            |
| CLAIMED      | RUNNING      | `begin_step()`            | status = CLAIMED, owner = my_id, gen = my_gen, lease_expiry > now          |
| RUNNING      | CHECKPOINT   | `commit_step()` (not last)| status = RUNNING, owner = my_id, gen = my_gen, lease_expiry > now          |
| RUNNING      | DONE         | `commit_step()` (last)    | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry > now          |
| CHECKPOINT   | RUNNING      | `begin_step()` (resume)   | status = CHECKPOINT, owner = my_id, gen = my_gen, lease_expiry > now       |
| RUNNING      | FAILED       | `fail()`                  | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry > now          |
| RUNNING      | NEEDS_REVIEW | `unknown_outcome()`       | status = RUNNING, owner = my_id, gen = my_gen, lease_expiry > now          |
| CHECKPOINT   | NEEDS_REVIEW | `schema_mismatch()`       | status = CHECKPOINT, owner = my_id, gen = my_gen, lease_expiry > now       |
| CLAIMED      | FAILED       | `fail()` (mismatch)       | status = CLAIMED, owner = my_id, gen = my_gen, lease_expiry > now          |
| FAILED       | PENDING      | `reset()`                 | operator-only; no evidence required                                        |
| DONE         | —            | (none)                    | DONE is final; all accesses are read-only                                  |
| NEEDS_REVIEW | —            | (none)                    | NEEDS_REVIEW is terminal in MVP; no automated path out                     |

**`now`** = the single `time.time()` float sampled in Python **after**
`conn.execute("BEGIN IMMEDIATE")` returns without raising. It is passed as a
bound parameter to all SQL statements in that transaction.  
**Lease valid** iff `lease_expiry > now`.  
**Lease expired** iff `lease_expiry <= now` (or `lease_expiry IS NULL`).

---

## 4. Transaction protocols

All mutating operations use `BEGIN IMMEDIATE`. If SQLite raises
`sqlite3.OperationalError` ("database is locked"), the caller backs off and
retries up to a configurable limit before returning an error to the main loop.
On any other exception, the Python `with conn:` context manager rolls back the
entire transaction; no partial changes persist.

### 4.1 `create_job()`

```python
now = time.time()                        # before BEGIN IMMEDIATE is fine for
                                         # creation; no concurrent expiry race
conn.execute("BEGIN IMMEDIATE")
conn.execute("""
    INSERT INTO jobs(id, input_hash, status, generation, created_at, updated_at)
    VALUES (?, ?, 'PENDING', 0, ?, ?)
    """, (job_id, sha256_canonical_json(input_blob), now, now))
conn.execute("""
    INSERT INTO history(job_id, from_status, to_status, worker, generation,
                        note, recorded_at)
    VALUES (?, NULL, 'PENDING', NULL, 0, 'job created', ?)
    """, (job_id, now))
conn.commit()
```

`sha256_canonical_json(input_blob)` is the full 64-character hex SHA-256 of the
UTF-8 bytes of the canonically serialised JSON input (keys sorted, no extra
whitespace).

### 4.2 `claim()` — unified fresh claim and expired-lease takeover

The input-hash check runs **inside** `BEGIN IMMEDIATE`, before any mutation.
If the hash mismatches the transaction is rolled back without writing a single
history row or altering the job row.

```python
now = None

conn.execute("BEGIN IMMEDIATE")
now = time.time()                        # sampled AFTER lock acquired

row = conn.execute("""
    SELECT generation, status, input_hash, lease_expiry
      FROM jobs WHERE id = ?
    """, (job_id,)).fetchone()

if row is None:
    conn.rollback(); raise JobNotFound

stored_hash = row["input_hash"]
presented_hash = sha256_canonical_json(presented_input)

if presented_hash != stored_hash:
    # Mismatch detected BEFORE any mutation; roll back entirely
    conn.rollback()
    raise InputHashMismatch(f"expected {stored_hash}, got {presented_hash}")

st, old_gen, expiry = row["status"], row["generation"], row["lease_expiry"]

claimable = (
    st == "PENDING"
    or (st in ("CLAIMED", "RUNNING", "CHECKPOINT") and expiry is not None and expiry <= now)
)
if not claimable:
    conn.rollback(); raise NotClaimable(st)

new_gen = old_gen + 1
new_expiry = now + LEASE_TTL

conn.execute("""
    UPDATE jobs
       SET status='CLAIMED', owner=?, lease_expiry=?, generation=?, updated_at=?
     WHERE id=? AND generation=?
    """, (my_id, new_expiry, new_gen, now, job_id, old_gen))

if conn.execute("SELECT changes()").fetchone()[0] != 1:
    conn.rollback(); raise FencingConflict   # concurrent writer won

conn.execute("""
    INSERT INTO history(job_id, from_status, to_status, worker, generation,
                        note, recorded_at)
    VALUES (?, ?, 'CLAIMED', ?, ?, NULL, ?)
    """, (job_id, st, my_id, new_gen, now))
conn.commit()
return new_gen, new_expiry
```

### 4.3 `renewal()`

Renewal extends an unexpired lease. The `lease_expiry > now` guard prevents
resurrecting an expired lease; a worker whose lease has lapsed must halt or
re-claim.

```python
conn.execute("BEGIN IMMEDIATE")
now = time.time()

conn.execute("""
    UPDATE jobs
       SET lease_expiry=?, updated_at=?
     WHERE id=? AND owner=? AND generation=?
       AND status IN ('CLAIMED','RUNNING','CHECKPOINT')
       AND lease_expiry > ?
    """, (now + LEASE_TTL, now, job_id, my_id, my_gen, now))

if conn.execute("SELECT changes()").fetchone()[0] != 1:
    conn.rollback(); raise LeaseExpiredOrStolen
conn.commit()
```

The main loop calls `renewal()` every `HEARTBEAT_INTERVAL` seconds (default
10 s; `LEASE_TTL` default 30 s). No background thread is used; between steps
the main loop handles renewal synchronously.

### 4.4 `commit_step()` — atomic receipt + checkpoint/done + history

This is the single path for recording a completed step. Receipt, job status
change, and history row are committed in **one transaction**. There is no
"receipt now, checkpoint later" path; that non-atomic pattern is prohibited.

```python
action_key = f"{job_id}:{step_id}:{stored_input_hash}"   # full hash

conn.execute("BEGIN IMMEDIATE")
now = time.time()

# Full guard: owner, generation, status, unexpired lease
row = conn.execute("""
    SELECT 1 FROM jobs
     WHERE id=? AND owner=? AND generation=? AND status='RUNNING'
       AND lease_expiry > ?
    """, (job_id, my_id, my_gen, now)).fetchone()
if row is None:
    conn.rollback(); raise GuardFailed

# Attempt receipt insert
try:
    conn.execute("""
        INSERT INTO receipts(action_key, job_id, step_id, payload_hash,
                             result, committed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (action_key, job_id, step_id, payload_hash, serialised_result, now))
except sqlite3.IntegrityError:
    # Receipt already exists → recovery path; see §5
    conn.rollback()
    return _recover_existing_receipt(conn, job_id, step_id, action_key,
                                     payload_hash, my_id, my_gen)

# Determine next status
is_last_step = (step_id == LAST_STEP)   # LAST_STEP = "summarize"
new_status = "DONE" if is_last_step else "CHECKPOINT"
new_checkpoint = None if is_last_step else build_checkpoint_json(step_id)

conn.execute("""
    UPDATE jobs
       SET status=?, checkpoint=?, step_cursor=?, updated_at=?
     WHERE id=? AND owner=? AND generation=? AND status='RUNNING'
       AND lease_expiry > ?
    """, (new_status, new_checkpoint, step_id, now,
          job_id, my_id, my_gen, now))

if conn.execute("SELECT changes()").fetchone()[0] != 1:
    conn.rollback(); raise GuardFailed

conn.execute("""
    INSERT INTO history(job_id, from_status, to_status, worker, generation,
                        note, recorded_at)
    VALUES (?, 'RUNNING', ?, ?, ?, ?, ?)
    """, (job_id, new_status, my_id, my_gen, step_id, now))
conn.commit()
```

### 4.5 Recovery path — existing receipt found

Called when `commit_step()` encounters a UNIQUE conflict on `action_key`.
Verifies the receipt is consistent before advancing the checkpoint; any
inconsistency fails closed to NEEDS_REVIEW.

```python
def _recover_existing_receipt(conn, job_id, step_id, action_key,
                               payload_hash, my_id, my_gen):
    conn.execute("BEGIN IMMEDIATE")
    now = time.time()

    # Re-check lease/status guard under new lock
    row = conn.execute("""
        SELECT status, step_cursor, checkpoint FROM jobs
         WHERE id=? AND owner=? AND generation=?
           AND status IN ('RUNNING','CHECKPOINT') AND lease_expiry > ?
        """, (job_id, my_id, my_gen, now)).fetchone()
    if row is None:
        conn.rollback(); raise GuardFailed

    # Read existing receipt
    r = conn.execute("""
        SELECT payload_hash, result FROM receipts WHERE action_key=?
        """, (action_key,)).fetchone()

    if r is None:
        # Receipt vanished between the UNIQUE error and this read — logic error
        conn.rollback()
        _fail_closed(conn, job_id, my_id, my_gen,
                     "receipt disappeared during recovery")
        return

    # Verify identity: payload_hash must match
    if r["payload_hash"] != payload_hash:
        conn.rollback()
        _fail_closed(conn, job_id, my_id, my_gen,
                     f"receipt payload_hash mismatch on recovery for {step_id}")
        return

    # Verify the checkpoint is ordered — step_cursor must not be ahead of step_id
    current_cursor = row["step_cursor"]
    step_order = ["validate", "create_receipt", "summarize"]
    if (current_cursor is not None
            and step_order.index(current_cursor) >= step_order.index(step_id)):
        # Progress would regress; checkpoint is already past this step
        # (This step was already committed and checkpointed; skip normally)
        conn.rollback()
        return r["result"]   # caller uses this result without any DB write

    # Advance checkpoint (receipt already committed; only checkpoint+history missing)
    is_last = (step_id == "summarize")
    new_status = "DONE" if is_last else "CHECKPOINT"
    new_checkpoint = None if is_last else build_checkpoint_json(step_id)

    conn.execute("""
        UPDATE jobs SET status=?, checkpoint=?, step_cursor=?, updated_at=?
         WHERE id=? AND owner=? AND generation=?
           AND status IN ('RUNNING','CHECKPOINT') AND lease_expiry > ?
        """, (new_status, new_checkpoint, step_id, now,
              job_id, my_id, my_gen, now))

    if conn.execute("SELECT changes()").fetchone()[0] != 1:
        conn.rollback(); raise GuardFailed

    conn.execute("""
        INSERT INTO history(job_id, from_status, to_status, worker, generation,
                            note, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (job_id, row["status"], new_status, my_id, my_gen,
              f"recovered:{step_id}", now))
    conn.commit()
    return r["result"]
```

### 4.6 `fail()` and `unknown_outcome()`

Both use the same four-part guard: owner, generation, active status, unexpired
lease. Both commit the job update and history row atomically.

```python
# fail() — known error, job is definitively failed
conn.execute("BEGIN IMMEDIATE")
now = time.time()
conn.execute("""
    UPDATE jobs SET status='FAILED', failure_msg=?, updated_at=?
     WHERE id=? AND owner=? AND generation=?
       AND status IN ('RUNNING','CLAIMED') AND lease_expiry > ?
    """, (msg, now, job_id, my_id, my_gen, now))
if conn.execute("SELECT changes()").fetchone()[0] != 1:
    conn.rollback(); raise GuardFailed
conn.execute("""
    INSERT INTO history(job_id, from_status, to_status, worker, generation,
                        note, recorded_at)
    VALUES (?, ?, 'FAILED', ?, ?, ?, ?)
    """, (job_id, current_status, my_id, my_gen, msg, now))
conn.commit()

# unknown_outcome() — outcome cannot be locally verified; terminal in MVP
conn.execute("BEGIN IMMEDIATE")
now = time.time()
conn.execute("""
    UPDATE jobs SET status='NEEDS_REVIEW', failure_msg=?, updated_at=?
     WHERE id=? AND owner=? AND generation=?
       AND status='RUNNING' AND lease_expiry > ?
    """, (reason, now, job_id, my_id, my_gen, now))
if conn.execute("SELECT changes()").fetchone()[0] != 1:
    conn.rollback(); raise GuardFailed
conn.execute("""
    INSERT INTO history(job_id, from_status, to_status, worker, generation,
                        note, recorded_at)
    VALUES (?, 'RUNNING', 'NEEDS_REVIEW', ?, ?, ?, ?)
    """, (job_id, my_id, my_gen, reason, now))
conn.commit()
```

### 4.7 `_fail_closed()` — internal error path

Any inconsistency detected during recovery transitions the job to NEEDS_REVIEW
rather than FAILED, so that an operator must inspect before any retry:

```python
def _fail_closed(conn, job_id, my_id, my_gen, reason):
    conn.execute("BEGIN IMMEDIATE")
    now = time.time()
    conn.execute("""
        UPDATE jobs SET status='NEEDS_REVIEW', failure_msg=?, updated_at=?
         WHERE id=? AND owner=? AND generation=?
           AND status IN ('RUNNING','CLAIMED','CHECKPOINT') AND lease_expiry > ?
        """, (reason, now, job_id, my_id, my_gen, now))
    conn.execute("""
        INSERT INTO history(job_id, from_status, to_status, worker, generation,
                            note, recorded_at)
        VALUES (?, ?, 'NEEDS_REVIEW', ?, ?, ?, ?)
        """, (job_id, "?", my_id, my_gen, f"fail_closed: {reason}", now))
    conn.commit()
```

### 4.8 `reset()` — operator path out of FAILED

`reset()` transitions FAILED → PENDING, incrementing generation. It does not
alter existing receipts or history rows. Because `job_id` and `input_hash` are
immutable, the same action keys apply on the next claim; existing committed
receipts will block re-execution of already-completed steps.

```python
conn.execute("BEGIN IMMEDIATE")
now = time.time()
conn.execute("""
    UPDATE jobs SET status='PENDING', owner=NULL, lease_expiry=NULL,
        generation=generation+1, failure_msg=NULL, updated_at=?
     WHERE id=? AND status='FAILED'
    """, (now, job_id))
if conn.execute("SELECT changes()").fetchone()[0] != 1:
    conn.rollback(); raise JobNotFailed
conn.execute("""
    INSERT INTO history(job_id, from_status, to_status, worker, generation,
                        note, recorded_at)
    VALUES (?, 'FAILED', 'PENDING', NULL, (SELECT generation FROM jobs WHERE id=?),
            'operator reset', ?)
    """, (job_id, job_id, now))
conn.commit()
```

---

## 5. Fencing and stale-write rejection

Every mutating statement includes `owner = my_id`, `generation = my_gen`,
and `lease_expiry > now` (the `now` sampled after lock acquisition). On
rowcount = 0 the entire transaction is rolled back and the worker halts — it
must not execute further steps, insert receipts, or trigger any side effect.

**No window for dual success**: `BEGIN IMMEDIATE` acquires an exclusive write
lock before `time.time()` is sampled; two concurrent writers cannot both
observe rowcount = 1 for the same generation.

**Stale write sequence (illustrative — untested):**
```
Worker A: claim → gen=1, expiry=T+30
                          [A pauses 40 s; lease expired at T+30]
                          Worker B: takeover → gen=2, expiry=T+70
Worker A: resumes; commit_step(gen=1)
          → lease_expiry(T+30) <= now(T+40) → rowcount=0 → ROLLBACK; A halts
```

---

## 6. Checkpoint JSON structure and schema version

```json
{
  "schema_version": 1,
  "step_cursor": "validate",
  "completed_steps": ["validate"]
}
```

`completed_steps` is the ordered list of all successfully committed steps so
far. On recovery, the worker verifies that the checkpoint's `completed_steps`
exactly matches the receipts present in the `receipts` table (same keys, same
`payload_hash`, same `result`). Any discrepancy calls `_fail_closed()`.

A worker reading `schema_version` it does not recognise calls
`schema_mismatch()` (→ NEEDS_REVIEW) before executing any step.

---

## 7. Action identity and idempotency

### 7.1 Action key

```
action_key = f"{job_id}:{step_id}:{input_hash_full}"
```

`input_hash_full` is the full 64-character hex SHA-256 stored in `jobs.input_hash`.
Examples:
```
job-001:validate:a3f82c91d2e4b6f8a1c3e5d7f9b2d4e6f8a1c3e5d7f9b2d4e6f8a1c3e5d7f9b2
job-001:create_receipt:a3f82c91d2e4b6f8a1c3e5d7f9b2d4e6f8a1c3e5d7f9b2d4e6f8a1c3e5d7f9b2
job-001:summarize:a3f82c91d2e4b6f8a1c3e5d7f9b2d4e6f8a1c3e5d7f9b2d4e6f8a1c3e5d7f9b2
```

The key is **stable across all lease transfers, crashes, and replays**:
Worker B constructs the identical key from the same `job_id`, `step_id`, and
the `input_hash` already stored in the `jobs` row. No claim-time or run-time
metadata is embedded.

Because `job_id` and `input_hash` are immutable, a different input requires a
distinct `job_id`; there is no ambiguity about which input produced a receipt.

### 7.2 `payload_hash`

`payload_hash` in `receipts` is the full SHA-256 of the serialised step-specific
inputs (which may be a subset of the full job input). It is verified on recovery
to confirm the existing receipt was produced by identical inputs. A mismatch
indicates a logic error and fails closed to NEEDS_REVIEW.

---

## 8. Input-hash verification

The presented input blob is checked against `jobs.input_hash` **inside
`BEGIN IMMEDIATE`**, before any UPDATE or INSERT. On mismatch the transaction
is rolled back without writing a single byte to `jobs`, `history`, or
`receipts`. This preserves the complete prior audit trail.

The check is performed at claim time. Once a worker holds a CLAIMED lease it
has already verified the hash; `begin_step()` does not re-read input from the
caller (it reads from the checkpoint state).

---

## 9. Append-only transition history

History rows are never updated or deleted (enforced by §2.4 triggers). Every
status change writes exactly one history row in the same transaction as the
`jobs` UPDATE. Workers never read history for control flow. The `seq` column
provides a total order per job.

Every path — including `_fail_closed()`, `schema_mismatch()`, recovery
checkpoint advance, and operator `reset()` — writes a history row.

---

## 10. NEEDS_REVIEW: terminal in MVP

**No external adapters exist in this MVP.** Any step that would call an
external system is represented by a local synthetic function. If that function
cannot produce a locally verifiable result, it raises `UnknownOutcomeError` and
the job transitions to NEEDS_REVIEW.

NEEDS_REVIEW is **terminal in this MVP**. There is no automated path out.
The rationale: providing a verdict + file + hash is insufficient evidence
without an external system to verify against; and no external system exists
in this scope.

**Synthetic fixture (test/demo only):** a specially marked test fixture may
hard-code a pre-agreed outcome and call an internal `_synthetic_reconcile()`
function that writes a DONE or FAILED transition with a fixture-generated
evidence record. This path must be clearly labelled `# SYNTHETIC-FIXTURE-ONLY`
and must never be reachable from the production CLI. It exists solely to
demonstrate the DONE transition in an offline demo.

**Future scope:** if a real external system is later integrated, reconcile
requires independently verifiable evidence (external system receipt ID, signed
response, or equivalent); merely supplying a local file hash is insufficient.

Transitions removed from MVP:
- `NEEDS_REVIEW → DONE` (production reconcile — no adapter)
- `NEEDS_REVIEW → FAILED` (production reconcile — no adapter)
- `NEEDS_REVIEW → PENDING` (production retry — no adapter)

---

## 11. Clock assumptions and lease TTL

**Timestamp source**: `time.time()` in Python, called **once per
transaction**, **after** `conn.execute("BEGIN IMMEDIATE")` returns. This float
is passed as a bound parameter to all SQL statements in the transaction.
No SQLite time functions are called; this avoids `unixepoch('now','subsec')`
version dependency.

**Validity convention**: lease valid iff `lease_expiry > now`;
expired iff `lease_expiry <= now` (or `lease_expiry IS NULL`).

**LEASE_TTL**: 30 s (configurable). **Heartbeat interval**: 10 s (configurable).
The main loop renews synchronously between steps; no background thread.

**Forward wall-clock jump** (e.g. NTP step-forward of Δ seconds): `now` inside
the next transaction will be larger by Δ. If `now > lease_expiry`, the renewal
guard fails → rowcount = 0 → worker halts. Another worker may then take over
normally. This is safe: the generation fence prevents the original holder from
writing after takeover.

**Backward wall-clock jump** (e.g. NTP step-back of Δ seconds): `now` inside
the next transaction will be smaller than expected. A lease that should have
expired may appear live for up to Δ additional seconds. This is a known
limitation for a single-machine demo; no NTP correction is expected during
a short hackathon run. The generation fence still serialises DB writes; the
risk is only that takeover is delayed, not that two workers both commit.

**Same-owner expiry**: a paused worker's own `lease_expiry <= now` causes
renewal to fail (rowcount = 0). It must halt or re-claim (new generation).
Renewal never resurrects an expired lease.

**No cross-process clock skew**: both workers read the same OS clock on the
same machine; skew is zero for this design.

**Fencing scope**: the generation counter serialises local database effects.
It does not and cannot guarantee exactly-once execution of real-world actions
outside the database.

---

## 12. Operational-error and rollback handling

| Condition | Action |
|-----------|--------|
| `sqlite3.OperationalError: database is locked` | Retry with exponential back-off up to `MAX_RETRIES` (suggested: 5); then raise to main loop |
| Any other exception inside a transaction | Python `with conn:` context rolls back automatically; worker logs error and halts the current step |
| rowcount = 0 after guarded UPDATE | Rollback any partially written rows in same transaction; raise `GuardFailed`; worker halts |
| `IntegrityError` on receipt INSERT | Triggers recovery path (§4.5); worker does not halt |
| `InputHashMismatch` from `claim()` | Transaction already rolled back; worker calls `fail()` or lets lease expire |

---

## 13. Demo walkthrough (design-level; untested)

```
Setup:    LEASE_TTL=30s, HEARTBEAT=10s
          Steps: validate → create_receipt → summarize
          Input: {"amount": 100, "currency": "ZAR"}  (canonical JSON, keys sorted)
          input_hash = sha256('{"amount":100,"currency":"ZAR"}')
                     = "b94f6f125..." (illustrative placeholder)

Step D0:  create_job(job_id="job-001", input_blob=b'{"amount":100,"currency":"ZAR"}')
          → jobs: id=job-001, input_hash=b94f6f125..., status=PENDING, gen=0
          → history: seq=1 [NULL→PENDING, gen=0, "job created"]

Step D1:  Worker A: claim("job-001", presented_input=input_blob)
          BEGIN IMMEDIATE; now=T0
          hash check: sha256(presented)==b94f6f125... ✓
          UPDATE jobs SET status=CLAIMED, owner=A, lease_expiry=T0+30, gen=1
          history: seq=2 [PENDING→CLAIMED, gen=1, worker=A]
          COMMIT
          A holds: (gen=1, expiry=T0+30)

Step D2:  Worker A: begin_step("validate")
          BEGIN IMMEDIATE; now=T1 (T1<T0+30; lease valid)
          UPDATE jobs SET status=RUNNING ... WHERE gen=1 AND lease_expiry>T1
          history: seq=3 [CLAIMED→RUNNING, gen=1, step=validate]
          COMMIT

Step D3:  Worker A: commit_step("validate", result={"valid":true})
          action_key = "job-001:validate:b94f6f125..."
          payload_hash = sha256(serialise(validate_inputs))
          BEGIN IMMEDIATE; now=T2
          INSERT receipts (action_key, ..., result='{"valid":true}')  ← succeeds
          UPDATE jobs SET status=CHECKPOINT, step_cursor=validate,
              checkpoint='{"schema_version":1,"step_cursor":"validate","completed_steps":["validate"]}'
          history: seq=4 [RUNNING→CHECKPOINT, gen=1, step=validate]
          COMMIT

Step D4:  Worker A: [simulated crash]
          lease expires at T0+30

Step D5:  Worker B: polls; sees CHECKPOINT, lease_expiry=T0+30 <= now=T0+35
          claim("job-001", presented_input=input_blob)
          BEGIN IMMEDIATE; now=T0+35
          hash check ✓; expiry T0+30 <= T0+35 → takeover
          UPDATE jobs SET status=CLAIMED, owner=B, lease_expiry=T0+65, gen=2
          history: seq=5 [CHECKPOINT→CLAIMED, gen=2, worker=B]
          COMMIT

Step D6:  Worker B: reads checkpoint → completed_steps=["validate"]
          Verifies receipt for (job-001, validate, b94f6f125...) exists and
          payload_hash matches → checkpoint consistent

Step D7:  Worker B: begin_step("create_receipt")
          BEGIN IMMEDIATE; UPDATE jobs SET status=RUNNING WHERE gen=2 AND expiry>now
          history: seq=6 [CLAIMED→RUNNING, gen=2, step=create_receipt]
          COMMIT

Step D8:  Worker B: commit_step("create_receipt", result={"receipt_id":"R-42"})
          action_key = "job-001:create_receipt:b94f6f125..."
          INSERT receipts ← succeeds (first execution)
          UPDATE jobs SET status=CHECKPOINT, step_cursor=create_receipt
          history: seq=7 [RUNNING→CHECKPOINT, gen=2, step=create_receipt]
          COMMIT

Step D9:  Worker B: begin_step("summarize")
          BEGIN IMMEDIATE; UPDATE jobs SET status=RUNNING ...
          history: seq=8 [CHECKPOINT→RUNNING, gen=2, step=summarize]
          COMMIT

Step D10: Worker B: commit_step("summarize", result={"summary":"done"})
          action_key = "job-001:summarize:b94f6f125..."
          INSERT receipts ← succeeds
          UPDATE jobs SET status=DONE, checkpoint=NULL, step_cursor=summarize
          history: seq=9 [RUNNING→DONE, gen=2, step=summarize]
          COMMIT

Step D11: Operator: cli status job-001
          status=DONE, owner=B, gen=2, step_cursor=summarize
          receipts: 3 rows (validate, create_receipt, summarize)
          history: 9 rows (D0–D10 above)
          Further write attempts on this job are rejected (status=DONE, no
          active transitions available).
```

---

## 14. Fault-injection test cases (acceptance criteria; none executed)

| # | Scenario | Setup | Expected outcome |
|---|----------|-------|------------------|
| T1 | **Crash before atomic commit** | Worker A begins `commit_step("validate")` but process dies before `COMMIT` | SQLite rolls back the partial transaction; job remains RUNNING; lease expires; Worker B takes over (gen+1); re-executes validate, commits atomically |
| T2 | **Crash after atomic commit** | Worker A commits receipt+checkpoint for validate, then dies | Job in CHECKPOINT; Worker B takes over; recovery path verifies receipt identity/payload/result; skips re-execution; advances from checkpoint |
| T3 | **Concurrent claimants on PENDING** | Two workers simultaneously call `claim()` | One wins (exclusive write lock); other sees `FencingConflict` (rowcount=0) and backs off |
| T4 | **Concurrent claimants on expired RUNNING** | Both workers detect expired RUNNING and call `claim()` | Same as T3; generation guard fires |
| T5 | **Stale write after takeover** | Worker A (expired gen=1) calls `commit_step()` after B holds gen=2 | `lease_expiry <= now` AND `generation` mismatch → rowcount=0 → ROLLBACK; A halts; B undisturbed |
| T6 | **Expired owner receipt attempt** | Worker A (expired lease) calls `commit_step()` | `lease_expiry > now` guard fails inside `BEGIN IMMEDIATE`; receipt not inserted; transaction rolled back; A halts |
| T7 | **Input hash mismatch at claim** | Worker presents input Y; job was created with input X (different hash) | `claim()` rolls back inside `BEGIN IMMEDIATE` before any mutation; job row, history, and receipts unchanged; `InputHashMismatch` raised |
| T8 | **Receipt replay: payload_hash match** | Worker B recovers; existing receipt for validate found; payload_hash matches | Recovery advances checkpoint; result reused; no re-execution |
| T9 | **Receipt replay: payload_hash mismatch** | Existing receipt has different payload_hash | `_fail_closed()` transitions to NEEDS_REVIEW; history row written; job halts |
| T10 | **Duplicate receipt across recovery** | Worker A commits receipt; Worker B (new gen) attempts same action_key | UNIQUE fires; recovery path runs; hash verified; result reused; no duplicate execution |
| T11 | **Checkpoint inconsistency: receipt missing** | Checkpoint lists validate as completed but no receipt row exists | Recovery detects missing receipt → `_fail_closed()` → NEEDS_REVIEW |
| T12 | **Checkpoint inconsistency: result mismatch** | Receipt row exists but payload_hash differs from checkpoint expectation | `_fail_closed()` → NEEDS_REVIEW |
| T13 | **Progress regression attempt** | Recovery worker tries to re-run a step already ahead in checkpoint cursor | `step_order.index(current_cursor) >= step_order.index(step_id)` check fires; no DB write; result returned from existing receipt |
| T14 | **Lease renewal keeps lease alive** | Worker A heartbeats every 10 s | `lease_expiry` advances; Worker B sees live lease; does not attempt takeover |
| T15 | **Renewal after expiry rejected** | Worker A pauses > 30 s; calls `renewal()` | `lease_expiry > now` guard fails → rowcount=0 → `LeaseExpiredOrStolen`; A must halt |
| T16 | **Expired CLAIMED takeover** | Worker A claims but never calls `begin_step()`; lease expires | Worker B claims (gen+1); A's next renewal fails; B proceeds from CLAIMED |
| T17 | **Checkpoint schema_version unknown** | Checkpoint has `schema_version=99` | Worker calls `schema_mismatch()` → NEEDS_REVIEW; history row written; operator must inspect |
| T18 | **History immutability** | Any process issues UPDATE or DELETE on a history row | Trigger raises `ABORT`; write rejected; history unchanged |
| T19 | **NEEDS_REVIEW is terminal** | Any code path attempts an automated transition out of NEEDS_REVIEW | No such transition exists in production code; only `_synthetic_reconcile()` (test fixture, unreachable from CLI) |
| T20 | **Forward clock jump mid-lease** | OS clock steps forward Δ>30 s while A holds lease | A's next renewal: `now > lease_expiry` → rowcount=0 → A halts; B can take over |
| T21 | **Same-owner expired write attempt** | Worker A pauses > TTL; resumes; calls `commit_step()` | `lease_expiry > now` guard fails inside `BEGIN IMMEDIATE`; rollback; A must re-claim or halt |
| T22 | **DONE job is read-only** | Any worker calls a mutating function on a DONE job | No transition from DONE exists; guard `status IN (...)` excludes DONE; rowcount=0; no mutation |
| T23 | **Operational error / database locked** | SQLite raises `OperationalError: database is locked` | Retry with back-off; after `MAX_RETRIES` exhausted, raise to main loop; job left in last stable state |

---

## 15. CLI surface (design sketch)

All subcommands use `--db FILE` (default: `handoff.db` in CWD).

```
cli.py create   --job-id JOB --input-file FILE
cli.py run      --job-id JOB --worker-id WORKER
                # claim → step loop → heartbeat in main loop → commit steps
cli.py status   --job-id JOB
cli.py history  --job-id JOB [--last N]
cli.py reset    --job-id JOB           # FAILED → PENDING (operator only)
```

`reconcile` is removed from the production CLI in MVP (NEEDS_REVIEW is
terminal). The `_synthetic_reconcile()` fixture is invoked only by test
harnesses, never from the CLI.

---

## 16. What this document does not cover

- Application Python source code (not yet written; no code implemented).
- Dependency installation (none planned; Python 3.9 stdlib + bundled SQLite).
- Deployment, publishing, or submission.
- Telegram integration (deferred; out of MVP scope).
- Production use, distributed workers, or universal exactly-once external
  execution — the design is for a single-machine transactional demo only.
- Prior Bob session evidence (none yet; this task is the first real session).
