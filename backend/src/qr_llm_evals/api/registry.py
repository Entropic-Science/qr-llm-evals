"""In-process registry of live runs (spec §2.1, §2.4, D-14).

A :class:`LiveRun` is everything about a run that exists **only in memory**:
its :class:`~qr_llm_evals.runner.control.RunControl`, its event bus (the SSE
fan-out), its :class:`~qr_llm_evals.keys.secrets.SecretBag` and the thread the
orchestrator runs on. The store holds the durable half (``run.json`` + JSONL).

A backend restart therefore loses every ``LiveRun``; :func:`mark_orphans`
flips each unfinished run on disk to ``needs_keys`` at boot so the host
re-enters the BYO keys and resumes (D-14: honest about never persisting keys).

Threading: the orchestrator is synchronous and blocks; it runs on a daemon
thread per run. ``start`` / ``resume`` are the only paths into ``running``
and both need an explicit host control call (AGENTS.md invariant 6).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Protocol

from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.runner.control import RunControl, RunControlError
from qr_llm_evals.runner.events import EventSink, RunEvent, RunEventBus
from qr_llm_evals.store.schema import Run, RunState
from qr_llm_evals.store.store import RunStore

log = logging.getLogger("qr_llm_evals.api.registry")

TERMINAL_STATES: frozenset[str] = frozenset({"complete", "aborted"})
#: States from which ``start`` is accepted.
STARTABLE: frozenset[str] = frozenset({"created", "ready"})
#: States from which ``resume`` is accepted.
RESUMABLE: frozenset[str] = frozenset({"paused", "needs_keys", "ready"})


class Runnable(Protocol):
    """The slice of ``Orchestrator`` the registry drives."""

    def run(self) -> Run: ...


#: Builds the orchestrator for one run; the app injects transports through it.
OrchestratorFactory = Callable[[RunStore, SecretBag, RunControl, EventSink], Runnable]


class LiveRun:
    def __init__(self, run_store: RunStore, secrets: SecretBag, *, queue_size: int) -> None:
        self.run_store = run_store
        self.secrets = secrets
        self.control = RunControl()
        self.bus = RunEventBus(queue_size)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        return self.run_store.run_id

    @property
    def thread_alive(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def read(self) -> Run:
        return self.run_store.read_run()

    def write(self, run: Run) -> None:
        self.run_store.write_run(run)

    def set_state(self, state: RunState, **extra: object) -> Run:
        """Persist a state change made by the API itself (not by the orchestrator)."""
        run = self.read().model_copy(update={"state": state, **extra})
        self.write(run)
        self.bus.emit(
            RunEvent(
                "run.state",
                {"run_id": run.run_id, "state": state, "at": datetime.now(UTC).isoformat()},
            )
        )
        return run

    # ── host controls ─────────────────────────────────────────────────────

    def start(self, factory: OrchestratorFactory) -> Run:
        run = self.read()
        if run.state not in STARTABLE:
            raise RunControlError(f"cannot start a run in state {run.state!r}")
        self._launch(factory)
        return self.read()

    def resume(self, factory: OrchestratorFactory) -> Run:
        run = self.read()
        if run.state in TERMINAL_STATES:
            raise RunControlError(f"cannot resume a run in state {run.state!r}")
        if run.state == "needs_keys" or run.needs_keys:
            raise RunControlError("run needs its provider keys first (POST /runs/{id}/keys)")
        if run.state not in RESUMABLE and run.state != "running":
            raise RunControlError(f"cannot resume a run in state {run.state!r}")
        with self._lock:
            if self.thread_alive:
                if run.open_halts():
                    # Lifting the pause would do nothing: the orchestrator is on
                    # its way out and only a new one goes on past a check stop.
                    raise RunControlError(
                        "a check has stopped this run and the samples that were already "
                        "being generated are still finishing; resume once the run is paused"
                    )
                self.control.resume()
                return self.read()
        # The orchestrator returned (a check stop, needs_keys after a restart,
        # or a prior backend process): start a fresh one. It plans again from
        # the store, so it skips completed sample_refs, tries the rest again
        # with the keys held now, and stamps an open halt as resumed.
        self.control = RunControl()
        self._launch(factory)
        return self.read()

    def pause(self) -> Run:
        run = self.read()
        if run.state != "running":
            raise RunControlError(f"cannot pause a run in state {run.state!r}")
        self.control.pause()
        return run

    def abort(self, reason: str = "aborted by host") -> Run:
        run = self.read()
        if run.state in TERMINAL_STATES:
            raise RunControlError(f"run is already {run.state}")
        self.control.abort(reason)
        if not self.thread_alive:
            # Nothing is running that would persist the transition; do it here.
            run = self.set_state("aborted")
            self.bus.close()
        return run

    def supply_keys(self, keys: dict[str, str | None]) -> Run:
        for name, value in keys.items():
            if value:
                self.secrets.set(name, value)
        run = self.read()
        update: dict[str, object] = {"keys_present": self.secrets.keys_present()}
        if run.needs_keys or run.state == "needs_keys":
            update["needs_keys"] = False
            update["state"] = "paused"
        run = run.model_copy(update=update)
        self.write(run)
        return run

    # ── thread ────────────────────────────────────────────────────────────

    def _launch(self, factory: OrchestratorFactory) -> None:
        with self._lock:
            if self.thread_alive:
                raise RunControlError("run is already executing")
            orchestrator = factory(self.run_store, self.secrets, self.control, self.bus)
            self.control.start()
            thread = threading.Thread(
                target=self._body, args=(orchestrator,), name=f"run-{self.run_id}", daemon=True
            )
            self._thread = thread
            thread.start()

    def _body(self, orchestrator: Runnable) -> None:
        try:
            run = orchestrator.run()
        except Exception:  # noqa: BLE001 - the orchestrator persisted 'aborted' already
            log.exception("run %s failed", self.run_id, extra={"run_id": self.run_id})
            run = self.read()
        if run.state in TERMINAL_STATES:
            self.bus.close()

    def join(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def close(self) -> None:
        if self.thread_alive:
            host_aborted = self.control.aborted
            self.control.abort("backend shutdown")
            self.join(10.0)
            if self.thread_alive:
                raise RuntimeError("runner did not stop during backend shutdown")
            if not host_aborted and self.read().state != "complete":
                self.set_state("needs_keys", needs_keys=True)
        self.bus.close()
        self.secrets.clear()


class RunRegistry:
    """``run_id -> LiveRun`` for this process."""

    def __init__(self, *, queue_size: int = 2000) -> None:
        self._runs: dict[str, LiveRun] = {}
        self._lock = threading.Lock()
        self._queue_size = queue_size

    def register(self, run_store: RunStore, secrets: SecretBag) -> LiveRun:
        live = LiveRun(run_store, secrets, queue_size=self._queue_size)
        with self._lock:
            if run_store.run_id in self._runs:
                raise RunControlError(f"run {run_store.run_id!r} is already registered")
            self._runs[run_store.run_id] = live
        return live

    def adopt(self, run_store: RunStore) -> LiveRun:
        """Attach a bus/control to a run found only on disk (post-restart)."""
        with self._lock:
            existing = self._runs.get(run_store.run_id)
            if existing is not None:
                return existing
            live = LiveRun(run_store, SecretBag(), queue_size=self._queue_size)
            self._runs[run_store.run_id] = live
            return live

    def get(self, run_id: str) -> LiveRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)

    def values(self) -> list[LiveRun]:
        with self._lock:
            return list(self._runs.values())

    def close_all(self) -> None:
        for live in self.values():
            live.close()


def mark_orphans(runs: Iterable[Run], write: Callable[[Run], None]) -> list[str]:
    """Flip every unfinished run to ``needs_keys`` (boot after a restart, D-14)."""
    flipped: list[str] = []
    for run in runs:
        if run.state in TERMINAL_STATES:
            continue
        write(run.model_copy(update={"state": "needs_keys", "needs_keys": True}))
        flipped.append(run.run_id)
    return flipped


__all__ = [
    "RESUMABLE",
    "STARTABLE",
    "TERMINAL_STATES",
    "LiveRun",
    "OrchestratorFactory",
    "RunRegistry",
    "Runnable",
    "mark_orphans",
]
