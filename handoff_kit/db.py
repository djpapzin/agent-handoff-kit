"""Database connection and guarded transaction layer."""
from __future__ import annotations

import math
import sqlite3
import time
from typing import Callable, Optional, TypeVar

from .schema import init_db, check_version
from pathlib import Path
from .models import HandoffError

# ---------------------------------------------------------------------------
# SQLite lock-error detection (Python 3.9 compatible — no errorcode attr)
#
# Only retry on genuine write-lock contention ("database is locked").
# disk I/O errors, unable-to-open, and nested-transaction errors are NOT
# retriable — they indicate storage failures or programming errors.
# ---------------------------------------------------------------------------

_LOCK_PHRASES = (
    "database is locked",
    "database table is locked",
)


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _LOCK_PHRASES)


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

_BUSY_TIMEOUT_MS = 5_000  # 5 s busy timeout at SQLite level


def open_conn(db_path: str) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys, and explicit tx control."""
    conn = sqlite3.connect(
        db_path,
        timeout=5.0,
        isolation_level=None,  # explicit transaction control
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # Pragmas that take effect per-connection
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        check_version(conn, allow_empty=True)
    except BaseException:
        conn.close()
        raise
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def open_and_init(db_path: str) -> sqlite3.Connection:
    """Open connection and initialise / verify schema."""
    conn = open_conn(db_path)
    try:
        init_db(conn)
        return conn
    except BaseException:
        conn.close()
        raise


def open_readonly(db_path: str):
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        check_version(conn)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except BaseException:
        conn.close()
        raise


class CommitUncertain(HandoffError):
    """The caller must halt; evidence is an observation, never retry permission."""
    def __init__(self, evidence):
        super().__init__("Commit outcome uncertain; halted. Read-only inspection: " + str(evidence))
        self.evidence = evidence


def _inspect_after_uncertainty(path):
    if not path:
        return {"error": "No durable database path available"}
    try:
        from .worker import _row_to_job, _validate_prefix
        conn = open_readonly(path)
        try:
            conn.execute("BEGIN")
            evidence = {}
            for row in conn.execute("SELECT * FROM jobs"):
                job = _row_to_job(row)
                try:
                    cp = _validate_prefix(conn, job)
                    evidence[job.job_id] = {"status": job.status.value, "completed_steps": cp["completed_steps"]}
                except Exception as exc:
                    evidence[job.job_id] = {"integrity_error": str(exc)}
            conn.execute("ROLLBACK")
            return evidence
        finally:
            conn.close()
    except Exception as exc:
        return {"storage_error": str(exc)}


# ---------------------------------------------------------------------------
# Guarded transaction helper
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_RETRY_BASE_S = 0.05

T = TypeVar("T")


def run_immediate(
    conn: sqlite3.Connection,
    body: Callable[[sqlite3.Connection, float], T],
    clock: Optional[Callable[[], float]] = None,
) -> T:
    """
    Execute *body* inside an explicit BEGIN IMMEDIATE transaction.

    1. BEGIN IMMEDIATE (serialises writers).
    2. Sample clock once inside the try block, after the lock is acquired.
       A non-finite clock value rolls back and raises ValueError.
    3. Call body(conn, now); body performs all reads/writes.
    4. COMMIT on success; ROLLBACK on any exception.

    Retries up to _MAX_RETRIES times on genuine lock contention only.
    Unrecognised OperationalError is NOT retried and propagates immediately.
    """
    _clock = clock if clock is not None else time.time
    last_exc: Exception = RuntimeError("no attempts made")

    for attempt in range(_MAX_RETRIES + 1):
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_lock_error(exc) and attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BASE_S * (2 ** attempt))
                last_exc = exc
                continue
            raise

        # Lock acquired — sample clock inside the rollback-guarded block
        try:
            now = _clock()
            if not math.isfinite(now):
                raise ValueError(f"Non-finite clock value: {now!r}")

            result = body(conn, now)
            path = conn.execute("PRAGMA database_list").fetchone()[2]
            try:
                conn.execute("COMMIT")
            except Exception as exc:
                # Never repeat a body when COMMIT might have reached disk.
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                finally:
                    conn.close()
                raise CommitUncertain(_inspect_after_uncertainty(path)) from exc
            return result
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    raise last_exc
