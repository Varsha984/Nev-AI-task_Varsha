"""Coach tests.

Most-important assertion: the coach NEVER emits a sessionId or tradeId that
isn't on its allow-list. The allow-list comes from the inbound trades and the
deterministic detector evidence — anything else is by definition a
hallucination.
"""
from __future__ import annotations

from app.coach import _enforce_allowlist, _tokenize_for_stream, detect_signals
from app.schemas import SessionEventTrade


REAL_SID = "4f39c2ea-8687-41f7-85a0-1fafd3e976df"
REAL_TID = "9c967550-357f-4bfb-9726-c8b863e968ce"
FAKE_SID = "deadbeef-dead-beef-dead-beefdeadbeef"


def test_enforce_allowlist_keeps_allowed_uuids():
    text = f"Trade {REAL_TID} in session {REAL_SID} shows the pattern."
    cleaned = _enforce_allowlist(text, {REAL_SID}, {REAL_TID})
    assert REAL_TID in cleaned
    assert REAL_SID in cleaned


def test_enforce_allowlist_redacts_unknown_uuids():
    text = f"Trade {REAL_TID} ok, session {FAKE_SID} not ok."
    cleaned = _enforce_allowlist(text, {REAL_SID}, {REAL_TID})
    assert REAL_TID in cleaned
    assert FAKE_SID not in cleaned
    assert "[REDACTED]" in cleaned


def test_enforce_allowlist_case_insensitive():
    text = f"See {REAL_SID.upper()}"
    cleaned = _enforce_allowlist(text, {REAL_SID}, set())
    # Original case preserved, but allowlist match was case-insensitive.
    assert REAL_SID.upper() in cleaned
    assert "[REDACTED]" not in cleaned


def test_tokenize_for_stream_preserves_text():
    text = "Hello world this is a test."
    chunks = _tokenize_for_stream(text)
    assert "".join(chunks) == text
    assert len(chunks) > 1  # should split on whitespace


def test_detect_signals_fires_on_revenge():
    """Build a trade stream that screams revenge — verify the detector picks it up."""
    trades = [
        SessionEventTrade(
            tradeId=f"00000000-0000-0000-0000-{i:012d}",
            userId="user-1",
            sessionId="sess-1",
            asset="AAPL",
            assetClass="equity",
            direction="long",
            entryPrice=100.0,
            exitPrice=95.0,
            quantity=10,
            entryAt=f"2025-01-06T09:{30+i:02d}:00Z",
            exitAt=f"2025-01-06T09:{31+i:02d}:00Z",
            status="closed",
            outcome="loss",
            pnl=-50.0,
            planAdherence=2,
            emotionalState="anxious",
            entryRationale="recovery trade",
            revengeFlag=True,
        )
        for i in range(5)
    ]
    fired = detect_signals(trades)
    pathologies = [f.pathology for f in fired]
    assert "revenge_trading" in pathologies


def test_detect_signals_empty_input():
    assert detect_signals([]) == []
