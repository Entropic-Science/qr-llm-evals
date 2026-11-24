"""The token limit of an eval: the rule, a study's override and where it is applied."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import run_check_helpers as h
from qr_llm_evals.cards import design, load_cards
from qr_llm_evals.cards.estimates import CardOverrides as EstimateOverrides
from qr_llm_evals.cards.estimates import tokens_per_arm
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.cards.token_limits import (
    SAFE_FACTOR,
    SAFE_FLOOR,
    TOKEN_LIMIT_MAX,
    effective_max_tokens,
    token_limit,
    token_limits_export,
)
from qr_llm_evals.cards.validate import CardAdmissionError, validate_card
from qr_llm_evals.models.schema import (
    MAX_TOKENS_CEILING,
    CardOverrides,
    StudyConfig,
    canonical_json,
    config_hash,
)
from qr_llm_evals.prereg import load_renderer
from stub_engine import StubEngine

CONTRACTS = REPO_ROOT / "packages/contracts"


def _study(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONTRACTS / "fixtures" / name).read_text(encoding="utf-8"))
    return data


def _with_limit(data: dict[str, Any], card_id: str, limit: int) -> dict[str, Any]:
    selection = next(s for s in data["cards"] if s["card_id"] == card_id)
    selection.setdefault("overrides", {})["max_tokens"] = limit
    return data


# ── the rule ─────────────────────────────────────────────────────────────────


def test_the_two_layer_0_modules_hold_the_same_ceiling() -> None:
    assert MAX_TOKENS_CEILING == TOKEN_LIMIT_MAX


def test_every_generating_card_has_limits_and_no_other_card_does() -> None:
    cards = load_cards()
    for card in cards.values():
        limit = token_limit(card)
        if not card.generates:
            assert limit is None, card.id
            continue
        assert limit is not None, card.id
        assert limit.standard == card.max_tokens
        assert limit.min == card.est_tokens_per_sample
        assert limit.max == TOKEN_LIMIT_MAX
        assert limit.safe == min(TOKEN_LIMIT_MAX, max(SAFE_FACTOR * card.max_tokens, SAFE_FLOOR))
        assert limit.min <= limit.standard <= limit.safe <= limit.max, card.id


def test_the_safe_limits_of_the_registry() -> None:
    cards = load_cards()
    safe = {card_id: limit["safe"] for card_id, limit in token_limits_export(cards).items()}
    # Four times the card's own, never under 256 and never over the ceiling.
    assert safe["livecodebench"] == 40_000
    assert safe["gpqa_diamond"] == 32_000
    assert safe["aime2026"] == 64_000
    assert safe["sentience_statements"] == 256
    assert safe["darkbench"] == 2_400
    assert "tier0_pit_residuals" not in safe


def test_the_exported_limits_are_the_committed_file() -> None:
    committed = json.loads(
        (CONTRACTS / "src/generated/token-limits.json").read_text(encoding="utf-8")
    )
    assert committed == token_limits_export(load_cards())


def test_a_card_may_not_ask_for_more_than_the_ceiling() -> None:
    card = load_cards()["aime2026"].model_copy(update={"max_tokens": TOKEN_LIMIT_MAX + 1})
    with pytest.raises(CardAdmissionError, match="max_tokens: must not exceed 64000"):
        validate_card(card)


def test_an_override_is_held_to_the_card() -> None:
    cards = load_cards()
    card = cards["livecodebench"]
    assert effective_max_tokens(card) == 10_000
    assert effective_max_tokens(card, 40_000) == 40_000
    assert effective_max_tokens(card, card.est_tokens_per_sample) == card.est_tokens_per_sample
    with pytest.raises(ValueError, match=r"max_tokens=3999 outside \[4000, 64000\]"):
        effective_max_tokens(card, card.est_tokens_per_sample - 1)
    with pytest.raises(ValueError, match="outside"):
        effective_max_tokens(card, TOKEN_LIMIT_MAX + 1)
    with pytest.raises(ValueError, match="generates nothing"):
        effective_max_tokens(cards["tier0_pit_residuals"], 100)


# ── the study ────────────────────────────────────────────────────────────────


def test_a_study_that_sets_no_limit_keeps_its_bytes() -> None:
    assert CardOverrides().model_dump(mode="json") == {"k": None, "items": None}
    assert CardOverrides(max_tokens=None).model_dump(mode="json") == {"k": None, "items": None}
    assert CardOverrides(max_tokens=500).model_dump(mode="json") == {
        "k": None,
        "items": None,
        "max_tokens": 500,
    }
    data = _study("study1.json")
    study = StudyConfig.model_validate(data)
    assert "max_tokens" not in json.loads(canonical_json(study))["cards"][0]["overrides"]
    # A null in a file is read as no limit and is not written back.
    data["cards"][3]["overrides"]["max_tokens"] = None
    assert config_hash(StudyConfig.model_validate(data)) == config_hash(study)


def test_a_limit_is_part_of_the_study_hash_and_survives_a_round_trip() -> None:
    plain = StudyConfig.model_validate(_study("study1.json"))
    limited = StudyConfig.model_validate(
        _with_limit(_study("study1.json"), "self_referential_probe", 3_200)
    )
    assert config_hash(limited) != config_hash(plain)
    again = StudyConfig.model_validate_json(limited.model_dump_json())
    assert again == limited and config_hash(again) == config_hash(limited)
    selection = next(s for s in again.cards if s.card_id == "self_referential_probe")
    assert selection.overrides.max_tokens == 3_200


@pytest.mark.parametrize("value", [0, -5, TOKEN_LIMIT_MAX + 1, 1.5, "many"])
def test_a_malformed_limit_is_refused_at_parse_time(value: Any) -> None:
    with pytest.raises(ValidationError):
        StudyConfig.model_validate(_with_limit(_study("study1.json"), "novelty_bench", value))


def test_the_plan_refuses_a_limit_the_card_cannot_run_with() -> None:
    cards = load_cards()
    low = StudyConfig.model_validate(_with_limit(_study("study1.json"), "novelty_bench", 5))
    with pytest.raises(ValueError, match="novelty_bench: max_tokens=5 outside"):
        StatisticalPlan.from_config(low, cards)
    stray = StudyConfig.model_validate(_with_limit(_study("study1.json"), "tier0_mmd_hamming", 64))
    with pytest.raises(ValueError, match="generates nothing"):
        StatisticalPlan.from_config(stray, cards)
    designed = StudyConfig.model_validate(
        _with_limit(_study("study1.designed.json"), "novelty_bench", 5)
    )
    with pytest.raises(ValueError, match="novelty_bench: max_tokens=5 outside"):
        StatisticalPlan.from_config(designed, cards)


def test_a_limit_is_registered_with_the_plan_and_leaves_the_estimate_alone() -> None:
    cards = load_cards()
    for name in ("study1.json", "study1.designed.json"):
        plain = StatisticalPlan.from_config(StudyConfig.model_validate(_study(name)), cards)
        limited = StatisticalPlan.from_config(
            StudyConfig.model_validate(_with_limit(_study(name), "novelty_bench", 2_400)), cards
        )
        # The plan names the study it was built from, and nothing else of it moves.
        before, after = json.loads(plain.canonical_json()), json.loads(limited.canonical_json())
        assert [key for key in before if before[key] != after[key]] == ["config_hash"]
    card = cards["novelty_bench"]
    assert tokens_per_arm(card, EstimateOverrides(max_tokens=2_400)) == tokens_per_arm(card)


def test_the_problems_list_names_a_limit_out_of_bounds() -> None:
    cards = load_cards()
    data = _with_limit(_study("study1.designed.json"), "novelty_bench", 5)
    messages = [p["message"] for p in design.problems(data, cards)]
    card = cards["novelty_bench"]
    assert (
        f"{card.title}: the token limit must be between {card.est_tokens_per_sample} "
        f"and {TOKEN_LIMIT_MAX}."
    ) in messages
    fine = _with_limit(_study("study1.designed.json"), "novelty_bench", 2_400)
    assert design.problems(fine, cards) == design.problems(_study("study1.designed.json"), cards)


# ── the run ──────────────────────────────────────────────────────────────────


def _limits_sent(tmp_path: Path, limit: int | None) -> set[int]:
    config = h.study(seeded=1)
    if limit is not None:
        data = config.model_dump(mode="json")
        data["cards"][0]["overrides"]["max_tokens"] = limit
        config = StudyConfig.model_validate(data)
    cards = {h.CARD: h.card(judged=False)}
    rs = h.run_store(tmp_path, config)
    engine = StubEngine()
    run = h.orchestrator(rs, config, cards, engine).run()
    assert run.state == "complete"
    sent = [body for body in engine.requests if "messages" in body]
    assert sent, "the run generated"
    return {int(body["max_tokens"]) for body in sent}


def test_every_request_carries_the_card_limit_when_the_study_sets_none(tmp_path: Path) -> None:
    assert _limits_sent(tmp_path, None) == {h.card().max_tokens}


def test_every_request_of_every_arm_carries_the_study_limit(tmp_path: Path) -> None:
    assert _limits_sent(tmp_path, 128) == {128}


def test_a_run_refuses_a_limit_outside_the_card_bounds(tmp_path: Path) -> None:
    config = h.study(seeded=0)
    data = config.model_dump(mode="json")
    data["cards"][0]["overrides"]["max_tokens"] = 5  # under the card's usual reply length
    config = StudyConfig.model_validate(data)
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, {h.CARD: h.card(judged=False)}, StubEngine())
    with pytest.raises(ValueError, match="max_tokens=5 outside"):
        orch.plan_arm(orch.runnable_arms()[0])


# ── the pre-registration ─────────────────────────────────────────────────────


def test_the_designed_pre_registration_prints_the_study_limit_and_whose_it_is() -> None:
    renderer = load_renderer()
    plain = renderer.render(StudyConfig.model_validate(_study("study1.designed.json")))
    assert "set by this study" not in plain
    limited = renderer.render(
        StudyConfig.model_validate(
            _with_limit(_study("study1.designed.json"), "novelty_bench", 2_400)
        )
    )
    own = load_cards()["novelty_bench"].max_tokens
    # The document escapes Markdown, so the brackets are written with a backslash.
    line = f"max_tokens=2400 \(set by this study; the card has {own}\), thinking="
    assert line in limited
    changed = [
        after
        for before, after in zip(plain.splitlines(), limited.splitlines(), strict=True)
        if before != after
    ]
    assert len([text for text in changed if "max_tokens=" in text]) == 1


def test_the_legacy_pre_registration_prints_the_study_limit() -> None:
    renderer = load_renderer()
    limited = renderer.render(
        StudyConfig.model_validate(_with_limit(_study("study1.json"), "novelty_bench", 2_400))
    )
    assert "- **Generation:** max_tokens=2400, thinking=" in limited
