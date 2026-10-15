"""Shared Python/TypeScript estimate vectors (spec §5.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qr_llm_evals.cards import CardOverrides, EstimateAssumptions, estimate_selection, load_cards

FIXTURE = Path(__file__).parents[2] / "packages" / "contracts" / "fixtures" / "estimates.json"


def test_estimate_fixtures_match_backend() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    registry = load_cards()
    for case in payload["cases"]:
        assumptions = EstimateAssumptions.model_validate(case["assumptions"])
        overrides = {
            card_id: CardOverrides.model_validate(values)
            for card_id, values in case["overrides"].items()
        }
        result = estimate_selection(
            [registry[card_id] for card_id in case["card_ids"]],
            case["model_id"],
            assumptions,
            overrides,
        )
        expected = case["expected"]
        assert result.tokens_per_arm == expected["tokens_per_arm"]
        assert result.time_unsupervised_h == pytest.approx(expected["time_unsupervised_h"])
        assert result.observer_hours_single == pytest.approx(expected["observer_hours_single"])
        assert result.observer_hours_tiled == pytest.approx(expected["observer_hours_tiled"])
        assert result.sessions_single == expected["sessions_single"]
        assert result.sessions_tiled == expected["sessions_tiled"]
        assert {row.card_id: row.tokens_per_arm for row in result.per_card} == expected[
            "per_card_tokens"
        ]
        assert "formula" in result.assumptions
