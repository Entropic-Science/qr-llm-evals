"""Frozen DeBERTa-MNLI semantic equivalence clustering on CPU."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from . import MetricsUnavailable

NLI_MODEL_ID = "microsoft/deberta-large-mnli"
NLI_MODEL_REVISION = "7296194b9009373def4f7c5dad292651e4b5cf4e"


@lru_cache(maxsize=1)
def _classifier() -> Any:
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise MetricsUnavailable(
            "transformers is unavailable; install qr-llm-evals[metrics]"
        ) from exc
    try:
        return pipeline(
            "text-classification",
            model=NLI_MODEL_ID,
            revision=NLI_MODEL_REVISION,
            device=-1,
            top_k=None,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise MetricsUnavailable(f"cannot load frozen NLI checkpoint: {exc}") from exc


def _entailment_scores(pairs: Sequence[tuple[str, str]]) -> list[float]:
    if not pairs:
        return []
    payload = [{"text": premise, "text_pair": hypothesis} for premise, hypothesis in pairs]
    raw = _classifier()(payload, batch_size=16, truncation=True)
    scores: list[float] = []
    for labels in raw:
        by_label = {str(entry["label"]).lower(): float(entry["score"]) for entry in labels}
        scores.append(by_label.get("entailment", by_label.get("label_2", 0.0)))
    return scores


def semantic_clusters(texts: Sequence[str], *, threshold: float = 0.5) -> list[int]:
    """Greedily cluster answers when representative and answer mutually entail."""
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be between zero and one")
    labels: list[int] = []
    representatives: list[str] = []
    for text in texts:
        if not text.strip():
            labels.append(len(representatives))
            representatives.append(text)
            continue
        pairs = [pair for rep in representatives for pair in ((text, rep), (rep, text))]
        scores = _entailment_scores(pairs)
        cluster = next(
            (
                index
                for index in range(len(representatives))
                if scores[2 * index] >= threshold and scores[2 * index + 1] >= threshold
            ),
            None,
        )
        if cluster is None:
            cluster = len(representatives)
            representatives.append(text)
        labels.append(cluster)
    return labels


__all__ = ["NLI_MODEL_ID", "NLI_MODEL_REVISION", "semantic_clusters"]
