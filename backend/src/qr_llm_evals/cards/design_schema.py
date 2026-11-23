"""Frozen shapes of a designed study: its hypotheses, sample sizes and verdicts.

A study with a ``design`` block registers a list of hypotheses, and each one
resolves by script to exactly one verdict. The models here are that contract:
``PlanDesign`` is embedded in the statistical plan (and so in the prereg and
the plan hash); ``DecisionsTable`` is what the analysis writes once the data
exist. They live apart from the code that fills them so that the plan, the
power calculation, the prereg and the analysis share one set of names.

Layer 0, pydantic only: no internal imports.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: What a hypothesis can resolve to. ``unavailable`` and ``invalid`` are not
#: findings: the data were not produced, or a control says not to trust them.
Verdict = Literal[
    "unavailable", "invalid", "effect", "effect_within_margin", "equivalent", "inconclusive"
]
#: Why a hypothesis did not resolve to a finding.
Reason = Literal[
    "not_produced",
    "incomplete",
    "negative_control",
    "negative_control_cannot_fail",
    "equivalence_not_claimed",
    "equivalence_not_testable",
    "no_margin",
    "interval_exceeds_margin",
    "positive_control_absent",
    "positive_control_failed",
]
HypothesisTest = Literal["paired_permutation", "ks_two_sample", "mmd_hamming_permutation"]
SampleSizeRating = Literal["sufficient", "marginal", "insufficient", "unknown"]
SampleSizeBasis = Literal["margin_worst_case", "standardized_reference", "no_closed_form"]
PositiveControlStatus = Literal["passed", "failed", "absent"]
StudyVerdict = Literal[
    "not_confirmatory", "invalid", "effect", "no_effect_within_margins", "mixed_or_inconclusive"
]
NonConfirmatoryReason = Literal["pilot_run", "not_first_full_run"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SampleSize(_Frozen):
    """Whether the planned number of prompts can carry one hypothesis.

    The numbers are worst-case bounds computed before any data exist. A field
    is ``None`` when no closed form applies to the test.
    """

    n: int
    n_floor: int
    #: The fewest prompts the harness accepts for the seed-against-seed control
    #: of the hypothesis: below it the control is not certain to be able to
    #: fail. ``None`` for a test on pooled tokens, and for a study without a
    #: seed pair.
    control_floor: int | None = None
    mde_standardized: float | None
    sd_bound: float | None
    mde_raw: float | None
    power_at_margin: float | None
    equivalence_floor: float | None
    n_recommended: int | None
    reachable: bool | None
    #: The fewest repetitions (k), from the eval's own up to the most a study
    #: may set, at which all the prompts of the eval are enough for the margin.
    #: ``None`` when none is, or when repetitions do not enter the bound.
    k_recommended: int | None = None
    rating: SampleSizeRating
    basis: SampleSizeBasis


class HypothesisPlan(_Frozen):
    """One registered hypothesis: an endpoint in one (model, sampling) cell."""

    id: str
    card_id: str
    endpoint: str
    label: str
    model: str
    sampling: str
    test: HypothesisTest
    sided: Literal["two_sided", "one_sided_upper"]
    # Indices into the plan's ``contrasts``; the contrasts themselves are not copied.
    contrast: int
    negative_controls: tuple[int, ...]
    positive_control: int | None
    margin: float | None
    margin_source: Literal["card_sesoi", "declared", "none"]
    planned_units: int
    planned_pairs: int
    sample_size: SampleSize
    statement: str


class FamilySizes(_Frozen):
    """How many tests each Holm family corrects for, unavailable members included."""

    difference: int
    equivalence: int
    negative_control: int


class PlanDesign(_Frozen):
    """The designed part of a statistical plan."""

    version: Literal[1] = 1
    matrix_version: Literal[1] = 1
    claim: str
    alpha: float
    power: float = 0.80
    min_completeness: float = 0.90
    reference_effect: float = 0.30
    sensitivity_epsilon: float | None
    families: FamilySizes
    hypotheses: tuple[HypothesisPlan, ...]


class NegativeControl(_Frozen):
    """Seed against seed: two runs of the same generator must not differ."""

    passed: bool
    n_pairs: int
    min_p_adjusted: float | None
    #: Whether every computed pair could have failed: its test had enough
    #: prompts and permutations to return a p-value below the corrected level.
    #: A control that could not have failed did not pass.
    can_fail: bool = True


class PositiveControl(_Frozen):
    """The biased arm: a known sampler bias the assay has to detect."""

    epsilon: float | None
    status: PositiveControlStatus
    p_value: float | None


class DecisionRow(_Frozen):
    """One hypothesis after the analysis: the numbers, the gates and the verdict."""

    hypothesis_id: str
    card_id: str
    endpoint: str
    label: str
    model: str
    sampling: str
    test: HypothesisTest
    estimate: float | None
    ci_lower: float | None
    ci_upper: float | None
    ci_level: float
    p_value: float | None
    p_adjusted: float | None
    margin: float | None
    p_equivalence: float | None
    p_equivalence_adjusted: float | None
    completeness: float | None
    negative_control: NegativeControl
    positive_control: PositiveControl
    above_seed_noise: bool | None
    planned_mde_raw: float | None
    planned_mde_standardized: float | None
    verdict: Verdict
    reason: Reason | None
    wording: str


class DecisionsTable(_Frozen):
    """``tables/decisions.json``: every hypothesis resolved, and the study rolled up."""

    schema_version: Literal[1] = 1
    precision: int
    matrix_version: Literal[1] = 1
    confirmatory: bool
    non_confirmatory_reason: NonConfirmatoryReason | None
    claim: str
    alpha: float
    power: float
    min_completeness: float
    sensitivity_epsilon: float | None
    families: FamilySizes
    study_verdict: StudyVerdict
    rows: tuple[DecisionRow, ...]


__all__ = [
    "DecisionRow",
    "DecisionsTable",
    "FamilySizes",
    "HypothesisPlan",
    "HypothesisTest",
    "NegativeControl",
    "NonConfirmatoryReason",
    "PlanDesign",
    "PositiveControl",
    "PositiveControlStatus",
    "Reason",
    "SampleSize",
    "SampleSizeBasis",
    "SampleSizeRating",
    "StudyVerdict",
    "Verdict",
]
