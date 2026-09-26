# Agent Handoff Kit

Resume an interrupted synthetic developer task from its last durable checkpoint.
The local demo kills worker A after it commits a receipt, waits for lease expiry,
and starts worker B to finish the job. Replay leaves the receipt and audit history
unchanged. Python 3.9+ standard library and SQLite; no installation or API keys.

## Open the interactive demo

```sh
python3 -m handoff_kit.web
```

Open http://127.0.0.1:8080 and click **Run the recovery demo**. The page shows
actual results from real worker processes: checkpoint, takeover generation,
one receipt, nine audit events and verified unchanged replay. Download the
per-run JSON evidence from the result card. Each run uses a new temporary DB.

The web interface and hosting wrapper are Codex-authored; they execute the
reviewed Bob-assisted core. [Hosting instructions](docs/WEB-DEMO.md).

## Run the CLI demo

```sh
python3 demo.py
```

Uses a fresh temporary database and cleans it up. To keep the database, pass
`--db new-demo.db`; an existing path is rejected. Expected result: generation 1
is interrupted, generation 2 reaches DONE, exactly one receipt, nine history
events, and read-only replay. The two workers are real separate processes.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

109 tests passed on Python 3.9.6 (103 core tests and 6 web tests). Includes concurrent process claims, abrupt
process death before/after every step commit, transaction rollback, stale leases,
malformed evidence, input mismatches and uncertain commit acknowledgement.
See [implementation report](docs/IMPLEMENTATION-REPORT.md) for results and limits.

## CLI

```sh
python3 -m handoff_kit --db local.db create --job-id example-1 --input-file fixtures/input_v1.json
python3 -m handoff_kit --db local.db run --job-id example-1 --input-file fixtures/input_v1.json --worker-id A
python3 -m handoff_kit --db local.db status --job-id example-1
python3 -m handoff_kit --db local.db history --job-id example-1
python3 -m handoff_kit --db local.db run --job-id example-1 --input-file fixtures/input_v1.json --worker-id B
```

`recover` accepts the same options as `run`; it waits for no lease itself and
rejects a currently live owner. `--worker-id` is a label with a fresh UUID added
per CLI invocation. Optional `--ttl` and `--heartbeat` must satisfy
`0 < heartbeat < ttl`. A deliberately new execution requires a new job ID.
The Python API requires callers to provide a fresh worker ID per invocation.

## Guarantees and limits

- Receipt, checkpoint and history commit together in a local SQLite transaction.
- Generation fencing rejects a stale worker after takeover. Input is immutable.
- Unknown outcomes and contradictory evidence remain NEEDS_REVIEW; no reset or
  reconciliation shortcut exists. FAILED and DONE are also terminal.
- Exactly one receipt is a bounded claim about the local synthetic transaction.
  This is not an exactly-once guarantee for external APIs or real-world effects.
- Use a local disk, not a network filesystem. Wall-clock jumps affect lease timing.
  Long work can outlive its lease and must be rejected at commit. Physical disk
  failures and machine power loss have not been tested.

## Provenance and next stage

Bob authored the initial implementation and revised its tests. Codex reviewed,
corrected and independently verified it, including the final real-kill demo.
Bob's preserved implementation is commit `e47d4b5`; subsequent corrections are
separately attributed. [Actual Bob task evidence](bob_sessions/2026-09-26-core-session.md)
includes the task ID and consumption screenshot. No prior OddsEdge implementation,
production data or secrets were copied.

The local core and interactive web app are ready. Public hosting, public repository,
submission video/slides/statements and final submission remain future work.
[Organizer requirements](docs/ORGANIZER-REQUIREMENTS.md) record event-specific limits.
