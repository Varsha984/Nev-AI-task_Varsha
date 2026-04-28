"""Hallucination audit.

`POST /audit` accepts a coaching response and returns, for every sessionId
referenced, whether that session actually exists in either:
  * the seed dataset (read-only ground truth), or
  * the persistent memory store (sessions written via PUT /memory/...)

Two parsing modes:
  * If the caller passed `citations` explicitly (recommended — the coach does
    this), we audit those.
  * Otherwise we extract anything that looks like a UUID from `message` and
    audit each. Belt-and-braces — reviewers can call this on any string.

Output is structured: one entry per session id with a `found` flag, plus an
overall `hallucinated` boolean for quick programmatic checks.
"""
from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.memory import list_user_sessions
from app.schemas import AuditBody, AuditCitation, AuditResponse
from app.seed import seed_store

UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _extract_uuids(text: str) -> list[str]:
    seen = []
    seen_set = set()
    for m in UUID_RE.findall(text or ""):
        m = m.lower()
        if m not in seen_set:
            seen_set.add(m)
            seen.append(m)
    return seen


async def audit_response(db: AsyncSession, body: AuditBody) -> AuditResponse:
    explicit_ids: list[str] = []
    for c in body.citations:
        sid = c.get("sessionId") if isinstance(c, dict) else None
        if sid:
            explicit_ids.append(sid.lower())
    extracted_ids = _extract_uuids(body.message)

    # Memory-stored sessions for this user — these count as "real" sessions
    # because we wrote them ourselves with verifiable provenance.
    persisted = {sid.lower() for sid in await list_user_sessions(db, user_id=body.userId)}

    citations: list[AuditCitation] = []
    seen: set[str] = set()

    for sid in explicit_ids:
        if sid in seen:
            continue
        seen.add(sid)
        citations.append(
            AuditCitation(
                sessionId=sid,
                found=_is_real(sid, body.userId, persisted),
                source="explicit",
            )
        )

    for sid in extracted_ids:
        if sid in seen:
            continue
        seen.add(sid)
        citations.append(
            AuditCitation(
                sessionId=sid,
                found=_is_real(sid, body.userId, persisted),
                source="extracted",
            )
        )

    hallucinated = any(not c.found for c in citations)
    return AuditResponse(
        userId=body.userId,
        citations=citations,
        hallucinated=hallucinated,
    )


def _is_real(session_id: str, user_id: str, persisted: Iterable[str]) -> bool:
    """A citation is 'real' iff the session belongs to this user, in either store."""
    if session_id in persisted:
        return True
    seed_session = seed_store.session(session_id)
    return bool(seed_session and seed_session.get("userId", "").lower() == user_id.lower())
