"""The HTTP/SSE API (spec §4.7, §4.8): FastAPI app, routes, auth dependencies, error envelope.

Top layer: may import every other subpackage; nothing imports ``api``.
"""

from __future__ import annotations

from qr_llm_evals.api.app import API_PREFIX, create_app
from qr_llm_evals.api.deps import AppState
from qr_llm_evals.api.errors import ApiError, error_response

__all__ = ["API_PREFIX", "ApiError", "AppState", "create_app", "error_response"]
