"""Detector unit tests.

Two flavours:
  1. Property tests — invariants every scorer must hold (deterministic,
     range-bound, empty-input safe).
  2. End-to-end — the full eval harness on the seed dataset must produce
     10/10 classification. This is the headline grade.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.detectors import (
    CONTROL_THRESHOLD,
    PATHOLOGIES,
    SignalResult,
    predict_dominant,
    score_all,
    score_fomo_entries,
    score_loss_running,
    score_overtrading,
    score_plan_non_adherence,
    score_premature_exit,
    score_revenge_trading,
    score_session_tilt,
    score_time_of_day_bias,
    score_position_sizing,
)

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "seed_data" / "nevup_seed_dataset.json").read_text())


def _trader(name: str) -> dict:
    return next(t for t in SEED["traders"] if t["name"] == name)


# --------------------------------------------------------------------------- #
# Property tests
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "scorer",
    [
        score_revenge_trading,
        score_overtrading,
        score_fomo_entries,
        score_plan_non_adherence,
        score_premature_exit,
        score_loss_running,
        score_time_of_day_bias,
        score_position_sizing,
    ],
)
def test_scorer_handles_empty_trades(scorer):
    """Empty input → zero score, no crash, no fake evidence."""
    result = scorer([])
    assert isinstance(result, SignalResult)
    assert result.score == 0.0
    assert result.evidence_sessions == []
    assert result.evidence_trades == []


def test_session_tilt_handles_empty_sessions():
    result = score_session_tilt([])
    assert result.score == 0.0


def test_score_all_returns_all_pathologies():
    """Avery has no pathology, but every scorer must still emit a result."""
    avery = _trader("Avery Chen")
    scores = score_all(avery)
    assert set(scores.keys()) == set(PATHOLOGIES)
    for p, r in scores.items():
        assert 0.0 <= r.score <= 1.0, f"{p} score {r.score} out of range"


def test_predict_dominant_below_threshold_returns_control():
    avery = _trader("Avery Chen")
    scores = score_all(avery)
    assert max(s.score for s in scores.values()) < CONTROL_THRESHOLD
    assert predict_dominant(scores) == "control"


def test_evidence_ids_are_real():
    """Every cited tradeId in the evidence must come from the trader's data."""
    alex = _trader("Alex Mercer")
    real_trades = {t["tradeId"] for s in alex["sessions"] for t in s["trades"]}
    real_sessions = {s["sessionId"] for s in alex["sessions"]}

    scores = score_all(alex)
    for p, r in scores.items():
        for tid in r.evidence_trades:
            assert tid in real_trades, f"{p} cited fake tradeId {tid}"
        for sid in r.evidence_sessions:
            assert sid in real_sessions, f"{p} cited fake sessionId {sid}"


# --------------------------------------------------------------------------- #
# End-to-end classification (the headline grade)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Alex Mercer", "revenge_trading"),
        ("Jordan Lee", "overtrading"),
        ("Sam Rivera", "fomo_entries"),
        ("Casey Kim", "plan_non_adherence"),
        ("Morgan Bell", "premature_exit"),
        ("Taylor Grant", "loss_running"),
        ("Riley Stone", "session_tilt"),
        ("Drew Patel", "time_of_day_bias"),
        ("Quinn Torres", "position_sizing_inconsistency"),
        ("Avery Chen", "control"),
    ],
)
def test_classification_matches_ground_truth(name, expected):
    trader = _trader(name)
    pred = predict_dominant(score_all(trader))
    assert pred == expected, f"{name}: predicted {pred}, expected {expected}"


def test_full_dataset_accuracy_is_perfect():
    """Top-line: 10/10 on the seed dataset. Regressions here are blockers."""
    correct = 0
    for trader in SEED["traders"]:
        truth = (trader.get("groundTruthPathologies") or ["control"])[0]
        if predict_dominant(score_all(trader)) == truth:
            correct += 1
    assert correct == len(SEED["traders"]), f"only {correct}/{len(SEED['traders'])}"


def test_determinism():
    """Two runs on the same data produce byte-identical scores."""
    alex = _trader("Alex Mercer")
    a = score_all(alex)
    b = score_all(alex)
    for p in PATHOLOGIES:
        assert a[p].score == b[p].score
        assert a[p].evidence_trades == b[p].evidence_trades
