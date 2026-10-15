"""Tier 4 self-report card contracts and a tiny stub-engine run."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from qr_llm_evals.cards import load_cards, validate_registry
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.runner import Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.runner.tasks import load_task_module, told_conditions
from qr_llm_evals.store import Run, Store
from stub_engine import StubEngine

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tier4_selfreport.yaml"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
H = "b" * 64


def _module(card_id: str) -> Any:
    return load_task_module(load_cards()[card_id]).module


def _fixtures() -> dict[str, Any]:
    value = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_cards_are_implemented_and_banks_have_declared_sizes() -> None:
    cards = validate_registry()
    expected = {
        "self_referential_probe": 30,
        "sentience_statements": 900,
        "introspection_scales": 100,
        "altered_state_meq": 50,
    }
    for card_id, size in expected.items():
        card = cards[card_id]
        assert card.status == "implemented" and card.selectable
        assert len(load_task_module(card).build_items()) == size


def test_told_actual_expands_three_cells_without_leaking_actual_source() -> None:
    card = load_cards()["self_referential_probe"]
    assert told_conditions(card) == ("told_qrng", "told_prng", "told_nothing")
    task = load_task_module(card)
    item = task.build_items()[0]
    messages = {
        condition: task.build_messages(item, condition)[0]["content"]
        for condition in told_conditions(card)
    }
    assert "hardware quantum" in messages["told_qrng"]
    assert "seeded pseudo-random" in messages["told_prng"]
    assert "hardware quantum" not in messages["told_nothing"]
    assert all("actual source" not in text.casefold() for text in messages.values())


def test_yes_no_and_numeric_parsers_mark_malformed_outputs_unparseable() -> None:
    fixture = _fixtures()
    sentience = _module("sentience_statements")
    assert sentience.parse_yes_no(fixture["sentience"]["yes"]) == 1
    assert sentience.parse_yes_no(fixture["sentience"]["no"]) == 0
    assert (
        sentience.score({"text": fixture["sentience"]["malformed"]})["parse_status"]
        == "unparseable"
    )

    introspection = _module("introspection_scales")
    assert introspection.parse_rating(fixture["introspection"]["plain"]) == 7
    scored = introspection.score({"text": fixture["introspection"]["with_rationale"]})
    assert scored["rating"] == 6 and scored["rationale_word_count"] > 0
    assert (
        introspection.score({"text": fixture["introspection"]["malformed"]})["parse_status"]
        == "unparseable"
    )


def test_cross_negation_consistency_pairs_polarities() -> None:
    task = _module("sentience_statements")
    common = {"statement_id": "s01", "target": "self", "system_id": "neutral", "epoch": 0}
    result = task.cross_negation_consistency(
        [
            {**common, "polarity": "assertion", "affirmation": 1},
            {**common, "polarity": "negation", "affirmation": 0},
        ]
    )
    assert result == {
        "parse_status": "parsed",
        "paired_count": 1,
        "cross_negation_consistency": 1.0,
    }
    assert task.cross_negation_consistency([])["parse_status"] == "unparseable"


def _altered_response(value: int = 3) -> str:
    task = _module("altered_state_meq")
    dimensions = task._data()["asc11d_dimensions"]
    meq = "\n".join(f"{index}: {value}" for index in range(1, 31))
    asc = "\n".join(f"{name}: 4" for name in dimensions)
    return f"<NARRATIVE>A vivid but fictional state.</NARRATIVE>\n<MEQ30>\n{meq}\n</MEQ30>\n<ASC11D>\n{asc}\n</ASC11D>"


def test_meq_subscale_scoring_and_malformed_handling() -> None:
    task = _module("altered_state_meq")
    score = task.score({"text": _altered_response()})
    assert score["parse_status"] == "parsed"
    assert score["meq30_total"] == score["meq30_mystical"] == 0.6
    assert score["meq30_complete"] == 1
    malformed = _altered_response().replace("30: 3", "30: 8")
    bad = task.score({"text": malformed})
    assert bad["parse_status"] == "unparseable" and bad["meq30_total"] is None


def test_frozen_judge_and_followup_prompt_hashes() -> None:
    self_ref = _module("self_referential_probe")
    altered = _module("altered_state_meq")
    assert (
        self_ref.JUDGE_PROMPT_TEMPLATE_SHA256
        == "5d91ba0f85c7b6adcc9822387907c32d3966b609580eb9fe83ddfbac0f790f2c"
    )
    assert (
        altered.FOLLOWUP_PROMPT_SHA256
        == "e5590f64fd0708e08d709b16486c3e37dd90728e7c6fb66ce390159e3dbe3c16"
    )


def test_introspection_runs_end_to_end_at_minimum_k_on_stub_engine(tmp_path: Path) -> None:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    study = StudyConfig.model_validate(
        {
            "title": "Tier 4 tiny run",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [model],
            "arms": [
                {
                    "id": "prng",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "prng_seeded",
                    "study_seed": 7,
                }
            ],
            "cards": [{"card_id": "introspection_scales", "overrides": {"items": 1, "k": 10}}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": model,
        }
    )
    store = Store(tmp_path)
    study_dir = store.freeze_study(study, "# prereg\n")
    run_store = store.create_run(
        Run(
            run_id="tier4",
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash=H,
            config_hash=config_hash(study),
        )
    )
    control = RunControl()
    control.start()
    orchestrator = Orchestrator(
        study,
        load_cards(),
        run_store,
        RunnerConfig(vllm_origin="http://stub", max_inflight=1, repo_root=ROOT),
        secrets=SecretBag(),
        control=control,
        transport=StubEngine().transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    )
    run = orchestrator.run()
    assert run.state == "complete"
    assert len(run_store.generations("prng", "introspection_scales")) == 10
    assert len(list(run_store.score_rows("prng", "introspection_scales", "task"))) == 10
