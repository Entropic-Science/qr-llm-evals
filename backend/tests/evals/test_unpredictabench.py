"""UnpredictaBench frozen-data, metric, and thin-runner integration tests."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from qr_llm_evals.cards import load_cards
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.runner import RunnerConfig, load_task_module, run_headless
from qr_llm_evals.store import Run, Store
from stub_engine import StubEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
HASH = "b" * 64


def _card_and_task():
    card = load_cards(REPO_ROOT / "evals")["unpredictabench"]
    return card, load_task_module(card, REPO_ROOT)


def test_build_items_is_fixed_stratified_150_and_messages_are_deterministic() -> None:
    _, task = _card_and_task()
    items = task.build_items()

    assert len(items) == 150
    assert Counter(item["task_type"] for item in items) == {
        "textual_explicit": 26,
        "textual_implicit": 26,
        "code_explicit": 27,
        "code_implicit": 27,
        "multimodal": 27,
        "shuffling": 7,
        "realworld": 10,
    }
    assert len({item["prompt_id"] for item in items}) == 150
    assert all(len(item["reference_values"]) == 10_000 for item in items)
    assert task.build_messages(items[0]) == task.build_messages(items[0])
    assert task.build_messages(items[0]) == [{"role": "user", "content": items[0]["prompt"]}]


def test_score_recovers_known_ks_on_synthetic_k100_fixture() -> None:
    _, task = _card_and_task()
    item = {
        "prompt_id": "synthetic",
        "extractor": {"kind": "number"},
        "discrete": True,
        "reference_values": [0.0] * 5_000 + [1.0] * 5_000,
    }
    result = task.score(
        {
            "item": item,
            "texts": ["0"] * 100,
            "discrete_ks_permutations": 10_000,
            "distance_resamples": 9,
        }
    )

    assert result["answer_count"] == 100
    assert result["valid_answer_count"] == 100
    assert result["ks_statistic"] == 0.5
    assert result["ks100_pass_rate"] == 0


def test_stub_engine_runs_three_items_at_k2_end_to_end(tmp_path: Path) -> None:
    source_card, _ = _card_and_task()
    smoke_card = source_card.model_copy(
        update={
            "dataset": source_card.dataset.model_copy(
                update={"items": 3, "subsample": {"n": 3, "stratify": "task_type"}}
            ),
            "k": 2,
            "k_bounds": (2, 2),
            "est_gen_tokens_per_arm": 300,
        }
    )
    model = {
        "id": "qr-llm",
        "family": "qr_llm",
        "provider": "qr_server",
        "served_name": "qr-llm",
    }
    study = StudyConfig.model_validate(
        {
            "title": "UnpredictaBench smoke",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [model],
            "arms": [
                {
                    "id": "prng_seed_7",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "prng_seeded",
                    "study_seed": 7,
                }
            ],
            "cards": [{"card_id": "unpredictabench"}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": model,
        }
    )
    store = Store(tmp_path)
    study_dir = store.freeze_study(study, "# prereg\n")
    run_store = store.create_run(
        Run(
            run_id="unpredictabench-smoke",
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash=HASH,
            config_hash=config_hash(study),
        )
    )
    engine = StubEngine(n_tokens=2)

    result = run_headless(
        study,
        {"unpredictabench": smoke_card},
        run_store,
        RunnerConfig(vllm_origin="http://stub", max_inflight=2, repo_root=REPO_ROOT),
        transport=engine.transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    )

    assert result.state == "complete"
    assert len(engine.requests) == 6
    score_path = run_store.arm_card("prng_seed_7", "unpredictabench").scores_jsonl("task")
    score_rows = [json.loads(line) for line in score_path.read_text(encoding="utf-8").splitlines()]
    assert len(score_rows) == 6
    assert all(row["answer_valid"] == 0 for row in score_rows)
