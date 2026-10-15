"""Per-(arm, card) progress counters with throttled ``run.progress`` events (spec §4.4, §4.8).

Counters are kept in memory (thread-safe), mirrored into the store's
``progress`` index on every change (cheap SQLite upsert) and emitted on the
event sink at most every :data:`PROGRESS_INTERVAL_S` seconds — plus once
whenever a card completes and once at :meth:`ProgressTracker.flush`, so the
final state is always delivered.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from qr_llm_evals.runner.events import EventSink, RunEvent
from qr_llm_evals.store.schema import ArmProgress, CardProgress, Progress
from qr_llm_evals.store.store import RunStore

PROGRESS_INTERVAL_S = 2.0


class ProgressTracker:
    def __init__(
        self,
        run_store: RunStore | None,
        sink: EventSink,
        *,
        perf: Callable[[], float],
        interval_s: float = PROGRESS_INTERVAL_S,
    ) -> None:
        self._store = run_store
        self._sink = sink
        self._perf = perf
        self._interval = interval_s
        self._lock = threading.Lock()
        self._cells: dict[tuple[str, str], CardProgress] = {}
        self._last_emit: float | None = None

    def set_total(self, arm_id: str, card_id: str, total: int, *, done: int = 0) -> None:
        with self._lock:
            cell = CardProgress(done=done, total=total, aborted=0)
            self._cells[(arm_id, card_id)] = cell
            self._persist(arm_id, card_id, cell)
        self._maybe_emit(force=False)

    def record(self, arm_id: str, card_id: str, *, completed: bool, aborted: bool = False) -> None:
        with self._lock:
            cell = self._cells.get((arm_id, card_id)) or CardProgress()
            cell = CardProgress(
                done=cell.done + (1 if completed else 0),
                total=cell.total,
                aborted=cell.aborted + (1 if aborted else 0),
            )
            self._cells[(arm_id, card_id)] = cell
            self._persist(arm_id, card_id, cell)
            card_done = cell.total > 0 and cell.done >= cell.total
        self._maybe_emit(force=card_done)

    def _persist(self, arm_id: str, card_id: str, cell: CardProgress) -> None:
        if self._store is not None:
            self._store.set_progress(
                arm_id, card_id, done=cell.done, total=cell.total, aborted=cell.aborted
            )

    def snapshot(self) -> Progress:
        with self._lock:
            by_arm: dict[str, ArmProgress] = {}
            for (arm_id, card_id), cell in self._cells.items():
                arm = by_arm.setdefault(arm_id, ArmProgress())
                arm.by_card[card_id] = cell.model_copy()
            return Progress(by_arm=by_arm)

    def totals(self) -> dict[str, int]:
        with self._lock:
            return {
                "done": sum(c.done for c in self._cells.values()),
                "total": sum(c.total for c in self._cells.values()),
                "aborted": sum(c.aborted for c in self._cells.values()),
            }

    def _maybe_emit(self, *, force: bool) -> None:
        now = self._perf()
        with self._lock:
            due = self._last_emit is None or now - self._last_emit >= self._interval
            if not (force or due):
                return
            self._last_emit = now
        self._sink.emit(RunEvent("run.progress", self.payload()))

    def payload(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {"by_arm": snap.model_dump(mode="json")["by_arm"], **self.totals()}

    def flush(self) -> None:
        self._maybe_emit(force=True)


__all__ = ["PROGRESS_INTERVAL_S", "ProgressTracker"]
