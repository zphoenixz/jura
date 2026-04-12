from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class DecisionInput(BaseModel):
    """Request body for POST /api/v1/epics-police/decisions"""

    week_monday: date
    decided_at: datetime

    orphan_identifier: str
    orphan_labels: list[str] = []
    orphan_squad: str | None = None

    suggested_parent_id: str | None = None
    suggested_confidence: int | None = None
    suggested_signals: dict[str, float] | None = None
    match_source: str | None = None  # "pass1", "pass2"

    decision: str  # "accepted", "rejected", "redirected", "manual"
    actual_parent_id: str | None = None

    inferred: bool = False


class DecisionRead(BaseModel):
    id: UUID
    week_monday: date
    decided_at: datetime
    orphan_identifier: str
    orphan_labels: Any | None = None
    orphan_squad: str | None = None
    suggested_parent_id: str | None = None
    suggested_confidence: int | None = None
    suggested_signals: Any | None = None
    match_source: str | None = None
    decision: str
    actual_parent_id: str | None = None
    inferred: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class DecisionBatchInput(BaseModel):
    """Batch of decisions from a single commit operation."""

    decisions: list[DecisionInput]


class ConfidenceBand(BaseModel):
    accepted: int = 0
    rejected: int = 0
    redirected: int = 0
    precision: float = 0.0


class SignalEffectiveness(BaseModel):
    avg_accepted: float = 0.0
    avg_rejected: float = 0.0
    lift: float = 1.0


class LearningsResponse(BaseModel):
    """Distilled learnings from decision history."""

    total_decisions: int = 0
    weeks_covered: int = 0
    last_distilled: datetime | None = None

    # Confidence calibration — are our scores trustworthy?
    confidence_calibration: dict[str, ConfidenceBand] = {}

    # Which signals predict correct matches?
    signal_effectiveness: dict[str, SignalEffectiveness] = {}

    # Bayesian-updated weights (sum to 100)
    learned_weights: dict[str, float] = {}

    # Bayesian-updated thresholds
    learned_thresholds: dict[str, float] = {}

    # Structural patterns (no decay, accumulate over time)
    structural_patterns: list[dict[str, Any]] = []

    # Decision counts by type
    decision_counts: dict[str, int] = {}

    # Enough data to be meaningful?
    sufficient_data: bool = False


class DistillResponse(BaseModel):
    """Response from POST /distill."""

    distilled_at: datetime
    total_decisions: int
    weeks_covered: int
    sufficient_data: bool
    weights_changed: bool
    learned_weights: dict[str, float]
    learned_thresholds: dict[str, float]
