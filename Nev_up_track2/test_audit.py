"""Audit tests.

Hallucination audit must:
  * accept explicit citations and audit them
  * extract UUIDs from free-text and audit those too
  * flag a session that doesn't belong to the user as `found=False`
  * flag a fake UUID as `found=False`
  * flag a real session belonging to the right user as `found=True`
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.audit import _extract_uuids, audit_response
from app.schemas import AuditBody
from app.seed import seed_store

ROOT = Path(__file__).resolve().parent.parent
seed_store.load()  # ensure singleton populated for tests

SEED = json.loads((ROOT / "seed_data" / "nevup_seed_dataset.json").read_text())
ALEX = next(t for t in SEED["traders"] if t["name"] == "Alex Mercer")
JORDAN = next(t for t in SEED["traders"] if t["name"] == "Jordan Lee")


def _mock_db_with_sessions(sessions: list[str]):
    """Return a mock AsyncSession that pretends `list_user_sessions` returns these."""
    db = AsyncMock()
    return db


def test_extract_uuids_finds_them_in_freeform_text():
    sid = ALEX["sessions"][0]["sessionId"]
    text = f"You traded {sid} poorly. Also see another sid: {sid.upper()}"
    extracted = _extract_uuids(text)
    assert sid in extracted
    # Case-folded to lower so duplicate-case versions dedupe.
    assert len(extracted) == 1


def test_extract_uuids_handles_no_uuids():
    assert _extract_uuids("No uuids in here.") == []


@pytest.mark.asyncio
async def test_audit_real_session_explicit_citation(monkeypatch):
    """Citation pointing to a session the user owns → found=True."""
    real_sid = ALEX["sessions"][0]["sessionId"]
    body = AuditBody(
        userId=ALEX["userId"],
        message="Coaching message body.",
        citations=[{"sessionId": real_sid, "tradeId": "abc"}],
    )

    async def fake_list(_, *, user_id):
        return []

    monkeypatch.setattr("app.audit.list_user_sessions", fake_list)
    res = await audit_response(AsyncMock(), body)

    assert len(res.citations) == 1
    assert res.citations[0].found is True
    assert res.citations[0].source == "explicit"
    assert res.hallucinated is False


@pytest.mark.asyncio
async def test_audit_cross_tenant_session_flagged(monkeypatch):
    """Citation pointing to *another user's* session → found=False (not owned)."""
    other_sid = JORDAN["sessions"][0]["sessionId"]
    body = AuditBody(
        userId=ALEX["userId"],
        message=f"Look at session {other_sid}",
        citations=[],
    )

    async def fake_list(_, *, user_id):
        return []

    monkeypatch.setattr("app.audit.list_user_sessions", fake_list)
    res = await audit_response(AsyncMock(), body)

    assert any(c.sessionId == other_sid.lower() and c.found is False for c in res.citations)
    assert res.hallucinated is True


@pytest.mark.asyncio
async def test_audit_fake_uuid_flagged(monkeypatch):
    """A made-up UUID → found=False, hallucinated=True."""
    fake = "deadbeef-dead-beef-dead-beefdeadbeef"
    body = AuditBody(
        userId=ALEX["userId"],
        message=f"You did this in session {fake}.",
        citations=[],
    )

    async def fake_list(_, *, user_id):
        return []

    monkeypatch.setattr("app.audit.list_user_sessions", fake_list)
    res = await audit_response(AsyncMock(), body)

    assert len(res.citations) == 1
    assert res.citations[0].found is False
    assert res.citations[0].source == "extracted"
    assert res.hallucinated is True


@pytest.mark.asyncio
async def test_audit_persisted_memory_session_passes(monkeypatch):
    """Session from the persistent memory store (not seed) → found=True."""
    persisted_sid = "11111111-2222-3333-4444-555555555555"
    body = AuditBody(
        userId=ALEX["userId"],
        message=f"See {persisted_sid}",
        citations=[],
    )

    async def fake_list(_, *, user_id):
        return [persisted_sid]

    monkeypatch.setattr("app.audit.list_user_sessions", fake_list)
    res = await audit_response(AsyncMock(), body)

    assert res.citations[0].found is True
    assert res.hallucinated is False
