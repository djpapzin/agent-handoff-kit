# Contributing

Try the local demo with Python 3.9+ on macOS or Linux, then run:

```sh
python3 -m unittest discover -s tests -v
```

Open a GitHub issue with your use case, expected behavior and a minimal synthetic
example. For pull requests, explain the behavior change and relevant verification.
Do not attach credentials, private logs or real customer data.

Useful contributions include clearer integration examples, failure-case tests,
and designs for external adapters with explicit reconciliation. Preserve generation
fencing, atomic local commits and NEEDS_REVIEW for uncertain external outcomes.
Do not claim exactly-once behavior across external APIs.

Bob/Codex development provenance is documented in README.md and bob_sessions/.
Keep attribution accurate when changing or extending the project.
