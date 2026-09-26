"""Data classes, enumerations, and exceptions for the Agent Handoff Kit."""
from __future__ import annotations

import dataclasses
import enum
from typing import Optional


# ---------------------------------------------------------------------------
# Status enumeration
# ---------------------------------------------------------------------------

class Status(str, enum.Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    CHECKPOINT = "CHECKPOINT"
    DONE = "DONE"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"

    @property
    def is_terminal(self) -> bool:
        return self in (Status.DONE, Status.FAILED, Status.NEEDS_REVIEW)

    @property
    def is_active(self) -> bool:
        return self in (Status.CLAIMED, Status.RUNNING, Status.CHECKPOINT)


ACTIVE_STATES = (Status.CLAIMED, Status.RUNNING, Status.CHECKPOINT)
TERMINAL_STATES = (Status.DONE, Status.FAILED, Status.NEEDS_REVIEW)

# Ordered processing steps
STEPS = ("validate", "create_receipt", "summarize")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HandoffError(Exception):
    """Base for all handoff-kit errors."""


class AlreadyExists(HandoffError):
    """Job ID already present; non-mutating."""


class NotFound(HandoffError):
    """Job ID not found."""


class NotClaimable(HandoffError):
    """Job cannot be claimed (active lease held by another worker)."""


class InputHashMismatch(HandoffError):
    """Presented input does not match the stored canonical input."""


class GuardFailed(HandoffError):
    """Write guard failed; transaction rolled back without mutation."""


class StaleOwner(HandoffError):
    """This worker's lease has expired or generation was superseded."""


class InvalidStep(HandoffError):
    """Requested step is out of order or already complete (non-mutating)."""


class InconsistentState(HandoffError):
    """Durable state is internally contradictory; job held for review."""


class UnsupportedVersion(HandoffError):
    """DB schema version or checkpoint version not supported."""


class SchemaError(HandoffError):
    """Presented input fails structural validation."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class JobRow:
    job_id: str
    input_json: str
    input_hash: str
    status: Status
    owner: Optional[str]
    lease_expiry: Optional[float]
    generation: int
    checkpoint_json: str
    active_step: Optional[str]
    failure_reason: Optional[str]
    created_at: float
    updated_at: float


@dataclasses.dataclass(frozen=True)
class ReceiptRow:
    job_id: str
    step_id: str
    input_hash: str
    payload_hash: str
    result_json: str
    result_hash: str
    committed_at: float


@dataclasses.dataclass(frozen=True)
class HistoryRow:
    seq: int
    job_id: str
    old_status: Optional[str]
    new_status: str
    owner: Optional[str]
    generation: int
    event: str
    step: Optional[str]
    reason: Optional[str]
    recorded_at: float
