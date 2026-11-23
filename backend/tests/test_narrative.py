"""The LLM narrative may restate table numbers but cannot create them."""

from __future__ import annotations

import pytest

from qr_llm_evals.analysis.narrative import (
    build_prompt,
    prompt_hash,
    render_narrative,
    verify_numbers,
)


def test_verified_narrative_passes() -> None:
    tables = {"primary": {"precision": 3, "effect": 0.125, "n": 24}}
    narrative = "The effect was 0.125 across 24 observations."
    assert verify_numbers(narrative, tables) == []


def test_invented_number_is_rejected() -> None:
    tables = {"primary": {"precision": 3, "effect": 0.125, "n": 24}}
    assert verify_numbers("The effect was 0.126 across 24 observations.", tables) == ["0.126"]


def test_withheld_message_lists_unverified_numbers() -> None:
    prompt = build_prompt({"primary": {"effect": 0.125}}, "# Frozen prereg")
    rendered = render_narrative(
        "Invented 99.",
        model="model-x",
        prompt_sha256=prompt_hash(prompt),
        offending_numbers=["99"],
    )
    assert "LLM-written summary (model model-x, prompt hash" in rendered
    assert "Narrative withheld: unverified numbers" in rendered
    assert "`99`" in rendered
    assert "Invented 99" not in rendered


_TABLES = {"primary": {"precision": 3, "effect": 0.125, "n": 24}}


@pytest.mark.parametrize(
    ("narrative", "offending"),
    [
        ("The effect was 0.999.", ["0.999"]),
        ("The effect was 0.999", ["0.999"]),
        ("The effect was 0.999, then nothing.", ["0.999"]),
        ("The effect was 0.999; then nothing.", ["0.999"]),
        ("The effect was 0.999: nothing.", ["0.999"]),
        ("The effect (0.999) was large.", ["0.999"]),
        ("The share was 99%.", ["99%"]),
        ("It ended at 0.999...", ["0.999"]),
        ("There were 25.\nNext line.", ["25"]),
        ("The effect was 0.999.\n", ["0.999"]),
        ("The effect was -1.5e-3.", ["-1.5e-3"]),
        ("A total of 1,000.", ["1,000"]),
    ],
)
def test_a_number_at_the_end_of_a_sentence_is_checked(narrative: str, offending: list[str]) -> None:
    assert verify_numbers(narrative, _TABLES) == offending


@pytest.mark.parametrize(
    "narrative",
    [
        "The effect was 0.125.",
        "The effect was 0.125, across 24.",
        "Across 24; effect 0.125: done (24).",
        "See study1.yaml and version 7.8.9 of the tool.",
        "1. The effect was 0.125.\n2. Across 24 observations.\n  10. Indented item.",
    ],
)
def test_what_the_gate_accepted_is_still_accepted(narrative: str) -> None:
    assert verify_numbers(narrative, _TABLES) == []


def test_a_list_marker_is_not_a_licence_for_the_rest_of_the_line() -> None:
    assert verify_numbers("1. The effect was 0.5.\n2. 77 samples.", _TABLES) == ["0.5", "77"]
    # Three digits, or a marker with nothing after it, is read as a number.
    assert verify_numbers("123. Something.", _TABLES) == ["123"]
    assert verify_numbers("Total:\n77.", _TABLES) == ["77"]
