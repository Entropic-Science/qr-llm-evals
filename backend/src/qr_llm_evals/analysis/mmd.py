"""T0-b: MMD with a Hamming kernel on token-id sequences + within-prompt permutation test.

Port of the Model Equality Testing statistic (Gao et al., ICLR 2025;
``model-equality-testing``, ``tests.mmd_hamming`` → ``_mmd``), a pure
numpy **leaf** like :mod:`qr_llm_evals.analysis.stats`.

The statistic (identical to upstream, verified on a fixture in ``test_stats.py``):

- ``K(x, y) = Σ_i 1[x_i == y_i]`` over pre-padded sequences (pad tokens compare
  equal, exactly as upstream, so length agreement is part of the signal).
- Pairs from *different prompts* contribute 0 (prompts are handled jointly as
  ``(prompt, completion)`` pairs and never compared across prompts).
- Kernel normalised so the diagonal is 1 — since ``K(x, x) = L`` this is ``K / L``.
- Estimator: unbiased-style U-statistic with self-pairs removed::

      MMD² = mean_{i≠j, same prompt} K_XX − 2 · mean_{same prompt} K_XY
             + mean_{i≠j, same prompt} K_YY

Where the port deliberately **differs from upstream** (spec §2.10 mandates it):

- Upstream's ``two_sample_permutation_pvalue`` shuffles labels **globally**
  with ``torch.randperm`` (unseedable, moves samples across prompts). Here the
  permutation shuffles arm labels **within each prompt**, with a seeded numpy
  ``Generator`` and ``b ≥ 2000`` by default; the per-prompt arm counts are
  invariant, which keeps the null exchangeable.
- The p-value uses add-one smoothing ``(k + 1) / (b + 1)``.

If ``model_equality_testing`` is importable (it needs ``torch``; see the
``[metrics]`` extra) the observed statistic is *also* computed through the
upstream code path and returned as ``statistic_upstream`` for cross-checking;
the permutation is always ours.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "PAD_TOKEN",
    "hamming_kernel",
    "mmd_hamming",
    "mmd_hamming_permutation_test",
    "pad_sequences",
    "upstream_available",
]

#: Pad value for :func:`pad_sequences`; negative so it never collides with a token id.
PAD_TOKEN = -1

IntArray = NDArray[np.int64]
FloatArray = NDArray[np.float64]

Implementation = Literal["auto", "vendored", "upstream"]


def upstream_available() -> bool:
    """True if the upstream ``model_equality_testing`` package imports cleanly."""
    try:
        importlib.import_module("model_equality_testing.algorithm")
    except Exception:  # noqa: BLE001 — any import failure (torch missing, ABI) means "not available"
        return False
    return True


def pad_sequences(
    sequences: Sequence[Sequence[int]], length: int | None = None, pad: int = PAD_TOKEN
) -> IntArray:
    """Right-pad variable-length token-id sequences to a ``(n, L)`` int64 array.

    ``length`` defaults to the longest sequence; longer sequences are truncated
    when an explicit ``length`` is given (so both arms of a test can share one L).
    """
    if len(sequences) == 0:
        raise ValueError("sequences must not be empty")
    max_len = max((len(s) for s in sequences), default=0)
    length = max_len if length is None else length
    if length <= 0:
        raise ValueError("sequences must contain at least one token")
    out = np.full((len(sequences), length), pad, dtype=np.int64)
    for i, seq in enumerate(sequences):
        row = np.asarray(seq, dtype=np.int64)[:length]
        out[i, : row.size] = row
    return out


def hamming_kernel(x: IntArray, y: IntArray, memory_threshold: int = 10_000) -> FloatArray:
    """Pairwise count of position-wise equal tokens, ``(n, m)``; broadcast when small."""
    x2 = np.asarray(x, dtype=np.int64)
    y2 = np.asarray(y, dtype=np.int64)
    if x2.ndim != 2 or y2.ndim != 2 or x2.shape[1] != y2.shape[1]:
        raise ValueError("x and y must be 2-D with the same sequence length")
    if x2.shape[0] * y2.shape[0] <= memory_threshold**2:
        out: FloatArray = np.sum(x2[:, None, :] == y2[None, :, :], axis=-1).astype(np.float64)
        return out
    out = np.zeros((x2.shape[0], y2.shape[0]), dtype=np.float64)
    for i in range(x2.shape[0]):
        out[i] = np.sum(x2[i][None, :] == y2, axis=-1)
    return out


def _prepare(
    seqs_a: ArrayLike, seqs_b: ArrayLike, prompts_a: ArrayLike, prompts_b: ArrayLike
) -> tuple[IntArray, IntArray, IntArray]:
    a = np.asarray(seqs_a, dtype=np.int64)
    b = np.asarray(seqs_b, dtype=np.int64)
    pa = np.asarray(prompts_a).ravel()
    pb = np.asarray(prompts_b).ravel()
    if a.ndim != 2 or b.ndim != 2:
        raise ValueError("token sequences must be 2-D (n, L) arrays; use pad_sequences()")
    if a.shape[1] != b.shape[1]:
        raise ValueError("both arms must be padded to the same length L")
    if pa.size != a.shape[0] or pb.size != b.shape[0]:
        raise ValueError("prompt id arrays must match the number of sequences")
    pooled = np.concatenate([a, b], axis=0)
    _, prompt_idx = np.unique(np.concatenate([pa, pb]), return_inverse=True)
    labels = np.concatenate(
        [np.ones(a.shape[0], dtype=np.int64), np.zeros(b.shape[0], dtype=np.int64)]
    )
    return pooled, prompt_idx.astype(np.int64), labels


def _pooled_kernel(pooled: IntArray, prompt_idx: IntArray) -> FloatArray:
    """Normalised Hamming kernel on the pooled rows, zeroed across prompts and on the diagonal."""
    length = pooled.shape[1]
    k = hamming_kernel(pooled, pooled) / float(length)
    k[prompt_idx[:, None] != prompt_idx[None, :]] = 0.0
    np.fill_diagonal(k, 0.0)
    return k


def _pair_counts(
    prompt_idx: IntArray, labels_matrix: IntArray
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Within-prompt pair counts (n_XX, n_XY, n_YY) for each labelling row of ``labels_matrix``."""
    n_prompts = int(prompt_idx.max()) + 1
    onehot = np.zeros((prompt_idx.size, n_prompts), dtype=np.float64)
    onehot[np.arange(prompt_idx.size), prompt_idx] = 1.0
    n_a = labels_matrix.astype(np.float64) @ onehot  # (b, G)
    n_g = onehot.sum(axis=0)  # (G,)
    n_b = n_g[None, :] - n_a
    n_xx = np.sum(n_a * (n_a - 1), axis=1)
    n_yy = np.sum(n_b * (n_b - 1), axis=1)
    n_xy = np.sum(n_a * n_b, axis=1)
    return n_xx, n_xy, n_yy


def _mmd_from_kernel(k: FloatArray, prompt_idx: IntArray, labels_matrix: IntArray) -> FloatArray:
    """MMD² for each row of ``labels_matrix`` (1 = arm A) from the pooled kernel."""
    z = labels_matrix.astype(np.float64)
    kz = z @ k  # (b, N)
    s_xx = np.sum(kz * z, axis=1)
    s_xy = np.sum(kz * (1.0 - z), axis=1)
    total = k.sum()
    s_yy = total - s_xx - 2.0 * s_xy
    n_xx, n_xy, n_yy = _pair_counts(prompt_idx, labels_matrix)
    if np.any(n_xx == 0) or np.any(n_yy == 0) or np.any(n_xy == 0):
        raise ValueError("every prompt needs at least two samples per arm for the MMD estimator")
    result: FloatArray = s_xx / n_xx - 2.0 * s_xy / n_xy + s_yy / n_yy
    return result


def mmd_hamming(
    seqs_a: ArrayLike, seqs_b: ArrayLike, prompts_a: ArrayLike, prompts_b: ArrayLike
) -> float:
    """Observed MMD²-Hamming between arm A and arm B (prompts matched jointly)."""
    pooled, prompt_idx, labels = _prepare(seqs_a, seqs_b, prompts_a, prompts_b)
    k = _pooled_kernel(pooled, prompt_idx)
    return float(_mmd_from_kernel(k, prompt_idx, labels[None, :])[0])


def _upstream_statistic(
    seqs_a: IntArray, seqs_b: IntArray, prompt_idx: IntArray, n_a: int
) -> float:
    algorithm = importlib.import_module("model_equality_testing.algorithm")
    distribution = importlib.import_module("model_equality_testing.distribution")
    m = int(prompt_idx.max()) + 1
    sample_a = distribution.CompletionSample(prompts=prompt_idx[:n_a], completions=seqs_a, m=m)
    sample_b = distribution.CompletionSample(prompts=prompt_idx[n_a:], completions=seqs_b, m=m)
    _, statistic = algorithm.run_two_sample_test(
        sample_a, sample_b, pvalue_type="dummy", stat_type="mmd_hamming"
    )
    return float(statistic)


def _permuted_labels(
    prompt_idx: IntArray, labels: IntArray, n_permutations: int, rng: np.random.Generator
) -> IntArray:
    """``(b, N)`` label matrix: arm labels shuffled independently within each prompt."""
    out = np.empty((n_permutations, labels.size), dtype=np.int64)
    for g in np.unique(prompt_idx):
        members = np.flatnonzero(prompt_idx == g)
        block = np.tile(labels[members], (n_permutations, 1))
        perm = rng.permuted(block, axis=1)
        out[:, members] = perm
    return out


def mmd_hamming_permutation_test(
    seqs_a: ArrayLike,
    seqs_b: ArrayLike,
    prompts_a: ArrayLike,
    prompts_b: ArrayLike,
    n_permutations: int = 2000,
    seed: int = 42,
    implementation: Implementation = "auto",
) -> dict[str, Any]:
    """MMD-Hamming two-sample test with labels permuted **within prompt** (spec T0-b).

    Args:
        seqs_a, seqs_b: ``(n, L)`` / ``(m, L)`` padded token-id arrays (see
            :func:`pad_sequences`; both arms must share ``L``).
        prompts_a, prompts_b: prompt identifiers per row (any hashable dtype).
        n_permutations: ``b``; the preregistered plan requires ``≥ 2000``.
        seed: seed for the numpy ``Generator`` driving the permutations.
        implementation: ``"vendored"`` never touches upstream; ``"upstream"``
            requires the package and raises otherwise; ``"auto"`` cross-checks
            through upstream only when importable.

    Returns:
        ``statistic`` (observed MMD²), ``p_value`` (smoothed, one-sided: large
        MMD rejects), ``null_quantiles`` (5/50/95 % of the permutation
        distribution), ``n_permutations``, ``n_prompts``, ``n_a``, ``n_b``,
        ``implementation`` (which code path produced ``statistic``) and
        ``statistic_upstream`` (``None`` unless upstream ran).
    """
    if n_permutations < 1:
        raise ValueError("n_permutations must be positive")
    if implementation == "upstream" and not upstream_available():
        raise RuntimeError("model_equality_testing is not importable; install the [metrics] extra")
    pooled, prompt_idx, labels = _prepare(seqs_a, seqs_b, prompts_a, prompts_b)
    n_a = int(labels.sum())
    k = _pooled_kernel(pooled, prompt_idx)
    observed = float(_mmd_from_kernel(k, prompt_idx, labels[None, :])[0])

    statistic_upstream: float | None = None
    used: Literal["vendored", "upstream"] = "vendored"
    if implementation in ("upstream", "auto") and upstream_available():
        statistic_upstream = _upstream_statistic(pooled[:n_a], pooled[n_a:], prompt_idx, n_a)
        if implementation == "upstream":
            used = "upstream"

    rng = np.random.default_rng(seed)
    labels_matrix = _permuted_labels(prompt_idx, labels, n_permutations, rng)
    null = _mmd_from_kernel(k, prompt_idx, labels_matrix)
    statistic = (
        statistic_upstream if used == "upstream" and statistic_upstream is not None else observed
    )
    extreme = int(np.sum(null >= statistic - 1e-12))
    p_value = min(1.0, (extreme + 1) / (n_permutations + 1))
    q05, q50, q95 = np.percentile(null, [5, 50, 95])
    return {
        "statistic": statistic,
        "statistic_upstream": statistic_upstream,
        "p_value": p_value,
        "null_quantiles": {"q05": float(q05), "q50": float(q50), "q95": float(q95)},
        "n_permutations": int(n_permutations),
        "n_prompts": int(prompt_idx.max()) + 1,
        "n_a": n_a,
        "n_b": int(labels.size - n_a),
        "implementation": used,
        "significant_at_05": p_value < 0.05,
    }
