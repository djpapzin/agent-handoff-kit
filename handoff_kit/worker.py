"""
Agent Handoff Kit — worker operations.

All public functions that mutate state use run_immediate() for the
BEGIN IMMEDIATE / sample-clock / guarded-update / COMMIT pattern.

Clock injection: pass clock=<callable> to any function that accepts it;
this replaces time.time() for that call only.  Never modifies the host clock.

Fault hooks: fault_hook is an optional callable(phase: str) called at named
points; it is only used in tests/demo and has no effect on production paths.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from typing import Callable, List, Optional

from .db import open_and_init, run_immediate
from .models import (
    ACTIVE_STATES,
    STEPS,
    TERMINAL_STATES,
    AlreadyExists,
    GuardFailed,
    HandoffError,
    HistoryRow,
    InconsistentState,
    InputHashMismatch,
    InvalidStep,
    JobRow,
    NotClaimable,
    NotFound,
    ReceiptRow,
    StaleOwner,
    Status,
)
from .steps import (
    EMPTY_CHECKPOINT,
    canonical_result,
    extend_checkpoint,
    hash_input,
    hash_result,
    load_checkpoint,
    next_step_from_checkpoint,
    sha256hex,
    step_create_receipt,
    step_summarize,
    step_validate,
)

# Default lease TTL and heartbeat interval (seconds)
DEFAULT_TTL = 30.0
DEFAULT_HEARTBEAT = 10.0


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_ttl_and_heartbeat(ttl: float, heartbeat: float) -> None:
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"TTL must be finite and positive, got {ttl!r}")
    if not math.isfinite(heartbeat) or heartbeat <= 0:
        raise ValueError(f"heartbeat must be finite and positive, got {heartbeat!r}")
    if heartbeat >= ttl:
        raise ValueError(
            f"heartbeat ({heartbeat}) must be less than TTL ({ttl})"
        )


def _validate_worker_id(worker_id: str) -> None:
    if not worker_id or not isinstance(worker_id, str):
        raise ValueError("worker_id must be a non-empty string")


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _row_to_job(row) -> JobRow:
    return JobRow(
        job_id=row["job_id"],
        input_json=row["input_json"],
        input_hash=row["input_hash"],
        status=Status(row["status"]),
        owner=row["owner"],
        lease_expiry=row["lease_expiry"],
        generation=row["generation"],
        checkpoint_json=row["checkpoint_json"],
        active_step=row["active_step"],
        failure_reason=row["failure_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_receipt(row) -> ReceiptRow:
    return ReceiptRow(
        job_id=row["job_id"],
        step_id=row["step_id"],
        input_hash=row["input_hash"],
        payload_hash=row["payload_hash"],
        result_json=row["result_json"],
        result_hash=row["result_hash"],
        committed_at=row["committed_at"],
    )


def _row_to_history(row) -> HistoryRow:
    return HistoryRow(
        seq=row["seq"],
        job_id=row["job_id"],
        old_status=row["old_status"],
        new_status=row["new_status"],
        owner=row["owner"],
        generation=row["generation"],
        event=row["event"],
        step=row["step"],
        reason=row["reason"],
        recorded_at=row["recorded_at"],
    )


def _read_job(conn, job_id: str) -> Optional[JobRow]:
    cur = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    row = cur.fetchone()
    return _row_to_job(row) if row else None


def _read_receipt(conn, job_id: str) -> Optional[ReceiptRow]:
    cur = conn.execute("SELECT * FROM receipts WHERE job_id = ?", (job_id,))
    row = cur.fetchone()
    return _row_to_receipt(row) if row else None


def _append_history(
    conn,
    job_id: str,
    old_status: Optional[str],
    new_status: str,
    owner: Optional[str],
    generation: int,
    event: str,
    now: float,
    step: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO history(job_id, old_status, new_status, owner, generation,
                             event, step, reason, recorded_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (job_id, old_status, new_status, owner, generation,
         event, step, reason, now),
    )


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def create(
    db_path: str,
    job_id: str,
    input_raw: str,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """
    Create a new job. Non-mutating if job_id already exists (AlreadyExists).
    """
    canonical_json, input_hash = hash_input(input_raw)

    def _body(conn, now: float) -> JobRow:
        existing = _read_job(conn, job_id)
        if existing is not None:
            raise AlreadyExists(f"Job {job_id!r} already exists.")

        conn.execute(
            """
            INSERT INTO jobs(job_id, input_json, input_hash, status, owner,
                             lease_expiry, generation, checkpoint_json,
                             active_step, failure_reason, created_at, updated_at)
            VALUES(?,?,?,'PENDING',NULL,NULL,0,?,NULL,NULL,?,?)
            """,
            (job_id, canonical_json, input_hash, EMPTY_CHECKPOINT, now, now),
        )
        _append_history(
            conn, job_id,
            old_status=None, new_status="PENDING",
            owner=None, generation=0,
            event="create", now=now,
        )
        return _read_job(conn, job_id)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------

def claim(
    db_path: str,
    job_id: str,
    input_raw: str,
    worker_id: str,
    ttl: float = DEFAULT_TTL,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """
    Claim a PENDING job or re-claim an expired active job.

    Compares canonical presented input to stored input/hash under the lock
    BEFORE any mutation. Mismatch → InputHashMismatch (no mutation).
    """
    _validate_worker_id(worker_id)
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"TTL must be finite and positive, got {ttl!r}")

    canonical_presented, presented_hash = hash_input(input_raw)

    def _body(conn, now: float) -> JobRow:
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")

        # --- Input check BEFORE any mutation ---
        if job.input_hash != presented_hash or job.input_json != canonical_presented:
            raise InputHashMismatch(
                f"Presented input hash {presented_hash!r} != stored {job.input_hash!r}"
            )

        old_status = job.status

        if old_status == Status.PENDING:
            pass  # Always claimable
        elif old_status in ACTIVE_STATES:
            # Validate lease expiry field presence (corruption guard)
            if job.lease_expiry is None:
                raise GuardFailed(
                    f"Job {job_id!r} has active status {old_status.value!r} "
                    "but NULL lease_expiry — possible corruption."
                )
            if job.lease_expiry > now:
                raise NotClaimable(
                    f"Job {job_id!r} is held by {job.owner!r} "
                    f"until {job.lease_expiry} (now={now})."
                )
            # Expired — takeover allowed
        elif old_status in TERMINAL_STATES:
            raise NotClaimable(
                f"Job {job_id!r} is in terminal state {old_status.value!r}."
            )
        else:
            raise NotClaimable(f"Job {job_id!r} cannot be claimed from {old_status!r}.")

        new_gen = job.generation + 1
        new_expiry = now + ttl

        conn.execute(
            """
            UPDATE jobs
               SET status='CLAIMED', owner=?, lease_expiry=?, generation=?,
                   active_step=NULL, updated_at=?
             WHERE job_id=? AND status=? AND generation=?
            """,
            (worker_id, new_expiry, new_gen, now,
             job_id, old_status.value, job.generation),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise GuardFailed("claim: rowcount != 1; concurrent mutation detected.")

        _append_history(
            conn, job_id,
            old_status=old_status.value, new_status="CLAIMED",
            owner=worker_id, generation=new_gen,
            event="claim", now=now,
        )
        return _read_job(conn, job_id)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lease guard helper (used inside transactions)
# ---------------------------------------------------------------------------

def _check_holder_guard(
    job: JobRow,
    worker_id: str,
    generation: int,
    now: float,
    allowed_statuses: tuple,
) -> None:
    """
    Raise StaleOwner or GuardFailed if the holder guard fails.
    Must be called under the write lock with a freshly read job row.
    """
    if job.status not in allowed_statuses:
        raise GuardFailed(
            f"Guard failed: status is {job.status.value!r}, "
            f"expected one of {[s.value for s in allowed_statuses]}."
        )
    if job.owner != worker_id:
        raise StaleOwner(
            f"Guard failed: owner is {job.owner!r}, expected {worker_id!r}."
        )
    if job.generation != generation:
        raise StaleOwner(
            f"Guard failed: generation is {job.generation}, expected {generation}."
        )
    if job.lease_expiry is None or job.lease_expiry <= now:
        raise StaleOwner(
            f"Guard failed: lease expired at {job.lease_expiry} (now={now})."
        )


# ---------------------------------------------------------------------------
# renew
# ---------------------------------------------------------------------------

def renew(
    db_path: str,
    job_id: str,
    worker_id: str,
    generation: int,
    ttl: float = DEFAULT_TTL,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """Extend lease; cannot resurrect an expired lease."""
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"TTL must be finite and positive, got {ttl!r}")

    def _body(conn, now: float) -> JobRow:
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")

        _check_holder_guard(
            job, worker_id, generation, now, ACTIVE_STATES
        )

        # Monotonic: new expiry = max(current, now+TTL)
        new_expiry = max(job.lease_expiry, now + ttl)

        conn.execute(
            """
            UPDATE jobs
               SET lease_expiry=?, updated_at=?
             WHERE job_id=? AND owner=? AND generation=?
            """,
            (new_expiry, now, job_id, worker_id, generation),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise GuardFailed("renew: rowcount != 1.")

        _append_history(
            conn, job_id,
            old_status=job.status.value, new_status=job.status.value,
            owner=worker_id, generation=generation,
            event="heartbeat", now=now,
        )
        return _read_job(conn, job_id)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# hold_for_review  (internal helper, also exposed for tests)
# ---------------------------------------------------------------------------

def _hold_for_review_body(
    conn,
    now: float,
    job: JobRow,
    worker_id: str,
    generation: int,
    reason: str,
) -> JobRow:
    """Body for a NEEDS_REVIEW transition under the write lock."""
    _check_holder_guard(job, worker_id, generation, now, ACTIVE_STATES)

    conn.execute(
        """
        UPDATE jobs
           SET status='NEEDS_REVIEW', owner=NULL, lease_expiry=NULL,
               active_step=NULL, failure_reason=?, updated_at=?
         WHERE job_id=? AND owner=? AND generation=? AND status!=?
        """,
        (reason, now, job.job_id, worker_id, generation, "NEEDS_REVIEW"),
    )
    if conn.execute("SELECT changes()").fetchone()[0] != 1:
        raise GuardFailed("hold_for_review: rowcount != 1.")

    _append_history(
        conn, job.job_id,
        old_status=job.status.value, new_status="NEEDS_REVIEW",
        owner=worker_id, generation=generation,
        event="hold_for_review", reason=reason, now=now,
    )
    return _read_job(conn, job.job_id)


# ---------------------------------------------------------------------------
# mark_failed (guarded FAILED transition — known deterministic error)
# ---------------------------------------------------------------------------

def mark_failed(
    db_path: str,
    job_id: str,
    worker_id: str,
    generation: int,
    reason: str,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """
    Guarded FAILED transition for known deterministic task errors.
    Uses the same holder guard as all other writes.
    """
    def _body(conn, now: float) -> JobRow:
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")
        _check_holder_guard(job, worker_id, generation, now, ACTIVE_STATES)

        conn.execute(
            """
            UPDATE jobs
               SET status='FAILED', owner=NULL, lease_expiry=NULL,
                   active_step=NULL, failure_reason=?, updated_at=?
             WHERE job_id=? AND owner=? AND generation=? AND status!=?
            """,
            (reason, now, job_id, worker_id, generation, "FAILED"),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise GuardFailed("mark_failed: rowcount != 1.")

        _append_history(
            conn, job_id,
            old_status=job.status.value, new_status="FAILED",
            owner=worker_id, generation=generation,
            event="mark_failed", reason=reason, now=now,
        )
        return _read_job(conn, job_id)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# hold_for_review (public variant for tests)
# ---------------------------------------------------------------------------

def hold_for_review(
    db_path: str,
    job_id: str,
    worker_id: str,
    generation: int,
    reason: str,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """Public wrapper for the guarded NEEDS_REVIEW transition."""
    def _body(conn, now: float) -> JobRow:
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")
        return _hold_for_review_body(conn, now, job, worker_id, generation, reason)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Validate prefix and receipt consistency
# ---------------------------------------------------------------------------

def _validate_prefix(
    conn,
    job: JobRow,
) -> dict:
    """
    Validate the committed checkpoint prefix and receipt consistency.

    Returns the parsed checkpoint on success.
    Raises InconsistentState on any contradiction, including:
    - extra entries in the results map (more than completed_steps)
    - non-dict result entries
    - receipt step_id != 'create_receipt'
    """
    try:
        cp = load_checkpoint(job.checkpoint_json)
    except ValueError as exc:
        raise InconsistentState(f"Checkpoint parse error: {exc}") from exc

    input_obj = json.loads(job.input_json)
    artifact = input_obj.get("artifact", "")
    job_id = job.job_id
    input_hash = job.input_hash

    completed = cp["completed_steps"]
    results_map = cp["results"]

    # Reject extra entries in results_map beyond completed steps
    extra_keys = set(results_map) - set(completed)
    if extra_keys:
        raise InconsistentState(
            f"Checkpoint results map has extra keys not in completed_steps: {sorted(extra_keys)}."
        )

    # Verify it is a valid ordered prefix of STEPS
    for i, step_name in enumerate(completed):
        if i >= len(STEPS) or STEPS[i] != step_name:
            raise InconsistentState(
                f"Checkpoint step {i} is {step_name!r}; expected {STEPS[i]!r}."
            )

    # Check for duplicate steps
    if len(completed) != len(set(completed)):
        raise InconsistentState("Checkpoint contains duplicate steps.")

    # Recompute and verify each step result
    prior_validate_result = None
    prior_receipt_result = None

    for step_name in completed:
        if step_name not in results_map:
            raise InconsistentState(f"Checkpoint missing result for step {step_name!r}.")

        entry = results_map[step_name]
        if not isinstance(entry, dict):
            raise InconsistentState(
                f"Checkpoint result entry for {step_name!r} is not a dict."
            )
        stored_result = entry.get("result")
        stored_hash = entry.get("result_hash")

        if not isinstance(stored_result, dict):
            raise InconsistentState(
                f"Checkpoint result for {step_name!r} is not a dict."
            )

        if step_name == "validate":
            expected = step_validate(job_id, input_hash)
        elif step_name == "create_receipt":
            expected = step_create_receipt(job_id, input_hash, artifact)
        elif step_name == "summarize":
            if prior_validate_result is None or prior_receipt_result is None:
                raise InconsistentState("summarize in checkpoint but prior steps missing.")
            expected = step_summarize(job_id, prior_validate_result, prior_receipt_result)
        else:
            raise InconsistentState(f"Unknown step in checkpoint: {step_name!r}.")

        # Compare using canonical JSON bytes to reject bool/int substitution
        if canonical_result(stored_result) != canonical_result(expected):
            raise InconsistentState(
                f"Step {step_name!r} result mismatch."
            )
        expected_hash = hash_result(expected)
        if stored_hash != expected_hash:
            raise InconsistentState(
                f"Step {step_name!r} result hash mismatch."
            )

        if step_name == "validate":
            prior_validate_result = expected
        elif step_name == "create_receipt":
            prior_receipt_result = expected

    # Receipt consistency
    receipt = _read_receipt(conn, job_id)
    if "create_receipt" in completed:
        if receipt is None:
            raise InconsistentState("create_receipt in checkpoint but no receipt row found.")
        # Verify receipt step_id
        if receipt.step_id != "create_receipt":
            raise InconsistentState(
                f"Receipt step_id is {receipt.step_id!r}, expected 'create_receipt'."
            )
        if receipt.input_hash != input_hash:
            raise InconsistentState("Receipt input_hash does not match job input_hash.")
        expected_payload = step_create_receipt(job_id, input_hash, artifact)
        expected_payload_hash = sha256hex(
            canonical_result(expected_payload).encode("utf-8")
        )
        if receipt.payload_hash != expected_payload_hash:
            raise InconsistentState("Receipt payload_hash mismatch.")
        expected_result = step_create_receipt(job_id, input_hash, artifact)
        expected_result_hash = hash_result(expected_result)
        # Validate receipt result_json is valid JSON and matches
        try:
            stored_r = json.loads(receipt.result_json)
        except json.JSONDecodeError as exc:
            raise InconsistentState(f"Receipt result_json is invalid JSON: {exc}") from exc
        if canonical_result(stored_r) != canonical_result(expected_result):
            raise InconsistentState("Receipt result mismatch.")
        if receipt.result_hash != expected_result_hash:
            raise InconsistentState("Receipt result_hash mismatch.")
    else:
        if receipt is not None:
            raise InconsistentState(
                "Receipt exists but create_receipt not in checkpoint — receipt ahead of checkpoint."
            )

    return cp


# ---------------------------------------------------------------------------
# _validate_prefix_after_claim — fresh guarded transaction post-claim check
# ---------------------------------------------------------------------------

def _validate_prefix_after_claim(
    db_path: str,
    job_id: str,
    worker_id: str,
    generation: int,
    clock: Optional[Callable[[], float]],
) -> Optional[dict]:
    """
    Open a fresh guarded transaction and validate the durable prefix.

    Returns the parsed checkpoint on success, or None if held for review
    (the hold transition was already committed inside this call).
    """
    result_holder: list = []

    def _body(conn, now: float):
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")
        _check_holder_guard(job, worker_id, generation, now, ACTIVE_STATES)

        try:
            cp = _validate_prefix(conn, job)
        except InconsistentState as exc:
            held = _hold_for_review_body(conn, now, job, worker_id, generation, str(exc))
            result_holder.append(("held", held))
            return

        # Also check: active complete-prefix is inconsistent
        next_s = next_step_from_checkpoint(cp)
        if next_s is None and job.status != Status.DONE:
            reason = (
                "Active job has all three steps in checkpoint without being DONE — "
                "inconsistent state."
            )
            held = _hold_for_review_body(conn, now, job, worker_id, generation, reason)
            result_holder.append(("held", held))
            return

        result_holder.append(("ok", cp))

    conn = open_and_init(db_path)
    try:
        run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()

    kind, value = result_holder[0]
    if kind == "held":
        return None  # Caller should return the held job
    return value


# ---------------------------------------------------------------------------
# begin_step
# ---------------------------------------------------------------------------

def begin_step(
    db_path: str,
    job_id: str,
    worker_id: str,
    generation: int,
    step_name: str,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """
    Transition CLAIMED/CHECKPOINT → RUNNING for the given step.
    Validates the committed prefix first.
    """
    if step_name not in STEPS:
        raise InvalidStep(f"Unknown step: {step_name!r}")

    def _body(conn, now: float) -> JobRow:
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")

        _check_holder_guard(
            job, worker_id, generation, now,
            (Status.CLAIMED, Status.CHECKPOINT),
        )

        # Validate prefix (may raise InconsistentState)
        try:
            cp = _validate_prefix(conn, job)
        except InconsistentState as exc:
            # Hold for review under the same lock
            return _hold_for_review_body(
                conn, now, job, worker_id, generation, str(exc)
            )

        # Determine next step from durable checkpoint
        next_s = next_step_from_checkpoint(cp)
        if next_s is None:
            raise InvalidStep("All steps already completed; use replay.")
        if next_s != step_name:
            raise InvalidStep(
                f"Next step is {next_s!r}, not {step_name!r}."
            )

        conn.execute(
            """
            UPDATE jobs
               SET status='RUNNING', active_step=?, updated_at=?
             WHERE job_id=? AND owner=? AND generation=?
            """,
            (step_name, now, job_id, worker_id, generation),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise GuardFailed("begin_step: rowcount != 1.")

        _append_history(
            conn, job_id,
            old_status=job.status.value, new_status="RUNNING",
            owner=worker_id, generation=generation,
            event="begin_step", step=step_name, now=now,
        )
        return _read_job(conn, job_id)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# commit_step
# ---------------------------------------------------------------------------

def commit_step(
    db_path: str,
    job_id: str,
    worker_id: str,
    generation: int,
    step_name: str,
    fault_hook: Optional[Callable[[str], None]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """
    Commit the completed step result.

    For create_receipt: insert receipt, extend checkpoint, update status.
    For validate/summarize: extend checkpoint, update status.
    For summarize (final): transition to DONE.
    fault_hook(phase) is called at named points for test/demo injection only.
    Named phases: 'before_receipt_insert', 'after_receipt_insert', 'before_commit'.
    """
    if step_name not in STEPS:
        raise InvalidStep(f"Unknown step: {step_name!r}")

    def _body(conn, now: float) -> JobRow:
        job = _read_job(conn, job_id)
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")

        _check_holder_guard(
            job, worker_id, generation, now,
            (Status.RUNNING,),
        )

        if job.active_step != step_name:
            raise InvalidStep(
                f"active_step is {job.active_step!r}, not {step_name!r}."
            )

        # Re-validate entire persisted prefix under this lock
        try:
            cp = _validate_prefix(conn, job)
        except InconsistentState as exc:
            return _hold_for_review_body(
                conn, now, job, worker_id, generation, str(exc)
            )

        # Verify committed prefix matches expected
        next_s = next_step_from_checkpoint(cp)
        if next_s != step_name:
            raise GuardFailed(
                f"Persisted next step is {next_s!r}, not {step_name!r}."
            )

        input_obj = json.loads(job.input_json)
        artifact = input_obj.get("artifact", "")
        prior_validate = cp["results"].get("validate", {}).get("result")
        prior_receipt = cp["results"].get("create_receipt", {}).get("result")

        # Compute deterministic result
        if step_name == "validate":
            result = step_validate(job_id, job.input_hash)
        elif step_name == "create_receipt":
            result = step_create_receipt(job_id, job.input_hash, artifact)
        elif step_name == "summarize":
            if prior_validate is None or prior_receipt is None:
                raise GuardFailed(
                    "summarize: prior step results missing from checkpoint."
                )
            result = step_summarize(job_id, prior_validate, prior_receipt)
        else:
            raise InvalidStep(f"Unknown step: {step_name!r}")

        new_checkpoint_json = extend_checkpoint(cp, step_name, result)
        result_hash_val = hash_result(result)

        # Determine new status
        is_final = (step_name == "summarize")
        new_status = "DONE" if is_final else "CHECKPOINT"

        if step_name == "create_receipt":
            # Require zero receipts
            existing_receipt = _read_receipt(conn, job_id)
            if existing_receipt is not None:
                raise InconsistentState(
                    "Receipt already exists before create_receipt commit."
                )
            payload = step_create_receipt(job_id, job.input_hash, artifact)
            payload_hash = sha256hex(
                canonical_result(payload).encode("utf-8")
            )
            result_json_str = canonical_result(result)

            # Fault hook: 'before_receipt_insert'
            if fault_hook:
                fault_hook("before_receipt_insert")

            conn.execute(
                """
                INSERT INTO receipts(job_id, step_id, input_hash, payload_hash,
                                      result_json, result_hash, committed_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (job_id, "create_receipt", job.input_hash,
                 payload_hash, result_json_str, result_hash_val, now),
            )

            # Fault hook: 'after_receipt_insert' — proves atomic rollback
            if fault_hook:
                fault_hook("after_receipt_insert")

        if is_final:
            # Verify two-step prefix and single receipt before DONE
            new_cp_parsed = json.loads(new_checkpoint_json)
            if new_cp_parsed.get("completed_steps") != list(STEPS):
                raise GuardFailed("Final commit: completed_steps != all three steps.")
            receipt = _read_receipt(conn, job_id)
            if receipt is None:
                raise GuardFailed("Final commit: no receipt found.")

        # Fault hook: 'before_commit' — for demo process-kill injection
        if fault_hook:
            fault_hook("before_commit")

        if is_final:
            conn.execute(
                """
                UPDATE jobs
                   SET status='DONE', checkpoint_json=?, active_step=NULL,
                       owner=NULL, lease_expiry=NULL, updated_at=?
                 WHERE job_id=? AND owner=? AND generation=? AND status='RUNNING'
                """,
                (new_checkpoint_json, now, job_id, worker_id, generation),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                   SET status=?, checkpoint_json=?, active_step=NULL, updated_at=?
                 WHERE job_id=? AND owner=? AND generation=? AND status='RUNNING'
                """,
                (new_status, new_checkpoint_json, now,
                 job_id, worker_id, generation),
            )

        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise GuardFailed("commit_step: rowcount != 1.")

        event = "commit_done" if is_final else "commit_step"
        _append_history(
            conn, job_id,
            old_status="RUNNING", new_status=new_status,
            owner=worker_id if not is_final else None,
            generation=generation,
            event=event, step=step_name, now=now,
        )
        return _read_job(conn, job_id)

    conn = open_and_init(db_path)
    try:
        return run_immediate(conn, _body, clock=clock)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# run_job — full execution loop for one worker
# ---------------------------------------------------------------------------

def run_job(
    db_path: str,
    job_id: str,
    input_raw: str,
    worker_id: str,
    ttl: float = DEFAULT_TTL,
    heartbeat: float = DEFAULT_HEARTBEAT,
    fault_hook: Optional[Callable[[str], None]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> JobRow:
    """
    Claim (or re-claim) and execute all remaining steps.

    If the job is already DONE, performs a read-only replay and raises
    NotClaimable (terminal — no writes).

    Returns the final JobRow (DONE or terminal hold).

    heartbeat: interval in seconds for lease renewal. Must satisfy
               0 < heartbeat < ttl.
    """
    _validate_worker_id(worker_id)
    _validate_ttl_and_heartbeat(ttl, heartbeat)

    # If already DONE, route to read-only replay.
    # Check status without claiming first.
    conn_check = open_and_init(db_path)
    try:
        conn_check.execute("BEGIN")
        try:
            row = _read_job(conn_check, job_id)
        finally:
            conn_check.execute("ROLLBACK")
    finally:
        conn_check.close()

    if row is not None and row.status == Status.DONE:
        # Terminal — raise NotClaimable; callers may use replay() directly.
        raise NotClaimable(
            f"Job {job_id!r} is already DONE. Use replay() to read results."
        )

    job = claim(db_path, job_id, input_raw, worker_id, ttl=ttl, clock=clock)
    gen = job.generation

    # Validate full prefix in a fresh guarded transaction immediately after claim
    cp = _validate_prefix_after_claim(db_path, job_id, worker_id, gen, clock)
    if cp is None:
        # Held for review during post-claim prefix check — read and return current state
        return _read_terminal_job(db_path, job_id)

    next_s = next_step_from_checkpoint(cp)
    last_renewal = time.time() if clock is None else clock()

    while next_s is not None:
        # begin_step
        job = begin_step(db_path, job_id, worker_id, gen, next_s, clock=clock)
        if job.status == Status.NEEDS_REVIEW:
            return job

        # commit_step (with optional fault_hook)
        job = commit_step(
            db_path, job_id, worker_id, gen, next_s,
            fault_hook=fault_hook, clock=clock,
        )
        if job.status in (Status.NEEDS_REVIEW, Status.FAILED):
            return job
        if job.status == Status.DONE:
            return job

        # Heartbeat check — renew if past heartbeat interval
        now = time.time() if clock is None else clock()
        if now - last_renewal >= heartbeat:
            try:
                job = renew(db_path, job_id, worker_id, gen, ttl=ttl, clock=clock)
                last_renewal = now
            except (StaleOwner, GuardFailed):
                # Lease gone — stop
                return job

        # Refresh checkpoint for next iteration
        try:
            cp = load_checkpoint(job.checkpoint_json)
        except ValueError:
            break
        next_s = next_step_from_checkpoint(cp)

    return job


def _read_terminal_job(db_path: str, job_id: str) -> JobRow:
    """Read current job row without any write (for post-hold return)."""
    conn = open_and_init(db_path)
    try:
        conn.execute("BEGIN")
        try:
            job = _read_job(conn, job_id)
        finally:
            conn.execute("ROLLBACK")
        if job is None:
            raise NotFound(f"Job {job_id!r} not found.")
        return job
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# replay — read-only DONE replay
# ---------------------------------------------------------------------------

def replay(
    db_path: str,
    job_id: str,
    input_raw: str,
) -> dict:
    """
    Read-only replay of a DONE job. Verifies input, prefix, and receipt.
    Returns stored summary result dict.
    No claim, heartbeat, or history write.
    """
    canonical_presented, presented_hash = hash_input(input_raw)

    conn = open_and_init(db_path)
    try:
        conn.execute("BEGIN")  # consistent read
        try:
            job = _read_job(conn, job_id)
            if job is None:
                raise NotFound(f"Job {job_id!r} not found.")

            if job.status == Status.NEEDS_REVIEW:
                raise InconsistentState(
                    f"Job {job_id!r} is NEEDS_REVIEW: {job.failure_reason!r}"
                )
            if job.status == Status.FAILED:
                raise InconsistentState(
                    f"Job {job_id!r} is FAILED: {job.failure_reason!r}"
                )
            if job.status != Status.DONE:
                raise InvalidStep(
                    f"Job {job_id!r} is not DONE (status={job.status.value!r})."
                )

            # Input check
            if job.input_hash != presented_hash or job.input_json != canonical_presented:
                raise InputHashMismatch(
                    f"Presented input hash {presented_hash!r} != stored {job.input_hash!r}"
                )

            # Verify complete prefix and single receipt (read-only)
            try:
                cp = _validate_prefix(conn, job)
            except InconsistentState as exc:
                raise InconsistentState(
                    f"DONE job has inconsistent state: {exc}"
                ) from exc

            if cp.get("completed_steps") != list(STEPS):
                raise InconsistentState(
                    "DONE job does not have all three steps in checkpoint."
                )

            summary = cp["results"]["summarize"]["result"]
            conn.execute("ROLLBACK")
            return summary
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def get_status(db_path: str, job_id: str) -> dict:
    """Read-only status report."""
    conn = open_and_init(db_path)
    try:
        conn.execute("BEGIN")
        try:
            job = _read_job(conn, job_id)
            if job is None:
                raise NotFound(f"Job {job_id!r} not found.")

            try:
                cp = load_checkpoint(job.checkpoint_json)
                last_step = (
                    cp["completed_steps"][-1] if cp["completed_steps"] else None
                )
                next_s = next_step_from_checkpoint(cp)
            except ValueError:
                last_step = None
                next_s = None

            receipt = _read_receipt(conn, job_id)
            conn.execute("ROLLBACK")

            if job.status == Status.DONE:
                next_action = "replay"
            elif job.status in (Status.FAILED, Status.NEEDS_REVIEW):
                next_action = "inspect only"
            elif job.status in ACTIVE_STATES:
                next_action = "wait or recover after expiry"
            else:
                next_action = "run"

            return {
                "job_id": job.job_id,
                "status": job.status.value,
                "owner": job.owner,
                "generation": job.generation,
                "lease_expiry": job.lease_expiry,
                "last_completed_step": last_step,
                "next_step": next_s,
                "active_step": job.active_step,
                "failure_reason": job.failure_reason,
                "has_receipt": receipt is not None,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "next_action": next_action,
            }
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def get_history(db_path: str, job_id: str) -> List[HistoryRow]:
    """Read-only history for a job, ordered by seq."""
    conn = open_and_init(db_path)
    try:
        conn.execute("BEGIN")
        try:
            job = _read_job(conn, job_id)
            if job is None:
                raise NotFound(f"Job {job_id!r} not found.")

            cur = conn.execute(
                "SELECT * FROM history WHERE job_id=? ORDER BY seq",
                (job_id,),
            )
            rows = [_row_to_history(r) for r in cur.fetchall()]
            conn.execute("ROLLBACK")
            return rows
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
