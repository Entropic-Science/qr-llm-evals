"""Source-guessing chance test, outside observer and privileged access (H3)."""

from __future__ import annotations

import random
from typing import Any

import pytest

from qr_llm_evals.analysis.source_detection import (
    chance_test,
    guess_rows,
    outside_observer,
    privileged_access,
)
from qr_llm_evals.tier0.schema import Tier0Record

ARMS = ("qrng", "prng_seed_1", "prng_seed_7", "prng_seed_42")


def _guesses(
    accuracy: float, *, completions: int = 120, always: str | None = None, seed: int = 3
) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    out: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    for c in range(completions):
        source = "qrng" if c % 2 == 0 else "prng"
        ref = f"{'qrng' if source == 'qrng' else 'prng_seed_1'}/self_referential_probe/p{c}/0/told_nothing"
        for variant in ("self_judgement", "in_context"):
            for arm in ARMS:
                for epoch in range(3):
                    if always is not None:
                        guess = always
                    else:
                        right = rng.random() < accuracy
                        guess = source if right else ("prng" if source == "qrng" else "qrng")
                    out[arm].append(
                        {
                            "sample_ref": f"{arm}/source_guessing_self_report/g{c}_{variant}/{epoch}",
                            "guess": guess,
                            "source": source,
                            "variant": variant,
                            "source_sample_ref": ref,
                        }
                    )
    return out


def test_guess_rows_skip_unparsed_and_unlabelled() -> None:
    rows = {"qrng": [{"sample_ref": "q/c/g/0", "guess": None, "source": "qrng"}]}
    assert guess_rows(rows) == []


def test_chance_level_guessing_is_not_detection() -> None:
    result = chance_test(_guesses(0.5), quantum_arms=["qrng"], n_permutations=1000, seed=42)
    assert result["available"]
    assert result["balanced_accuracy"] == pytest.approx(0.5, abs=0.05)
    assert result["p_value"] > 0.05
    assert result["n_completions"] == 120
    assert set(result["by_variant"]) == {"self_judgement", "in_context"}
    assert set(result["by_guesser_source"]) == {
        "guesses_sampled_by_qrng",
        "guesses_sampled_by_prng",
    }


def test_response_bias_scores_exactly_half() -> None:
    result = chance_test(_guesses(0.0, always="qrng"), n_permutations=500, seed=42)
    assert result["balanced_accuracy"] == pytest.approx(0.5)
    assert result["raw_accuracy"] == pytest.approx(0.5)
    assert result["p_value"] == pytest.approx(1.0)


def test_informative_guesser_is_detected() -> None:
    result = chance_test(_guesses(0.65), n_permutations=1000, seed=42)
    assert result["balanced_accuracy"] > 0.6
    assert result["p_value"] < 0.01


def _records(
    ref: str, mean_logprob: float, n: int = 12, rng: random.Random | None = None
) -> list[Tier0Record]:
    """Tokens whose nuisance features (rank, entropy, ids) are random, never label-linked."""
    draw = rng or random.Random(ref)
    return [
        Tier0Record(
            sample_ref=ref,
            token_index=i,
            token_id=draw.randrange(1000),
            logprob=mean_logprob,
            rank_raw=draw.randrange(3),
            entropy_raw_top20=0.5 + 0.1 * draw.random(),
            timestamp_ns=0,
        )
        for i in range(n)
    ]


def test_outside_observer_finds_a_real_signal_and_misses_noise() -> None:
    rng = random.Random(5)
    labels: dict[str, str] = {}
    signal: dict[str, list[Tier0Record]] = {}
    noise: dict[str, list[Tier0Record]] = {}
    for c in range(80):
        ref = f"arm/self_referential_probe/p{c}/0/told_nothing"
        source = "qrng" if c % 2 == 0 else "prng"
        labels[ref] = source
        shift = -0.5 if source == "qrng" else -1.5
        signal[ref] = _records(ref, shift + rng.gauss(0, 0.2), n=rng.randint(10, 16), rng=rng)
        noise[ref] = _records(ref, -1.0 + rng.gauss(0, 0.2), n=rng.randint(10, 16), rng=rng)
    found, correct = outside_observer(signal, labels, n_permutations=100, seed=42)
    assert found["available"] and found["balanced_accuracy"] > 0.9
    assert found["p_value"] < 0.05 and "mean_logprob" in found["features"]
    assert set(correct) == set(labels)
    missed, _ = outside_observer(noise, labels, n_permutations=100, seed=42)
    assert missed["available"] and abs(missed["balanced_accuracy"] - 0.5) < 0.2


def test_outside_observer_needs_enough_completions() -> None:
    labels = {"a/c/p1/0/told_nothing": "qrng", "a/c/p2/0/told_nothing": "prng"}
    records = {ref: _records(ref, -1.0) for ref in labels}
    table, correct = outside_observer(records, labels, n_permutations=10)
    assert table["available"] is False and correct == {}


def test_privileged_access_is_in_context_minus_observer() -> None:
    rows = _guesses(1.0, completions=40)
    refs = {r["source_sample_ref"] for arm in rows.values() for r in arm}
    observer = {ref: 0 for ref in refs}
    result = privileged_access(rows, observer, n_resamples=500, seed=42)
    assert result["available"]
    assert result["effect"] == pytest.approx(1.0)
    assert result["guess_balanced_accuracy"] == pytest.approx(1.0)
    assert result["observer_balanced_accuracy"] == pytest.approx(0.0)
    assert result["n_completions"] == 40
    unavailable = privileged_access(rows, {}, n_resamples=10)
    assert unavailable["available"] is False


def test_privileged_access_ignores_a_majority_class_observer() -> None:
    rows = _guesses(0.5, completions=40)
    # Observer "always PRNG": right on every PRNG completion, wrong on every QRNG one.
    observer = {
        r["source_sample_ref"]: int(r["source"] == "prng") for arm in rows.values() for r in arm
    }
    result = privileged_access(rows, observer, n_resamples=500, seed=42)
    assert result["observer_balanced_accuracy"] == pytest.approx(0.5)
    assert abs(result["effect"]) < 0.1
