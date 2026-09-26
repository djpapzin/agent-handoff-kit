# Agent Handoff Kit

Hackathon workspace prepared on 26 September 2026. This repository contains setup documents, not a working prototype.

A replacement worker should resume an interrupted developer task from a durable checkpoint, avoid repeating committed local demo actions, and hold uncertain external outcomes for review.

Planned stack: Python standard library, SQLite, thin CLI. The available Mac Python is 3.9.6; keep the initial proposal compatible or document a justified upgrade before implementation.

## First Bob task

Read AGENTS.md and docs/BRIEF.md. Inspect this fresh baseline, then propose the smallest schema, transitions, ownership/recovery protocol, and acceptance-test plan in docs/ARCHITECTURE.md. Do not implement. Stop for human review. Save actual Bob session consumption evidence immediately after completion.

## Preparation provenance

Codex created this isolated repository and its setup documents. No OddsEdge source code, production data, secrets, or previous implementation was copied. The scoped product idea derives from the owner's existing planning brief. Bob contributions will be recorded separately after actual work.

See docs/SETUP.md for access verification and remaining event requirements, docs/BASELINE.md for the setup commit, and bob_sessions/README.md for evidence requirements.

## Current progress

Bob access and the isolated workspace are ready. Bob completed the first architecture proposal and one revision; [actual session evidence](bob_sessions/2026-09-26-architecture-session.md) is saved. The [design review](docs/ARCHITECTURE-REVIEW.md) is resolved in revision 4; runtime validation remains pending. This repository still contains no working prototype.

### Review follow-up

The six architecture findings are now resolved in revision 4, consolidated by
Codex from Bob's preserved revisions. See docs/ARCHITECTURE-REVIEW.md.
Published submission requirements are recorded in docs/ORGANIZER-REQUIREMENTS.md;
lablab sign-in, the Agent Handoff Kit solo team, and LABLAB.AI Discord membership with readable IBM Bob 2.0 announcements are verified. Implementation remains unstarted.
