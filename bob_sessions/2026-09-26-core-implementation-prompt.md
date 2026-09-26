# Core implementation prompt — prepared by Codex

Status: sent to a new Bob task on 26 September 2026 via the dispatch message below. Bob visibly read AGENTS.md and this prompt. Implementation and verification results will be recorded separately.

## Exact prompt

The user has authorized core implementation. AGENTS.md has been updated accordingly. Read AGENTS.md, docs/ARCHITECTURE.md revision 4, and docs/ARCHITECTURE-REVIEW.md. Implement the core in this isolated repository using Python 3.9 standard library and SQLite only. Treat revision 4 as the authoritative contract; do not restore reset/reconcile, receipt-to-checkpoint repair, or fail-after-input-mismatch paths from earlier designs.

Deliver a small importable Python package, thin CLI, synthetic input fixture, standard-library unittest suite, and reproducible local demo. Implement create/run/recover/status/history. Use explicit transactions, immutable canonical input, full SHA-256 identities, ordered checkpoints, exactly one transactional local receipt per job, generation fencing and strict lease guards sampled after BEGIN IMMEDIATE. DONE replay is read-only. NEEDS_REVIEW is terminal; unknown outcomes remain held. No external actions, arbitrary commands, credentials, production data or providers.

Prove section 9's acceptance matrix with meaningful tests: real independent processes for competing claims and process death before/after commits; snapshots proving input mismatch and stale/expired guards do not mutate any table; invalid durable evidence holds; immutable-record constraints; terminal replay; lock contention and rollback/commit uncertainty handling. Inject clocks without altering the host clock. Fault hooks must be explicitly test/demo scoped, not bypasses for terminal holds. Test bounded retries on Python 3.9 without relying on newer sqlite exception attributes.

The demo must run A through the receipt checkpoint, interrupt A, recover with B after lease expiry, finish summarize, then replay DONE with exactly one receipt and unchanged durable state. Keep demo databases disposable and ignored by Git. Use bounded waits and guaranteed child-process cleanup.

Run tests and a fresh demo; fix failures. Update README with exact commands and limitations. Write docs/IMPLEMENTATION-REPORT.md with files, executed commands, actual results, and any unmet acceptance cases. Do not claim unrun cases passed. Preserve the architecture and existing Bob evidence. Do not commit, deploy, publish, submit, or install dependencies. Stop with a concise completion report so Codex can independently review and verify.

## Actual dispatch message

Implement the core Agent Handoff Kit now. The user approved implementation and AGENTS.md is updated. Read bob_sessions/2026-09-26-core-implementation-prompt.md and follow its Exact prompt in full, plus AGENTS.md and docs/ARCHITECTURE.md revision 4. Deliver the Python 3.9 standard-library SQLite package, CLI, meaningful fault/concurrency tests, reproducible two-worker recovery demo, README commands and honest implementation report. Run and fix tests and demo. No dependencies, external services, deployment, publication, submission or git commit. Preserve prior architecture and evidence. Stop when ready for Codex's independent review.

## Review follow-up 1 (queued during implementation)

Early review findings to address before finishing: schema.init_db currently executes DDL before checking existing version, violating unknown-version no-write rule; check first, including before journal-mode writes. DDL needs finite numeric lease_expiry and lowercase-hex hash constraints. db._LOCK_PHRASES incorrectly classifies disk I/O, unable-to-open and nested transactions as lock contention: retry only actual lock errors. Clock sampling exceptions must roll back too (currently outside try). Commit uncertainty must halt/reopen for read-only validation, never blindly rerun. Input regex should fullmatch to reject trailing newline. Checkpoint version must reject bool and duplicate keys. Add targeted tests for these.

## Review follow-up 2 (queued during implementation)

Worker review to address before completion: run_job must validate the entire durable prefix in a fresh guarded transaction immediately after claim, even when next_step returns None; an active complete-prefix is inconsistent and must hold. _validate_prefix must reject extra result-map entries, non-dict entries, invalid JSON, >3 steps without IndexError, bool-vs-int result substitution (compare canonical bytes), and verify receipt step_id/canonical JSON. Route malformed evidence to guarded NEEDS_REVIEW, terminal corruption to read-only integrity error. Add heartbeat parameter with 0<heartbeat<TTL and main-loop renewal; validate finite positive TTL and worker identity. Implement guarded FAILED and public unknown-outcome hold paths for tests. run_job on DONE must perform read-only replay, not claim. Test these cases as well as direct commit_step no-mutation guards.

## Review follow-up 3 (steered and visibly received)

Use python3 -m unittest discover -s tests -v (Python 3.9.6 is installed as python3; no pytest/dependency installation). Incorporate both queued review messages now and read docs/CODEX-IMPLEMENTATION-REVIEW.md. My first independent run: 63 tests, 7 errors. Fix those plus real step-targeted abrupt process-death coverage, separate A/B subprocesses with actual lease timeout, and no deletion of preexisting demo DB paths. Address all findings, run corrected tests and demo, then update the report with actual results.
