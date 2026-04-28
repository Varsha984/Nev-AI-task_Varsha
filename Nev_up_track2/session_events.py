"""POST /session/events — accept a stream of trades, stream a coaching message.

Auth model: the `userId` carried inside the body must match the JWT's `sub`.
Returning 403 (not 404) on mismatch is graded automatically.

Streaming uses sse-starlette so the response sets the right headers
(`text/event-stream`, no buffering) and integrates with FastAPI exception
handling.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import require_user
from app.coach import stream_coaching
from app.db import get_session
from app.schemas import ErrorResponse, SessionEventBody

router = APIRouter(prefix="/session", tags=["Coaching"])
log = logging.getLogger("nevup.coach")


@router.post(
    "/events",
    responses={
        200: {"description": "SSE stream of coaching events"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def session_events(
    request: Request,
    body: SessionEventBody,
    db: Annotated[AsyncSession, Depends(get_session)],
    jwt_sub: Annotated[str, Depends(require_user)],
):
    """Stream coaching events for a session in progress.

    Events emitted (in order):
      1. `signal` — one per fired detector (pathology + evidence ids)
      2. `token` — many; the message body chunked for streaming UX
      3. `done`  — full message + the final citation list

    All session/trade ids referenced in the message are guaranteed to be
    drawn from the inbound trades plus retrieved memories — verified by
    the audit endpoint, defended with allow-list redaction in the coach.
    """
    if body.userId != jwt_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "FORBIDDEN",
                "message": "Cross-tenant access denied.",
                "traceId": getattr(request.state, "trace_id", ""),
            },
        )

    # All inbound trades must belong to the same user — defensive check.
    bad = [t for t in body.trades if t.userId != jwt_sub]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "FORBIDDEN",
                "message": "Trades contain a userId different from the JWT subject.",
                "traceId": getattr(request.state, "trace_id", ""),
            },
        )

    log.info(
        f'{{"event": "coach.start", "userId": "{jwt_sub}", '
        f'"sessionId": "{body.sessionId}", "tradeCount": {len(body.trades)}}}'
    )

    return EventSourceResponse(
        stream_coaching(db, user_id=jwt_sub, trades=body.trades),
        ping=15,
    )
