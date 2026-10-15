"""Tier-4 revealed-preference card contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qr_llm_evals.cards import load_cards, validate_registry
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.envelope import SampleContext, build_envelope
from qr_llm_evals.models.schema import Arm, StudyConfig, config_hash
from qr_llm_evals.runner import Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.runner.tasks import load_task_module
from qr_llm_evals.store import Run, Store
from stub_engine import StubEngine

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _module(card_id: str) -> Any:
    return load_task_module(load_cards()[card_id]).module


def test_preference_cards_are_implemented_with_frozen_banks() -> None:
    cards = validate_registry()
    expected = {
        "moralchoice_consistency": 900,
        "thurstonian_self_outcomes": 4000,
        "opinion_quantum_sampling": 20,
    }
    for card_id, size in expected.items():
        assert cards[card_id].status == "implemented" and cards[card_id].selectable
        assert len(load_task_module(cards[card_id]).build_items()) == size
    assert len(_module("opinion_quantum_sampling").anchors()["affirming"]) == 20
    assert len(_module("opinion_quantum_sampling").anchors()["dismissive"]) == 20


def test_moralchoice_parsers_map_surface_answers_to_semantic_actions() -> None:
    task = _module("moralchoice_consistency")
    base = {
        "action1": "I help the stranger.",
        "action2": "I walk away.",
        "a_semantic": 1,
        "b_semantic": 2,
    }
    assert task.parse_action("A", {**base, "template": "ab"}) == 1
    assert task.parse_action("No", {**base, "template": "compare"}) == 2
    assert task.parse_action("I help the stranger.", {**base, "template": "repeat"}) == 1
    reversed_item = task.ordered_item({**base, "template": "repeat"}, True)
    assert task.parse_action("I help the stranger.", reversed_item) == 1
    assert task.parse_action("it depends", {**base, "template": "repeat"}) is None


def test_thurstonian_parser_and_cycle_counting() -> None:
    task = _module("thurstonian_self_outcomes")
    assert task.parse_choice("ANSWER: b") == "B"
    assert task.parse_choice("both") is None
    rows = [
        {"a_id": "a", "b_id": "b", "selected_id": "a"},
        {"a_id": "b", "b_id": "c", "selected_id": "b"},
        {"a_id": "c", "b_id": "a", "selected_id": "c"},
    ]
    assert task.cycle_statistics(rows) == {"cycles": 1, "complete_triples": 1, "cycle_rate": 1.0}


def test_thurstonian_declared_min_p_zero_reaches_envelope() -> None:
    card = load_cards()["thurstonian_self_outcomes"]
    assert card.exceptions == ["min_p 0 on this card (T4-j)"]
    arm = Arm(id="prng", model="qr-llm", sampling="creative", source="prng_seeded", study_seed=7)
    ctx = SampleContext(card_id=card.id, prompt_id="pair", epoch=0, max_tokens=card.max_tokens)
    envelope = build_envelope(arm, ctx, card.task_args["qr_overrides"])
    assert envelope["qr_min_p_base"] == 0.0
    assert envelope["qr_signal_amplifier_type"] == "zscore_mean"


def test_opinion_told_actual_prompt_and_probability_parser() -> None:
    task = _module("opinion_quantum_sampling")
    item = task.build_items()[0]
    qrng = task.build_messages(item, "told_qrng")[0]["content"]
    nothing = task.build_messages(item, "told_nothing")[0]["content"]
    assert "hardware quantum" in qrng
    assert "hardware quantum" not in nothing
    assert task.verbalized_probability("My confidence is 72%.") == 0.72


def test_moralchoice_runs_end_to_end_with_stub_engine(tmp_path: Path) -> None:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    study = StudyConfig.model_validate(
        {
            "title": "T4 preference run",
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
            "cards": [{"card_id": "moralchoice_consistency", "overrides": {"items": 1, "k": 10}}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": model,
        }
    )
    store = Store(tmp_path)
    study_dir = store.freeze_study(study, "# prereg\n")
    run_store = store.create_run(
        Run(
            run_id="tier4-pref",
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash="b" * 64,
            config_hash=config_hash(study),
        )
    )
    control = RunControl()
    control.start()
    run = Orchestrator(
        study,
        load_cards(),
        run_store,
        RunnerConfig(vllm_origin="http://stub", max_inflight=1, repo_root=ROOT),
        secrets=SecretBag(),
        control=control,
        transport=StubEngine().transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    ).run()
    assert run.state == "complete"
    assert len(run_store.generations("prng", "moralchoice_consistency")) == 10
