"""Deterministic Jinja2 rendering with explicit, snapshot-able repository inputs.

``Renderer.render`` is pure: no clock, randomness, network, reads or writes.
``load_renderer`` is the I/O boundary; the convenience ``render`` loads the
repository inputs unless a previously loaded renderer is supplied.

There are two templates. A study without a ``design`` block renders from
``docs/prereg-template.md.j2``, whose bytes are frozen with the studies already
registered from it. A study with a design block renders from
``docs/prereg-design-template.md.j2``, which prints what that study registered:
its hypotheses, the decision rules, the sample sizes and the controls, all read
from ``plan.design``.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel

from qr_llm_evals.cards import power, result_checks
from qr_llm_evals.cards.availability import CARD_ENDPOINTS, is_available
from qr_llm_evals.cards.design import (
    CONTROL_PERMUTATIONS_PER_LEVEL,
    INCONCLUSIVE_REASON_TEXT,
    MIN_PERMUTATIONS,
    PERMUTATIONS_PER_LEVEL,
    VERDICT_RULES,
    positive_control_shift,
    reachable_verdicts,
)
from qr_llm_evals.cards.design_schema import HypothesisPlan
from qr_llm_evals.cards.endpoint_meta import meta_for
from qr_llm_evals.cards.estimates import CardOverrides, EstimateAssumptions, estimate_selection
from qr_llm_evals.cards.loader import REPO_ROOT, load_cards
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.cards.statistical_plan import Contrast, StatisticalPlan
from qr_llm_evals.cards.token_limits import effective_max_tokens
from qr_llm_evals.models.envelope import SampleContext, build_envelope
from qr_llm_evals.models.schema import StudyConfig, canonical_json, config_hash
from qr_llm_evals.prereg.hashes import normalize_lf, prereg_hash

_MARKDOWN_PUNCTUATION = "\\`*_{}[]()#+-!|"


def _text(value: object) -> str:
    """Keep user strings from creating Markdown headings, HTML or table cells."""
    text = html.escape(str(value), quote=True)
    for char in _MARKDOWN_PUNCTUATION:
        text = text.replace(char, "\\" + char)
    return normalize_lf(text).replace("\n", "<br>")


def _design_text(value: object) -> str:
    """``_text`` for the design template, with apostrophes that stay readable.

    ``_text`` escapes HTML first, so the ``#`` of the apostrophe's entity is then
    escaped as Markdown and a reader sees ``&#x27;`` spelled out. That output is
    frozen with the legacy template. Escaping the Markdown first protects the
    same characters and leaves the entity whole.
    """
    text = str(value)
    for char in _MARKDOWN_PUNCTUATION:
        text = text.replace(char, "\\" + char)
    return normalize_lf(html.escape(text, quote=True)).replace("\n", "<br>")


def _fence(value: str, language: str = "json") -> str:
    # A user-controlled JSON string may itself contain Markdown backticks.
    longest = max(
        (len(part) for part in value.split("\n") if part and set(part) == {"`"}), default=0
    )
    fence = "`" * max(
        3, longest + 1, max((len(p) for p in re.findall(r"`+", value)), default=0) + 1
    )
    return f"{fence}{language}\n{value}\n{fence}"


# ── what a designed study registered, in a reader's words ────────────────────

#: The only test with a paired difference. The other two pool an arm's tokens.
_PAIRED_TEST = "paired_permutation"
#: How each registered test reads in the document. The tests are implemented in
#: ``analysis``, which this layer may not import, so their descriptions are kept
#: here, beside the template that prints them.
_TEST_TEXT: Mapping[str, str] = {
    "paired_permutation": (
        "Paired permutation test on the mean difference. The arm labels are swapped "
        "within each prompt, so the prompt is the unit of analysis"
    ),
    "ks_two_sample": (
        "Two-sample Kolmogorov–Smirnov test on the token sampling residuals, pooled per side"
    ),
    "mmd_hamming_permutation": (
        "Permutation test on the maximum mean discrepancy (Hamming kernel) between token "
        "sequences. The arm labels are permuted within each prompt"
    ),
}
_SIDED_TEXT: Mapping[str, str] = {
    "two_sided": "two-sided",
    "one_sided_upper": "one-sided, upper tail: only a statistic larger than chance counts",
}
_MARGIN_SOURCE_TEXT: Mapping[str, str] = {
    "card_sesoi": "the smallest effect of interest stated on the eval card",
    "declared": "declared in this study",
}
#: Why a hypothesis cannot reach a verdict, as it is known at registration.
_UNREACHABLE_TEXT: Mapping[str, str] = {
    "equivalence_not_claimed": "the study registers no equivalence claim",
    "equivalence_not_testable": "this test has no equivalence margin",
    "no_margin": "no margin is registered for this outcome",
    "positive_control_absent": "no positive control is available",
    "below_n_floor": "too few prompts to reach significance at any effect size",
    "control_cannot_fail": (
        "fewer prompts than the seed-against-seed control is asked for, "
        "so the hypothesis is invalid"
    ),
}
#: An endpoint's place in a designed study, as the card list prints it. An
#: endpoint is called computed only where the registry table says the harness
#: computes it (``cards.endpoint_meta``); nothing else is promised.
_NOT_COMPUTED = "Not computed by the harness and reported as unavailable"
_SECONDARY = "Exploratory, reported only where the analysis has a value for it"
_NOT_IMPLEMENTED = "Unavailable, because the eval is not implemented"
_NO_TOKENS = "Unavailable, because the supplied readiness reports no token records they can use"
_CONFIRMATORY_NO_TOKENS = (
    "Confirmatory, but the supplied readiness reports no token records it can use, so it "
    "would resolve to unavailable"
)
_TOKENS_UNVERIFIED = (
    "These endpoints are computed from the token records of the engine, which no readiness "
    "report has verified for this document."
)


#: When a sample is tainted, by kind of taint, as the designed document states it.
#: The kinds are the store's (``store.schema.TaintKind``), which this layer may
#: not import, so a test holds the two lists together.
_TAINT_TEXT: Mapping[str, str] = {
    "stream_error": "its generation ended in an error before the reply was complete",
    "engine_fallback": (
        "the engine reported that it fell back from the arm's entropy source while the "
        "sample was generated"
    ),
    "qrng_degraded": (
        "it is a sample of a quantum arm, and while it was generated the engine's entropy "
        "check reported the quantum source as degraded or could not be reached"
    ),
    "stateless_row": "its token records could not be built from what the engine returned",
}
#: The one taint that a completed reply lifts (``analysis.endpoints.excluded_refs``).
_LIFTED_TAINT = "stream_error"


def _result_checks() -> dict[str, Any]:
    """What the "Result checks" section prints, read from the catalogue of the checks.

    The section has no list of its own: every flag kind and stop rule the
    catalogue holds is printed with the catalogue's own label and sentences,
    so the document cannot name a check that does not exist or miss one that does.
    """
    return {
        "version": result_checks.CHECKS_VERSION,
        "flags": [
            {"kind": spec.kind, "label": spec.label, "sentence": spec.sentence}
            for spec in result_checks.FLAG_KINDS.values()
        ],
        "not_detected": "; ".join(result_checks.NOT_DETECTED),
        "taints": [{"kind": kind, "when": when} for kind, when in _TAINT_TEXT.items()],
        "lifted_taint": _LIFTED_TAINT,
        "rules": [
            {
                "rule": spec.rule,
                "label": spec.label,
                "sentence": spec.sentence,
                "recovery": spec.recovery,
            }
            for spec in result_checks.STOP_RULES.values()
        ],
    }


def _percent(level: float) -> str:
    """A level as a percentage. Rounding first keeps ``100 * 0.995`` at 99.5."""
    return f"{round(100 * level, 6):g}%"


def _arms(ids: Sequence[str]) -> str:
    return ", ".join(ids)


def _tests(count: int) -> str:
    return f"{count} test" if count == 1 else f"{count} tests"


def _hypothesis_row(hypothesis: HypothesisPlan, contrast: Contrast, claim: str) -> dict[str, Any]:
    paired = hypothesis.test == _PAIRED_TEST
    unreachable = [
        f"{verdict} ({_UNREACHABLE_TEXT.get(why, why)})"
        for verdict, why in reachable_verdicts(hypothesis, claim).items()
        if why is not None
    ]
    return {
        "plan": hypothesis,
        "outcome": f"{hypothesis.card_id}/{hypothesis.endpoint}",
        "cell": f"{hypothesis.model} · {hypothesis.sampling}",
        "quantum": _arms(contrast.a),
        "seeded": _arms(contrast.b),
        "compared_with": "the per-prompt mean of" if paired else "the pooled tokens of",
        "test": _TEST_TEXT[hypothesis.test],
        "sided": _SIDED_TEXT[hypothesis.sided],
        "margin": "none"
        if hypothesis.margin is None
        else f"±{hypothesis.margin:g}, {_MARGIN_SOURCE_TEXT[hypothesis.margin_source]}",
        "planned": (
            f"{hypothesis.planned_units} prompts, {hypothesis.planned_pairs} paired values "
            "(prompt, repetition and told cell)"
            if paired
            # What the completeness of a pooled test is counted on
            # (``cards.design.in_planned_stream``).
            else (
                f"{hypothesis.planned_units} generations per arm, counted on the selected evals "
                "that run on a prompt list fixed at registration; an eval derived from the "
                "replies of another eval is not counted"
                # A hypothesis that plans nothing has no share of planned data to
                # compute, and the document may not let row 2 seem to guard it.
                + (
                    ""
                    if hypothesis.planned_units
                    else ". This study selects no eval that is counted, so this hypothesis "
                    "plans no data: the share of planned data that was analysed cannot be "
                    "computed, and row 2 of the decision matrix does not apply to it"
                )
            )
        ),
        "unreachable": "; ".join(unreachable) or "none",
    }


def _detectable(hypothesis: HypothesisPlan) -> str | None:
    """The smallest difference the planned prompts detect; ``None`` without a closed form."""
    size = hypothesis.sample_size
    if size.basis == "no_closed_form":
        return None
    if size.n < size.n_floor:
        return (
            f"None: {size.n} prompts cannot reach significance at any effect size. "
            f"At least {size.n_floor} are needed."
        )
    if size.control_floor is not None and size.n < size.control_floor:
        return (
            f"None: with {size.n} prompts it is not certain that the seed-against-seed control "
            "can fail, so the analysis does not count it as passed and the hypothesis can only "
            f"resolve to invalid. At least {size.control_floor} prompts are needed."
        )
    if (
        size.mde_raw is not None
        and size.power_at_margin is not None
        and hypothesis.margin is not None
    ):
        return (
            f"{size.mde_raw:.3f} in the units of the outcome; {size.power_at_margin:.1%} "
            f"power for a difference as large as the margin ±{hypothesis.margin:g}"
        )
    if size.mde_standardized is not None:
        return f"{size.mde_standardized:.2f} standard deviations of the per-prompt difference"
    return "Not computed."


def _sample_size_row(hypothesis: HypothesisPlan, items: int | None) -> dict[str, Any]:
    """One row of the sample-size table. ``items`` is how many prompts the eval has."""
    size = hypothesis.sample_size
    pooled = size.basis == "no_closed_form"
    if size.n_recommended is None:
        recommended = "not computed"
    elif size.reachable:
        recommended = str(size.n_recommended)
    else:
        recommended = f"{size.n_recommended}, more than the dataset of the eval holds"
        if size.basis == "margin_worst_case":
            # Repetitions are the other way to sharpen a prompt's value.
            prompts = "its prompts" if items is None else f"its {items} prompts"
            recommended += (
                f". With k = {size.k_recommended}, {prompts} are enough"
                if size.k_recommended is not None
                else f". No number of repetitions the eval allows makes {prompts} enough"
            )
    return {
        "plan": hypothesis,
        "cell": f"{hypothesis.model} · {hypothesis.sampling}",
        "planned": f"{size.n} generations per arm" if pooled else f"{size.n} prompts",
        "rating": size.rating,
        # The template prints its own sentence where this is ``None``.
        "detectable": _detectable(hypothesis),
        "recommended": recommended,
    }


def _control_rows(config: StudyConfig, plan: StatisticalPlan) -> list[dict[str, Any]]:
    """The controls of each (model, sampling) comparison; its hypotheses share them."""
    design = plan.design
    if design is None:
        return []
    epsilon = design.sensitivity_epsilon
    cells: dict[tuple[str, str], list[HypothesisPlan]] = {}
    for hypothesis in design.hypotheses:
        cells.setdefault((hypothesis.model, hypothesis.sampling), []).append(hypothesis)
    repetitions = {(e.card_id, e.endpoint): e.k for e in plan.endpoints}
    rows: list[dict[str, Any]] = []
    for (model, sampling), members in cells.items():
        first = members[0]
        source = plan.contrasts[first.contrast]
        pairs = [plan.contrasts[index] for index in first.negative_controls]
        # What the biased arm has to do for 'equivalent' to be within reach.
        shifts: list[str] = []
        if epsilon is None:
            positive = "None registered."
        elif first.positive_control is not None:
            biased = plan.contrasts[first.positive_control]
            positive = f"{_arms(biased.a)}, biased by ε = {epsilon:.2f}, against {_arms(biased.b)}."
            for member in members:
                if reachable_verdicts(member, design.claim)["equivalent"] is not None:
                    continue
                shift = positive_control_shift(
                    member,
                    k=repetitions.get((member.card_id, member.endpoint)),
                    pooled_seeds=len(biased.b),
                    alpha=plan.alpha,
                )
                shifts.append(
                    f"{member.id}: no bound is known for this outcome"
                    if shift is None
                    else f"{member.id}: by at least {shift:.3f}"
                )
        else:
            # The arm is registered and cannot run, so the control will be absent.
            named = _arms(
                [
                    arm.id
                    for arm in config.arms
                    if arm.source == "prng_epsilon"
                    and (arm.model, arm.sampling) == (model, sampling)
                    and arm.epsilon is not None
                    and abs(arm.epsilon - epsilon) < 1e-12
                ]
            )
            positive = (
                f"Registered at ε = {epsilon:.2f}, but its arm"
                f"{f' ({named})' if named else ''} is not available in this configuration. "
                "The control is absent."
            )
        rows.append(
            {
                "cell": f"{model} · {sampling}",
                "hypotheses": _arms([member.id for member in members]),
                "quantum": _arms(source.a),
                "seeded": _arms(source.b),
                "negative": "; ".join(f"{_arms(pair.a)} vs {_arms(pair.b)}" for pair in pairs)
                or "none",
                "positive": positive,
                "shifts": "; ".join(shifts),
            }
        )
    return rows


def _fixed_rules(plan: StatisticalPlan) -> list[dict[str, str]]:
    """What the harness fixes for every designed study, with this study's numbers."""
    design = plan.design
    if design is None:
        return []
    families = design.families
    alpha = f"{plan.alpha:g}"
    by_test: dict[tuple[str, str], list[str]] = {}
    for hypothesis in design.hypotheses:
        by_test.setdefault((hypothesis.test, hypothesis.sided), []).append(hypothesis.id)
    rules = [
        (f"Difference test of {_arms(ids)}", f"{_TEST_TEXT[test]}; {_SIDED_TEXT[sided]}.")
        for (test, sided), ids in by_test.items()
    ]
    with_margin = [h.id for h in design.hypotheses if h.margin is not None]
    if with_margin:
        rules.append(
            (
                f"Equivalence test of {_arms(with_margin)}",
                "Two one-sided t-tests of the mean difference against the registered margin, "
                "with prompt-clustered standard errors. The equivalence p-value is the larger "
                "of the two one-sided p-values.",
            )
        )
    rules += [
        (
            "Significance level",
            f"α = {alpha}, family-wise, across all confirmatory hypotheses of the study.",
        ),
        (
            "Difference family",
            f"{plan.source_family} In this study that is {_tests(families.difference)}, "
            "one per hypothesis.",
        ),
        (
            "Equivalence family",
            f"Holm–Bonferroni over {_tests(families.equivalence)}, one per hypothesis with "
            "a registered margin."
            if families.equivalence
            else "Empty: no hypothesis has a registered margin.",
        ),
        (
            "Negative-control family",
            f"Holm–Bonferroni over {_tests(families.negative_control)}: every "
            "seed-against-seed pair of every hypothesis. A pair whose test could not have "
            "returned a p-value below the corrected level counts as a failed control.",
        ),
        ("Unavailable tests", plan.unavailable_family_rule),
        (
            "Confidence intervals",
            f"{_percent(plan.ci_level)} for the mean difference of a paired test; "
            f"{_percent(1 - 2 * plan.alpha)} for an equivalence test. The two tests on pooled "
            "tokens report a statistic and no interval.",
        ),
        (
            "Resampling",
            f"{plan.permutations} permutations per permutation test. That is the largest of "
            f"three numbers: {MIN_PERMUTATIONS}; {PERMUTATIONS_PER_LEVEL} times the number of "
            "difference tests divided by α, so that the smallest p-value a difference test "
            "can return is at most a twentieth of its corrected level; and "
            f"{CONTROL_PERMUTATIONS_PER_LEVEL} times the number of seed-pair tests divided by "
            "α, so that the smallest p-value a seed-pair test can return is at most half of "
            f"its corrected level. {plan.bootstrap_replicates} bootstrap replicates for "
            f"exploratory summaries; analysis random seed {plan.random_seed}.",
        ),
        ("Pairing", f"{plan.pairing}."),
        ("Margins", f"{plan.margin_rule} {plan.missing_margin_rule}"),
        (
            "Power",
            f"Sample sizes are rated at {design.power:.0%} power. An outcome without a margin "
            f"in its own units is rated against a difference of {design.reference_effect:g} "
            "standard deviations.",
        ),
        (
            "Completeness",
            f"A verdict needs at least {design.min_completeness:.0%} of the planned data analysed.",
        ),
        ("Supervised contrasts", f"{plan.supervised_family} Unit: {plan.supervised_unit}."),
        ("Biased arms", plan.epsilon_rule),
        ("Readiness", plan.readiness_rule),
    ]
    return [{"name": name, "rule": rule} for name, rule in rules]


def _design_endpoints(
    card: EvalCard,
    confirmatory: Mapping[str, Sequence[str]],
    tier0: Any,
    verified: bool,
) -> str:
    """The endpoints of one card as a designed study registers them.

    The legacy list calls every endpoint of an implemented card "available".
    A designed document may not: the harness reads one value per reply, and an
    endpoint that needs several replies scored together is not computed at
    all (``EndpointMeta.executable``). So each endpoint is given its role, and
    a headline endpoint the harness does not compute says so. The registry
    table covers headline endpoints only; nothing is asserted about whether a
    secondary endpoint is computed.
    """
    needs_tokens = card.id in CARD_ENDPOINTS or card.endpoints.requires_tier0 != "none"
    groups: dict[str, list[str]] = {}
    for endpoint in card.endpoints.all:
        if card.id in CARD_ENDPOINTS:
            met = is_available(endpoint, tier0)
        else:
            met = tier0 == "engine_log" or tier0 == card.endpoints.requires_tier0
        name = endpoint
        if card.status != "implemented":
            group = _NOT_IMPLEMENTED
        elif endpoint in confirmatory:
            # A confirmatory test that cannot run keeps its place and resolves to unavailable.
            lacking = needs_tokens and verified and not met
            group = _CONFIRMATORY_NO_TOKENS if lacking else "Confirmatory"
            name = f"{endpoint} ({_arms(confirmatory[endpoint])})"
        elif needs_tokens and verified and not met:
            group = _NO_TOKENS
        elif endpoint not in card.endpoints.primary:
            group = _SECONDARY
        elif meta_for(card.id, endpoint).executable:
            group = "Exploratory"
        else:
            group = _NOT_COMPUTED
        groups.setdefault(group, []).append(name)
    sentences = [f"{group}: {', '.join(names)}." for group, names in groups.items()]
    if needs_tokens and not verified and card.status == "implemented":
        sentences.append(_TOKENS_UNVERIFIED)
    return " ".join(sentences)


def _design_context(
    config: StudyConfig,
    plan: StatisticalPlan,
    cards: Mapping[str, EvalCard],
    tier0: Any,
    verified: bool,
) -> dict[str, Any]:
    """What the design template prints beyond the legacy context, all from ``plan.design``."""
    design = plan.design
    if design is None:
        return {}
    hypotheses = design.hypotheses
    epsilon = design.sensitivity_epsilon
    confirmatory: dict[str, dict[str, list[str]]] = {}
    for hypothesis in hypotheses:
        confirmatory.setdefault(hypothesis.card_id, {}).setdefault(hypothesis.endpoint, []).append(
            hypothesis.id
        )
    families = design.families
    control_floor = (
        power.control_floor(design.alpha, families.negative_control)
        if families.negative_control >= 1
        else None
    )
    paired = [h for h in hypotheses if h.test == _PAIRED_TEST]
    selected = [cards[selection.card_id] for selection in config.cards]
    return {
        "design": design,
        "hypotheses": [
            _hypothesis_row(h, plan.contrasts[h.contrast], design.claim) for h in hypotheses
        ],
        "decision_matrix": VERDICT_RULES,
        "reason_texts": [
            {"reason": reason, "text": INCONCLUSIVE_REASON_TEXT[reason]}
            for rule in VERDICT_RULES
            if rule.verdict == "inconclusive"
            for reason in rule.reasons
        ],
        "sample_size_rows": [
            _sample_size_row(h, cards[h.card_id].dataset.items if h.card_id in cards else None)
            for h in hypotheses
        ],
        "controls": _control_rows(config, plan),
        "fixed_rules": _fixed_rules(plan),
        "confirmatory_endpoints": {
            card_id: "; ".join(f"{endpoint} ({_arms(ids)})" for endpoint, ids in endpoints.items())
            for card_id, endpoints in confirmatory.items()
        },
        # Per selected card: its endpoints with the role each has in this
        # study, and its task arguments without the permutation count, which
        # the statistical plan sets for every test.
        "card_endpoints": {
            card.id: _design_endpoints(card, confirmatory.get(card.id, {}), tier0, verified)
            for card in selected
        },
        "card_task_args": {
            card.id: {key: value for key, value in card.task_args.items() if key != "permutations"}
            for card in selected
        },
        # What the registered design cannot conclude, by hypothesis id.
        "never_equivalent": _arms(
            [h.id for h in hypotheses if reachable_verdicts(h, design.claim)["equivalent"]]
        ),
        "pooled": _arms([h.id for h in hypotheses if h.test != _PAIRED_TEST]),
        "pooled_count": sum(h.test != _PAIRED_TEST for h in hypotheses),
        "control_floor": control_floor,
        "paired_count": len(paired),
        "below_control_floor": _arms(
            [h.id for h in paired if control_floor is not None and h.sample_size.n < control_floor]
        ),
        # Levels as the document prints them. ε keeps two decimals and a margin
        # its shortest form, as in the result sentences of the analysis.
        "levels": {
            "alpha": f"{design.alpha:g}",
            "power": f"{design.power:.0%}",
            "marginal_power": f"{power.MARGINAL_POWER:.0%}",
            "min_completeness": f"{design.min_completeness:.0%}",
            "reference_effect": f"{design.reference_effect:g}",
            "epsilon": None if epsilon is None else f"{epsilon:.2f}",
            "n_floor": hypotheses[0].sample_size.n_floor if hypotheses else None,
        },
        "result_checks": _result_checks(),
    }


@dataclass(frozen=True)
class Renderer:
    template: str
    cards: Mapping[str, EvalCard]
    asset_hashes: Mapping[str, str | None]
    #: The template of a study with a design block. ``template`` stays the legacy one.
    design_template: str = ""

    def render(
        self,
        study_config: StudyConfig,
        readiness: BaseModel | None = None,
        expectations: str | None = None,
    ) -> str:
        if expectations is not None and expectations != study_config.expectations:
            raise ValueError("expectations must match the frozen StudyConfig.expectations")
        config = study_config
        designed = config.design is not None
        if designed and not self.design_template:
            raise ValueError("this renderer was built without the design template")
        template = self.design_template if designed else self.template
        plan = StatisticalPlan.from_config(config, self.cards)
        report = readiness.model_dump(mode="json") if readiness is not None else None
        tier0 = report["tier0_source"] if report else None
        warnings: list[str] = []
        for model in config.models:
            if not model.checkpoint:
                warnings.append(f"{model.id}: exact model checkpoint/revision is not frozen.")
        if config.judge.model.startswith("PIN_") or "latest" in config.judge.model.lower():
            warnings.append(
                "Judge model is unresolved; freeze an exact identifier before full run."
            )
        if config.judge.temperature not in (None, 0):
            warnings.append(
                f"Judge temperature is {config.judge.temperature}: its scores vary between "
                "repeated calls on the same reply."
            )
        if report is None:
            warnings.append(
                "Readiness not supplied: arm availability, entropy health and achieved determinism are unverified."
            )
        elif not report["equivalence_ok"]:
            warnings.append(
                "Readiness reports failed sampler equivalence; no equivalent-pipeline source claim is justified."
            )
        if report and (not report["entropy"]["rpc_ok"] or report["entropy"]["currently_degraded"]):
            warnings.append(
                "Readiness reports unhealthy/degraded quantum entropy; affected output cannot be labelled quantum."
            )
        assets = dict(self.asset_hashes)
        for name, expected in (
            (
                "questionnaires",
                config.supervised.questionnaires_hash if config.supervised else None,
            ),
            ("target_pool", config.supervised.target_pool_hash if config.supervised else None),
        ):
            actual = assets.get(name)
            if config.supervised and (not actual or not expected):
                warnings.append(f"{name}: unavailable or not frozen in the study config.")
            if expected and actual != expected:
                warnings.append(
                    f"{name}: declared hash does not match the supplied asset snapshot."
                )
        selected = [self.cards[s.card_id] for s in config.cards]
        overrides = {
            s.card_id: CardOverrides(
                k=s.overrides.k, items=s.overrides.items, max_tokens=s.overrides.max_tokens
            )
            for s in config.cards
        }
        assumptions = EstimateAssumptions.model_validate(config.estimates.model_dump())
        card_rows = []
        for card in selected:
            ov = overrides[card.id]
            revision = card.dataset.revision
            if card.dataset.scheme == "repo":
                revision = assets.get(f"dataset:{card.id}")
            if card.dataset.scheme != "derived" and not revision:
                warnings.append(
                    f"{card.id}: dataset revision/content hash unavailable; pin before full run."
                )
            status = (
                "implemented"
                if card.status == "implemented"
                else "unavailable: implementation planned"
            )
            endpoint_rows = []
            for endpoint in card.endpoints.all:
                available = card.status == "implemented"
                if card.id in CARD_ENDPOINTS:
                    available = available and is_available(endpoint, tier0)
                elif card.endpoints.requires_tier0 != "none":
                    available = available and (
                        tier0 == "engine_log" or tier0 == card.endpoints.requires_tier0
                    )
                endpoint_rows.append(
                    f"{endpoint}: {'available under supplied readiness' if available else 'unavailable'}"
                )
            # A study's own token limit is printed in place of the card's, by
            # both templates; the designed one also says whose limit it is.
            limit = effective_max_tokens(card, ov.max_tokens)
            shown = (
                card if limit == card.max_tokens else card.model_copy(update={"max_tokens": limit})
            )
            card_rows.append(
                {
                    "card": shown,
                    "max_tokens_note": ""
                    if limit == card.max_tokens
                    else f" (set by this study; the card has {card.max_tokens})",
                    "k": ov.k if ov.k is not None else card.k,
                    "items": ov.items if ov.items is not None else card.dataset.items,
                    "revision": revision
                    or (
                        "derived from frozen run records"
                        if card.dataset.scheme == "derived"
                        else "UNPINNED"
                    ),
                    "status": status,
                    "endpoints": "; ".join(endpoint_rows),
                    "judge": config.judge.model
                    if card.judge.model == "study_judge"
                    else card.judge.model or "none",
                }
            )
        arms = []
        budget = []
        for arm in config.arms:
            result = report["arms"].get(arm.id) if report else None
            available = arm.availability == "available" and (result is None or result["available"])
            reason = arm.unavailable_reason or (result.get("reason") if result else None)
            if arm.source == "prng_epsilon" and (report is None or not report["epsilon_supported"]):
                available, reason = False, "epsilon support not demonstrated"
            status = (
                "available under supplied readiness"
                if available and result
                else "unverified"
                if available
                else "unavailable"
            )
            model = next(m for m in config.models if m.id == arm.model)
            envelope = (
                build_envelope(
                    arm,
                    SampleContext(card_id="prereg", prompt_id="example", epoch=0, max_tokens=1),
                    conditioning=config.conditioning,
                )
                if model.family == "qr_llm" and arm.source != "provider"
                else {}
            )
            arms.append(
                {
                    "arm": arm,
                    "status": status,
                    "reason": reason or "",
                    "envelope": canonical_json(envelope),
                }
            )
            estimate = estimate_selection(selected, arm.model, assumptions, overrides)
            row = {
                "arm": arm.id,
                "status": status,
                "tokens": estimate.tokens_per_arm,
                "generation_h": estimate.time_unsupervised_h,
                "observer_h": 0.0,
                "sessions": 0,
                "wall_h": estimate.time_unsupervised_h,
            }
            if arm.observer_mode == "supervised" and config.supervised:
                settings = config.supervised.settings
                streams = settings.streams if config.supervised.display == "tiled" else 1
                tps = min(streams * assumptions.reading_tps, assumptions.tps_by_model[arm.model])
                hours = estimate.tokens_per_arm / tps / 3600
                sessions = math.ceil(hours * 60 / settings.session_min)
                wall = (
                    sessions * settings.session_min
                    + max(0, sessions - 1) * settings.break_min
                    + config.supervised.intention_timer_min
                    + max(0, sessions - 1) * config.supervised.refocus_s / 60
                ) / 60
                row.update(observer_h=hours, sessions=sessions, wall_h=wall)
            budget.append(row)
        environment = Environment(
            undefined=StrictUndefined, autoescape=True, keep_trailing_newline=True
        )
        # All untrusted prose is escaped explicitly for Markdown, not HTML output.
        from markupsafe import Markup

        escape = _design_text if designed else _text
        environment.filters["md"] = lambda value: Markup(escape(value))  # nosec B704 - escaped above
        environment.filters["jsonblock"] = lambda value: Markup(_fence(value))  # nosec B704 - fenced JSON
        markdown = environment.from_string(template).render(
            config=config,
            config_json=canonical_json(config),
            config_hash=config_hash(config),
            readiness_json=canonical_json(readiness) if readiness else "null",
            readiness=report,
            plan=plan,
            plan_json=plan.canonical_json(),
            cards=card_rows,
            arms=arms,
            budget=budget,
            base_tokens=sum(
                row["tokens"]
                for row, arm in zip(budget, config.arms, strict=True)
                if arm.availability == "available"
            ),
            assets=assets,
            warnings=warnings,
            expectations=config.expectations
            or "Not stated; record prior expectations before publishing.",
            # The hash names the template these bytes were rendered from.
            template_hash=prereg_hash(template),
            **_design_context(config, plan, self.cards, tier0, report is not None),
        )
        return normalize_lf(markdown)


def load_renderer(
    *, cards: Mapping[str, EvalCard] | None = None, repo_root: Path = REPO_ROOT
) -> Renderer:
    registry = dict(cards) if cards is not None else load_cards(repo_root / "evals")
    assets: dict[str, str | None] = {}
    paths = {
        "questionnaires": repo_root / "config/questionnaires.yaml",
        "target_pool": repo_root / "evals/_pools/targets_200.yaml",
    }
    for card in registry.values():
        if card.dataset.scheme == "repo":
            candidate = (repo_root / card.dataset.source.split(":", 1)[1]).resolve()
            if not candidate.is_relative_to(repo_root.resolve()):
                raise ValueError("dataset path escapes repository")
            paths[f"dataset:{card.id}"] = candidate
    for name, path in paths.items():
        if path.is_file():
            assets[name] = prereg_hash(path.read_text(encoding="utf-8"))
        elif path.is_dir():
            manifest = {
                p.relative_to(path).as_posix(): prereg_hash(p.read_text(encoding="utf-8"))
                for p in sorted(path.rglob("*"))
                if p.is_file()
            }
            assets[name] = config_hash(manifest) if manifest else None
        else:
            assets[name] = None
    template = (REPO_ROOT / "docs/prereg-template.md.j2").read_text(encoding="utf-8")
    design_template = (REPO_ROOT / "docs/prereg-design-template.md.j2").read_text(encoding="utf-8")
    return Renderer(template, MappingProxyType(registry), MappingProxyType(assets), design_template)


def render(
    study_config: StudyConfig,
    readiness: BaseModel | None = None,
    expectations: str | None = None,
    *,
    renderer: Renderer | None = None,
) -> str:
    """Render with explicit immutable inputs, or load the repository defaults."""
    return (renderer if renderer is not None else load_renderer()).render(
        study_config, readiness, expectations
    )
