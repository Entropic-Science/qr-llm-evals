"""The error envelope (spec §4.7): ``{"error": {"message", "type", "param", "code"}}``.

Every error the backend returns — a refused launch key, a quota ``429``, a
malformed body, an unknown route, an unexpected exception — is rendered in
the same OpenAI-style envelope qr-llm-api uses, so the SPA has one parser for
both services. Status-specific headers (``Retry-After``, ``WWW-Authenticate``)
ride on the :class:`ApiError`.

The catch-all handler never echoes ``str(exc)``: an upstream client error can
carry a header or URL with a BYO key in it. It logs the traceback (through the
root ``SecretFilter``) and answers with a fixed message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from qr_llm_evals.store.bundle import FrozenStudyUnreadableError

log = logging.getLogger("qr_llm_evals.api")

#: ``type`` values on the wire (OpenAI vocabulary plus the harness's own).
INVALID_REQUEST = "invalid_request_error"
AUTHENTICATION = "authentication_error"
PERMISSION = "permission_error"
NOT_FOUND = "not_found_error"
CONFLICT = "conflict_error"
RATE_LIMIT = "rate_limit_error"
UPSTREAM = "upstream_error"
SERVER = "server_error"

_STATUS_TYPES: dict[int, str] = {
    400: INVALID_REQUEST,
    401: AUTHENTICATION,
    403: PERMISSION,
    404: NOT_FOUND,
    405: INVALID_REQUEST,
    409: CONFLICT,
    422: INVALID_REQUEST,
    429: RATE_LIMIT,
    502: UPSTREAM,
    503: UPSTREAM,
}


def envelope(
    status: int, message: str, *, type_: str | None = None, param: str | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": type_ or _STATUS_TYPES.get(status, SERVER),
            "param": param,
            "code": status,
        }
    }


def error_response(
    status: int,
    message: str,
    *,
    type_: str | None = None,
    param: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        envelope(status, message, type_=type_, param=param),
        status_code=status,
        headers=dict(headers or {}),
    )


class ApiError(Exception):
    """Raise anywhere inside a route/dependency; rendered by :func:`install_handlers`."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        type_: str | None = None,
        param: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type_ = type_ or _STATUS_TYPES.get(status, SERVER)
        self.param = param
        self.headers = dict(headers or {})

    def response(self) -> JSONResponse:
        return error_response(
            self.status, self.message, type_=self.type_, param=self.param, headers=self.headers
        )


def _validation_param(exc: RequestValidationError) -> str | None:
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p != "body"]
        if loc:
            return ".".join(loc)
    return None


def _validation_message(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "body"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "invalid request: " + "; ".join(parts) if parts else "invalid request"


def install_handlers(app: FastAPI) -> None:
    """Route every exception class to the envelope."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return error_response(exc.status_code, detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 400, not FastAPI's 422: the SPA and curl users expect the OpenAI status.
        return error_response(400, _validation_message(exc), param=_validation_param(exc))

    @app.exception_handler(FrozenStudyUnreadableError)
    async def _unreadable_study(request: Request, exc: FrozenStudyUnreadableError) -> JSONResponse:
        # The message is built from field paths and rule texts of the study config, no values.
        return error_response(409, str(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled error",
            extra={"event": "api.error", "path": request.url.path, "kind": type(exc).__name__},
            exc_info=exc,
        )
        return error_response(500, "internal error; see the server log")


__all__ = [
    "AUTHENTICATION",
    "CONFLICT",
    "INVALID_REQUEST",
    "NOT_FOUND",
    "PERMISSION",
    "RATE_LIMIT",
    "SERVER",
    "UPSTREAM",
    "ApiError",
    "envelope",
    "error_response",
    "install_handlers",
]
