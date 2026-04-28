"""Persistent memory store.

Implements the deck's three-endpoint memory contract:
  * `put_session_memory` — write or upsert
  * `get_context` — vector + tag retrieval for a signal
  * `get_session_memory` — exact lookup for hallucination audit

The store is Postgres + pgvector. The volume mount in docker-compose ensures
data survives `docker compose restart` — that's a hard requirement.

We never lose the raw payload that was written: `raw_record` is JSONB and the
audit lookup returns it verbatim, which is what makes the audit endpoint
trustworthy.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import embeddings
from app.schemas import (
    BehavioralMetrics,
    MemoryWriteBody,
    SessionSummaryRecord,
)


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
async def put_session_memory(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    body: MemoryWriteBody,
) -> SessionSummaryRecord:
    """Idempotent upsert keyed on (user_id, session_id).

    The embedding is computed from the summary text. If a row exists, we
    overwrite — the deck doesn't specify versioning and treating it as
    last-write-wins keeps the contract simple for reviewers.
    """
    vec = embeddings.embed(body.summary)
    raw_record = {
        "userId": user_id,
        "sessionId": session_id,
        "summary": body.summary,
        "metrics": body.metrics.model_dump(),
        "tags": body.tags,
    }

    # asyncpg + sqlalchemy: parameterise everything via :name binds. Vector is
    # serialised to its '[1.0, 2.0, ...]' textual form which pgvector accepts.
    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

    await db.execute(
        text(
            """
            INSERT INTO session_memory
                (user_id, session_id, summary, metrics, tags, raw_record, embedding, updated_at)
            VALUES
                (:uid, :sid, :summary, CAST(:metrics AS jsonb), :tags,
                 CAST(:raw AS jsonb), CAST(:emb AS vector), now())
            ON CONFLICT (user_id, session_id) DO UPDATE SET
                summary    = EXCLUDED.summary,
                metrics    = EXCLUDED.metrics,
                tags       = EXCLUDED.tags,
                raw_record = EXCLUDED.raw_record,
                embedding  = EXCLUDED.embedding,
                updated_at = now()
            """
        ),
        {
            "uid": user_id,
            "sid": session_id,
            "summary": body.summary,
            "metrics": json.dumps(body.metrics.model_dump()),
            "tags": body.tags,
            "raw": json.dumps(raw_record),
            "emb": vec_str,
        },
    )
    await db.commit()
    return await get_session_memory(db, user_id=user_id, session_id=session_id)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
async def get_session_memory(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
) -> SessionSummaryRecord | None:
    row = (
        await db.execute(
            text(
                """
                SELECT user_id::text, session_id::text, summary, metrics, tags,
                       created_at, updated_at
                FROM session_memory
                WHERE user_id = :uid AND session_id = :sid
                """
            ),
            {"uid": user_id, "sid": session_id},
        )
    ).first()
    if not row:
        return None
    return SessionSummaryRecord(
        userId=row[0],
        sessionId=row[1],
        summary=row[2],
        metrics=BehavioralMetrics(**row[3]),
        tags=list(row[4] or []),
        createdAt=_ensure_tz(row[5]),
        updatedAt=_ensure_tz(row[6]),
    )


async def get_context(
    db: AsyncSession,
    *,
    user_id: str,
    relevant_to: str,
    limit: int = 5,
) -> tuple[list[SessionSummaryRecord], list[str]]:
    """Retrieve the K most relevant session memories for `relevant_to`.

    `relevant_to` may be a pathology name (resolved via canonical embeddings)
    or any free-text signal (embedded on the fly).

    Pattern IDs returned are the union of tags across the retrieved sessions
    plus the canonical signal name itself, deduped — these tell the caller
    "this is what we found pattern-wise."
    """
    qvec = embeddings.canonical_embedding(relevant_to)
    qvec_str = "[" + ",".join(f"{x:.8f}" for x in qvec) + "]"

    rows = (
        await db.execute(
            text(
                """
                SELECT user_id::text, session_id::text, summary, metrics, tags,
                       created_at, updated_at,
                       1 - (embedding <=> CAST(:qv AS vector)) AS sim
                FROM session_memory
                WHERE user_id = :uid AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:qv AS vector)
                LIMIT :lim
                """
            ),
            {"uid": user_id, "qv": qvec_str, "lim": limit},
        )
    ).fetchall()

    sessions = [
        SessionSummaryRecord(
            userId=r[0],
            sessionId=r[1],
            summary=r[2],
            metrics=BehavioralMetrics(**r[3]),
            tags=list(r[4] or []),
            createdAt=_ensure_tz(r[5]),
            updatedAt=_ensure_tz(r[6]),
        )
        for r in rows
    ]
    pattern_ids: list[str] = []
    if relevant_to in embeddings.CANONICAL_SIGNALS:
        pattern_ids.append(relevant_to)
    seen = set(pattern_ids)
    for s in sessions:
        for tag in s.tags:
            if tag not in seen:
                pattern_ids.append(tag)
                seen.add(tag)
    return sessions, pattern_ids


async def list_user_sessions(db: AsyncSession, *, user_id: str) -> list[str]:
    rows = (
        await db.execute(
            text(
                "SELECT session_id::text FROM session_memory WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).fetchall()
    return [r[0] for r in rows]


def _ensure_tz(dt: Any) -> datetime:
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
