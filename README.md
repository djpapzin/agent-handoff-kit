# Agent Handoff Kit

Resume an interrupted synthetic developer task from its last durable checkpoint.
The local demo kills worker A after it commits a receipt, waits for lease expiry,
and starts worker B to finish the job. Replay leaves the receipt and audit history
unchanged. Python 3.9+ standard library and SQLite; no installation or API keys.

## Open the interactive demo

[Live Oracle-hosted demo](https://handoff.djpapzin.com/)

The demo runs on an Oracle VM behind a stable Cloudflare hostname. It does not
depend on a laptop being awake. This is a low-traffic demonstration, not a hosted
service for processing your own tasks.

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

Requires Python 3.9+ on macOS or Linux (the demo uses POSIX process signals).
No pip packages or API keys are needed.

```sh
git clone https://github.com/djpapzin/agent-handoff-kit.git
cd agent-handoff-kit
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

113 tests passed on Python 3.9.6 (103 core, 6 HTTP and 4 WSGI tests). Includes concurrent process claims, abrupt
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
includes the task ID and consumption screenshot. No prior implementation, production data or secrets were copied.

The local core and interactive web app are ready. The source is public at
https://github.com/djpapzin/agent-handoff-kit under the MIT license. The app is deployed on the existing Oracle VM and verified over public HTTPS.
No paid hosting resources were added.

## Release and media

[Download v0.1.0: demo video, PDF slides and editable deck](https://github.com/djpapzin/agent-handoff-kit/releases/tag/v0.1.0).

This is an experimental local recovery prototype. It is useful for learning,
inspecting recovery behavior and adapting the core to local workflows. External
service adapters and reconciliation are not implemented.

Originally built for the September 2026 IBM Bob 2.0 hackathon. The entry was not
submitted before the deadline; this is an independent open-source release, not
an accepted hackathon submission. The recorded video retains that original context.

The source code is MIT licensed. Video narration was generated with
[ElevenLabs](https://elevenlabs.io/); media licensing is described in the release.
See [contribution guidance](CONTRIBUTING.md) and [security boundaries](SECURITY.md).
[Organizer requirements](docs/ORGANIZER-REQUIREMENTS.md) record event-specific limits.
