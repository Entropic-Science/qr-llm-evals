"""Seed derivation (spec §5.2): fixed vectors, range ``[0, 2**63)``, distinct
across prompts / epochs / study seeds, stable across platforms (pure SHA-256
over a fixed UTF-8 string — the vectors below are the cross-platform pin).
"""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qr_llm_evals.models.schema import DEFAULT_SEEDS, SEED_MAX_EXCLUSIVE
from qr_llm_evals.models.seeds import SEPARATOR, derive_seed, pairing_key

#: (study_seed, card_id, prompt_id, epoch) → qr_seed. Changing these changes
#: every seeded arm's outputs; bump the prereg schema version if you must.
VECTORS: list[tuple[tuple[int, str, str, int], int]] = [
    ((1, "creative_diversity", "p0001", 0), 2558847153261501190),
    ((7, "creative_diversity", "p0001", 0), 4798978718871851284),
    ((42, "creative_diversity", "p0001", 0), 422598750703451419),
    ((1, "creative_diversity", "p0002", 0), 5236294790716903019),
    ((1, "creative_diversity", "p0001", 1), 7503955311267017560),
    ((0, "a", "b", 0), 6720691090591114774),
]


@pytest.mark.parametrize(("args", "expected"), VECTORS)
def test_fixed_vectors(args: tuple[int, str, str, int], expected: int) -> None:
    assert derive_seed(*args) == expected


def test_matches_spec_reference_implementation() -> None:
    """The spec's literal formula (§2.2), re-derived here so the module cannot drift."""
    study_seed, card_id, prompt_id, epoch = 42, "novelty_bench", "q17", 3
    digest = hashlib.sha256(f"{study_seed}|{card_id}|{prompt_id}|{epoch}".encode()).digest()
    assert (
        derive_seed(study_seed, card_id, prompt_id, epoch) == int.from_bytes(digest[:8], "big") >> 1
    )


def test_pairing_key_is_the_hashed_string() -> None:
    assert SEPARATOR == "|"
    assert pairing_key(7, "card", "p1", 2) == "7|card|p1|2"


@given(
    study_seed=st.integers(min_value=0, max_value=SEED_MAX_EXCLUSIVE - 1),
    card_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=32),
    prompt_id=st.text(min_size=1, max_size=32).filter(lambda s: SEPARATOR not in s),
    epoch=st.integers(min_value=0, max_value=10_000),
)
def test_range_and_purity(study_seed: int, card_id: str, prompt_id: str, epoch: int) -> None:
    seed = derive_seed(study_seed, card_id, prompt_id, epoch)
    assert 0 <= seed < SEED_MAX_EXCLUSIVE
    assert seed == derive_seed(study_seed, card_id, prompt_id, epoch)


def test_distinct_across_prompts_epochs_and_study_seeds() -> None:
    seeds = {
        derive_seed(s, "creative_diversity", f"p{i:04d}", e)
        for s in DEFAULT_SEEDS
        for i in range(200)
        for e in range(3)
    }
    assert len(seeds) == len(DEFAULT_SEEDS) * 200 * 3


def test_study_seeds_are_one_seven_forty_two() -> None:
    assert DEFAULT_SEEDS == (1, 7, 42)


@pytest.mark.parametrize(
    "bad",
    [
        (-1, "c", "p", 0),
        (SEED_MAX_EXCLUSIVE, "c", "p", 0),
        (1, "c", "p", -1),
        (1, "", "p", 0),
        (1, "c", "", 0),
        (1, "c", "a|b", 0),
    ],
)
def test_rejects_bad_inputs(bad: tuple[int, str, str, int]) -> None:
    with pytest.raises(ValueError):
        derive_seed(*bad)
