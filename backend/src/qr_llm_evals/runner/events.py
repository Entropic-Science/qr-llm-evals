"""Run events and the fan-out bus (spec §2.1, §4.8).

The orchestrator emits :class:`RunEvent` objects onto an :class:`EventSink`.
Headless runs use :class:`NullEventSink`; the API attaches a :class:`RunEventBus`
whose subscribers (SSE handlers) each get a **bounded, drop-oldest queue**
(:data:`DEFAULT_QUEUE_SIZE` = 2 000) so a slow browser tab can never
backpressure generation. When a subscriber has lost events, the next thing it
reads is a ``resync`` event telling the client to refetch run state.

The sink is the *only* difference between a viewed and an unviewed run; the
``backend/tests/test_runner.py`` equivalence test pins byte-identical artifacts.

Threading: ``emit()`` is called from worker threads; consumers poll from the
API's asyncio loop. Queues are ``collections.deque(maxlen=N)`` under a lock
(append on a full deque evicts the head atomically), which keeps the producer
loop-agnostic (pattern: ``qr_llm_research.viz.events.RunEventBus``).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

DEFAULT_QUEUE_SIZE: Final[int] = 2000

#: Event names on the wire (spec §4.8). Supervised-mode and analysis events are
#: listed so every producer shares one vocabulary.
EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "run.state",
        "run.progress",
        "sample.started",
        "sample.delta",
        "sample.finished",
        "taint",
        "session.state",
        "session.timer",
        "observer.presence",
        "analysis.progress",
        "error",
        "resync",
    }
)


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One event: ``type`` from :data:`EVENT_TYPES`, a JSON-able ``data`` payload."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {self.type!r}")

    def to_sse(self) -> str:
        """``event:``/``id:``/``data:`` frame (data is one JSON line)."""
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.type}\nid: {self.seq}\ndata: {payload}\n\n"


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: RunEvent) -> None: ...


class NullEventSink:
    """Headless: every event is discarded."""

    def emit(self, event: RunEvent) -> None:
        return None


class ListEventSink:
    """Collects events in memory (tests, the CLI summary)."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []
        self._lock = threading.Lock()

    def emit(self, event: RunEvent) -> None:
        with self._lock:
            self.events.append(event)

    def of_type(self, type_: str) -> list[RunEvent]:
        with self._lock:
            return [e for e in self.events if e.type == type_]


class Subscription:
    """One subscriber's bounded, drop-oldest queue."""

    def __init__(self, maxsize: int) -> None:
        self._queue: deque[RunEvent] = deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self._closed = False
        self.dropped = 0
        self._resync_pending = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    # producer side
    def _offer(self, event: RunEvent) -> None:
        with self._lock:
            if self._queue.maxlen is not None and len(self._queue) >= self._queue.maxlen:
                self.dropped += 1
                self._resync_pending = True
            self._queue.append(event)  # full deque evicts the head: drop-oldest

    def _close(self) -> None:
        with self._lock:
            self._closed = True

    # consumer side
    def get_nowait(self) -> RunEvent | None:
        with self._lock:
            if self._resync_pending:
                self._resync_pending = False
                return RunEvent("resync", {"dropped": self.dropped})
            return self._queue.popleft() if self._queue else None

    def drain(self) -> list[RunEvent]:
        out: list[RunEvent] = []
        while (event := self.get_nowait()) is not None:
            out.append(event)
        return out

    def __iter__(self) -> Iterator[RunEvent]:
        return iter(self.drain())

    async def get(self, poll_interval: float = 0.05) -> RunEvent | None:
        """Await the next event; ``None`` once the bus is closed and the queue drained."""
        while True:
            event = self.get_nowait()
            if event is not None:
                return event
            if self.closed:
                return None
            await asyncio.sleep(poll_interval)


class RunEventBus:
    """Thread-safe fan-out :class:`EventSink` with per-subscriber bounded queues."""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._subscribers: list[Subscription] = []
        self._closed = False
        self._seq = itertools.count(1)
        self._last_state: RunEvent | None = None

    def emit(self, event: RunEvent) -> None:
        with self._lock:
            if self._closed:
                return
            stamped = RunEvent(event.type, event.data, next(self._seq))
            if stamped.type == "run.state":
                self._last_state = stamped
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub._offer(stamped)

    def subscribe(self) -> Subscription:
        """New subscriber; the latest ``run.state`` is replayed so a late tab has a header."""
        sub = Subscription(self._maxsize)
        with self._lock:
            if self._last_state is not None:
                sub._offer(self._last_state)
            if self._closed:
                sub._close()
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._subscribers.remove(sub)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub._close()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


__all__ = [
    "DEFAULT_QUEUE_SIZE",
    "EVENT_TYPES",
    "EventSink",
    "ListEventSink",
    "NullEventSink",
    "RunEvent",
    "RunEventBus",
    "Subscription",
]
