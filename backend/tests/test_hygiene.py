"""Results/secrets hygiene gate over *git-tracked* files (spec §5.2).

1. ``results/``, ``data/``, ``logs/`` are never tracked.
2. No ``*.jsonl`` outside ``backend/tests/fixtures/`` or ``packages/contracts/fixtures/``.
3. No Inspect ``*.eval`` logs.
4. No key files (``api-keys.json``, ``*.env`` other than ``*.env.example``).
5. No credential-shaped strings in tracked text files. Test fixtures that need a
   key-shaped value build it by concatenation (``"qrk_" + "..."``) so the source
   never contains the shape.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXTURE_ROOTS = (("backend", "tests", "fixtures"), ("packages", "contracts", "fixtures"))
NEVER_TRACKED_DIRS = ("results", "data", "logs")
SECRET_PATTERNS = (
    re.compile(r"\bqrk_[A-Za-z0-9_-]{24,}\b"),  # qr-llm-api bearer keys
    re.compile(r"\bsk-ant-api\d{2}-[A-Za-z0-9_-]{20,}\b"),  # Anthropic
    re.compile(r"\bsk-or-v1-[a-f0-9]{32,}\b"),  # OpenRouter
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{32,}\b"),  # OpenAI project keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS
)
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".md", ".yml", ".yaml", ".json", ".toml", ".txt", ".j2"}


def _tracked() -> list[PurePosixPath]:
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [PurePosixPath(p) for p in out.split("\0") if p]


def _under_fixtures(rel: PurePosixPath) -> bool:
    return any(rel.parts[: len(root)] == root for root in FIXTURE_ROOTS)


def test_artifact_directories_never_tracked() -> None:
    bad = [str(r) for r in _tracked() if r.parts and r.parts[0] in NEVER_TRACKED_DIRS]
    assert not bad, f"artifact directories must stay gitignored: {bad}"


def test_no_jsonl_outside_fixtures() -> None:
    bad = [str(r) for r in _tracked() if r.suffix == ".jsonl" and not _under_fixtures(r)]
    assert not bad, f"*.jsonl artifacts committed outside fixtures/: {bad}"


def test_no_inspect_eval_logs() -> None:
    bad = [str(r) for r in _tracked() if r.suffix == ".eval"]
    assert not bad, f"Inspect *.eval logs must never be committed: {bad}"


def test_no_key_files() -> None:
    bad = [
        str(r)
        for r in _tracked()
        if r.name == "api-keys.json"
        or (r.name.endswith(".env") and not r.name.endswith(".env.example"))
    ]
    assert not bad, f"key/env files must never be committed: {bad}"


def test_no_credential_shaped_strings() -> None:
    hits: list[str] = []
    for rel in _tracked():
        if rel.suffix not in TEXT_SUFFIXES:
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        hits.extend(f"{rel}: matches /{rx.pattern}/" for rx in SECRET_PATTERNS if rx.search(text))
    assert not hits, "credential-shaped strings in tracked files:\n" + "\n".join(hits)
