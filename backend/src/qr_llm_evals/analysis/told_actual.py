"""Told × actual decomposition for told/actual cards (``docs/ANALYSIS_PLAN.md``).

The frozen plan says: *for told/actual cards report told-source priming first,
then actual and interaction; pair by card/prompt/epoch/told condition and use
prompt-clustered errors.* The executor used to print only that order as a
label. This module computes the three effects from the archived per-sample
score rows so a told/actual table carries numbers, not just headings:

* **told-source priming** — ``told_qrng − told_prng`` on the same arm, prompt
  and epoch, pooled over every arm of the source contrast. It is the
  manipulation check: it shows the self-report measure can move at all.
  ``told_qrng − told_nothing`` and ``told_prng − told_nothing`` are reported
  beside it.
* **actual-source main effect** — the quantum arm(s) minus the mean of the
  pseudo-random arms on the same prompt, epoch and told condition.
* **told × actual interaction** — ``(told_qrng − told_prng | quantum) −
  (told_qrng − told_prng | pseudo-random)`` per prompt and epoch; positive
  means the "told quantum" note moves reports more when it is true. It is the
  same contrast as "told the truth" minus "told a fiction".

Every test is :func:`~qr_llm_evals.analysis.stats.paired_permutation_test`
(sign flips by prompt, prompt-clustered SE). Missing cells are reported as
unavailable, never as zero effects. The module reads only rows handed to it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from qr_llm_evals.analysis.stats import paired_permutation_test

TOLD_QRNG = "told_qrng"
TOLD_PRNG = "told_prng"
TOLD_NOTHING = "told_nothing"
TOLD_CELLS: tuple[str, ...] = (TOLD_QRNG, TOLD_PRNG, TOLD_NOTHING)
EFFECT_ORDER: tuple[str, ...] = (
    "told_source_priming",
    "actual_source_main_effect",
    "told_by_actual_interaction",
)

#: ``(prompt_id, epoch, told)`` → value, for one arm.
CellValues = dict[tuple[str, int, str], float]


def parse_told_ref(sample_ref: str) -> tuple[str, int, str] | None:
    """``arm/card/prompt/epoch/told`` → ``(prompt, epoch, told)``; ``None`` if not a told ref."""
    parts = sample_ref.split("/")
    if len(parts) != 5 or parts[4] not in TOLD_CELLS:
        return None
    try:
        epoch = int(parts[3])
    except ValueError:
        return None
    return parts[2], epoch, parts[4]


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        number = float(value)
        return number if np.isfinite(number) else None
    return None


def cell_values(rows: Sequence[Mapping[str, Any]], endpoint: str) -> CellValues:
    """Numeric ``endpoint`` values of one arm keyed by ``(prompt, epoch, told)``."""
    out: CellValues = {}
    for row in rows:
        key = parse_told_ref(str(row.get("sample_ref", "")))
        value = _numeric(row.get(endpoint))
        if key is not None and value is not None:
            out[key] = value
    return out


def _test(
    left: list[float],
    right: list[float],
    prompts: list[str],
    *,
    comparison: str,
    n_permutations: int,
    seed: int,
    ci: float,
) -> dict[str, Any]:
    if len(left) < 2 or len(set(prompts)) < 2:
        return {
            "available": False,
            "comparison": comparison,
            "reason": "fewer than two complete pairs across at least two prompts",
            "n_pairs": len(left),
        }
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    result = paired_permutation_test(
        a, b, np.asarray(prompts), n_permutations=n_permutations, seed=seed, ci=ci
    )
    return {
        "available": True,
        "comparison": comparison,
        "effect": float(result["mean_diff"]),
        "mean_a": float(a.mean()),
        "mean_b": float(b.mean()),
        **result,
    }


def _within_arm_pairs(
    by_arm: Mapping[str, CellValues], arms: Sequence[str], told_a: str, told_b: str
) -> tuple[list[float], list[float], list[str]]:
    left: list[float] = []
    right: list[float] = []
    prompts: list[str] = []
    for arm in arms:
        values = by_arm.get(arm, {})
        keys = sorted({(p, e) for (p, e, t) in values if t == told_a})
        for prompt, epoch in keys:
            b = values.get((prompt, epoch, told_b))
            if b is None:
                continue
            left.append(values[(prompt, epoch, told_a)])
            right.append(b)
            prompts.append(prompt)
    return left, right, prompts


def _actual_pairs(
    by_arm: Mapping[str, CellValues], quantum: Sequence[str], prng: Sequence[str]
) -> tuple[list[float], list[float], list[str]]:
    sides = [by_arm.get(arm, {}) for arm in (*quantum, *prng)]
    if not quantum or not prng or not all(sides):
        return [], [], []
    keys = set.intersection(*(set(values) for values in sides))
    left: list[float] = []
    right: list[float] = []
    prompts: list[str] = []
    for key in sorted(keys):
        left.append(float(np.mean([by_arm[arm][key] for arm in quantum])))
        right.append(float(np.mean([by_arm[arm][key] for arm in prng])))
        prompts.append(key[0])
    return left, right, prompts


def _interaction_pairs(
    by_arm: Mapping[str, CellValues], quantum: Sequence[str], prng: Sequence[str]
) -> tuple[list[float], list[float], list[str]]:
    def told_gaps(arm: str) -> dict[tuple[str, int], float]:
        values = by_arm.get(arm, {})
        gaps: dict[tuple[str, int], float] = {}
        for prompt, epoch, told in values:
            if told != TOLD_QRNG:
                continue
            other = values.get((prompt, epoch, TOLD_PRNG))
            if other is not None:
                gaps[(prompt, epoch)] = values[(prompt, epoch, TOLD_QRNG)] - other
        return gaps

    if not quantum or not prng:
        return [], [], []
    gaps = {arm: told_gaps(arm) for arm in (*quantum, *prng)}
    if not all(gaps.values()):
        return [], [], []
    keys = set.intersection(*(set(g) for g in gaps.values()))
    left: list[float] = []
    right: list[float] = []
    prompts: list[str] = []
    for key in sorted(keys):
        left.append(float(np.mean([gaps[arm][key] for arm in quantum])))
        right.append(float(np.mean([gaps[arm][key] for arm in prng])))
        prompts.append(key[0])
    return left, right, prompts


def _cell_means(
    by_arm: Mapping[str, CellValues], quantum: Sequence[str], prng: Sequence[str]
) -> dict[str, dict[str, dict[str, float | int | None]]]:
    grid: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for told in TOLD_CELLS:
        row: dict[str, dict[str, float | int | None]] = {}
        for label, arms in (("actual_quantum", quantum), ("actual_pseudorandom", prng)):
            values = [
                value
                for arm in arms
                for (_, _, cell), value in by_arm.get(arm, {}).items()
                if cell == told
            ]
            row[label] = {
                "n": len(values),
                "mean": float(np.mean(values)) if values else None,
            }
        grid[told] = row
    return grid


def decompose(
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    endpoint: str,
    quantum_arms: Sequence[str],
    prng_arms: Sequence[str],
    *,
    n_permutations: int = 2000,
    seed: int = 42,
    ci: float = 0.95,
    cluster: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """The three told/actual effects (plus the 3 × 2 cell means) for one endpoint.

    Args:
        rows_by_arm: Archived score rows per arm (``sample_ref`` + the endpoint field).
        endpoint: The per-sample field to analyse (e.g. ``experience_claim_rate``).
        quantum_arms: The source contrast's QRNG side (``Contrast.a``).
        prng_arms: Its pooled seeded-PRNG side (``Contrast.b``).
        cluster: Maps a prompt id to its inference unit (default: the prompt id
            itself). Sign flips and clustered errors use these units, so prompt
            variants that share a base prompt can be flipped and clustered together.
    """
    arms = (*quantum_arms, *prng_arms)
    by_arm = {arm: cell_values(rows_by_arm.get(arm, []), endpoint) for arm in arms}

    def run(pairs: tuple[list[float], list[float], list[str]], comparison: str) -> dict[str, Any]:
        left, right, prompts = pairs
        if cluster is not None:
            prompts = [cluster(prompt) for prompt in prompts]
        return _test(
            left,
            right,
            prompts,
            comparison=comparison,
            n_permutations=n_permutations,
            seed=seed,
            ci=ci,
        )

    told = run(
        _within_arm_pairs(by_arm, arms, TOLD_QRNG, TOLD_PRNG),
        "told_qrng minus told_prng, same arm/prompt/epoch, pooled over all arms",
    )
    told_q_nothing = run(
        _within_arm_pairs(by_arm, arms, TOLD_QRNG, TOLD_NOTHING),
        "told_qrng minus told_nothing, same arm/prompt/epoch, pooled over all arms",
    )
    told_p_nothing = run(
        _within_arm_pairs(by_arm, arms, TOLD_PRNG, TOLD_NOTHING),
        "told_prng minus told_nothing, same arm/prompt/epoch, pooled over all arms",
    )
    actual = run(
        _actual_pairs(by_arm, quantum_arms, prng_arms),
        "quantum arm minus mean of pseudo-random arms, same prompt/epoch/told",
    )
    interaction = run(
        _interaction_pairs(by_arm, quantum_arms, prng_arms),
        "(told_qrng minus told_prng on the quantum arm) minus the same gap on the "
        "pseudo-random arms; equals told-the-truth minus told-a-fiction",
    )
    return {
        "endpoint": endpoint,
        "available": any(
            item.get("available") for item in (told, actual, interaction, told_q_nothing)
        ),
        "effect_order": list(EFFECT_ORDER),
        "quantum_arms": list(quantum_arms),
        "pseudorandom_arms": list(prng_arms),
        "cell_means": _cell_means(by_arm, quantum_arms, prng_arms),
        "told_source_priming": told,
        "told_qrng_vs_nothing": told_q_nothing,
        "told_prng_vs_nothing": told_p_nothing,
        "actual_source_main_effect": actual,
        "told_by_actual_interaction": interaction,
    }


__all__ = [
    "EFFECT_ORDER",
    "TOLD_CELLS",
    "cell_values",
    "decompose",
    "parse_told_ref",
]
