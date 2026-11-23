"""The health summary of a run as the API serves it.

``store.health.summarise`` reads every generation and judge row of a run. The
summary is served on open routes (the run page polls it, the study page asks
for every run of a study), so the last summary of a run is kept and reused
while none of the files it was read from has changed.

This module has no router: ``runs`` and ``studies`` call it.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from qr_llm_evals.api.schema import RunHealthCounts
from qr_llm_evals.store.health import HealthSummary, summarise
from qr_llm_evals.store.store import RunStore

#: Runs whose last summary is kept.
CACHE_SIZE = 64

Signature = tuple[tuple[tuple[str, int, int], ...], str | None]


def _signature(run_store: RunStore) -> Signature:
    """What the summary depends on: the files it reads and the recorded progress.

    A JSONL file only grows and ``run.json`` is replaced as a whole, so the size
    and the modification time of each file change whenever its content does.
    """
    run_dir = run_store.run_dir
    paths = [run_dir.run_json, run_dir.taints_jsonl, run_dir.flags_jsonl]
    if run_dir.arms_dir.is_dir():
        paths += sorted(run_dir.arms_dir.glob("*/cards/*/generations.jsonl"))
        paths += sorted(run_dir.arms_dir.glob("*/cards/*/judge/*.jsonl"))
    stats: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            found = path.stat()
        except OSError:
            continue  # a file the run has not written yet
        stats.append((path.as_posix(), found.st_size, found.st_mtime_ns))
    try:
        progress: str | None = run_store.progress().model_dump_json()
    except Exception:  # noqa: BLE001 - the index is a convenience; the files are the record
        progress = None
    return tuple(stats), progress


class HealthCache:
    """The last health summary of each of the most recently asked runs."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._kept: OrderedDict[str, tuple[Signature, HealthSummary]] = OrderedDict()

    def summary(self, run_store: RunStore) -> HealthSummary:
        key = run_store.run_dir.path.as_posix()
        # Taken before the files are read: a row written while the summary is
        # built gives the next call another signature, never a stale answer.
        signature = _signature(run_store)
        with self._lock:
            kept = self._kept.get(key)
            if kept is not None and kept[0] == signature:
                self._kept.move_to_end(key)
                return kept[1]
        summary = summarise(run_store)
        with self._lock:
            self._kept[key] = (signature, summary)
            self._kept.move_to_end(key)
            while len(self._kept) > self._size:
                self._kept.popitem(last=False)
        return summary

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()


_CACHE = HealthCache()


def run_health(run_store: RunStore) -> HealthSummary:
    """The health summary of one run, from its files."""
    return _CACHE.summary(run_store)


def counts(summary: HealthSummary) -> RunHealthCounts:
    """The public sample counts of a run, added up from its health summary."""
    return RunHealthCounts(
        completed_samples=sum(cell.completed for cell in summary.cells),
        failed_samples=sum(cell.errored for cell in summary.cells),
        tainted_samples=summary.tainted_samples,
        taints=dict(summary.taints),
    )


def run_counts(run_store: RunStore) -> RunHealthCounts | None:
    """``counts`` of a run, or nothing when its files cannot be summarised."""
    try:
        return counts(run_health(run_store))
    except (OSError, ValueError):
        return None


__all__ = ["CACHE_SIZE", "HealthCache", "counts", "run_counts", "run_health"]
