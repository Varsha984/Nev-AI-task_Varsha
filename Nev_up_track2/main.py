"""FastAPI application factory.

Wires everything together:
  * Tracing middleware (assigns traceId, logs structured JSON per request)
  * DB schema bootstrap on startup (idempotent — safe across restarts)
  * Seed data load + embedding model warm-up
  * Routers for all five surfaces: memory, session events, profile, audit, health
  * Uniform error envelope so 401/403/404/500 always carry traceId
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import embeddings
from app.db import init_db
from app.logging_mw import TracingMiddleware
from app.routers import audit, health, memory, profile, session_events
from app.seed import seed_store

log = logging.getLogger("nevup.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: schema, seed, embeddings. Shutdown: nothing — connections drain."""
    log.info('{"event": "app.startup.begin"}')
    await init_db()
    seed_store.load()
    # Warm the embedding model so the first /memory or /session/events request
    # doesn't pay the ~3s model-load cost. Failure here is non-fatal — the
    # model will load on first real use, just slowly.
    try:
        embeddings.warmup()
    except Exception as exc:  # noqa: BLE001
        log.warning(f'{{"event": "app.startup.embed_warmup_failed", "error": "{exc}"}}')
    log.info('{"event": "app.startup.done"}')
    yield
    log.info('{"event": "app.shutdown"}')


def create_app() -> FastAPI:
    app = FastAPI(
        title="NevUp Track 2 — System of AI Engine",
        description=(
            "Stateful trading-psychology coach with a verifiable memory layer. "
            "Every coaching claim cites concrete sessionIds and tradeIds; "
            "POST /audit will tell you if any of them are hallucinated."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(TracingMiddleware)

    # Uniform error envelope. The deck mandates {error, message, traceId} on
    # every error response — these handlers normalise both HTTPException
    # (already shaped by our auth helpers) and validation errors (FastAPI's
    # default 422) into the same shape.
    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _err_code_for(exc.status_code),
                "message": str(exc.detail) if exc.detail else "",
                "traceId": getattr(request.state, "trace_id", ""),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": "BAD_REQUEST",
                "message": "Request body or parameters failed validation.",
                "details": exc.errors(),
                "traceId": getattr(request.state, "trace_id", ""),
            },
        )

    app.include_router(memory.router)
    app.include_router(session_events.router)
    app.include_router(profile.router)
    app.include_router(audit.router)
    app.include_router(health.router)

    return app


def _err_code_for(status: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "UNPROCESSABLE_ENTITY",
    }.get(status, "INTERNAL_ERROR")


app = create_app()
