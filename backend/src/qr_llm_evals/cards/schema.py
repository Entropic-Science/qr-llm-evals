"""Pydantic model of an eval card, ``evals/<id>.yaml`` (spec §4.3).

The schema is deliberately strict (``extra="forbid"``, closed enums) so that a
mistyped field or an inadmissible value such as ``generation: forced_choice``
fails at parse time. Rules that span several fields (k floor, judge settings,
estimate arithmetic, status/selectable coupling) live in
:mod:`qr_llm_evals.cards.validate`.

``scripts/export_schemas.py`` exports this model as JSON Schema for the
``@qr-evals/contracts`` package; keep field names stable.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CardId = Annotated[str, Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=64)]
EndpointId = Annotated[str, Field(pattern=r"^[a-z0-9_]+$", min_length=1)]

Tier = Literal[0, 1, 2, 3, 4]
Family = Literal[
    "distribution",
    "diversity",
    "self_report",
    "preference",
    "synchronicity",
    "alignment",
    "capability",
]
Engine = Literal["thin", "inspect"]
#: ``forced_choice`` / ``logprob`` are inadmissible (registry B.1 rule 1) and are
#: therefore not even representable.
Generation = Literal["free", "short"]
Sensitivity = Literal["native", "high", "moderate", "low"]
JudgeKind = Literal["none", "llm", "classifier"]
RequiresTier0 = Literal["none", "engine_log", "api_logprobs"]
PairedStat = Literal["permutation_within_prompt", "paired_binomial", "none"]
Status = Literal["implemented", "planned"]

#: Per arm, a told/actual card runs told ∈ {qrng, prng, nothing}; the *actual*
#: factor is the arm itself (spec §4.3).
TOLD_CELLS = 3

#: Dataset source schemes. ``hf:`` and ``git:`` need a pinned ``revision`` once
#: the card is implemented; ``repo:`` prompts ship in this repository and are
#: hashed into the prereg; ``derived:`` cards consume another card's samples,
#: the Tier 0 token log or supervised-session records and generate nothing new.
DATASET_SCHEMES: tuple[str, ...] = ("hf", "git", "repo", "derived")
REVISION_REQUIRED_SCHEMES: frozenset[str] = frozenset({"hf", "git"})

#: The literal ``judge.model`` value meaning "the study's shared judge"
#: (``StudyConfig.judge``, Anthropic Messages API at temperature 0, spec D-9).
STUDY_JUDGE = "study_judge"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Subsample(_Strict):
    """How the pinned dataset is cut down for a run (frozen in the prereg)."""

    n: int = Field(ge=1, description="Number of items drawn from the pinned dataset.")
    stratify: str | None = Field(default=None, description="Column to stratify the draw on.")


class Dataset(_Strict):
    source: str = Field(
        pattern=r"^(hf|git|repo|derived):.+$",
        description=(
            "Prompt source: hf:<dataset>, git:<repo>, repo:<path in this repo>, derived:<what>."
        ),
    )
    revision: str | None = Field(
        default=None,
        description="Pinned dataset revision / commit; required for hf:/git: once implemented.",
    )
    items: int = Field(ge=0, description="Prompts actually run per arm (after subsampling).")
    subsample: Subsample | None = None

    @property
    def scheme(self) -> str:
        return self.source.split(":", 1)[0]


class Judge(_Strict):
    kind: JudgeKind = "none"
    model: str | None = Field(
        default=None,
        description=f"Judge model; '{STUDY_JUDGE}' resolves to the study's shared judge.",
    )
    temperature: float | None = Field(default=None, ge=0)
    rubric: str | None = Field(default=None, description="Repo-relative rubric path.")
    different_from_target: bool | None = None


class Endpoints(_Strict):
    primary: list[EndpointId] = Field(min_length=1)
    secondary: list[EndpointId] = Field(default_factory=list)
    requires_tier0: RequiresTier0 = "none"

    @property
    def all(self) -> list[str]:
        return [*self.primary, *self.secondary]


class EvalCard(_Strict):
    """One registry entry. File name must equal ``id`` (checked by the loader)."""

    id: CardId
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1, description="One line: what the eval measures and why.")
    tier: Tier
    family: Family
    engine: Engine
    inspect_task: str | None = Field(default=None, description="e.g. inspect_evals/novelty_bench")
    task_module: str | None = Field(default=None, description="e.g. evals/<id>/task.py")
    task_args: dict[str, Any] = Field(default_factory=dict)
    dataset: Dataset
    generation: Generation
    thinking: bool = False
    k: int | None = Field(
        default=None,
        ge=1,
        description="Samples per prompt; null only for cards that generate nothing.",
    )
    k_bounds: tuple[int, int] | None = Field(
        default=None, description="[lo, hi] a study may override k within."
    )
    max_tokens: int = Field(ge=0, description="Per-call generation cap sent to the engine.")
    est_tokens_per_sample: int = Field(ge=0)
    est_gen_tokens_per_arm: int = Field(
        ge=0,
        description="Must equal items × k × est_tokens_per_sample × told_cells (validator).",
    )
    sensitivity: Sensitivity
    judge: Judge = Field(default_factory=Judge)
    told_actual: bool = Field(
        default=False,
        description="True ⇒ each arm runs cells [told_qrng, told_prng, told_nothing].",
    )
    endpoints: Endpoints
    paired_stat: PairedStat
    sesoi: dict[EndpointId, float] = Field(
        default_factory=dict, description="TOST margin floor per endpoint."
    )
    exceptions: list[str] = Field(default_factory=list)
    status: Status
    selectable: bool
    notes: str = ""

    @property
    def told_cells(self) -> int:
        return TOLD_CELLS if self.told_actual else 1

    @property
    def generates(self) -> bool:
        """False for Tier 0 / derived / judge-only cards that never call the target."""
        return self.est_tokens_per_sample > 0

    @property
    def implementation(self) -> str | None:
        return self.inspect_task if self.engine == "inspect" else self.task_module


__all__ = [
    "DATASET_SCHEMES",
    "REVISION_REQUIRED_SCHEMES",
    "STUDY_JUDGE",
    "TOLD_CELLS",
    "Dataset",
    "Endpoints",
    "Engine",
    "EvalCard",
    "Family",
    "Generation",
    "Judge",
    "JudgeKind",
    "PairedStat",
    "RequiresTier0",
    "Sensitivity",
    "Status",
    "Subsample",
    "Tier",
]
