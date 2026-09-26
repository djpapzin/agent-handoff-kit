"""Input canonicalisation, hashing, and step result functions."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional

from .models import SchemaError

# ---------------------------------------------------------------------------
# Allowed ASCII identifier pattern for task/artifact fields.
# fullmatch prevents trailing-newline/whitespace from slipping through.
# ---------------------------------------------------------------------------
_IDENT_RE = re.compile(r'[A-Za-z0-9_\-\.]{1,128}')


# ---------------------------------------------------------------------------
# Input validation and canonicalisation
# ---------------------------------------------------------------------------

def _no_duplicate_keys(pairs: list) -> dict:
    """object_pairs_hook that raises SchemaError on duplicate JSON keys."""
    seen: Dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise SchemaError(f"Duplicate JSON key: {k!r}")
        seen[k] = v
    return seen


def validate_input_structure(raw: str) -> dict:
    """Parse, validate structure, and return the validated dict."""
    try:
        obj = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Input is not valid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise SchemaError("Input must be a JSON object.")

    allowed = {"schema_version", "task", "artifact"}
    unknown = set(obj) - allowed
    if unknown:
        raise SchemaError(f"Unknown input fields: {sorted(unknown)}")

    sv = obj.get("schema_version")
    if sv is None:
        raise SchemaError("Missing required field: schema_version")
    if isinstance(sv, bool) or not isinstance(sv, int):
        raise SchemaError("schema_version must be integer 1")
    if sv != 1:
        raise SchemaError(f"Unsupported schema_version: {sv}")

    task = obj.get("task")
    if task is None:
        raise SchemaError("Missing required field: task")
    if not isinstance(task, str):
        raise SchemaError("task must be a string")
    if not _IDENT_RE.fullmatch(task):
        raise SchemaError(f"task must be a bounded ASCII identifier, got: {task!r}")

    artifact = obj.get("artifact")
    if artifact is None:
        raise SchemaError("Missing required field: artifact")
    if not isinstance(artifact, str):
        raise SchemaError("artifact must be a string")
    if not _IDENT_RE.fullmatch(artifact):
        raise SchemaError(f"artifact must be a bounded ASCII identifier, got: {artifact!r}")

    return obj


def canonicalise(obj: dict) -> bytes:
    """Return canonical UTF-8 bytes for the validated input dict."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def sha256hex(data: bytes) -> str:
    """Return full 64-char lowercase hex SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def hash_input(raw: str) -> tuple:
    """Validate, canonicalise, hash. Returns (canonical_json_str, hash_hex)."""
    obj = validate_input_structure(raw)
    canonical_bytes = canonicalise(obj)
    canonical_str = canonical_bytes.decode("utf-8")
    h = sha256hex(canonical_bytes)
    return canonical_str, h


# ---------------------------------------------------------------------------
# Empty / initial checkpoint
# ---------------------------------------------------------------------------

EMPTY_CHECKPOINT = json.dumps(
    {"completed_steps": [], "results": {}, "schema_version": 1},
    sort_keys=True,
    separators=(",", ":"),
)


# ---------------------------------------------------------------------------
# Deterministic step result functions
# ---------------------------------------------------------------------------

def step_validate(job_id: str, input_hash: str) -> dict:
    """Pure deterministic result for the 'validate' step."""
    return {
        "input_hash": input_hash,
        "ok": True,
        "supported_schema_version": 1,
    }


def step_create_receipt(job_id: str, input_hash: str, artifact: str) -> dict:
    """Pure deterministic result / business receipt payload."""
    return {
        "artifact": artifact,
        "input_hash": input_hash,
        "job_id": job_id,
    }


def step_summarize(
    job_id: str,
    validate_result: dict,
    receipt_result: dict,
) -> dict:
    """Pure deterministic summary derived from two prior results."""
    return {
        "artifact": receipt_result["artifact"],
        "input_hash": validate_result["input_hash"],
        "job_id": job_id,
        "receipt_for": receipt_result["job_id"],
        "validated": validate_result["ok"],
    }


def canonical_result(result: dict) -> str:
    """Canonical JSON string of a step result (no timestamps)."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_result(result: dict) -> str:
    """Full SHA-256 hex of canonical result JSON."""
    return sha256hex(canonical_result(result).encode("utf-8"))


# ---------------------------------------------------------------------------
# Checkpoint manipulation
# ---------------------------------------------------------------------------

def _cp_no_duplicate_keys(pairs: list) -> dict:
    """object_pairs_hook for checkpoint JSON — raises ValueError on dup keys."""
    seen: Dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"Duplicate key in checkpoint JSON: {k!r}")
        seen[k] = v
    return seen


def load_checkpoint(checkpoint_json: str) -> dict:
    """
    Parse and minimally validate a checkpoint JSON string.

    Raises ValueError on:
    - invalid JSON
    - non-object top-level
    - duplicate keys
    - schema_version not integer 1 (bool rejected)
    - missing completed_steps list or results dict
    """
    try:
        cp = json.loads(checkpoint_json, object_pairs_hook=_cp_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Checkpoint is not valid JSON: {exc}") from exc
    if not isinstance(cp, dict):
        raise ValueError("Checkpoint must be a JSON object.")
    sv = cp.get("schema_version")
    # Reject booleans (isinstance(True, int) is True in Python)
    if isinstance(sv, bool) or not isinstance(sv, int) or sv != 1:
        raise ValueError(f"Unsupported checkpoint schema_version: {sv!r}")
    if not isinstance(cp.get("completed_steps"), list):
        raise ValueError("Checkpoint missing completed_steps list.")
    if not isinstance(cp.get("results"), dict):
        raise ValueError("Checkpoint missing results map.")
    return cp


def next_step_from_checkpoint(cp: dict) -> Optional[str]:
    """Return the next step name, or None if all steps complete."""
    from .models import STEPS
    done = cp["completed_steps"]
    for step in STEPS:
        if step not in done:
            return step
    return None


def extend_checkpoint(cp: dict, step_name: str, result: dict) -> str:
    """Return new checkpoint JSON string with step appended."""
    new_done = list(cp["completed_steps"]) + [step_name]
    new_results = dict(cp["results"])
    new_results[step_name] = {
        "result": result,
        "result_hash": hash_result(result),
    }
    new_cp = {
        "completed_steps": new_done,
        "results": new_results,
        "schema_version": 1,
    }
    return json.dumps(new_cp, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
