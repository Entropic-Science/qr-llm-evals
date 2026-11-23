"""From a study's design block to its registered hypotheses.

A study with a ``design`` block declares which headline endpoints decide it,
at what significance level, and whether the absence of an effect may be
claimed. This module turns those declarations into the plan the analysis
executes: one hypothesis per confirmatory endpoint and (model, sampling) cell,
each bound to its quantum-vs-control contrast, its seed-vs-seed negative
controls and, where one is registered, its biased-sampler positive control.

Four things are decided here and nowhere else:

* :func:`eligible`: which endpoints can carry a confirmatory test at all;
* :func:`recommend` and :func:`recommend_k`: the design, and the repetitions,
  a study gets when the researcher changes nothing;
* :func:`project`: the statistical plan of a designed study, or a refusal;
* :data:`VERDICT_RULES` and :func:`reachable_verdicts`: how a hypothesis
  resolves to a verdict, and which verdicts a registered hypothesis can reach.

Two limits of a design are computed here before any data exist, because a
study that ignores them can only mislead: :func:`plan_permutations` gives the
difference tests the resolution they are read at and the seed-pair controls
enough permutations to fall below their level, and :func:`control_can_fail`
says whether a seed-against-seed test is accepted as a control that could have
failed. The analysis asks the same question of the rows it computed, and a
control that is not accepted does not pass.

:func:`problems` lists what is wrong with a draft in the words the setup pages
show (:data:`PROBLEM_CODES` names every code). ``apps/web/src/lib/design.ts``
mirrors :func:`eligible`, :func:`recommend`, :func:`recommend_k`,
:func:`draft_hypotheses`, :func:`plan_permutations`,
:func:`positive_control_shift` and :func:`reachable_verdicts`, and is checked
against ``packages/contracts/fixtures/design.json``.

The config arrives as dumped JSON (``StudyConfig.model_dump(mode="json")``):
``cards`` may not import ``models``. Layer 0: imports only from ``cards``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict, cast, get_args

from pydantic import BaseModel, ConfigDict

from qr_llm_evals.cards import power
from qr_llm_evals.cards.design_schema import (
    FamilySizes,
    HypothesisPlan,
    HypothesisTest,
    PlanDesign,
    Reason,
    Verdict,
)
from qr_llm_evals.cards.endpoint_meta import meta_for
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.cards.statistical_plan import (
    PRIMARY_TESTS,
    SPECIAL_TESTS,
    Contrast,
    EndpointPlan,
    StatisticalPlan,
)

#: The tests a hypothesis can be registered on: the three the analysis resolves to a verdict.
HYPOTHESIS_TESTS: tuple[str, ...] = get_args(HypothesisTest)
#: The only test with a paired difference, and so the only one a margin applies to.
PAIRED_TEST = "paired_permutation"
#: A study registers at most this many confirmatory endpoints (``StudyDesign.confirmatory``).
MAX_CONFIRMATORY = 8
MIN_PERMUTATIONS = 10_000
#: More permutations than this per test do not finish in a reasonable analysis run.
MAX_PERMUTATIONS = 100_000
#: Permutations per unit of ``m / alpha`` for ``m`` difference tests: the smallest
#: p a difference test can return is then a twentieth of its corrected level.
PERMUTATIONS_PER_LEVEL = 20
#: Permutations per unit of ``F / alpha`` for ``F`` seed-pair control tests: the
#: smallest p a seed pair can return is then half its corrected level. A control
#: is only asked whether it falls below its level, so it needs room under the
#: level and not the resolution a difference test is read at.
CONTROL_PERMUTATIONS_PER_LEVEL = 2
#: The positive control a study gets when it has a choice.
PREFERRED_EPSILON = 0.10

Ref = tuple[str, str]
Cell = tuple[str, str]
MarginSource = Literal["card_sesoi", "declared", "none"]
Unreachable = Reason | Literal["below_n_floor", "control_cannot_fail"]
StepId = Literal["compare", "measure", "design", "instruments", "review"]

#: Prose of a designed plan. The legacy wording describes pilot-derived margins
#: and the three-seed claim rule, neither of which a designed study uses.
MARGIN_RULE = (
    "Fixed at registration: the card SESOI, or the declared margin where the card has none. "
    "Never derived from pilot or full-run data."
)
MISSING_MARGIN_RULE = "No equivalence claim on an endpoint without a margin fixed at registration."
CLAIM_RULE = (
    "Each confirmatory hypothesis resolves by script to one verdict under decision matrix "
    "version 1."
)
SUPERVISED_FAMILY = (
    "Supervised vs unsupervised contrasts are exploratory in designed studies; no verdict."
)
STOPPING_RULE = (
    "Fixed items and k per available arm; no significance-based stopping; only the first full "
    "run of a study is confirmatory; pilot runs never are."
)
SOURCE_FAMILY = (
    "Holm across confirmatory endpoints x (model, sampling) cells, unavailable members included."
)


class Problem(TypedDict):
    """One thing wrong with a draft, addressed to a setup step and a map node."""

    code: str
    severity: Literal["error", "warning"]
    message: str
    step_id: StepId
    node_id: str


class VerdictRule(BaseModel):
    """One row of the decision matrix. Rows are tried in order; the first match wins."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int
    condition: str
    verdict: Verdict
    #: The reason recorded with the verdict; the last row records the first that applies.
    reasons: tuple[Reason, ...]
    #: ``str.format`` template over ``label``, ``margin``, ``epsilon`` and ``reason_text``.
    wording: str


#: Decision matrix version 1. ``pD`` and ``pE`` are the Holm-adjusted difference
#: and equivalence p-values; the prereg prints these rows and the analysis
#: applies them, so the two cannot drift apart.
VERDICT_RULES: tuple[VerdictRule, ...] = (
    VerdictRule(
        number=1,
        condition="The comparison could not be computed.",
        verdict="unavailable",
        reasons=("not_produced",),
        wording="Not assessed: the required data were not produced.",
    ),
    VerdictRule(
        number=2,
        condition="Fewer than 90% of the planned pairs were analysed.",
        verdict="invalid",
        reasons=("incomplete",),
        wording="No conclusion: fewer than 90% of the planned pairs were analysed.",
    ),
    VerdictRule(
        number=3,
        condition=(
            "A seed-vs-seed control of the cell is missing, differs after Holm correction, "
            "or could not have failed."
        ),
        verdict="invalid",
        reasons=("negative_control", "negative_control_cannot_fail"),
        wording=(
            "No conclusion: two seeds of the same pseudo-random generator differed, "
            "or a seed control is missing or could not have failed."
        ),
    ),
    VerdictRule(
        number=4,
        condition="pD < alpha, a margin is registered, and pE < alpha.",
        verdict="effect_within_margin",
        reasons=(),
        wording="A difference on {label} is detectable but smaller than ±{margin}.",
    ),
    VerdictRule(
        number=5,
        condition="pD < alpha.",
        verdict="effect",
        reasons=(),
        wording="Quantum and pseudo-random sampling differ on {label}.",
    ),
    VerdictRule(
        number=6,
        condition=(
            "The claim includes equivalence, a margin is registered, pE < alpha, "
            "and the positive control was detected."
        ),
        verdict="equivalent",
        reasons=(),
        wording=(
            "No difference larger than ±{margin} on {label}; "
            "the assay detected a sampler bias of ε = {epsilon}."
        ),
    ),
    VerdictRule(
        number=7,
        condition="Otherwise.",
        verdict="inconclusive",
        reasons=(
            "equivalence_not_claimed",
            "equivalence_not_testable",
            "no_margin",
            "interval_exceeds_margin",
            "positive_control_absent",
            "positive_control_failed",
        ),
        wording=("Neither a difference nor its absence is established on {label} ({reason_text})."),
    ),
)

#: The ``reason_text`` of an inconclusive verdict.
INCONCLUSIVE_REASON_TEXT: dict[Reason, str] = {
    "equivalence_not_claimed": "the study registered no equivalence claim",
    "equivalence_not_testable": "this test has no equivalence margin",
    "no_margin": "no margin was registered for this outcome",
    # The rule of row 7 is the Holm-adjusted equivalence p-value. With several
    # hypotheses that have a margin, a raw test can pass (its interval is then
    # inside the margin) and fail after correction, so the text names the rule.
    "interval_exceeds_margin": "the equivalence test did not pass after correction",
    "positive_control_absent": "the positive control was not run",
    "positive_control_failed": "the positive control was not detected",
}


# ── endpoints ────────────────────────────────────────────────────────────────


def planned_test(card: EvalCard, endpoint: str) -> str:
    """The test the plan names for an endpoint, as the built-in plan resolves it."""
    return {**SPECIAL_TESTS, **PRIMARY_TESTS}.get(
        (card.id, endpoint),
        "paired_binomial" if card.paired_stat == "paired_binomial" else PAIRED_TEST,
    )


def ineligible_reason(card: EvalCard | None, endpoint: str) -> str | None:
    """Why an endpoint cannot decide a study, as the end of a sentence; ``None`` if it can."""
    if card is None:
        return "the eval is not in the registry"
    if card.status != "implemented":
        return "the eval is not implemented"
    if endpoint not in card.endpoints.primary:
        return "it is not the eval's headline outcome"
    test = planned_test(card, endpoint)
    if test not in HYPOTHESIS_TESTS:
        return f"its test ({test}) does not resolve to a verdict"
    meta = meta_for(card.id, endpoint)
    if not meta.executable:
        return (
            "it is scored per prompt group, which is not computed yet"
            if meta.level == "prompt_group"
            else "it is not computed from single samples yet"
        )
    return None


def eligible(card: EvalCard, endpoint: str) -> bool:
    """Whether a confirmatory hypothesis may be registered on ``endpoint``."""
    return ineligible_reason(card, endpoint) is None


def card_margin(card: EvalCard, endpoint: str) -> float | None:
    """The card's SESOI, where the endpoint's test can use it as an equivalence margin."""
    if planned_test(card, endpoint) != PAIRED_TEST:
        return None
    sesoi = card.sesoi.get(endpoint)
    return sesoi if sesoi is not None and sesoi > 0 else None


def margin_refusal(card: EvalCard, endpoint: str, margin: float) -> str | None:
    """Why a declared margin cannot stand on ``endpoint``; ``None`` if it can.

    The test comes first: a card may state a SESOI for an endpoint whose test
    has no paired difference (the KS residual test does), and such an endpoint
    has no margin at all, whatever the card says.
    """
    if planned_test(card, endpoint) != PAIRED_TEST:
        return "has no paired difference a margin could apply to"
    if endpoint in card.sesoi:
        return "takes its margin from the eval card"
    bounds = meta_for(card.id, endpoint).range
    if bounds is None:
        return "has no fixed range to state a margin against"
    width = bounds[1] - bounds[0]
    cap = power.MARGIN_CAP_FRACTION * width
    if margin > cap + 1e-12:
        return f"cannot take a margin above {cap:g} ({power.MARGIN_CAP_FRACTION:.0%} of its range)"
    least = power.MARGIN_FLOOR_FRACTION * width
    if margin < least - 1e-12:
        return (
            f"cannot take a margin below {least:g} ({power.MARGIN_FLOOR_FRACTION:.1%} of its range)"
        )
    return None


# ── arms ─────────────────────────────────────────────────────────────────────


def _compared(arm: Mapping[str, Any], source: str) -> bool:
    return bool(
        arm["source"] == source
        and arm["observer_mode"] == "unsupervised"
        and arm["availability"] == "available"
    )


def _same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-12


def source_cells(data: Mapping[str, Any]) -> list[Cell]:
    """The (model, sampling) pairs with an available unsupervised quantum arm, sorted."""
    return sorted({(a["model"], a["sampling"]) for a in data["arms"] if _compared(a, "qrng")})


def _cell_arms(data: Mapping[str, Any], cell: Cell) -> list[Mapping[str, Any]]:
    return [a for a in data["arms"] if (a["model"], a["sampling"]) == cell]


def _epsilon_arms(data: Mapping[str, Any], cell: Cell, epsilon: float) -> list[Mapping[str, Any]]:
    return [
        a
        for a in _cell_arms(data, cell)
        if a["source"] == "prng_epsilon" and _same(a["epsilon"], epsilon)
    ]


def _control_seeds(data: Mapping[str, Any], cell: Cell) -> list[int]:
    return [a["study_seed"] for a in _cell_arms(data, cell) if a["source"] == "prng_seeded"]


def _lane(cell: Cell) -> str:
    return f"{cell[0]}/{cell[1]}"


def _cell_text(cell: Cell) -> str:
    return f"{cell[0]} · {cell[1]}"


def _recommended_epsilon(data: Mapping[str, Any]) -> float | None:
    """The positive control every cell can run: ε 0.10 if it can, else the largest."""
    cells = source_cells(data)
    if not cells:
        return None

    def usable(level: float) -> bool:
        for cell in cells:
            arms = _epsilon_arms(data, cell, level)
            if len(arms) != 1 or not _compared(arms[0], "prng_epsilon"):
                return False
            if arms[0]["study_seed"] in _control_seeds(data, cell):
                return False
        return True

    levels = sorted(
        {a["epsilon"] for a in data["arms"] if a["source"] == "prng_epsilon" and a["epsilon"]},
        reverse=True,
    )
    candidates = [level for level in levels if usable(level)]
    for level in candidates:
        if _same(level, PREFERRED_EPSILON):
            return float(level)
    return float(candidates[0]) if candidates else None


# ── the recommended design ───────────────────────────────────────────────────


def recommend(data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> dict[str, Any] | None:
    """The design block a study gets by default, or ``None`` if nothing can decide it.

    Confirmatory are the built-in primaries that are selected and eligible,
    then every selected eligible headline endpoint whose card states a SESOI.
    The absence of an effect is claimed only where it can be shown: a positive
    control every cell runs, and a margin on at least one endpoint.
    """
    selected = [cards[s["card_id"]] for s in data["cards"] if s["card_id"] in cards]
    by_id = {card.id: card for card in selected}
    refs: list[Ref] = [
        key for key in PRIMARY_TESTS if key[0] in by_id and eligible(by_id[key[0]], key[1])
    ]
    for card in selected:
        for endpoint in card.endpoints.primary:
            if (
                endpoint in card.sesoi
                and eligible(card, endpoint)
                and (card.id, endpoint) not in refs
            ):
                refs.append((card.id, endpoint))
    if not refs:
        first = next(
            (
                (card.id, endpoint)
                for card in selected
                for endpoint in card.endpoints.primary
                if eligible(card, endpoint)
            ),
            None,
        )
        if first is None:
            return None
        refs = [first]
    refs = refs[:MAX_CONFIRMATORY]
    epsilon = _recommended_epsilon(data)
    has_margin = any(
        card_margin(by_id[card_id], endpoint) is not None for card_id, endpoint in refs
    )
    return {
        "version": 1,
        "claim": "difference_or_equivalence"
        if epsilon is not None and has_margin
        else "difference",
        "confirmatory": [{"card_id": card_id, "endpoint": endpoint} for card_id, endpoint in refs],
        "alpha": 0.05,
        "sensitivity_epsilon": epsilon,
        "margins": [],
    }


def recommend_k(data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> dict[str, int]:
    """The repetitions (k) a study gets by default where an eval's own k is too few.

    Repetitions drive the sample-size rating as much as prompts do, and an
    eval has only so many prompts. Where all the prompts of an eval are too few
    for the margin of an outcome that decides the study, this names the fewest
    repetitions, within the eval's bounds, at which they are enough
    (``SampleSize.k_recommended``). An eval that is not in the result keeps
    its own k: either that is enough, or no k within its bounds is.

    The hypotheses are those of the declared design, or of the recommended one
    when the study declares none. The answer does not depend on the k and
    prompt overrides of the study, so applying it does not change it.
    """
    design = data.get("design") or recommend(data, cards)
    if design is None:
        return {}
    out: dict[str, int] = {}
    for hypothesis in draft_hypotheses({**data, "design": design}, cards):
        k = hypothesis.sample_size.k_recommended
        own = cards[hypothesis.card_id].k
        if k is not None and own is not None and k > own:
            out[hypothesis.card_id] = max(out.get(hypothesis.card_id, 0), k)
    return out


# ── hypotheses ───────────────────────────────────────────────────────────────


def _per_level(factor: int, tests: int, alpha: float) -> int:
    # The subtraction absorbs the float error of the division (1000 / 0.01 is
    # a hair above 100 000), which would otherwise round an exact count up.
    return math.ceil(factor * tests / alpha - 1e-9)


def planned_permutations(m: int, alpha: float) -> int:
    """Permutations per test for ``m`` difference tests at level ``alpha``, before the cap."""
    return max(MIN_PERMUTATIONS, _per_level(PERMUTATIONS_PER_LEVEL, m, alpha))


def control_permutations(m_control: int, alpha: float) -> int:
    """The fewest permutations the plan gives ``m_control`` seed-pair control tests."""
    return _per_level(CONTROL_PERMUTATIONS_PER_LEVEL, m_control, alpha)


def plan_permutations(families: FamilySizes, alpha: float) -> int:
    """Permutations per test of a plan: what its difference tests and its controls each need.

    Both are read off permutation p-values, each corrected within its own Holm
    family, and a test with ``P`` permutations returns no p-value below
    ``1 / (P + 1)``. A difference test is given a resolution of a twentieth of
    its corrected level (:func:`planned_permutations`). A seed-against-seed
    pair is only asked whether it falls below ``alpha / F``, so its smallest
    p-value is put at half that level and no lower
    (:func:`control_permutations`): sized like a difference test, the ``F``
    seed pairs of a study with five control seeds would ask for ten times the
    permutations of its hypotheses and put ordinary designs over the cap. The
    count is the larger of the two. The controls decide it only from six
    control seeds on (15 pairs for each hypothesis); sized on the difference
    tests alone, a study with seven would have controls that cannot fail. The
    equivalence tests are t-tests and need no permutations.
    """
    return max(
        planned_permutations(families.difference, alpha),
        control_permutations(families.negative_control, alpha),
    )


def control_can_fail(
    test: str, *, prompts: int | None, permutations: int | None, alpha: float, family: int
) -> bool:
    """Whether one seed-against-seed test is accepted as a control that could have failed.

    A pair fails at ``p < alpha / family`` (the first step of the correction
    over ``family`` seed-pair tests). A permutation test returns no p-value
    below ``1 / (permutations + 1)``, so with too few permutations no pair can
    fail. A paired test is also asked for ``power.control_floor`` prompts: one
    more than the fewest at which its exact p-value can be below that level,
    because one prompt below the floor a pair that differs as much as the
    outcome allows often fails and is not certain to. The KS test reads its
    p-value from a distribution and has no such floor. ``prompts`` and
    ``permutations`` are ``None`` where the number is not known; it then sets
    no limit.
    """
    if test not in HYPOTHESIS_TESTS or test == "ks_two_sample":
        return True
    if permutations is not None and family / (permutations + 1) >= alpha:
        return False
    return not (
        test == PAIRED_TEST
        and prompts is not None
        and family >= 1
        and prompts < power.control_floor(alpha, family)
    )


def family_sizes(hypotheses: Sequence[HypothesisPlan]) -> FamilySizes:
    """How many tests each Holm family corrects for."""
    return FamilySizes(
        difference=len(hypotheses),
        equivalence=sum(h.margin is not None for h in hypotheses),
        negative_control=sum(len(h.negative_controls) for h in hypotheses),
    )


def _effective(card: EvalCard, selection: Mapping[str, Any]) -> tuple[int | None, int]:
    overrides = selection.get("overrides") or {}
    k = overrides.get("k")
    items = overrides.get("items")
    return (
        k if k is not None else card.k,
        items if items is not None else card.dataset.items,
    )


def in_planned_stream(card: EvalCard | Mapping[str, Any]) -> bool:
    """Whether an eval's replies are counted as planned data of a token-stream test.

    A token-stream test pools every reply of the run, but the share of its
    planned data that was analysed needs a count that is known at registration.
    That is the evals that run (implemented, and generating) on a prompt list
    fixed before the run. An eval derived from another eval's replies asks as
    many prompts as those replies allow, so its item count is an expectation:
    counting it could call a complete run incomplete, or hide missing replies
    behind extra ones. Such an eval is in neither count.

    ``card`` is a registry card or its dumped form, as a plan archives it
    (``StatisticalPlan.cards_json``): the analysis asks the same question of
    the frozen snapshot, so both counts are of one set.
    """
    if isinstance(card, EvalCard):
        generates, status, source = card.generates, str(card.status), card.dataset.source
    else:
        dataset = card.get("dataset")
        tokens = card.get("est_tokens_per_sample")
        generates = isinstance(tokens, int) and tokens > 0
        status = str(card.get("status"))
        source = str(dataset.get("source", "")) if isinstance(dataset, Mapping) else ""
    fixed_list = bool(source) and not source.startswith("derived:")
    return generates and status == "implemented" and fixed_list


def _generations_per_arm(data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> int:
    """Replies one arm is planned to generate on the evals with a fixed prompt list."""
    total = 0
    for selection in data["cards"]:
        card = cards.get(selection["card_id"])
        if card is None or not in_planned_stream(card):
            continue
        k, items = _effective(card, selection)
        total += items * (k if k is not None else 1) * card.told_cells
    return total


def _confirmatory(data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> list[Ref]:
    """The declared confirmatory endpoints that can be tested, in declared order."""
    selected = {s["card_id"] for s in data["cards"]}
    refs: list[Ref] = []
    for ref in data["design"]["confirmatory"]:
        key = (ref["card_id"], ref["endpoint"])
        card = cards.get(key[0])
        if card is not None and key[0] in selected and eligible(card, key[1]) and key not in refs:
            refs.append(key)
    return refs


def _margins(
    data: Mapping[str, Any], cards: Mapping[str, EvalCard], refs: Sequence[Ref]
) -> dict[Ref, tuple[float | None, MarginSource]]:
    """Each endpoint's margin: the card SESOI first, then a declared margin that can stand."""
    declared = {
        (m["card_id"], m["endpoint"]): float(m["margin"]) for m in data["design"]["margins"]
    }
    out: dict[Ref, tuple[float | None, MarginSource]] = {}
    for ref in refs:
        card = cards[ref[0]]
        sesoi = card_margin(card, ref[1])
        if sesoi is not None:
            out[ref] = (sesoi, "card_sesoi")
        elif ref in declared and margin_refusal(card, ref[1], declared[ref]) is None:
            out[ref] = (declared[ref], "declared")
        else:
            out[ref] = (None, "none")
    return out


def _hypotheses(
    data: Mapping[str, Any], cards: Mapping[str, EvalCard], contrasts: Sequence[Contrast]
) -> tuple[HypothesisPlan, ...]:
    design = data["design"]
    alpha = float(design["alpha"])
    epsilon = design["sensitivity_epsilon"]
    refs = _confirmatory(data, cards)
    margins = _margins(data, cards, refs)
    selections = {s["card_id"]: s for s in data["cards"]}

    def index(kind: str, cell: Cell) -> list[int]:
        return [
            i
            for i, contrast in enumerate(contrasts)
            if contrast.kind == kind and (contrast.model, contrast.sampling) == cell
        ]

    # A cell is tested only where there is something to compare the quantum arm with.
    cells = [cell for cell in source_cells(data) if index("source", cell)]
    m = len(refs) * len(cells)
    m_e = sum(margins[ref][0] is not None for ref in refs) * len(cells)
    # Every seed pair of every hypothesis: the family the negative control is corrected over.
    m_control = len(refs) * sum(len(index("seed_vs_seed", cell)) for cell in cells)
    generations = _generations_per_arm(data, cards)

    hypotheses: list[HypothesisPlan] = []
    for ref in refs:
        card = cards[ref[0]]
        meta = meta_for(*ref)
        test = planned_test(card, ref[1])
        margin, margin_source = margins[ref]
        k, items = _effective(card, selections[card.id])
        pooled = meta.level == "token_stream"
        units = generations if pooled else items
        pairs = generations if pooled else items * (k if k is not None else 1) * card.told_cells
        # The most repetitions a study may set: the card's upper bound, or its
        # own k where it has no bounds or the endpoint is defined for that k only.
        k_max = card.k if meta.k_locked or card.k_bounds is None else card.k_bounds[1]
        for cell in cells:
            source = index("source", cell)[0]
            seeds = len(contrasts[source].b)
            positive = [i for i in index("epsilon", cell) if _same(contrasts[i].epsilon, epsilon)]
            number = len(hypotheses) + 1
            compared_with = "the pooled tokens of" if pooled else "the per-prompt mean of"
            hypotheses.append(
                HypothesisPlan(
                    id=f"H{number}",
                    card_id=card.id,
                    endpoint=ref[1],
                    label=meta.label,
                    model=cell[0],
                    sampling=cell[1],
                    test=cast(HypothesisTest, test),
                    sided="one_sided_upper" if test == "mmd_hamming_permutation" else "two_sided",
                    contrast=source,
                    negative_controls=tuple(index("seed_vs_seed", cell)),
                    positive_control=positive[0] if positive else None,
                    margin=margin,
                    margin_source=margin_source,
                    planned_units=units,
                    planned_pairs=pairs,
                    sample_size=power.rate(
                        level=meta.level,
                        range=meta.range,
                        margin=margin,
                        n=units,
                        dataset_items=card.dataset.items,
                        k=k,
                        told_cells=card.told_cells,
                        pooled_seeds=seeds,
                        alpha=alpha,
                        m=m,
                        m_e=m_e,
                        m_control=m_control,
                        k_default=card.k,
                        k_max=k_max,
                    ),
                    statement=(
                        f"H{number}: on {meta.label} ({card.id}), quantum sampling differs from "
                        f"{compared_with} {seeds} seeded pseudo-random runs "
                        f"({_cell_text(cell)})."
                    ),
                )
            )
    return tuple(hypotheses)


def _without_design(data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != "design"}


def draft_hypotheses(
    data: Mapping[str, Any], cards: Mapping[str, EvalCard]
) -> tuple[HypothesisPlan, ...]:
    """The hypotheses a draft would register, without :func:`project`'s refusals.

    A confirmatory endpoint that cannot be tested and a declared margin that
    cannot stand are left out rather than refused, so a draft that is being
    edited still shows the rest; :func:`problems` names what was left out.
    Empty for a study without a design block, and while the selected evals
    themselves are not valid (an unknown card, an override out of bounds).
    """
    if data.get("design") is None:
        return ()
    try:
        contrasts = StatisticalPlan._legacy(_without_design(data), cards).contrasts
    except ValueError:
        return ()
    return _hypotheses(data, cards, contrasts)


# ── the plan of a designed study ─────────────────────────────────────────────


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _refusals(data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> list[str]:
    """Everything in the design block that the plan cannot be built from."""
    design = data["design"]
    selections = {s["card_id"]: s for s in data["cards"]}
    out: list[str] = []
    for ref in design["confirmatory"]:
        card_id, endpoint = ref["card_id"], ref["endpoint"]
        name = f"{card_id}/{endpoint}"
        if card_id not in selections:
            out.append(f"design.confirmatory names {name}, but the eval is not selected")
            continue
        card = cards.get(card_id)
        why = ineligible_reason(card, endpoint)
        if card is None or why is not None:
            out.append(f"design.confirmatory names {name}, which cannot decide a study: {why}")
            continue
        override = (selections[card_id].get("overrides") or {}).get("k")
        if meta_for(card_id, endpoint).k_locked and override is not None and override != card.k:
            out.append(
                f"{name} is defined for k = {card.k} only; "
                f"a study that makes it confirmatory may not override k"
            )
    for decl in design["margins"]:
        card = cards.get(decl["card_id"])
        if card is None:
            continue
        why = margin_refusal(card, decl["endpoint"], float(decl["margin"]))
        if why is not None:
            out.append(f"design.margins: {decl['card_id']}/{decl['endpoint']} {why}")
    return out


def project(
    plan_cls: type[StatisticalPlan], data: Mapping[str, Any], cards: Mapping[str, EvalCard]
) -> StatisticalPlan:
    """The statistical plan of a study with a design block.

    The contrasts and the card snapshots are those of the built-in plan: a
    design changes which endpoints are confirmatory and how they are decided,
    never which arms are compared. Raises ``ValueError`` when the design names
    something the analysis cannot execute.
    """
    design = data.get("design")
    if design is None:
        raise ValueError("a plan can only be projected from a study with a design block")
    legacy = plan_cls._legacy(_without_design(data), cards)
    refusals = _refusals(data, cards)
    if refusals:
        raise ValueError("; ".join(refusals))
    hypotheses = _hypotheses(data, cards, legacy.contrasts)
    if not hypotheses:
        raise ValueError(
            "design has nothing to test: no (model, sampling) cell compares an available "
            "quantum arm with seeded pseudo-random arms"
        )
    alpha = float(design["alpha"])
    families = family_sizes(hypotheses)
    permutations = plan_permutations(families, alpha)
    if permutations > MAX_PERMUTATIONS:
        raise ValueError(
            f"{families.difference} confirmatory tests and {families.negative_control} "
            f"seed-pair control tests at alpha {alpha:g} need {permutations} "
            f"permutations each; the analysis runs at most {MAX_PERMUTATIONS}"
        )

    resolved = {(h.card_id, h.endpoint): h for h in hypotheses}
    endpoints: list[EndpointPlan] = []
    for endpoint in legacy.endpoints:
        hypothesis = resolved.get((endpoint.card_id, endpoint.endpoint))
        fields = endpoint.model_dump()
        if hypothesis is None:
            fields.update(
                role="exploratory",
                correction="none",
                family=f"tier_{endpoint.tier}_exploratory",
                equivalence="unavailable",
            )
        else:
            fields.update(
                role="primary",
                correction="holm",
                family="confirmatory",
                equivalence="tost" if hypothesis.margin is not None else "unavailable",
            )
            if hypothesis.margin_source == "declared":
                fields["sesoi"] = hypothesis.margin
        endpoints.append(EndpointPlan.model_validate(fields))

    return plan_cls(
        config_hash=_digest(dict(data)),
        cards_hash=legacy.cards_hash,
        cards_json=legacy.cards_json,
        endpoints=tuple(endpoints),
        contrasts=legacy.contrasts,
        design=PlanDesign(
            claim=design["claim"],
            alpha=alpha,
            power=power.POWER,
            min_completeness=power.MIN_COMPLETENESS,
            reference_effect=power.REFERENCE_EFFECT,
            sensitivity_epsilon=design["sensitivity_epsilon"],
            families=families,
            hypotheses=hypotheses,
        ),
        alpha=alpha,
        permutations=permutations,
        ci_level=round(1 - alpha, power.PRECISION),
        pilot_quantile=None,
        margin_rule=MARGIN_RULE,
        missing_margin_rule=MISSING_MARGIN_RULE,
        source_family=SOURCE_FAMILY,
        supervised_family=SUPERVISED_FAMILY,
        claim_rule=CLAIM_RULE,
        stopping_rule=STOPPING_RULE,
        supervised_exploratory=legacy.supervised_exploratory,
    )


# ── verdicts a registered hypothesis can reach ───────────────────────────────


def reachable_verdicts(hypothesis: HypothesisPlan, claim: str) -> dict[Verdict, Unreachable | None]:
    """Every verdict, with ``None`` if the hypothesis can resolve to it, else why not.

    What is ruled out is known at registration: a test without a paired
    difference has no margin, a study that registered no equivalence claim or
    no positive control cannot return ``equivalent``, and too few prompts
    cannot reach significance at any effect size. With fewer prompts than the
    seed-against-seed control is asked for (``control_floor``), the control
    gate (row 3) makes the hypothesis invalid, so no finding is within reach;
    that is the reason given wherever nothing else rules the verdict out.
    """
    size = hypothesis.sample_size
    below_floor = size.basis != "no_closed_form" and size.n < size.n_floor
    no_control: Unreachable | None = (
        "control_cannot_fail"
        if size.control_floor is not None and size.n < size.control_floor
        else None
    )
    # Why the hypothesis has no margin to test against, if it has none. The
    # reasons come in the order the decision matrix gives them.
    marginless: Unreachable | None = (
        "equivalence_not_testable"
        if hypothesis.test != PAIRED_TEST
        else "no_margin"
        if hypothesis.margin is None
        else None
    )
    equivalent: Unreachable | None = (
        "equivalence_not_claimed"
        if claim != "difference_or_equivalence"
        else marginless
        if marginless is not None
        else "positive_control_absent"
        if hypothesis.positive_control is None
        else None
    )
    return {
        "unavailable": None,
        "invalid": None,
        "effect": "below_n_floor" if below_floor else no_control,
        "effect_within_margin": (
            marginless if marginless is not None else "below_n_floor" if below_floor else no_control
        ),
        "equivalent": equivalent if equivalent is not None else no_control,
        "inconclusive": no_control,
    }


def positive_control_shift(
    hypothesis: HypothesisPlan, *, k: int | None, pooled_seeds: int, alpha: float
) -> float | None:
    """How far the biased arm must move the outcome to be detected with ``power.POWER``.

    ``equivalent`` needs the positive control detected on the hypothesis's own
    outcome at the uncorrected level. This is the smallest shift that comparison
    detects under the worst-case variance: the biased arm is one seeded run, so
    its told cells count once, against ``pooled_seeds`` seeded runs. It is a
    property of the test only. How far a sampler bias of ε moves a behavioural
    outcome is not known before a run, so whether the control will be detected
    is not rated. ``None`` where the outcome has no variance bound.
    """
    meta = meta_for(hypothesis.card_id, hypothesis.endpoint)
    n = hypothesis.sample_size.n
    if hypothesis.test != PAIRED_TEST or meta.range is None or n < 1 or pooled_seeds < 1:
        return None
    sd = power.sd_bound(
        meta.range[0], meta.range[1], meta.level, k if k is not None else 1, 1, pooled_seeds
    )
    return round(power.mde_standardized(n, alpha, 1) * sd, power.PRECISION)


# ── what is wrong with a draft ───────────────────────────────────────────────


def problems(data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> list[Problem]:
    """What stops a draft from being registered, and what weakens it.

    The codes and the copy are those of the setup pages. This covers what can
    be told from the config and the registry; the pages add their own (an
    unsaved draft, a placeholder contact, a missing judge, a server rejection).
    """
    out: list[Problem] = []

    def add(code: str, severity: Literal["error", "warning"], message: str, node_id: str) -> None:
        step = cast(StepId, code.split(".", 1)[0])
        problem = Problem(
            code=code, severity=severity, message=message, step_id=step, node_id=node_id
        )
        if problem not in out:
            out.append(problem)

    # The server asks for a comparison and its two control seeds only of a
    # study with a design block (``StudyConfig._check_design``). A study on the
    # built-in plan is validated, planned and launched without them, so there
    # they are said and do not block. A study with no arm at all is refused
    # whatever its plan.
    comparison: Literal["error", "warning"] = (
        "error" if data.get("design") is not None or not data["arms"] else "warning"
    )
    cells = source_cells(data)
    if not cells:
        add("compare.no_cell", comparison, "Tick at least one model and sampling preset.", "study")
    for cell in cells:
        seeds = [a["study_seed"] for a in _cell_arms(data, cell) if _compared(a, "prng_seeded")]
        if len(set(seeds)) < 2:
            add(
                "compare.too_few_seeds",
                comparison,
                "Each comparison needs at least 2 control seeds.",
                f"deck:{_lane(cell)}",
            )

    if not data["cards"]:
        add("measure.no_eval", "error", "Select at least one eval.", "ghost:evals")
    for selection in data["cards"]:
        card = cards.get(selection["card_id"])
        if card is None:
            continue
        overrides = selection.get("overrides") or {}
        k = overrides.get("k")
        if k is not None and card.k is not None:
            lo, hi = card.k_bounds if card.k_bounds is not None else (card.k, card.k)
            if not lo <= k <= hi:
                add(
                    "measure.out_of_bounds",
                    "error",
                    f"{card.title}: k must be between {lo} and {hi}.",
                    f"card:{card.id}",
                )
        items = overrides.get("items")
        if items is not None and not 1 <= items <= card.dataset.items:
            add(
                "measure.out_of_bounds",
                "error",
                f"{card.title}: prompts must be between 1 and {card.dataset.items}.",
                f"card:{card.id}",
            )

    design = data.get("design")
    if design is None:
        add("design.legacy_plan", "warning", "This draft uses the fixed Study 1 plan.", "family")
        return out

    if not design["confirmatory"]:
        add(
            "design.no_outcome",
            "error",
            "Choose at least one outcome that decides the study.",
            "family",
        )
    selections = {s["card_id"]: s for s in data["cards"]}
    for ref in design["confirmatory"]:
        card_id, endpoint = ref["card_id"], ref["endpoint"]
        card = cards.get(card_id)
        label = meta_for(card_id, endpoint).label
        node = f"endpoint:{card_id}/{endpoint}"
        why = (
            "the eval is not selected"
            if card_id not in selections
            else ineligible_reason(card, endpoint)
        )
        if card is None or why is not None:
            add(
                "design.not_eligible",
                "error",
                f"{label} can no longer decide a study: {why}.",
                node,
            )
            continue
        override = (selections[card_id].get("overrides") or {}).get("k")
        if meta_for(card_id, endpoint).k_locked and override is not None and override != card.k:
            add(
                "measure.out_of_bounds",
                "error",
                f"{card.title}: k must be between {card.k} and {card.k}.",
                f"card:{card_id}",
            )

    epsilon = design["sensitivity_epsilon"]
    if epsilon is None:
        if design["claim"] == "difference_or_equivalence":
            add(
                "design.equivalence_needs_control",
                "error",
                "'Absence within a margin' needs a positive control.",
                "study",
            )
    else:
        for cell in cells:
            arms = _epsilon_arms(data, cell, epsilon)
            node = f"gate:pos:{_lane(cell)}"
            if not arms:
                add(
                    "design.epsilon_missing",
                    "error",
                    f"No ε = {epsilon:.2f} arm in {_cell_text(cell)}. "
                    "Add it in Compare, or choose 'None'.",
                    node,
                )
                continue
            if not any(_compared(arm, "prng_epsilon") for arm in arms):
                add(
                    "design.epsilon_off",
                    "error",
                    f"The ε = {epsilon:.2f} arm is switched off in {_cell_text(cell)}.",
                    node,
                )
            if any(arm["study_seed"] in _control_seeds(data, cell) for arm in arms):
                add(
                    "design.epsilon_seed_shared",
                    "error",
                    "The ε arm shares a seed with a control arm, which overstates sensitivity.",
                    node,
                )

    for decl in design["margins"]:
        card = cards.get(decl["card_id"])
        if card is None:
            continue
        why = margin_refusal(card, decl["endpoint"], float(decl["margin"]))
        if why is not None:
            label = meta_for(decl["card_id"], decl["endpoint"]).label
            add(
                "design.margin_not_allowed",
                "error",
                f"{label} {why}.",
                f"endpoint:{decl['card_id']}/{decl['endpoint']}",
            )

    hypotheses = draft_hypotheses(data, cards)
    alpha = float(design["alpha"])
    claims_absence = design["claim"] == "difference_or_equivalence"
    for h in hypotheses:
        size = h.sample_size
        if size.basis == "no_closed_form":
            # A test on pooled tokens counts its planned data on the selected
            # evals with a prompt list of their own (``in_planned_stream``).
            # With none of them the hypothesis registers no amount of data,
            # and the completeness gate has nothing to hold the run to.
            if h.planned_units == 0:
                add(
                    "design.no_planned_data",
                    "error",
                    f"{h.label} is tested on the tokens of the replies the study generates, "
                    "and no selected eval generates replies to a prompt list of its own. The "
                    "study therefore plans no data for this outcome, and the analysis cannot "
                    "check how much of the planned data it used. Select an eval that has its "
                    "own prompt list.",
                    f"endpoint:{h.card_id}/{h.endpoint}",
                )
            continue
        card = cards[h.card_id]
        k, _ = _effective(card, selections[h.card_id])
        below = size.n < size.n_floor
        uncontrolled = size.control_floor is not None and size.n < size.control_floor
        if below:
            add(
                "design.below_floor",
                "error",
                f"{h.label}: {size.n} prompts cannot reach significance at any effect size. "
                f"At least {size.n_floor} are needed.",
                f"card:{h.card_id}",
            )
        if uncontrolled:
            add(
                "design.control_below_floor",
                "error",
                f"{h.label}: with {size.n} prompts it is not certain that the seed-against-seed "
                "control can fail, so the analysis does not count it as passed and the outcome "
                f"could only come out invalid. At least {size.control_floor} prompts are needed.",
                f"card:{h.card_id}",
            )
        if not below and not uncontrolled and size.rating == "insufficient":
            detects, target = (
                (f"{size.mde_raw:.3f}", f"±{h.margin:g}")
                if size.mde_raw is not None and h.margin is not None
                else (f"{size.mde_standardized:.2f} SD", f"{power.REFERENCE_EFFECT:.2f} SD")
            )
            # Repetitions are the way out where the eval has no more prompts.
            more_k = (
                f" With k = {size.k_recommended}, the {card.dataset.items} prompts of the eval "
                "are enough."
                if size.reachable is False
                and size.k_recommended is not None
                and size.k_recommended != k
                else ""
            )
            add(
                "design.insufficient",
                "warning",
                f"{h.label}: {size.n} prompts detect ≥ {detects}; "
                f"{target} needs {size.n_recommended}.{more_k}",
                f"card:{h.card_id}",
            )
        # 'Equivalent' also needs the biased arm detected on this outcome, and
        # nothing rates that: the size of its effect is unknown before a run.
        if claims_absence and reachable_verdicts(h, design["claim"])["equivalent"] is None:
            cell = (h.model, h.sampling)
            pooled = sum(_compared(arm, "prng_seeded") for arm in _cell_arms(data, cell))
            shift = positive_control_shift(h, k=k, pooled_seeds=pooled, alpha=alpha)
            needs = (
                "the biased arm to be detected on the same outcome"
                if shift is None
                else f"the biased arm to move it by at least {shift:.3f}"
            )
            add(
                "design.control_not_rated",
                "warning",
                f"'Equivalent' on {h.label} needs {needs}. Whether a sampler bias of "
                f"ε = {epsilon:.2f} does that is not known before the run.",
                f"gate:pos:{_lane(cell)}",
            )
        # A margin is only confirmed by an equivalence claim; without one the
        # floor has nothing to warn about.
        if (
            design["claim"] == "difference_or_equivalence"
            and h.margin is not None
            and size.equivalence_floor is not None
            and h.margin < size.equivalence_floor
        ):
            add(
                "design.margin_below_floor",
                "warning",
                f"±{h.margin:g} cannot be confirmed with {size.n} prompts; "
                f"the smallest confirmable margin is ±{size.equivalence_floor:.3f}.",
                f"endpoint:{h.card_id}/{h.endpoint}",
            )
    families = family_sizes(hypotheses)
    if hypotheses and plan_permutations(families, alpha) > MAX_PERMUTATIONS:
        add(
            "design.too_many_tests",
            "error",
            f"{families.difference} tests with {families.negative_control} seed-pair controls "
            f"at α = {alpha:g} need more permutations than the harness runs. "
            "Remove outcomes, comparisons or control seeds.",
            "family",
        )
    not_equivalent = [
        h.id for h in hypotheses if reachable_verdicts(h, design["claim"])["equivalent"] is not None
    ]
    if hypotheses and claims_absence and len(not_equivalent) == len(hypotheses):
        add(
            "design.equivalent_unreachable",
            "warning",
            "No outcome can return 'equivalent' as configured.",
            "family",
        )
    elif claims_absence and not_equivalent:
        # The study verdict is 'no effect within margins' only when every
        # hypothesis is equivalent, and these cannot be.
        add(
            "design.study_absence_unreachable",
            "warning",
            f"The study as a whole cannot conclude 'no effect within margins': "
            f"{', '.join(not_equivalent)} cannot return 'equivalent'. An outcome that has a "
            "margin can still be reported as equivalent on its own.",
            "family",
        )
    if not str(data.get("expectations", "")).strip():
        add("design.no_expectations", "warning", "No expectations recorded.", "study")
    return out


#: Every code :func:`problems` can report, in the order it reports them. The
#: setup pages mirror these and add their own (``apps/web/src/lib/problems.ts``).
PROBLEM_CODES: tuple[str, ...] = (
    "compare.no_cell",
    "compare.too_few_seeds",
    "measure.no_eval",
    "measure.out_of_bounds",
    "design.legacy_plan",
    "design.no_outcome",
    "design.not_eligible",
    "design.equivalence_needs_control",
    "design.epsilon_missing",
    "design.epsilon_off",
    "design.epsilon_seed_shared",
    "design.margin_not_allowed",
    "design.no_planned_data",
    "design.below_floor",
    "design.control_below_floor",
    "design.insufficient",
    "design.control_not_rated",
    "design.margin_below_floor",
    "design.too_many_tests",
    "design.equivalent_unreachable",
    "design.study_absence_unreachable",
    "design.no_expectations",
)


__all__ = [
    "CONTROL_PERMUTATIONS_PER_LEVEL",
    "HYPOTHESIS_TESTS",
    "INCONCLUSIVE_REASON_TEXT",
    "MAX_CONFIRMATORY",
    "MAX_PERMUTATIONS",
    "MIN_PERMUTATIONS",
    "PERMUTATIONS_PER_LEVEL",
    "PREFERRED_EPSILON",
    "PROBLEM_CODES",
    "VERDICT_RULES",
    "Problem",
    "VerdictRule",
    "card_margin",
    "control_can_fail",
    "control_permutations",
    "draft_hypotheses",
    "eligible",
    "family_sizes",
    "in_planned_stream",
    "ineligible_reason",
    "margin_refusal",
    "plan_permutations",
    "planned_permutations",
    "planned_test",
    "positive_control_shift",
    "problems",
    "project",
    "reachable_verdicts",
    "recommend",
    "recommend_k",
    "source_cells",
]
