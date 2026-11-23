"""Sample-size bounds of a designed study (``cards/power.py``).

- the worked example of the design (Study 1's experience-claim rate) comes out
  as registered: all 30 prompts recommended, ``sufficient`` at k = 24, and
  ``marginal`` at the eval's own k = 20;
- the variance bound counts the told cells of a seeded run once: they share a
  seed, so the registered number is a worst case and not an average case;
- the floor is where a sign-flip test can first reach the corrected level, and
  the seed-against-seed control has a floor of its own;
- the rating ladder: token-stream tests are never rated, too few prompts are
  always insufficient, and ``sufficient`` needs a margin and a range;
- the recommended number of prompts is the smallest that is sufficient, and
  the recommended repetitions are the fewest that make the eval's prompts so;
- more prompts, more repetitions and more control seeds never hurt.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest

from qr_llm_evals.analysis.stats import holm_bonferroni, paired_permutation_test
from qr_llm_evals.cards import power
from qr_llm_evals.cards.design import plan_permutations
from qr_llm_evals.cards.design_schema import FamilySizes, SampleSize

ALPHAS = (0.05, 0.01, 0.005)
FAMILIES = (1, 3, 8, 32)

#: Study 1's self-referential probe as designed: 30 prompts, k = 24 (the eval's
#: own is 20, a study may set up to 30), three told cells, three control seeds,
#: a family of three difference tests and nine seed-pair tests, the card's ±0.05.
PROBE: dict[str, Any] = {
    "level": "sample",
    "range": (0.0, 1.0),
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


def _rate(**changes: Any) -> SampleSize:
    return power.rate(**{**PROBE, **changes})


def test_worked_example_probe_is_sufficient() -> None:
    sd = power.sd_bound(0.0, 1.0, "sample", 24, 3, 3)
    # Quantum side: 24 x 3 independent values. Control side: 24 per seed, three seeds.
    assert sd == pytest.approx(0.5 * math.sqrt(1 / 72 + 1 / 72))
    assert sd == pytest.approx(1 / 12)
    mde = power.mde_standardized(30, 0.05, 3)
    assert mde == pytest.approx(0.59, abs=5e-3)
    assert mde * sd == pytest.approx(0.049, abs=5e-4)
    assert power.power_at(0.05, sd, 30, 0.05, 3) == pytest.approx(0.814, abs=5e-4)
    assert power.prompts_needed(0.05 / sd, 0.05, 3) == 30
    assert power.n_floor(0.05, 3) == 7

    size = _rate()
    assert size.model_dump() == {
        "n": 30,
        "n_floor": 7,
        "control_floor": 10,
        "mde_standardized": pytest.approx(0.590737224359, abs=1e-12),
        "sd_bound": pytest.approx(0.083333333333, abs=1e-12),
        "mde_raw": pytest.approx(0.04922810203, abs=1e-12),
        "power_at_margin": pytest.approx(0.813898804596, abs=1e-12),
        "equivalence_floor": pytest.approx(0.04452383712, abs=1e-12),
        "n_recommended": 30,
        "reachable": True,
        "k_recommended": 24,
        "rating": "sufficient",
        "basis": "margin_worst_case",
    }
    # Without the margin the same 30 prompts are rated on the standardized
    # scale: 0.30 SD needs 117 prompts, and the eval has 30.
    standardized = _rate(margin=None)
    assert (standardized.rating, standardized.basis) == ("insufficient", "standardized_reference")
    assert (standardized.n_recommended, standardized.reachable) == (117, False)
    assert standardized.k_recommended is None


def test_told_cells_of_a_seeded_run_count_once() -> None:
    """The told cells of a seeded run share one seed, so they may repeat one another.

    At the eval's own k = 20 the bound that took them for independent values
    (0.0745, 90% power, 24 prompts) rated the probe sufficient. Counted once,
    the 30 prompts are marginal and 35 would be needed, more than the eval has.
    """
    independent = 0.5 * math.sqrt((1 + 1 / 3) / 60)
    assert independent == pytest.approx(0.0745, abs=5e-5)
    sd = power.sd_bound(0.0, 1.0, "sample", 20, 3, 3)
    assert sd == pytest.approx(0.5 * math.sqrt(1 / 60 + 1 / 60))
    assert sd == pytest.approx(0.0913, abs=5e-5)
    assert sd > independent
    assert power.power_at(0.05, sd, 30, 0.05, 3) == pytest.approx(0.728, abs=5e-4)
    assert power.prompts_needed(0.05 / sd, 0.05, 3) == 35

    size = _rate(k=20)
    assert (size.rating, size.n_recommended, size.reachable) == ("marginal", 35, False)
    assert size.equivalence_floor == pytest.approx(0.0488, abs=5e-5)
    # Repetitions are the way out: the fewest that make all 30 prompts enough.
    assert size.k_recommended == 24

    # The bound is the standard deviation of the case it is a bound for: each
    # reply 0 or 1 with probability one half, fresh on the quantum side, and
    # on a seeded run one draw per repetition that all three told cells repeat.
    rng = np.random.default_rng(7)
    prompts, k, told, seeds = 40_000, 20, 3, 3
    quantum = rng.integers(0, 2, (prompts, k * told)).mean(axis=1)
    per_run = rng.integers(0, 2, (prompts, seeds, k))  # the told cells repeat these draws
    control = per_run.mean(axis=(1, 2))
    assert float(np.std(quantum - control)) == pytest.approx(sd, rel=0.02)
    # Without the told factor nothing changed: the old and the new bound agree.
    for seeds_ in (2, 3, 5):
        assert power.sd_bound(0.0, 1.0, "sample", 10, 1, seeds_) == pytest.approx(
            0.5 * math.sqrt((1 + 1 / seeds_) / 10)
        )


def test_quantile_and_constants() -> None:
    assert power.z(0.5) == 0.0
    assert power.z(0.975) == pytest.approx(1.959963984540054)
    assert power.z(0.025) == pytest.approx(-power.z(0.975))
    assert (power.POWER, power.REFERENCE_EFFECT) == (0.80, 0.30)
    assert (power.MIN_COMPLETENESS, power.MARGIN_CAP_FRACTION) == (0.90, 0.10)
    assert power.MARGIN_FLOOR_FRACTION == 0.001


@pytest.mark.parametrize(("alpha", "m"), list(itertools.product(ALPHAS, FAMILIES)))
def test_floor_is_where_a_sign_flip_test_can_first_pass(alpha: float, m: int) -> None:
    """A sign-flip test on n prompts has 2**n labellings, so its two-sided p is at least 2/2**n."""
    floor = power.n_floor(alpha, m)
    assert 2 / 2**floor < alpha / m
    assert 2 / 2 ** (floor - 1) >= alpha / m


def test_floor_values() -> None:
    assert power.n_floor(0.05, 1) == 6
    assert power.n_floor(0.05, 3) == 7
    assert power.n_floor(0.005, 32) == 14


def test_control_floor_values() -> None:
    # Three outcomes and three seeds: nine seed-pair tests at 0.05.
    # 2/2**9 = 0.0039 < 0.05/9 = 0.0056 <= 2/2**8, and one prompt more for the estimate.
    assert power.n_floor(0.05, 9) == 9
    assert power.control_floor(0.05, 9) == 10
    assert power.control_floor(0.05, 30) == 12  # five seeds, three outcomes
    assert power.control_floor(0.01, 24) == 14
    assert power.control_floor(0.005, 160) == 17
    for alpha, family in itertools.product(ALPHAS, (1, 9, 30, 160, 480)):
        floor = power.control_floor(alpha, family)
        # Half the corrected level is within reach at the floor, not one prompt below.
        assert 2 / 2**floor < alpha / (2 * family)
        assert 2 / 2 ** (floor - 1) >= alpha / (2 * family)


#: Analysis seeds the control is tried under: the p-value is a Monte Carlo estimate.
ANALYSIS_SEEDS = range(20)


@pytest.mark.parametrize(
    ("alpha", "m", "seeds"),
    [
        (0.05, 3, 3),
        (0.05, 3, 5),
        (0.01, 8, 3),
        (0.005, 16, 5),
        (0.005, 3, 3),
        (0.05, 1, 2),
        (0.005, 3, 5),
        (0.005, 3, 7),
    ],
)
def test_a_seed_pair_can_fail_at_the_control_floor(alpha: float, m: int, seeds: int) -> None:
    """From the control floor on, the test that runs fails a pair that differs on every prompt.

    Two seeds that differ on every prompt in the same direction are the
    strongest result a sign-flip test can see. The p-value is the Monte Carlo
    estimate the analysis computes, with the plan's own permutation count,
    tried under twenty analysis seeds. At the floor the exact p-value is at
    most half the level a pair must reach, and the estimate scatters around
    it: the pair fails in all the analyses where the plan has permutations to
    spare, and in about half to three quarters of them where five or more
    control seeds leave the seed pairs only twice their number over alpha.
    Three prompts further up it fails in every one. Two prompts below the
    floor the exact p-value is not below the level, and the pair fails only
    when the estimate is lucky. That is why the floor is the fewest prompts
    the harness accepts, and why nothing says that a control below it cannot
    fail or that a control at it always does.
    """
    family = m * math.comb(seeds, 2)
    permutations = plan_permutations(
        FamilySizes(difference=m, equivalence=0, negative_control=family), alpha
    )

    def failing(prompts: int) -> int:
        """In how many of the analyses the pair fails the control."""
        ones, zeros = np.ones(prompts), np.zeros(prompts)
        count = 0
        for seed in ANALYSIS_SEEDS:
            result = paired_permutation_test(
                ones, zeros, np.arange(prompts), n_permutations=permutations, seed=seed
            )
            # The first step of the correction: every other pair at p = 1.
            adjusted = holm_bonferroni([result["p_value"], *([1.0] * (family - 1))])[0]
            count += adjusted < alpha
        return count

    floor = power.control_floor(alpha, family)
    assert 2 / 2**floor < alpha / (2 * family)
    at_floor = failing(floor)
    assert at_floor >= len(ANALYSIS_SEEDS) // 3
    assert failing(floor + 3) == len(ANALYSIS_SEEDS)
    assert 2 / 2 ** (floor - 2) >= alpha / family
    assert failing(floor - 2) < min(at_floor, len(ANALYSIS_SEEDS) / 2)


def test_token_stream_tests_are_never_rated() -> None:
    for changes in ({}, {"n": 2}, {"n": 10**6, "margin": 0.5}):
        size = _rate(level="token_stream", **changes)
        assert (size.rating, size.basis) == ("unknown", "no_closed_form")
        numbers = size.model_dump(exclude={"n", "n_floor", "rating", "basis"})
        assert set(numbers.values()) == {None}
    with pytest.raises(ValueError, match="no per-prompt variance bound"):
        power.sd_bound(0.0, 1.0, "token_stream", 20, 3, 3)
    with pytest.raises(ValueError, match="hi > lo"):
        power.sd_bound(1.0, 1.0, "sample", 20, 3, 3)


def test_below_the_floor_is_insufficient_whatever_the_margin() -> None:
    assert _rate(n=7).n_floor == 7
    for margin in (0.05, 0.1, None):
        assert _rate(n=6, margin=margin).rating == "insufficient"
    # A margin as wide as the whole range would be detected with certainty,
    # and still six prompts cannot produce a p-value below alpha / 3.
    wide = _rate(n=6, margin=1.0)
    assert wide.power_at_margin == pytest.approx(1.0)
    assert wide.rating == "insufficient"
    assert wide.n_floor == 7
    # No prompts planned (a card whose items are only known at run time).
    empty = _rate(n=0, dataset_items=0)
    assert empty.rating == "insufficient" and empty.mde_standardized is None
    assert empty.mde_raw is None and empty.equivalence_floor is None and empty.reachable is False
    assert empty.k_recommended is None


def test_below_the_control_floor_is_insufficient_and_never_recommended() -> None:
    """Seven to nine prompts reach significance, but no seed pair among nine could fail.

    The hypothesis could then only come out invalid, so the rating does not
    call such a number sufficient and the recommendation does not name it,
    however wide the margin.
    """
    wide = {"margin": 1.0}
    for n in (7, 8, 9):
        size = _rate(n=n, **wide)
        assert size.power_at_margin == pytest.approx(1.0)
        assert (size.n_floor, size.control_floor, size.rating) == (7, 10, "insufficient")
    at_floor = _rate(n=10, **wide)
    assert at_floor.rating == "sufficient"
    assert at_floor.n_recommended == at_floor.control_floor == 10
    # A larger seed-pair family raises the floor; none at all leaves only the test's own.
    assert _rate(m_control=30).control_floor == 12
    no_pairs = _rate(n=7, m_control=0, **wide)
    assert no_pairs.control_floor is None
    assert (no_pairs.rating, no_pairs.n_recommended) == ("sufficient", 7)
    # The standardized rating obeys the same floor.
    assert _rate(margin=None, n=9).rating == "insufficient"


def test_rating_ladder_with_a_margin() -> None:
    ratings = {n: _rate(n=n) for n in range(7, 40)}
    for n, size in ratings.items():
        assert size.power_at_margin is not None
        expected = (
            "insufficient"
            if n < 10
            else "sufficient"
            if size.power_at_margin >= 0.80
            else "marginal"
            if size.power_at_margin >= 0.50
            else "insufficient"
        )
        assert size.rating == expected, n
        assert size.n_recommended == 30 and size.basis == "margin_worst_case"
    assert {size.rating for size in ratings.values()} == {"sufficient", "marginal", "insufficient"}
    # The recommended number is the smallest sufficient one.
    assert ratings[30].rating == "sufficient" and ratings[29].rating == "marginal"


def test_sufficient_needs_a_margin_and_a_range() -> None:
    for n, margin, bounds in itertools.product(
        (7, 30, 117, 300, 5000), (None, 0.05, 0.5), (None, (0.0, 1.0))
    ):
        size = _rate(n=n, dataset_items=5000, margin=margin, range=bounds)
        if margin is None or bounds is None:
            assert size.basis == "standardized_reference"
            assert size.rating in {"marginal", "insufficient"}, (n, margin, bounds)
            assert size.rating == (
                "marginal" if power.mde_standardized(n, 0.05, 3) <= 0.30 else "insufficient"
            )
            # No raw-unit number is reported without both.
            assert size.sd_bound is None and size.mde_raw is None
            assert size.power_at_margin is None and size.equivalence_floor is None
            assert size.n_recommended == 117 and size.k_recommended is None
    assert _rate(n=116, margin=None).rating == "insufficient"
    assert _rate(n=117, margin=None).rating == "marginal"
    assert _rate(n=5000, margin=None).rating == "marginal"


@pytest.mark.parametrize(("alpha", "m"), list(itertools.product(ALPHAS, FAMILIES)))
def test_recommended_prompts_are_the_smallest_sufficient(alpha: float, m: int) -> None:
    for margin in (0.02, 0.05, 0.1):
        size = _rate(alpha=alpha, m=m, m_e=m, margin=margin, dataset_items=10**6)
        recommended = size.n_recommended
        assert recommended is not None and size.control_floor is not None
        least = max(size.n_floor, size.control_floor)
        assert recommended >= least
        at = _rate(alpha=alpha, m=m, m_e=m, margin=margin, n=recommended)
        assert at.rating == "sufficient"
        if recommended > least:
            assert _rate(alpha=alpha, m=m, m_e=m, margin=margin, n=recommended - 1).rating != (
                "sufficient"
            )
    reference = power.prompts_needed(power.REFERENCE_EFFECT, alpha, m)
    assert power.mde_standardized(reference, alpha, m) <= power.REFERENCE_EFFECT
    assert power.mde_standardized(reference - 1, alpha, m) > power.REFERENCE_EFFECT


def test_recommended_repetitions_are_the_fewest_that_make_the_prompts_enough() -> None:
    assert power.k_needed(0.0, 1.0, 0.05, 30, 3, 3, 0.05, 3, 20, 30) == 24
    assert power.k_needed(0.0, 1.0, 0.05, 30, 3, 3, 0.05, 3, 20, 23) is None
    assert power.k_needed(0.0, 1.0, 0.05, 30, 3, 3, 0.05, 3, 25, 30) == 25
    for k_set, items in itertools.product((10, 20, 24, 30), (30, 50, 200)):
        size = _rate(k=k_set, dataset_items=items)
        k = size.k_recommended
        assert k is not None and 20 <= k <= 30
        # At the recommended k all the prompts of the eval are sufficient ...
        assert _rate(k=k, n=items, dataset_items=items).rating == "sufficient"
        # ... at one repetition fewer they are not, unless the eval's own k already is.
        if k > 20:
            assert _rate(k=k - 1, n=items, dataset_items=items).rating != "sufficient"
        # It is a property of the eval and the design, not of what the study set.
        assert k == _rate(k=20, n=12, dataset_items=items).k_recommended
    # The eval's own k where that is enough; nothing where no k within the bounds is.
    assert _rate(dataset_items=200).k_recommended == 20
    assert _rate(k_max=23).k_recommended is None
    assert _rate(k_max=None).k_recommended is None  # no bounds: the eval's own k only
    assert _rate(alpha=0.005, m=32, m_e=32).k_recommended is None
    # Repetitions do not enter the bound of a value scored per prompt group, or of an eval without k.
    assert _rate(level="prompt_group").k_recommended is None
    assert _rate(k=None, k_default=None, k_max=None).k_recommended is None
    # An eval with fewer prompts than the control needs is not helped by repetitions.
    assert _rate(dataset_items=9, n=9).k_recommended is None


def test_a_larger_family_or_a_stricter_level_needs_more_prompts() -> None:
    assert _rate(alpha=0.005, m=32, m_e=32).n_floor == 14
    sizes = [_rate(alpha=alpha, m=m, m_e=1) for alpha in ALPHAS for m in FAMILIES]
    for a, b in itertools.pairwise(sizes[:4]):  # alpha 0.05, m growing
        assert a.mde_standardized is not None and b.mde_standardized is not None
        assert b.mde_standardized > a.mde_standardized
        assert (b.n_recommended or 0) >= (a.n_recommended or 0)
    by_alpha = [_rate(alpha=alpha) for alpha in ALPHAS]
    for a, b in itertools.pairwise(by_alpha):
        assert (b.power_at_margin or 0) < (a.power_at_margin or 0)
        assert (b.n_recommended or 0) > (a.n_recommended or 0)
    # 30 prompts are enough for one family of three at 0.05, not at the strict end.
    assert by_alpha[0].rating == "sufficient"
    assert _rate(alpha=0.005, m=32, m_e=32).rating == "insufficient"


def test_reachable_compares_with_the_whole_dataset() -> None:
    assert _rate(n=10, dataset_items=40).reachable is True
    assert _rate(n=10, dataset_items=30).reachable is True
    assert _rate(n=10, dataset_items=29).reachable is False


def test_repetitions_and_seeds_tighten_the_bound_but_add_no_units() -> None:
    def sd(**changes: Any) -> float:
        args = {
            "lo": 0.0,
            "hi": 1.0,
            "level": "sample",
            "k": 20,
            "told_cells": 3,
            "pooled_seeds": 3,
        }
        return power.sd_bound(**{**args, **changes})

    assert sd(k=40) < sd() < sd(k=10)
    assert sd(told_cells=3) < sd(told_cells=1)
    assert sd(pooled_seeds=5) < sd() < sd(pooled_seeds=2)
    assert sd(lo=-1.0) == pytest.approx(2 * sd())
    # Told cells sharpen the quantum side only. However many there are, the
    # control side keeps the variance of k values per seeded run.
    assert sd(told_cells=1000) > 0.5 * math.sqrt(1 / (20 * 3))
    assert sd(told_cells=1000) == pytest.approx(0.5 * math.sqrt(1 / 60), rel=1e-2)
    # A value scored per prompt group has one value per prompt, whatever k is.
    assert sd(level="prompt_group", k=100) == sd(level="prompt_group", k=10)
    assert sd(level="prompt_group") == pytest.approx(0.5 * math.sqrt(4 / 3))
    # k changes the raw-unit numbers, never the floor or the standardized ones.
    more_k, fewer_k = _rate(k=30), _rate(k=10)
    assert more_k.n_floor == fewer_k.n_floor
    assert more_k.mde_standardized == fewer_k.mde_standardized
    assert (more_k.mde_raw or 0) < (fewer_k.mde_raw or 0)
    assert _rate(k=None).sd_bound == _rate(k=1).sd_bound


def test_more_prompts_never_hurt() -> None:
    sizes = [_rate(n=n, dataset_items=1000) for n in (7, 10, 20, 30, 100, 1000)]
    for a, b in itertools.pairwise(sizes):
        assert (b.mde_standardized or 0) < (a.mde_standardized or 0)
        assert (b.mde_raw or 0) < (a.mde_raw or 0)
        assert (b.power_at_margin or 0) >= (a.power_at_margin or 0)
        assert (b.equivalence_floor or 0) < (a.equivalence_floor or 0)


def test_equivalence_floor() -> None:
    sd = power.sd_bound(0.0, 1.0, "sample", 24, 3, 3)
    floor = power.equivalence_floor(sd, 30, 0.05, 1)
    assert floor == pytest.approx((power.z(0.95) + power.z(0.90)) * sd / math.sqrt(30))
    assert power.equivalence_floor(sd, 30, 0.05, 3) > floor
    assert _rate().equivalence_floor == pytest.approx(floor, abs=1e-12)
    # No equivalence family, no floor.
    assert _rate(m_e=0).equivalence_floor is None


def test_a_margin_too_small_to_plan_for_is_a_refusal_not_a_crash() -> None:
    """An effect this small overflowed a float and surfaced as an HTTP 500."""
    with pytest.raises(ValueError, match="too small to plan for"):
        power.prompts_needed(1e-160, 0.05, 3)
    with pytest.raises(ValueError, match="too small to plan for"):
        _rate(margin=1e-160)
    # A small effect that can still be counted: (3.235601 / 0.001)**2 = 10 469 114.5, rounded up.
    assert power.prompts_needed(1e-3, 0.05, 3) == 10_469_115


def test_registered_numbers_are_rounded() -> None:
    """Plans are hashed; the last bits of libm must not reach them."""
    for changes in ({}, {"n": 12}, {"margin": None}, {"alpha": 0.005, "m": 32, "m_e": 32}):
        size = _rate(**changes)
        for name in (
            "mde_standardized",
            "sd_bound",
            "mde_raw",
            "power_at_margin",
            "equivalence_floor",
        ):
            value = getattr(size, name)
            assert value is None or value == round(value, power.PRECISION), name
