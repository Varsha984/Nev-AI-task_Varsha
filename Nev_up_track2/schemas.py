"""Pydantic request/response models.

Field names are pinned to the OpenAPI spec exactly. Two contracts overlap here:
  * the public API surface (memory, audit, profile, session events)
  * the canonical Trade schema from the kick-off deck

Anything sent to clients goes through these models; any field rename is a
breaking change to interoperability scoring.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Shared trade & session shapes
# --------------------------------------------------------------------------- #
class Trade(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tradeId: str
    userId: str
    sessionId: str
    asset: str
    assetClass: Literal["equity", "crypto", "forex"]
    direction: Literal["long", "short"]
    entryPrice: float
    exitPrice: float | None = None
    quantity: float
    entryAt: str
    exitAt: str | None = None
    status: Literal["open", "closed", "cancelled"]
    outcome: Literal["win", "loss"] | None = None
    pnl: float | None = None
    planAdherence: int | None = None
    emotionalState: Literal["calm", "anxious", "greedy", "fearful", "neutral"] | None = None
    entryRationale: str | None = None
    revengeFlag: bool = False


class BehavioralMetrics(BaseModel):
    """Subset of the spec's BehavioralMetrics — what we actually persist."""

    planAdherenceScore: float = 0.0
    sessionTiltIndex: float = 0.0
    revengeTrades: int = 0
    overtradingEvents: int = 0
    winRateByEmotionalState: dict[str, dict[str, float]] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Memory contract — exactly the three endpoints the deck specifies
# --------------------------------------------------------------------------- #
class MemoryWriteBody(BaseModel):
    summary: str = Field(..., min_length=1, max_length=4000)
    metrics: BehavioralMetrics = Field(default_factory=BehavioralMetrics)
    tags: list[str] = Field(default_factory=list, max_length=32)


class SessionSummaryRecord(BaseModel):
    """Returned by GET /memory/{userId}/sessions/{sessionId}.

    Must round-trip exactly what was written — anti-hallucination depends on
    this being byte-stable.
    """

    userId: str
    sessionId: str
    summary: str
    metrics: BehavioralMetrics
    tags: list[str]
    createdAt: datetime
    updatedAt: datetime


class ContextResponse(BaseModel):
    sessions: list[SessionSummaryRecord]
    patternIds: list[str]


# --------------------------------------------------------------------------- #
# Coaching / session events
# --------------------------------------------------------------------------- #
class SessionEventTrade(BaseModel):
    """Trade payload accepted by POST /session/events.

    Looser than the canonical Trade — closing fields may be missing for an
    open trade. We coerce to the canonical schema before signal detection.
    """

    model_config = ConfigDict(extra="ignore")

    tradeId: str
    userId: str
    sessionId: str
    asset: str
    assetClass: Literal["equity", "crypto", "forex"]
    direction: Literal["long", "short"]
    entryPrice: float
    exitPrice: float | None = None
    quantity: float
    entryAt: str
    exitAt: str | None = None
    status: Literal["open", "closed", "cancelled"] = "closed"
    outcome: Literal["win", "loss"] | None = None
    pnl: float | None = None
    planAdherence: int | None = None
    emotionalState: Literal["calm", "anxious", "greedy", "fearful", "neutral"] | None = None
    entryRationale: str | None = None
    revengeFlag: bool = False


class SessionEventBody(BaseModel):
    userId: str
    sessionId: str
    trades: list[SessionEventTrade]


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditBody(BaseModel):
    """Reviewers POST a coaching response back here for hallucination audit."""

    userId: str
    message: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class AuditCitation(BaseModel):
    sessionId: str
    found: bool
    source: Literal["explicit", "extracted"]


class AuditResponse(BaseModel):
    userId: str
    citations: list[AuditCitation]
    hallucinated: bool


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
class DominantPathology(BaseModel):
    pathology: str
    confidence: float
    evidenceSessions: list[str]
    evidenceTrades: list[str]


class PeakWindow(BaseModel):
    startHour: int
    endHour: int
    winRate: float


class BehavioralProfile(BaseModel):
    userId: str
    generatedAt: datetime
    dominantPathologies: list[DominantPathology]
    strengths: list[str]
    peakPerformanceWindow: PeakWindow | None = None


# --------------------------------------------------------------------------- #
# Error
# --------------------------------------------------------------------------- #
class ErrorResponse(BaseModel):
    error: str
    message: str
    traceId: str
