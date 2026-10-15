"""``GET /runs/{id}/events`` — the SSE stream (spec §4.8).

Open to viewers (no auth): the stream carries prompts, outputs and provenance,
never keys. A run that is not live in this process (finished, or orphaned by a
restart) is adopted so the client still gets a ``run.state`` header frame and
keep-alives, then the stream ends when the bus closes.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from qr_llm_evals.api.deps import State
from qr_llm_evals.api.registry import TERMINAL_STATES
from qr_llm_evals.api.sse import sse_response
from qr_llm_evals.runner.events import RunEvent

router = APIRouter(tags=["events"])


@router.get("/runs/{run_id}/events", response_class=StreamingResponse)
async def run_events(run_id: str, state: State) -> StreamingResponse:
    live = state.live_run(run_id)
    run = live.read()
    if not live.thread_alive:
        # No orchestrator is feeding the bus: give the subscriber the current
        # state as the header frame (a live bus replays its last run.state itself).
        live.bus.emit(
            RunEvent(
                "run.state",
                {"run_id": run.run_id, "state": run.state, "at": state.clock().isoformat()},
            )
        )
        if run.state in TERMINAL_STATES:
            live.bus.close()
    return sse_response(live.bus)


__all__ = ["router"]
