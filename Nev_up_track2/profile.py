"""Behavioral profile builder.

Produces a `BehavioralProfile` for a trader directly from their seed history.
Every claim — every dominant pathology — carries `evidenceSessions` and
`evidenceTrades` from the deterministic detectors. There are no LLM-generated
claims here; the LLM (when present) only summarises in natural language
*after* the evidence is fixed.

This is the heart of the anti-hallucination guarantee: by construction, the
profile cannot reference a sessionId or tradeId that doesn't exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from app.detectors import (
    CONTROL_THRESHOLD,
    SignalResult,
    score_all,
    score_session_tilt,
)
from app.schemas import BehavioralProfile, DominantPathology, PeakWindow
from app.seed import seed_store


def _peak_window(trader: dict) -> PeakWindow | None:
    """Find the 4-hour window with the highest win rate (≥3 trades)."""
    closed = []
    for s in trader.get("sessions", []):
        for t in s["trades"]:
            if t.get("status") == "closed" and t.get("exitAt"):
                closed.append(t)
    if not closed:
        return None
    buckets: dict[int, list[int]] = {}
    for t in closed:
        h = int(t["entryAt"][11:13])  # ISO-8601 UTC: positions 11-12 are hour
        b = h // 4
        buckets.setdefault(b, []).append(1 if t.get("outcome") == "win" else 0)
    qualifying = [(b, mean(v)) for b, v in buckets.items() if len(v) >= 3]
    if not qualifying:
        return None
    best_b, best_wr = max(qualifying, key=lambda x: x[1])
    return PeakWindow(startHour=best_b * 4, endHour=best_b * 4 + 4, winRate=round(best_wr, 4))


def _strengths(trader: dict, scores: dict[str, SignalResult]) -> list[str]:
    """Lightweight, evidence-free strengths list — they're descriptive, not
    diagnostic, so they don't need citations."""
    out = []
    pas = []
    for s in trader.get("sessions", []):
        for t in s["trades"]:
            if t.get("planAdherence") is not None:
                pas.append(t["planAdherence"])
    if pas and mean(pas) >= 3.5:
        out.append("Maintains high plan adherence across sessions")
    if scores["revenge_trading"].score < 0.1 and scores["overtrading"].score < 0.1:
        out.append("Disciplined emotional control around losses")
    if scores["session_tilt"].score < 0.3:
        out.append("Sessions stay coherent — not derailed by intra-session losses")
    return out or ["Active engagement with self-review"]


def build_profile(user_id: str) -> BehavioralProfile | None:
    trader = seed_store.trader(user_id)
    if not trader:
        return None

    scores = score_all(trader)

    # Dominant pathologies = every signal that crosses the control threshold,
    # ordered by score descending. Each carries hard evidence.
    dominant: list[DominantPathology] = []
    for pathology, result in sorted(
        scores.items(), key=lambda kv: -kv[1].score
    ):
        if result.score < CONTROL_THRESHOLD:
            continue
        dominant.append(
            DominantPathology(
                pathology=pathology,
                confidence=round(result.score, 4),
                evidenceSessions=result.evidence_sessions,
                evidenceTrades=result.evidence_trades,
            )
        )

    return BehavioralProfile(
        userId=user_id,
        generatedAt=datetime.now(timezone.utc),
        dominantPathologies=dominant,
        strengths=_strengths(trader, scores),
        peakPerformanceWindow=_peak_window(trader),
    )
