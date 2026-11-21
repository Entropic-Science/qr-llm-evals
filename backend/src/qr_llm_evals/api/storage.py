"""Disk used by each account's run artifacts, and the free space left.

A run's size is the sum of the files under its run directory. Study-level
files (``study.json``, the pre-registration) are a few kilobytes and can be
shared by several owners, so they are not attributed to anyone.

Sizes are measured by walking the directory and kept for
:data:`CACHE_SECONDS`; a run that is still generating grows between
measurements, which is fine for a storage read-out. Nothing here deletes or
moves a file.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qr_llm_evals.store.schema import Run
from qr_llm_evals.store.store import Store

CACHE_SECONDS = 60.0


def directory_bytes(path: Path) -> int:
    """Total size of the regular files under ``path``; ``0`` when it is gone."""
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


@dataclass(frozen=True, slots=True)
class RunSize:
    run: Run
    size_bytes: int


@dataclass(slots=True)
class StorageMeter:
    """Per-run sizes with a short cache, grouped by owner on request."""

    clock: Callable[[], float] = time.monotonic
    _sizes: dict[str, tuple[float, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def run_bytes(self, store: Store, run: Run) -> int:
        now = self.clock()
        with self._lock:
            cached = self._sizes.get(run.run_id)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        size = directory_bytes(store.root.study(run.study_id).run(run.run_id).path)
        with self._lock:
            self._sizes[run.run_id] = (now, size)
        return size

    def runs_of(self, store: Store, owner_id: str) -> list[RunSize]:
        """The owner's runs, largest first."""
        sized = [
            RunSize(run=run, size_bytes=self.run_bytes(store, run))
            for run in store.list_runs()
            if run.owner_id == owner_id
        ]
        return sorted(sized, key=lambda item: -item.size_bytes)

    def bytes_by_owner(self, store: Store) -> dict[str, int]:
        totals: dict[str, int] = {}
        for run in store.list_runs():
            if run.owner_id is not None:
                totals[run.owner_id] = totals.get(run.owner_id, 0) + self.run_bytes(store, run)
        return totals

    @staticmethod
    def disk(store: Store) -> tuple[int, int]:
        """``(free, total)`` bytes of the volume holding the data directory."""
        usage = shutil.disk_usage(store.root.path)
        return usage.free, usage.total


__all__ = ["CACHE_SECONDS", "RunSize", "StorageMeter", "directory_bytes"]
