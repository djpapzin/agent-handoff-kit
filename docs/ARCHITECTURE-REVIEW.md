# Codex review disposition — revision 4

**The six recorded design findings are resolved in revision 4.** It is a
coherent contract for the next implementation task, not tested runtime code
or a claim of user approval to implement/deploy/submit.

Bob authored revisions 1–3; revision 3 is preserved unchanged at a6f8449.
Codex consolidated revision 4 because revision 3 still included reset paths,
receipt-to-checkpoint repair, a fail-after-input-mismatch branch, missing
rowcount checks and contradictory transition claims. Those final corrections
are explicitly Codex work, not retroactively attributed to Bob.

| Original finding | Final resolution |
|---|---|
| Input mismatch mutates claim | Section 4 compares input inside the write transaction before any mutation; mismatch returns without fail/history effects. DONE replay also checks input read-only. |
| Unstable/truncated operation identity | Sections 1–2 use immutable job/input and full-hash tuple, receipt primary key per job; a new execution needs a new ID. No reset/delete path. |
| Missing transitions | Section 5 enumerates all writes, including holds/errors from CLAIMED/RUNNING/CHECKPOINT, final atomic DONE and audited renewal. Guard failure is not a transition. |
| Verdict treated as evidence | Sections 1/6 keep NEEDS_REVIEW terminal; remove reconcile and synthetic bypass. Future provider integration is outside this MVP. |
| Unsupported clock interface | Section 4 uses Python time.time after BEGIN IMMEDIATE, strict expiry, explicit jump limitations and generation fencing; no SQLite subsec dependency. |
| Guards/history/completeness gaps | Sections 3–7 require rollback on every failed guard, actual previous status in history, full deterministic result/receipt validation, atomic checkpoint/effect/history and monotonic prefix. Exactly one receipt is created; DONE retains checkpoint. |

Manual design walkthrough performed against creation, concurrent claim order,
crash before/after receipt commit, recovery, stale-owner writes, input mismatch,
inconsistent evidence, DONE replay and terminal holds. Each mutation has an
explicit source status and guard; each status mutation has an atomic audit.
This is reasoning over the written design, not execution or fault-injection
results. The acceptance matrix in section 9 remains entirely unrun.

Implementation must still prove correctness through real multi-process tests,
crash/restart tests, guard/no-mutation snapshots and a fresh-run demo. Online
submission packaging and account/team verification are tracked separately in
ORGANIZER-REQUIREMENTS.md.
