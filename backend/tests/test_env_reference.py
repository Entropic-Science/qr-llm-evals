"""``config.py`` is the only ``os.environ`` reader, and every read has a doc row.

Every env var literal read in ``backend/src`` (through ``config.py``'s ``_env_*``
helpers or a raw ``os.environ`` access) must have a ``| `VAR` | ... |`` row in
``infra/ENV_REFERENCE.md``. That is what makes the on-box ``.env`` catalogue
exhaustive — a variable cannot enter the code without entering the doc.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from qr_llm_evals.config import Settings, load_settings, normalise_origin

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "backend" / "src"
ENV_DOC = REPO_ROOT / "infra" / "ENV_REFERENCE.md"
CONFIG_REL = "backend/src/qr_llm_evals/config.py"

_ENV_READ_RES = (
    re.compile(r"""_env_(?:str|opt|int|float|bool|list)\(\s*\w+,\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""environ\.get\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""environ\[\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""getenv\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
)
_ENV_TOUCH_RE = re.compile(r"\bos\.environ\b|\bos\.getenv\b")
_TABLE_ROW_RE = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|")

#: Documented for operators but never read by the backend process.
OPERATOR_ONLY = frozenset({"PYTHONUTF8", "TUNNEL_TOKEN"})


def _src_env_reads() -> dict[str, str]:
    seen: dict[str, str] = {}
    for path in sorted(SRC_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for rx in _ENV_READ_RES:
                for name in rx.findall(line):
                    seen.setdefault(name, f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}")
    return seen


def _doc_rows() -> dict[str, str]:
    assert ENV_DOC.is_file(), f"{ENV_DOC} not found"
    rows: dict[str, str] = {}
    for line in ENV_DOC.read_text(encoding="utf-8").splitlines():
        m = _TABLE_ROW_RE.match(line)
        if m:
            rows.setdefault(m.group(1), line)
    return rows


def test_only_config_touches_the_environment() -> None:
    touchers = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in SRC_DIR.rglob("*.py")
        if _ENV_TOUCH_RE.search(path.read_text(encoding="utf-8"))
    }
    assert touchers == {CONFIG_REL}, f"os.environ touched outside config.py: {sorted(touchers)}"


def test_scanner_finds_the_known_reads() -> None:
    reads = _src_env_reads()
    for var in ("QR_EVALS_KEY_VERIFY", "QR_EVALS_MAX_INFLIGHT", "QR_EVALS_DATA_DIR"):
        assert var in reads, var


@pytest.mark.parametrize("var", sorted(_src_env_reads()))
def test_every_env_read_has_a_doc_row(var: str) -> None:
    rows = _doc_rows()
    assert var in rows, (
        f"backend reads {var!r} (first at {_src_env_reads()[var]}) but infra/ENV_REFERENCE.md "
        f"has no '| `{var}` | required | example | source |' row"
    )
    cells = [c.strip() for c in rows[var].strip().strip("|").split("|")]
    assert len(cells) >= 4, f"row for {var} needs name / required / example / source cells"


def test_every_doc_row_is_read_or_operator_only() -> None:
    """Rows must not go stale: each documented var is read by config.py or operator-only."""
    stale = set(_doc_rows()) - set(_src_env_reads()) - OPERATOR_ONLY
    assert not stale, f"documented but never read: {sorted(stale)}"


def test_defaults_load_from_empty_environment() -> None:
    s = load_settings({})
    assert isinstance(s, Settings)
    assert s.vllm_origin == "http://127.0.0.1:8000"
    assert s.key_verify == "file"
    assert s.max_inflight == 3
    assert s.token_log_dir is None
    assert s.bind_port == 8830


def test_settings_are_frozen_and_validated() -> None:
    s = load_settings(
        {"QR_EVALS_VLLM_ORIGIN": " http://127.0.0.1:8000/v1/ ", "QR_EVALS_PRIORITY": "2"}
    )
    assert s.vllm_origin == "http://127.0.0.1:8000"
    assert s.priority == 2
    with pytest.raises(ValidationError):
        s.priority = 3  # type: ignore[misc]
    with pytest.raises(SystemExit):
        load_settings({"QR_EVALS_MAX_INFLIGHT": "three"})
    with pytest.raises(SystemExit):
        load_settings({"QR_EVALS_KEY_VERIFY": "magic"})
    with pytest.raises(ValidationError):
        load_settings({"QR_EVALS_BIND_PORT": "0"})


def test_normalise_origin() -> None:
    assert normalise_origin("http://h:1/v1") == "http://h:1"
    assert normalise_origin("http://h:1///") == "http://h:1"
