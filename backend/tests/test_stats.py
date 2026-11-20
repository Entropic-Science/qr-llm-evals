"""Known-answer vectors, planted-shift recovery, null calibration and determinism
for ``analysis.stats`` and ``analysis.mmd`` (spec §5.2 Tier 0 / §2.10)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy import stats as sp

from qr_llm_evals.analysis import mmd, stats

# --------------------------------------------------------------------------- #
# Multiple-comparison control: known-answer vectors
# --------------------------------------------------------------------------- #

RAW_P = [0.01, 0.04, 0.03, 0.005, 0.2]


def test_bh_fdr_known_answer() -> None:
    # Hand-computed BH: sorted [0.005, 0.01, 0.03, 0.04, 0.2] * 5 / rank -> monotone.
    expected = [0.025, 0.05, 0.05, 0.025, 0.2]
    np.testing.assert_allclose(stats.bh_fdr(RAW_P), expected)
    assert stats.bh_fdr([]).size == 0


def test_holm_known_answer() -> None:
    # sorted p * (n - i + 1): 0.005*5=0.025, 0.01*4=0.04, 0.03*3=0.09, 0.04*2=0.08->0.09, 0.2*1=0.2
    expected = [0.04, 0.09, 0.09, 0.025, 0.2]
    np.testing.assert_allclose(stats.holm_bonferroni(RAW_P), expected)
    assert stats.holm_bonferroni([]).size == 0


def test_holm_is_between_raw_and_bonferroni_and_capped() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(size=25)
    holm = stats.holm_bonferroni(p)
    bonf = np.array(stats.bonferroni_correct(p))
    assert np.all(holm >= p) and np.all(holm <= bonf) and np.all(holm <= 1.0)
    assert stats.bonferroni_correct([0.3, 0.5]) == [0.6, 1.0]
    assert stats.bonferroni_correct([0.01], k=10) == [0.1]


# --------------------------------------------------------------------------- #
# CIs, effect sizes, paired bootstrap
# --------------------------------------------------------------------------- #


def test_binomial_ci_known_answer_and_edges() -> None:
    lo, hi = stats.binomial_ci(8, 10)
    assert (lo, hi) == pytest.approx((0.4439, 0.9748), abs=1e-4)
    assert stats.binomial_ci(0, 5)[0] == 0.0
    assert stats.binomial_ci(5, 5)[1] == 1.0
    with pytest.raises(ValueError):
        stats.binomial_ci(6, 5)


def test_cohens_d_known_answer_and_degenerate() -> None:
    a = [3.0, 4.0, 5.0, 6.0]
    b = [1.0, 3.0, 2.0, 5.0]  # diffs 2,1,3,1 -> mean 1.75, sd 0.9574
    assert stats.cohens_d(a, b) == pytest.approx(1.75 / np.std([2, 1, 3, 1], ddof=1))
    assert stats.cohens_d([1.0, 2.0], [1.0, 2.0]) == 0.0
    assert stats.cohens_d([2.0, 3.0], [1.0, 2.0]) == np.inf
    with pytest.raises(ValueError):
        stats.cohens_d([1.0, 2.0], [1.0])


def test_paired_bootstrap_detects_shift_and_is_deterministic() -> None:
    rng = np.random.default_rng(1)
    b = rng.normal(size=60)
    a = b + 0.8 + rng.normal(scale=0.5, size=60)
    r1 = stats.paired_bootstrap_test(a, b, n_resamples=2000, seed=7)
    r2 = stats.paired_bootstrap_test(a, b, n_resamples=2000, seed=7)
    assert r1 == r2
    assert r1["p_value"] < 0.01 and r1["ci_lower"] > 0
    assert r1["mean_diff"] == pytest.approx(float(np.mean(a - b)))
    null = stats.paired_bootstrap_test(b, b, n_resamples=500, seed=7)
    assert null["p_value"] == 1.0
    lo, hi = stats.bootstrap_ci(a, n_resamples=2000, seed=3)
    assert lo < np.mean(a) < hi


# --------------------------------------------------------------------------- #
# Prompt-clustered permutation and TOST
# --------------------------------------------------------------------------- #


def test_clustered_se_reduces_to_plain_se_with_singleton_clusters() -> None:
    d = np.array([0.5, -0.2, 1.1, 0.3, 0.9])
    cl = stats.prompt_clustered_se(d, np.arange(5))
    assert cl["se"] == pytest.approx(d.std(ddof=1) / np.sqrt(5))
    assert cl["df"] == 4


def test_paired_permutation_recovers_planted_shift_and_null() -> None:
    rng = np.random.default_rng(11)
    prompts = np.repeat(np.arange(20), 5)
    prompt_effect = rng.normal(scale=1.0, size=20)[prompts]  # shared difficulty per prompt
    b = prompt_effect + rng.normal(size=prompts.size)
    a = b + 0.7 + rng.normal(scale=0.4, size=prompts.size)
    r = stats.paired_permutation_test(a, b, prompts, n_permutations=2000, seed=5)
    assert r["p_value"] < 0.01 and r["ci_lower"] > 0 and r["n_prompts"] == 20
    assert r == stats.paired_permutation_test(a, b, prompts, n_permutations=2000, seed=5)
    # Null: swapping arms per prompt at random gives no effect.
    null_a = b + rng.normal(scale=0.4, size=prompts.size)
    r0 = stats.paired_permutation_test(null_a, b, prompts, n_permutations=2000, seed=5)
    assert r0["p_value"] > 0.05


def test_paired_permutation_null_calibration() -> None:
    """Under H0 (with strong within-prompt correlation) the rejection rate stays near alpha."""
    rng = np.random.default_rng(2024)
    prompts = np.repeat(np.arange(15), 4)
    rejections = 0
    n_rep = 60
    for _ in range(n_rep):
        shared = rng.normal(scale=2.0, size=15)[prompts]
        a = shared + rng.normal(size=prompts.size)
        b = shared + rng.normal(size=prompts.size)
        r = stats.paired_permutation_test(
            a, b, prompts, n_permutations=300, seed=int(rng.integers(1 << 30))
        )
        rejections += r["p_value"] < 0.05
    assert rejections <= 9  # ≈ 5 % ± binomial noise; 9/60 is the 99.9 % tail


def test_tost_known_answer_matches_scipy_ttests() -> None:
    d = np.array([0.02, -0.05, 0.01, 0.03, -0.01, 0.00, 0.02, -0.02])
    margin = 0.1
    r = stats.tost_paired(d, margin, alpha=0.05)
    p_lower = float(sp.ttest_1samp(d, -margin, alternative="greater").pvalue)
    p_upper = float(sp.ttest_1samp(d, margin, alternative="less").pvalue)
    assert r["p_lower"] == pytest.approx(p_lower)
    assert r["p_upper"] == pytest.approx(p_upper)
    assert r["p_value"] == pytest.approx(max(p_lower, p_upper))
    assert r["equivalent"] and r["ci_level"] == pytest.approx(0.9)
    assert -margin < r["ci_lower"] < r["ci_upper"] < margin
    # A real effect larger than the margin is NOT equivalent.
    r2 = stats.tost_paired(d + 0.3, margin)
    assert not r2["equivalent"] and r2["p_value"] > 0.5
    # Clustered variant uses G - 1 df.
    r3 = stats.tost_paired(d, margin, prompt_ids=[0, 0, 1, 1, 2, 2, 3, 3])
    assert r3["df"] == 3


def test_tost_margin_rule() -> None:
    pilot = np.linspace(-0.05, 0.05, 101)
    assert stats.tost_margin(0.25, pilot) == 0.25
    assert stats.tost_margin(0.01, pilot) == pytest.approx(np.percentile(np.abs(pilot), 95))
    assert stats.tost_margin(0.3) == 0.3


# --------------------------------------------------------------------------- #
# KS / AD on PIT residuals
# --------------------------------------------------------------------------- #


def test_ks_and_ad_uniform_pass_on_uniform_and_fail_on_biased() -> None:
    rng = np.random.default_rng(99)
    u = rng.uniform(size=2000)
    assert stats.ks_uniform(u)["p_value"] > 0.01
    assert stats.anderson_darling_uniform(u)["p_value"] > 0.01
    biased = np.clip(u**1.5, 0, 1)
    assert stats.ks_uniform(biased)["p_value"] < 1e-6
    assert stats.anderson_darling_uniform(biased)["p_value"] < 1e-6
    with pytest.raises(ValueError):
        stats.ks_uniform([0.5, 1.2])


def test_anderson_darling_known_answer() -> None:
    # Hand-computable: u = (i - 0.5) / n for n = 4 gives A² ≈ 0.0947 (very uniform).
    u = (np.arange(1, 5) - 0.5) / 4
    r = stats.anderson_darling_uniform(u)
    n = 4
    i = np.arange(1, n + 1)
    expected = -n - np.mean((2 * i - 1) * (np.log(u) + np.log(1 - u[::-1])))
    assert r["statistic"] == pytest.approx(expected)
    assert r["p_value"] > 0.9


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_anderson_darling_pvalue_matches_monte_carlo(seed: int) -> None:
    """The Marsaglia CDF must agree with scipy's seeded Monte-Carlo null for U[0, 1]."""
    rng = np.random.default_rng(seed)
    u = rng.uniform(size=150) ** (1.0 + 0.15 * seed)  # mild, seed-dependent departure
    ours = stats.anderson_darling_uniform(u)
    mc = sp.goodness_of_fit(
        sp.uniform,
        u,
        known_params={"loc": 0.0, "scale": 1.0},
        statistic="ad",
        n_mc_samples=4000,
        rng=123,
    )
    assert ours["statistic"] == pytest.approx(float(mc.statistic), rel=1e-6)
    assert ours["p_value"] == pytest.approx(float(mc.pvalue), abs=0.03)


def test_two_sample_ks_and_ad() -> None:
    rng = np.random.default_rng(5)
    x, y = rng.uniform(size=500), rng.uniform(size=500)
    assert stats.ks_two_sample(x, y)["p_value"] > 0.01
    shifted = np.clip(rng.uniform(size=500) ** 2, 0, 1)
    assert stats.ks_two_sample(x, shifted)["p_value"] < 1e-6
    ad = stats.anderson_two_sample(x, shifted)
    assert ad["p_value"] <= 0.001 and ad["p_capped"]


# --------------------------------------------------------------------------- #
# Stream layer: Stouffer-Z, cumulative deviation, McNemar
# --------------------------------------------------------------------------- #


def test_stouffer_and_binomial_z_known_answers() -> None:
    z = stats.binomial_z([60, 50], [100, 100])
    np.testing.assert_allclose(z, [2.0, 0.0])
    r = stats.stouffer_z([1.0, 1.0, 1.0, 1.0])
    assert r["z"] == pytest.approx(2.0)
    assert r["p_value"] == pytest.approx(2 * sp.norm.sf(2.0))
    weighted = stats.stouffer_z([2.0, 0.0], weights=[1.0, 0.0])
    assert weighted["z"] == pytest.approx(2.0)


def test_cumulative_deviation_series() -> None:
    bits = np.array([1, 1, 1, 0, 1, 1, 0, 1], dtype=float)
    r = stats.cumulative_deviation(bits, expected_mean=0.5, expected_var=0.25)
    np.testing.assert_allclose(r["cumdev"], np.cumsum(bits - 0.5))
    assert r["envelope"][0] == pytest.approx(1.959964 * 0.5, rel=1e-5)
    assert r["terminal_z"] == pytest.approx(2.0 / (0.5 * np.sqrt(8)))
    assert r["n"] == 8


def test_mcnemar_known_answer() -> None:
    a = np.array([1, 1, 1, 1, 0, 0, 1, 1, 1, 0], dtype=bool)
    b = np.array([1, 0, 0, 0, 0, 1, 1, 1, 0, 0], dtype=bool)
    r = stats.mcnemar_test(a, b)
    assert r["table"] == {"a": 3, "b": 4, "c": 1, "d": 2}
    assert r["chi2"] == pytest.approx((abs(4 - 1) - 1) ** 2 / 5)
    assert stats.mcnemar_test([1, 0], [1, 0])["p_value"] == 1.0


# --------------------------------------------------------------------------- #
# MMD-Hamming (T0-b)
# --------------------------------------------------------------------------- #


def _sample_arm(
    rng: np.random.Generator, n_prompts: int, per_prompt: int, length: int, shift: float
) -> tuple[np.ndarray, np.ndarray]:
    """Categorical token sampler; each prompt has its own base distribution over 6 tokens.

    ``shift`` moves probability mass from token 0 to token 1 — the "planted shift"
    of spec §5.2. A ``shift`` of 0 is a pure seed-vs-seed null.
    """
    seqs: list[list[int]] = []
    prompts: list[int] = []
    prompt_rng = np.random.default_rng(1234)  # base distributions shared by both arms
    for g in range(n_prompts):
        base = prompt_rng.dirichlet(np.ones(6) * 2.0)
        p = base.copy()
        moved = min(shift, p[0] * 0.9)
        p[0] -= moved
        p[1] += moved
        for _ in range(per_prompt):
            seqs.append(list(rng.choice(6, size=length, p=p)))
            prompts.append(g)
    return mmd.pad_sequences(seqs), np.array(prompts)


def _brute_force_mmd(
    seqs_a: np.ndarray, seqs_b: np.ndarray, pa: np.ndarray, pb: np.ndarray
) -> float:
    """Literal transcription of upstream ``_mmd`` + ``get_hamming_kernel`` (no vectorisation)."""
    length = seqs_a.shape[1]

    def k(x: np.ndarray, y: np.ndarray) -> float:
        return float(np.sum(x == y)) / length

    xx = [
        k(seqs_a[i], seqs_a[j])
        for i, j in itertools.permutations(range(len(seqs_a)), 2)
        if pa[i] == pa[j]
    ]
    yy = [
        k(seqs_b[i], seqs_b[j])
        for i, j in itertools.permutations(range(len(seqs_b)), 2)
        if pb[i] == pb[j]
    ]
    xy = [
        k(seqs_a[i], seqs_b[j])
        for i in range(len(seqs_a))
        for j in range(len(seqs_b))
        if pa[i] == pb[j]
    ]
    return float(np.mean(xx) - 2 * np.mean(xy) + np.mean(yy))


def test_pad_sequences_and_kernel() -> None:
    padded = mmd.pad_sequences([[1, 2, 3], [1, 2], [4]])
    assert padded.shape == (3, 3)
    assert padded[1].tolist() == [1, 2, mmd.PAD_TOKEN]
    k = mmd.hamming_kernel(padded, padded)
    assert k[0, 0] == 3 and k[0, 1] == 2 and k[0, 2] == 0
    assert k[1, 2] == 1  # pads compare equal (upstream semantics): length agreement counts
    with pytest.raises(ValueError):
        mmd.pad_sequences([])


def test_vendored_mmd_matches_brute_force_transcription_of_upstream() -> None:
    rng = np.random.default_rng(3)
    a, pa = _sample_arm(rng, n_prompts=4, per_prompt=5, length=7, shift=0.0)
    b, pb = _sample_arm(rng, n_prompts=4, per_prompt=6, length=7, shift=0.2)
    assert mmd.mmd_hamming(a, b, pa, pb) == pytest.approx(_brute_force_mmd(a, b, pa, pb))


@pytest.mark.skipif(not mmd.upstream_available(), reason="model_equality_testing not installed")
def test_vendored_mmd_equals_upstream_package_on_fixture() -> None:
    rng = np.random.default_rng(3)
    a, pa = _sample_arm(rng, n_prompts=4, per_prompt=5, length=7, shift=0.0)
    b, pb = _sample_arm(rng, n_prompts=4, per_prompt=6, length=7, shift=0.2)
    r = mmd.mmd_hamming_permutation_test(a, b, pa, pb, n_permutations=50, implementation="auto")
    assert r["statistic_upstream"] is not None
    assert r["statistic"] == pytest.approx(r["statistic_upstream"])


def test_mmd_recovers_planted_shift_and_null_on_seed_vs_seed() -> None:
    rng_a = np.random.default_rng(1)
    rng_b = np.random.default_rng(7)
    rng_c = np.random.default_rng(42)
    a, pa = _sample_arm(rng_a, n_prompts=10, per_prompt=10, length=20, shift=0.0)
    b, pb = _sample_arm(rng_b, n_prompts=10, per_prompt=10, length=20, shift=0.0)
    c, pc = _sample_arm(rng_c, n_prompts=10, per_prompt=10, length=20, shift=0.35)

    planted = mmd.mmd_hamming_permutation_test(a, c, pa, pc, n_permutations=2000, seed=0)
    assert planted["p_value"] < 0.01 and planted["n_permutations"] == 2000
    assert planted["statistic"] > planted["null_quantiles"]["q95"]

    null = mmd.mmd_hamming_permutation_test(a, b, pa, pb, n_permutations=2000, seed=0)
    assert null["p_value"] > 0.05
    assert null["implementation"] in ("vendored", "upstream")
    assert null["n_prompts"] == 10 and null["n_a"] == 100 and null["n_b"] == 100

    # Determinism given seed; a different seed changes only the null draw, not the statistic.
    again = mmd.mmd_hamming_permutation_test(a, b, pa, pb, n_permutations=2000, seed=0)
    assert again == null
    other = mmd.mmd_hamming_permutation_test(a, b, pa, pb, n_permutations=2000, seed=1)
    assert other["statistic"] == null["statistic"]


def test_mmd_null_calibration_seed_vs_seed() -> None:
    """Across replicated seed-vs-seed contrasts the rejection rate stays near alpha."""
    rejections = 0
    n_rep = 30
    for rep in range(n_rep):
        a, pa = _sample_arm(np.random.default_rng(100 + rep), 6, 6, 12, 0.0)
        b, pb = _sample_arm(np.random.default_rng(500 + rep), 6, 6, 12, 0.0)
        r = mmd.mmd_hamming_permutation_test(a, b, pa, pb, n_permutations=200, seed=rep)
        rejections += r["p_value"] < 0.05
    assert rejections <= 6


def test_mmd_permutation_keeps_within_prompt_counts() -> None:
    prompt_idx = np.array([0, 0, 0, 0, 1, 1, 1, 1, 1, 1])
    labels = np.array([1, 1, 0, 0, 1, 1, 1, 0, 0, 0])
    perm = mmd._permuted_labels(prompt_idx, labels, 25, np.random.default_rng(0))
    assert perm.shape == (25, 10)
    assert np.all(perm[:, :4].sum(axis=1) == 2) and np.all(perm[:, 4:].sum(axis=1) == 3)


def test_mmd_blockwise_equals_full_kernel() -> None:
    rng = np.random.default_rng(5)
    pooled = rng.integers(0, 6, size=(40, 12))
    prompt_idx = np.repeat(np.arange(4), 10)
    labels = np.tile(np.array([1] * 5 + [0] * 5), 4)
    labels_matrix = np.vstack([labels[None, :], mmd._permuted_labels(prompt_idx, labels, 30, rng)])
    full = mmd._mmd_from_kernel(mmd._pooled_kernel(pooled, prompt_idx), prompt_idx, labels_matrix)
    blocks = mmd._mmd_blockwise(pooled, prompt_idx, labels_matrix)
    assert np.allclose(blocks, full, rtol=0, atol=1e-12)


def test_mmd_drops_prompts_without_two_samples_per_arm() -> None:
    """Prompts asked once per arm (derived cards with k = 1) do not move the statistic."""
    rng = np.random.default_rng(11)
    a, pa = _sample_arm(rng, n_prompts=6, per_prompt=6, length=10, shift=0.0)
    b, pb = _sample_arm(rng, n_prompts=6, per_prompt=6, length=10, shift=0.0)
    base = mmd.mmd_hamming_permutation_test(a, b, pa, pb, n_permutations=200, seed=0)
    # 40 one-off prompts with identical short answers on both arms, as source guesses are.
    once = mmd.pad_sequences([[5]] * 40)
    width = a.shape[1]
    once = np.pad(once, ((0, 0), (0, width - once.shape[1])), constant_values=mmd.PAD_TOKEN)
    ids = np.arange(100, 140)
    mixed = mmd.mmd_hamming_permutation_test(
        np.vstack([a, once]),
        np.vstack([b, once]),
        np.concatenate([pa, ids]),
        np.concatenate([pb, ids]),
        n_permutations=200,
        seed=0,
    )
    assert mixed["n_prompts_dropped"] == 40 and base["n_prompts_dropped"] == 0
    assert mixed["statistic"] == pytest.approx(base["statistic"])
    assert mixed["p_value"] == base["p_value"]
    assert mixed["n_prompts"] == 6 and mixed["n_a"] == 36 and mixed["n_b"] == 36


def test_mmd_input_validation() -> None:
    a = mmd.pad_sequences([[1, 2], [1, 3], [2, 2], [3, 3]])
    with pytest.raises(ValueError):  # one sample per arm per prompt -> no within-arm pairs
        mmd.mmd_hamming(a[:2], a[2:], [0, 1], [0, 1])
    with pytest.raises(ValueError):  # mismatched L
        mmd.mmd_hamming(a, a[:, :1], [0, 0, 0, 0], [0, 0, 0, 0])
    if not mmd.upstream_available():
        with pytest.raises(RuntimeError):
            mmd.mmd_hamming_permutation_test(a, a, [0] * 4, [0] * 4, implementation="upstream")
    with pytest.raises(ValueError):
        mmd.mmd_hamming_permutation_test(a, a, [0] * 4, [0] * 4, n_permutations=0)
