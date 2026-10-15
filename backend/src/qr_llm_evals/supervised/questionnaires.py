"""Frozen questionnaire definitions, answer validation and session gates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from qr_llm_evals.supervised.schema import AnswerValue, Questionnaire, QuestionnairePhase

ItemKind = Literal["scale", "text"]


class QuestionnaireItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    prompt: str = Field(min_length=1)
    kind: ItemKind
    min: int | None = None
    max: int | None = None
    required: bool = True

    @model_validator(mode="after")
    def _scale_bounds(self) -> QuestionnaireItem:
        if self.kind == "scale" and (self.min is None or self.max is None or self.min >= self.max):
            raise ValueError("scale items require min < max")
        if self.kind == "text" and (self.min is not None or self.max is not None):
            raise ValueError("text items cannot have scale bounds")
        return self


class QuestionnaireConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(ge=1)
    pre: list[QuestionnaireItem] = Field(min_length=1)
    post: list[QuestionnaireItem] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class LoadedQuestionnaires:
    config: QuestionnaireConfig
    sha256: str


def load_questionnaires(path: Path) -> LoadedQuestionnaires:
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    parsed: Any = yaml.safe_load(raw.decode("utf-8"))
    return LoadedQuestionnaires(
        config=QuestionnaireConfig.model_validate(parsed),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def validate_answers(
    config: QuestionnaireConfig, phase: QuestionnairePhase, answers: dict[str, AnswerValue]
) -> None:
    items = config.pre if phase == "pre" else config.post
    known = {item.id: item for item in items}
    unknown = sorted(set(answers) - set(known))
    if unknown:
        raise ValueError(f"unknown {phase} questionnaire items: {unknown}")
    missing = [item.id for item in items if item.required and item.id not in answers]
    if missing:
        raise ValueError(f"missing required {phase} questionnaire items: {missing}")
    for item_id, value in answers.items():
        item = known[item_id]
        if item.kind == "scale":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{item_id} must be numeric")
            low, high = item.min, item.max
            if low is None or high is None:
                raise ValueError(f"{item_id} has invalid scale bounds")
            if not low <= value <= high:
                raise ValueError(f"{item_id} must be between {low} and {high}")
        elif not isinstance(value, str):
            raise ValueError(f"{item_id} must be text")


class QuestionnaireGate:
    """Tracks submissions and explicit departures without storing answer values."""

    def __init__(self) -> None:
        self._submitted: set[tuple[str, str, QuestionnairePhase]] = set()
        self._left: set[str] = set()

    def record(self, questionnaire: Questionnaire) -> None:
        key = (questionnaire.observer_id, questionnaire.session_id, questionnaire.phase)
        if key in self._submitted:
            raise ValueError("questionnaire phase already submitted")
        self._submitted.add(key)

    def mark_left(self, observer_id: str) -> None:
        self._left.add(observer_id)

    def mark_present(self, observer_id: str) -> None:
        self._left.discard(observer_id)

    def missing(
        self, session_id: str, phase: QuestionnairePhase, observer_ids: set[str]
    ) -> set[str]:
        active = observer_ids - self._left
        return {
            observer_id
            for observer_id in active
            if (observer_id, session_id, phase) not in self._submitted
        }

    def require(self, session_id: str, phase: QuestionnairePhase, observer_ids: set[str]) -> None:
        missing = self.missing(session_id, phase, observer_ids)
        if missing:
            raise ValueError(f"{phase} questionnaire required for observers: {sorted(missing)}")


__all__ = [
    "LoadedQuestionnaires",
    "QuestionnaireConfig",
    "QuestionnaireGate",
    "QuestionnaireItem",
    "load_questionnaires",
    "validate_answers",
]
