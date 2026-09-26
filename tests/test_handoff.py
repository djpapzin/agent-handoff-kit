"""
Tests for Agent Handoff Kit — section 9 acceptance matrix.

Python 3.9 standard library only. Run with:
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from handoff_kit.db import open_and_init, run_immediate
from handoff_kit.models import (
    STEPS,
    AlreadyExists,
    GuardFailed,
    InconsistentState,
    InputHashMismatch,
    InvalidStep,
    NotClaimable,
    NotFound,
    SchemaError,
    StaleOwner,
    Status,
)
from handoff_kit.steps import (
    EMPTY_CHECKPOINT,
    canonical_result,
    extend_checkpoint,
    hash_input,
    hash_result,
    load_checkpoint,
    sha256hex,
    step_create_receipt,
    step_summarize,
    step_validate,
)
from handoff_kit.worker import (
    begin_step,
    claim,
    commit_step,
    create,
    get_history,
    get_status,
    hold_for_review,
    mark_failed,
    replay,
    renew,
    run_job,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INPUT_RAW = '{"schema_version":1,"task":"build-sample","artifact":"sample.txt"}'
INPUT_RAW_ALT = '{"schema_version":1,"task":"other-task","artifact":"other.txt"}'


def _tmpdb() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _cleanup(db: str) -> None:
    for f in [db, db + "-shm", db + "-wal"]:
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass


class _DBTest(unittest.TestCase):
    """Base class: creates a fresh temp DB per test."""
    def setUp(self):
        self.db = _tmpdb()

    def tearDown(self):
        _cleanup(self.db)


def _monotonic_clock(start: float = 1_000_000.0):
    state = [start]
    def clock():
        v = state[0]
        state[0] += 0.001
        return v
    return clock


class FakeClockFixed:
    def __init__(self, t: float):
        self.t = t
    def __call__(self) -> float:
        return self.t


def _expire_lease(db: str, job_id: str) -> None:
    """Force a job's lease to the past so any worker can reclaim it."""
    conn = open_and_init(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE jobs SET lease_expiry=0.001, updated_at=0.001 "
            "WHERE job_id=?", (job_id,)
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


# ============================================================
# 1. Happy path
# ============================================================

class TestHappyPath(_DBTest):
    def test_full_run_done(self):
        create(self.db, "j1", INPUT_RAW)
        job = run_job(self.db, "j1", INPUT_RAW, "wA")
        self.assertEqual(job.status, Status.DONE)

    def test_done_checkpoint_all_steps(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        info = get_status(self.db, "j1")
        self.assertEqual(info["last_completed_step"], "summarize")
        self.assertTrue(info["has_receipt"])

    def test_done_replay(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        s = replay(self.db, "j1", INPUT_RAW)
        self.assertTrue(s["validated"])
        self.assertEqual(s["job_id"], "j1")

    def test_receipt_count_one(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        conn = open_and_init(self.db)
        try:
            c = conn.execute("SELECT COUNT(*) FROM receipts WHERE job_id='j1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 1)

    def test_history_has_create_claim_commit_done(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        events = [r.event for r in get_history(self.db, "j1")]
        self.assertIn("create", events)
        self.assertIn("claim", events)
        self.assertIn("commit_done", events)
        self.assertEqual(events.count("commit_done"), 1)

    def test_done_owner_lease_null(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        info = get_status(self.db, "j1")
        self.assertIsNone(info["owner"])
        self.assertIsNone(info["lease_expiry"])


# ============================================================
# 2. Input validation
# ============================================================

class TestInputValidation(unittest.TestCase):
    def test_duplicate_keys(self):
        with self.assertRaises(SchemaError):
            hash_input('{"schema_version":1,"schema_version":1,"task":"t","artifact":"a"}')

    def test_unknown_field(self):
        with self.assertRaises(SchemaError):
            hash_input('{"schema_version":1,"task":"t","artifact":"a","extra":"x"}')

    def test_bool_schema_version(self):
        with self.assertRaises(SchemaError):
            hash_input('{"schema_version":true,"task":"t","artifact":"a"}')

    def test_wrong_version(self):
        with self.assertRaises(SchemaError):
            hash_input('{"schema_version":2,"task":"t","artifact":"a"}')

    def test_trailing_newline_rejected(self):
        """fullmatch rejects trailing whitespace in task/artifact."""
        with self.assertRaises(SchemaError):
            hash_input('{"schema_version":1,"task":"t\n","artifact":"a"}')

    def test_canonical_hash_stable_across_key_order(self):
        _, h1 = hash_input(INPUT_RAW)
        _, h2 = hash_input('{"artifact":"sample.txt","schema_version":1,"task":"build-sample"}')
        self.assertEqual(h1, h2)

    def test_hash_is_64_lowercase_hex(self):
        _, h = hash_input(INPUT_RAW)
        self.assertEqual(len(h), 64)
        self.assertEqual(h, h.lower())
        self.assertTrue(all(c in "0123456789abcdef" for c in h))


# ============================================================
# 3. Duplicate job ID (non-mutating)
# ============================================================

class TestDuplicateJobId(_DBTest):
    def test_raises_already_exists(self):
        create(self.db, "j1", INPUT_RAW)
        with self.assertRaises(AlreadyExists):
            create(self.db, "j1", INPUT_RAW)

    def test_status_unchanged(self):
        create(self.db, "j1", INPUT_RAW)
        try:
            create(self.db, "j1", INPUT_RAW)
        except AlreadyExists:
            pass
        self.assertEqual(get_status(self.db, "j1")["status"], "PENDING")

    def test_history_unchanged(self):
        create(self.db, "j1", INPUT_RAW)
        try:
            create(self.db, "j1", INPUT_RAW)
        except AlreadyExists:
            pass
        self.assertEqual(len(get_history(self.db, "j1")), 1)

    def test_new_job_same_input_is_independent(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        create(self.db, "j2", INPUT_RAW)
        self.assertEqual(get_status(self.db, "j2")["status"], "PENDING")
        conn = open_and_init(self.db)
        try:
            c = conn.execute("SELECT COUNT(*) FROM receipts WHERE job_id='j1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 1)


# ============================================================
# 4. Input hash mismatch (no mutation)
# ============================================================

class TestInputHashMismatch(_DBTest):
    def test_mismatch_on_pending_raises(self):
        create(self.db, "j1", INPUT_RAW)
        with self.assertRaises(InputHashMismatch):
            claim(self.db, "j1", INPUT_RAW_ALT, "wA")

    def test_mismatch_no_status_change(self):
        create(self.db, "j1", INPUT_RAW)
        try:
            claim(self.db, "j1", INPUT_RAW_ALT, "wA")
        except InputHashMismatch:
            pass
        self.assertEqual(get_status(self.db, "j1")["status"], "PENDING")

    def test_mismatch_no_history_added(self):
        create(self.db, "j1", INPUT_RAW)
        try:
            claim(self.db, "j1", INPUT_RAW_ALT, "wA")
        except InputHashMismatch:
            pass
        self.assertEqual(len(get_history(self.db, "j1")), 1)  # only "create"

    def test_mismatch_not_failed(self):
        create(self.db, "j1", INPUT_RAW)
        try:
            claim(self.db, "j1", INPUT_RAW_ALT, "wA")
        except InputHashMismatch:
            pass
        self.assertNotEqual(get_status(self.db, "j1")["status"], "FAILED")

    def test_mismatch_on_done_replay(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        with self.assertRaises(InputHashMismatch):
            replay(self.db, "j1", INPUT_RAW_ALT)

    def test_mismatch_on_done_replay_no_write(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        before = len(get_history(self.db, "j1"))
        try:
            replay(self.db, "j1", INPUT_RAW_ALT)
        except InputHashMismatch:
            pass
        self.assertEqual(len(get_history(self.db, "j1")), before)


# ============================================================
# 5. Stale owner (no mutation)
# ============================================================

class TestStaleOwner(_DBTest):
    def _claim_and_expire(self):
        c = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c)
        job_a = claim(self.db, "j1", INPUT_RAW, "wA", ttl=0.001, clock=c)
        gen_a = job_a.generation
        for _ in range(20):
            c()
        job_b = claim(self.db, "j1", INPUT_RAW, "wB", ttl=30.0, clock=c)
        return gen_a, job_b.generation

    def test_stale_begin_step_raises(self):
        gen_a, gen_b = self._claim_and_expire()
        with self.assertRaises(StaleOwner):
            begin_step(self.db, "j1", "wA", gen_a, "validate")

    def test_stale_begin_no_history(self):
        gen_a, gen_b = self._claim_and_expire()
        before = len(get_history(self.db, "j1"))
        try:
            begin_step(self.db, "j1", "wA", gen_a, "validate")
        except StaleOwner:
            pass
        self.assertEqual(len(get_history(self.db, "j1")), before)

    def test_stale_renew_raises(self):
        gen_a, gen_b = self._claim_and_expire()
        with self.assertRaises(StaleOwner):
            renew(self.db, "j1", "wA", gen_a)

    def test_stale_hold_raises(self):
        gen_a, gen_b = self._claim_and_expire()
        with self.assertRaises(StaleOwner):
            hold_for_review(self.db, "j1", "wA", gen_a, "test")

    def test_stale_fail_raises(self):
        gen_a, gen_b = self._claim_and_expire()
        with self.assertRaises(StaleOwner):
            mark_failed(self.db, "j1", "wA", gen_a, "test")

    def test_stale_commit_no_mutation(self):
        """Stale worker's commit attempt is rejected; no table change."""
        c = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c)
        job_a = claim(self.db, "j1", INPUT_RAW, "wA", ttl=60.0, clock=c)
        gen_a = job_a.generation
        begin_step(self.db, "j1", "wA", gen_a, "validate", clock=c)

        # Expire by advancing the clock far past TTL
        far_future = FakeClockFixed(2_000_000.0)
        before = len(get_history(self.db, "j1"))
        with self.assertRaises(StaleOwner):
            commit_step(self.db, "j1", "wA", gen_a, "validate", clock=far_future)
        self.assertEqual(len(get_history(self.db, "j1")), before)


# ============================================================
# 6. Terminal states — read-only
# ============================================================

class TestTerminalStates(_DBTest):
    def test_done_cannot_be_claimed(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        with self.assertRaises(NotClaimable):
            claim(self.db, "j1", INPUT_RAW, "wB")

    def test_done_run_job_raises_not_claimable(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        with self.assertRaises(NotClaimable):
            run_job(self.db, "j1", INPUT_RAW, "wB")

    def test_repeated_replay_read_only(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        before = len(get_history(self.db, "j1"))
        for _ in range(3):
            s = replay(self.db, "j1", INPUT_RAW)
            self.assertIn("job_id", s)
        self.assertEqual(len(get_history(self.db, "j1")), before)

    def test_done_next_action_replay(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")
        self.assertEqual(get_status(self.db, "j1")["next_action"], "replay")

    def test_needs_review_inspect_only(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        hold_for_review(self.db, "j1", "wA", job.generation, "forced")
        self.assertEqual(get_status(self.db, "j1")["next_action"], "inspect only")


# ============================================================
# 7. Out-of-order steps — non-mutating
# ============================================================

class TestOutOfOrder(_DBTest):
    def test_wrong_order_raises(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA")
        with self.assertRaises(InvalidStep):
            begin_step(self.db, "j1", "wA", job.generation, "summarize")

    def test_wrong_order_no_mutation(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA")
        before = len(get_history(self.db, "j1"))
        try:
            begin_step(self.db, "j1", "wA", job.generation, "summarize")
        except InvalidStep:
            pass
        self.assertEqual(len(get_history(self.db, "j1")), before)

    def test_commit_wrong_step_raises(self):
        """commit_step when active_step differs raises InvalidStep (no mutation)."""
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA")
        gen = job.generation
        begin_step(self.db, "j1", "wA", gen, "validate")
        before = len(get_history(self.db, "j1"))
        with self.assertRaises(InvalidStep):
            commit_step(self.db, "j1", "wA", gen, "create_receipt")
        self.assertEqual(len(get_history(self.db, "j1")), before)


# ============================================================
# 8. Lease renewal
# ============================================================

class TestLeaseRenewal(_DBTest):
    def test_renewal_extends(self):
        c = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0, clock=c)
        old = job.lease_expiry
        job2 = renew(self.db, "j1", "wA", job.generation, ttl=30.0, clock=c)
        self.assertGreaterEqual(job2.lease_expiry, old)

    def test_renewal_monotonic_backward_clock(self):
        """Backward-clock: expiry must not shrink."""
        fwd = _monotonic_clock(2_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=fwd)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0, clock=fwd)
        orig = job.lease_expiry
        bwd = FakeClockFixed(1_000_000.0)
        job2 = renew(self.db, "j1", "wA", job.generation, ttl=30.0, clock=bwd)
        self.assertGreaterEqual(job2.lease_expiry, orig)

    def test_renewal_after_expiry_fails(self):
        c = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=0.001, clock=c)
        for _ in range(20):
            c()
        with self.assertRaises(StaleOwner):
            renew(self.db, "j1", "wA", job.generation, ttl=30.0, clock=c)

    def test_renewal_adds_heartbeat_event(self):
        c = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0, clock=c)
        renew(self.db, "j1", "wA", job.generation, ttl=30.0, clock=c)
        events = [r.event for r in get_history(self.db, "j1")]
        self.assertIn("heartbeat", events)


# ============================================================
# 9. Immutable constraints — triggers raise IntegrityError
# ============================================================

class TestImmutableConstraints(_DBTest):
    def _run_full(self):
        create(self.db, "j1", INPUT_RAW)
        run_job(self.db, "j1", INPUT_RAW, "wA")

    def test_cannot_delete_history(self):
        self._run_full()
        conn = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM history WHERE job_id='j1'")
        finally:
            conn.close()

    def test_cannot_update_history(self):
        self._run_full()
        conn = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE history SET event='x' WHERE job_id='j1'")
        finally:
            conn.close()

    def test_cannot_delete_receipt(self):
        self._run_full()
        conn = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM receipts WHERE job_id='j1'")
        finally:
            conn.close()

    def test_cannot_update_receipt(self):
        self._run_full()
        conn = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE receipts SET result_json='{}' WHERE job_id='j1'")
        finally:
            conn.close()

    def test_cannot_change_job_input(self):
        create(self.db, "j1", INPUT_RAW)
        conn = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE jobs SET input_json='{}' WHERE job_id='j1'")
        finally:
            conn.close()

    def test_cannot_delete_job(self):
        create(self.db, "j1", INPUT_RAW)
        conn = open_and_init(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM jobs WHERE job_id='j1'")
        finally:
            conn.close()


# ============================================================
# 10. Concurrent claims — real processes
# ============================================================

def _proc_claim(db: str, job_id: str, worker_id: str, q: multiprocessing.Queue):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from handoff_kit.worker import claim
    from handoff_kit.models import NotClaimable, InputHashMismatch
    try:
        job = claim(db, job_id, INPUT_RAW, worker_id, ttl=30.0)
        q.put(("ok", worker_id, job.generation))
    except NotClaimable as e:
        q.put(("nc", worker_id, str(e)))
    except Exception as e:
        q.put(("err", worker_id, str(e)))


class TestConcurrentClaims(_DBTest):
    def _run_two(self, n=2):
        create(self.db, "j1", INPUT_RAW)
        q = multiprocessing.Queue()
        procs = [
            multiprocessing.Process(target=_proc_claim,
                                    args=(self.db, "j1", f"w{i}", q))
            for i in range(n)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=20)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
        results = []
        while not q.empty():
            results.append(q.get_nowait())
        return results

    def test_exactly_one_winner(self):
        results = self._run_two(2)
        self.assertEqual(len(results), 2, f"results={results}")
        ok = [r for r in results if r[0] == "ok"]
        nc = [r for r in results if r[0] == "nc"]
        self.assertEqual(len(ok), 1, f"expected 1 winner, got: {results}")
        self.assertEqual(len(nc), 1)

    def test_exactly_one_claim_event(self):
        self._run_two(3)
        rows = get_history(self.db, "j1")
        claims = [r for r in rows if r.event == "claim"]
        self.assertEqual(len(claims), 1)

    def test_winner_generation_is_one(self):
        results = self._run_two(2)
        ok = [r for r in results if r[0] == "ok"]
        self.assertEqual(ok[0][2], 1)


# ============================================================
# 11. Abrupt process death — real os._exit, targeted by step/phase
# ============================================================

# Each subprocess: claim → begin_step → then calls os._exit(1) at the named
# fault hook phase, BEFORE the commit.  After it exits the parent:
#   - checks the DB is unchanged
#   - expires the lease
#   - runs recovery with worker-B
#   - asserts DONE + exactly one receipt

def _proc_death_at_phase(
        db: str, job_id: str, step: str, phase: str, q: multiprocessing.Queue):
    """
    Child: runs steps up to `step`, then os._exit at `phase`.
    Specifically:
      - Completes all steps BEFORE `step` (so prefix is valid on recovery).
      - begin_step for `step`.
      - commit_step for `step` with fault_hook that calls os._exit at `phase`.
    """
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from handoff_kit.worker import claim, begin_step, commit_step
    from handoff_kit.models import STEPS

    def _hook(p: str):
        if p == phase:
            os._exit(1)

    try:
        job = claim(db, job_id, INPUT_RAW, "wA", ttl=60.0)
        gen = job.generation

        from handoff_kit.models import STEPS as _STEPS
        # Run all steps before the target step
        idx = _STEPS.index(step)
        for s in _STEPS[:idx]:
            job = begin_step(db, job_id, "wA", gen, s)
            job = commit_step(db, job_id, "wA", gen, s)

        # Now begin the target step and commit with the fault hook
        job = begin_step(db, job_id, "wA", gen, step)
        commit_step(db, job_id, "wA", gen, step, fault_hook=_hook)
        # If we get here the hook didn't fire this phase; signal normal exit
        q.put(("no_fault", step, phase))
    except SystemExit:
        pass  # fallback if someone uses sys.exit
    except Exception as e:
        q.put(("error", str(e)))


class TestProcessDeath(_DBTest):
    def _death_and_recover(self, step: str, phase: str):
        """
        Kill worker-A at `phase` during `step` commit.
        Expire lease. Worker-B recovers to DONE.
        Returns final job and history rows.
        """
        create(self.db, "j1", INPUT_RAW)

        q = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=_proc_death_at_phase,
            args=(self.db, "j1", step, phase, q),
        )
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)

        # Expire worker-A's lease
        _expire_lease(self.db, "j1")

        # Worker-B recovers
        job_b = run_job(self.db, "j1", INPUT_RAW, "wB")
        rows = get_history(self.db, "j1")
        return job_b, rows

    def _assert_done_one_receipt(self, job, label: str):
        self.assertEqual(job.status, Status.DONE, f"{label}: expected DONE")
        conn = open_and_init(self.db)
        try:
            c = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE job_id='j1'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 1, f"{label}: expected exactly 1 receipt")

    # --- validate step deaths ---

    def test_death_before_commit_validate(self):
        job, rows = self._death_and_recover("validate", "before_commit")
        self._assert_done_one_receipt(job, "death_before_commit_validate")
        # wB must have started from validate (no validate in checkpoint when A died)
        claims = [r for r in rows if r.event == "claim"]
        self.assertEqual(len(claims), 2, "Two claims expected (A and B)")

    def test_death_before_commit_receipt(self):
        """Death before_commit during create_receipt: no receipt committed."""
        job, rows = self._death_and_recover("create_receipt", "before_commit")
        self._assert_done_one_receipt(job, "death_before_commit_receipt")

    def test_death_before_receipt_insert(self):
        """Death before receipt INSERT: receipt must not exist after death."""
        create(self.db, "j1-r", INPUT_RAW)
        q = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=_proc_death_at_phase,
            args=(self.db, "j1-r", "create_receipt", "before_receipt_insert", q),
        )
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate(); p.join(timeout=5)

        # Check no receipt
        conn = open_and_init(self.db)
        try:
            c = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE job_id='j1-r'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 0, "No receipt should exist after death before insert")

    def test_death_after_receipt_insert_atomic_rollback(self):
        """
        Death after receipt INSERT but before job UPDATE (after_receipt_insert phase).
        The whole transaction rolls back atomically — no orphaned receipt.
        """
        create(self.db, "j1-ar", INPUT_RAW)
        q = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=_proc_death_at_phase,
            args=(self.db, "j1-ar", "create_receipt", "after_receipt_insert", q),
        )
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate(); p.join(timeout=5)

        # Atomic rollback: receipt must NOT exist
        conn = open_and_init(self.db)
        try:
            c = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE job_id='j1-ar'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 0, "Receipt must not exist after atomic rollback")

    def test_death_before_commit_summarize(self):
        """Death before_commit during summarize: job resumes from summarize."""
        job, rows = self._death_and_recover("summarize", "before_commit")
        self._assert_done_one_receipt(job, "death_before_commit_summarize")

    def test_recovery_skips_completed_steps(self):
        """
        A completes validate, dies before create_receipt commit.
        B must not re-run validate; checkpoint should list validate after A.
        """
        create(self.db, "j1-sk", INPUT_RAW)
        q = multiprocessing.Queue()
        p = multiprocessing.Process(
            target=_proc_death_at_phase,
            args=(self.db, "j1-sk", "create_receipt", "before_commit", q),
        )
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate(); p.join(timeout=5)

        # At this point validate should be in checkpoint (A committed it)
        info = get_status(self.db, "j1-sk")
        self.assertEqual(info["last_completed_step"], "validate",
                         "validate should be committed before death")

        _expire_lease(self.db, "j1-sk")
        job_b = run_job(self.db, "j1-sk", INPUT_RAW, "wB")
        self.assertEqual(job_b.status, Status.DONE)

        rows = get_history(self.db, "j1-sk")
        # Count begin_step events for validate — should be exactly 1 (from A)
        validate_begins = [r for r in rows if r.event == "begin_step" and r.step == "validate"]
        self.assertEqual(len(validate_begins), 1, "B must not re-run validate")

        # Exactly one receipt
        conn = open_and_init(self.db)
        try:
            c = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE job_id='j1-sk'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 1)


# ============================================================
# 12. NEEDS_REVIEW on invalid checkpoint/receipt evidence
# ============================================================

class TestNeedsReview(_DBTest):
    def _corrupt_checkpoint(self, job_id: str, bad_json: str):
        conn = open_and_init(self.db)
        try:
            conn.execute(
                "UPDATE jobs SET checkpoint_json=? WHERE job_id=?",
                (bad_json, job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_bad_checkpoint_version_holds(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        self._corrupt_checkpoint("j1", '{"schema_version":99,"completed_steps":[],"results":{}}')
        job = begin_step(self.db, "j1", "wA", job.generation, "validate")
        self.assertEqual(job.status, Status.NEEDS_REVIEW)

    def test_bad_checkpoint_bool_version_holds(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        self._corrupt_checkpoint("j1", '{"schema_version":true,"completed_steps":[],"results":{}}')
        job = begin_step(self.db, "j1", "wA", job.generation, "validate")
        self.assertEqual(job.status, Status.NEEDS_REVIEW)

    def test_needs_review_terminal_no_claim(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        self._corrupt_checkpoint("j1", '{"schema_version":99,"completed_steps":[],"results":{}}')
        begin_step(self.db, "j1", "wA", job.generation, "validate")
        with self.assertRaises(NotClaimable):
            claim(self.db, "j1", INPUT_RAW, "wB")

    def test_receipt_ahead_of_checkpoint_holds(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        _, input_hash = hash_input(INPUT_RAW)
        payload = step_create_receipt("j1", input_hash, "sample.txt")
        p_hash = sha256hex(canonical_result(payload).encode())
        r_json = canonical_result(payload)
        r_hash = hash_result(payload)
        conn = open_and_init(self.db)
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO receipts(job_id,step_id,input_hash,payload_hash,"
                "result_json,result_hash,committed_at) VALUES(?,?,?,?,?,?,?)",
                ("j1","create_receipt",input_hash,p_hash,r_json,r_hash,1.0),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        job = begin_step(self.db, "j1", "wA", job.generation, "validate")
        self.assertEqual(job.status, Status.NEEDS_REVIEW)

    def test_extra_results_map_entry_holds(self):
        """Extra key in checkpoint results that isn't in completed_steps → NEEDS_REVIEW."""
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        bad = '{"schema_version":1,"completed_steps":[],"results":{"validate":{"result":{},"result_hash":"x"}}}'
        self._corrupt_checkpoint("j1", bad)
        job = begin_step(self.db, "j1", "wA", job.generation, "validate")
        self.assertEqual(job.status, Status.NEEDS_REVIEW)

    def test_unknown_outcome_stays_held(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        hold_for_review(self.db, "j1", "wA", job.generation, "unknown external outcome")
        info = get_status(self.db, "j1")
        self.assertEqual(info["status"], "NEEDS_REVIEW")
        self.assertEqual(info["next_action"], "inspect only")
        # Still NEEDS_REVIEW after status reads
        self.assertEqual(get_status(self.db, "j1")["status"], "NEEDS_REVIEW")

    def test_malformed_checkpoint_json_holds(self):
        """Non-JSON checkpoint_json causes hold on begin_step."""
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        self._corrupt_checkpoint("j1", "NOT JSON {{")
        job = begin_step(self.db, "j1", "wA", job.generation, "validate")
        self.assertEqual(job.status, Status.NEEDS_REVIEW)

    def test_malformed_receipt_result_json_holds(self):
        """Receipt with bad result_json causes hold on begin_step of summarize."""
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        gen = job.generation
        begin_step(self.db, "j1", "wA", gen, "validate")
        commit_step(self.db, "j1", "wA", gen, "validate")
        begin_step(self.db, "j1", "wA", gen, "create_receipt")
        commit_step(self.db, "j1", "wA", gen, "create_receipt")
        # Corrupt the receipt's result_json directly
        conn = open_and_init(self.db)
        try:
            conn.execute(
                "UPDATE receipts SET result_json='NOT JSON' WHERE job_id='j1'"
            )
            conn.commit()
        finally:
            conn.close()
        job = begin_step(self.db, "j1", "wA", gen, "summarize")
        self.assertEqual(job.status, Status.NEEDS_REVIEW)


# ============================================================
# 13. Clock injection
# ============================================================

class TestClockInjection(_DBTest):
    def test_inf_clock_rejected(self):
        with self.assertRaises(ValueError):
            create(self.db, "j1", INPUT_RAW, clock=lambda: float("inf"))

    def test_nan_clock_rejected(self):
        with self.assertRaises(ValueError):
            create(self.db, "j1", INPUT_RAW, clock=lambda: float("nan"))

    def test_negative_inf_rejected(self):
        with self.assertRaises(ValueError):
            create(self.db, "j1", INPUT_RAW, clock=lambda: float("-inf"))


# ============================================================
# 14. Deterministic step results
# ============================================================

class TestDeterministicSteps(unittest.TestCase):
    def test_validate_stable(self):
        self.assertEqual(step_validate("j","x"*64), step_validate("j","x"*64))

    def test_receipt_stable(self):
        _, h = hash_input(INPUT_RAW)
        self.assertEqual(
            step_create_receipt("j",h,"a.txt"),
            step_create_receipt("j",h,"a.txt"),
        )

    def test_summarize_stable(self):
        _, h = hash_input(INPUT_RAW)
        vr = step_validate("j",h)
        rr = step_create_receipt("j",h,"a.txt")
        self.assertEqual(step_summarize("j",vr,rr), step_summarize("j",vr,rr))

    def test_hash_result_64_lowercase(self):
        r = step_validate("j","a"*64)
        h = hash_result(r)
        self.assertEqual(len(h), 64)
        self.assertEqual(h, h.lower())


# ============================================================
# 15. Expiry boundary
# ============================================================

class TestExpiryBoundary(_DBTest):
    def test_exact_expiry_is_expired(self):
        t0 = 1_000_000.0
        ca = FakeClockFixed(t0)
        create(self.db, "j1", INPUT_RAW, clock=ca)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0, clock=ca)
        expiry = job.lease_expiry
        with self.assertRaises(StaleOwner):
            begin_step(self.db, "j1", "wA", job.generation, "validate",
                       clock=FakeClockFixed(expiry))

    def test_one_ms_before_expiry_is_live(self):
        t0 = 1_000_000.0
        ca = FakeClockFixed(t0)
        create(self.db, "j1", INPUT_RAW, clock=ca)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0, clock=ca)
        expiry = job.lease_expiry
        job2 = begin_step(self.db, "j1", "wA", job.generation, "validate",
                          clock=FakeClockFixed(expiry - 0.001))
        self.assertEqual(job2.status, Status.RUNNING)


# ============================================================
# 16. Two-worker recovery (in-process, clock injection)
# ============================================================

class TestTwoWorkerRecovery(_DBTest):
    def test_a_stops_after_receipt_b_finishes(self):
        c_a = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c_a)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=60.0, clock=c_a)
        gen_a = job.generation

        begin_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        begin_step(self.db, "j1", "wA", gen_a, "create_receipt", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "create_receipt", clock=c_a)

        _expire_lease(self.db, "j1")

        c_b = _monotonic_clock(1_000_100.0)
        job_b = claim(self.db, "j1", INPUT_RAW, "wB", ttl=60.0, clock=c_b)
        self.assertEqual(job_b.generation, gen_a + 1)
        begin_step(self.db, "j1", "wB", job_b.generation, "summarize", clock=c_b)
        job_done = commit_step(self.db, "j1", "wB", job_b.generation, "summarize", clock=c_b)
        self.assertEqual(job_done.status, Status.DONE)

    def test_one_receipt_after_two_worker_run(self):
        c_a = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c_a)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=60.0, clock=c_a)
        gen_a = job.generation
        begin_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        begin_step(self.db, "j1", "wA", gen_a, "create_receipt", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "create_receipt", clock=c_a)
        _expire_lease(self.db, "j1")
        c_b = _monotonic_clock(1_000_100.0)
        run_job(self.db, "j1", INPUT_RAW, "wB", clock=c_b)
        conn = open_and_init(self.db)
        try:
            c = conn.execute("SELECT COUNT(*) FROM receipts WHERE job_id='j1'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(c, 1)

    def test_replay_after_two_worker_run(self):
        c_a = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c_a)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=60.0, clock=c_a)
        gen_a = job.generation
        begin_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        begin_step(self.db, "j1", "wA", gen_a, "create_receipt", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "create_receipt", clock=c_a)
        _expire_lease(self.db, "j1")
        c_b = _monotonic_clock(1_000_100.0)
        run_job(self.db, "j1", INPUT_RAW, "wB", clock=c_b)
        s = replay(self.db, "j1", INPUT_RAW)
        self.assertEqual(s["job_id"], "j1")
        self.assertTrue(s["validated"])

    def test_history_two_generations(self):
        c_a = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c_a)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=60.0, clock=c_a)
        gen_a = job.generation
        begin_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        commit_step(self.db, "j1", "wA", gen_a, "validate", clock=c_a)
        _expire_lease(self.db, "j1")
        c_b = _monotonic_clock(1_000_100.0)
        run_job(self.db, "j1", INPUT_RAW, "wB", clock=c_b)
        rows = get_history(self.db, "j1")
        claim_gens = sorted(r.generation for r in rows if r.event == "claim")
        self.assertEqual(claim_gens, [1, 2])


# ============================================================
# 17. guarded FAILED transition
# ============================================================

class TestMarkFailed(_DBTest):
    def test_mark_failed_transitions(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        job = mark_failed(self.db, "j1", "wA", job.generation, "known error")
        self.assertEqual(job.status, Status.FAILED)
        self.assertIsNone(job.owner)
        self.assertIsNone(job.lease_expiry)

    def test_mark_failed_stale_raises(self):
        c = _monotonic_clock(1_000_000.0)
        create(self.db, "j1", INPUT_RAW, clock=c)
        job_a = claim(self.db, "j1", INPUT_RAW, "wA", ttl=0.001, clock=c)
        for _ in range(20):
            c()
        claim(self.db, "j1", INPUT_RAW, "wB", ttl=30.0, clock=c)
        with self.assertRaises(StaleOwner):
            mark_failed(self.db, "j1", "wA", job_a.generation, "x")

    def test_failed_inspect_only(self):
        create(self.db, "j1", INPUT_RAW)
        job = claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        mark_failed(self.db, "j1", "wA", job.generation, "deliberate")
        info = get_status(self.db, "j1")
        self.assertEqual(info["status"], "FAILED")
        self.assertEqual(info["next_action"], "inspect only")


# ============================================================
# 18. Schema version check — no write on unknown version
# ============================================================

class TestSchemaVersion(unittest.TestCase):
    def setUp(self):
        self.db = _tmpdb()

    def tearDown(self):
        _cleanup(self.db)

    def test_unknown_version_raises_no_write(self):
        """Write version=99 directly, then open_and_init must raise without tables."""
        # First create a minimal DB with a bad version
        import sqlite3 as _sq3
        conn = _sq3.connect(self.db)
        conn.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_meta VALUES('version','99')")
        conn.commit()
        conn.close()

        with self.assertRaises(RuntimeError) as ctx:
            open_and_init(self.db)
        self.assertIn("Unsupported", str(ctx.exception))

        # Verify jobs table was NOT created
        conn2 = _sq3.connect(self.db)
        try:
            tables = {r[0] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        finally:
            conn2.close()
        self.assertNotIn("jobs", tables)


# ============================================================
# 19. Lock-contention retry (simulated)
# ============================================================

class TestLockContention(_DBTest):
    def test_non_lock_error_not_retried(self):
        """An unrecognised OperationalError propagates immediately."""
        import sqlite3 as _sq3
        conn = open_and_init(self.db)
        try:
            call_count = [0]
            def bad_body(c, now):
                call_count[0] += 1
                raise _sq3.OperationalError("no such table: nonexistent")
            with self.assertRaises(_sq3.OperationalError):
                run_immediate(conn, bad_body)
            self.assertEqual(call_count[0], 1, "Should not retry non-lock error")
        finally:
            conn.close()

    def test_body_exception_rolls_back(self):
        """Exception in body rolls back, leaving DB unchanged."""
        create(self.db, "j1", INPUT_RAW)
        conn = open_and_init(self.db)
        try:
            def _body(c, now):
                c.execute(
                    "UPDATE jobs SET status='RUNNING' WHERE job_id='j1'"
                )
                raise RuntimeError("deliberate")
            with self.assertRaises(RuntimeError):
                run_immediate(conn, _body)
        finally:
            conn.close()
        self.assertEqual(get_status(self.db, "j1")["status"], "PENDING")


# ============================================================
# 20. Worker ID and TTL validation
# ============================================================

class TestWorkerIdTTLValidation(_DBTest):
    def test_empty_worker_id_raises(self):
        create(self.db, "j1", INPUT_RAW)
        with self.assertRaises(ValueError):
            claim(self.db, "j1", INPUT_RAW, "")

    def test_non_positive_ttl_raises(self):
        create(self.db, "j1", INPUT_RAW)
        with self.assertRaises(ValueError):
            claim(self.db, "j1", INPUT_RAW, "wA", ttl=0.0)

    def test_inf_ttl_raises(self):
        create(self.db, "j1", INPUT_RAW)
        with self.assertRaises(ValueError):
            claim(self.db, "j1", INPUT_RAW, "wA", ttl=float("inf"))

    def test_heartbeat_ge_ttl_raises(self):
        create(self.db, "j1", INPUT_RAW)
        claim(self.db, "j1", INPUT_RAW, "wA", ttl=30.0)
        with self.assertRaises(ValueError):
            run_job(self.db, "j1", INPUT_RAW, "wB", ttl=5.0, heartbeat=5.0)


# ============================================================
# 21. Checkpoint load_checkpoint strict validation
# ============================================================

class TestLoadCheckpoint(unittest.TestCase):
    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            load_checkpoint("NOT JSON")

    def test_non_object_raises(self):
        with self.assertRaises(ValueError):
            load_checkpoint("[1,2,3]")

    def test_bool_version_raises(self):
        with self.assertRaises(ValueError):
            load_checkpoint('{"schema_version":true,"completed_steps":[],"results":{}}')

    def test_duplicate_keys_raises(self):
        with self.assertRaises(ValueError):
            load_checkpoint(
                '{"schema_version":1,"schema_version":1,'
                '"completed_steps":[],"results":{}}'
            )

    def test_missing_completed_steps_raises(self):
        with self.assertRaises(ValueError):
            load_checkpoint('{"schema_version":1,"results":{}}')


if __name__ == "__main__":
    unittest.main(verbosity=2)
