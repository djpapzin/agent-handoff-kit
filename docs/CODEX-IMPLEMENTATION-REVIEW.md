# Codex implementation review — in progress

This is an independent review of Bob's initial implementation, not acceptance.
Review notes can refer to files while Bob is still changing them; findings need
rechecking after its completion.

## Initial test execution

`python3 -m unittest discover -s tests -v` on Python 3.9.6:
63 tests, 7 errors, 1.329 seconds. Six immutability tests expected the wrong
exception (SQLite triggers raise IntegrityError); one stale commit test mixed
an injected clock with wall time. These failures must be fixed and rerun.

## Queued implementation findings

The exact two review messages are preserved in
`bob_sessions/2026-09-26-core-implementation-prompt.md`. They cover schema
version checks before writes, finite lease/hash constraints, accurate lock
classification, rollback on clock failure, uncertain commit handling, strict
input/checkpoint validation, full-prefix validation immediately after claim,
heartbeat configuration, guarded fail/hold operations, and DONE run replay.

## Additional test coverage required

- Current `_worker_run_with_fault` catches SystemExit and returns normally; it
  is not abrupt process death. The hook fires before the first validate commit,
  although the test text claims receipt commit. Replace/supplement with actual
  process termination (os._exit or parent kill) targeted by step AND phase,
  before and after each of validate/receipt/summarize commits. Include a hook
  after receipt insertion but before checkpoint/history to prove atomic rollback.
- Test recovery skips completed steps by asserting event counts, checkpoint
  contents and receipts, not just eventual DONE / receipt count.
- Use real separate A and B processes and short actual lease expiry for the
  demo; do not simulate killing by direct UPDATE of the lease. Guarantee
  finally cleanup/termination for every child and bounded queue reads.
- Concurrent claims must cover PENDING and expired CLAIMED/RUNNING/CHECKPOINT,
  with synchronization and full durable snapshots for losers/stale operations.
- Add meaningful lock-contention, transaction-body rollback, commit-uncertainty,
  unknown-version no-write, malformed checkpoint/receipt, and hold/fail guard
  tests. Do not label untested matrix rows covered.
- Initial demo deletes any supplied --db path on startup. Use a fresh temporary
  directory by default and reject an existing explicit path; never erase a
  caller's existing database. Current demo force-expires the lease, exits A
  cleanly and runs B in the parent, so it does not yet demonstrate the requested
  real two-process crash/expiry/recovery scenario.

## Independent regression checks added by Codex

`tests/test_codex_review.py` checks unknown-schema byte-for-byte no-write,
infinite active expiry rejection, malformed checkpoint holds, DONE run
no-mutation, and status on a missing DB not creating a new file. This file is
Codex-authored and should run with discovery alongside Bob's tests.

Independent five-test regression run after first correction pass: three
failures and two errors. Remaining issues: unknown-version open changes the
SQLite journal header, infinite expiry is accepted, missing-DB status creates
a file, DONE run raises NotClaimable, and >3 checkpoint steps raises IndexError.

## Final disposition

The above implementation findings were resolved by Codex after preserving Bob's
output at e47d4b5. The strict independent tests were restored; all 103 tests pass.
Real before/after-commit crash cases, expired-state races, uncertain commit
observations, and an actual process-kill demo passed. See IMPLEMENTATION-REPORT.md
for evidence, attribution, and remaining physical-failure limitations.
