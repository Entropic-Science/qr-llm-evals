"""``GET /health`` — liveness, engine reachability, version/commit (spec §4.7)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter

from qr_llm_evals.api.deps import State
from qr_llm_evals.api.schema import EngineStatus, HealthResponse

router = APIRouter(tags=["health"])

ENGINE_HEALTH_PATH = "/health"
ENGINE_TIMEOUT_S = 2.0


async def probe_engine(
    origin: str, *, transport: httpx.AsyncBaseTransport | None, checked_at: str
) -> EngineStatus:
    url = origin.rstrip("/") + ENGINE_HEALTH_PATH
    try:
        async with httpx.AsyncClient(timeout=ENGINE_TIMEOUT_S, transport=transport) as client:
            resp = await client.get(url)
    except httpx.HTTPError as err:
        return EngineStatus(
            origin=origin, reachable=False, detail=type(err).__name__, checked_at=checked_at
        )
    ok = resp.status_code < 400
    return EngineStatus(
        origin=origin,
        reachable=ok,
        detail=None if ok else f"HTTP {resp.status_code}",
        checked_at=checked_at,
    )


@router.get("/health", response_model=HealthResponse)
async def health(state: State) -> HealthResponse:
    checked_at = state.clock().isoformat()
    engine = await probe_engine(
        state.settings.vllm_origin, transport=state.engine_transport, checked_at=checked_at
    )
    return HealthResponse(
        status="ok" if engine.reachable else "degraded",
        version=state.version,
        commit=state.commit,
        key_verify=state.settings.key_verify,
        engine=engine,
        active_runs=len(state.registry),
    )


__all__ = ["router", "probe_engine"]
