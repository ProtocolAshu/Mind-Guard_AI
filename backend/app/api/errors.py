"""Uniform error envelope: {"error": {"code", "message", "details", "request_id"}}.
Validation errors never echo submitted values (they may contain secrets or content)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agents.learning_agent import LearningError
from app.core.errors import (
    ConflictError,
    ConsentRequiredError,
    ForbiddenError,
    MindGuardError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationFailedError,
)
from app.core.logging import request_id_var

log = logging.getLogger(__name__)
STATUS: dict[type[MindGuardError], int] = {
    NotFoundError: 404, ForbiddenError: 403, UnauthorizedError: 401, ConflictError: 409, ValidationFailedError: 422,
    RateLimitedError: 429, ConsentRequiredError: 403, ServiceUnavailableError: 503,
}


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MindGuardError)
    async def mindguard_error(_: Request, exc: MindGuardError) -> JSONResponse:
        status = next((code for cls, code in STATUS.items() if isinstance(exc, cls)), 400)
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None
        if isinstance(exc, UnauthorizedError):
            headers = {"WWW-Authenticate": "Bearer"}
        default_code = type(exc).__name__.replace("Error", "").lower() or "error"
        return JSONResponse(_envelope(exc.code or default_code, exc.message, exc.details), status_code=status, headers=headers)

    @app.exception_handler(LearningError)
    async def learning_error(_: Request, exc: LearningError) -> JSONResponse:
        status = 409 if "already recorded" in exc.message else 404 if exc.status == "denied" else 422
        return JSONResponse(_envelope("feedback_rejected", exc.message), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": [str(p) for p in e.get("loc", ())], "type": e.get("type"), "msg": e.get("msg")} for e in exc.errors()[:20]]
        return JSONResponse(_envelope("validation_error", "request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(_envelope(f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(_envelope("internal_error", "internal server error"), status_code=500)
