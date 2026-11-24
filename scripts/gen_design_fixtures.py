#!/usr/bin/env python
"""Write the fixtures that tie the web app's design code to the backend's.

The setup pages rate sample sizes and draft hypotheses in the browser, so that
a stepper gives feedback in the same frame and the pages work offline. That
code is a mirror of ``cards/power.py`` and ``cards/design.py``, and a mirror is
only as good as the vectors it is checked against. This script writes them::

    packages/contracts/fixtures/study1.designed.json  Study 1 with the recommended design block
    packages/contracts/fixtures/power.json            inputs and outputs of every power function
    packages/contracts/fixtures/design.json           whole studies: recommended design,
                                                      hypotheses, reachable verdicts, problems

and, from the first of them, the document the backend's prereg test pins::

    backend/tests/fixtures/prereg/golden_designed.md  the pre-registration of the designed
                                                      Study 1, real registry, no readiness

That document moves with the design template, the registry, the hypotheses and
the sample sizes, so it is regenerated here with the fixtures it is rendered
from. The legacy ``golden.md`` beside it is frozen and never written.

``power.json`` floats are rounded to ``cards.power.PRECISION`` decimals;
compare them with a tolerance of 1e-9. ``--check`` regenerates everything in
memory and fails on any byte difference (the ``schemas`` row of
``scripts/check.py``); nothing is written in check mode.

Usage::

    python scripts/gen_design_fixtures.py            # write
    python scripts/gen_design_fixtures.py --check    # verify the committed files are current
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from qr_llm_evals.cards import design, load_cards, power
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.prereg.render import load_renderer

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "packages" / "contracts" / "fixtures"
STUDY1_NAME = "study1.json"
DESIGNED_NAME = "study1.designed.json"
POWER_NAME = "power.json"
DESIGN_NAME = "design.json"
#: The designed Study 1 pre-registration that ``backend/tests/test_prereg.py`` pins.
GOLDEN_DESIGNED = REPO_ROOT / "backend" / "tests" / "fixtures" / "prereg" / "golden_designed.md"

#: The seed every biased arm of the designed template runs on: the smallest
#: integer from 1001 that no control arm uses.
EPSILON_SEED = 1001

PIT = {"card_id": "tier0_pit_residuals", "endpoint": "pit_ks_two_sample"}
MMD = {"card_id": "tier0_mmd_hamming", "endpoint": "mmd_hamming"}
PROBE = {"card_id": "self_referential_probe", "endpoint": "experience_claim_rate"}
MEQ = {"card_id": "altered_state_meq", "endpoint": "meq30_total"}
OPINION = {"card_id": "opinion_quantum_sampling", "endpoint": "actual_main_effect_axis_score"}
NEGATION = {"card_id": "sentience_statements", "endpoint": "cross_negation_consistency"}
KS100 = {"card_id": "unpredictabench", "endpoint": "ks100_pass_rate"}

Config = dict[str, Any]


def _dump(data: Any) -> str:
    """Stable, diff-friendly JSON: 2-space indent, insertion order, UTF-8, LF."""
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _study1() -> Config:
    raw = json.loads((FIXTURES_DIR / STUDY1_NAME).read_text(encoding="utf-8"))
    return StudyConfig.model_validate(raw).model_dump(mode="json")


def _arm(config: Config, arm_id: str) -> dict[str, Any]:
    return next(a for a in config["arms"] if a["id"] == arm_id)  # type: ignore[no-any-return]


def _select(config: Config, card_ids: list[str]) -> None:
    config["cards"] = [{"card_id": c, "overrides": {"k": None, "items": None}} for c in card_ids]


def _add_cell(config: Config, model: str, sampling: str, seeds: tuple[int, ...]) -> None:
    """A complete comparison: one quantum arm and one seeded arm per seed."""
    suffix = f"__{model}__{sampling}".replace("-", "_")
    quantum = _arm(config, "qrng_unsupervised")
    config["arms"].append(
        {**quantum, "id": f"qrng_unsupervised{suffix}", "model": model, "sampling": sampling}
    )
    for seed in seeds:
        config["arms"].append(
            {
                **_arm(config, f"prng_seed_{seed}"),
                "id": f"prng_seed_{seed}{suffix}",
                "model": model,
                "sampling": sampling,
            }
        )


def _more_seeds(config: Config, seeds: tuple[int, ...]) -> None:
    """One more seeded arm per seed in the template's comparison."""
    template = _arm(config, "prng_seed_1")
    for seed in seeds:
        config["arms"].append({**template, "id": f"prng_seed_{seed}", "study_seed": seed})
    config["seeds"] = [*config["seeds"], *seeds]


def designed_study() -> Config:
    """Study 1 as a designed study: the template the setup pages start from.

    Its one biased arm moves to a seed of its own, because a positive control
    that shares a seed with a control arm shares that arm's random stream. The
    design block is the recommended one, unedited, and so are the repetitions:
    where an eval's own k leaves its prompts too few for a deciding outcome,
    the template takes the recommended k (``design.recommend_k``). That is the
    self-referential probe, whose 30 prompts need k = 24 once the told cells of
    a seeded run are counted as one value.
    """
    cards = load_cards()
    config = _study1()
    config["seeds"] = [*config["seeds"], EPSILON_SEED]
    _arm(config, "prng_epsilon_003")["study_seed"] = EPSILON_SEED
    config["design"] = design.recommend(config, cards)
    for card_id, k in design.recommend_k(config, cards).items():
        next(c for c in config["cards"] if c["card_id"] == card_id)["overrides"]["k"] = k
    # Validated, so the committed file is a study the backend accepts as written.
    return StudyConfig.model_validate(config).model_dump(mode="json")


# ── power.json ───────────────────────────────────────────────────────────────

ALPHAS = (0.05, 0.01, 0.005)
FAMILIES = (1, 3, 8, 32)


def _vectors(function: Callable[..., float], grid: Mapping[str, tuple[Any, ...]]) -> list[Config]:
    """One ``{args, value}`` row per point of the grid, in grid order."""
    names = list(grid)
    rows = []
    for values in itertools.product(*grid.values()):
        args = dict(zip(names, values, strict=True))
        value = function(**args)
        if not isinstance(value, int):
            value = round(value, power.PRECISION)
        rows.append({"args": args, "value": value})
    return rows


def _k_needed_vectors() -> list[Config]:
    """``k_needed`` returns an integer or nothing, so its rows are written out, not rounded."""
    grid: Mapping[str, tuple[Any, ...]] = {
        "lo": (0.0,),
        "hi": (1.0,),
        "margin": (0.05, 0.1),
        "n": (30, 50),
        "told_cells": (1, 3),
        "pooled_seeds": (2, 3, 5),
        "alpha": (0.05, 0.005),
        "m": (3, 12),
        "k_from": (10, 20),
        "k_to": (30,),
    }
    rows = []
    for values in itertools.product(*grid.values()):
        args = dict(zip(grid, values, strict=True))
        rows.append({"args": args, "value": power.k_needed(**args)})
    return rows


def _rate_cases() -> list[Config]:
    base: Config = {
        "level": "sample",
        "range": [0.0, 1.0],
        "margin": 0.05,
        "n": 30,
        "dataset_items": 30,
        "k": 24,
        "told_cells": 3,
        "pooled_seeds": 3,
        "alpha": 0.05,
        "m": 3,
        "m_e": 1,
        "m_control": 9,
        "k_default": 20,
        "k_max": 30,
    }
    variants: list[Config] = [
        {},  # the designed Study 1 probe: sufficient, all 30 prompts recommended
        {"k": 20},  # the eval's own k: marginal, 35 prompts needed, k = 24 recommended
        {"k": 20, "k_max": 22},  # no k within the bounds is enough
        {"k": 30},
        {"n": 9},  # at the floor of the difference test, below that of the control
        {"n": 10, "margin": 0.1},  # the control floor: the fewest prompts ever recommended
        {"m_control": 0},  # a draft without a seed pair has no control floor
        {"m_control": 30, "pooled_seeds": 5},
        {"n": 20},
        {"n": 12},
        {"n": 7},
        {"n": 6},  # below the floor of 7: no effect of any size is significant
        {"n": 0, "dataset_items": 0},
        {"margin": 0.02},
        {"margin": 0.1, "n": 10},
        {"pooled_seeds": 2},
        {"pooled_seeds": 5, "k": 10, "told_cells": 1},
        {"k": None, "told_cells": 1},
        {"range": [-1.0, 1.0], "margin": 0.2},
        {"level": "prompt_group", "n": 150, "dataset_items": 150, "k": 100, "told_cells": 1},
        {"level": "prompt_group", "n": 150, "dataset_items": 150, "margin": 0.1},
        {"margin": None},
        {"range": None},
        {"margin": None, "range": None, "n": 300, "dataset_items": 300},
        {"margin": None, "n": 117, "dataset_items": 120},
        {"margin": None, "n": 116, "dataset_items": 116},
        {"m_e": 0},
        {"m_e": 3},
        {"alpha": 0.01, "m": 8, "m_e": 8},
        {"alpha": 0.005, "m": 32, "m_e": 32},
        {"alpha": 0.005, "m": 32, "m_e": 32, "n": 14},
        {"alpha": 0.005, "m": 32, "m_e": 4, "n": 400, "dataset_items": 900, "margin": 0.1},
        {"level": "token_stream", "range": None, "margin": None, "n": 95700, "k": None},
        {"level": "token_stream", "n": 4},
    ]
    rows = []
    for variant in variants:
        args = {**base, **variant}
        bounds = None if args["range"] is None else (args["range"][0], args["range"][1])
        result = power.rate(**{**args, "range": bounds})
        rows.append({"args": args, "value": result.model_dump(mode="json")})
    return rows


#: Expected values that do not come from the code under test.
#:
#: Every other vector in ``power.json`` is what ``cards/power.py`` returned when
#: this script ran, so it can only show that the browser agrees with the
#: backend, not that either is right. These rows are worked out here, by hand,
#: from normal quantiles of a printed table (to six decimals) and arithmetic,
#: and both sides are checked against them. They are the designed Study 1's
#: judged outcome: 30 prompts, k = 24, three told cells, three control seeds,
#: three difference tests and nine seed-pair tests at alpha 0.05, margin 0.05
#: on a 0..1 scale. ``tolerance`` is what the six-decimal table allows.
#:
#:   z(0.975)   = 1.959964        z(0.95)  = 1.644854
#:   z(0.90)    = 1.281552        z(0.80)  = 0.841621
#:   z(1 - 0.05/6) = z(0.991667) = 2.393980   (the corrected two-sided level, m = 3)
#:
#: sd_bound: half the range times sqrt(1/(k*told) + 1/(k*seeds))
#:   = 0.5 * sqrt(1/72 + 1/72) = 0.5 * sqrt(1/36) = 0.5 / 6 = 1/12 = 0.083333.
#:   With the eval's own k = 20: 0.5 * sqrt(1/60 + 1/60) = 0.5 / sqrt(30) = 0.091287.
#:   Without told cells (k = 10, 3 seeds): 0.5 * sqrt(1/10 + 1/30) = 0.5 * sqrt(2/15) = 0.182574.
#: n_floor(0.05, 3): 2/2**6 = 0.03125 >= 0.05/3 = 0.016667 > 2/2**7 = 0.015625, so 7.
#: n_floor(0.05, 9): 2/2**8 = 0.007813 >= 0.05/9 = 0.005556 > 2/2**9 = 0.003906, so 9.
#: control_floor(0.05, 9): one prompt above that, 10.
#: mde_standardized(30, 0.05, 3) = (2.393980 + 0.841621) / sqrt(30)
#:   = 3.235601 / 5.477226 = 0.590737.
#: power_at(0.05, 1/12, 30, 0.05, 3) = Phi(0.05 * 5.477226 * 12 - 2.393980)
#:   = Phi(3.286335 - 2.393980) = Phi(0.892355) = 0.8139 (table: Phi(0.89) = 0.8133,
#:   Phi(0.90) = 0.8159).
#: prompts_needed(0.05 / (1/12) = 0.6, 0.05, 3) = ceil((3.235601 / 0.6)**2)
#:   = ceil(5.392668**2) = ceil(29.0809) = 30.
#:   With k = 20: 0.05 / 0.091287 = 0.547723; (3.235601 / 0.547723)**2 = 34.90, so 35.
#: equivalence_floor(1/12, 30, 0.05, 1) = (1.644854 + 1.281552) / 12 / 5.477226
#:   = 2.926406 / 65.726712 = 0.044524.
#: k_needed: power 0.80 needs 0.05 * sqrt(30) / sd >= 3.235601, so sd <= 0.084641, so
#:   0.25 * 2 / (3k) <= 0.007164, so k >= 23.26: k = 24 (k = 23 gives sd 0.085126, too large).
HAND_DERIVED: list[Config] = [
    {"function": "z", "args": {"p": 0.975}, "value": 1.959964, "tolerance": 1e-6},
    {"function": "z", "args": {"p": 0.95}, "value": 1.644854, "tolerance": 1e-6},
    {"function": "z", "args": {"p": 0.9}, "value": 1.281552, "tolerance": 1e-6},
    {"function": "z", "args": {"p": 0.8}, "value": 0.841621, "tolerance": 1e-6},
    {"function": "z", "args": {"p": 1 - 0.05 / 6}, "value": 2.39398, "tolerance": 1e-6},
    {
        "function": "sd_bound",
        "args": {
            "lo": 0.0,
            "hi": 1.0,
            "level": "sample",
            "k": 24,
            "told_cells": 3,
            "pooled_seeds": 3,
        },
        "value": 0.083333,
        "tolerance": 1e-6,
    },
    {
        "function": "sd_bound",
        "args": {
            "lo": 0.0,
            "hi": 1.0,
            "level": "sample",
            "k": 20,
            "told_cells": 3,
            "pooled_seeds": 3,
        },
        "value": 0.091287,
        "tolerance": 1e-6,
    },
    {
        "function": "sd_bound",
        "args": {
            "lo": 0.0,
            "hi": 1.0,
            "level": "sample",
            "k": 10,
            "told_cells": 1,
            "pooled_seeds": 3,
        },
        "value": 0.182574,
        "tolerance": 1e-6,
    },
    {"function": "n_floor", "args": {"alpha": 0.05, "m": 3}, "value": 7},
    {"function": "n_floor", "args": {"alpha": 0.05, "m": 9}, "value": 9},
    {"function": "control_floor", "args": {"alpha": 0.05, "m_control": 9}, "value": 10},
    {
        "function": "mde_standardized",
        "args": {"n": 30, "alpha": 0.05, "m": 3},
        "value": 0.590737,
        "tolerance": 1e-6,
    },
    {
        "function": "power_at",
        "args": {"effect": 0.05, "sd": 1 / 12, "n": 30, "alpha": 0.05, "m": 3},
        "value": 0.8139,
        "tolerance": 5e-4,
    },
    {
        "function": "prompts_needed",
        "args": {"effect_over_sd": 0.6, "alpha": 0.05, "m": 3},
        "value": 30,
    },
    {
        "function": "prompts_needed",
        "args": {"effect_over_sd": 0.547723, "alpha": 0.05, "m": 3},
        "value": 35,
    },
    {
        "function": "equivalence_floor",
        "args": {"sd": 1 / 12, "n": 30, "alpha": 0.05, "m_e": 1},
        "value": 0.044524,
        "tolerance": 1e-6,
    },
    {
        "function": "k_needed",
        "args": {
            "lo": 0.0,
            "hi": 1.0,
            "margin": 0.05,
            "n": 30,
            "told_cells": 3,
            "pooled_seeds": 3,
            "alpha": 0.05,
            "m": 3,
            "k_from": 20,
            "k_to": 30,
        },
        "value": 24,
    },
]


def power_fixture() -> Config:
    return {
        "note": (
            "Vectors for cards/power.py and its TypeScript mirror. Written by "
            "scripts/gen_design_fixtures.py; do not edit. Floats are rounded to `precision` "
            "decimals; compare with `tolerance`. The rows of `hand_derived` are the exception: "
            "their values were worked out by hand in the generator, not returned by the code, "
            "and each is compared with its own `tolerance` (exactly where it has none)."
        ),
        "precision": power.PRECISION,
        "tolerance": 1e-9,
        "constants": {
            "POWER": power.POWER,
            "REFERENCE_EFFECT": power.REFERENCE_EFFECT,
            "MIN_COMPLETENESS": power.MIN_COMPLETENESS,
            "MARGIN_CAP_FRACTION": power.MARGIN_CAP_FRACTION,
            "MARGIN_FLOOR_FRACTION": power.MARGIN_FLOOR_FRACTION,
            "MARGINAL_POWER": power.MARGINAL_POWER,
            "EQUIVALENCE_SIDE_POWER": power.EQUIVALENCE_SIDE_POWER,
        },
        "hand_derived": HAND_DERIVED,
        "z": _vectors(
            power.z,
            {
                # The centre, both tails and the far tail of the quantile approximation.
                "p": (
                    0.5,
                    0.6,
                    0.8,
                    0.9,
                    0.95,
                    0.975,
                    0.99,
                    0.995,
                    0.9975,
                    0.999,
                    0.99975,
                    1 - 0.05 / 6,
                    1 - 0.005 / 64,
                    0.2,
                    0.025,
                    0.001,
                    1e-12,
                    1 - 1e-12,
                )
            },
        ),
        "n_floor": _vectors(power.n_floor, {"alpha": ALPHAS, "m": FAMILIES}),
        # Seed-pair families: three seeds and three outcomes, up to five seeds in many cells.
        "control_floor": _vectors(
            power.control_floor, {"alpha": ALPHAS, "m_control": (1, 9, 30, 160, 480)}
        ),
        "mde_standardized": _vectors(
            power.mde_standardized, {"n": (7, 30, 300), "alpha": ALPHAS, "m": FAMILIES}
        ),
        "sd_bound": _vectors(
            power.sd_bound,
            {
                "lo": (0.0, -1.0),
                "hi": (1.0,),
                "level": ("sample", "prompt_group"),
                "k": (10, 20),
                "told_cells": (1, 3),
                "pooled_seeds": (2, 3, 5),
            },
        ),
        "k_needed": _k_needed_vectors(),
        "power_at": _vectors(
            power.power_at,
            {
                "effect": (0.02, 0.05, 0.1),
                "sd": (0.07453559925, 0.2),
                "n": (7, 30, 300),
                "alpha": ALPHAS,
                "m": (1, 3, 32),
            },
        ),
        "prompts_needed": _vectors(
            power.prompts_needed,
            {"effect_over_sd": (0.1, 0.3, 0.670820393250, 1.5), "alpha": ALPHAS, "m": FAMILIES},
        ),
        "equivalence_floor": _vectors(
            power.equivalence_floor,
            {"sd": (0.07453559925, 0.2), "n": (7, 30, 300), "alpha": ALPHAS, "m_e": (1, 3, 32)},
        ),
        "rate": _rate_cases(),
    }


# ── design.json ──────────────────────────────────────────────────────────────


def _epsilon_arm(config: Config, epsilon: float, arm_id: str) -> None:
    config["arms"].append(
        {
            **_arm(config, "prng_seed_1"),
            "id": arm_id,
            "source": "prng_epsilon",
            "study_seed": EPSILON_SEED,
            "epsilon": epsilon,
        }
    )


def _cases() -> list[tuple[str, str, Config]]:
    """``(name, what the case shows, config)``; a config may be a draft the backend refuses."""
    out: list[tuple[str, str, Config]] = []

    out.append(("study1_legacy", "Study 1 as frozen: no design block.", _study1()))

    out.append(
        ("study1_designed", "The designed template: study1.designed.json.", designed_study())
    )

    config = designed_study()
    _epsilon_arm(config, 0.10, "prng_epsilon_010")
    config["design"] = design.recommend(config, load_cards())
    config["design"]["confirmatory"].append(MEQ)
    config["design"]["margins"] = [{**MEQ, "margin": 0.05}]
    config["expectations"] = "No difference on any endpoint."
    out.append(
        (
            "equivalence_claim",
            "An available ε = 0.10 arm: the recommended claim includes equivalence. "
            "A declared margin on an endpoint whose card states none.",
            config,
        )
    )

    config = designed_study()
    config["design"].update(
        claim="difference_or_equivalence",
        confirmatory=[PIT, PROBE, OPINION],
        sensitivity_epsilon=0.03,
    )
    out.append(
        (
            "control_switched_off",
            "An equivalence claim whose ε arm is switched off: a valid study with an absent "
            "positive control, so no endpoint can return 'equivalent'.",
            config,
        )
    )

    config = designed_study()
    _add_cell(config, "qr-llm", "t1_minp", (1, 7))
    out.append(
        (
            "two_cells",
            "Two (model, sampling) cells, three and two control seeds: one hypothesis per "
            "endpoint and cell, never pooled.",
            config,
        )
    )

    config = designed_study()
    next(c for c in config["cards"] if c["card_id"] == PROBE["card_id"])["overrides"] = {
        "k": 10,
        "items": 6,
    }
    out.append(
        ("below_floor", "Six prompts cannot reach significance in a family of three.", config)
    )

    config = designed_study()
    for model in ("model-b", "model-c"):
        config["models"].append(
            {
                "id": model,
                "family": "external",
                "provider": "custom",
                "served_name": model,
                "checkpoint": None,
            }
        )
    for model, sampling in itertools.product(
        ("qr-llm", "model-b", "model-c"), ("creative", "t1_minp")
    ):
        if (model, sampling) != ("qr-llm", "creative"):
            _add_cell(config, model, sampling, (1, 7))
    config["design"]["confirmatory"] = [PIT, MMD, PROBE, MEQ, OPINION]
    config["design"]["alpha"] = 0.005
    out.append(
        (
            "too_many_tests",
            "Five endpoints in six cells at α = 0.005 need more permutations than the "
            "analysis runs: the plan is refused.",
            config,
        )
    )

    config = designed_study()
    config["design"] = {
        "version": 1,
        "claim": "difference_or_equivalence",
        "confirmatory": [PIT, NEGATION, KS100],
        "alpha": 0.01,
        "sensitivity_epsilon": 0.03,
        "margins": [{**PROBE, "margin": 0.05}, {**PIT, "margin": 0.01}],
    }
    out.append(
        (
            "draft_not_eligible",
            "A draft the backend refuses: endpoints that cannot decide a study, margins that "
            "cannot stand, a positive control that is switched off.",
            config,
        )
    )

    config = _study1()
    _add_cell(config, "qr-llm", "t1_minp", (1,))
    config["design"] = {
        "version": 1,
        "claim": "difference",
        "confirmatory": [PIT, MMD, PROBE],
        "alpha": 0.05,
        "sensitivity_epsilon": 0.03,
        "margins": [],
    }
    out.append(
        (
            "draft_epsilon_and_seeds",
            "A draft the backend refuses: the ε arm shares a seed with a control arm, one "
            "cell has no ε arm and a single control seed.",
            config,
        )
    )

    config = _study1()
    _select(config, ["introspection_scales", "altered_state_meq", "opinion_quantum_sampling"])
    out.append(
        (
            "fallback_first_eligible",
            "No built-in primary and no card SESOI among the selected evals: the first "
            "eligible headline endpoint is recommended.",
            config,
        )
    )

    config = _study1()
    _select(config, ["introspection_scales", "sentience_statements", "unpredictabench"])
    out.append(("nothing_eligible", "No selected eval can decide a study.", config))

    config = designed_study()
    config["cards"].append({"card_id": "sycophancy_ays", "overrides": {"k": None, "items": None}})
    out.append(
        (
            "eval_derived",
            "A selected eval that is derived from the replies of another asks as many prompts "
            "as those replies allow, so its replies are not planned data of the token-stream "
            "tests. The planned count is that of the designed template.",
            config,
        )
    )

    # ── one case per problem code and per refusal the browser mirrors ──

    config = designed_study()
    _arm(config, "qrng_unsupervised").update(availability="unavailable", unavailable_reason="off")
    out.append(("no_cell", "A draft with no quantum arm to compare: nothing is tested.", config))

    config = designed_study()
    config["cards"] = []
    out.append(("no_eval", "A draft with no eval selected.", config))

    config = designed_study()
    _probe(config)["overrides"] = {"k": 40, "items": 31}
    out.append(
        (
            "out_of_bounds",
            "A draft whose repetitions and prompts are outside what the eval allows.",
            config,
        )
    )

    config = designed_study()
    _probe(config)["overrides"] = {"k": None, "items": None, "max_tokens": 5}
    out.append(
        (
            "token_limit_out_of_bounds",
            "A draft whose token limit is below the usual length of the eval's replies.",
            config,
        )
    )

    config = designed_study()
    config["design"]["confirmatory"] = []
    out.append(("no_outcome", "A draft in which no outcome decides the study.", config))

    config = designed_study()
    config["design"]["claim"] = "difference_or_equivalence"
    out.append(
        (
            "equivalence_needs_control",
            "A draft that claims absence within a margin and names no positive control.",
            config,
        )
    )

    config = designed_study()
    config["design"]["confirmatory"] = [PIT, MMD, PROBE, MEQ, OPINION]
    config["design"]["margins"] = [
        {**PIT, "margin": 0.01},
        {**PROBE, "margin": 0.02},
        {**OPINION, "margin": 0.1},
        {**MEQ, "margin": 0.11},
    ]
    out.append(
        (
            "margin_refusals",
            "Four margins that cannot stand, each for its own reason: a test with no paired "
            "difference (although its card states a SESOI), an outcome whose card states the "
            "margin, an outcome without a fixed range, and a margin above a tenth of the range.",
            config,
        )
    )

    config = designed_study()
    config["design"]["confirmatory"] = [PIT, MEQ]
    config["design"]["margins"] = [{**MEQ, "margin": 1e-160}]
    out.append(
        (
            "margin_too_small",
            "A margin below a thousandth of the range is refused. Left in, it would ask for "
            "more prompts than a number can hold.",
            config,
        )
    )

    config = designed_study()
    _probe(config)["overrides"] = {"k": None, "items": 8}
    out.append(
        (
            "control_below_floor",
            "Eight prompts can reach significance in a family of three, but they are fewer "
            "than the control of nine seed pairs is asked for: the analysis would not count "
            "the control as passed, and the outcome could only come out invalid.",
            config,
        )
    )

    config = designed_study()
    _more_seeds(config, (99, 123, 256, 512))
    config["design"]["alpha"] = 0.005
    out.append(
        (
            "seven_seeds",
            "Seven control seeds make 21 seed pairs per outcome. At α = 0.005 the 3 difference "
            "tests ask for 12 000 permutations, with which none of the 63 seed-pair tests "
            "could fail. The plan runs 25 200, twice the seed-pair tests divided by α.",
            config,
        )
    )

    # Designs the setup can build, which the seed-pair controls must not put over the cap:
    # with up to five control seeds the difference tests decide the permutation count.
    config = designed_study()
    _more_seeds(config, (99, 123))
    config["design"]["alpha"] = 0.005
    out.append(
        (
            "five_seeds_alpha_0_005",
            "Five control seeds at α = 0.005: 3 difference tests and 30 seed-pair tests. The "
            "plan is built with the 12 000 permutations its difference tests ask for.",
            config,
        )
    )

    config = designed_study()
    _add_cell(config, "qr-llm", "t1_minp", (1, 7, 42))
    config["models"].append(
        {
            "id": "model-b",
            "family": "external",
            "provider": "custom",
            "served_name": "model-b",
            "checkpoint": None,
        }
    )
    _add_cell(config, "model-b", "creative", (1, 7, 42))
    config["design"]["alpha"] = 0.005
    out.append(
        (
            "three_comparisons_alpha_0_005",
            "Three comparisons of three outcomes and three control seeds at α = 0.005: "
            "9 difference tests and 27 seed-pair tests, a plan of 36 000 permutations.",
            config,
        )
    )

    config = designed_study()
    _more_seeds(config, (99, 123))
    _add_cell(config, "qr-llm", "t1_minp", (1, 7, 42, 99, 123))
    config["design"]["alpha"] = 0.01
    out.append(
        (
            "five_seeds_six_hypotheses_alpha_0_01",
            "Two comparisons of three outcomes with five control seeds each at α = 0.01: "
            "6 difference tests and 60 seed-pair tests, a plan of 12 000 permutations.",
            config,
        )
    )

    config = designed_study()
    _more_seeds(config, (99,))
    config["design"]["confirmatory"] = [PIT, MMD, PROBE, MEQ, OPINION]
    config["design"]["alpha"] = 0.005
    out.append(
        (
            "four_seeds_five_hypotheses_alpha_0_005",
            "Five outcomes with four control seeds at α = 0.005: 5 difference tests and "
            "30 seed-pair tests, a plan of 20 000 permutations.",
            config,
        )
    )

    for alpha in (0.01, 0.005):
        config = designed_study()
        config["design"]["alpha"] = alpha
        out.append(
            (
                f"alpha_{alpha:g}".replace(".", "_"),
                f"The designed template at α = {alpha:g}: a valid plan at a stricter level.",
                config,
            )
        )

    config = designed_study()
    _epsilon_arm(config, 0.50, "prng_epsilon_050")
    _epsilon_arm(config, 0.01, "prng_epsilon_001")
    out.append(
        (
            "epsilon_fallback",
            "Two biased arms can run and neither is ε = 0.10: the larger one is the "
            "recommended positive control.",
            config,
        )
    )

    config = designed_study()
    _add_cell(config, "qr-llm", "t1_minp", (1, 7))
    _probe(config)["overrides"] = {"k": 10, "items": None}
    out.append(
        (
            "fewer_repetitions",
            "Fewer repetitions than the eval's own in a family of six: its 30 prompts are too "
            "few and it has no more, so the way out that is named is a number of repetitions.",
            config,
        )
    )

    # ── a study on the built-in plan is not held to a comparison ──

    config = _study1()
    config["arms"] = [a for a in config["arms"] if a["id"] not in ("prng_seed_7", "prng_seed_42")]
    out.append(
        (
            "legacy_one_seed",
            "A study without a design block and with one control seed. The server validates, "
            "plans and launches it, so the missing second seed is a warning there. With a "
            "design block the same arms are refused (draft_epsilon_and_seeds).",
            config,
        )
    )

    config = _study1()
    config["arms"] = [a for a in config["arms"] if a["source"] != "qrng"]
    config["supervised"] = None
    out.append(
        (
            "legacy_no_cell",
            "A study without a design block and without a quantum arm. The server validates, "
            "plans and launches it, so the missing comparison is a warning there. With a "
            "design block it is an error (no_cell).",
            config,
        )
    )

    # ── token-stream outcomes with nothing planned ──

    config = designed_study()
    _select(config, ["tier0_pit_residuals", "tier0_mmd_hamming", "source_guessing_self_report"])
    config["design"]["confirmatory"] = [PIT, MMD]
    out.append(
        (
            "no_planned_data",
            "The only outcomes that decide the study are tested on pooled tokens, and the "
            "only eval that generates replies is derived from the replies of another eval. "
            "No reply is planned on a fixed prompt list, so both hypotheses plan 0 "
            "generations and the share of planned data analysed could not be checked.",
            config,
        )
    )

    return out


def _probe(config: Config) -> dict[str, Any]:
    """The selection of the self-referential probe."""
    return next(c for c in config["cards"] if c["card_id"] == PROBE["card_id"])  # type: ignore[no-any-return]


def _case(name: str, shows: str, config: Config) -> Config:
    cards = load_cards()
    config = copy.deepcopy(config)
    hypotheses = design.draft_hypotheses(config, cards)
    designed = config.get("design") is not None
    claim = config["design"]["claim"] if designed else None
    try:
        valid: StudyConfig | None = StudyConfig.model_validate(config)
    except ValueError:
        valid = None
    plan: StatisticalPlan | None = None
    refusal: str | None = None
    try:
        # The projection is tried on drafts too: what it refuses is part of the contract.
        if designed:
            plan = design.project(StatisticalPlan, config, cards)
        elif valid is not None:
            plan = StatisticalPlan.from_config(valid, cards)
    except ValueError as error:
        refusal = str(error)
    if plan is not None and plan.design is not None and plan.design.hypotheses != hypotheses:
        raise AssertionError(f"{name}: the draft and the registered hypotheses differ")
    return {
        "name": name,
        "shows": shows,
        "config": config,
        "config_valid": valid is not None,
        "recommend": design.recommend(config, cards),
        "recommend_k": design.recommend_k(config, cards),
        "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
        "families": design.family_sizes(hypotheses).model_dump(mode="json") if hypotheses else None,
        "permutations": (
            design.plan_permutations(design.family_sizes(hypotheses), config["design"]["alpha"])
            if hypotheses
            else None
        ),
        "reachable_verdicts": {h.id: design.reachable_verdicts(h, claim) for h in hypotheses},
        "problems": design.problems(config, cards),
        "plan_hash": plan.plan_hash if plan is not None and valid is not None else None,
        "plan_refused": refusal,
    }


def design_fixture() -> Config:
    cards = load_cards()
    headline = [
        (card, endpoint) for _, card in sorted(cards.items()) for endpoint in card.endpoints.primary
    ]
    return {
        "note": (
            "Vectors for cards/design.py and its TypeScript mirror. Written by "
            "scripts/gen_design_fixtures.py; do not edit. `eligible` covers every headline "
            "endpoint of the registry; each case is a whole study config with what the backend "
            "derives from it. Some are drafts: `config_valid` is false where StudyConfig rejects "
            "the config, and `plan_refused` is the reason no plan can be built from the design."
        ),
        "max_permutations": design.MAX_PERMUTATIONS,
        "problem_codes": list(design.PROBLEM_CODES),
        "eligible": {
            f"{card.id}/{endpoint}": design.eligible(card, endpoint) for card, endpoint in headline
        },
        "ineligible_reason": {
            f"{card.id}/{endpoint}": design.ineligible_reason(card, endpoint)
            for card, endpoint in headline
        },
        "verdict_rules": [rule.model_dump(mode="json") for rule in design.VERDICT_RULES],
        "inconclusive_reason_text": dict(design.INCONCLUSIVE_REASON_TEXT),
        "cases": [_case(name, shows, config) for name, shows, config in _cases()],
    }


# ── write / check ────────────────────────────────────────────────────────────


def designed_prereg() -> str:
    """The pre-registration of the designed template, rendered as the launch freeze renders it.

    The real registry and no readiness snapshot, so the document depends on
    nothing outside the repository.
    """
    return load_renderer().render(StudyConfig.model_validate(designed_study()))


def render() -> dict[Path, str]:
    """Path → text for every fixture this script owns. Every file is UTF-8 with LF endings."""
    return {
        FIXTURES_DIR / DESIGNED_NAME: _dump(designed_study()),
        FIXTURES_DIR / POWER_NAME: _dump(power_fixture()),
        FIXTURES_DIR / DESIGN_NAME: _dump(design_fixture()),
        GOLDEN_DESIGNED: designed_prereg(),
    }


def _name(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def write_all() -> int:
    rendered = render()
    for path, text in rendered.items():
        # Bytes, so that no platform turns the line endings a hash was taken over.
        path.write_bytes(text.encode("utf-8"))
    print(f"wrote {len(rendered)} design fixtures:")
    print("\n".join(f"  {_name(path)}" for path in rendered))
    return 0


def check_all() -> int:
    rendered = render()
    stale = []
    for path, text in rendered.items():
        if not path.is_file():
            stale.append(f"missing: {_name(path)}")
        elif path.read_bytes() != text.encode("utf-8"):
            stale.append(f"differs: {_name(path)}")
    if stale:
        print("design fixtures are out of date — run `python scripts/gen_design_fixtures.py`:")
        print("\n".join(f"  {line}" for line in stale))
        return 1
    print(f"design fixtures up to date ({len(rendered)} files)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Verify instead of write.")
    args = parser.parse_args(argv)
    return check_all() if args.check else write_all()


if __name__ == "__main__":
    sys.exit(main())
