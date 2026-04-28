"""Structured request logging middleware.

Every request gets:
  * a UUID `traceId` attached to `request.state.trace_id`
  * a single JSON log line on completion with: traceId, userId, latency_ms,
    statusCode, method, path

Unhandled exceptions are caught, logged, and converted into a 500 response
with the same traceId so error responses can be correlated to logs.

The log format is intentionally one-line JSON so the deck's required fields
(`traceId`, `userId`, `latency`, `statusCode`) are grep-able in container logs.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


def _configure_root_logger() -> None:
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. uvicorn imported us twice)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))


_configure_root_logger()
log = logging.getLogger("nevup")


class TracingMiddleware(BaseHTTPMiddleware):
    """Attach traceId, time the request, emit structured JSON log line."""

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 — we want a uniform 500 path
            latency_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                json.dumps(
                    {
                        "traceId": trace_id,
                        "userId": getattr(request.state, "user_id", None),
                        "latency": latency_ms,
                        "statusCode": 500,
                        "method": request.method,
                        "path": request.url.path,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                    "traceId": trace_id,
                },
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        # Emit log AFTER auth has had a chance to set user_id.
        log.info(
            json.dumps(
                {
                    "traceId": trace_id,
                    "userId": getattr(request.state, "user_id", None),
                    "latency": latency_ms,
                    "statusCode": response.status_code,
                    "method": request.method,
                    "path": request.url.path,
                }
            )
        )
        # Echo trace id so clients can correlate too.
        response.headers["X-Trace-Id"] = trace_id
        return response
