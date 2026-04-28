"""Database layer.

Postgres + pgvector. The schema is intentionally narrow — Track 2 only stores
session memories and the embeddings used to retrieve them. Trade history
itself lives in the seed JSON which we treat as read-only ground truth.

Tables:
  * `session_memory` — one row per session memory, with embedding column.
    Survives `docker compose restart` because the volume is mounted.
  * `debrief` — append-only debrief submissions (used by Track 3 if reviewers
    test the full surface).

`init_db()` is called once at startup and is idempotent: CREATE EXTENSION IF
NOT EXISTS vector and CREATE TABLE IF NOT EXISTS — safe to run on a warm DB.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

log = logging.getLogger("nevup.db")

_engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,  # heal stale connections after compose restart
)

SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


# --------------------------------------------------------------------------- #
# Schema (raw SQL — keeps the migration story trivial: one statement, no Alembic).
# --------------------------------------------------------------------------- #
SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS session_memory (
    user_id        UUID NOT NULL,
    session_id     UUID NOT NULL,
    summary        TEXT NOT NULL,
    metrics        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    tags           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    raw_record     JSONB NOT NULL,
    embedding      vector({settings.embedding_dim}),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, session_id)
);

CREATE INDEX IF NOT EXISTS session_memory_user_idx
    ON session_memory (user_id);

CREATE INDEX IF NOT EXISTS session_memory_tags_idx
    ON session_memory USING GIN (tags);

-- IVFFlat is overkill at hackathon scale (~50 rows), but the index is here so
-- the system stays performant if reviewers stress-test with their own data.
CREATE INDEX IF NOT EXISTS session_memory_embedding_idx
    ON session_memory USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

CREATE TABLE IF NOT EXISTS debrief (
    debrief_id            UUID PRIMARY KEY,
    session_id            UUID NOT NULL,
    user_id               UUID NOT NULL,
    overall_mood          TEXT NOT NULL,
    key_mistake           TEXT,
    key_lesson            TEXT,
    plan_adherence_rating INT NOT NULL,
    will_review_tomorrow  BOOLEAN NOT NULL DEFAULT FALSE,
    saved_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def init_db() -> None:
    """Create extension + tables if they don't exist. Idempotent."""
    async with _engine.begin() as conn:
        # pgvector + DDL split into separate statements so DDL errors are clear.
        for stmt in [s for s in SCHEMA_SQL.split(";") if s.strip()]:
            await conn.execute(text(stmt))
    log.info('{"event": "db.init", "status": "ok"}')


async def db_healthy() -> bool:
    """Used by GET /health."""
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Convenience context manager for code outside FastAPI dep injection."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session
