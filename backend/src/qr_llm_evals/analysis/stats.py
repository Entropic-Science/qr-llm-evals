"""Statistical core — a pure numpy/scipy **leaf** (spec §2.10, §1.1).

Ported from ``qr-llm-research/src/qr_llm_research/stats/core.py`` (paired
bootstrap 10 000, BH-FDR, Bonferroni, Cohen's d, McNemar, exact binomial CI)
and extended with the Study 1 secondaries: Holm–Bonferroni, paired per-prompt
permutation with prompt-clustered SEs, TOST equivalence with a preregistered
margin and 90 % CI, one- and two-sample KS / Anderson–Darling on PIT residuals,
Stouffer-Z and cumulative-deviation series for the T4-l stream layer.

Conventions that every function here follows:

- **Seeded, modern RNG.** Every resampling routine takes ``seed`` and builds a
  ``numpy.random.Generator`` (``default_rng``) locally; nothing touches global
  RNG state, so results are reproducible byte-for-byte from the prereg seed.
- **Resampling p-values use add-one smoothing** ``(k + 1) / (B + 1)`` (Davison &
  Hinkley): a Monte-Carlo p-value can never legitimately be 0 — its resolution
  floor is ``1 / (B + 1)``.
- **Imports nothing internal** (enforced by ``tests/test_layering.py``) and no
  heavy dependency: TOST is implemented directly on ``scipy.stats.t`` rather
  than via statsmodels so the leaf stays importable without the ``[analysis]``
  extra.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import stats

__all__ = [
    "anderson_darling_uniform",
    "anderson_two_sample",
    "bh_fdr",
    "binomial_ci",
    "binomial_z",
    "bonferroni_correct",
    "bootstrap_ci",
    "cohens_d",
    "cumulative_deviation",
    "holm_bonferroni",
    "ks_two_sample",
    "ks_uniform",
    "mcnemar_test",
    "paired_bootstrap_test",
    "paired_permutation_test",
    "prompt_clustered_se",
    "stouffer_z",
    "tost_margin",
    "tost_paired",
]

FloatArray = NDArray[np.float64]


def _as_float_array(values: ArrayLike, name: str) -> FloatArray:
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values")
    return arr


def _paired(scores_a: ArrayLike, scores_b: ArrayLike) -> tuple[FloatArray, FloatArray]:
    a = _as_float_array(scores_a, "scores_a")
    b = _as_float_array(scores_b, "scores_b")
    if a.size != b.size:
        raise ValueError(f"Arrays must have same length: {a.size} != {b.size}")
    return a, b


def _mc_pvalue(count_extreme: int, n_resamples: int) -> float:
    """Add-one-smoothed Monte-Carlo p-value, capped at 1."""
    return min(1.0, (count_extreme + 1) / (n_resamples + 1))


# --------------------------------------------------------------------------- #
# Confidence intervals and effect sizes
# --------------------------------------------------------------------------- #


def binomial_ci(successes: int, trials: int, ci: float = 0.95) -> tuple[float, float]:
    """Exact (Clopper–Pearson) confidence interval for a pass rate.

    Preferred over Wald intervals because eval pass rates sit near 0 or 1,
    where normal approximations are badly anti-conservative. Closed endpoints
    at ``k = 0`` (lower = 0) and ``k = n`` (upper = 1).
    """
    if trials <= 0:
        raise ValueError(f"trials must be positive, got {trials}")
    if not 0 <= successes <= trials:
        raise ValueError(f"successes must be in [0, {trials}], got {successes}")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci}")
    alpha = 1.0 - ci
    lower = (
        0.0
        if successes == 0
        else float(stats.beta.ppf(alpha / 2, successes, trials - successes + 1))
    )
    upper = (
        1.0
        if successes == trials
        else float(stats.beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    )
    return lower, upper


def bootstrap_ci(
    scores: ArrayLike,
    n_resamples: int = 10_000,
    ci: float = 0.95,
    seed: int = 42,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for the mean (vectorised)."""
    x = _as_float_array(scores, "scores")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci}")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, x.size, size=(n_resamples, x.size))
    boot_means = x[idx].mean(axis=1)
    alpha = 1.0 - ci
    lower, upper = np.percentile(boot_means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lower), float(upper)


def cohens_d(scores_a: ArrayLike, scores_b: ArrayLike) -> float:
    """Paired Cohen's d (``d_z``): mean difference over the SD of the differences.

    A constant non-zero difference (zero spread, non-zero mean) is the strongest
    possible paired effect and is reported as ``±inf`` rather than 0.
    """
    a, b = _paired(scores_a, scores_b)
    diffs = a - b
    if diffs.size < 2:
        raise ValueError("cohens_d needs at least two pairs")
    sd = float(diffs.std(ddof=1))
    mean = float(diffs.mean())
    if sd == 0.0:
        return 0.0 if mean == 0.0 else math.copysign(math.inf, mean)
    return mean / sd


# --------------------------------------------------------------------------- #
# Paired tests
# --------------------------------------------------------------------------- #


def paired_bootstrap_test(
    scores_a: ArrayLike,
    scores_b: ArrayLike,
    n_resamples: int = 10_000,
    seed: int = 42,
    ci: float = 0.95,
) -> dict[str, Any]:
    """Paired bootstrap for the mean difference ``A − B`` (default 10 000 resamples).

    Pairs are resampled with replacement (same index into both arms, so prompt
    difficulty is controlled). The two-sided p-value is twice the smoothed
    proportion of resampled means on the wrong side of zero.
    """
    a, b = _paired(scores_a, scores_b)
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci}")
    diffs = a - b
    observed = float(diffs.mean())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, diffs.size, size=(n_resamples, diffs.size))
    boot = diffs[idx].mean(axis=1)

    if observed > 0:
        p_value = min(1.0, 2.0 * _mc_pvalue(int(np.sum(boot <= 0)), n_resamples))
    elif observed < 0:
        p_value = min(1.0, 2.0 * _mc_pvalue(int(np.sum(boot >= 0)), n_resamples))
    else:
        p_value = 1.0

    alpha = 1.0 - ci
    lo, hi = np.percentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "mean_diff": observed,
        "ci_lower": float(lo),
        "ci_upper": float(hi),
        "ci_level": ci,
        "p_value": p_value,
        "n_pairs": int(diffs.size),
        "n_resamples": int(n_resamples),
        "significant_at_05": p_value < 0.05,
    }


def prompt_clustered_se(diffs: ArrayLike, prompt_ids: ArrayLike) -> dict[str, Any]:
    """Cluster-robust standard error of the mean difference, clustered by prompt.

    Several completions per prompt are not independent (they share the prompt);
    the cluster estimator treats each prompt's *summed* differences as one
    draw. With ``G`` clusters and ``N`` pairs::

        SE² = G / (G − 1) · Σ_g (S_g − n_g · d̄)² / N²,   S_g = Σ_{i∈g} d_i

    which reduces to the ordinary SE of the mean when every cluster has one pair.
    """
    d = _as_float_array(diffs, "diffs")
    groups = np.asarray(prompt_ids).ravel()
    if groups.size != d.size:
        raise ValueError(f"prompt_ids length {groups.size} != diffs length {d.size}")
    _, inverse, counts = np.unique(groups, return_inverse=True, return_counts=True)
    n_groups = int(counts.size)
    if n_groups < 2:
        raise ValueError("prompt-clustered SE needs at least two prompts")
    mean = float(d.mean())
    sums = np.bincount(inverse, weights=d, minlength=n_groups)
    resid = sums - counts * mean
    var = (n_groups / (n_groups - 1)) * float(np.sum(resid**2)) / d.size**2
    return {
        "mean_diff": mean,
        "se": math.sqrt(var),
        "n_prompts": n_groups,
        "n_pairs": int(d.size),
        "df": n_groups - 1,
    }


def paired_permutation_test(
    scores_a: ArrayLike,
    scores_b: ArrayLike,
    prompt_ids: ArrayLike,
    n_permutations: int = 10_000,
    seed: int = 42,
    ci: float = 0.95,
) -> dict[str, Any]:
    """Paired per-prompt permutation test with prompt-clustered SE (spec §2.10).

    The exchangeable unit under H0 is the *prompt*: a permutation flips the
    sign of every paired difference belonging to a prompt at once (arm labels
    swapped within that prompt), never of individual pairs, so within-prompt
    correlation cannot inflate the null. The statistic is the mean difference;
    the reported CI is ``mean ± t_{df} · SE_cluster`` with ``df = G − 1``.
    """
    a, b = _paired(scores_a, scores_b)
    diffs = a - b
    cl = prompt_clustered_se(diffs, prompt_ids)
    groups = np.asarray(prompt_ids).ravel()
    _, inverse = np.unique(groups, return_inverse=True)
    n_groups: int = cl["n_prompts"]
    sums = np.bincount(inverse, weights=diffs, minlength=n_groups)
    observed: float = cl["mean_diff"]
    se: float = cl["se"]

    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n_permutations, n_groups))
    null_means = (signs @ sums) / diffs.size
    extreme = int(np.sum(np.abs(null_means) >= abs(observed) - 1e-12))
    p_value = _mc_pvalue(extreme, n_permutations)

    if se > 0:
        t_clustered = observed / se
    else:
        t_clustered = 0.0 if observed == 0 else math.copysign(math.inf, observed)
    t_crit = float(stats.t.ppf(1 - (1 - ci) / 2, df=cl["df"]))
    return {
        "mean_diff": observed,
        "se_clustered": se,
        "t_clustered": t_clustered,
        "ci_lower": observed - t_crit * se,
        "ci_upper": observed + t_crit * se,
        "ci_level": ci,
        "p_value": p_value,
        "n_prompts": n_groups,
        "n_pairs": cl["n_pairs"],
        "n_permutations": int(n_permutations),
        "significant_at_05": p_value < 0.05,
    }


# --------------------------------------------------------------------------- #
# Equivalence (TOST)
# --------------------------------------------------------------------------- #


def tost_margin(sesoi: float, pilot_null_diffs: ArrayLike | None = None) -> float:
    """Preregistered TOST margin ``Δ = max(SESOI, 95th pct of |seed-vs-seed diff|)``."""
    if sesoi < 0:
        raise ValueError("sesoi must be non-negative")
    if pilot_null_diffs is None:
        return float(sesoi)
    pilot = np.abs(_as_float_array(pilot_null_diffs, "pilot_null_diffs"))
    return float(max(sesoi, np.percentile(pilot, 95)))


def tost_paired(
    diffs: ArrayLike,
    margin: float,
    alpha: float = 0.05,
    prompt_ids: ArrayLike | None = None,
) -> dict[str, Any]:
    """Two one-sided tests of equivalence on paired differences (Lakens 2017).

    H0 (non-equivalence): ``|μ| ≥ Δ``. Both one-sided t-tests — ``μ > −Δ`` and
    ``μ < Δ`` — must reject at ``alpha``; equivalently the ``1 − 2α`` (90 %) CI
    must lie inside ``±Δ``. When ``prompt_ids`` is given the SE and df come from
    :func:`prompt_clustered_se`; otherwise the ordinary paired SE with
    ``df = n − 1`` is used.
    """
    d = _as_float_array(diffs, "diffs")
    if margin <= 0:
        raise ValueError("margin must be positive")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    if prompt_ids is not None:
        cl = prompt_clustered_se(d, prompt_ids)
        mean, se, df = float(cl["mean_diff"]), float(cl["se"]), int(cl["df"])
    else:
        if d.size < 2:
            raise ValueError("tost_paired needs at least two differences")
        mean = float(d.mean())
        se = float(d.std(ddof=1) / math.sqrt(d.size))
        df = d.size - 1
    if se == 0.0:
        # Degenerate: no spread. Equivalent iff the constant difference sits inside ±Δ.
        inside = abs(mean) < margin
        p_degenerate = 0.0 if inside else 1.0
        return {
            "mean_diff": mean,
            "se": 0.0,
            "df": df,
            "margin": float(margin),
            "t_lower": math.inf if inside else -math.inf,
            "t_upper": -math.inf if inside else math.inf,
            "p_lower": p_degenerate,
            "p_upper": p_degenerate,
            "p_value": p_degenerate,
            "ci_lower": mean,
            "ci_upper": mean,
            "ci_level": 1 - 2 * alpha,
            "equivalent": inside,
        }
    t_lower = (mean + margin) / se  # H0: μ ≤ −Δ  vs  H1: μ > −Δ
    t_upper = (mean - margin) / se  # H0: μ ≥ +Δ  vs  H1: μ < +Δ
    p_lower = float(stats.t.sf(t_lower, df))
    p_upper = float(stats.t.cdf(t_upper, df))
    p_value = max(p_lower, p_upper)
    t_crit = float(stats.t.ppf(1 - alpha, df))
    return {
        "mean_diff": mean,
        "se": se,
        "df": df,
        "margin": float(margin),
        "t_lower": t_lower,
        "t_upper": t_upper,
        "p_lower": p_lower,
        "p_upper": p_upper,
        "p_value": p_value,
        "ci_lower": mean - t_crit * se,
        "ci_upper": mean + t_crit * se,
        "ci_level": 1 - 2 * alpha,
        "equivalent": p_value < alpha,
    }


# --------------------------------------------------------------------------- #
# Multiple-comparison control
# --------------------------------------------------------------------------- #


def bonferroni_correct(p_values: ArrayLike, k: int | None = None) -> list[float]:
    """Bonferroni-adjusted p-values (``min(1, p · k)``); ``k`` defaults to the count."""
    pvals = [float(p) for p in np.asarray(p_values, dtype=np.float64).ravel()]
    if k is None:
        k = len(pvals)
    return [min(1.0, p * k) for p in pvals]


def bh_fdr(p_values: ArrayLike) -> FloatArray:
    """Benjamini–Hochberg FDR-adjusted p-values (step-up, monotone, capped at 1).

    Matches ``statsmodels.stats.multitest.multipletests(method="fdr_bh")`` in
    pure numpy. Input order is preserved; empty input → empty output.
    """
    pvals = np.asarray(p_values, dtype=np.float64).ravel()
    n = pvals.size
    if n == 0:
        return pvals
    order = np.argsort(pvals, kind="stable")
    ranks = np.arange(1, n + 1, dtype=np.float64)
    adjusted_sorted = pvals[order] * n / ranks
    adjusted_sorted = np.minimum.accumulate(adjusted_sorted[::-1])[::-1]
    adjusted_sorted = np.minimum(adjusted_sorted, 1.0)
    out = np.empty(n, dtype=np.float64)
    out[order] = adjusted_sorted
    return out


def holm_bonferroni(p_values: ArrayLike) -> FloatArray:
    """Holm–Bonferroni step-down adjusted p-values (family-wise error control).

    Used for the Study 1 confirmatory primaries at family ``α = 0.05``: sorted
    ascending, ``p_(i)`` is multiplied by ``n − i + 1``, made monotone
    non-decreasing (running max), and capped at 1. Uniformly more powerful than
    Bonferroni with the same FWER guarantee.
    """
    pvals = np.asarray(p_values, dtype=np.float64).ravel()
    n = pvals.size
    if n == 0:
        return pvals
    order = np.argsort(pvals, kind="stable")
    multipliers = np.arange(n, 0, -1, dtype=np.float64)
    adjusted_sorted = pvals[order] * multipliers
    adjusted_sorted = np.maximum.accumulate(adjusted_sorted)
    adjusted_sorted = np.minimum(adjusted_sorted, 1.0)
    out = np.empty(n, dtype=np.float64)
    out[order] = adjusted_sorted
    return out


# --------------------------------------------------------------------------- #
# Distribution tests on PIT residuals (T0-a)
# --------------------------------------------------------------------------- #


def _check_unit_interval(residuals: ArrayLike) -> FloatArray:
    r = _as_float_array(residuals, "residuals")
    if np.any((r < 0) | (r > 1)):
        raise ValueError("PIT residuals must lie in [0, 1]")
    return r


def ks_uniform(residuals: ArrayLike) -> dict[str, Any]:
    """One-sample Kolmogorov–Smirnov test of PIT residuals against U[0, 1]."""
    r = _check_unit_interval(residuals)
    res = stats.kstest(r, "uniform", method="exact" if r.size <= 10_000 else "asymp")
    return {"statistic": float(res.statistic), "p_value": float(res.pvalue), "n": int(r.size)}


def ks_two_sample(x: ArrayLike, y: ArrayLike) -> dict[str, Any]:
    """Two-sample Kolmogorov–Smirnov test (arm vs arm), two-sided."""
    a = _as_float_array(x, "x")
    b = _as_float_array(y, "y")
    res = stats.ks_2samp(a, b, alternative="two-sided", method="auto")
    return {
        "statistic": float(res.statistic),
        "p_value": float(res.pvalue),
        "n_x": int(a.size),
        "n_y": int(b.size),
    }


def anderson_darling_uniform(residuals: ArrayLike) -> dict[str, Any]:
    """One-sample Anderson–Darling test against a fully specified U[0, 1].

    ``A² = −n − (1/n) Σ (2i − 1)[ln u_(i) + ln(1 − u_(n+1−i))]``. The p-value
    is ``1 − P_n(A²)`` from Marsaglia & Marsaglia (2004), *Evaluating the
    Anderson-Darling Distribution* (J. Stat. Soft. 9(2)): the asymptotic CDF
    plus their finite-``n`` correction, the same routine R's ``goftest::pAD``
    uses — valid for a fully specified null ("case 0"), which U[0, 1] is. AD
    weights the tails more than KS, so it is the more sensitive of the two to
    the near-0 / near-1 residual pile-ups a biased sampler would produce.
    """
    r = _check_unit_interval(residuals)
    n = r.size
    tiny = np.finfo(np.float64).tiny
    u = np.clip(np.sort(r), tiny, 1 - 1e-16)
    i = np.arange(1, n + 1, dtype=np.float64)
    a2 = -n - float(np.mean((2 * i - 1) * (np.log(u) + np.log1p(-u[::-1]))))
    p = 1.0 - _anderson_darling_cdf(n, a2)
    return {"statistic": float(a2), "p_value": float(min(max(p, 0.0), 1.0)), "n": int(n)}


def _anderson_darling_cdf_asymptotic(z: float) -> float:
    """Marsaglia & Marsaglia (2004) ``adinf``: limiting CDF of A², |err| < 2e-6."""
    if z <= 0.0:
        return 0.0
    if z < 2.0:
        return (
            math.exp(-1.2337141 / z)
            / math.sqrt(z)
            * (
                2.00012
                + (0.247105 - (0.0649821 - (0.0347962 - (0.011672 - 0.00168691 * z) * z) * z) * z)
                * z
            )
        )
    return math.exp(
        -math.exp(
            1.0776 - (2.30695 - (0.43424 - (0.082433 - (0.008056 - 0.0003146 * z) * z) * z) * z) * z
        )
    )


def _anderson_darling_cdf(n: int, z: float) -> float:
    """Marsaglia & Marsaglia (2004) ``AD(n, z)``: finite-n CDF = ``adinf(z) + errfix(n, adinf(z))``."""
    x = _anderson_darling_cdf_asymptotic(z)
    if x > 0.8:
        v = (
            -130.2137
            + (745.2337 - (1705.091 - (1950.646 - (1116.360 - 255.7844 * x) * x) * x) * x) * x
        ) / n
        return min(1.0, x + v)
    c = 0.01265 + 0.1757 / n
    if x < c:
        t = x / c
        t = math.sqrt(t) * (1.0 - t) * (49 * t - 102)
        return max(0.0, x + t * (0.0037 / (n * n) + 0.00078 / n + 0.00006) / n)
    t = (x - c) / (0.8 - c)
    t = -0.00022633 + (6.54034 - (14.6538 - (14.458 - (8.259 - 1.91864 * t) * t) * t) * t) * t
    return x + t * (0.04213 + 0.01365 / n) / n


def anderson_two_sample(x: ArrayLike, y: ArrayLike) -> dict[str, Any]:
    """Two-sample (k = 2) Anderson–Darling test via ``scipy.stats.anderson_ksamp``.

    scipy floors/caps the p-value at ``[0.001, 0.25]``; ``p_capped`` flags when
    the reported value hit that range so tables can print ``<0.001`` / ``>0.25``.
    """
    a = _as_float_array(x, "x")
    b = _as_float_array(y, "y")
    with warnings.catch_warnings():
        # scipy warns when it floors/caps p; `p_capped` below reports that explicitly.
        warnings.simplefilter("ignore", category=UserWarning)
        try:
            res = stats.anderson_ksamp([a, b], variant="midrank")  # scipy >= 1.17
        except TypeError:
            res = stats.anderson_ksamp([a, b], midrank=True)
    p = float(res.pvalue)
    return {
        "statistic": float(res.statistic),
        "p_value": p,
        "p_capped": p <= 0.001 or p >= 0.25,
        "n_x": int(a.size),
        "n_y": int(b.size),
    }


# --------------------------------------------------------------------------- #
# Stream-level layer (T4-l): Stouffer-Z and cumulative deviation
# --------------------------------------------------------------------------- #


def binomial_z(successes: ArrayLike, trials: ArrayLike, p: float = 0.5) -> FloatArray:
    """Per-window z-scores of a bit fraction: ``(k − n p) / sqrt(n p (1 − p))``."""
    k = np.asarray(successes, dtype=np.float64).ravel()
    n = np.asarray(trials, dtype=np.float64).ravel()
    if k.size != n.size:
        raise ValueError("successes and trials must have the same length")
    if np.any(n <= 0):
        raise ValueError("trials must be positive")
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    result: FloatArray = (k - n * p) / np.sqrt(n * p * (1 - p))
    return result


def stouffer_z(z_scores: ArrayLike, weights: ArrayLike | None = None) -> dict[str, Any]:
    """Stouffer's weighted combination ``Z = Σ w_i z_i / sqrt(Σ w_i²)``, two-sided p."""
    z = _as_float_array(z_scores, "z_scores")
    w = np.ones_like(z) if weights is None else _as_float_array(weights, "weights")
    if w.size != z.size:
        raise ValueError("weights must match z_scores in length")
    if np.any(w < 0) or not np.any(w > 0):
        raise ValueError("weights must be non-negative with at least one positive")
    combined = float(np.sum(w * z) / math.sqrt(float(np.sum(w**2))))
    return {
        "z": combined,
        "p_value": float(2 * stats.norm.sf(abs(combined))),
        "n": int(z.size),
    }


def cumulative_deviation(
    values: ArrayLike,
    expected_mean: float,
    expected_var: float,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Cumulative deviation series ``C_k = Σ_{i≤k} (x_i − μ)`` with a ``1 − α`` envelope.

    The GCP/PEAR-style plot: under H0 the walk has variance ``k · σ²`` at step
    ``k``, so ``±z_{1−α/2} · σ · sqrt(k)`` is the pointwise envelope. The final
    value scaled by ``σ · sqrt(n)`` is the terminal z-score.
    """
    x = _as_float_array(values, "values")
    if expected_var <= 0:
        raise ValueError("expected_var must be positive")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    cumdev = np.cumsum(x - expected_mean)
    k = np.arange(1, x.size + 1, dtype=np.float64)
    envelope = float(stats.norm.ppf(1 - alpha / 2)) * math.sqrt(expected_var) * np.sqrt(k)
    terminal_z = float(cumdev[-1] / (math.sqrt(expected_var) * math.sqrt(x.size)))
    return {
        "cumdev": cumdev,
        "envelope": envelope,
        "terminal_z": terminal_z,
        "terminal_p_value": float(2 * stats.norm.sf(abs(terminal_z))),
        "n": int(x.size),
    }


# --------------------------------------------------------------------------- #
# Paired binary outcomes
# --------------------------------------------------------------------------- #


def mcnemar_test(correct_a: ArrayLike, correct_b: ArrayLike) -> dict[str, Any]:
    """McNemar's test (continuity-corrected) for paired binary outcomes."""
    a = np.asarray(correct_a, dtype=bool).ravel()
    b = np.asarray(correct_b, dtype=bool).ravel()
    if a.size != b.size:
        raise ValueError(f"Arrays must have same length: {a.size} != {b.size}")
    both = int(np.sum(a & b))
    a_only = int(np.sum(a & ~b))
    b_only = int(np.sum(~a & b))
    neither = int(np.sum(~a & ~b))
    table = {"a": both, "b": a_only, "c": b_only, "d": neither}
    n_discordant = a_only + b_only
    if n_discordant == 0:
        return {
            "chi2": 0.0,
            "p_value": 1.0,
            "significant_at_05": False,
            "n_discordant": 0,
            "table": table,
        }
    chi2 = (abs(a_only - b_only) - 1) ** 2 / n_discordant
    p_value = float(stats.chi2.sf(chi2, df=1))
    return {
        "chi2": float(chi2),
        "p_value": p_value,
        "significant_at_05": p_value < 0.05,
        "n_discordant": n_discordant,
        "table": table,
    }
