"""Supervised-mode records: sessions, windows, observers, questionnaires (spec §4.4, §2.9).

A :class:`Session` is one timed sitting (target ≈ 30 min, overrun expected and
logged); its :class:`Window` list is the exact timeline of supervised / break /
refocus intervals with who was present in each, which is what the analysis
joins generations and Tier 0 records against (via ``window`` tags). Observers
are pseudonymous (D-13); questionnaires are per observer per session per phase.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from qr_llm_evals.models.schema import Display, Sha256Hex
from qr_llm_evals.store.schema import Identifier

WindowKind = Literal["supervised", "break", "refocus"]
QuestionnairePhase = Literal["pre", "post"]
#: How the T4-m target was drawn (spec §2.9): a QRNG uniform from a Tier 0
#: record, or ``secrets.randbelow`` when the engine log is unavailable.
DrawSource = Literal["qrng_tier0", "secrets"]
DisplayName = Annotated[str, Field(min_length=1, max_length=64)]
AnswerValue = int | float | str | bool


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObserverPresence(_Record):
    observer_id: Identifier
    name: DisplayName


class Window(_Record):
    window_id: Identifier
    kind: WindowKind
    start: AwareDatetime
    end: AwareDatetime | None = None
    observers_present: list[ObserverPresence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_order(self) -> Window:
        if self.end is not None and self.end < self.start:
            raise ValueError("window end precedes start")
        return self


class Intention(_Record):
    text: str = Field(min_length=1)
    hash: Sha256Hex
    at: AwareDatetime


class TargetDraw(_Record):
    """T4-m target theme with decoys; shown to observers only (FR-5.8)."""

    theme: str = Field(min_length=1)
    decoys: list[str] = Field(min_length=1)
    draw_source: DrawSource


class Session(_Record):
    """``runs/<run_id>/sessions/<session_id>.json``."""

    session_id: Identifier
    run_id: Identifier
    index: int = Field(ge=0)
    display: Display
    target_s: int = Field(ge=1, description="Planned session length in seconds.")
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    overrun_s: float = Field(default=0.0, description="Actual minus planned; may be negative.")
    intention: Intention | None = None
    target: TargetDraw | None = None
    windows: list[Window] = Field(default_factory=list)
    host_observer_id: Identifier


class Observer(_Record):
    """One line of ``observers.jsonl`` (pseudonymous, spec D-13)."""

    observer_id: Identifier
    run_id: Identifier
    display_name: DisplayName
    joined_at: AwareDatetime
    consent_at: AwareDatetime | None = None


class Questionnaire(_Record):
    """One line of ``questionnaires.jsonl``; items are frozen by ``items_hash``."""

    observer_id: Identifier
    session_id: Identifier
    phase: QuestionnairePhase
    items_hash: Sha256Hex
    answers: dict[str, AnswerValue] = Field(default_factory=dict)
    free_text: str | None = None
    at: AwareDatetime


__all__ = [
    "AnswerValue",
    "DisplayName",
    "DrawSource",
    "Intention",
    "Observer",
    "ObserverPresence",
    "Questionnaire",
    "QuestionnairePhase",
    "Session",
    "TargetDraw",
    "Window",
    "WindowKind",
]
