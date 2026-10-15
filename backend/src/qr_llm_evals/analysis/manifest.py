"""Reproducibility manifest for one analysis output."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_hashes(study_dir: Path, run_dir: Path) -> dict[str, str]:
    """Hash every analysis input, excluding generated analysis directories."""
    paths: list[Path] = []
    for root in (study_dir, run_dir):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.is_relative_to(run_dir) and "analysis" in path.relative_to(run_dir).parts:
                continue
            paths.append(path)
    unique = sorted(set(paths), key=lambda item: item.as_posix())
    return {path.relative_to(study_dir).as_posix(): sha256_file(path) for path in unique}


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def git_commit(root: Path) -> str | None:
    """Resolve HEAD without launching a process (supports repos and worktrees)."""
    marker = root / ".git"
    if marker.is_dir():
        git_dir = marker
    elif marker.is_file():
        line = marker.read_text(encoding="utf-8").strip()
        if not line.startswith("gitdir: "):
            return None
        candidate = Path(line.removeprefix("gitdir: "))
        git_dir = candidate if candidate.is_absolute() else (root / candidate).resolve()
    else:
        return None
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        return None
    head = head_path.read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head or None
    ref = head.removeprefix("ref: ")
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip() or None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(("#", "^")):
                commit, _, name = line.partition(" ")
                if name == ref:
                    return commit
    # A linked worktree keeps shared refs in the common directory.
    common_path = git_dir / "commondir"
    if common_path.is_file():
        common = (git_dir / common_path.read_text(encoding="utf-8").strip()).resolve()
        loose = common / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
    return None


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return Path(__file__).resolve().parent


def build_manifest(study_dir: Path, run_dir: Path, plan_hash: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plan_hash": plan_hash,
        "inputs": input_hashes(study_dir, run_dir),
        "code": {
            "version": _version("qr-llm-evals"),
            "commit": git_commit(_repository_root()),
        },
        "tools": {
            "python": platform.python_version(),
            "numpy": _version("numpy"),
            "scipy": _version("scipy"),
            "matplotlib": _version("matplotlib"),
        },
    }


__all__ = ["build_manifest", "git_commit", "input_hashes", "sha256_file"]
