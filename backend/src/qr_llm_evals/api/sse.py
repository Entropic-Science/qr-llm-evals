"""Server-sent events for ``GET /runs/{id}/events`` (spec §4.8).

The stream is a plain ``text/event-stream`` fed from one
:class:`~qr_llm_evals.runner.events.Subscription` (a bounded, drop-oldest
queue on the run's bus). Three things happen here and nowhere else:

* **Keep-alive.** ``: keep-alive`` is written every :data:`KEEPALIVE_S`
  seconds of silence so cloudflared and browsers keep the connection open.
* **Coalescing.** The orchestrator already limits ``sample.delta`` to 20 Hz per
  stream; when a burst is queued (a slow tab catching up) consecutive deltas
  for the same ``sample_ref`` are merged into one frame so the client never
  receives more than :data:`MAX_DELTA_HZ` per stream.
* **Resync.** After drops the subscription yields a ``resync`` event; it is
  forwarded untouched so the client refetches ``GET /runs/{id}``.

The generator ends when the bus closes (terminal run state) or the client
disconnects; either way the subscription is removed from the bus.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from qr_llm_evals.runner.events import RunEvent, RunEventBus, Subscription

KEEPALIVE_S = 10.0
MAX_DELTA_HZ = 20
POLL_S = 0.05
KEEPALIVE_FRAME = ": keep-alive\n\n"
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def merge_deltas(events: list[RunEvent]) -> list[RunEvent]:
    """Merge runs of consecutive ``sample.delta`` events that share a ``sample_ref``."""
    out: list[RunEvent] = []
    for ev in events:
        prev = out[-1] if out else None
        if (
            prev is not None
            and ev.type == "sample.delta"
            and prev.type == "sample.delta"
            and ev.data.get("sample_ref") == prev.data.get("sample_ref")
        ):
            merged = dict(prev.data)
            for key in ("text", "thinking"):
                if key in ev.data:
                    merged[key] = str(merged.get(key, "")) + str(ev.data[key])
            out[-1] = RunEvent(prev.type, merged, ev.seq)
        else:
            out.append(ev)
    return out


async def event_frames(
    sub: Subscription,
    *,
    keepalive_s: float = KEEPALIVE_S,
    poll_s: float = POLL_S,
) -> AsyncIterator[str]:
    """Yield SSE frames from ``sub`` until the bus closes."""
    loop = asyncio.get_running_loop()
    last_write = loop.time()
    delta_gap = 1.0 / MAX_DELTA_HZ
    last_delta: dict[str, float] = {}
    while True:
        batch = merge_deltas(sub.drain())
        if batch:
            for ev in batch:
                if ev.type == "sample.delta":
                    ref = str(ev.data.get("sample_ref", ""))
                    now = loop.time()
                    wait = delta_gap - (now - last_delta.get(ref, 0.0))
                    if wait > 0:
                        await asyncio.sleep(wait)
                    last_delta[ref] = loop.time()
                yield ev.to_sse()
            last_write = loop.time()
            continue
        if sub.closed:
            return
        if loop.time() - last_write >= keepalive_s:
            yield KEEPALIVE_FRAME
            last_write = loop.time()
        await asyncio.sleep(poll_s)


async def stream_bus(
    bus: RunEventBus, *, keepalive_s: float = KEEPALIVE_S, poll_s: float = POLL_S
) -> AsyncIterator[str]:
    sub = bus.subscribe()
    try:
        async for frame in event_frames(sub, keepalive_s=keepalive_s, poll_s=poll_s):
            yield frame
    finally:
        bus.unsubscribe(sub)


def sse_response(
    bus: RunEventBus, *, keepalive_s: float = KEEPALIVE_S, poll_s: float = POLL_S
) -> StreamingResponse:
    return StreamingResponse(
        stream_bus(bus, keepalive_s=keepalive_s, poll_s=poll_s),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


__all__ = [
    "KEEPALIVE_FRAME",
    "KEEPALIVE_S",
    "MAX_DELTA_HZ",
    "SSE_HEADERS",
    "event_frames",
    "merge_deltas",
    "sse_response",
    "stream_bus",
]
