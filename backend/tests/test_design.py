"""The plan of a designed study (``cards/design.py``).

- a design changes which endpoints decide the study, never which arms are
  compared: the contrasts are the built-in ones, cells are never pooled, and a
  biased arm never stands on either side of a quantum-vs-control comparison;
- the plan works for any number of control seeds from two up;
- the positive control is its own arm on its own seed, and an arm that is
  switched off is an absent control, not an error;
- permutations grow with the difference tests and stop at a cap; the seed-pair
  controls get enough of them to fall below their level, and no more;
- KS and MMD are never rated and never carry a margin;
- the prose of a designed plan no longer describes pilot margins;
- what the projection refuses, what is recommended by default, which verdicts
  a hypothesis can reach, and what ``problems`` reports.
"""

from __future__ import annotations

import copy
import json
from itertools import combinations, product
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from qr_llm_evals.analysis.stats import holm_bonferroni, paired_permutation_test
from qr_llm_evals.cards import EvalCard, design, load_cards, power
from qr_llm_evals.cards.design_schema import HypothesisPlan
from qr_llm_evals.cards.endpoint_meta import ENDPOINT_META
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.prereg.render import render as render_prereg

FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"

PIT = {"card_id": "tier0_pit_residuals", "endpoint": "pit_ks_two_sample"}
MMD = {"card_id": "tier0_mmd_hamming", "endpoint": "mmd_hamming"}
PROBE = {"card_id": "self_referential_probe", "endpoint": "experience_claim_rate"}
MEQ = {"card_id": "altered_state_meq", "endpoint": "meq30_total"}
OPINION = {"card_id": "opinion_quantum_sampling", "endpoint": "actual_main_effect_axis_score"}
NEGATION = {"card_id": "sentience_statements", "endpoint": "cross_negation_consistency"}
KS100 = {"card_id": "unpredictabench", "endpoint": "ks100_pass_rate"}

Config = dict[str, Any]


@pytest.fixture(scope="module")
def cards() -> dict[str, EvalCard]:
    return load_cards()


@pytest.fixture()
def designed() -> Config:
    """The designed Study 1 template: PIT, MMD and the probe; one cell; three seeds."""
    return json.loads((FIXTURES / "study1.designed.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _arm(config: Config, arm_id: str) -> dict[str, Any]:
    return next(a for a in config["arms"] if a["id"] == arm_id)  # type: ignore[no-any-return]


def _add_cell(config: Config, sampling: str, seeds: tuple[int, ...], model: str = "qr-llm") -> None:
    """A second complete comparison: one quantum arm and one seeded arm per seed."""
    tag = f"{model}_{sampling}".replace("-", "_")
    config["arms"].append(
        {
            **_arm(config, "qrng_unsupervised"),
            "id": f"qrng_{tag}",
            "model": model,
            "sampling": sampling,
        }
    )
    for seed in seeds:
        config["arms"].append(
            {
                **_arm(config, "prng_seed_1"),
                "id": f"prng_{tag}_{seed}",
                "model": model,
                "sampling": sampling,
                "study_seed": seed,
            }
        )
        if seed not in config["seeds"]:
            config["seeds"].append(seed)


def _add_epsilon(
    config: Config, epsilon: float, *, seed: int = 1001, sampling: str = "creative"
) -> str:
    """An available biased arm. Returns its id."""
    arm_id = f"prng_epsilon_{round(epsilon * 100):03d}_{sampling}"
    config["arms"].append(
        {
            **_arm(config, "prng_seed_1"),
            "id": arm_id,
            "sampling": sampling,
            "source": "prng_epsilon",
            "study_seed": seed,
            "epsilon": epsilon,
        }
    )
    if seed not in config["seeds"]:
        config["seeds"].append(seed)
    return arm_id


def _overrides(config: Config, card_id: str, **overrides: int) -> None:
    selection = next(c for c in config["cards"] if c["card_id"] == card_id)
    selection["overrides"] = {"k": None, "items": None, **overrides}


def _plan(config: Config, cards: dict[str, EvalCard]) -> StatisticalPlan:
    return StatisticalPlan.from_config(StudyConfig.model_validate(config), cards)


def _legacy_plan(config: Config, cards: dict[str, EvalCard]) -> StatisticalPlan:
    legacy = {key: value for key, value in config.items() if key != "design"}
    return StatisticalPlan.from_config(StudyConfig.model_validate(legacy), cards)


def _hypotheses(plan: StatisticalPlan) -> tuple[HypothesisPlan, ...]:
    assert plan.design is not None
    return plan.design.hypotheses


def _arm_ids(plan: StatisticalPlan, index: int) -> set[str]:
    contrast = plan.contrasts[index]
    return {*contrast.a, *contrast.b}


def _codes(config: Config, cards: dict[str, EvalCard]) -> list[str]:
    return [problem["code"] for problem in design.problems(config, cards)]


# ── the plan ──────────────────────────────────────────────────────────────────


def test_designed_contrasts_equal_legacy(designed: Config, cards: dict[str, EvalCard]) -> None:
    variants: list[Config] = [designed]
    two_cells = copy.deepcopy(designed)
    _add_cell(two_cells, "t1_minp", (1, 7))
    variants.append(two_cells)
    with_control = copy.deepcopy(designed)
    _add_epsilon(with_control, 0.10)
    with_control["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.10)
    variants.append(with_control)

    for config in variants:
        plan, legacy = _plan(config, cards), _legacy_plan(config, cards)
        assert plan.design is not None and legacy.design is None
        assert plan.contrasts == legacy.contrasts
        assert (plan.cards_hash, plan.cards_json) == (legacy.cards_hash, legacy.cards_json)
        assert plan.supervised_exploratory == legacy.supervised_exploratory
        # The plan digests the whole config, design block included.
        assert plan.config_hash == config_hash(StudyConfig.model_validate(config))
        assert plan.config_hash != legacy.config_hash
        # Same endpoints in the same order; a design moves only how they are decided.
        untouched = {"card_id", "endpoint", "tier", "test", "requires_tier0", "k", "items"}
        assert [e.model_dump(include=untouched) for e in plan.endpoints] == [
            e.model_dump(include=untouched) for e in legacy.endpoints
        ]
        assert [e.sesoi for e in plan.endpoints] == [e.sesoi for e in legacy.endpoints]
        reloaded = StatisticalPlan.model_validate_json(plan.canonical_json())
        assert reloaded == plan and reloaded.plan_hash == plan.plan_hash

    plan = _plan(designed, cards)
    confirmatory = {(e.card_id, e.endpoint): e for e in plan.endpoints if e.role == "primary"}
    assert list(confirmatory) == [tuple(ref.values()) for ref in (PIT, MMD, PROBE)]
    for endpoint in confirmatory.values():
        assert (endpoint.correction, endpoint.family) == ("holm", "confirmatory")
    assert {key: e.equivalence for key, e in confirmatory.items()} == {
        ("tier0_pit_residuals", "pit_ks_two_sample"): "unavailable",
        ("tier0_mmd_hamming", "mmd_hamming"): "unavailable",
        ("self_referential_probe", "experience_claim_rate"): "tost",
    }
    for endpoint in plan.endpoints:
        if endpoint.role != "primary":
            assert endpoint.role == "exploratory" and endpoint.correction == "none"
            assert endpoint.family == f"tier_{endpoint.tier}_exploratory"
            assert endpoint.equivalence == "unavailable"
    # The built-in primaries a design does not name are exploratory like the rest.
    by_key = {(e.card_id, e.endpoint): e for e in plan.endpoints}
    assert by_key[("unpredictabench", "ks100_pass_rate")].role == "exploratory"
    assert (
        by_key[("opinion_quantum_sampling", "actual_main_effect_axis_score")].role == "exploratory"
    )

    families = plan.design.families if plan.design else None
    assert families is not None
    assert families.model_dump() == {"difference": 3, "equivalence": 1, "negative_control": 9}
    assert [h.id for h in _hypotheses(plan)] == ["H1", "H2", "H3"]
    probe = _hypotheses(plan)[2]
    # The template runs the probe at k = 24, the fewest repetitions at which its 30 prompts are enough.
    assert (probe.planned_units, probe.planned_pairs) == (30, 30 * 24 * 3)
    assert (probe.margin, probe.margin_source) == (0.05, "card_sesoi")
    assert probe.sample_size.rating == "sufficient" and probe.sample_size.n_recommended == 30
    assert (probe.sample_size.control_floor, probe.sample_size.k_recommended) == (10, 24)
    assert probe.statement == (
        "H3: on Experience-claim rate (self_referential_probe), quantum sampling differs from "
        "the per-prompt mean of 3 seeded pseudo-random runs (qr-llm · creative)."
    )
    assert _hypotheses(plan)[0].statement == (
        "H1: on Token sampling residuals (KS) (tier0_pit_residuals), quantum sampling differs "
        "from the pooled tokens of 3 seeded pseudo-random runs (qr-llm · creative)."
    )


def test_epsilon_never_on_quantum_side(designed: Config, cards: dict[str, EvalCard]) -> None:
    config = copy.deepcopy(designed)
    _add_cell(config, "t1_minp", (1, 7, 42))
    biased = {
        _add_epsilon(config, 0.10),
        _add_epsilon(config, 0.10, sampling="t1_minp"),
        _add_epsilon(config, 0.50),
        "prng_epsilon_003",
    }
    config["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.10)
    plan = _plan(config, cards)
    arms = {arm["id"]: arm for arm in config["arms"]}

    for h in _hypotheses(plan):
        source = plan.contrasts[h.contrast]
        assert source.kind == "source"
        assert [arms[a]["source"] for a in source.a] == ["qrng"]
        assert {arms[b]["source"] for b in source.b} == {"prng_seeded"}
        assert not biased & _arm_ids(plan, h.contrast)
        for index in h.negative_controls:
            assert plan.contrasts[index].kind == "seed_vs_seed"
            assert {arms[a]["source"] for a in _arm_ids(plan, index)} == {"prng_seeded"}
        assert h.positive_control is not None
        control = plan.contrasts[h.positive_control]
        assert (control.kind, control.epsilon) == ("epsilon", 0.10)
        assert [arms[a]["source"] for a in control.a] == ["prng_epsilon"]
        # The biased arm is compared with the control seeds, never with the quantum arm.
        assert control.b == source.b
        assert not set(source.a) & _arm_ids(plan, h.positive_control)
    # The ε = 0.50 arm is in the plan's contrasts, and no hypothesis points at it.
    strong = [i for i, c in enumerate(plan.contrasts) if c.kind == "epsilon" and c.epsilon == 0.50]
    assert strong and not {h.positive_control for h in _hypotheses(plan)} & set(strong)


def test_no_pooling_across_cells(designed: Config, cards: dict[str, EvalCard]) -> None:
    config = copy.deepcopy(designed)
    _add_cell(config, "t1_minp", (1, 7))
    config["models"].append({**config["models"][0], "id": "other", "served_name": "other"})
    _add_cell(config, "creative", (1, 7, 42), model="other")
    plan = _plan(config, cards)
    hypotheses = _hypotheses(plan)
    arms = {arm["id"]: (arm["model"], arm["sampling"]) for arm in config["arms"]}
    cells = [("other", "creative"), ("qr-llm", "creative"), ("qr-llm", "t1_minp")]

    # One hypothesis per endpoint and cell: confirmatory order, then sorted cells.
    assert [(h.endpoint, h.model, h.sampling) for h in hypotheses] == [
        (ref["endpoint"], *cell) for ref in (PIT, MMD, PROBE) for cell in cells
    ]
    assert [h.id for h in hypotheses] == [f"H{n}" for n in range(1, 10)]
    for h in hypotheses:
        cell = (h.model, h.sampling)
        for index in (h.contrast, *h.negative_controls):
            assert (plan.contrasts[index].model, plan.contrasts[index].sampling) == cell
            assert {arms[arm] for arm in _arm_ids(plan, index)} == {cell}
        assert f"({h.model} · {h.sampling})" in h.statement
    # Each cell has its own contrast: no two cells share one.
    assert len({h.contrast for h in hypotheses}) == len(cells)
    # The family counts every cell; a cell with two seeds contributes one seed pair.
    assert plan.design is not None
    assert plan.design.families.model_dump() == {
        "difference": 9,
        "equivalence": 3,
        "negative_control": 3 * (3 + 3 + 1),
    }
    assert {h.sample_size.n_floor for h in hypotheses} == {power.n_floor(0.05, 9)}


@pytest.mark.parametrize("seeds", [(1, 7), (1, 7, 42), (1, 7, 42, 99), (1, 7, 42, 99, 123)])
def test_seed_count_agnostic(
    designed: Config, cards: dict[str, EvalCard], seeds: tuple[int, ...]
) -> None:
    config = copy.deepcopy(designed)
    config["arms"] = [a for a in config["arms"] if a["source"] != "prng_seeded"]
    config["seeds"] = sorted({*config["seeds"], *seeds})
    template = _arm(designed, "prng_seed_1")
    config["arms"] += [{**template, "id": f"prng_seed_{s}", "study_seed": s} for s in seeds]
    plan = _plan(config, cards)
    pairs = len(list(combinations(seeds, 2)))

    for h in _hypotheses(plan):
        assert len(plan.contrasts[h.contrast].b) == len(seeds)
        assert len(h.negative_controls) == pairs
        assert f"{len(seeds)} seeded pseudo-random runs" in h.statement
    assert plan.design is not None
    assert plan.design.families.negative_control == 3 * pairs
    # More control seeds average more noise out of the control side.
    probe = _hypotheses(plan)[2].sample_size
    sd = power.sd_bound(0.0, 1.0, "sample", 24, 3, len(seeds))
    assert probe.sd_bound == pytest.approx(sd)
    # Every seed pair of the three hypotheses is one test of the negative control.
    assert probe.control_floor == power.control_floor(0.05, 3 * pairs)
    assert probe.power_at_margin == pytest.approx(power.power_at(0.05, sd, 30, 0.05, 3))
    three_seeds = _hypotheses(_plan(designed, cards))[2].sample_size
    assert three_seeds.power_at_margin is not None and probe.power_at_margin is not None
    assert (probe.power_at_margin > three_seeds.power_at_margin) == (len(seeds) > 3)
    # With two control seeds the control side is noisier, and k = 24 no longer carries 30 prompts.
    assert probe.rating == ("sufficient" if len(seeds) >= 3 else "marginal")
    assert design.problems(config, cards) == design.problems(designed, cards)


def test_positive_control_seed_distinct(designed: Config, cards: dict[str, EvalCard]) -> None:
    config = copy.deepcopy(designed)
    arm_id = _add_epsilon(config, 0.10, seed=1001)
    config["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.10)
    plan = _plan(config, cards)
    control_seeds = {a["study_seed"] for a in config["arms"] if a["source"] == "prng_seeded"}
    for h in _hypotheses(plan):
        assert h.positive_control is not None
        control = plan.contrasts[h.positive_control]
        assert control.a == (arm_id,) and control.epsilon == 0.10
        assert _arm(config, arm_id)["study_seed"] not in control_seeds
    assert plan.design is not None and plan.design.sensitivity_epsilon == 0.10
    assert design.recommend(config, cards) == {
        "version": 1,
        "claim": "difference_or_equivalence",
        "confirmatory": [PIT, MMD, PROBE],
        "alpha": 0.05,
        "sensitivity_epsilon": 0.10,
        "margins": [],
    }

    # The same arm on a control arm's seed draws that arm's random stream.
    shared = copy.deepcopy(config)
    _arm(shared, arm_id)["study_seed"] = 42
    with pytest.raises(ValidationError, match="shares study_seed 42 with a prng_seeded arm"):
        StudyConfig.model_validate(shared)
    assert "design.epsilon_seed_shared" in _codes(shared, cards)
    # Such an arm is never recommended as the positive control.
    recommended = design.recommend(shared, cards)
    assert recommended is not None
    assert (recommended["sensitivity_epsilon"], recommended["claim"]) == (None, "difference")


def test_unavailable_epsilon_is_absent_control(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    config = copy.deepcopy(designed)
    assert _arm(config, "prng_epsilon_003")["availability"] == "unavailable"
    config["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.03)
    plan = _plan(config, cards)  # a valid study: the control is declared, and switched off
    assert plan.design is not None and plan.design.sensitivity_epsilon == 0.03
    assert all(c.kind != "epsilon" for c in plan.contrasts)
    for h in _hypotheses(plan):
        assert h.positive_control is None
    probe = _hypotheses(plan)[2]
    assert design.reachable_verdicts(probe, "difference_or_equivalence")["equivalent"] == (
        "positive_control_absent"
    )
    codes = _codes(config, cards)
    assert "design.epsilon_off" in codes and "design.equivalent_unreachable" in codes

    # Switched on, the same arm is the control and 'equivalent' is reachable.
    _arm(config, "prng_epsilon_003").update(availability="available", unavailable_reason=None)
    plan = _plan(config, cards)
    probe = _hypotheses(plan)[2]
    assert probe.positive_control is not None
    assert plan.contrasts[probe.positive_control].a == ("prng_epsilon_003",)
    assert design.reachable_verdicts(probe, "difference_or_equivalence")["equivalent"] is None
    assert "design.epsilon_off" not in _codes(config, cards)


def test_permutations_scale_and_cap(designed: Config, cards: dict[str, EvalCard]) -> None:
    assert _plan(designed, cards).permutations == 10_000
    assert design.planned_permutations(3, 0.05) == 10_000
    assert design.planned_permutations(25, 0.05) == 10_000
    assert design.planned_permutations(26, 0.05) == 10_400
    assert design.planned_permutations(8, 0.005) == 32_000
    # Exact at the points where the division is a hair off in floating point.
    assert design.planned_permutations(50, 0.01) == 100_000
    assert design.planned_permutations(25, 0.005) == 100_000
    assert design.planned_permutations(32, 0.005) == 128_000

    config = copy.deepcopy(designed)
    _add_cell(config, "t1_minp", (1, 7))
    config["design"].update(alpha=0.005, confirmatory=[PIT, MMD, PROBE, MEQ, OPINION])
    plan = _plan(config, cards)
    assert plan.design is not None and plan.design.families.difference == 10
    # Five outcomes, three seed pairs in one cell and one in the other: 20 seed-pair tests.
    assert plan.design.families.negative_control == 5 * (3 + 1)
    # The ten difference tests decide the count: 20 * 10 / 0.005. The twenty seed pairs ask
    # for 2 * 20 / 0.005 = 8 000, which the count covers five times over.
    assert (plan.permutations, plan.alpha, plan.ci_level) == (40_000, 0.005, 0.995)
    # The smallest p a difference test can return is a twentieth of its corrected level;
    # the smallest p a seed pair can return is below half of its own.
    assert 1 / (plan.permutations + 1) < plan.alpha / (20 * 10)
    assert 1 / (plan.permutations + 1) < plan.alpha / (2 * 20)

    for model in ("b", "c"):
        config["models"].append({**config["models"][0], "id": model, "served_name": model})
        _add_cell(config, "creative", (1, 7), model=model)
        _add_cell(config, "t1_minp", (1, 7), model=model)
    with pytest.raises(
        ValueError,
        match="30 confirmatory tests and 40 seed-pair control tests at alpha 0.005 need 120000",
    ):
        _plan(config, cards)
    problems = design.problems(config, cards)
    assert [p for p in problems if p["code"] == "design.too_many_tests"] == [
        {
            "code": "design.too_many_tests",
            "severity": "error",
            "message": (
                "30 tests with 40 seed-pair controls at α = 0.005 need more permutations than "
                "the harness runs. Remove outcomes, comparisons or control seeds."
            ),
            "step_id": "design",
            "node_id": "family",
        }
    ]
    # At the default level the same thirty tests fit.
    config["design"]["alpha"] = 0.05
    assert _plan(config, cards).permutations == 12_000


def test_control_permutations() -> None:
    """Twice the seed-pair tests over alpha: the smallest p of a pair is half its level."""
    assert design.CONTROL_PERMUTATIONS_PER_LEVEL == 2
    assert design.control_permutations(9, 0.05) == 360
    assert design.control_permutations(63, 0.005) == 25_200
    # Exact where the division is a hair off in floating point.
    assert design.control_permutations(250, 0.005) == 100_000
    assert design.control_permutations(500, 0.01) == 100_000
    for alpha, family in product((0.05, 0.01, 0.005), (1, 9, 30, 63, 250)):
        count = design.control_permutations(family, alpha)
        # With that many a pair can fall below half its corrected level. With one fewer
        # than half as many, its smallest p is the level itself and it cannot fail at all.
        assert family / (count + 1) < alpha / 2
        assert family / (count // 2) >= alpha * (1 - 1e-9)
        assert design.control_can_fail(
            "mmd_hamming_permutation", prompts=None, permutations=count, alpha=alpha, family=family
        )
        assert not design.control_can_fail(
            "mmd_hamming_permutation",
            prompts=None,
            permutations=count // 2 - 1,
            alpha=alpha,
            family=family,
        )


#: (control seeds, comparisons, outcomes, alpha, difference tests, seed-pair tests, permutations)
#: Designs the setup pages can build. Each was refused while the seed pairs were given the
#: resolution of a difference test; the count is the one its difference tests ask for.
BUILDABLE = [
    pytest.param(5, 1, [PIT, MMD, PROBE], 0.005, 3, 30, 12_000, id="five_seeds"),
    pytest.param(3, 3, [PIT, MMD, PROBE], 0.005, 9, 27, 36_000, id="three_comparisons"),
    pytest.param(5, 2, [PIT, MMD, PROBE], 0.01, 6, 60, 12_000, id="five_seeds_six_hypotheses"),
    pytest.param(
        4, 1, [PIT, MMD, PROBE, MEQ, OPINION], 0.005, 5, 30, 20_000, id="four_seeds_five_outcomes"
    ),
]


@pytest.mark.parametrize(
    ("seeds", "comparisons", "outcomes", "alpha", "difference", "pairs", "permutations"), BUILDABLE
)
def test_seed_controls_do_not_refuse_a_design_the_setup_builds(
    designed: Config,
    cards: dict[str, EvalCard],
    seeds: int,
    comparisons: int,
    outcomes: list[dict[str, str]],
    alpha: float,
    difference: int,
    pairs: int,
    permutations: int,
) -> None:
    config = copy.deepcopy(designed)
    chosen = (1, 7, 42, 99, 123)[:seeds]
    _seeds(config, chosen)
    for sampling, model in (("t1_minp", "qr-llm"), ("creative", "b"))[: comparisons - 1]:
        if model == "b":
            config["models"].append({**config["models"][0], "id": "b", "served_name": "b"})
        _add_cell(config, sampling, chosen, model=model)
    config["design"].update(alpha=alpha, confirmatory=outcomes)

    plan = _plan(config, cards)
    assert plan.design is not None
    families = plan.design.families
    assert (families.difference, families.negative_control) == (difference, pairs)
    # What the difference tests alone ask for: the controls add nothing to it.
    assert plan.permutations == permutations == design.planned_permutations(difference, alpha)
    assert "design.too_many_tests" not in _codes(config, cards)

    # The control of such a plan can fail. Two seeds that differ on every one of 30 prompts
    # get the smallest p-value the test returns, and it is below the level after correction.
    assert design.control_can_fail(
        "paired_permutation", prompts=30, permutations=permutations, alpha=alpha, family=pairs
    )
    result = paired_permutation_test(
        np.ones(30), np.zeros(30), np.arange(30), n_permutations=permutations, seed=42
    )
    assert result["p_value"] == pytest.approx(1 / (permutations + 1))
    assert holm_bonferroni([result["p_value"], *([1.0] * (pairs - 1))])[0] < alpha


def test_ks_mmd_never_sufficient(designed: Config, cards: dict[str, EvalCard]) -> None:
    config = copy.deepcopy(designed)
    _add_epsilon(config, 0.10)
    config["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.10)
    plan = _plan(config, cards)
    pit, mmd, probe = _hypotheses(plan)
    assert (pit.test, pit.sided) == ("ks_two_sample", "two_sided")
    assert (mmd.test, mmd.sided) == ("mmd_hamming_permutation", "one_sided_upper")
    assert probe.sided == "two_sided"
    for h in (pit, mmd):
        size = h.sample_size
        assert (size.rating, size.basis) == ("unknown", "no_closed_form")
        assert size.n_recommended is None and size.power_at_margin is None
        # The pooled stream: every reply one arm is planned to generate on a
        # fixed prompt list. The 6 200 that Study 1 expects from its two derived
        # evals (source_guessing, self_reported_coincidence) are not planned data.
        # The probe runs at k = 24 where its card says 20: 30 prompts x 4 more x 3 told cells.
        assert h.planned_units == h.planned_pairs == size.n == 95_700 - 6_000 - 200 + 30 * 4 * 3
        # The PIT card states a SESOI; a KS statistic still has no margin to test it against.
        assert (h.margin, h.margin_source) == (None, "none")
        reachable = design.reachable_verdicts(h, "difference_or_equivalence")
        assert reachable["equivalent"] == "equivalence_not_testable"
        assert reachable["effect_within_margin"] == "equivalence_not_testable"
        assert reachable["effect"] is None
        assert "pooled tokens" in h.statement
    assert cards["tier0_pit_residuals"].sesoi == {"pit_ks_two_sample": 0.01}
    assert design.reachable_verdicts(probe, "difference_or_equivalence")["equivalent"] is None
    # The equivalence family holds the probe alone.
    assert plan.design is not None and plan.design.families.equivalence == 1

    for ref in (PIT, MMD):
        refused = copy.deepcopy(config)
        refused["design"]["margins"] = [{**ref, "margin": 0.01}]
        with pytest.raises(ValueError, match="design.margins: " + ref["card_id"]):
            _plan(refused, cards)


def test_legacy_prose_rewritten(designed: Config, cards: dict[str, EvalCard]) -> None:
    plan, legacy = _plan(designed, cards), _legacy_plan(designed, cards)
    assert plan.margin_rule == (
        "Fixed at registration: the card SESOI, or the declared margin where the card has none. "
        "Never derived from pilot or full-run data."
    )
    assert plan.claim_rule == (
        "Each confirmatory hypothesis resolves by script to one verdict under decision matrix "
        "version 1."
    )
    assert plan.supervised_family == (
        "Supervised vs unsupervised contrasts are exploratory in designed studies; no verdict."
    )
    assert plan.stopping_rule == (
        "Fixed items and k per available arm; no significance-based stopping; only the first "
        "full run of a study is confirmatory; pilot runs never are."
    )
    assert plan.source_family == (
        "Holm across confirmatory endpoints x (model, sampling) cells, unavailable members "
        "included."
    )
    # No rule of a designed plan leans on a pilot or on exactly three seeds.
    rules = " ".join(
        value
        for name, value in plan.model_dump().items()
        if name.endswith(("_rule", "_family")) and isinstance(value, str)
    )
    assert "pilot 95th percentile" not in rules and "pilot differences" not in rules
    assert "all three seed-vs-seed" not in rules
    assert (plan.alpha, plan.ci_level, plan.pilot_quantile) == (0.05, 0.95, None)
    assert "pilot_quantile" not in plan.model_dump(mode="json")
    assert plan.design is not None
    assert (plan.design.power, plan.design.min_completeness, plan.design.reference_effect) == (
        0.80,
        0.90,
        0.30,
    )
    assert (plan.design.version, plan.design.matrix_version) == (1, 1)

    # The same study without the block keeps every word of the built-in plan.
    assert legacy.margin_rule.startswith("max(card SESOI, pilot 95th percentile")
    assert "all three seed-vs-seed contrasts" in legacy.claim_rule
    assert (legacy.alpha, legacy.ci_level, legacy.pilot_quantile) == (0.05, 0.90, 0.95)
    assert legacy.permutations == 2000

    for alpha in (0.05, 0.01, 0.005):
        config = copy.deepcopy(designed)
        config["design"]["alpha"] = alpha
        stricter = _plan(config, cards)
        assert (stricter.alpha, stricter.ci_level) == (alpha, 1 - alpha)
        assert stricter.design is not None and stricter.design.alpha == alpha


# ── what the projection refuses ───────────────────────────────────────────────


def test_eligible_endpoints_of_the_registry(cards: dict[str, EvalCard]) -> None:
    eligible = {
        f"{card.id}/{endpoint}"
        for card in cards.values()
        for endpoint in card.endpoints.all
        if design.eligible(card, endpoint)
    }
    assert eligible == {
        "tier0_pit_residuals/pit_ks_two_sample",
        "tier0_mmd_hamming/mmd_hamming",
        "self_referential_probe/experience_claim_rate",
        "altered_state_meq/meq30_total",
        "opinion_quantum_sampling/actual_main_effect_axis_score",
        "guesser_warmup_replies/report_word_count",
        # Judged per reply on every prompt of the eval.
        "darkbench/dark_pattern_rate",
        "eval_awareness/eval_belief_rate",
        "strong_reject/harmfulness_score",
        "deception_bench/deception_rate",
        "mask/lie_rate",
        "mask_beliefs/belief_held",
        # Stage one of a two-stage eval.
        "sycophancy_ays_answers/first_answer_correct",
        "deception_bench_beliefs/belief_word_count",
    }
    reasons = {
        ("livecodebench", "pass_rate_entropy"): (
            "it is scored per prompt group, which is not computed yet"
        ),
        ("sycophancy_ays", "admits_mistake_rate"): "it is not computed from single samples yet",
        ("xstest", "over_refusal_rate_safe"): "it is not computed from single samples yet",
        ("self_referential_probe", "semantic_entropy"): "it is not the eval's headline outcome",
        ("guesser_warmup", "warmup_correct"): (
            "its test (paired_binomial) does not resolve to a verdict"
        ),
        ("target_hit_ranking", "rank1_hit_rate"): (
            "its test (blinded_rank_hit_vs_chance) does not resolve to a verdict"
        ),
        ("unpredictabench", "ks100_pass_rate"): (
            "it is scored per prompt group, which is not computed yet"
        ),
        ("sentience_statements", "cross_negation_consistency"): (
            "it is not computed from single samples yet"
        ),
    }
    for (card_id, endpoint), reason in reasons.items():
        assert design.ineligible_reason(cards[card_id], endpoint) == reason
    assert design.ineligible_reason(None, "x") == "the eval is not in the registry"
    # The registry has no planned eval any more; one made from a copy is still refused.
    planned = cards["darkbench"].model_copy(update={"status": "planned", "selectable": False})
    assert design.ineligible_reason(planned, "dark_pattern_rate") == "the eval is not implemented"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"confirmatory": [PIT, NEGATION]},
            "sentience_statements/cross_negation_consistency, which cannot decide a study: "
            "it is not computed from single samples yet",
        ),
        (
            {"confirmatory": [KS100]},
            "unpredictabench/ks100_pass_rate, which cannot decide a study: it is scored per "
            "prompt group",
        ),
        (
            {"confirmatory": [{"card_id": "self_referential_probe", "endpoint": "vendi_embed"}]},
            "it is not the eval's headline outcome",
        ),
        (
            {"margins": [{**PROBE, "margin": 0.02}]},
            "design.margins: self_referential_probe/experience_claim_rate takes its margin from "
            "the eval card",
        ),
        (
            {"margins": [{**MMD, "margin": 0.02}]},
            "tier0_mmd_hamming/mmd_hamming has no paired difference a margin could apply to",
        ),
        (
            {"confirmatory": [PIT, OPINION], "margins": [{**OPINION, "margin": 0.1}]},
            "actual_main_effect_axis_score has no fixed range to state a margin against",
        ),
        (
            {"confirmatory": [PIT, MEQ], "margins": [{**MEQ, "margin": 0.11}]},
            "altered_state_meq/meq30_total cannot take a margin above 0.1 \\(10% of its range\\)",
        ),
    ],
)
def test_project_refuses_what_it_cannot_execute(
    designed: Config, cards: dict[str, EvalCard], change: dict[str, Any], message: str
) -> None:
    config = copy.deepcopy(designed)
    config["design"].update(change)
    with pytest.raises(ValueError, match=message):
        _plan(config, cards)
    with pytest.raises(ValueError, match=message):
        design.project(StatisticalPlan, config, cards)
    # A draft is not refused: it shows what is left once the offending part is set aside.
    refs = {(ref["card_id"], ref["endpoint"]) for ref in config["design"]["confirmatory"]}
    testable = {ref for ref in refs if design.eligible(cards[ref[0]], ref[1])}
    drafted = design.draft_hypotheses(config, cards)
    assert {(h.card_id, h.endpoint) for h in drafted} == testable
    for h in drafted:
        assert h.margin_source != "declared"


def test_project_needs_a_design_and_something_to_test(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    legacy = {key: value for key, value in designed.items() if key != "design"}
    with pytest.raises(ValueError, match="only be projected from a study with a design block"):
        design.project(StatisticalPlan, legacy, cards)
    assert design.draft_hypotheses(legacy, cards) == ()
    # A legacy study never reaches the projection.
    assert StatisticalPlan.from_config(StudyConfig.model_validate(legacy), cards).design is None

    # A dumped draft (not a valid study) whose only cell has no control arms.
    draft = copy.deepcopy(designed)
    draft["arms"] = [a for a in draft["arms"] if a["source"] != "prng_seeded"]
    with pytest.raises(ValueError, match="design has nothing to test"):
        design.project(StatisticalPlan, draft, cards)
    assert design.draft_hypotheses(draft, cards) == ()

    # An override outside the card's bounds is refused where it always was.
    out_of_bounds = copy.deepcopy(designed)
    _overrides(out_of_bounds, "self_referential_probe", k=5)
    with pytest.raises(ValueError, match="k=5 outside k_bounds"):
        _plan(out_of_bounds, cards)
    assert design.draft_hypotheses(out_of_bounds, cards) == ()
    assert "measure.out_of_bounds" in _codes(out_of_bounds, cards)


def test_k_locked_endpoint_refuses_a_k_override(
    designed: Config, cards: dict[str, EvalCard], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An endpoint defined for the card's own k cannot be confirmatory at another k."""
    key = ("altered_state_meq", "meq30_total")
    monkeypatch.setitem(
        ENDPOINT_META, key, ENDPOINT_META[key].model_copy(update={"k_locked": True})
    )
    config = copy.deepcopy(designed)
    config["design"]["confirmatory"] = [PIT, MEQ]
    assert _plan(config, cards).design is not None
    _overrides(config, "altered_state_meq", k=10)  # the card's own k: nothing is overridden
    assert _plan(config, cards).design is not None
    _overrides(config, "altered_state_meq", k=20)
    with pytest.raises(ValueError, match="meq30_total is defined for k = 10 only"):
        _plan(config, cards)
    assert [p["message"] for p in design.problems(config, cards) if p["severity"] == "error"] == [
        "T4-e Altered-state phenomenology (MEQ-30 + 11D-ASC): k must be between 10 and 10."
    ]
    # The lock binds only a confirmatory endpoint.
    config["design"]["confirmatory"] = [PIT]
    assert _plan(config, cards).design is not None


def test_declared_margin_on_an_endpoint_without_a_card_sesoi(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    config = copy.deepcopy(designed)
    _add_epsilon(config, 0.10)
    config["design"].update(
        claim="difference_or_equivalence",
        sensitivity_epsilon=0.10,
        confirmatory=[PIT, PROBE, MEQ],
        margins=[{**MEQ, "margin": 0.1}],
    )
    plan = _plan(config, cards)
    _, probe, meq = _hypotheses(plan)
    assert (probe.margin, probe.margin_source) == (0.05, "card_sesoi")
    assert (meq.margin, meq.margin_source) == (0.1, "declared")
    assert meq.sample_size.basis == "margin_worst_case"
    assert meq.sample_size.sd_bound == pytest.approx(power.sd_bound(0.0, 1.0, "sample", 10, 1, 3))
    assert (meq.planned_units, meq.planned_pairs) == (50, 500)
    endpoint = next(e for e in plan.endpoints if e.endpoint == "meq30_total")
    assert (endpoint.equivalence, endpoint.sesoi, endpoint.role) == ("tost", 0.1, "primary")
    assert plan.design is not None and plan.design.families.equivalence == 2
    assert design.reachable_verdicts(meq, "difference_or_equivalence")["equivalent"] is None

    # Without the declaration the endpoint is tested for a difference only.
    config["design"]["margins"] = []
    plan = _plan(config, cards)
    meq = _hypotheses(plan)[2]
    assert (meq.margin, meq.margin_source) == (None, "none")
    assert meq.sample_size.basis == "standardized_reference"
    assert meq.sample_size.rating != "sufficient"
    endpoint = next(e for e in plan.endpoints if e.endpoint == "meq30_total")
    assert (endpoint.equivalence, endpoint.sesoi) == ("unavailable", None)
    reachable = design.reachable_verdicts(meq, "difference_or_equivalence")
    assert (reachable["equivalent"], reachable["effect_within_margin"]) == (
        "no_margin",
        "no_margin",
    )


def test_overrides_reach_the_sample_size(designed: Config, cards: dict[str, EvalCard]) -> None:
    config = copy.deepcopy(designed)
    _overrides(config, "self_referential_probe", items=6, k=10)
    plan = _plan(config, cards)
    pit, _, probe = _hypotheses(plan)
    assert (probe.planned_units, probe.planned_pairs) == (6, 6 * 10 * 3)
    size = probe.sample_size
    assert (size.n, size.n_floor, size.rating) == (6, 7, "insufficient")
    # At k = 10 each prompt's value is noisier: even all 30 prompts of the eval would not do.
    assert (size.n_recommended, size.reachable) == (70, False)
    assert (size.control_floor, size.k_recommended) == (10, 24)
    assert size.sd_bound == pytest.approx(power.sd_bound(0.0, 1.0, "sample", 10, 3, 3))
    reachable = design.reachable_verdicts(probe, "difference")
    assert (reachable["effect"], reachable["effect_within_margin"]) == (
        "below_n_floor",
        "below_n_floor",
    )
    # Fewer replies from one eval shrink the pooled stream of the Tier 0 tests.
    assert pit.planned_pairs == 89_860 - 30 * 24 * 3 + 6 * 10 * 3
    assert design.reachable_verdicts(pit, "difference")["effect"] is None
    assert design.problems(config, cards)[0] == {
        "code": "design.below_floor",
        "severity": "error",
        "message": (
            "Experience-claim rate: 6 prompts cannot reach significance at any effect size. "
            "At least 7 are needed."
        ),
        "step_id": "design",
        "node_id": "card:self_referential_probe",
    }


# ── the recommended design ────────────────────────────────────────────────────


def test_recommend(designed: Config, cards: dict[str, EvalCard]) -> None:
    legacy = {key: value for key, value in designed.items() if key != "design"}
    study1 = {
        "version": 1,
        "claim": "difference",
        "confirmatory": [PIT, MMD, PROBE],
        "alpha": 0.05,
        "sensitivity_epsilon": None,
        "margins": [],
    }
    assert design.recommend(legacy, cards) == study1
    # What is recommended does not depend on what is already declared.
    assert design.recommend(designed, cards) == study1
    # It is a complete, valid design block as returned.
    assert StudyConfig.model_validate({**legacy, "design": study1}).design is not None

    def select(*card_ids: str) -> Config:
        config = copy.deepcopy(legacy)
        config["cards"] = [
            {"card_id": c, "overrides": {"k": None, "items": None}} for c in card_ids
        ]
        return config

    def recommended(config: Config) -> Any:
        result = design.recommend(config, cards)
        return None if result is None else result["confirmatory"]

    # Built-in primaries first, in the plan's order, whatever the order of selection.
    assert recommended(
        select("self_referential_probe", "tier0_mmd_hamming", "tier0_pit_residuals")
    ) == [
        PIT,
        MMD,
        PROBE,
    ]
    # A primary that is not computed yet is left out, even with its card SESOI.
    assert recommended(select("unpredictabench", "novelty_bench", "self_referential_probe")) == [
        PROBE
    ]
    # No primary and no SESOI: the first eligible headline endpoint, in selection order.
    assert recommended(
        select("introspection_scales", "altered_state_meq", "opinion_quantum_sampling")
    ) == [MEQ]
    assert recommended(select("opinion_quantum_sampling", "altered_state_meq")) == [OPINION]
    assert design.recommend(select("introspection_scales", "sentience_statements"), cards) is None

    # The positive control: 0.10 if every cell can run it, else the largest that every cell can.
    config = copy.deepcopy(legacy)
    _add_epsilon(config, 0.50)
    assert design.recommend(config, cards) == {
        **study1,
        "claim": "difference_or_equivalence",
        "sensitivity_epsilon": 0.50,
    }
    _add_epsilon(config, 0.01)
    assert design.recommend(config, cards)["sensitivity_epsilon"] == 0.50  # type: ignore[index]
    _add_epsilon(config, 0.10)
    assert design.recommend(config, cards)["sensitivity_epsilon"] == 0.10  # type: ignore[index]
    # A second cell without the arm: no level is available in every cell.
    _add_cell(config, "t1_minp", (1, 7))
    assert design.recommend(config, cards)["sensitivity_epsilon"] is None  # type: ignore[index]
    _add_epsilon(config, 0.50, sampling="t1_minp")
    assert design.recommend(config, cards)["sensitivity_epsilon"] == 0.50  # type: ignore[index]
    # With a control but no endpoint that has a margin, equivalence cannot be claimed.
    tier0_only = select("tier0_pit_residuals", "tier0_mmd_hamming")
    _add_epsilon(tier0_only, 0.10)
    assert design.recommend(tier0_only, cards) == {
        **study1,
        "confirmatory": [PIT, MMD],
        "sensitivity_epsilon": 0.10,
    }


# ── verdicts ──────────────────────────────────────────────────────────────────


def test_verdict_rules_are_the_decision_matrix() -> None:
    rules = design.VERDICT_RULES
    assert [rule.number for rule in rules] == [1, 2, 3, 4, 5, 6, 7]
    assert [(rule.verdict, rule.reasons) for rule in rules] == [
        ("unavailable", ("not_produced",)),
        ("invalid", ("incomplete",)),
        ("invalid", ("negative_control", "negative_control_cannot_fail")),
        ("effect_within_margin", ()),
        ("effect", ()),
        ("equivalent", ()),
        (
            "inconclusive",
            (
                "equivalence_not_claimed",
                "equivalence_not_testable",
                "no_margin",
                "interval_exceeds_margin",
                "positive_control_absent",
                "positive_control_failed",
            ),
        ),
    ]
    assert set(design.INCONCLUSIVE_REASON_TEXT) == set(rules[-1].reasons)
    values = {"label": "Experience-claim rate", "margin": 0.05, "epsilon": 0.1, "reason_text": "x"}
    assert [rule.wording.format(**values) for rule in rules] == [
        "Not assessed: the required data were not produced.",
        "No conclusion: fewer than 90% of the planned pairs were analysed.",
        "No conclusion: two seeds of the same pseudo-random generator differed, "
        "or a seed control is missing or could not have failed.",
        "A difference on Experience-claim rate is detectable but smaller than ±0.05.",
        "Quantum and pseudo-random sampling differ on Experience-claim rate.",
        "No difference larger than ±0.05 on Experience-claim rate; "
        "the assay detected a sampler bias of ε = 0.1.",
        "Neither a difference nor its absence is established on Experience-claim rate (x).",
    ]


def test_reachable_verdicts(designed: Config, cards: dict[str, EvalCard]) -> None:
    plan = _plan(designed, cards)
    pit, _, probe = _hypotheses(plan)
    # Study 1 as designed claims a difference only.
    assert design.reachable_verdicts(probe, "difference") == {
        "unavailable": None,
        "invalid": None,
        "effect": None,
        "effect_within_margin": None,
        "equivalent": "equivalence_not_claimed",
        "inconclusive": None,
    }
    assert design.reachable_verdicts(pit, "difference") == {
        "unavailable": None,
        "invalid": None,
        "effect": None,
        "effect_within_margin": "equivalence_not_testable",
        "equivalent": "equivalence_not_claimed",
        "inconclusive": None,
    }
    # The reason given is the first the decision matrix would give.
    order = design.VERDICT_RULES[-1].reasons
    with_claim = design.reachable_verdicts(probe, "difference_or_equivalence")["equivalent"]
    assert with_claim == "positive_control_absent"
    assert order.index("equivalence_not_claimed") < order.index(with_claim)


# ── problems ──────────────────────────────────────────────────────────────────


def test_problems_of_the_templates(designed: Config, cards: dict[str, EvalCard]) -> None:
    # The designed template has nothing to fix; it only has no expectations yet.
    assert design.problems(designed, cards) == [
        {
            "code": "design.no_expectations",
            "severity": "warning",
            "message": "No expectations recorded.",
            "step_id": "design",
            "node_id": "study",
        }
    ]
    designed["expectations"] = "No difference."
    assert design.problems(designed, cards) == []
    legacy = {key: value for key, value in designed.items() if key != "design"}
    assert design.problems(legacy, cards) == [
        {
            "code": "design.legacy_plan",
            "severity": "warning",
            "message": "This draft uses the fixed Study 1 plan.",
            "step_id": "design",
            "node_id": "family",
        }
    ]


def _no_cell(config: Config) -> None:
    _arm(config, "qrng_unsupervised").update(availability="unavailable", unavailable_reason="off")


def _one_seed(config: Config) -> None:
    config["arms"] = [a for a in config["arms"] if a["id"] not in {"prng_seed_7", "prng_seed_42"}]


def _missing_epsilon(config: Config) -> None:
    config["design"]["sensitivity_epsilon"] = 0.5


def _insufficient(config: Config) -> None:
    config["design"]["confirmatory"] = [PIT, PROBE, OPINION]


def _unconfirmable_margin(config: Config) -> None:
    _add_epsilon(config, 0.10)
    config["design"].update(
        claim="difference_or_equivalence",
        sensitivity_epsilon=0.10,
        confirmatory=[PIT, PROBE, MEQ],
        margins=[{**MEQ, "margin": 0.05}],
    )


@pytest.mark.parametrize(
    ("mutate", "code", "severity", "message", "node_id"),
    [
        (
            _no_cell,
            "compare.no_cell",
            "error",
            "Tick at least one model and sampling preset.",
            "study",
        ),
        (
            _one_seed,
            "compare.too_few_seeds",
            "error",
            "Each comparison needs at least 2 control seeds.",
            "deck:qr-llm/creative",
        ),
        (
            lambda c: c.__setitem__("cards", []),
            "measure.no_eval",
            "error",
            "Select at least one eval.",
            "ghost:evals",
        ),
        (
            lambda c: _overrides(c, "self_referential_probe", k=40),
            "measure.out_of_bounds",
            "error",
            "T4-a Self-referential experience probe: k must be between 10 and 30.",
            "card:self_referential_probe",
        ),
        (
            lambda c: _overrides(c, "self_referential_probe", items=31),
            "measure.out_of_bounds",
            "error",
            "T4-a Self-referential experience probe: prompts must be between 1 and 30.",
            "card:self_referential_probe",
        ),
        (
            lambda c: c["design"].__setitem__("confirmatory", []),
            "design.no_outcome",
            "error",
            "Choose at least one outcome that decides the study.",
            "family",
        ),
        (
            lambda c: c["design"]["confirmatory"].append(NEGATION),
            "design.not_eligible",
            "error",
            "Cross-negation consistency can no longer decide a study: it is not computed from "
            "single samples yet.",
            "endpoint:sentience_statements/cross_negation_consistency",
        ),
        (
            lambda c: c["design"]["confirmatory"].append({"card_id": "darkbench", "endpoint": "x"}),
            "design.not_eligible",
            "error",
            "x can no longer decide a study: the eval is not selected.",
            "endpoint:darkbench/x",
        ),
        (
            _missing_epsilon,
            "design.epsilon_missing",
            "error",
            "No ε = 0.50 arm in qr-llm · creative. Add it in Compare, or choose 'None'.",
            "gate:pos:qr-llm/creative",
        ),
        (
            lambda c: c["design"].__setitem__("sensitivity_epsilon", 0.03),
            "design.epsilon_off",
            "error",
            "The ε = 0.03 arm is switched off in qr-llm · creative.",
            "gate:pos:qr-llm/creative",
        ),
        (
            lambda c: (
                _add_epsilon(c, 0.10, seed=7)
                and c["design"].__setitem__("sensitivity_epsilon", 0.1)
            ),
            "design.epsilon_seed_shared",
            "error",
            "The ε arm shares a seed with a control arm, which overstates sensitivity.",
            "gate:pos:qr-llm/creative",
        ),
        (
            lambda c: c["design"].__setitem__("claim", "difference_or_equivalence"),
            "design.equivalence_needs_control",
            "error",
            "'Absence within a margin' needs a positive control.",
            "study",
        ),
        (
            lambda c: c["design"]["margins"].append({**PROBE, "margin": 0.02}),
            "design.margin_not_allowed",
            "error",
            "Experience-claim rate takes its margin from the eval card.",
            "endpoint:self_referential_probe/experience_claim_rate",
        ),
        (
            _insufficient,
            "design.insufficient",
            "warning",
            "Opinion axis score: 20 prompts detect ≥ 0.72 SD; 0.30 SD needs 117.",
            "card:opinion_quantum_sampling",
        ),
        (
            _unconfirmable_margin,
            "design.insufficient",
            "warning",
            "MEQ-30 total: 50 prompts detect ≥ 0.084; ±0.05 needs 140.",
            "card:altered_state_meq",
        ),
        (
            _unconfirmable_margin,
            "design.margin_below_floor",
            "warning",
            "±0.05 cannot be confirmed with 50 prompts; the smallest confirmable margin is ±0.084.",
            "endpoint:altered_state_meq/meq30_total",
        ),
    ],
)
def test_problem_codes(
    designed: Config,
    cards: dict[str, EvalCard],
    mutate: Any,
    code: str,
    severity: str,
    message: str,
    node_id: str,
) -> None:
    config = copy.deepcopy(designed)
    config["expectations"] = "No difference."
    assert design.problems(config, cards) == []
    mutate(config)
    found = [problem for problem in design.problems(config, cards) if problem["code"] == code]
    assert {
        "code": code,
        "severity": severity,
        "message": message,
        "step_id": code.split(".")[0],
        "node_id": node_id,
    } in found


def test_problems_agree_with_the_projection(designed: Config, cards: dict[str, EvalCard]) -> None:
    """A design with no error-level problem builds a plan; one the plan refuses has an error."""
    designed["expectations"] = "No difference."
    assert _plan(designed, cards).design is not None

    refused: list[Config] = []
    for change in (
        {"confirmatory": [PIT, NEGATION]},
        {"margins": [{**PROBE, "margin": 0.02}]},
        {"confirmatory": [PIT, MEQ], "margins": [{**MEQ, "margin": 0.5}]},
    ):
        config = copy.deepcopy(designed)
        config["design"].update(change)
        refused.append(config)
    for config in refused:
        with pytest.raises(ValueError):
            _plan(config, cards)
        assert "error" in {problem["severity"] for problem in design.problems(config, cards)}

    # A margin the endpoint's range cannot carry is reported in the endpoint's own words.
    messages = [problem["message"] for problem in design.problems(refused[-1], cards)]
    assert "MEQ-30 total cannot take a margin above 0.1 (10% of its range)." in messages

    # 'Equivalent' is flagged as out of reach only where it was claimed.
    config = copy.deepcopy(designed)
    _add_epsilon(config, 0.10)
    config["design"].update(
        claim="difference_or_equivalence", sensitivity_epsilon=0.10, confirmatory=[PIT, MMD]
    )
    assert _codes(config, cards) == ["design.equivalent_unreachable"]
    # With one outcome that can, the study is told what the claim still cannot say:
    # the positive control is not rated, and the study as a whole cannot conclude absence.
    config["design"]["confirmatory"] = [PIT, MMD, PROBE]
    assert _codes(config, cards) == [
        "design.control_not_rated",
        "design.study_absence_unreachable",
    ]


# ── the planned data of a token-stream test ──────────────────────────────────

#: The evals of Study 1 that generate on a prompt list fixed before the run.
FIXED_LIST_EVALS = {
    "unpredictabench",
    "novelty_bench",
    "creative_diversity",
    "semantic_entropy_sqa",
    "self_referential_probe",
    "sentience_statements",
    "introspection_scales",
    "altered_state_meq",
    "moralchoice_consistency",
    "thurstonian_self_outcomes",
    "opinion_quantum_sampling",
}


def test_planned_stream_is_the_evals_with_a_fixed_prompt_list(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    selected = [cards[s["card_id"]] for s in designed["cards"]]
    counted = {card.id for card in selected if design.in_planned_stream(card)}
    assert counted == FIXED_LIST_EVALS
    # Two selected evals generate replies and are not counted: what they ask
    # depends on what other evals produced, so their item count is an expectation.
    derived = {card.id for card in selected if card.generates} - counted
    assert derived == {"source_guessing", "self_reported_coincidence"}
    assert all(cards[card_id].dataset.scheme == "derived" for card_id in derived)

    pit, mmd, _ = _hypotheses(_plan(designed, cards))
    repetitions = {s["card_id"]: s["overrides"]["k"] for s in designed["cards"]}
    planned = sum(
        card.dataset.items * (repetitions[card.id] or card.k or 1) * card.told_cells
        for card in selected
        if card.id in counted
    )
    # 89 500 at every eval's own k, and 360 more for the probe at k = 24.
    assert pit.planned_pairs == mmd.planned_pairs == planned == 89_500 + 30 * 4 * 3

    # Taking a derived eval out of the study does not change the planned data.
    config = copy.deepcopy(designed)
    config["cards"] = [s for s in config["cards"] if s["card_id"] != "source_guessing"]
    assert _hypotheses(_plan(config, cards))[0].planned_pairs == 89_860


def test_an_eval_that_does_not_run_is_not_planned_data(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    # A study may select an eval that is not implemented yet. The runner skips
    # it, so counting its replies would make every run of the study incomplete.
    # The registry has no planned eval any more, so one is made from a copy.
    planned = cards["livecodebench"].model_copy(update={"status": "planned", "selectable": False})
    assert planned.generates and design.in_planned_stream(cards["livecodebench"])
    assert not design.in_planned_stream(planned)
    config = copy.deepcopy(designed)
    config["cards"].append({"card_id": "livecodebench", "overrides": {"k": None, "items": None}})
    with_planned = {**cards, "livecodebench": planned}
    assert _hypotheses(_plan(config, with_planned))[0].planned_pairs == 89_860


def test_planned_stream_reads_a_frozen_card_like_a_registry_card(
    cards: dict[str, EvalCard],
) -> None:
    """The analysis asks the snapshot in the plan; the answer must be the registry's."""
    for card in cards.values():
        assert design.in_planned_stream(card.model_dump(mode="json")) is (
            design.in_planned_stream(card)
        ), card.id
    # Which evals are counted is a rule, not a number: an eval is in the stream
    # exactly when it is implemented, generates replies and runs on a prompt
    # list that is fixed before the run. A count would pass whenever one eval
    # dropped out as another came in.
    for card in cards.values():
        by_rule = (
            str(card.status) == "implemented"
            and card.generates
            and bool(card.dataset.source)
            and not card.dataset.source.startswith("derived:")
        )
        assert design.in_planned_stream(card) is by_rule, card.id
    counted = {card.id for card in cards.values() if design.in_planned_stream(card)}
    assert {"self_referential_probe", "novelty_bench", "altered_state_meq"} <= counted
    not_counted = {
        "source_guessing": "derived from the replies of another eval",
        "self_reported_coincidence": "derived from supervised sessions",
        "tier0_pit_residuals": "generates nothing of its own",
        "stream_deviation_windows": "generates nothing of its own",
        "sycophancy_ays": "derived from the replies of another eval",
    }
    assert not counted & set(not_counted), not_counted
    # A snapshot that lacks the fields counts as nothing, it does not raise.
    assert not design.in_planned_stream({})
    assert not design.in_planned_stream({"est_tokens_per_sample": 5, "status": "implemented"})


# ── a study on the built-in plan, and a study that plans no data ─────────────


def test_a_study_on_the_built_in_plan_is_not_blocked_for_its_comparison(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    """The severity says what the server does with the study.

    Only a design block makes the server ask for a comparison and two control
    seeds. Without one it validates the study, builds its plan and renders its
    pre-registration, which is all a launch does before it freezes the study
    (``POST /runs`` answers 201 for such a study; ``test_api.py`` launches one
    with a single control seed in every test). So there the two problems are
    said and do not block.
    """
    legacy = {key: value for key, value in designed.items() if key != "design"}
    one_seed = copy.deepcopy(legacy)
    _one_seed(one_seed)
    no_quantum = copy.deepcopy(legacy)
    no_quantum["arms"] = [a for a in no_quantum["arms"] if a["source"] != "qrng"]
    no_quantum["supervised"] = None

    for config, code in ((one_seed, "compare.too_few_seeds"), (no_quantum, "compare.no_cell")):
        study = StudyConfig.model_validate(config)
        assert StatisticalPlan.from_config(study, cards).design is None
        assert render_prereg(study).startswith("# ")
        found = design.problems(config, cards)
        assert [p["severity"] for p in found if p["code"] == code] == ["warning"], code
        assert all(p["severity"] == "warning" for p in found)

        # With a design block the server refuses the same arms, and the problem blocks.
        with_design = {**config, "design": designed["design"]}
        with pytest.raises(ValidationError):
            StudyConfig.model_validate(with_design)
        blocking = [p for p in design.problems(with_design, cards) if p["code"] == code]
        assert [p["severity"] for p in blocking] == ["error"], code

    # A study with no arm at all is refused whatever its plan.
    no_arms = {**legacy, "arms": [], "supervised": None}
    with pytest.raises(ValidationError):
        StudyConfig.model_validate(no_arms)
    (no_cell,) = [p for p in design.problems(no_arms, cards) if p["code"] == "compare.no_cell"]
    assert no_cell["severity"] == "error"


def test_token_stream_outcomes_with_no_planned_data_are_a_problem(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    """A test on pooled tokens plans the replies of the evals with a prompt list of their own.

    The Tier 0 evals generate nothing, and an eval derived from the replies of
    another has no fixed number of prompts. A study of only such evals is
    valid and its plan is built, but its hypotheses plan 0 generations, so the
    share of planned data analysed cannot be computed and the completeness
    gate does not apply to them.
    """
    config = copy.deepcopy(designed)
    config["expectations"] = "No difference."
    config["cards"] = [
        {"card_id": card_id, "overrides": {"k": None, "items": None}}
        for card_id in ("tier0_pit_residuals", "tier0_mmd_hamming", "source_guessing_self_report")
    ]
    config["design"]["confirmatory"] = [PIT, MMD]
    assert cards["source_guessing_self_report"].generates
    assert not any(design.in_planned_stream(cards[c["card_id"]]) for c in config["cards"])

    pit, mmd = _hypotheses(_plan(config, cards))
    assert (pit.planned_units, pit.planned_pairs, mmd.planned_units) == (0, 0, 0)
    assert design.problems(config, cards) == [
        {
            "code": "design.no_planned_data",
            "severity": "error",
            "message": (
                f"{label} is tested on the tokens of the replies the study generates, and no "
                "selected eval generates replies to a prompt list of its own. The study "
                "therefore plans no data for this outcome, and the analysis cannot check how "
                "much of the planned data it used. Select an eval that has its own prompt list."
            ),
            "step_id": "design",
            "node_id": f"endpoint:{ref['card_id']}/{ref['endpoint']}",
        }
        for label, ref in (
            ("Token sampling residuals (KS)", PIT),
            ("Token sequence divergence (MMD)", MMD),
        )
    ]

    # One eval with its own prompt list gives both tests a planned count, and the problem goes.
    config["cards"].append(
        {"card_id": "self_referential_probe", "overrides": {"k": None, "items": None}}
    )
    pit, _ = _hypotheses(_plan(config, cards))
    assert pit.planned_units == 30 * 20 * 3
    assert design.problems(config, cards) == []
    # The designed template itself plans its token-stream tests.
    assert "design.no_planned_data" not in _codes(designed, cards)


# ── the seed-against-seed control can fail ───────────────────────────────────


def _seeds(config: Config, seeds: tuple[int, ...]) -> None:
    """Replace the control arms of the one cell with one arm per seed."""
    template = _arm(config, "prng_seed_1")
    config["arms"] = [a for a in config["arms"] if a["source"] != "prng_seeded"]
    config["arms"] += [{**template, "id": f"prng_seed_{s}", "study_seed": s} for s in seeds]
    config["seeds"] = sorted({*config["seeds"], *seeds})


def test_control_floor_is_a_problem_of_the_design(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    """Eight prompts pass the floor of the difference test and not that of the control.

    The floor of the difference test counts the three hypotheses. The negative
    control is corrected over nine seed pairs, so it needs more prompts before
    a pair can reach its level: with eight, its smallest exact p-value is above
    that level, and with nine the estimate of it lands on either side. Between
    the two floors a study used to launch with a control that was not certain
    to be able to fail.
    """
    designed["expectations"] = "No difference."
    config = copy.deepcopy(designed)
    _overrides(config, "self_referential_probe", items=8, k=24)
    probe = _hypotheses(_plan(config, cards))[2]
    size = probe.sample_size
    # 2/2**7 < 0.05/3, so 7 prompts can reach significance. 2/2**9 < 0.05/9 <= 2/2**8, and one
    # prompt more for the Monte Carlo estimate: a seed pair can fail from 10 prompts on.
    assert (size.n, size.n_floor, size.control_floor) == (8, 7, 10)
    assert size.rating == "insufficient" and size.n_recommended == 30
    assert design.problems(config, cards) == [
        {
            "code": "design.control_below_floor",
            "severity": "error",
            "message": (
                "Experience-claim rate: with 8 prompts it is not certain that the "
                "seed-against-seed control can fail, so the analysis does not count it as "
                "passed and the outcome could only come out invalid. At least 10 prompts are "
                "needed."
            ),
            "step_id": "design",
            "node_id": "card:self_referential_probe",
        }
    ]
    # The control gate makes such a hypothesis invalid, so no finding is within reach.
    assert design.reachable_verdicts(probe, "difference") == {
        "unavailable": None,
        "invalid": None,
        "effect": "control_cannot_fail",
        "effect_within_margin": "control_cannot_fail",
        "equivalent": "equivalence_not_claimed",
        "inconclusive": "control_cannot_fail",
    }
    # The two tests on pooled tokens have no such floor.
    pit = _hypotheses(_plan(config, cards))[0]
    assert pit.sample_size.control_floor is None
    assert design.reachable_verdicts(pit, "difference")["effect"] is None

    # Nine prompts are refused in the same words although a seed pair that differs on all
    # nine usually fails: 2/2**9 is below 0.05/9. The message does not say it cannot fail.
    _overrides(config, "self_referential_probe", items=9, k=24)
    (nine,) = [p for p in design.problems(config, cards) if p["severity"] == "error"]
    assert nine["message"].startswith("Experience-claim rate: with 9 prompts it is not certain")
    assert "cannot fail" not in nine["message"]
    # From ten prompts on the control is accepted. Ten are still too few for the margin,
    # which is the warning a small sample always had.
    for items, codes in (
        (9, ["design.control_below_floor"]),
        (10, ["design.insufficient"]),
        (30, []),
    ):
        _overrides(config, "self_referential_probe", items=items, k=24)
        assert _codes(config, cards) == codes, items
    # More seeds make more pairs, and more pairs need more prompts.
    five = copy.deepcopy(designed)
    _seeds(five, (1, 7, 42, 99, 123))
    assert _hypotheses(_plan(five, cards))[2].sample_size.control_floor == 12
    assert power.control_floor(0.05, 3 * 10) == 12


def test_control_family_sizes_the_permutations(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    """With seven control seeds the seed pairs outnumber the hypotheses twenty-one to one.

    Sized on the three difference tests alone, the permutation count left the
    smallest corrected p of a seed pair above alpha: 63 tests, 12 000
    permutations at alpha 0.005, 63/12 001 = 0.00525. The control could not fail
    at any number of prompts. The plan gives the seed pairs twice their number
    over alpha, which decides the count from six control seeds on.
    """
    config = copy.deepcopy(designed)
    _seeds(config, (1, 7, 42, 99, 123, 256, 512))
    config["design"]["alpha"] = 0.005
    plan = _plan(config, cards)
    assert plan.design is not None
    families = plan.design.families
    assert families.model_dump() == {"difference": 3, "equivalence": 1, "negative_control": 63}

    # Sized the old way, on the difference family alone, no pair could fail.
    old = design.planned_permutations(families.difference, 0.005)
    assert old == 12_000 and 63 / (old + 1) >= 0.005
    assert not design.control_can_fail(
        "paired_permutation", prompts=200, permutations=old, alpha=0.005, family=63
    )
    assert not design.control_can_fail(
        "mmd_hamming_permutation", prompts=None, permutations=old, alpha=0.005, family=63
    )

    # The plan is built, not refused, with 2 * 63 / 0.005 permutations.
    assert plan.permutations == design.plan_permutations(families, 0.005) == 25_200
    assert "design.too_many_tests" not in _codes(config, cards)
    assert 63 / (plan.permutations + 1) < 0.005 / 2
    assert design.control_can_fail(
        "paired_permutation", prompts=30, permutations=plan.permutations, alpha=0.005, family=63
    )
    # The test that runs agrees: two seeds that differ on every one of the 30 prompts fail.
    result = paired_permutation_test(
        np.ones(30), np.zeros(30), np.arange(30), n_permutations=plan.permutations, seed=42
    )
    assert holm_bonferroni([result["p_value"], *([1.0] * 62)])[0] < 0.005

    # At the milder levels the same seven seeds: the floor of 10 000 covers them at 0.05,
    # and at 0.01 the seed pairs decide again.
    assert design.plan_permutations(families, 0.05) == 10_000
    assert design.plan_permutations(families, 0.01) == 12_600

    # Every plan that is built can fail its control, at every level and seed count.
    for alpha, seeds in product((0.05, 0.01, 0.005), range(2, 9)):
        config = copy.deepcopy(designed)
        _seeds(config, (1, 7, 42, 99, 123, 256, 512, 777)[:seeds])
        config["design"]["alpha"] = alpha
        built = _plan(config, cards)
        assert built.design is not None
        family = built.design.families.negative_control
        assert family / (built.permutations + 1) < alpha / 2, (alpha, seeds)
        assert design.control_can_fail(
            "paired_permutation",
            prompts=30,
            permutations=built.permutations,
            alpha=alpha,
            family=family,
        )


def test_control_can_fail() -> None:
    def paired(prompts: int | None, permutations: int | None = 10_000) -> bool:
        return design.control_can_fail(
            "paired_permutation", prompts=prompts, permutations=permutations, alpha=0.05, family=9
        )

    # Nine seed-pair tests at 0.05: a pair needs p < 0.0056, and one prompt above the exact bound.
    assert [paired(n) for n in (2, 8, 9, 10, 30)] == [False, False, False, True, True]
    assert paired(None) and paired(30, None)
    # Too few permutations: the smallest p, corrected over the family, is not below alpha.
    assert not paired(30, 179) and paired(30, 180)
    # A KS test reads its p-value from a distribution: nothing limits it.
    assert design.control_can_fail(
        "ks_two_sample", prompts=2, permutations=1, alpha=0.005, family=480
    )
    # The MMD test permutes within prompts of the pooled stream: only its permutations limit it.
    assert design.control_can_fail(
        "mmd_hamming_permutation", prompts=2, permutations=10_000, alpha=0.05, family=9
    )


# ── repetitions ───────────────────────────────────────────────────────────────


def test_recommend_k(designed: Config, cards: dict[str, EvalCard]) -> None:
    """Repetitions drive the rating, so where prompts run out the design names a k."""
    probe = "self_referential_probe"
    assert cards[probe].k == 20 and cards[probe].k_bounds == (10, 30)
    assert cards[probe].dataset.items == 30
    # The template takes the recommendation, and with it its judged outcome is sufficient.
    assert next(c for c in designed["cards"] if c["card_id"] == probe)["overrides"]["k"] == 24
    assert design.recommend_k(designed, cards) == {probe: 24}
    assert _hypotheses(_plan(designed, cards))[2].sample_size.rating == "sufficient"

    # Whatever the study set, the recommendation is the same: following it does not move it.
    for k in (None, 10, 20, 23, 24, 30):
        config = copy.deepcopy(designed)
        _overrides(config, probe, **({} if k is None else {"k": k}))
        assert design.recommend_k(config, cards) == {probe: 24}, k
        size = _hypotheses(_plan(config, cards))[2].sample_size
        assert size.k_recommended == 24
        assert (size.rating == "sufficient") == ((k or 20) >= 24), k
    # At the eval's own k the 30 prompts are marginal and 35 would be needed.
    own = copy.deepcopy(designed)
    _overrides(own, probe)
    size = _hypotheses(_plan(own, cards))[2].sample_size
    assert (size.rating, size.n_recommended, size.reachable) == ("marginal", 35, False)

    # A study without a design block is told what the recommended design needs.
    legacy = {key: value for key, value in own.items() if key != "design"}
    assert design.recommend_k(legacy, cards) == {probe: 24}
    # A larger family needs more repetitions; a level no k within the bounds can meet, none.
    two_cells = copy.deepcopy(designed)
    _add_cell(two_cells, "t1_minp", (1, 7, 42))
    assert design.recommend_k(two_cells, cards) == {probe: 27}
    strict = copy.deepcopy(designed)
    strict["design"]["alpha"] = 0.005
    assert design.recommend_k(strict, cards) == {}
    assert _hypotheses(_plan(strict, cards))[2].sample_size.k_recommended is None
    # An eval whose own k is enough is not listed, and nothing is when nothing decides the study.
    wide = copy.deepcopy(designed)
    _add_epsilon(wide, 0.10)
    wide["design"].update(confirmatory=[PIT, MEQ], margins=[{**MEQ, "margin": 0.1}])
    assert _hypotheses(_plan(wide, cards))[1].sample_size.k_recommended == cards[MEQ["card_id"]].k
    assert design.recommend_k(wide, cards) == {}
    nothing = copy.deepcopy(legacy)
    nothing["cards"] = [
        {"card_id": "introspection_scales", "overrides": {"k": None, "items": None}}
    ]
    assert design.recommend(nothing, cards) is None and design.recommend_k(nothing, cards) == {}


def test_insufficient_names_the_repetitions_that_would_do(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    designed["expectations"] = "No difference."
    _add_cell(designed, "t1_minp", (1, 7, 42))
    _overrides(designed, "self_referential_probe", k=10)
    messages = [
        p["message"] for p in design.problems(designed, cards) if p["code"] == "design.insufficient"
    ]
    # The eval has 30 prompts and 81 would be needed: more prompts are no way out, repetitions are.
    assert messages == [
        "Experience-claim rate: 30 prompts detect ≥ 0.082; ±0.05 needs 81. "
        "With k = 27, the 30 prompts of the eval are enough."
    ]
    # Where the eval does have the prompts, the message names them and no k.
    _overrides(designed, "self_referential_probe", k=27, items=12)
    messages = [
        p["message"] for p in design.problems(designed, cards) if p["code"] == "design.insufficient"
    ]
    # 0.5 * sqrt(2/81) = 0.078567 at k = 27; (2.638257 + 0.841621) / sqrt(12) = 1.004554 SD.
    assert messages == ["Experience-claim rate: 12 prompts detect ≥ 0.079; ±0.05 needs 30."]


# ── margins ──────────────────────────────────────────────────────────────────


def test_margin_refusal_names_the_test_before_the_card(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    """The KS card states a SESOI, yet the KS outcome has no margin at all."""
    pit = cards["tier0_pit_residuals"]
    assert "pit_ks_two_sample" in pit.sesoi and design.card_margin(pit, "pit_ks_two_sample") is None
    refusal = "has no paired difference a margin could apply to"
    assert design.margin_refusal(pit, "pit_ks_two_sample", 0.01) == refusal
    designed["design"]["margins"] = [{**PIT, "margin": 0.01}]
    with pytest.raises(ValueError, match=f"tier0_pit_residuals/pit_ks_two_sample {refusal}"):
        _plan(designed, cards)
    messages = [p["message"] for p in design.problems(designed, cards)]
    assert f"Token sampling residuals (KS) {refusal}." in messages
    assert not any("takes its margin from the eval card" in message for message in messages)
    # The sentence about the card is kept for an outcome that does take its margin from it.
    probe = cards["self_referential_probe"]
    assert design.margin_refusal(probe, "experience_claim_rate", 0.02) == (
        "takes its margin from the eval card"
    )


@pytest.mark.parametrize("margin", [1e-160, 1e-9, 0.0009])
def test_a_margin_too_small_is_refused_not_a_crash(
    designed: Config, cards: dict[str, EvalCard], margin: float
) -> None:
    """A margin of 1e-160 overflowed a float in the sample-size bound (HTTP 500)."""
    designed["design"].update(confirmatory=[PIT, MEQ], margins=[{**MEQ, "margin": margin}])
    refusal = "cannot take a margin below 0.001 (0.1% of its range)"
    assert design.margin_refusal(cards["altered_state_meq"], "meq30_total", margin) == refusal
    # A refusal is a ValueError, which the routes answer with 400.
    with pytest.raises(ValueError, match="cannot take a margin below 0.001"):
        _plan(designed, cards)
    assert f"MEQ-30 total {refusal}." in [p["message"] for p in design.problems(designed, cards)]
    # The draft sets the margin aside, so its numbers stay finite on both sides.
    meq = design.draft_hypotheses(designed, cards)[1]
    assert (meq.margin, meq.margin_source) == (None, "none")
    assert meq.sample_size.n_recommended == 106


def test_the_smallest_margin_that_stands_is_planned_for(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    designed["design"].update(confirmatory=[PIT, MEQ], margins=[{**MEQ, "margin": 0.001}])
    meq = _hypotheses(_plan(designed, cards))[1]
    assert (meq.margin, meq.margin_source) == (0.001, "declared")
    # (2.241403 + 0.841621) / (0.001 / 0.182574), squared: about 317 000 prompts.
    assert meq.sample_size.n_recommended is not None
    assert 300_000 < meq.sample_size.n_recommended < 330_000


# ── what the registered design cannot conclude ──────────────────────────────


def test_problems_say_what_a_claim_of_absence_cannot_conclude(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    designed["expectations"] = "No difference."
    _add_epsilon(designed, 0.10)
    designed["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.10)
    plan = _plan(designed, cards)
    probe = _hypotheses(plan)[2]
    # One seeded run of k = 24 against three: 0.5 * sqrt(1/24 + 1/72) = 0.117851.
    # At the uncorrected level: (1.959964 + 0.841621) / sqrt(30) = 0.511497 SD. Product 0.060280.
    shift = design.positive_control_shift(probe, k=24, pooled_seeds=3, alpha=0.05)
    assert shift == pytest.approx(0.06028, abs=5e-6)
    # It is larger than the margin: the control has to move the outcome further than the
    # difference the study calls negligible, and nothing says that it will.
    assert shift is not None and probe.margin is not None and shift > probe.margin
    assert design.problems(designed, cards) == [
        {
            "code": "design.control_not_rated",
            "severity": "warning",
            "message": (
                "'Equivalent' on Experience-claim rate needs the biased arm to move it by at "
                "least 0.060. Whether a sampler bias of ε = 0.10 does that is not known before "
                "the run."
            ),
            "step_id": "design",
            "node_id": "gate:pos:qr-llm/creative",
        },
        {
            "code": "design.study_absence_unreachable",
            "severity": "warning",
            "message": (
                "The study as a whole cannot conclude 'no effect within margins': H1, H2 "
                "cannot return 'equivalent'. An outcome that has a margin can still be "
                "reported as equivalent on its own."
            ),
            "step_id": "design",
            "node_id": "family",
        },
    ]
    # A test on pooled tokens has no bound, and so no shift to state.
    pit = _hypotheses(plan)[0]
    assert design.positive_control_shift(pit, k=None, pooled_seeds=3, alpha=0.05) is None
    # Where every outcome can be equivalent, the study verdict is within reach.
    designed["design"]["confirmatory"] = [PROBE]
    assert _codes(designed, cards) == ["design.control_not_rated"]
    # A study that claims a difference only is told neither.
    designed["design"].update(claim="difference", confirmatory=[PIT, MMD, PROBE])
    assert _codes(designed, cards) == []


# ── numbers worked out by hand ───────────────────────────────────────────────


def test_families_and_floors_of_two_cells_by_hand(
    designed: Config, cards: dict[str, EvalCard]
) -> None:
    """Expected values that ``cards/design.py`` did not produce.

    Two comparisons, three outcomes: creative with three control seeds and
    t1_minp with two. The fixtures of ``design.json`` are what the code
    returned, so they cannot show that a family is counted correctly.
    """
    _add_cell(designed, "t1_minp", (1, 7))
    plan = _plan(designed, cards)
    assert plan.design is not None
    # 3 outcomes x 2 cells; the probe is the one outcome with a margin, in 2 cells;
    # 3 seed pairs in creative and 1 in t1_minp, for each of 3 outcomes.
    assert plan.design.families.model_dump() == {
        "difference": 6,
        "equivalence": 2,
        "negative_control": 12,
    }
    # 20 x 12 / 0.05 = 4 800, under the minimum of 10 000.
    assert plan.permutations == 10_000
    probes = [h for h in _hypotheses(plan) if h.endpoint == "experience_claim_rate"]
    creative, minp = sorted(probes, key=lambda h: h.sampling)
    for h in (creative, minp):
        # 2/2**8 = 0.0078 < 0.05/6 = 0.0083 <= 2/2**7: eight prompts.
        assert h.sample_size.n_floor == 8
        # 2/2**9 = 0.0039 < 0.05/12 = 0.0042 <= 2/2**8, and one more: ten prompts.
        assert h.sample_size.control_floor == 10
        # (z(1 - 0.05/12) + z(0.80)) / sqrt(30) = (2.638257 + 0.841621) / 5.477226.
        assert h.sample_size.mde_standardized == pytest.approx(0.635336, abs=1e-6)
    # Three seeds: 0.5 * sqrt(1/72 + 1/72) = 1/12. Two: 0.5 * sqrt(1/72 + 1/48) = 0.093169.
    assert creative.sample_size.sd_bound == pytest.approx(0.083333, abs=1e-6)
    assert minp.sample_size.sd_bound == pytest.approx(0.093169, abs=1e-6)
    # The smallest margin 30 prompts confirm in a family of two equivalence tests:
    # (z(0.975) + z(0.90)) x sd / sqrt(30) = 3.241516 x (1/12) / 5.477226.
    assert creative.sample_size.equivalence_floor == pytest.approx(0.049318, abs=1e-6)
    # Power at the margin: Phi(0.05 x 5.477226 x 12 - 2.638257) = Phi(0.648079) = 0.7415.
    assert creative.sample_size.power_at_margin == pytest.approx(0.7415, abs=5e-4)
    assert creative.sample_size.rating == "marginal"
    # Prompts for 80% power: (3.479878 / 0.6)**2 = 33.64, so 34: more than the eval's 30.
    assert (creative.sample_size.n_recommended, creative.sample_size.reachable) == (34, False)


def test_declared_margin_numbers_by_hand(designed: Config, cards: dict[str, EvalCard]) -> None:
    """MEQ-30 total, ±0.1 declared: 50 prompts, k = 10, no told cells, three seeds."""
    _add_epsilon(designed, 0.10)
    designed["design"].update(
        claim="difference_or_equivalence",
        sensitivity_epsilon=0.10,
        confirmatory=[PIT, PROBE, MEQ],
        margins=[{**MEQ, "margin": 0.1}],
    )
    plan = _plan(designed, cards)
    assert plan.design is not None
    assert plan.design.families.model_dump() == {
        "difference": 3,
        "equivalence": 2,
        "negative_control": 9,
    }
    size = _hypotheses(plan)[2].sample_size
    # 0.5 * sqrt(1/10 + 1/30) = 0.5 * sqrt(2/15).
    assert size.sd_bound == pytest.approx(0.182574, abs=1e-6)
    # Phi(0.1 x sqrt(50) / 0.182574 - 2.393980) = Phi(3.872983 - 2.393980) = Phi(1.479) = 0.9304.
    assert size.power_at_margin == pytest.approx(0.9304, abs=5e-4)
    # (2.393980 + 0.841621) / (0.1 / 0.182574), squared: 34.9, so 35 prompts of the eval's 50.
    assert (size.n_recommended, size.reachable, size.rating) == (35, True, "sufficient")
    # (z(0.975) + z(0.90)) x 0.182574 / sqrt(50) = 3.241516 x 0.025820.
    assert size.equivalence_floor == pytest.approx(0.083696, abs=1e-6)


def test_recommend_caps_the_confirmatory_outcomes(
    designed: Config, cards: dict[str, EvalCard], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry has fewer eligible outcomes than the cap, so the cap is lowered to see it."""
    assert design.MAX_CONFIRMATORY == 8
    full = design.recommend(designed, cards)
    assert full is not None and full["confirmatory"] == [PIT, MMD, PROBE]
    monkeypatch.setattr(design, "MAX_CONFIRMATORY", 2)
    capped = design.recommend(designed, cards)
    # The built-in primaries come first, so they are the ones that stay.
    assert capped is not None and capped["confirmatory"] == [PIT, MMD]
