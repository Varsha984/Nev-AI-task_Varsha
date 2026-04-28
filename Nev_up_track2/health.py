"""GET /health — public, no auth. Returns DB state and a static queue lag.

Track 2 doesn't run a real message queue (the spec doesn't require one for
this track), so `queueLag` is always 0. Including the field keeps the
response shape compatible with the deck's HealthResponse schema.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.db import db_healthy

router = APIRouter(tags=["System"])


@router.get("/health")
async def health() -> dict:
    db_ok = await db_healthy()
    return {
        "status": "ok" if db_ok else "degraded",
        "dbConnection": "connected" if db_ok else "disconnected",
        "queueLag": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
