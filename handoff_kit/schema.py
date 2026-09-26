"""DDL, schema initialisation, and integrity triggers for Agent Handoff Kit."""
from __future__ import annotations

import sqlite3

# The only supported DB schema version.
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# DDL — applied only after version check on existing databases.
# ---------------------------------------------------------------------------

# Per-connection pragmas applied before any reads or writes.
_CONN_PRAGMAS = """\
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = FULL;
PRAGMA foreign_keys = ON;
"""

# Table + trigger DDL.  Executed only on a fresh (or confirmed matching) DB.
_SCHEMA_DDL = """\
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY NOT NULL,
    input_json      TEXT NOT NULL,
    input_hash      TEXT NOT NULL
        CHECK(
            length(input_hash) = 64
            AND lower(input_hash) = input_hash
            AND NOT input_hash GLOB '*[^0-9a-f]*'
        ),
    status          TEXT NOT NULL
        CHECK(status IN ('PENDING','CLAIMED','RUNNING','CHECKPOINT',
                          'DONE','FAILED','NEEDS_REVIEW')),
    owner           TEXT,
    lease_expiry    REAL
        CHECK(lease_expiry IS NULL OR (typeof(lease_expiry) = 'real' AND lease_expiry > 0)),
    generation      INTEGER NOT NULL DEFAULT 0
        CHECK(generation >= 0),
    checkpoint_json TEXT NOT NULL DEFAULT '{"completed_steps":[],"results":{},"schema_version":1}',
    active_step     TEXT,
    failure_reason  TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    -- owner and lease_expiry must be NULL for PENDING/terminal
    CHECK(
        (status IN ('PENDING','DONE','FAILED','NEEDS_REVIEW')
         AND owner IS NULL AND lease_expiry IS NULL AND active_step IS NULL)
        OR
        (status IN ('CLAIMED','RUNNING','CHECKPOINT')
         AND owner IS NOT NULL AND lease_expiry IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS receipts (
    job_id       TEXT PRIMARY KEY NOT NULL
        REFERENCES jobs(job_id),
    step_id      TEXT NOT NULL DEFAULT 'create_receipt'
        CHECK(step_id = 'create_receipt'),
    input_hash   TEXT NOT NULL
        CHECK(
            length(input_hash) = 64
            AND lower(input_hash) = input_hash
            AND NOT input_hash GLOB '*[^0-9a-f]*'
        ),
    payload_hash TEXT NOT NULL
        CHECK(
            length(payload_hash) = 64
            AND lower(payload_hash) = payload_hash
            AND NOT payload_hash GLOB '*[^0-9a-f]*'
        ),
    result_json  TEXT NOT NULL,
    result_hash  TEXT NOT NULL
        CHECK(
            length(result_hash) = 64
            AND lower(result_hash) = result_hash
            AND NOT result_hash GLOB '*[^0-9a-f]*'
        ),
    committed_at REAL NOT NULL,
    UNIQUE(job_id, step_id, input_hash)
);

CREATE TABLE IF NOT EXISTS history (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL
        REFERENCES jobs(job_id),
    old_status TEXT,
    new_status TEXT NOT NULL,
    owner      TEXT,
    generation INTEGER NOT NULL,
    event      TEXT NOT NULL,
    step       TEXT,
    reason     TEXT,
    recorded_at REAL NOT NULL
);

-- Prevent UPDATE on history
CREATE TRIGGER IF NOT EXISTS trg_history_no_update
    BEFORE UPDATE ON history
BEGIN
    SELECT RAISE(ABORT, 'history rows are immutable');
END;

-- Prevent DELETE on history
CREATE TRIGGER IF NOT EXISTS trg_history_no_delete
    BEFORE DELETE ON history
BEGIN
    SELECT RAISE(ABORT, 'history rows are append-only');
END;

-- Prevent UPDATE on receipts
CREATE TRIGGER IF NOT EXISTS trg_receipts_no_update
    BEFORE UPDATE ON receipts
BEGIN
    SELECT RAISE(ABORT, 'receipt rows are immutable');
END;

-- Prevent DELETE on receipts
CREATE TRIGGER IF NOT EXISTS trg_receipts_no_delete
    BEFORE DELETE ON receipts
BEGIN
    SELECT RAISE(ABORT, 'receipt rows are immutable');
END;

-- Prevent DELETE on jobs
CREATE TRIGGER IF NOT EXISTS trg_jobs_no_delete
    BEFORE DELETE ON jobs
BEGIN
    SELECT RAISE(ABORT, 'job rows cannot be deleted');
END;

-- Prevent changing job identity or input
CREATE TRIGGER IF NOT EXISTS trg_jobs_immutable_identity
    BEFORE UPDATE OF job_id, input_json, input_hash ON jobs
BEGIN
    SELECT RAISE(ABORT, 'job identity and input are immutable');
END;
"""


def _apply_conn_pragmas(conn: sqlite3.Connection) -> None:
    """Set per-connection pragmas (WAL, synchronous, foreign_keys)."""
    conn.executescript(_CONN_PRAGMAS)


def init_db(conn: sqlite3.Connection) -> None:
    """
    Initialise or verify schema.

    Order of operations:
    1. Check existing schema version BEFORE any writes or pragmas that
       modify the database file.  If the version is unknown, raise immediately.
    2. Apply per-connection pragmas (WAL mode etc.).  Only reached when the
       version is absent (fresh DB) or matches.
    3. Create tables/triggers.
    4. Insert version row for fresh databases.
    """
    # Step 1 — check version before any writes (no pragma executescript yet)
    try:
        cur = conn.execute("SELECT value FROM schema_meta WHERE key='version'")
        row = cur.fetchone()
    except sqlite3.OperationalError:
        # schema_meta doesn't exist yet — fresh database
        row = None

    if row is not None:
        stored = int(row[0])
        if stored != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported DB schema version {stored}; expected {SCHEMA_VERSION}. "
                "No writes performed."
            )

    # Step 2 — apply pragmas (only after version check passes)
    _apply_conn_pragmas(conn)

    # Step 3 — safe to create schema (version matches or is absent)
    conn.executescript(_SCHEMA_DDL)

    # Step 4 — insert version row if still missing
    cur = conn.execute("SELECT value FROM schema_meta WHERE key='version'")
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
