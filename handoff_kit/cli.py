"""Thin CLI for the Agent Handoff Kit."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .worker import (
    create,
    get_history,
    get_status,
    replay,
    run_job,
)
from .models import (
    AlreadyExists,
    GuardFailed,
    HandoffError,
    InputHashMismatch,
    NotClaimable,
    NotFound,
    StaleOwner,
)

DEFAULT_DB = "handoff.db"


def _load_input(input_file: str) -> str:
    try:
        return Path(input_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read input file {input_file!r}: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_create(args: argparse.Namespace) -> int:
    raw = _load_input(args.input_file)
    try:
        job = create(args.db, args.job_id, raw)
        print(f"Created job {job.job_id!r} status={job.status.value}")
        return 0
    except AlreadyExists as exc:
        print(f"already-exists: {exc}", file=sys.stderr)
        return 2
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    raw = _load_input(args.input_file)
    try:
        job = run_job(args.db, args.job_id, raw, args.worker_id)
        print(f"Job {job.job_id!r} → {job.status.value}")
        if job.failure_reason:
            print(f"  reason: {job.failure_reason}")
        return 0
    except (NotClaimable, InputHashMismatch, NotFound, GuardFailed, StaleOwner) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_recover(args: argparse.Namespace) -> int:
    # recover shares the same run_job path; the claim inside handles takeover
    return cmd_run(args)


def cmd_status(args: argparse.Namespace) -> int:
    try:
        info = get_status(args.db, args.job_id)
        print(json.dumps(info, indent=2))
        return 0
    except NotFound as exc:
        print(f"not-found: {exc}", file=sys.stderr)
        return 2
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_history(args: argparse.Namespace) -> int:
    try:
        rows = get_history(args.db, args.job_id)
        for r in rows:
            print(
                f"  {r.seq:4d}  {r.old_status or 'None':>12s} → {r.new_status:<12s}"
                f"  gen={r.generation}  event={r.event}"
                + (f"  step={r.step}" if r.step else "")
                + (f"  reason={r.reason!r}" if r.reason else "")
            )
        return 0
    except NotFound as exc:
        print(f"not-found: {exc}", file=sys.stderr)
        return 2
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_replay(args: argparse.Namespace) -> int:
    raw = _load_input(args.input_file)
    try:
        summary = replay(args.db, args.job_id, raw)
        print(json.dumps(summary, indent=2))
        return 0
    except HandoffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m handoff_kit",
        description="Agent Handoff Kit — durable job execution with SQLite",
    )
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")

    sub = p.add_subparsers(dest="command", required=True)

    # create
    pc = sub.add_parser("create", help="Create a new job")
    pc.add_argument("--job-id", required=True)
    pc.add_argument("--input-file", required=True)
    pc.set_defaults(func=cmd_create)

    # run
    pr = sub.add_parser("run", help="Claim and execute a job")
    pr.add_argument("--job-id", required=True)
    pr.add_argument("--input-file", required=True)
    pr.add_argument("--worker-id", required=True)
    pr.set_defaults(func=cmd_run)

    # recover
    prec = sub.add_parser("recover", help="Recover (re-claim and continue) a job")
    prec.add_argument("--job-id", required=True)
    prec.add_argument("--input-file", required=True)
    prec.add_argument("--worker-id", required=True)
    prec.set_defaults(func=cmd_recover)

    # status
    ps = sub.add_parser("status", help="Show job status")
    ps.add_argument("--job-id", required=True)
    ps.set_defaults(func=cmd_status)

    # history
    ph = sub.add_parser("history", help="Show job history")
    ph.add_argument("--job-id", required=True)
    ph.set_defaults(func=cmd_history)

    # replay
    prl = sub.add_parser("replay", help="Replay a completed (DONE) job read-only")
    prl.add_argument("--job-id", required=True)
    prl.add_argument("--input-file", required=True)
    prl.set_defaults(func=cmd_replay)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
