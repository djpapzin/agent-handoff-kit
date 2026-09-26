# Agent Handoff Kit

This is an isolated hackathon workspace. Use synthetic inputs and a separate local SQLite database only. Do not access production repositories, databases, browser sessions, credentials, Telegram tokens, or personal data.

Current stage: setup and architecture review only. Do not implement code, install dependencies, deploy, publish, or submit. Write the first architecture proposal to docs/ARCHITECTURE.md and then stop for review.

Scope: Python standard library, SQLite, thin CLI; one job, two local worker processes, one recovery. Telegram is optional and deferred. No dashboard, paid providers, live wagers, cloud orchestration, or billing.

Preserve atomic leases and fencing, guarded checkpoints, append-only history, unique action keys, input-hash mismatch rejection, and NEEDS_REVIEW for unknown external outcomes. Claim bounded duplicate suppression for the transactional local demo, never universal exactly-once external execution.

Distinguish Codex setup/planning from real Bob contributions. Never invent Bob sessions, screenshots, test results, or prior implementation. Retain actual task summaries and consumption screenshots under bob_sessions/ with prompt, decision, file, and commit references.

Reuse decision: no existing implementation or production data is copied into this workspace. Organizer-specific reuse rules remain unverified; obtain clarification before any future reuse. The baseline consists only of new Codex-authored setup documents.
