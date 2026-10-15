"""Invalidating artifacts without deleting them (AGENTS.md invariant 4, spec §4.5).

Runs cost QRNG time and GPU time, so nothing under the data root is ever
removed. Two operations exist instead:

* :func:`quarantine` **moves** a file or directory to
  ``<root>/_quarantine/<YYYY-MM-DD>/<original relative path>`` and logs why.
* :func:`withdraw` flags a run ``withdrawn`` (run.json + index) and writes a
  ``WITHDRAWN.md`` banner; the artifacts stay in place and stay in bundles.

``backend/tests/test_store.py`` AST-scans this package for ``os.remove``,
``os.unlink``, ``os.rmdir``, ``shutil.rmtree``, ``Path.unlink`` and
``Path.rmdir`` so the rule cannot erode silently.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from qr_llm_evals.store.artifacts import append_jsonl, read_json, write_json, write_text
from qr_llm_evals.store.db import Index
from qr_llm_evals.store.paths import DataRoot, RunDir
from qr_llm_evals.store.schema import Run

QUARANTINE_LOG = "log.jsonl"


def quarantine(root: DataRoot, path: Path, reason: str, *, now: datetime | None = None) -> Path:
    """Move ``path`` under ``_quarantine/<date>/`` and log the move. Returns the new path.

    ``path`` must live inside the data root (and not already in quarantine);
    the original relative path is preserved under the dated directory so the
    provenance of a quarantined artifact is obvious from its location alone.
    """
    if not reason.strip():
        raise ValueError("quarantine requires a non-empty reason")
    src = path.resolve()
    if not root.contains(src):
        raise ValueError(f"{path} is outside the data root {root.path}")
    rel = src.relative_to(root.path.resolve())
    if rel.parts and rel.parts[0] == root.quarantine_dir.name:
        raise ValueError(f"{path} is already quarantined")
    if not src.exists():
        raise FileNotFoundError(path)

    at = now or datetime.now(UTC)
    dest = root.quarantine_dir / at.strftime("%Y-%m-%d") / rel
    if dest.exists():  # same artifact quarantined twice in one day: keep both
        dest = dest.with_name(f"{dest.name}-{at.strftime('%H%M%S.%f')}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))

    entry = {
        "at": at.isoformat(timespec="seconds"),
        "from": rel.as_posix(),
        "to": dest.relative_to(root.path.resolve()).as_posix(),
        "reason": reason,
    }
    append_jsonl(root.quarantine_dir / QUARANTINE_LOG, entry)
    write_json(dest.with_name(dest.name + ".quarantined.json"), entry)
    return dest


def withdraw(
    run_dir: RunDir, reason: str, *, index: Index | None = None, now: datetime | None = None
) -> Run:
    """Mark a run withdrawn: ``run.json`` (+ index) flag and a ``WITHDRAWN.md`` banner.

    Idempotent. The run's artifacts are untouched and remain part of the
    study bundle; consumers render the banner (spec §4.5).
    """
    if not reason.strip():
        raise ValueError("withdraw requires a non-empty reason")
    run = Run.model_validate(read_json(run_dir.run_json))
    at = now or datetime.now(UTC)
    if not run.withdrawn:
        run = run.model_copy(update={"withdrawn": True})
        write_json(run_dir.run_json, run)
    banner = (
        "# Withdrawn run\n\n"
        f"Run `{run.run_id}` of study `{run.study_id}` was **withdrawn** on "
        f"{at.isoformat(timespec='seconds')}.\n\n"
        f"Reason: {reason.strip()}\n\n"
        "The artifacts in this directory are kept for the record and are excluded "
        "from the pre-registered analysis; they are not deleted.\n"
    )
    write_text(run_dir.withdrawn_md, banner)
    if index is not None:
        index.upsert_run(run)
    return run


__all__ = ["QUARANTINE_LOG", "quarantine", "withdraw"]
