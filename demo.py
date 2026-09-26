"""
Agent Handoff Kit — reproducible two-worker recovery demo.

Demo trace (section 9):
  1. Create job-demo in a fresh temp directory.
  2. Worker A subprocess: claim → validate → create_receipt (CHECKPOINT).
     Worker A process exits normally (simulating natural stop after checkpoint).
  3. Parent waits for Worker A to exit, then waits for A's short lease to expire
     (real wall-clock wait — no direct DB UPDATE).
  4. Worker B subprocess: claim (gen2) → validates two-step prefix → summarize → DONE.
  5. Replay: read-only, returns same summary, asserts receipt count = 1.
  6. Print history.

Usage:
    python demo.py [--input fixtures/input_v1.json]

    If --db is supplied and the path already exists, the demo aborts rather
    than overwriting it.  By default a fresh temp directory is used and
    cleaned up when the demo finishes.

The database file is disposable and ignored by Git (*.db in .gitignore).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from handoff_kit.db import open_and_init
from handoff_kit.models import Status
from handoff_kit.worker import (
    begin_step,
    claim,
    commit_step,
    create,
    get_history,
    get_status,
    replay,
)

JOB_ID = "job-demo"
WORKER_A = "worker-A-demo"
WORKER_B = "worker-B-demo"
# Short TTL so we only wait ~3 s for lease expiry in the demo.
LEASE_TTL = 3.0


# ---------------------------------------------------------------------------
# Worker A subprocess target
# ---------------------------------------------------------------------------

def _worker_a_body(db_path: str, job_id: str, input_raw: str,
                   result_q: multiprocessing.Queue) -> None:
    """
    Worker A: claim → validate → create_receipt (CHECKPOINT).
    Returns normally after checkpoint; does NOT run summarize.
    """
    try:
        job = claim(db_path, job_id, input_raw, WORKER_A, ttl=LEASE_TTL)
        gen = job.generation
        print(f"  [A] claimed  gen={gen}  status={job.status.value}", flush=True)

        job = begin_step(db_path, job_id, WORKER_A, gen, "validate")
        print(f"  [A] begin validate", flush=True)
        job = commit_step(db_path, job_id, WORKER_A, gen, "validate")
        print(f"  [A] commit validate  status={job.status.value}", flush=True)

        job = begin_step(db_path, job_id, WORKER_A, gen, "create_receipt")
        print(f"  [A] begin create_receipt", flush=True)
        job = commit_step(db_path, job_id, WORKER_A, gen, "create_receipt")
        print(f"  [A] commit create_receipt  status={job.status.value}", flush=True)

        result_q.put(("ok", gen, job.status.value))
        # Worker A stops here — it never runs summarize.
    except Exception as exc:
        result_q.put(("error", str(exc)))


# ---------------------------------------------------------------------------
# Worker B subprocess target
# ---------------------------------------------------------------------------

def _worker_b_body(db_path: str, job_id: str, input_raw: str,
                   result_q: multiprocessing.Queue) -> None:
    """Worker B: claim (recovery) → summarize → DONE."""
    try:
        job = claim(db_path, job_id, input_raw, WORKER_B, ttl=30.0)
        gen = job.generation
        print(f"  [B] claimed  gen={gen}  status={job.status.value}", flush=True)

        job = begin_step(db_path, job_id, WORKER_B, gen, "summarize")
        print(f"  [B] begin summarize", flush=True)
        job = commit_step(db_path, job_id, WORKER_B, gen, "summarize")
        print(f"  [B] commit summarize  status={job.status.value}", flush=True)

        result_q.put(("ok", gen, job.status.value))
    except Exception as exc:
        result_q.put(("error", str(exc)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep(title: str = "") -> None:
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
    print("=" * 60)


def _wait_for_process(proc: multiprocessing.Process, label: str,
                      timeout: float = 30.0) -> None:
    proc.join(timeout=timeout)
    if proc.is_alive():
        print(f"  [{label}] process still alive after {timeout}s — terminating")
        proc.terminate()
        proc.join(timeout=5)


def _drain_queue(q: multiprocessing.Queue) -> object:
    try:
        return q.get(timeout=2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

def run_demo(db_path: str, input_file: str) -> None:
    input_raw = Path(input_file).read_text(encoding="utf-8")

    # --- Step 1: Create ---
    _sep("STEP 1: Create job")
    job = create(db_path, JOB_ID, input_raw)
    print(f"  Created {job.job_id!r}  status={job.status.value}")

    # --- Step 2: Worker A subprocess ---
    _sep("STEP 2: Worker A subprocess — validate + create_receipt")
    qa: multiprocessing.Queue = multiprocessing.Queue()
    proc_a = multiprocessing.Process(
        target=_worker_a_body,
        args=(db_path, JOB_ID, input_raw, qa),
    )
    proc_a.start()
    _wait_for_process(proc_a, "A")

    res_a = _drain_queue(qa)
    print(f"  Worker A result: {res_a}")
    if res_a is None or res_a[0] != "ok":
        print("  Worker A did not succeed — aborting demo.")
        sys.exit(1)

    gen_a = res_a[1]
    status_a = res_a[2]
    print(f"  Worker A stopped at gen={gen_a}  last_status={status_a}")

    # --- Step 3: Verify A stopped at CHECKPOINT, then wait for lease to expire ---
    _sep("STEP 3: Verify checkpoint, wait for lease to expire")
    info = get_status(db_path, JOB_ID)
    print(f"  Status: {info['status']}  "
          f"last_step={info['last_completed_step']}  "
          f"lease_expiry={info['lease_expiry']:.3f}  "
          f"now={time.time():.3f}")
    assert info["status"] == "CHECKPOINT", (
        f"Expected CHECKPOINT, got {info['status']}"
    )

    # Wait for the real lease to expire (LEASE_TTL + small buffer)
    expiry = info["lease_expiry"]
    wait_needed = max(0.0, expiry - time.time() + 0.2)
    print(f"  Waiting {wait_needed:.2f}s for lease to expire...")
    time.sleep(wait_needed)
    print(f"  Lease expired at {expiry:.3f}  now={time.time():.3f}")

    # --- Step 4: Worker B subprocess ---
    _sep("STEP 4: Worker B subprocess — recover (claim gen2 + summarize → DONE)")
    qb: multiprocessing.Queue = multiprocessing.Queue()
    proc_b = multiprocessing.Process(
        target=_worker_b_body,
        args=(db_path, JOB_ID, input_raw, qb),
    )
    proc_b.start()
    _wait_for_process(proc_b, "B")

    res_b = _drain_queue(qb)
    print(f"  Worker B result: {res_b}")
    if res_b is None or res_b[0] != "ok":
        print(f"  Worker B failed — aborting demo: {res_b}")
        sys.exit(1)

    gen_b = res_b[1]
    assert gen_b == gen_a + 1, f"Expected gen={gen_a + 1}, got {gen_b}"
    print(f"  Worker B completed: gen={gen_b}  status={res_b[2]}")
    assert res_b[2] == "DONE", f"Expected DONE, got {res_b[2]}"

    # --- Step 5: Replay ---
    _sep("STEP 5: Replay — read-only")
    summary = replay(db_path, JOB_ID, input_raw)
    print(f"  Summary:\n{json.dumps(summary, indent=4)}")

    conn = open_and_init(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE job_id=?", (JOB_ID,)
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"  Receipt count: {count}  (expected exactly 1)")
    assert count == 1, f"Receipt count must be 1, got {count}"

    summary2 = replay(db_path, JOB_ID, input_raw)
    assert summary == summary2, "Replay must be idempotent"
    print("  Second replay matches: OK")

    # --- Step 6: History ---
    _sep("STEP 6: History")
    rows = get_history(db_path, JOB_ID)
    for r in rows:
        print(
            f"  {r.seq:4d}  {(r.old_status or 'None'):>12s} → {r.new_status:<12s}"
            f"  gen={r.generation}  owner={r.owner or '—':16s}  event={r.event}"
            + (f"  step={r.step}" if r.step else "")
        )

    _sep("DEMO COMPLETE")
    print(f"  Job {JOB_ID!r} is DONE.")
    print(f"  Receipt count: {count}")
    print(f"  History events: {len(rows)}")
    print(f"  Worker A gen={gen_a}, Worker B gen={gen_b}")
    print(f"  ✓  Two-worker recovery demo passed.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Handoff Kit two-worker recovery demo"
    )
    parser.add_argument(
        "--db", default=None,
        help=(
            "SQLite database path. Must NOT already exist. "
            "Defaults to a fresh file in a temp directory (auto-cleaned up)."
        ),
    )
    parser.add_argument(
        "--input", default="fixtures/input_v1.json",
        help="Input JSON fixture file (default: fixtures/input_v1.json)",
    )
    args = parser.parse_args()

    # Determine DB path
    tmpdir = None
    if args.db is None:
        tmpdir = tempfile.mkdtemp(prefix="handoff_demo_")
        db_path = os.path.join(tmpdir, "demo.db")
        print(f"Using temp database: {db_path}")
    else:
        db_path = args.db
        if os.path.exists(db_path):
            print(
                f"error: --db path {db_path!r} already exists. "
                "Supply a new path or omit --db to use a temp directory.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Using database: {db_path}")

    try:
        run_demo(db_path, args.input)
    finally:
        if tmpdir is not None:
            import shutil
            try:
                shutil.rmtree(tmpdir)
                print(f"Cleaned up temp directory: {tmpdir}")
            except Exception as exc:
                print(f"Warning: could not clean up {tmpdir}: {exc}")


if __name__ == "__main__":
    main()
