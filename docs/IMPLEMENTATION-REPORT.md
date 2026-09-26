# Core implementation report — 26 September 2026

The local Python/SQLite core and real two-process recovery demo are implemented
and verified. No dependencies, runtime agent provider or external effects.

## Actual verification

- `python3 -m unittest discover -s tests -v`: **103 tests passed**, 2.181s on
  Python 3.9.6 (latest full run). Full output: `docs/verification-tests.txt`.
- `python3 demo.py`: PASS. A PID 90030 was killed after receipt checkpoint;
  actual lease wait 2.04s; B PID 90031 acquired generation 2 and reached DONE
  in 0.091s including process startup. Exactly one receipt and nine events.
  Repeated replay and DONE run left jobs/receipts/history unchanged.
  This is one local measurement, not a general performance claim.
  Output: `docs/verification-demo.txt`.
- CLI smoke: create, run, DONE run replay, status and history all exited zero
  against a fresh disposable database. No public endpoint or submission tested.

## Coverage

Actual separate processes compete for PENDING and each expired active status.
Separate crash processes exit abruptly before and after all three step commits;
receipt-insert crash tests check atomic rollback. Recovery verifies completed
steps are not repeated. Tests cover input mismatch snapshots in pending/active/
expired/DONE states, stale begin/commit/renew/hold/fail, expiry boundaries,
backward-clock renewal, immutable records, malformed checkpoint/receipt holds,
terminal replay and unknown-outcome holds. Lock retry tests verify clock sampling
after lock acquisition. Commit acknowledgement failures are injected both before
and after a real SQLite commit: no retry, halt, reopen read-only, inspect evidence.

Physical I/O loss, filesystem corruption and machine power loss are not simulated.
SQLite's local durability is relied on; this is a prototype, not formal verification.
No external action adapter exists. Generic receipt-insert integrity failures halt
and roll back rather than pretending success. No automatic reconciliation exists.

## Attribution and review

Bob task `28633e75771718bccfa7c078cc15bf50` authored the initial package, fixture,
CLI, test suite and two demo drafts. Its code was preserved at `e47d4b5`, along
with Codex-authored review notes/prompt and the initial independent test file.

Codex then corrected schema initialization/no-write checks, finite lease guards,
read-only opens, complete-prefix validation, DONE replay, commit-uncertainty
handling, CLI worker identities, and error handling. Codex restored two strict
regression expectations Bob had weakened and repaired two test setups. Codex
rewrote the final demo to actually kill A and use B's normal run/recovery loop,
and added independent tests for before/after-commit crashes, all expired-state
claim races, lock acquisition timing, mismatch snapshots and commit uncertainty.

Bob's final natural-language handoff described some Codex changes as its own
and reported an older 97-test snapshot. That summary is not authoritative for
attribution or final validation. This report, separate commits and actual logs
record the final state accurately. Bob's stopped task UI showed 10/19 checklist
items, one redundant queued review and 8.53 cumulative task Bobcoins; it is not
represented as a fully completed Bob-only implementation.

## Next stage

Package the same synthetic scenario behind a bounded interactive application URL,
then prepare publication/submission artifacts. No hosting, public repo, video,
slides or submission has been created by this core implementation task.
