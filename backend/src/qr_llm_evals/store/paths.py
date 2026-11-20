"""Bundle layout under ``QR_EVALS_DATA_DIR`` (spec §4.5) as typed path helpers.

::

    <root>/
    ├── index.sqlite                      the SQLite index (rebuildable; never exported)
    ├── _quarantine/<date>/…              invalidated artifacts, moved never deleted
    └── studies/<study_id>/
        ├── study.json · prereg.md · prereg.sha256 · expectations.md · LICENSE-data · README.md
        └── runs/<run_id>/
            ├── run.json · readiness.json · taints.jsonl
            ├── arms/<arm_id>/cards/<card_id>/{generations.jsonl, tier0.jsonl, judge/, scores/, inspect/}
            ├── sessions/<session_id>.json · questionnaires.jsonl · observers.jsonl
            └── analysis/<analysis_id>/{plan.json, tables/, figures/, narrative.md, manifest.json}

Every identifier that becomes a path segment goes through :func:`safe_segment`,
so a crafted ``run_id`` such as ``../../etc`` can never escape the data root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

INDEX_DB_NAME = "index.sqlite"
STUDIES_DIR = "studies"
QUARANTINE_DIR = "_quarantine"

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe_segment(value: str, *, what: str = "identifier") -> str:
    """Return ``value`` if it is a safe single path segment, else raise ``ValueError``."""
    if not _SEGMENT.match(value) or value in {".", ".."} or ".." in value:
        raise ValueError(f"{what} {value!r} is not a safe path segment")
    return value


@dataclass(frozen=True, slots=True)
class ArmCardDir:
    """``runs/<run_id>/arms/<arm_id>/cards/<card_id>/`` — one JSONL family per engine phase."""

    path: Path

    @property
    def generations_jsonl(self) -> Path:
        return self.path / "generations.jsonl"

    @property
    def tier0_jsonl(self) -> Path:
        return self.path / "tier0.jsonl"

    @property
    def judge_dir(self) -> Path:
        return self.path / "judge"

    def judge_jsonl(self, judge_id: str) -> Path:
        return self.judge_dir / f"{safe_segment(judge_id, what='judge_id')}.jsonl"

    @property
    def scores_dir(self) -> Path:
        return self.path / "scores"

    def scores_jsonl(self, scorer: str) -> Path:
        return self.scores_dir / f"{safe_segment(scorer, what='scorer')}.jsonl"

    @property
    def inspect_dir(self) -> Path:
        return self.path / "inspect"

    def inspect_log(self, name: str) -> Path:
        return self.inspect_dir / f"{safe_segment(name, what='inspect log name')}.eval"


@dataclass(frozen=True, slots=True)
class RunDir:
    """``studies/<study_id>/runs/<run_id>/``."""

    path: Path

    @property
    def run_json(self) -> Path:
        return self.path / "run.json"

    @property
    def readiness_json(self) -> Path:
        return self.path / "readiness.json"

    @property
    def request_settings_json(self) -> Path:
        """Deployment request settings the run actually used (e.g. ``top_logprobs``)."""
        return self.path / "request_settings.json"

    @property
    def taints_jsonl(self) -> Path:
        return self.path / "taints.jsonl"

    @property
    def withdrawn_md(self) -> Path:
        return self.path / "WITHDRAWN.md"

    @property
    def arms_dir(self) -> Path:
        return self.path / "arms"

    def arm_card(self, arm_id: str, card_id: str) -> ArmCardDir:
        return ArmCardDir(
            self.arms_dir
            / safe_segment(arm_id, what="arm_id")
            / "cards"
            / safe_segment(card_id, what="card_id")
        )

    @property
    def sessions_dir(self) -> Path:
        return self.path / "sessions"

    def session_json(self, session_id: str) -> Path:
        return self.sessions_dir / f"{safe_segment(session_id, what='session_id')}.json"

    @property
    def questionnaires_jsonl(self) -> Path:
        return self.path / "questionnaires.jsonl"

    @property
    def observers_jsonl(self) -> Path:
        return self.path / "observers.jsonl"

    @property
    def analysis_root(self) -> Path:
        return self.path / "analysis"

    def analysis_dir(self, analysis_id: str) -> Path:
        return self.analysis_root / safe_segment(analysis_id, what="analysis_id")


@dataclass(frozen=True, slots=True)
class StudyDir:
    """``studies/<study_id>/`` — the frozen study and everything exported in its bundle."""

    path: Path

    @property
    def study_id(self) -> str:
        return self.path.name

    @property
    def study_json(self) -> Path:
        return self.path / "study.json"

    @property
    def prereg_md(self) -> Path:
        return self.path / "prereg.md"

    @property
    def prereg_sha256(self) -> Path:
        return self.path / "prereg.sha256"

    @property
    def expectations_md(self) -> Path:
        return self.path / "expectations.md"

    @property
    def licence_data(self) -> Path:
        return self.path / "LICENSE-data"

    @property
    def readme_md(self) -> Path:
        return self.path / "README.md"

    @property
    def runs_dir(self) -> Path:
        return self.path / "runs"

    def run(self, run_id: str) -> RunDir:
        return RunDir(self.runs_dir / safe_segment(run_id, what="run_id"))

    def run_ids(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(p.name for p in self.runs_dir.iterdir() if (p / "run.json").is_file())


@dataclass(frozen=True, slots=True)
class DataRoot:
    """``QR_EVALS_DATA_DIR`` (default ``/var/lib/qr-llm-evals``)."""

    path: Path

    @property
    def index_db(self) -> Path:
        return self.path / INDEX_DB_NAME

    @property
    def studies_dir(self) -> Path:
        return self.path / STUDIES_DIR

    @property
    def quarantine_dir(self) -> Path:
        return self.path / QUARANTINE_DIR

    def study(self, study_id: str) -> StudyDir:
        return StudyDir(self.studies_dir / safe_segment(study_id, what="study_id"))

    def study_ids(self) -> list[str]:
        if not self.studies_dir.is_dir():
            return []
        return sorted(p.name for p in self.studies_dir.iterdir() if (p / "study.json").is_file())

    def contains(self, path: Path) -> bool:
        """True if ``path`` resolves inside the data root (used by ``quarantine``)."""
        try:
            path.resolve().relative_to(self.path.resolve())
        except ValueError:
            return False
        return True


__all__ = [
    "INDEX_DB_NAME",
    "QUARANTINE_DIR",
    "STUDIES_DIR",
    "ArmCardDir",
    "DataRoot",
    "RunDir",
    "StudyDir",
    "safe_segment",
]
