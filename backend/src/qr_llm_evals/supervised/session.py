"""Supervised session state machine and per-slot boundary coordination."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from qr_llm_evals.store.store import RunStore
from qr_llm_evals.supervised.boundary import BoundaryEstimator
from qr_llm_evals.supervised.groups import PresenceTracker
from qr_llm_evals.supervised.questionnaires import QuestionnaireGate
from qr_llm_evals.supervised.schema import Intention, ObserverPresence, Session, Window

log = logging.getLogger("qr_llm_evals.supervised")

SessionState = Literal[
    "briefing",
    "intention",
    "ready",
    "running",
    "boundary_pending",
    "break",
    "refocus",
    "complete",
]
SessionAction = Literal["start", "resume", "stop_at_boundary", "end_break"]
EventEmitter = Callable[[str, dict[str, object]], None]


class SessionControlError(RuntimeError):
    pass


def new_session_id() -> str:
    return "sess_" + secrets.token_hex(8)


class SessionController:
    """Thread-safe state machine; only :meth:`control` may enter ``running``."""

    def __init__(
        self,
        record: Session,
        store: RunStore,
        estimator: BoundaryEstimator,
        *,
        streams: int,
        break_s: float,
        refocus_s: float,
        join_code: str,
        intention_s: float = 180.0,
        emit: EventEmitter | None = None,
        clock: Callable[[], datetime] | None = None,
        perf: Callable[[], float] | None = None,
    ) -> None:
        if streams < 1:
            raise ValueError("streams must be positive")
        self.record = record
        self.store = store
        self.estimator = estimator
        self.streams = streams
        self.break_s = break_s
        self.refocus_s = refocus_s
        self.intention_s = intention_s
        self.join_code = join_code
        self.emit = emit or (lambda _type, _data: None)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.perf = perf or time.monotonic
        self.state: SessionState = "briefing"
        self.presence = PresenceTracker()
        self.questionnaires = QuestionnaireGate()
        self._active_slots: set[int] = set()
        self._stopped_slots: set[int] = set()
        self._session_start_perf: float | None = None
        self._phase_start_perf: float = self.perf()
        self._lock = threading.RLock()
        self.store.write_session(self.record)

    @property
    def session_id(self) -> str:
        return self.record.session_id

    def acknowledge_briefing(self) -> None:
        with self._lock:
            if self.state != "briefing":
                raise SessionControlError("briefing can only be acknowledged once")
            self._set_state("intention")

    def set_intention(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            raise SessionControlError("intention must not be empty")
        with self._lock:
            if self.state not in {"briefing", "intention"}:
                raise SessionControlError(f"cannot set intention in state {self.state!r}")
            at = self.clock()
            intention = Intention(
                text=clean, hash=hashlib.sha256(clean.encode("utf-8")).hexdigest(), at=at
            )
            self.record = self.record.model_copy(update={"intention": intention})
            self.store.write_session(self.record)
            self._set_state("ready")

    def control(self, action: SessionAction, *, present_observer_ids: set[str]) -> None:
        with self._lock:
            if action in {"start", "resume"}:
                self._start(action, present_observer_ids)
            elif action == "stop_at_boundary":
                if self.state != "running":
                    raise SessionControlError("stop_at_boundary requires a running session")
                self._stopped_slots = set(range(self.streams))
                self._set_state("boundary_pending")
                if not self._active_slots:
                    self._finish_running_window()
            else:
                self._end_break()

    def _start(self, action: SessionAction, present_observer_ids: set[str]) -> None:
        if self.state != "ready":
            raise SessionControlError(f"cannot {action} a session in state {self.state!r}")
        if self.record.ended_at is not None:
            raise SessionControlError(
                "this sitting has ended; create the next session before resuming"
            )
        try:
            self.questionnaires.require(self.session_id, "pre", present_observer_ids)
        except ValueError as err:
            raise SessionControlError(str(err)) from err
        now = self.clock()
        if self.record.started_at is None:
            self.record = self.record.model_copy(update={"started_at": now})
        self._session_start_perf = self.perf()
        self._phase_start_perf = self._session_start_perf
        self._active_slots.clear()
        self._stopped_slots.clear()
        self._open_window("supervised")
        self._set_state("running")

    def slot_freed(self, slot: int, card_id: str, *, now: float | None = None) -> bool:
        """Apply the boundary rule for one idle tile; return whether to dispatch."""
        with self._lock:
            self._check_slot(slot)
            self._active_slots.discard(slot)
            if self.state not in {"running", "boundary_pending"}:
                return False
            if self.state == "boundary_pending":
                if not self._active_slots:
                    self._finish_running_window()
                return False
            if slot in self._stopped_slots:
                return False
            if self._session_start_perf is None:
                raise RuntimeError("running session has no start time")
            decision = self.estimator.slot_should_dispatch(
                card_id,
                now=self.perf() if now is None else now,
                session_start=self._session_start_perf,
                target_s=float(self.record.target_s),
            )
            if decision:
                self._active_slots.add(slot)
                return True
            self._stopped_slots.add(slot)
            if len(self._stopped_slots) == self.streams:
                self._set_state("boundary_pending")
                if not self._active_slots:
                    self._finish_running_window()
            return False

    def generation_finished(
        self,
        slot: int,
        card_id: str,
        *,
        tokens: int,
        elapsed_s: float,
        now: float | None = None,
        dispatch_next: bool = True,
    ) -> bool:
        with self._lock:
            self.estimator.observe(card_id, tokens=tokens, elapsed_s=elapsed_s)
            if dispatch_next:
                return self.slot_freed(slot, card_id, now=now)
            # Production admission uses the *next* job's card estimate. Do not
            # reserve a phantom generation when the queue is exhausted/paused.
            self._active_slots.discard(slot)
            if self.state == "boundary_pending" and not self._active_slots:
                self._finish_running_window()
            return False

    def claim_slot(self, card_id: str) -> int | None:
        """Atomically admit a real job across all supervised arms and engines."""
        with self._lock:
            present = {p.observer_id for p in self.current_present(self.perf())}
            if not present or self.questionnaires.missing(self.session_id, "pre", present):
                return None
            for slot in range(self.streams):
                if slot not in self._active_slots and self.slot_freed(slot, card_id):
                    return slot
            return None

    def stop(self) -> None:
        """Run pause/abort ends this sitting at its last completion boundary."""
        with self._lock:
            if self.state == "running":
                self.control("stop_at_boundary", present_observer_ids=set())

    def tick(self, *, now: float | None = None) -> float | None:
        """Advance refocus to ready only; never enter ``running``."""
        with self._lock:
            current = self.perf() if now is None else now
            if self.state == "ready" and self.record.ended_at is None:
                remaining = self.intention_s - (current - self._phase_start_perf)
                self._emit_timer("intention", remaining)
                return remaining
            if self.state == "refocus":
                remaining = self.refocus_s - (current - self._phase_start_perf)
                self._emit_timer("refocus", remaining)
                if remaining <= 0:
                    self._close_window()
                    self._set_state("ready")
                return remaining
            if self.state == "break":
                remaining = self.break_s - (current - self._phase_start_perf)
                self._emit_timer("break", remaining)
                return remaining
            if self.state == "running" and self._session_start_perf is not None:
                remaining = self.record.target_s - (current - self._session_start_perf)
                self._emit_timer("session", remaining)
                if remaining <= 0:
                    self.stop()
                return remaining
            return None

    def presence_changed(self, *, now: float) -> None:
        with self._lock:
            present = self.presence.present(now)
            if self.record.windows and self.record.windows[-1].end is None:
                current = self.record.windows[-1]
                if current.observers_present != present:
                    # Split the timeline at every presence change. This keeps
                    # the former set durable instead of overwriting history.
                    windows = list(self.record.windows)
                    windows[-1] = current.model_copy(update={"end": self.clock()})
                    self.record = self.record.model_copy(update={"windows": windows})
                    self.store.write_session(self.record)
                    self._open_window(current.kind)
                    return
            self.emit(
                "observer.presence",
                {
                    "session_id": self.session_id,
                    "present": [p.model_dump(mode="json") for p in present],
                },
            )

    def current_present(self, now: float) -> list[ObserverPresence]:
        return self.presence.present(now)

    def _finish_running_window(self) -> None:
        now_perf = self.perf()
        self._close_window()
        if self._session_start_perf is None:
            raise RuntimeError("boundary pending session has no start time")
        overrun = now_perf - self._session_start_perf - self.record.target_s
        self.record = self.record.model_copy(
            update={"ended_at": self.clock(), "overrun_s": overrun}
        )
        self.store.write_session(self.record)
        log.info(
            "supervised session boundary reached",
            extra={
                "event": "session.overrun_s",
                "session_id": self.session_id,
                "overrun_s": overrun,
            },
        )
        self._open_window("break")
        self._phase_start_perf = now_perf
        self._set_state("break")

    def _end_break(self) -> None:
        if self.state != "break":
            raise SessionControlError("end_break requires a break")
        self._close_window()
        self._open_window("refocus")
        self._phase_start_perf = self.perf()
        self._set_state("refocus")

    def _open_window(self, kind: Literal["supervised", "break", "refocus"]) -> None:
        now_perf = self.perf()
        present = self.presence.present(now_perf)
        window = Window(
            window_id=f"win_{secrets.token_hex(8)}",
            kind=kind,
            start=self.clock(),
            observers_present=present,
        )
        self.record = self.record.model_copy(update={"windows": [*self.record.windows, window]})
        self.store.write_session(self.record)
        self.emit(
            "observer.presence",
            {
                "session_id": self.session_id,
                "present": [p.model_dump(mode="json") for p in present],
            },
        )

    def _close_window(self) -> None:
        if not self.record.windows or self.record.windows[-1].end is not None:
            return
        windows = list(self.record.windows)
        windows[-1] = windows[-1].model_copy(update={"end": self.clock()})
        self.record = self.record.model_copy(update={"windows": windows})
        self.store.write_session(self.record)

    def _set_state(self, state: SessionState) -> None:
        self.state = state
        self.emit(
            "session.state",
            {"session_id": self.session_id, "state": state, "since": self.clock().isoformat()},
        )

    def _emit_timer(self, kind: str, remaining_s: float) -> None:
        self.emit(
            "session.timer",
            {"session_id": self.session_id, "kind": kind, "remaining_s": remaining_s},
        )

    def _check_slot(self, slot: int) -> None:
        if not 0 <= slot < self.streams:
            raise ValueError(f"slot must be in [0, {self.streams})")


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionController] = {}
        self._join_codes: dict[str, str] = {}
        self._lock = threading.Lock()

    def add(self, controller: SessionController) -> None:
        with self._lock:
            if controller.session_id in self._sessions or controller.join_code in self._join_codes:
                raise ValueError("duplicate session id or join code")
            self._sessions[controller.session_id] = controller
            self._join_codes[controller.join_code] = controller.session_id

    def get(self, session_id: str) -> SessionController | None:
        with self._lock:
            return self._sessions.get(session_id)

    def by_join_code(self, join_code: str) -> SessionController | None:
        with self._lock:
            session_id = self._join_codes.get(join_code)
            return None if session_id is None else self._sessions.get(session_id)

    def for_run(self, run_id: str) -> list[SessionController]:
        with self._lock:
            return sorted(
                (item for item in self._sessions.values() if item.record.run_id == run_id),
                key=lambda item: item.record.index,
            )

    def values(self) -> list[SessionController]:
        with self._lock:
            return list(self._sessions.values())


__all__ = [
    "SessionAction",
    "SessionControlError",
    "SessionController",
    "SessionRegistry",
    "SessionState",
    "new_session_id",
]
