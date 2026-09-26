# Agent Handoff Kit — architecture revision 4

Design contract for implementation; no runtime code or tests exist yet.
Bob authored revisions 1–3. Revision 3 is preserved at `a6f8449`.
Codex authored this final consolidation and reviewed its transaction paths.
See ARCHITECTURE-REVIEW.md for the disposition of each finding.

## 1. Scope and fixed decisions

One synthetic developer task, two local worker processes, one recovery.
Python 3.9 standard library, SQLite, thin CLI. The only effect is a row in
the demo database. No network actions, production data, Telegram, or agent
provider integration. Bob assists development; it is not a runtime dependency.

The ordered steps are validate, create_receipt, summarize.
Only create_receipt writes a business receipt: exactly one receipt per job.
The other steps store deterministic results in the checkpoint. All effects,
checkpoint changes, and corresponding history entries commit atomically.

Job identity and input are immutable. Every deliberately new execution,
including another execution of identical input, needs a new job ID.
Recovery and replay retain the original job ID and action identity.
There is no reset, delete/recreate, or reconcile command in this MVP.
DONE, FAILED, and NEEDS_REVIEW are terminal. Unknown outcomes stay held.

## 2. Input and durable records

Use a versioned synthetic input object such as
`{"schema_version":1,"task":"build-sample","artifact":"sample.txt"}`.
Reject unknown fields, duplicate JSON keys, unsupported types and versions.
Task/artifact are bounded ASCII identifiers, not filesystem paths;
schema_version is integer 1 (reject booleans). Canonicalization is
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
allow_nan=False).encode("utf-8")`. Hash canonical bytes with full SHA-256.
Store canonical UTF-8 text. Workers use this stored immutable input after
checking the caller's presented input at claim time. Never re-read a mutable
input file while executing steps.

Proposed tables (implement DDL against these field contracts):

| Table | Fields and constraints |
|---|---|
| jobs | job_id TEXT PRIMARY KEY NOT NULL; input_json TEXT NOT NULL; input_hash TEXT NOT NULL (64 lowercase hex); status in PENDING/CLAIMED/RUNNING/CHECKPOINT/DONE/FAILED/NEEDS_REVIEW; nullable owner/lease_expiry; nonnegative generation; checkpoint_json TEXT NOT NULL; nullable active_step, failure reason; created_at, updated_at |
| receipts | job_id TEXT PRIMARY KEY NOT NULL REFERENCES jobs; step_id constrained to create_receipt; full input_hash; full payload_hash; canonical result_json; full result_hash; committed_at; composite UNIQUE(job_id, step_id, input_hash) |
| history | autoincrement integer seq; job_id FK; old/new status (old NULL on creation); owner; generation; event/step/reason; recorded_at. Append-only, no update/delete |

The receipt primary key independently enforces at most one business effect
per job. Its action identity is the composite tuple
`(job_id, 'create_receipt', full_input_hash)`, never a concatenated string
with ambiguous delimiters, truncated hash, owner, time, or generation.

Enable foreign_keys=ON on every connection. Use local-disk WAL and
synchronous=FULL; do not share the DB over a network filesystem.
Check the database schema version on open; reject unknown versions without
writing anything. An unsupported checkpoint version inside a known DB schema
is instead a guarded NEEDS_REVIEW transition after claim.

DDL must prevent updating/deleting history and receipts, changing job
identity/input, or deleting jobs (triggers plus constraints). Enforce owner
and finite lease expiry for active states. PENDING/terminal states have owner,
lease expiry and active_step NULL. These rules protect normal use, not an
attacker with arbitrary SQLite/schema access.

## 3. Checkpoint and deterministic results

Keep a checkpoint even at DONE. It contains schema_version=1, an ordered
completed_steps list, and a result record for every completed step with
canonical result plus SHA-256. Empty list/map is the initial checkpoint.
No duplicate, skipped, unknown or reordered steps are permitted. Derive the
cursor from completed_steps rather than maintaining a second divergent cursor.

validate returns a fixed validation result for the supported input schema.
create_receipt returns a deterministic record containing job ID, input hash
and the synthetic artifact identifier. That record is the entire local
business effect. summarize derives a deterministic summary from the two prior
results. Define these pure functions once for execution and checking.
Timestamps are metadata, excluded from result/payload hashes.

Recovery validates the entire ordered prefix and result map. Recompute
expected deterministic results from stored input and prior verified results.
For completed create_receipt, require exactly one receipt whose job, step,
full input hash, canonical payload hash, result and result hash all match.
Before create_receipt completes, require zero receipts. A receipt ahead of
the checkpoint, a missing receipt, altered result, unsupported version, or
non-prefix checkpoint is inconsistent: hold for review, never repair the
checkpoint by guessing from a receipt. Atomic commit means such divergence
is not a legitimate crash state in this design.

## 4. Transaction discipline and lease semantics

Every mutation uses the same explicit transaction wrapper:

1. Execute BEGIN IMMEDIATE and wait for the write reservation.
2. Sample Python time.time() once, after the lock is acquired. Bind that
   value throughout this transaction. Reject non-finite clock values.
3. Read current rows and validate preconditions under the lock.
4. Perform guarded updates; require exactly one affected job row. Insert
   any receipt and audit record within this same transaction.
5. Commit. On any failed guard or exception, explicitly roll back the whole
   transaction (if active) before returning/retrying. Never commit a partial
   receipt or an audit-only change because an exception was swallowed.

Use Python sqlite3 with explicit transaction control (isolation_level=None)
and explicit commit/rollback; do not assume an unshown context manager.

For an existing lease holder, every write requires correct owner, generation,
permitted source status, and lease_expiry > now. Expiry is lease_expiry <= now.
A stale/expired holder rolls back and stops; it must not mark the job FAILED,
insert history, or attempt another effect. Creation and claim are explicit
ownership-acquisition exceptions. All receipt insertion is inside guarded
commit-step; no separate unguarded write API exists. Error/hold transitions
use the same guard and actual old status, never a placeholder audit value.

Claim compares canonical presented input to stored input/hash under the lock
BEFORE any mutation. Mismatch returns InputHashMismatch with job, receipts
and history unchanged. It never calls fail() afterward. If input matches,
claim accepts PENDING or an expired active state, increments generation,
sets owner/expiry, retains checkpoint, clears active_step, and appends actual
old status → CLAIMED atomically. A second claimant re-reads under the lock
and returns NotClaimable while the new lease is live. Missing/invalid active
expiry is corruption, not takeover permission: reject without mutation for
operator inspection.

Immediately after acquisition, validate checkpoint/receipt state under a
fresh guarded transaction before choosing a step. Commit-step rechecks
persisted state. Never rely solely on cached progress or a pre-lock guard.

TTL defaults to 30 seconds, main-loop renewal every 10 seconds, configurable
with 0 < heartbeat < TTL. Each tiny local step is synchronous. Long work may
lose its lease: commit must reject it. Renewal cannot resurrect an expired
lease and sets expiry to max(current_expiry, now+TTL), preventing backward
clock changes from shortening a live lease. Renewal adds an audit event with
unchanged status. Reclaiming after expiry gets a new generation, even for the
same worker ID. Require a fresh unique worker ID per process invocation.

BEGIN IMMEDIATE serializes writers; fencing rejects earlier generations
after takeover. It does not prohibit multiple valid sequential writes by the
same generation. Short transactions keep takeover responsive. Wall-clock
jumps affect availability/expiry timing: forward jumps expire leases early;
backward jumps can delay expiry or make a previously expired lease appear
live if no takeover occurred. Fencing still protects serialized local DB
effects after takeover. No real-time or external exactly-once claim is made.

## 5. Complete transition contract

H means the holder guard from section 4, checked under the write lock.
Every listed transition appends one history event in its transaction.

| From → To | Trigger and additional checks |
|---|---|
| absent → PENDING | create: new ID, valid canonical input, empty checkpoint; duplicate ID is non-mutating AlreadyExists |
| PENDING → CLAIMED | matching input before mutation; acquire generation+1 |
| CLAIMED/RUNNING/CHECKPOINT → CLAIMED | matching input; expiry <= now; acquire generation+1; clear interrupted active_step, retain committed prefix |
| CLAIMED/CHECKPOINT → RUNNING | H; validated prefix; requested step is exactly next; set active_step |
| RUNNING → CHECKPOINT | H; active_step is next nonfinal step; extend prefix by exactly one; commit result, receipt only for create_receipt, and history atomically; clear active_step |
| RUNNING → DONE | H; active_step=summarize; verified two-step prefix and receipt; append final result/prefix; clear owner/expiry/active_step; retain complete checkpoint |
| CLAIMED/RUNNING/CHECKPOINT → NEEDS_REVIEW | H; unsupported checkpoint, contradictory durable evidence or simulated unknown outcome; preserve checkpoint/receipt; record reason; clear ownership/active_step |
| CLAIMED/RUNNING/CHECKPOINT → FAILED | H; known deterministic task error with no uncertain effect; preserve checkpoint/receipt; record reason; clear ownership/active_step |
| CLAIMED/RUNNING/CHECKPOINT → same | H; renewal only; audit heartbeat |
| DONE/FAILED/NEEDS_REVIEW → none | terminal, read-only; no reset/retry/reconcile escape |

Guard failure is not a FAILED transition. Input mismatch is not a FAILED
transition. Transient database errors are not task failures. Invalid caller
step order is a non-mutating rejection; persisted corruption is a hold under H.
An active job with all three steps committed is inconsistent: final step and
DONE must commit together. There is no CLAIMED → DONE shortcut or
CHECKPOINT → DONE path outside final atomic commit.

## 6. Commit, recovery and replay protocol

For each next step, begin-step records RUNNING/active_step/history under H.
Calculate the small pure result from frozen verified inputs. Commit-step
reacquires the write lock, samples now and checks H, active_step and the entire
expected persisted prefix. Reject a changed prefix. For create_receipt,
require no receipt yet, insert deterministic receipt, extend checkpoint,
clear active_step, update status, append history and commit once. For other
steps, omit receipt insertion. Never perform external effects.

A crash before commit leaves none of that transaction; after commit it leaves
all of it. Recovery waits for expiry, claims with a new generation, validates
the durable prefix, and executes only the next uncompleted step. It does not
re-run completed steps to provoke UNIQUE conflicts. Expected duplicate replay
uses read/validation, not INSERT OR IGNORE or blanket IntegrityError handling.

If receipt INSERT unexpectedly conflicts despite the checks, roll back.
Inspect fresh durable state; a valid already-committed state is observed, never
mutated backward. Receipt/checkpoint contradiction is held using a new H
transaction if still owner; otherwise halt. Never treat every integrity error
as success or reuse a result without full validation.

DONE replay performs a consistent read transaction, compares presented input,
verifies complete prefix and single receipt, and returns stored summary.
No claim, heartbeat or history write. A corrupt terminal job returns an
integrity error without rewriting terminal state. FAILED/NEEDS_REVIEW reports
reason and permits inspection only. Retry of uncertain external work is absent,
including test-only backdoors. A synthetic test may inject an unknown outcome
and assert it stays held; it must not invent reconciliation evidence.

## 7. SQLite failures and status reads

Use a bounded busy timeout and at most five backoff retries for recognized
lock contention; roll back first and reacquire/re-read/re-sample each attempt.
For Python 3.9 do not assume newer sqlite_errorcode attributes: centralize the
compatibility mapping for known SQLite lock errors, test it on the target
runtime, and treat unrecognized OperationalError as an error rather than retry.
Never retry a cached transaction after another owner advances generation.

On commit/I/O uncertainty, halt and reopen a healthy connection for read-only
validation. Do not assume rollback proved absence, issue another effect, or
invent a history entry. SQLite recovery plus atomic persisted state must
determine what committed. If the DB cannot be read, report the storage error
without fabricating durable status. A later valid owner validates before work.

Status/history commands use a consistent read transaction. Status shows status,
holder/generation, last completed step, reason, evidence references and next
permitted action. DONE means inspection/replay; FAILED/NEEDS_REVIEW means
inspection only; live active means wait; expired active means recover with
matching input. Read-only status performs no repair.

## 8. CLI and submission boundary

Proposed commands: create --job-id --input-file,
run --job-id --input-file --worker-id, recover --job-id --input-file --worker-id,
status --job-id, and history --job-id; all accept --db.
run/recover share guarded acquisition/validation/step loop. DONE through run
returns verified stored results without writes. No arbitrary SQL, shell
command, path or external URL comes from synthetic input. Demo pauses/faults
are explicit local test flags.

The CLI is the core deliverable, but local CLI alone does not satisfy the
published interactive application-URL requirement. See ORGANIZER-REQUIREMENTS.md.
A future online execution harness can wrap the same synthetic scenario with
isolated per-run databases, bounded runtime and no user-supplied commands/data.
It is packaging work, not a dashboard or production service. Hosting choice,
implementation and publication remain separate work; none occurred here.

## 9. Demo trace and acceptance plan

Design walkthrough (not executed): create → A claims gen1 → validates and
checkpoints → creates the sole receipt and checkpoints → kill A → wait for
expiry → B claims gen2 → validates two-step prefix/receipt → summarizes/DONE
→ replay returns same summary. Receipt count stays one; history persists.
With one begin/commit per step and no heartbeat in the short trace there are
nine events: create, claim A, begin/commit validate, begin/commit receipt,
claim B, begin/commit summarize. Heartbeats add separately identified events.

Required implementation tests; all **unrun**:

| Case | Expected invariant |
|---|---|
| Kill before/after each atomic step commit, including receipt and DONE | Complete transaction or none; resume only missing step; one business receipt |
| Two claimants on PENDING and each expired active state | One live owner; second returns NotClaimable; one audit per winning claim |
| Stale owner after takeover; exact expiry; stale receipt/hold/fail/heartbeat | No job, receipt or history mutation on failed guard |
| Mismatched input on pending, active, expired and DONE replay | Logical snapshots unchanged; no fail() side effect |
| New job with identical input; replay original | Independent new identity; original receipt and prefix never overwritten |
| Invalid checkpoint order/version; receipt ahead/missing; wrong identity/payload/result/hash | Active valid holder records NEEDS_REVIEW; no guessed repair; terminal inspection errors read-only |
| Repeated DONE run/status/history | Complete stored result; no table changes |
| Unknown outcome with verdict/file/hash offered | Still NEEDS_REVIEW; no outgoing transition/backdoor |
| Out-of-order step or replay of prior step while active | Non-mutating caller rejection; no progress regression/extra receipt |
| Renewal before/at/after expiry; forward/backward injected clock jumps | Strict sampled boundary; documented wall-time limitations; stale generation rejected |
| Locked DB, transaction exception, commit uncertainty, process restart | Rollback/re-read discipline; never partial history/effect or blind replay |
| Mutation/deletion of history/receipts/immutable input; duplicate ID; invalid active fields | Constraints/triggers reject; original records retained |
| Final summarize commit and crash | DONE and full checkpoint/history together; no active complete-prefix state |

Inject a clock in tests; do not change the host clock. Run process-death and
concurrent-claim cases with real independent processes. Future implementation
is accepted only when these tests and a fresh-run demo pass. This design
review is not evidence that runtime behavior works.
