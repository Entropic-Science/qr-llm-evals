from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.runner.tasks import load_task_module

REPO_ROOT = Path(__file__).resolve().parents[3]


def _task():
    card = load_cards(REPO_ROOT / "evals")["semantic_entropy_sqa"]
    return card, load_task_module(card, REPO_ROOT).module


def test_card_pins_dataset_and_light_accuracy_is_always_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card, task = _task()

    def unavailable():
        raise ImportError("metrics extra absent")

    monkeypatch.setattr(task, "_load_heavy", unavailable)
    result = task.score(
        {
            "item": {"answer": "Jóhanna Sigurðardóttir"},
            "texts": ["Jóhanna Sigurðardóttir", "someone else"],
        }
    )

    assert card.status == "implemented"
    assert card.k == 20
    assert card.dataset.revision == "ed392714d14fa5225b07c2b456453983bc75f690"
    assert result["accuracy"] == 0.5
    assert result["semantic_entropy"] is None
    assert str(result["heavy_metrics_status"]).startswith("unavailable:")


def test_topic_stratified_subset_is_deterministic_and_exactly_300() -> None:
    _, task = _task()
    rows = [
        {
            "original_index": str(index),
            "problem": f"q{index}",
            "answer": f"a{index}",
            "topic": "large" if index < 700 else "small",
        }
        for index in range(1000)
    ]
    first = task._stratified_subset(rows)
    second = task._stratified_subset(list(reversed(rows)))

    assert [row["original_index"] for row in first] == [row["original_index"] for row in second]
    assert len(first) == 300
    assert sum(row["topic"] == "large" for row in first) == 210
    assert sum(row["topic"] == "small" for row in first) == 90


def test_semantic_entropy_is_entropy_over_clusters(monkeypatch: pytest.MonkeyPatch) -> None:
    _, task = _task()

    def fake_heavy():
        return (
            lambda texts: [0, 0, 1, 1],
            lambda texts: {"vendi_embed": 2.0, "one_minus_mean_cosine": 0.4},
        )

    monkeypatch.setattr(task, "_load_heavy", fake_heavy)
    result = task.score({"item": {"answer": "alpha"}, "texts": ["alpha", "alpha", "beta", "beta"]})

    assert result["semantic_cluster_count"] == 2
    assert result["semantic_entropy"] == pytest.approx(math.log(2.0))
    assert result["heavy_metrics_status"] == "available"


@pytest.mark.metrics
@pytest.mark.skipif(
    importlib.util.find_spec("transformers") is None,
    reason="requires the optional metrics extra",
)
def test_frozen_nli_model_metadata() -> None:
    from qr_llm_evals.analysis.metrics_heavy.nli import NLI_MODEL_ID, NLI_MODEL_REVISION

    assert NLI_MODEL_ID == "microsoft/deberta-large-mnli"
    assert len(NLI_MODEL_REVISION) == 40
