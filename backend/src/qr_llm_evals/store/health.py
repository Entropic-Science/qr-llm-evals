"""What a run folder holds, per arm and card: the health summary.

:func:`summarise` reads the files of one run and counts, for every arm and
card pair, the completed samples, the samples without a completed reply, the
taints, the result-check flags and the judgements. It adds the run's halts and
the **cautions**: plain sentences about things a reader of the results should
know before trusting a comparison.

The summary is a reading aid. It changes no stored record, no analysis reads
it and no verdict depends on it: a flag is not an exclusion. It is computed
the same way for every study, with or without a design block, and it works on
a run that is still going (the readers skip a line that is being written).

It reads what is there and does not refuse a folder for one odd line. A taint
whose kind this harness does not know is counted under ``other``, a ``run.json``
whose state it does not know is reported as the state ``other``, and a row
filed under an arm or a card whose name is not a valid id is left out of the
table (a taint on such a sample still counts in ``tainted_samples``).

Layer 1: imports ``cards`` and ``models`` only, and reads raw rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from qr_llm_evals.cards.result_checks import (
    CAUTION_SHARE,
    FINAL_JUDGE_STATUSES,
    FLAG_KINDS,
    FlagKind,
    FlagPhase,
    stored_judge_status,
)
from qr_llm_evals.models.schema import ArmId, CardId, StudyConfig
from qr_llm_evals.store.artifacts import COMPLETE_FINISH_REASONS, iter_jsonl, read_json
from qr_llm_evals.store.bundle import load_frozen_study
from qr_llm_evals.store.paths import RunDir
from qr_llm_evals.store.schema import Halt, Progress, Run, RunState, TaintKind
from qr_llm_evals.store.store import RunStore

#: What the summary says about a run that has no ``checks_version``.
MADE_BEFORE_RESULT_CHECKS = (
    "This run was made before result checks, so no flags were recorded for it."
)

#: How an arm is named beside its id, by the source of its randomness.
ARM_ROLES: dict[str, str] = {
    "qrng": "quantum arm",
    "prng_seeded": "seeded pseudo-random arm",
    "prng_system": "system pseudo-random arm",
    "prng_epsilon": "biased pseudo-random control arm",
    "provider": "provider arm",
}
#: The role of an arm that has files in the run and is not in the frozen study.
UNKNOWN_ROLE = "arm not in the study"

#: ``no_score`` is a judge row from before rows had a status that has no score:
#: no reply came, or a reply came and no score could be read. The row cannot say which.
JudgementKey = Literal["ok", "failed", "unreadable", "out_of_range", "no_score"]
#: ``other`` is every taint line whose kind this harness does not know. Such a
#: line excludes its sample from the clean analysis like any other taint.
TaintKey = TaintKind | Literal["other"]
#: ``other`` is a ``run.json`` whose state this harness does not know.
SummaryState = RunState | Literal["other"]
#: The state a ``run.json`` with an unknown state is read as, so that the rest
#: of the record can be validated. It is never reported: the summary says ``other``.
_STAND_IN_STATE: RunState = "paused"
_TAINT_KINDS: frozenset[str] = frozenset(get_args(TaintKind))
_RUN_STATES: frozenset[str] = frozenset(get_args(RunState))
_ID: TypeAdapter[str] = TypeAdapter(ArmId)


def _is_id(name: object) -> bool:
    """True when ``name`` can be the id of an arm or a card."""
    try:
        _ID.validate_python(name)
    except ValidationError:
        return False
    return True


def _taint_key(kind: str) -> str:
    return kind if kind in _TAINT_KINDS else "other"


CautionKind = Literal["flag_share", "run_not_complete", "open_halt", "reader_versions"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlagLegend(_Model):
    """One flag kind as the catalogue describes it, so a page needs no text of its own."""

    kind: FlagKind
    phase: FlagPhase
    label: str
    sentence: str
    cautions: bool = Field(description="False for a kind that never raises a caution.")


class CellHealth(_Model):
    """The counts of one arm and card pair."""

    arm_id: ArmId
    card_id: CardId
    role: str = Field(description="What kind of arm this is, in words.")
    planned: int | None = Field(
        ge=0,
        description=(
            "Samples the runner planned for the pair, from the progress it recorded; "
            "empty when no plan was recorded."
        ),
    )
    completed: int = Field(ge=0, description="Samples that have a completed reply.")
    errored: int = Field(
        ge=0,
        description=(
            "Samples that were tried and have no completed reply: every attempt ended in an "
            "error or was cancelled."
        ),
    )
    taints: dict[TaintKey, int] = Field(
        description=(
            "Samples that carry a taint, by kind; `other` counts taints of a kind this "
            "harness does not know."
        )
    )
    flags: dict[FlagKind, int] | None = Field(
        description=(
            "Samples that were flagged, by kind; empty for a run made before result checks."
        ),
    )
    judgements: dict[JudgementKey, int] = Field(
        description="Samples by what became of their judgement; the latest row of a sample counts.",
    )


class CautionArm(_Model):
    """One arm's numbers in a caution about a flag."""

    arm_id: ArmId
    role: str
    count: int = Field(ge=0, description="Completed samples that carry the flag.")
    completed: int = Field(ge=0)
    share: float | None = Field(
        ge=0, le=1, description="count / completed; empty when nothing completed."
    )


class Caution(_Model):
    """One thing a reader should know before trusting a comparison. Never a verdict."""

    kind: CautionKind
    text: str = Field(description="The caution as one complete sentence.")
    card_id: CardId | None = Field(description="The card a flag caution is about.")
    flag: FlagKind | None = Field(description="The flag kind a flag caution is about.")
    arms: list[CautionArm] = Field(
        description=(
            "For a flag: every arm of the card side by side, also those below the line. "
            "Empty for any other caution."
        ),
    )


class HealthSummary(_Model):
    """``GET /api/runs/{id}/health``: what a run folder holds and what to be careful about."""

    run_id: str
    study_id: str
    state: SummaryState = Field(
        description="The run's state; `other` when run.json holds a state this harness does not know."
    )
    checks_version: int | None = Field(
        description="Version of the result checks the run started under."
    )
    note: str | None = Field(
        description="Set for a run made before result checks; it has no flag counts."
    )
    halts: list[Halt] = Field(description="Every stop of the run by a check, oldest first.")
    cells: list[CellHealth] = Field(description="One row for each arm and card pair.")
    taints: dict[TaintKey, int] = Field(
        description=(
            "Tainted samples and windows of the whole run, by kind; `other` counts taints "
            "of a kind this harness does not know."
        )
    )
    tainted_samples: int = Field(ge=0, description="Samples that carry any taint.")
    cautions: list[Caution]
    flag_kinds: list[FlagLegend] = Field(description="Every flag kind, in the catalogue's order.")


# ── reading ───────────────────────────────────────────────────────────────────


class _Cell:
    """What the files of one arm and card pair hold."""

    def __init__(self) -> None:
        self.completed: set[str] = set()
        self.tried: set[str] = set()
        self.taints: dict[str, set[str]] = {}
        self.flags: dict[str, set[str]] = {}
        #: The latest judge row of each sample, as (status key, reply received).
        self.judged: dict[str, tuple[str, bool]] = {}


def _cell_of(sample_ref: str) -> tuple[str, str] | None:
    """``(arm_id, card_id)`` of ``<arm_id>/<card_id>/<prompt_id>/<epoch>[/<told>]``."""
    parts = sample_ref.split("/")
    return (parts[0], parts[1]) if len(parts) >= 4 and parts[0] and parts[1] else None


def _judgement_key(row: Mapping[str, Any]) -> str:
    if row.get("status") is None and row.get("score") is None:
        return "no_score"
    return stored_judge_status(row)


def _read_cells(
    run_dir: RunDir, read_flags: bool
) -> tuple[dict[tuple[str, str], _Cell], dict[int | None, int]]:
    cells: dict[tuple[str, str], _Cell] = {}
    #: reader version -> judge rows whose reply was read with it
    readers: dict[int | None, int] = {}

    def cell(arm_id: str, card_id: str) -> _Cell:
        return cells.setdefault((arm_id, card_id), _Cell())

    if run_dir.arms_dir.is_dir():
        for card_path in sorted(run_dir.arms_dir.glob("*/cards/*")):
            if not card_path.is_dir():
                continue
            arm_id, card_id = card_path.parents[1].name, card_path.name
            here = cell(arm_id, card_id)
            for row in iter_jsonl(card_path / "generations.jsonl"):
                ref = row.get("sample_ref")
                if not isinstance(ref, str):
                    continue
                here.tried.add(ref)
                if row.get("finish_reason") in COMPLETE_FINISH_REASONS:
                    here.completed.add(ref)
            for judge_path in sorted((card_path / "judge").glob("*.jsonl")):
                for row in iter_jsonl(judge_path):
                    ref = row.get("sample_ref")
                    if not isinstance(ref, str):
                        continue
                    key = _judgement_key(row)
                    received = key in FINAL_JUDGE_STATUSES
                    here.judged[ref] = (key, received)
                    if received:
                        version = row.get("reader_version")
                        version = version if isinstance(version, int) else None
                        readers[version] = readers.get(version, 0) + 1

    for row in iter_jsonl(run_dir.taints_jsonl):
        ref, kind = row.get("sample_ref"), row.get("kind")
        if not isinstance(ref, str) or not isinstance(kind, str):
            continue
        where = _cell_of(ref)
        if where is not None:
            cell(*where).taints.setdefault(_taint_key(kind), set()).add(ref)

    if read_flags:
        for row in iter_jsonl(run_dir.flags_jsonl):
            ref, kind = row.get("sample_ref"), row.get("kind")
            flag_arm, flag_card = row.get("arm_id"), row.get("card_id")
            if (
                isinstance(ref, str)
                and kind in FLAG_KINDS
                and isinstance(flag_arm, str)
                and isinstance(flag_card, str)
            ):
                cell(flag_arm, flag_card).flags.setdefault(str(kind), set()).add(ref)
    return cells, readers


def _run_taints(run_dir: RunDir) -> tuple[dict[str, int], int]:
    """Tainted samples and windows by kind, and the number of tainted samples."""
    targets: dict[str, set[str]] = {}
    samples: set[str] = set()
    for row in iter_jsonl(run_dir.taints_jsonl):
        kind = row.get("kind")
        ref, window = row.get("sample_ref"), row.get("window_id")
        if not isinstance(kind, str):
            continue
        kind = _taint_key(kind)
        if isinstance(ref, str):
            samples.add(ref)
            targets.setdefault(kind, set()).add(f"sample:{ref}")
        elif isinstance(window, str):
            targets.setdefault(kind, set()).add(f"window:{window}")
    return {kind: len(found) for kind, found in sorted(targets.items())}, len(samples)


def _planned(progress: Iterable[Progress]) -> dict[tuple[str, str], int]:
    """The largest total the runner recorded for each pair; a pair without one is left out."""
    totals: dict[tuple[str, str], int] = {}
    for snapshot in progress:
        for arm_id, arm in snapshot.by_arm.items():
            for card_id, card in arm.by_card.items():
                if card.total > 0:
                    totals[(arm_id, card_id)] = max(card.total, totals.get((arm_id, card_id), 0))
    return totals


def _ordered(keys: Iterable[tuple[str, str]], study: StudyConfig | None) -> list[tuple[str, str]]:
    """Pairs in the study's own order of arms and cards; anything else after, by name."""
    arms = [arm.id for arm in study.arms] if study is not None else []
    cards = [selection.card_id for selection in study.cards] if study is not None else []

    def rank(names: list[str], name: str) -> tuple[int, str]:
        return (names.index(name), "") if name in names else (len(names), name)

    # A pair whose arm or card is not a valid id cannot be a row of the table.
    valid = {key for key in keys if _is_id(key[0]) and _is_id(key[1])}
    return sorted(valid, key=lambda key: (rank(arms, key[0]), rank(cards, key[1])))


def _percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def _flag_cautions(
    cells: Mapping[tuple[str, str], _Cell],
    order: list[tuple[str, str]],
    roles: Mapping[str, str],
) -> list[Caution]:
    cautions: list[Caution] = []
    card_ids = list(dict.fromkeys(card_id for _, card_id in order))
    for card_id in card_ids:
        arm_ids = [arm_id for arm_id, card in order if card == card_id]
        for spec in FLAG_KINDS.values():
            if not spec.cautions:
                continue
            arms: list[CautionArm] = []
            for arm_id in arm_ids:
                here = cells[(arm_id, card_id)]
                flagged = here.flags.get(spec.kind, set()) & here.completed
                if spec.kind == "judge_failed":
                    # A judgement that failed and was asked again with a reply is no longer missing.
                    flagged = {ref for ref in flagged if not here.judged.get(ref, ("", False))[1]}
                done = len(here.completed)
                arms.append(
                    CautionArm(
                        arm_id=arm_id,
                        role=roles.get(arm_id, UNKNOWN_ROLE),
                        count=len(flagged),
                        completed=done,
                        share=len(flagged) / done if done else None,
                    )
                )
            if not any(arm.share is not None and arm.share >= CAUTION_SHARE for arm in arms):
                continue
            parts = "; ".join(
                f"arm {arm.arm_id} ({arm.role}) {arm.count} of {arm.completed}"
                + (f" ({_percent(arm.share)})" if arm.share is not None else "")
                for arm in arms
            )
            cautions.append(
                Caution(
                    kind="flag_share",
                    text=(
                        f'On card {card_id}, the flag "{spec.label}" is on at least '
                        f"{_percent(CAUTION_SHARE)} of an arm's completed samples: {parts}."
                    ),
                    card_id=card_id,
                    flag=spec.kind,
                    arms=arms,
                )
            )
    return cautions


def _plain_caution(kind: CautionKind, text: str) -> Caution:
    """A caution about the run as a whole, with no card, flag or arms."""
    return Caution(kind=kind, text=text, card_id=None, flag=None, arms=[])


def _reader_caution(readers: Mapping[int | None, int]) -> Caution | None:
    if len(readers) < 2:
        return None
    parts = ", ".join(
        f"{count} by "
        + ("a harness from before result checks" if version is None else f"version {version}")
        for version, count in sorted(
            readers.items(), key=lambda item: (item[0] is not None, item[0] or 0)
        )
    )
    return _plain_caution(
        "reader_versions",
        "The judge replies of this run were read by more than one version of the judge "
        f"reader ({parts}), so their scores were not all read by the same rules.",
    )


# ── the summary ───────────────────────────────────────────────────────────────


def summarise_run_dir(
    run_dir: RunDir,
    *,
    study: StudyConfig | None = None,
    progress: Progress | None = None,
) -> HealthSummary:
    """Summarise a run folder.

    ``study`` gives the order of arms and cards and the role of each arm; without
    it the pairs are listed by name and every arm is :data:`UNKNOWN_ROLE`.
    ``progress`` is the live progress of the run (the SQLite index), used beside
    the snapshot in ``run.json`` for the planned counts.
    """
    raw = read_json(run_dir.run_json)
    state: str = "other"
    if isinstance(raw, dict):
        stored = raw.get("state", "created")  # the record's own default
        if isinstance(stored, str) and stored in _RUN_STATES:
            state = stored
        else:
            raw = {**raw, "state": _STAND_IN_STATE}
    run = Run.model_validate(raw)
    checked = run.checks_version is not None
    cells, readers = _read_cells(run_dir, read_flags=checked)
    planned = _planned([run.progress] if progress is None else [run.progress, progress])
    order = _ordered([*cells, *planned], study)
    for key in order:
        cells.setdefault(key, _Cell())
    roles = {arm.id: ARM_ROLES[arm.source] for arm in study.arms} if study is not None else {}

    cell_rows: list[CellHealth] = []
    for arm_id, card_id in order:
        here = cells[(arm_id, card_id)]
        judgements: dict[str, int] = {}
        for status, _ in here.judged.values():
            judgements[status] = judgements.get(status, 0) + 1
        cell_rows.append(
            CellHealth.model_validate(
                {
                    "arm_id": arm_id,
                    "card_id": card_id,
                    "role": roles.get(arm_id, UNKNOWN_ROLE),
                    "planned": planned.get((arm_id, card_id)),
                    "completed": len(here.completed),
                    "errored": len(here.tried - here.completed),
                    "taints": {kind: len(refs) for kind, refs in sorted(here.taints.items())},
                    "flags": (
                        {kind: len(here.flags[kind]) for kind in FLAG_KINDS if kind in here.flags}
                        if checked
                        else None
                    ),
                    "judgements": dict(sorted(judgements.items())),
                }
            )
        )

    cautions: list[Caution] = []
    if state != "complete":
        named = "a state this harness does not know" if state == "other" else f"the state {state}"
        cautions.append(
            _plain_caution(
                "run_not_complete",
                f"The run is in {named}, not complete: the counts describe what is stored so far.",
            )
        )
    for halt in run.open_halts():
        cautions.append(
            _plain_caution(
                "open_halt",
                f"A check stopped the run and the run has not been resumed. {halt.sentence}",
            )
        )
    if checked:
        cautions.extend(_flag_cautions(cells, order, roles))
    reader_caution = _reader_caution(readers)
    if reader_caution is not None:
        cautions.append(reader_caution)

    taints, tainted_samples = _run_taints(run_dir)
    return HealthSummary.model_validate(
        {
            "run_id": run.run_id,
            "study_id": run.study_id,
            "state": state,
            "checks_version": run.checks_version,
            "note": None if checked else MADE_BEFORE_RESULT_CHECKS,
            "halts": run.halts,
            "cells": cell_rows,
            "taints": taints,
            "tainted_samples": tainted_samples,
            "cautions": cautions,
            "flag_kinds": [
                FlagLegend(
                    kind=spec.kind,
                    phase=spec.phase,
                    label=spec.label,
                    sentence=spec.sentence,
                    cautions=spec.cautions,
                )
                for spec in FLAG_KINDS.values()
            ],
        }
    )


def summarise(run_store: RunStore) -> HealthSummary:
    """The health summary of one run, read from its files.

    The study gives the arms' roles and the order of the table. The planned
    counts come from the progress the runner records: the store cannot build a
    card's items itself. A run that never recorded a plan has no planned counts.
    """
    try:
        study: StudyConfig | None = load_frozen_study(run_store.study_dir)
    except (OSError, ValueError, KeyError):
        study = None  # a run folder read without its study still has its counts
    try:
        progress: Progress | None = run_store.progress()
    except Exception:  # noqa: BLE001 - the index is a convenience; the files are the record
        progress = None
    return summarise_run_dir(run_store.run_dir, study=study, progress=progress)


__all__ = [
    "ARM_ROLES",
    "MADE_BEFORE_RESULT_CHECKS",
    "UNKNOWN_ROLE",
    "Caution",
    "CautionArm",
    "CautionKind",
    "CellHealth",
    "FlagLegend",
    "HealthSummary",
    "JudgementKey",
    "SummaryState",
    "TaintKey",
    "summarise",
    "summarise_run_dir",
]
