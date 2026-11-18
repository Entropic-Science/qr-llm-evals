"""``POST /readiness`` (key) — run the six-step readiness check (spec §2.7, §4.7).

The check talks to the shared engine and takes seconds to minutes (5× solo
replay, optional throughput probe), so it runs in a worker thread. With a
``run_id`` the report is persisted on that run (``readiness.json`` + the
summary fields of ``run.json``); an :class:`EquivalenceError` is still a
``409`` — the report is attached (and persisted) so the host can see why.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from qr_llm_evals.api.deps import AppState, LaunchAuth, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.schema import ReadinessRequest
from qr_llm_evals.readiness.check import (
    EquivalenceError,
    ReadinessConfig,
    ReadinessError,
    run_readiness,
)
from qr_llm_evals.store.schema import ReadinessReport
from qr_llm_evals.store.store import RunStore

router = APIRouter(tags=["readiness"])


def _run_store_for(state: AppState, run_id: str | None) -> RunStore | None:
    return state.run_store(run_id) if run_id else None


@router.post("/readiness", response_model=ReadinessReport)
async def post_readiness(body: ReadinessRequest, state: State, key: LaunchAuth) -> ReadinessReport:
    config = ReadinessConfig.from_settings(state.settings, throughput_probe=body.throughput_probe)
    run_store = _run_store_for(state, body.run_id)
    try:
        return await asyncio.to_thread(
            run_readiness,
            body.config,
            config,
            transport=state.readiness_transport,
            run_store=run_store,
            clock=state.clock,
        )
    except EquivalenceError as err:
        raise ApiError(409, str(err), type_="readiness_error", param="arms") from err
    except ReadinessError as err:
        raise ApiError(502, f"readiness check failed: {err}", type_="readiness_error") from err


__all__ = ["router"]
