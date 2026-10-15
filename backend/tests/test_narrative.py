"""The LLM narrative may restate table numbers but cannot create them."""

from __future__ import annotations

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
