"""Route modules; each exposes a ``router`` that ``api.app`` mounts under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from qr_llm_evals.api.routes import (
    admin,
    analysis,
    auth,
    cards,
    estimates,
    events,
    health,
    judge,
    models,
    prereg,
    readiness,
    runs,
    sessions,
    studies,
)

#: Mount order (also the order in the OpenAPI document).
ROUTERS: tuple[APIRouter, ...] = (
    health.router,
    auth.router,
    admin.router,
    cards.router,
    models.router,
    estimates.router,
    prereg.router,
    readiness.router,
    judge.router,
    runs.router,
    analysis.router,
    sessions.router,
    events.router,
    studies.router,
)

__all__ = ["ROUTERS"]
