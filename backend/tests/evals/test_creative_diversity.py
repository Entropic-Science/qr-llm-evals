from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.runner.tasks import load_task_module

REPO_ROOT = Path(__file__).resolve().parents[3]


def _task():
    card = load_cards(REPO_ROOT / "evals")["creative_diversity"]
    return card, load_task_module(card, REPO_ROOT).module


def test_frozen_prompt_set_has_100_items_per_task_and_k10_card() -> None:
    card, task = _task()
    items = task.build_items()

    assert card.status == "implemented"
    assert card.selectable is True
    assert card.k == 10
    assert len(items) == 300
    assert Counter(item["task"] for item in items) == {"poem": 100, "story": 100, "joke": 100}
    assert len({item["prompt_id"] for item in items}) == 300
    assert task.build_messages(items[0]) == [{"role": "user", "content": items[0]["prompt"]}]


def test_light_lexical_metrics_reward_distinct_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    _, task = _task()

    def unavailable():
        raise ImportError("metrics extra absent")

    monkeypatch.setattr(task, "_load_embedding_metric", unavailable)
    repeated = task.score({"texts": ["the same small phrase"] * 10})
    varied = task.score({"texts": [f"unique token{i} constellation{i}" for i in range(10)]})

    assert varied["distinct_1"] > repeated["distinct_1"]
    assert varied["one_minus_self_bleu"] > repeated["one_minus_self_bleu"]
    assert repeated["vendi_embed"] is None
    assert str(repeated["embedding_metrics_status"]).startswith("unavailable:")


@pytest.mark.metrics
@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="requires the optional metrics extra",
)
def test_heavy_embedding_metrics_use_frozen_cpu_model() -> None:
    from qr_llm_evals.analysis.metrics_heavy.embeddings import (
        EMBEDDING_MODEL_ID,
        EMBEDDING_MODEL_REVISION,
    )
    from qr_llm_evals.analysis.metrics_heavy.vendi import embedding_diversity

    result = embedding_diversity(["red fox", "red fox", "distant galaxy"])
    assert EMBEDDING_MODEL_ID == "sentence-transformers/all-mpnet-base-v2"
    assert len(EMBEDDING_MODEL_REVISION) == 40
    assert 1.0 <= result["vendi_embed"] <= 3.0
    assert 0.0 <= result["one_minus_mean_cosine"] <= 2.0
