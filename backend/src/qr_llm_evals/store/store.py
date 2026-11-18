"""``Store`` (one data root) and ``RunStore`` (one run) - the façade the runner,
supervised mode, analysis and API use (spec D-4, §2.5, §4.5).

A ``Store`` wraps ``QR_EVALS_DATA_DIR``: it freezes studies, creates runs,
streams bundles and quarantines. A ``RunStore`` is bound to
``studies/<study_id>/runs/<run_id>/`` and owns the append-only writers plus
the resume set. Both keep the SQLite index in step with what they write, but
the files are canonical: :meth:`Store.reindex_run` rebuilds the index rows of a
run from disk.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from qr_llm_evals.config import Settings
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.store.artifacts import (
    Row,
    RowLike,
    append_jsonl,
    completed_judge_refs,
    completed_refs,
    dump_row,
    iter_jsonl,
    read_json,
    write_json,
)
from qr_llm_evals.store.bundle import (
    iter_bundle,
    load_frozen_study,
    write_bundle,
    write_frozen_study,
)
from qr_llm_evals.store.db import Index
from qr_llm_evals.store.paths import ArmCardDir, DataRoot, RunDir, StudyDir
from qr_llm_evals.store.quarantine import quarantine, withdraw
from qr_llm_evals.store.schema import (
    ArmProgress,
    CardProgress,
    GenerationRow,
    JudgeRow,
    Progress,
    ReadinessReport,
    Run,
    Taint,
    Usage,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageNote:
    """Token usage of one persisted generation or judge row (for accounting)."""

    run_id: str
    kind: Literal["generation", "judge"]
    arm_id: str
    card_id: str
    model: str
    source_label: str | None
    pipeline_pinned: bool
    usage: Usage
    at: datetime


UsageHook = Callable[[UsageNote], None]


class RunStore:
    """Writers/readers for one run directory; created via :meth:`Store.create_run` / :meth:`Store.run`."""

    def __init__(self, store: Store, study_dir: StudyDir, run_dir: RunDir) -> None:
        self.store = store
        self.study_dir = study_dir
        self.run_dir = run_dir

    @property
    def run_id(self) -> str:
        return self.run_dir.path.name

    @property
    def study_id(self) -> str:
        return self.study_dir.study_id

    @property
    def index(self) -> Index:
        return self.store.index

    def arm_card(self, arm_id: str, card_id: str) -> ArmCardDir:
        return self.run_dir.arm_card(arm_id, card_id)

    # -- run summary -------------------------------------------------------

    def write_run(self, run: Run) -> None:
        if run.run_id != self.run_id or run.study_id != self.study_id:
            raise ValueError(
                f"run record {run.study_id}/{run.run_id} does not belong to "
                f"{self.study_id}/{self.run_id}"
            )
        write_json(self.run_dir.run_json, run)
        self.index.upsert_run(run)

    def read_run(self) -> Run:
        return Run.model_validate(read_json(self.run_dir.run_json))

    def write_readiness(self, report: ReadinessReport) -> None:
        write_json(self.run_dir.readiness_json, report)

    def read_readiness(self) -> ReadinessReport | None:
        if not self.run_dir.readiness_json.is_file():
            return None
        return ReadinessReport.model_validate(read_json(self.run_dir.readiness_json))

    # -- generation phase (append-only) ------------------------------------

    def append_generation(self, row: GenerationRow) -> None:
        append_jsonl(self.arm_card(row.arm_id, row.card_id).generations_jsonl, row)
        self.store.notify_usage(
            UsageNote(
                run_id=self.run_id,
                kind="generation",
                arm_id=row.arm_id,
                card_id=row.card_id,
                model=row.provenance.served_name,
                source_label=row.provenance.source_label,
                pipeline_pinned=row.provenance.pipeline_pinned,
                usage=row.usage,
                at=row.timing.finished or row.timing.started,
            )
        )

    def append_tier0(self, arm_id: str, card_id: str, records: Iterable[RowLike]) -> int:
        return append_jsonl(self.arm_card(arm_id, card_id).tier0_jsonl, records)

    def append_judge(self, arm_id: str, card_id: str, row: JudgeRow) -> None:
        append_jsonl(self.arm_card(arm_id, card_id).judge_jsonl(row.judge_id), row)
        self.store.notify_usage(
            UsageNote(
                run_id=self.run_id,
                kind="judge",
                arm_id=arm_id,
                card_id=card_id,
                model=row.model,
                source_label=None,
                pipeline_pinned=False,
                usage=row.usage,
                at=row.at,
            )
        )

    def append_score(self, arm_id: str, card_id: str, scorer: str, row: RowLike) -> None:
        append_jsonl(self.arm_card(arm_id, card_id).scores_jsonl(scorer), row)

    def append_taint(self, taint: Taint) -> None:
        append_jsonl(self.run_dir.taints_jsonl, taint)
        self.index.insert_taint(self.run_id, taint)

    def taints(self) -> list[Taint]:
        return [Taint.model_validate(r) for r in iter_jsonl(self.run_dir.taints_jsonl)]

    # -- reads -------------------------------------------------------------

    def iter_generation_rows(self, arm_id: str, card_id: str) -> Iterator[Row]:
        return iter_jsonl(self.arm_card(arm_id, card_id).generations_jsonl)

    def generations(self, arm_id: str, card_id: str) -> list[GenerationRow]:
        return [GenerationRow.model_validate(r) for r in self.iter_generation_rows(arm_id, card_id)]

    def iter_tier0_rows(self, arm_id: str, card_id: str) -> Iterator[Row]:
        return iter_jsonl(self.arm_card(arm_id, card_id).tier0_jsonl)

    def judge_rows(self, arm_id: str, card_id: str, judge_id: str) -> list[JudgeRow]:
        path = self.arm_card(arm_id, card_id).judge_jsonl(judge_id)
        return [JudgeRow.model_validate(r) for r in iter_jsonl(path)]

    def score_rows(self, arm_id: str, card_id: str, scorer: str) -> list[Row]:
        return list(iter_jsonl(self.arm_card(arm_id, card_id).scores_jsonl(scorer)))

    # -- resume (spec §2.5) ------------------------------------------------

    def completed_refs(self, arm_id: str, card_id: str) -> set[str]:
        """``sample_ref``s already generated for ``(arm, card)``; the runner skips them."""
        return completed_refs(self.arm_card(arm_id, card_id).generations_jsonl)

    def completed_judge_refs(self, arm_id: str, card_id: str, judge_id: str) -> set[str]:
        return completed_judge_refs(self.arm_card(arm_id, card_id).judge_jsonl(judge_id))

    # -- progress ----------------------------------------------------------

    def set_progress(
        self, arm_id: str, card_id: str, *, done: int, total: int, aborted: int = 0
    ) -> None:
        self.index.upsert_progress(
            self.run_id, arm_id, card_id, done=done, total=total, aborted=aborted
        )

    def progress(self) -> Progress:
        by_arm = {
            arm: ArmProgress(by_card={card: CardProgress(**vals) for card, vals in cards.items()})
            for arm, cards in self.index.progress(self.run_id).items()
        }
        return Progress(by_arm=by_arm)

    # -- supervised mode (layer 2 passes its own records; store stays layer 1)

    def write_session(self, session: RowLike) -> None:
        data = dump_row(session)
        if data.get("run_id") != self.run_id:
            raise ValueError("session does not belong to this run")
        write_json(self.run_dir.session_json(str(data["session_id"])), data)
        self.index.upsert_session(data)

    def read_session(self, session_id: str) -> Row | None:
        path = self.run_dir.session_json(session_id)
        return read_json(path) if path.is_file() else None

    def append_observer(self, observer: RowLike) -> None:
        data = dump_row(observer)
        if data.get("run_id") != self.run_id:
            raise ValueError("observer does not belong to this run")
        append_jsonl(self.run_dir.observers_jsonl, data)
        self.index.upsert_observer(data)

    def append_questionnaire(self, questionnaire: RowLike) -> None:
        data = dump_row(questionnaire)
        append_jsonl(self.run_dir.questionnaires_jsonl, data)
        self.index.insert_questionnaire(self.run_id, data)

    # -- invalidation ------------------------------------------------------

    def withdraw(self, reason: str, *, now: datetime | None = None) -> Run:
        return withdraw(self.run_dir, reason, index=self.index, now=now)


class Store:
    """Everything under ``QR_EVALS_DATA_DIR`` (spec §4.5)."""

    def __init__(self, data_dir: str | Path) -> None:
        self.root = DataRoot(Path(data_dir))
        self._index: Index | None = None
        #: Called after every generation / judge row is appended (token accounting).
        self.usage_hook: UsageHook | None = None

    def notify_usage(self, note: UsageNote) -> None:
        """Tell the hook about a row that was just persisted; never raises.

        The row is already on disk and is the record of truth. A failing
        accounting hook must not fail, or roll back, a paid-for generation.
        """
        hook = self.usage_hook
        if hook is None:
            return
        try:
            hook(note)
        except Exception:
            log.exception(
                "usage hook failed", extra={"event": "store.usage_hook", "run_id": note.run_id}
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> Store:
        return cls(settings.data_dir)

    @property
    def index(self) -> Index:
        if self._index is None:
            self._index = Index(self.root.index_db)
        return self._index

    def close(self) -> None:
        if self._index is not None:
            self._index.close()
            self._index = None

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- studies -----------------------------------------------------------

    def freeze_study(
        self,
        study: StudyConfig,
        prereg_md: str,
        *,
        study_id: str | None = None,
        frozen_at: datetime | None = None,
    ) -> StudyDir:
        """Write the frozen study files (bundle root) and index the study."""
        study_dir = write_frozen_study(
            self.root, study, prereg_md, study_id=study_id, frozen_at=frozen_at
        )
        meta = read_json(study_dir.study_json)
        self.index.upsert_study(
            study_dir.study_id,
            title=study.title,
            config_hash=str(meta["config_hash"]),
            prereg_hash=str(meta["prereg_hash"]),
            created_at=study.created_at.isoformat(),
        )
        return study_dir

    def study(self, study_id: str) -> StudyDir:
        study_dir = self.root.study(study_id)
        if not study_dir.study_json.is_file():
            raise FileNotFoundError(f"study {study_id!r} is not frozen")
        return study_dir

    def load_study(self, study_id: str) -> StudyConfig:
        return load_frozen_study(self.study(study_id))

    def study_ids(self) -> list[str]:
        return self.root.study_ids()

    # -- runs --------------------------------------------------------------

    def create_run(self, run: Run) -> RunStore:
        """Create ``runs/<run_id>/`` for a frozen study; refuses an existing run id."""
        study_dir = self.study(run.study_id)
        run_dir = study_dir.run(run.run_id)
        if run_dir.run_json.exists():
            raise FileExistsError(
                f"run {run.run_id!r} already exists under study {run.study_id!r}; "
                "resume it with Store.run() instead of recreating it"
            )
        run_dir.path.mkdir(parents=True, exist_ok=True)
        rs = RunStore(self, study_dir, run_dir)
        rs.write_run(run)
        return rs

    def run(self, study_id: str, run_id: str) -> RunStore:
        """Open an existing run (resume path)."""
        study_dir = self.study(study_id)
        run_dir = study_dir.run(run_id)
        if not run_dir.run_json.is_file():
            raise FileNotFoundError(f"run {run_id!r} not found under study {study_id!r}")
        return RunStore(self, study_dir, run_dir)

    def find_run(self, run_id: str) -> RunStore | None:
        """Locate a run by id via the index (falls back to scanning the studies)."""
        indexed = self.index.get_run(run_id)
        if indexed is not None:
            try:
                return self.run(str(indexed["study_id"]), run_id)
            except FileNotFoundError:
                pass
        for sid in self.study_ids():
            run_dir = self.root.study(sid).run(run_id)
            if run_dir.run_json.is_file():
                return RunStore(self, self.root.study(sid), run_dir)
        return None

    def list_runs(self, *, study_id: str | None = None, state: str | None = None) -> list[Run]:
        return [Run.model_validate(r) for r in self.index.list_runs(study_id=study_id, state=state)]

    def reindex_run(self, study_id: str, run_id: str) -> RunStore:
        """Rebuild every index row of a run from its files (the files are canonical)."""
        rs = self.run(study_id, run_id)
        run = rs.read_run()
        self.index.upsert_run(run)
        self.index.clear_run(run_id)
        for taint in iter_jsonl(rs.run_dir.taints_jsonl):
            self.index.insert_taint(run_id, taint)
        for arm_id, cards in run.progress.by_arm.items():
            for card_id, p in cards.by_card.items():
                self.index.upsert_progress(
                    run_id, arm_id, card_id, done=p.done, total=p.total, aborted=p.aborted
                )
        if rs.run_dir.sessions_dir.is_dir():
            for path in sorted(rs.run_dir.sessions_dir.glob("*.json")):
                self.index.upsert_session(read_json(path))
        for observer in iter_jsonl(rs.run_dir.observers_jsonl):
            self.index.upsert_observer(observer)
        for q in iter_jsonl(rs.run_dir.questionnaires_jsonl):
            self.index.insert_questionnaire(run_id, q)
        return rs

    # -- export / invalidation ---------------------------------------------

    def export_bundle(self, study_id: str) -> Iterator[bytes]:
        """``tar.zst`` chunks of ``studies/<study_id>/`` with provider metadata stripped."""
        return iter_bundle(self.root, study_id)

    def write_bundle(self, study_id: str, dest: Path) -> Path:
        return write_bundle(self.root, study_id, dest)

    def quarantine(self, path: Path, reason: str, *, now: datetime | None = None) -> Path:
        """Move an invalidated artifact to ``_quarantine/<date>/``; never deletes."""
        return quarantine(self.root, path, reason, now=now)

    def withdraw(self, run_id: str, reason: str, *, now: datetime | None = None) -> Run:
        rs = self.find_run(run_id)
        if rs is None:
            raise FileNotFoundError(f"run {run_id!r} not found")
        return rs.withdraw(reason, now=now)


__all__ = ["RunStore", "Store", "UsageHook", "UsageNote"]
