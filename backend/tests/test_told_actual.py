"""Told × actual decomposition (``analysis/told_actual.py``) on synthetic score rows."""

from __future__ import annotations

import random
from typing import Any

import pytest

from qr_llm_evals.analysis.told_actual import EFFECT_ORDER, decompose, parse_told_ref

QUANTUM = ("qrng",)
PRNG = ("prng_seed_1", "prng_seed_7", "prng_seed_42")
PROMPTS = [f"p{i:02d}" for i in range(12)]


def _rows(
    *,
    told_effect: float,
    actual_effect: float = 0.0,
    truth_bonus: float = 0.0,
    epochs: int = 20,
    seed: int = 7,
) -> dict[str, list[dict[str, Any]]]:
    """Binary claims: base 0.3, +told_effect when told quantum, +actual_effect on QRNG,
    +truth_bonus when the told note matches the arm (the interaction)."""
    rng = random.Random(seed)
    out: dict[str, list[dict[str, Any]]] = {}
    for arm in (*QUANTUM, *PRNG):
        rows: list[dict[str, Any]] = []
        quantum = arm in QUANTUM
        for prompt in PROMPTS:
            for epoch in range(epochs):
                for told in ("told_qrng", "told_prng", "told_nothing"):
                    p = 0.3
                    p += told_effect if told == "told_qrng" else 0.0
                    p += actual_effect if quantum else 0.0
                    truthful = (told == "told_qrng") == quantum and told != "told_nothing"
                    p += truth_bonus if truthful else 0.0
                    rows.append(
                        {
                            "sample_ref": f"{arm}/self_referential_probe/{prompt}/{epoch}/{told}",
                            "judge_score": float(rng.random() < p),
                        }
                    )
        out[arm] = rows
    return out


def test_parse_told_ref() -> None:
    assert parse_told_ref("qrng/card/p1/3/told_prng") == ("p1", 3, "told_prng")
    assert parse_told_ref("qrng/card/p1/3") is None
    assert parse_told_ref("qrng/card/p1/x/told_prng") is None
    assert parse_told_ref("qrng/card/p1/3/told_maybe") is None


def test_large_told_effect_and_no_actual_effect() -> None:
    result = decompose(
        _rows(told_effect=0.3), "judge_score", QUANTUM, PRNG, n_permutations=2000, seed=42
    )
    assert result["available"] and result["effect_order"] == list(EFFECT_ORDER)
    told = result["told_source_priming"]
    actual = result["actual_source_main_effect"]
    assert told["available"] and told["effect"] == pytest.approx(0.3, abs=0.06)
    assert told["p_value"] < 0.01
    assert told["n_prompts"] == len(PROMPTS)  # clustered by prompt, not by epoch
    assert actual["available"] and abs(actual["effect"]) < 0.06
    assert actual["p_value"] > 0.01
    assert result["told_qrng_vs_nothing"]["effect"] == pytest.approx(0.3, abs=0.06)
    assert abs(result["told_prng_vs_nothing"]["effect"]) < 0.06
    grid = result["cell_means"]
    assert grid["told_qrng"]["actual_quantum"]["n"] == len(PROMPTS) * 20
    assert grid["told_qrng"]["actual_pseudorandom"]["n"] == 3 * len(PROMPTS) * 20


def test_actual_effect_is_detected_when_present() -> None:
    result = decompose(
        _rows(told_effect=0.2, actual_effect=0.15), "judge_score", QUANTUM, PRNG, seed=42
    )
    actual = result["actual_source_main_effect"]
    assert actual["effect"] == pytest.approx(0.15, abs=0.06)
    assert actual["p_value"] < 0.01


def test_interaction_is_truth_minus_fiction() -> None:
    result = decompose(
        _rows(told_effect=0.1, truth_bonus=0.2, epochs=30),
        "judge_score",
        QUANTUM,
        PRNG,
        seed=42,
    )
    interaction = result["told_by_actual_interaction"]
    # quantum arm: told_qrng truthful (+0.2); PRNG arms: told_prng truthful (+0.2)
    # => (0.1+0.2 - 0) - (0.1 - 0.2) = 0.4
    assert interaction["effect"] == pytest.approx(0.4, abs=0.08)
    assert interaction["p_value"] < 0.01


def test_missing_cells_are_unavailable_not_zero() -> None:
    rows = _rows(told_effect=0.3)
    rows["qrng"] = [r for r in rows["qrng"] if not r["sample_ref"].endswith("told_prng")]
    result = decompose(rows, "judge_score", QUANTUM, PRNG, seed=42)
    assert result["told_by_actual_interaction"]["available"] is False
    assert "reason" in result["told_by_actual_interaction"]
    empty = decompose({}, "judge_score", QUANTUM, PRNG)
    assert empty["available"] is False
    assert empty["actual_source_main_effect"]["available"] is False
