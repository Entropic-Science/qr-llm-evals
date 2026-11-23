"""The MMD permutation loop runs in batches and returns what it returned before.

``analysis.mmd`` used to build one ``permutations x pooled sequences`` label
matrix and reduce it prompt by prompt. That matrix is what made a designed
study at 10 000 permutations need gigabytes. The loop now draws and reduces
one prompt at a time, in row batches where a prompt is large.

``_reference_*`` below is the implementation as it was before that change,
copied here so that it cannot move with the module. Every test compares the
module against it bit for bit: the registered seed must keep giving the
registered numbers, for the studies already analysed and for designed ones.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from qr_llm_evals.analysis import mmd

IntArray = np.ndarray[Any, np.dtype[np.int64]]
FloatArray = np.ndarray[Any, np.dtype[np.float64]]


# ── the implementation before batching, verbatim ─────────────────────────────


def _reference_pair_counts(
    prompt_idx: IntArray, labels_matrix: np.ndarray[Any, Any]
) -> tuple[FloatArray, FloatArray, FloatArray]:
    n_prompts = int(prompt_idx.max()) + 1
    onehot = np.zeros((prompt_idx.size, n_prompts), dtype=np.float64)
    onehot[np.arange(prompt_idx.size), prompt_idx] = 1.0
    n_a = labels_matrix.astype(np.float64) @ onehot
    n_g = onehot.sum(axis=0)
    n_b = n_g[None, :] - n_a
    n_xx = np.sum(n_a * (n_a - 1), axis=1)
    n_yy = np.sum(n_b * (n_b - 1), axis=1)
    n_xy = np.sum(n_a * n_b, axis=1)
    return n_xx, n_xy, n_yy


def _reference_blockwise(
    pooled: IntArray, prompt_idx: IntArray, labels_matrix: np.ndarray[Any, Any]
) -> FloatArray:
    length = float(pooled.shape[1])
    b = labels_matrix.shape[0]
    s_xx = np.zeros(b)
    s_xy = np.zeros(b)
    total = 0.0
    for g in np.unique(prompt_idx):
        members = np.flatnonzero(prompt_idx == g)
        kg = mmd.hamming_kernel(pooled[members], pooled[members]) / length
        np.fill_diagonal(kg, 0.0)
        zg = labels_matrix[:, members].astype(np.float64)
        kz = zg @ kg
        s_xx += np.sum(kz * zg, axis=1)
        s_xy += np.sum(kz * (1.0 - zg), axis=1)
        total += float(kg.sum())
    s_yy = total - s_xx - 2.0 * s_xy
    n_xx, n_xy, n_yy = _reference_pair_counts(prompt_idx, labels_matrix)
    if np.any(n_xx == 0) or np.any(n_yy == 0) or np.any(n_xy == 0):
        raise ValueError("every prompt needs at least two samples per arm for the MMD estimator")
    result: FloatArray = s_xx / n_xx - 2.0 * s_xy / n_xy + s_yy / n_yy
    return result


def _reference_permuted_labels(
    prompt_idx: IntArray, labels: IntArray, n_permutations: int, rng: np.random.Generator
) -> np.ndarray[Any, np.dtype[np.int8]]:
    out = np.empty((n_permutations, labels.size), dtype=np.int8)
    for g in np.unique(prompt_idx):
        members = np.flatnonzero(prompt_idx == g)
        block = np.tile(labels[members], (n_permutations, 1))
        perm = rng.permuted(block, axis=1)
        out[:, members] = perm
    return out


def _reference_null(
    pooled: IntArray, prompt_idx: IntArray, labels: IntArray, n_permutations: int, seed: int
) -> FloatArray:
    rng = np.random.default_rng(seed)
    labels_matrix = _reference_permuted_labels(prompt_idx, labels, n_permutations, rng)
    return _reference_blockwise(pooled, prompt_idx, labels_matrix)


def _reference_test(
    seqs_a: Any, seqs_b: Any, prompts_a: Any, prompts_b: Any, n_permutations: int, seed: int
) -> dict[str, Any]:
    pooled, prompt_idx, labels, dropped = mmd._prepare(seqs_a, seqs_b, prompts_a, prompts_b)
    n_a = int(labels.sum())
    statistic = float(_reference_blockwise(pooled, prompt_idx, labels[None, :])[0])
    null = _reference_null(pooled, prompt_idx, labels, n_permutations, seed)
    extreme = int(np.sum(null >= statistic - 1e-12))
    p_value = min(1.0, (extreme + 1) / (n_permutations + 1))
    q05, q50, q95 = np.percentile(null, [5, 50, 95])
    return {
        "statistic": statistic,
        "statistic_upstream": None,
        "p_value": p_value,
        "null_quantiles": {"q05": float(q05), "q50": float(q50), "q95": float(q95)},
        "n_permutations": int(n_permutations),
        "n_prompts": int(prompt_idx.max()) + 1,
        "n_prompts_dropped": dropped,
        "n_a": n_a,
        "n_b": int(labels.size - n_a),
        "implementation": "vendored",
        "significant_at_05": p_value < 0.05,
    }


# ── fixed inputs ─────────────────────────────────────────────────────────────

Inputs = tuple[IntArray, IntArray, list[Any], list[Any]]


def _arm(
    rng: np.random.Generator, counts: dict[Any, int], length: int, vocabulary: int
) -> tuple[IntArray, list[Any]]:
    """Token rows of varying true length, right-padded, with their prompt ids."""
    rows: list[IntArray] = []
    prompts: list[Any] = []
    for prompt, count in counts.items():
        for _ in range(count):
            row = rng.integers(0, vocabulary, size=length, dtype=np.int64)
            row[int(rng.integers(1, length + 1)) :] = mmd.PAD_TOKEN
            rows.append(row)
            prompts.append(prompt)
    return np.vstack(rows), prompts


def _balanced() -> Inputs:
    """Six prompts, eight samples per arm each: the shape of one eval at fixed k."""
    rng = np.random.default_rng(101)
    counts = {prompt: 8 for prompt in range(6)}
    a, pa = _arm(rng, counts, length=12, vocabulary=4)
    b, pb = _arm(rng, counts, length=12, vocabulary=4)
    return a, b, pa, pb


def _pooled_control() -> Inputs:
    """One arm against three pooled ones, as the quantum-vs-control contrast is."""
    rng = np.random.default_rng(202)
    a, pa = _arm(rng, {f"card/p{i}": 5 for i in range(9)}, length=20, vocabulary=3)
    b, pb = _arm(rng, {f"card/p{i}": 15 for i in range(9)}, length=20, vocabulary=3)
    return a, b, pa, pb


def _ragged() -> Inputs:
    """Prompts of different sizes, two of which the estimator leaves out."""
    rng = np.random.default_rng(303)
    a, pa = _arm(
        rng, {"x/1": 2, "x/2": 9, "x/3": 4, "y/1": 1, "y/2": 3, "z": 6}, length=9, vocabulary=5
    )
    b, pb = _arm(
        rng, {"x/1": 7, "x/2": 2, "x/3": 4, "y/1": 5, "y/2": 3, "only_b": 4}, length=9, vocabulary=5
    )
    return a, b, pa, pb


CASES = {"balanced": _balanced, "pooled_control": _pooled_control, "ragged": _ragged}


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize(("n_permutations", "seed"), [(1, 0), (7, 42), (500, 42), (2000, 7)])
def test_the_test_returns_what_it_returned_before_batching(
    case: str, n_permutations: int, seed: int
) -> None:
    a, b, pa, pb = CASES[case]()
    result = mmd.mmd_hamming_permutation_test(
        a, b, pa, pb, n_permutations=n_permutations, seed=seed, implementation="vendored"
    )
    assert result == _reference_test(a, b, pa, pb, n_permutations, seed)


# ── the batched loop itself ──────────────────────────────────────────────────


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("batch_cells", [1, 5, 64, 1000, mmd.BATCH_CELLS])
def test_null_is_the_same_bits_at_any_batch_size(case: str, batch_cells: int) -> None:
    a, b, pa, pb = CASES[case]()
    pooled, prompt_idx, labels, _ = mmd._prepare(a, b, pa, pb)
    observed, null = mmd._observed_and_null(
        pooled, prompt_idx, labels, 300, np.random.default_rng(9), batch_cells
    )
    assert observed == float(_reference_blockwise(pooled, prompt_idx, labels[None, :])[0])
    reference = _reference_null(pooled, prompt_idx, labels, 300, 9)
    # Bytes, not values: -0.0 and 0.0 compare equal and must not pass as identical.
    assert null.tobytes() == reference.tobytes()


def test_batched_draws_are_the_whole_label_matrix() -> None:
    """Drawing a prompt's relabellings in row batches continues the same stream."""
    labels = np.array([1, 0, 0, 1, 1, 0, 0, 0, 1], dtype=np.int64)
    whole = np.random.default_rng(3).permuted(np.tile(labels, (50, 1)), axis=1)
    rng = np.random.default_rng(3)
    parts = [
        rng.permuted(np.tile(labels, (stop - start, 1)), axis=1)
        for start, stop in mmd._row_batches(50, labels.size, batch_cells=7 * labels.size)
    ]
    assert len(parts) == 8
    assert np.array_equal(np.vstack(parts), whole)


def test_row_batches_cover_every_row_once() -> None:
    assert mmd._row_batches(10, 4, batch_cells=12) == [(0, 3), (3, 6), (6, 9), (9, 10)]
    assert mmd._row_batches(10, 4, batch_cells=10**9) == [(0, 10)]
    # A prompt wider than the budget still gets one row at a time.
    assert mmd._row_batches(3, 100, batch_cells=5) == [(0, 1), (1, 2), (2, 3)]


def test_prompt_members_are_the_per_prompt_scans() -> None:
    prompt_idx = np.array([2, 0, 1, 2, 2, 0, 3, 1, 0], dtype=np.int64)
    members = mmd._prompt_members(prompt_idx)
    expected = [np.flatnonzero(prompt_idx == g) for g in np.unique(prompt_idx)]
    assert len(members) == len(expected)
    for found, wanted in zip(members, expected, strict=True):
        assert found.tolist() == wanted.tolist()


def test_pair_counts_are_the_one_hot_counts() -> None:
    rng = np.random.default_rng(12)
    prompt_idx = np.repeat(np.arange(5), [4, 6, 2, 9, 3]).astype(np.int64)
    labels_matrix = (rng.random((40, prompt_idx.size)) < 0.4).astype(np.int8)
    for found, wanted in zip(
        mmd._pair_counts(prompt_idx, labels_matrix),
        _reference_pair_counts(prompt_idx, labels_matrix),
        strict=True,
    ):
        assert found.tobytes() == wanted.tobytes()


def test_the_loop_does_not_hold_permutations_times_pooled_sequences() -> None:
    """Peak memory follows the largest prompt, not the number of prompts."""
    import tracemalloc

    n_permutations = 2000
    rng = np.random.default_rng(77)
    a, pa = _arm(rng, {prompt: 4 for prompt in range(600)}, length=8, vocabulary=3)
    b, pb = _arm(rng, {prompt: 12 for prompt in range(600)}, length=8, vocabulary=3)
    pooled_sequences = a.shape[0] + b.shape[0]
    # One byte per label was the smallest the whole matrix ever was (19 MB here);
    # the loop also held it as float64 for the pair counts (154 MB).
    label_matrix_bytes = n_permutations * pooled_sequences

    tracemalloc.start()
    try:
        result = mmd.mmd_hamming_permutation_test(
            a, b, pa, pb, n_permutations=n_permutations, seed=42, implementation="vendored"
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result["n_prompts"] == 600 and result["n_a"] == 2400 and result["n_b"] == 7200
    assert peak < label_matrix_bytes / 4


def test_prompt_kernel_counts_the_columns_it_does_not_compare() -> None:
    """Short replies padded to a long study maximum: the kernel is the full comparison's."""
    rng = np.random.default_rng(21)
    for used, length in [(0, 6), (1, 40), (5, 5), (7, 300), (12, 13)]:
        rows = np.full((9, length), mmd.PAD_TOKEN, dtype=np.int64)
        rows[:, :used] = rng.integers(0, 3, size=(9, used))
        # A column in the padding where every row agrees on a token is counted too.
        if used + 2 < length:
            rows[:, used + 1] = 7
        full = mmd.hamming_kernel(rows, rows) / float(length)
        np.fill_diagonal(full, 0.0)
        assert mmd._prompt_kernel(rows).tobytes() == full.tobytes()


def test_padding_to_a_longer_study_changes_nothing_but_the_scale() -> None:
    """The batched test on heavily padded input is still the reference test."""
    a, b, pa, pb = _pooled_control()
    wide_a = np.pad(a, ((0, 0), (0, 400)), constant_values=mmd.PAD_TOKEN)
    wide_b = np.pad(b, ((0, 0), (0, 400)), constant_values=mmd.PAD_TOKEN)
    result = mmd.mmd_hamming_permutation_test(
        wide_a, wide_b, pa, pb, n_permutations=400, seed=42, implementation="vendored"
    )
    assert result == _reference_test(wide_a, wide_b, pa, pb, 400, 42)
