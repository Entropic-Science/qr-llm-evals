"""``RunControl`` — start / pause / resume / abort shared by the API and the workers (spec §2.5).

Semantics:

* **start** is explicit. The orchestrator refuses to dispatch until
  :meth:`RunControl.start` has been called (AGENTS.md invariant 6: nothing
  auto-starts; in supervised mode the session machine calls ``start``/``resume``
  only on a host click).
* **pause** is honoured at the *item boundary*: no new sample is dispatched
  while paused; in-flight streams finish (a vLLM request cannot be paused).
* **resume** lifts the pause.
* **abort** latches, wakes any paused wait, and trips every registered
  :class:`~qr_llm_evals.client.cancel.CancelToken`, which closes the in-flight
  HTTP streams so the engine aborts those sequences server-side.

Thread-safe: backed by ``threading.Event`` / ``Lock``; queried from worker
threads, driven from the API's event loop.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from qr_llm_evals.client.cancel import CancelToken

ControlState = Literal["created", "running", "paused", "aborted"]


class RunControlError(RuntimeError):
    """A control call that is not valid in the current state."""


class RunControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._not_paused = threading.Event()
        self._not_paused.set()
        self._abort = threading.Event()
        self._abort_reason = "aborted"
        self._tokens: set[CancelToken] = set()

    # ── host controls ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Explicit host start; idempotent while not aborted."""
        if self._abort.is_set():
            raise RunControlError("cannot start an aborted run")
        self._started.set()

    def pause(self) -> None:
        if self._abort.is_set():
            raise RunControlError("cannot pause an aborted run")
        self._not_paused.clear()

    def resume(self) -> None:
        """Explicit host resume (also the transition used after a supervised break)."""
        if self._abort.is_set():
            raise RunControlError("cannot resume an aborted run")
        self._started.set()
        self._not_paused.set()

    def abort(self, reason: str = "aborted") -> None:
        """Latch abort, wake paused waiters and close every in-flight stream."""
        with self._lock:
            if not self._abort.is_set():
                self._abort_reason = reason
            self._abort.set()
            self._not_paused.set()
            tokens = list(self._tokens)
        for token in tokens:
            token.cancel(reason)

    # ── queries (workers) ─────────────────────────────────────────────────

    @property
    def started(self) -> bool:
        return self._started.is_set()

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()

    @property
    def abort_reason(self) -> str:
        return self._abort_reason

    @property
    def paused(self) -> bool:
        return not self._not_paused.is_set() and not self._abort.is_set()

    @property
    def state(self) -> ControlState:
        if self._abort.is_set():
            return "aborted"
        if not self._started.is_set():
            return "created"
        return "paused" if not self._not_paused.is_set() else "running"

    def wait_while_paused(self, poll: float = 0.05) -> bool:
        """Block at a boundary while paused; ``True`` if the run may continue, ``False`` on abort."""
        while not self._not_paused.wait(poll):
            if self._abort.is_set():
                return False
        return not self._abort.is_set()

    def wait_for_start(self, timeout: float | None = None) -> bool:
        return self._started.wait(timeout)

    # ── in-flight stream registry ─────────────────────────────────────────

    def register(self, token: CancelToken) -> None:
        with self._lock:
            self._tokens.add(token)
            aborted = self._abort.is_set()
        if aborted:  # abort raced the registration: trip it now
            token.cancel(self._abort_reason)

    def unregister(self, token: CancelToken) -> None:
        with self._lock:
            self._tokens.discard(token)

    @contextmanager
    def in_flight(self) -> Iterator[CancelToken]:
        """``with control.in_flight() as cancel:`` — a token registered for the block."""
        token = CancelToken()
        self.register(token)
        try:
            yield token
        finally:
            self.unregister(token)

    @property
    def in_flight_count(self) -> int:
        with self._lock:
            return len(self._tokens)


__all__ = ["ControlState", "RunControl", "RunControlError"]
