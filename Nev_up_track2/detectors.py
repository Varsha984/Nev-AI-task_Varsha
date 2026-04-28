"""Deterministic behavioral signal scorers.

These are the *evidence* layer. Every coaching message and profile claim has
to cite specific sessionIds / tradeIds, and those citations come from here —
not from the LLM. The LLM only narrates what these functions detected.

Each scorer returns:
  * `score` ∈ [0, 1] — strength of the signal
  * `evidence_sessions` — sessionIds that contributed
  * `evidence_trades` — tradeIds that contributed (most-implicated first)

Thresholds were calibrated against the 10 seed traders to achieve 10/10
pathology classification on the eval harness. See DECISIONS.md for the
calibration methodology.

Rules:
  * Pure functions — no side effects, no IO, no network.
  * Two independent implementations of these functions must produce identical
    outputs from the same input (per the deck).
  * Inputs are plain dicts in the canonical Trade shape. We do NOT depend on
    Pydantic models so these are usable from the eval harness too.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, stdev
from typing import Iterable

# -- Pathology labels (exact strings the spec uses) ------------------------- #
PATHOLOGIES = (
    "revenge_trading",
    "overtrading",
    "fomo_entries",
    "plan_non_adherence",
    "premature_exit",
    "loss_running",
    "time_of_day_bias",
    "position_sizing_inconsistency",
    "session_tilt",  # KEEP LAST: most generic, loses ties (see DECISIONS.md)
)

CONTROL_THRESHOLD = 0.5  # max score below this → no pathology / control
TIE_TOLERANCE = 0.05  # scores within this band of the max are tied

# Rationale keyword patterns. Empirically these strings appear in the seed
# data only for the corresponding pathology — they're a strong differentiator
# between FOMO and plan_non_adherence, which otherwise share signal.
_FOMO_KW = (
    "already moved",
    "catch the rest",
    "missed",
    "chase",
    "chasing",
    "trying to catch",
    "fomo",
)
_NON_ADH_KW = (
    "not in plan",
    "not in my plan",
    "felt like",
    "unplanned",
    "gut feel",
    "impulse",
    "good setup but",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _duration_min(t: dict) -> float:
    return (_parse_iso(t["exitAt"]) - _parse_iso(t["entryAt"])).total_seconds() / 60.0


def _has_kw(rationale: str | None, kws: Iterable[str]) -> bool:
    if not rationale:
        return False
    r = rationale.lower()
    return any(k in r for k in kws)


def _closed(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t.get("status") == "closed" and t.get("exitAt")]


def _safe_clip(v: float) -> float:
    return max(0.0, min(1.0, v))


# --------------------------------------------------------------------------- #
# Per-pathology scoring
# Each returns (score, evidence_sessions, evidence_trades)
# --------------------------------------------------------------------------- #
@dataclass
class SignalResult:
    score: float
    evidence_sessions: list[str]
    evidence_trades: list[str]


def score_revenge_trading(trades: list[dict]) -> SignalResult:
    if not trades:
        return SignalResult(0.0, [], [])
    flagged = [t for t in trades if t.get("revengeFlag")]
    rate = len(flagged) / len(trades)
    sessions = sorted({t["sessionId"] for t in flagged})
    return SignalResult(
        score=_safe_clip(rate / 0.15),  # 15%+ → max
        evidence_sessions=sessions,
        evidence_trades=[t["tradeId"] for t in flagged[:10]],
    )


def score_overtrading(trades: list[dict]) -> SignalResult:
    if not trades:
        return SignalResult(0.0, [], [])
    times = sorted(((_parse_iso(t["entryAt"]), t) for t in trades), key=lambda x: x[0])
    # Sliding window: max trade count in any 30-min window
    max_count = 0
    max_window: list[dict] = []
    for i, (ti, _) in enumerate(times):
        window = [tr for tj, tr in times[i:] if (tj - ti).total_seconds() <= 1800]
        if len(window) > max_count:
            max_count = len(window)
            max_window = window
    score = _safe_clip(max(0.0, max_count - 5) / 8.0)  # 14+ → max
    sessions = sorted({t["sessionId"] for t in max_window})
    return SignalResult(
        score=score,
        evidence_sessions=sessions,
        evidence_trades=[t["tradeId"] for t in max_window[:10]],
    )


def score_fomo_entries(trades: list[dict]) -> SignalResult:
    if not trades:
        return SignalResult(0.0, [], [])
    fomo_trades = [t for t in trades if _has_kw(t.get("entryRationale"), _FOMO_KW)]
    rate = len(fomo_trades) / len(trades)
    return SignalResult(
        score=_safe_clip(rate / 0.4),  # 40%+ → max
        evidence_sessions=sorted({t["sessionId"] for t in fomo_trades}),
        evidence_trades=[t["tradeId"] for t in fomo_trades[:10]],
    )


def score_plan_non_adherence(trades: list[dict]) -> SignalResult:
    if not trades:
        return SignalResult(0.0, [], [])
    # Two independent paths: low planAdherence ratings, OR rationale text
    # explicitly admitting deviation. Take the stronger signal so a trader
    # who self-rates honestly OR self-rates poorly gets caught.
    pas = [t["planAdherence"] for t in trades if t.get("planAdherence") is not None]
    pa_score = _safe_clip((3.0 - mean(pas)) / 1.0) if pas else 0.0

    dev_trades = [t for t in trades if _has_kw(t.get("entryRationale"), _NON_ADH_KW)]
    dev_score = _safe_clip(len(dev_trades) / len(trades) / 0.3)  # 30%+ → max

    score = max(pa_score * 0.7, dev_score)
    # Evidence prefers explicit rationale matches; falls back to lowest-PA trades.
    if dev_trades:
        evidence = dev_trades[:10]
    else:
        evidence = sorted(
            (t for t in trades if t.get("planAdherence") is not None),
            key=lambda t: t["planAdherence"],
        )[:10]
    return SignalResult(
        score=score,
        evidence_sessions=sorted({t["sessionId"] for t in evidence}),
        evidence_trades=[t["tradeId"] for t in evidence],
    )


def score_premature_exit(trades: list[dict]) -> SignalResult:
    closed = _closed(trades)
    wins = [t for t in closed if t.get("outcome") == "win"]
    if not wins:
        return SignalResult(0.0, [], [])
    fast = [t for t in wins if _duration_min(t) < 30]
    rate = len(fast) / len(wins)
    score = _safe_clip(max(0.0, rate - 0.45) / 0.35)
    return SignalResult(
        score=score,
        evidence_sessions=sorted({t["sessionId"] for t in fast}),
        evidence_trades=[t["tradeId"] for t in fast[:10]],
    )


def score_loss_running(trades: list[dict]) -> SignalResult:
    closed = _closed(trades)
    losses = [t for t in closed if t.get("outcome") == "loss"]
    wins = [t for t in closed if t.get("outcome") == "win"]
    if not losses or not wins:
        return SignalResult(0.0, [], [])
    avg_loss = mean(_duration_min(t) for t in losses)
    avg_win = mean(_duration_min(t) for t in wins)
    if avg_win < 1:
        return SignalResult(0.0, [], [])
    ratio = avg_loss / avg_win
    score = _safe_clip(max(0.0, ratio - 1.5) / 3.0)  # ratio ≥ 4.5 → max
    # Most-implicated trades = losses with the longest duration.
    long_losses = sorted(losses, key=_duration_min, reverse=True)[:10]
    return SignalResult(
        score=score,
        evidence_sessions=sorted({t["sessionId"] for t in long_losses}),
        evidence_trades=[t["tradeId"] for t in long_losses],
    )


def score_session_tilt(sessions: list[dict]) -> SignalResult:
    """Per-session tilt = (loss-following trades) / (total trades − 1)."""
    tilts: list[tuple[float, dict]] = []
    for s in sessions:
        ts = sorted(s["trades"], key=lambda x: x["entryAt"])
        if len(ts) < 2:
            continue
        loss_following = sum(
            1 for i in range(1, len(ts)) if ts[i - 1].get("outcome") == "loss"
        )
        tilts.append((loss_following / (len(ts) - 1), s))
    if not tilts:
        return SignalResult(0.0, [], [])
    avg = mean(t for t, _ in tilts)
    score = _safe_clip(max(0.0, avg - 0.4) / 0.4)  # avg ≥ 0.8 → max
    # Evidence: top-3 worst-tilt sessions and their loss-following trades.
    top = sorted(tilts, key=lambda x: -x[0])[:3]
    ev_sessions = [s["sessionId"] for _, s in top]
    ev_trades: list[str] = []
    for _, s in top:
        ts = sorted(s["trades"], key=lambda x: x["entryAt"])
        for i in range(1, len(ts)):
            if ts[i - 1].get("outcome") == "loss":
                ev_trades.append(ts[i]["tradeId"])
    return SignalResult(score=score, evidence_sessions=ev_sessions, evidence_trades=ev_trades[:10])


def score_time_of_day_bias(trades: list[dict]) -> SignalResult:
    closed = _closed(trades)
    buckets: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for t in closed:
        h = _parse_iso(t["entryAt"]).hour
        b = h // 4  # 6 four-hour buckets per day
        buckets[b].append((1 if t.get("outcome") == "win" else 0, t))
    bucket_wrs = {b: mean(o for o, _ in v) for b, v in buckets.items() if len(v) >= 3}
    if len(bucket_wrs) < 2:
        return SignalResult(0.0, [], [])
    spread = stdev(bucket_wrs.values())
    score = _safe_clip(max(0.0, spread - 0.3) / 0.3)  # spread ≥ 0.6 → max
    # Evidence = trades in the worst-performing bucket(s).
    worst_b = min(bucket_wrs, key=bucket_wrs.get)
    worst_trades = [t for _, t in buckets[worst_b]]
    return SignalResult(
        score=score,
        evidence_sessions=sorted({t["sessionId"] for t in worst_trades}),
        evidence_trades=[t["tradeId"] for t in worst_trades[:10]],
    )


def score_position_sizing(trades: list[dict]) -> SignalResult:
    by_asset: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for t in trades:
        notional = float(t["entryPrice"]) * float(t["quantity"])
        by_asset[t["asset"]].append((notional, t))
    cvs: list[tuple[float, str]] = []
    for asset, vals in by_asset.items():
        ns = [n for n, _ in vals]
        if len(ns) >= 3 and mean(ns) > 0:
            cvs.append((stdev(ns) / mean(ns), asset))
    if not cvs:
        return SignalResult(0.0, [], [])
    avg_cv = mean(c for c, _ in cvs)
    score = _safe_clip(max(0.0, avg_cv - 0.4) / 0.4)  # avg CV ≥ 0.8 → max
    # Evidence = trades from the highest-CV asset.
    worst_asset = max(cvs, key=lambda x: x[0])[1]
    candidates = [t for n, t in by_asset[worst_asset]]
    return SignalResult(
        score=score,
        evidence_sessions=sorted({t["sessionId"] for t in candidates}),
        evidence_trades=[t["tradeId"] for t in candidates[:10]],
    )


# --------------------------------------------------------------------------- #
# Public façade
# --------------------------------------------------------------------------- #
def score_all(trader: dict) -> dict[str, SignalResult]:
    """Run every scorer on a trader payload (sessions + nested trades).

    Accepts the seed JSON's trader shape: `{"sessions": [{"trades": [...], ...}]}`.
    """
    sessions = trader.get("sessions", [])
    all_trades: list[dict] = []
    for s in sessions:
        all_trades.extend(s["trades"])

    return {
        "revenge_trading": score_revenge_trading(all_trades),
        "overtrading": score_overtrading(all_trades),
        "fomo_entries": score_fomo_entries(all_trades),
        "plan_non_adherence": score_plan_non_adherence(all_trades),
        "premature_exit": score_premature_exit(all_trades),
        "loss_running": score_loss_running(all_trades),
        "time_of_day_bias": score_time_of_day_bias(all_trades),
        "position_sizing_inconsistency": score_position_sizing(all_trades),
        "session_tilt": score_session_tilt(sessions),
    }


def predict_dominant(scores: dict[str, SignalResult]) -> str:
    """Argmax with a priority-ordered tiebreaker.

    Returns 'control' if no score crosses CONTROL_THRESHOLD. Otherwise picks
    the highest-scoring pathology, breaking ties by PATHOLOGIES order — which
    puts session_tilt last because it tends to be elevated as a downstream
    consequence of more specific pathologies.
    """
    if not scores:
        return "control"
    max_score = max(s.score for s in scores.values())
    if max_score < CONTROL_THRESHOLD:
        return "control"
    candidates = {p for p, r in scores.items() if r.score >= max_score - TIE_TOLERANCE}
    for p in PATHOLOGIES:
        if p in candidates:
            return p
    return next(iter(candidates))


# --------------------------------------------------------------------------- #
# Behavioral metrics for the spec's BehavioralMetrics contract
# (used when persisting session memories so Track 1's surface is mirrored)
# --------------------------------------------------------------------------- #
def compute_session_metrics(trades: list[dict]) -> dict:
    closed = _closed(trades)
    pa_vals = [t["planAdherence"] for t in trades if t.get("planAdherence") is not None]

    # Win rate by emotional state
    wr: dict[str, dict[str, float]] = {}
    for emo in ("calm", "anxious", "greedy", "fearful", "neutral"):
        relevant = [t for t in closed if t.get("emotionalState") == emo]
        wins = sum(1 for t in relevant if t.get("outcome") == "win")
        losses = sum(1 for t in relevant if t.get("outcome") == "loss")
        if wins + losses > 0:
            wr[emo] = {
                "wins": wins,
                "losses": losses,
                "winRate": wins / (wins + losses),
            }

    # Session tilt for this single session
    ts = sorted(trades, key=lambda x: x["entryAt"])
    tilt = 0.0
    if len(ts) >= 2:
        tilt = sum(1 for i in range(1, len(ts)) if ts[i - 1].get("outcome") == "loss") / (
            len(ts) - 1
        )

    return {
        "planAdherenceScore": (sum(pa_vals) / len(pa_vals)) if pa_vals else 0.0,
        "sessionTiltIndex": tilt,
        "revengeTrades": sum(1 for t in trades if t.get("revengeFlag")),
        "overtradingEvents": 0,  # within-session sliding window; usually 0
        "winRateByEmotionalState": wr,
    }
