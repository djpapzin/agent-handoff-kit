# Architecture task brief

Design only: one job, two local workers, one recovery using Python and SQLite.

1. Durable job contract: stable job and step IDs, canonical input hash, status, owner, lease expiry, fencing generation, checkpoint, evidence references.
2. Atomic claim, renewal, transfer, and guarded checkpoint updates; reject stale-generation writes.
3. Append-only transition history, unique local action keys, crash-safe checkpoints.
4. Two local processes simulate provider failure without paid providers.
5. Synthetic demo: validate input, create one uniquely keyed local receipt, summarize completion. Kill A after a checkpoint, resume B, then replay.
6. Status receipt: current worker, last completed step, failure reason, evidence, next permitted action.
7. Unknown external action outcomes become NEEDS_REVIEW; no retry without reconciliation.

Acceptance plan must cover process death/restart, single committed receipt on replay, concurrent claimants, expired lease transfer, stale writes, persisted history, input mismatch, and unknown outcomes. Include failure between action commit and checkpoint and explain transaction boundaries. Telegram status/resume is deferred; if later included, enforce numeric owner authorization.

Bounded local duplicate suppression only. No universal exactly-once guarantee. No runtime dependency on Bob. No application implementation in this task.

Deliver a concise architecture proposal with schema, allowed transitions, transaction boundaries, clock/lease assumptions, recovery rules, test cases and open questions. Stop for review.
