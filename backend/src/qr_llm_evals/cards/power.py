"""Can the planned number of prompts carry a hypothesis? Closed-form bounds only.

The unit of analysis of a paired test is the prompt: labels are flipped per
prompt and the standard error is clustered on it. Repetitions (k) and told
cells sharpen each prompt's value; they add no units. Everything here is
therefore a function of ``n``, the number of prompts, and is computed before
any data exist:

* a paired sign-flip test on ``n`` prompts cannot return a two-sided p below
  ``2 / 2**n``, so below :func:`n_floor` no effect of any size is significant
  at the first step of the correction;
* the same bound holds for the seed-against-seed control, whose Holm family
  is larger: below :func:`control_floor` it is not certain that two seeds
  that differ as much as the outcome allows would fail, so the harness does
  not take the control as one that can fail;
* for a bounded value the standard deviation of one prompt's difference has a
  worst case (:func:`sd_bound`), which turns a margin in raw units into a power
  (:func:`power_at`) and a number of prompts (:func:`prompts_needed`) or of
  repetitions (:func:`k_needed`);
* without a bound, only the standardized effect a study can detect is known
  (:func:`mde_standardized`), and it is rated against a reference effect.

The normal approximation and the worst-case variance ignore prompt-by-source
heterogeneity, so true power may be lower; the prereg says so beside every
number. Tests on the pooled token stream (KS, MMD) have no closed form and are
never rated.

``apps/web/src/lib/power.ts`` mirrors this module line for line and is checked
against ``packages/contracts/fixtures/power.json``.

Layer 0: standard library and the pydantic shapes of ``design_schema`` only.
"""

from __future__ import annotations

import math
from statistics import NormalDist

from qr_llm_evals.cards.design_schema import SampleSize, SampleSizeBasis, SampleSizeRating
from qr_llm_evals.cards.endpoint_meta import Level

#: Power every rating and every recommended sample size is computed for.
POWER = 0.80
#: Standardized effect an endpoint without a raw-unit margin is rated against.
REFERENCE_EFFECT = 0.30
#: Share of the planned pairs that must be analysed for a verdict to stand.
MIN_COMPLETENESS = 0.90
#: A declared margin may not exceed this share of the endpoint's range.
MARGIN_CAP_FRACTION = 0.10
#: Nor be below this share of it: no eval has the prompts to detect less, and
#: the number of prompts such a margin asks for is not one both sides can print.
MARGIN_FLOOR_FRACTION = 0.001
#: Below this power at the margin a sample size is rated insufficient.
MARGINAL_POWER = 0.50
#: Each of the two one-sided equivalence tests needs this power for 0.80 overall.
EQUIVALENCE_SIDE_POWER = 0.90
#: Decimals kept in a registered number. The last bits of ``log`` and ``erfc``
#: differ between C libraries, and these numbers are hashed into the plan.
PRECISION = 12

_NORMAL = NormalDist()


def z(p: float) -> float:
    """The standard normal quantile of ``p``."""
    return _NORMAL.inv_cdf(p)


def n_floor(alpha: float, m: int) -> int:
    """The fewest prompts at which a sign-flip test can reach ``alpha / m`` at all."""
    return math.floor(1 + math.log2(m / alpha)) + 1


def control_floor(alpha: float, m_control: int) -> int:
    """The fewest prompts the harness accepts for a seed-against-seed control.

    The control is one Holm family over every seed pair of every hypothesis
    (``m_control`` tests), so a pair fails only at ``p < alpha / m_control``.
    The floor is one prompt above the exact bound of :func:`n_floor`. The
    analysis estimates the p-value by Monte Carlo, and at the exact bound the
    estimate lands on either side of the level; one prompt more halves the
    smallest exact p, which leaves the estimate room. How much room depends on
    the permutations: with the fewest a plan gives its controls
    (``design.control_permutations``), a pair that differs as much as the
    outcome allows fails in about three analyses in four at the floor itself
    and in practically all of them three prompts above it. One prompt below
    the floor such a pair often still fails, so "cannot fail" is true only
    further down. The floor is where the harness stops taking the risk: a
    control that cannot fail passes whatever the seeds do.
    """
    return n_floor(alpha, m_control) + 1


def mde_standardized(n: int, alpha: float, m: int) -> float:
    """The smallest effect, in standard deviations, detected with :data:`POWER`."""
    return (z(1 - alpha / (2 * m)) + z(POWER)) / math.sqrt(n)


def sd_bound(
    lo: float, hi: float, level: Level, k: int, told_cells: int, pooled_seeds: int
) -> float:
    """Worst-case standard deviation of one prompt's quantum-minus-control difference.

    A value in ``[lo, hi]`` has a standard deviation of at most half the range.
    The quantum side is the mean of ``k * told_cells`` independent values per
    prompt: every request of a quantum arm draws fresh entropy. The control
    side is the mean over ``pooled_seeds`` seeded runs, and each run counts
    ``k`` independent values, not ``k * told_cells``: the told cells of a
    seeded run share one ``qr_seed`` per prompt and repetition
    (``models.envelope.request_seed`` leaves the told cell out of the key), so
    in the worst case they repeat one another. Without the told factor the two
    sides count the same and the variance is ``(1 + 1 / pooled_seeds) / k``.
    """
    if level == "token_stream":
        raise ValueError("a test on the pooled token stream has no per-prompt variance bound")
    if hi <= lo:
        raise ValueError(f"range must have hi > lo, got ({lo}, {hi})")
    if level == "sample":
        variance = 1 / (k * told_cells) + 1 / (k * pooled_seeds)
    else:
        variance = 1 + 1 / pooled_seeds
    return (hi - lo) / 2 * math.sqrt(variance)


def power_at(effect: float, sd: float, n: int, alpha: float, m: int) -> float:
    """Power to detect a raw ``effect`` on ``n`` prompts at the corrected level."""
    return _NORMAL.cdf(effect * math.sqrt(n) / sd - z(1 - alpha / (2 * m)))


def prompts_needed(effect_over_sd: float, alpha: float, m: int) -> int:
    """Prompts needed to detect a standardized effect with :data:`POWER`."""
    try:
        return math.ceil(((z(1 - alpha / (2 * m)) + z(POWER)) / effect_over_sd) ** 2)
    except (OverflowError, ZeroDivisionError):
        # A ValueError, like every other refusal of a plan: an effect this
        # small asks for more prompts than a float can count.
        raise ValueError(
            f"an effect of {effect_over_sd:g} standard deviations is too small to plan for"
        ) from None


def k_needed(
    lo: float,
    hi: float,
    margin: float,
    n: int,
    told_cells: int,
    pooled_seeds: int,
    alpha: float,
    m: int,
    k_from: int,
    k_to: int,
) -> int | None:
    """The fewest repetitions in ``[k_from, k_to]`` at which ``n`` prompts detect ``margin``.

    Repetitions add no units, but they shrink the worst-case variance of each
    prompt's value, so they are the way out when an eval has no more prompts.
    ``None`` when no number of repetitions in the range gives :data:`POWER`.
    """
    for k in range(k_from, k_to + 1):
        sd = sd_bound(lo, hi, "sample", k, told_cells, pooled_seeds)
        if power_at(margin, sd, n, alpha, m) >= POWER:
            return k
    return None


def equivalence_floor(sd: float, n: int, alpha: float, m_e: int) -> float:
    """The smallest margin ``n`` prompts can confirm when the true difference is zero."""
    return (z(1 - alpha / m_e) + z(EQUIVALENCE_SIDE_POWER)) * sd / math.sqrt(n)


def _registered(value: float) -> float:
    return round(value, PRECISION)


def rate(
    *,
    level: Level,
    range: tuple[float, float] | None,
    margin: float | None,
    n: int,
    dataset_items: int,
    k: int | None,
    told_cells: int,
    pooled_seeds: int,
    alpha: float,
    m: int,
    m_e: int,
    m_control: int,
    k_default: int | None,
    k_max: int | None,
) -> SampleSize:
    """Rate ``n`` prompts for one hypothesis in a family of ``m`` difference tests.

    ``m_e`` is the size of the equivalence family and ``m_control`` that of the
    seed-against-seed family (0 when the study has no seed pair). Only a margin
    together with a range can earn ``sufficient``: without both there is no
    raw-unit effect to have power for. Below :func:`control_floor` the analysis
    does not count the control as passed and the hypothesis could only resolve
    to invalid, so no number of prompts under it is rated above
    ``insufficient`` or recommended.

    ``k_default`` is the eval's own number of repetitions and ``k_max`` the most
    a study may set. ``k_recommended`` is the fewest repetitions from
    ``k_default`` up to ``k_max`` at which all the prompts of the eval are
    enough for the margin. It does not depend on ``k`` or ``n``, so following
    the recommendation does not change it.
    """
    floor = n_floor(alpha, m)
    if level == "token_stream":
        return SampleSize(
            n=n,
            n_floor=floor,
            control_floor=None,
            mde_standardized=None,
            sd_bound=None,
            mde_raw=None,
            power_at_margin=None,
            equivalence_floor=None,
            n_recommended=None,
            reachable=None,
            k_recommended=None,
            rating="unknown",
            basis="no_closed_form",
        )
    controls = control_floor(alpha, m_control) if m_control >= 1 else None
    least = floor if controls is None else max(floor, controls)
    # A card whose prompts are only known at run time plans zero of them.
    mde = mde_standardized(n, alpha, m) if n >= 1 else None
    sd: float | None = None
    power: float | None = None
    smallest_margin: float | None = None
    repetitions: int | None = None
    rating: SampleSizeRating
    basis: SampleSizeBasis
    if margin is not None and range is not None:
        basis = "margin_worst_case"
        sd = sd_bound(
            range[0], range[1], level, k if k is not None else 1, told_cells, pooled_seeds
        )
        power = power_at(margin, sd, n, alpha, m)
        if m_e >= 1 and n >= 1:
            smallest_margin = equivalence_floor(sd, n, alpha, m_e)
        recommended = max(least, prompts_needed(margin / sd, alpha, m))
        if level == "sample" and k_default is not None and dataset_items >= least:
            repetitions = k_needed(
                range[0],
                range[1],
                margin,
                dataset_items,
                told_cells,
                pooled_seeds,
                alpha,
                m,
                k_default,
                max(k_default, k_max if k_max is not None else k_default),
            )
        rating = (
            "sufficient"
            if power >= POWER
            else "marginal"
            if power >= MARGINAL_POWER
            else "insufficient"
        )
    else:
        basis = "standardized_reference"
        recommended = max(least, prompts_needed(REFERENCE_EFFECT, alpha, m))
        rating = "marginal" if mde is not None and mde <= REFERENCE_EFFECT else "insufficient"
    if n < least:
        rating = "insufficient"
    return SampleSize(
        n=n,
        n_floor=floor,
        control_floor=controls,
        mde_standardized=None if mde is None else _registered(mde),
        sd_bound=None if sd is None else _registered(sd),
        mde_raw=None if sd is None or mde is None else _registered(mde * sd),
        power_at_margin=None if power is None else _registered(power),
        equivalence_floor=None if smallest_margin is None else _registered(smallest_margin),
        n_recommended=recommended,
        reachable=recommended <= dataset_items,
        k_recommended=repetitions,
        rating=rating,
        basis=basis,
    )


__all__ = [
    "EQUIVALENCE_SIDE_POWER",
    "MARGINAL_POWER",
    "MARGIN_CAP_FRACTION",
    "MARGIN_FLOOR_FRACTION",
    "MIN_COMPLETENESS",
    "POWER",
    "PRECISION",
    "REFERENCE_EFFECT",
    "SampleSizeRating",
    "control_floor",
    "equivalence_floor",
    "k_needed",
    "mde_standardized",
    "n_floor",
    "power_at",
    "prompts_needed",
    "rate",
    "sd_bound",
    "z",
]
