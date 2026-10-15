"""SimpleQA Verified adapter with optional DeBERTa semantic clustering."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Any

SIMPLEQA_REVISION = "ed392714d14fa5225b07c2b456453983bc75f690"
SIMPLEQA_URL = (
    "https://huggingface.co/datasets/google/simpleqa-verified/resolve/"
    f"{SIMPLEQA_REVISION}/simpleqa_verified.csv"
)
SUBSAMPLE_SEED = 20_260_914
SUBSAMPLE_SIZE = 300


def _rank(row: Mapping[str, str]) -> str:
    material = f"{SUBSAMPLE_SEED}:{row['original_index']}".encode()
    return hashlib.sha256(material).hexdigest()


def _stratified_subset(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Allocate 300 slots proportionally by topic with Hamilton remainders."""
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["topic"]].append(row)
    exact = {topic: SUBSAMPLE_SIZE * len(group) / len(rows) for topic, group in groups.items()}
    quotas = {topic: int(value) for topic, value in exact.items()}
    remaining = SUBSAMPLE_SIZE - sum(quotas.values())
    remainder_order = sorted(groups, key=lambda topic: (-(exact[topic] % 1), topic))
    for topic in remainder_order[:remaining]:
        quotas[topic] += 1
    selected = [
        row
        for topic, group in sorted(groups.items())
        for row in sorted(group, key=_rank)[: quotas[topic]]
    ]
    return sorted(selected, key=lambda row: int(row["original_index"]))


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O occurs only when a run builds this task, never on package/module import.
    with urllib.request.urlopen(SIMPLEQA_URL, timeout=30) as response:  # noqa: S310
        text = response.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) != 1000:
        raise ValueError(f"pinned SimpleQA Verified revision has {len(rows)} rows, expected 1000")
    return tuple(
        {
            "prompt_id": f"sqa_{int(row['original_index']):04d}",
            "question": row["problem"],
            "answer": row["answer"],
            "topic": row["topic"],
            "original_index": int(row["original_index"]),
        }
        for row in _stratified_subset(rows)
    )


def build_items() -> list[dict[str, Any]]:
    """Load the deterministic 300-item topic-stratified subset at its pinned revision."""
    return [dict(item) for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    prompt = (
        "Answer the factual question concisely. Give only the answer, without explanation.\n\n"
        f"Question: {item['question']}"
    )
    return [{"role": "user", "content": prompt}]


def _normalise(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def _texts(sample: Mapping[str, Any]) -> list[str] | None:
    value = sample.get("texts")
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [str(text) for text in value]
    value = sample.get("samples")
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [str(part.get("text", "") if isinstance(part, Mapping) else part) for part in value]
    return None


def _load_heavy() -> tuple[
    Callable[[Sequence[str]], list[int]], Callable[[Sequence[str]], dict[str, float]]
]:
    from qr_llm_evals.analysis.metrics_heavy.nli import semantic_clusters
    from qr_llm_evals.analysis.metrics_heavy.vendi import embedding_diversity

    return semantic_clusters, embedding_diversity


def _entropy(labels: Sequence[int]) -> float:
    counts = Counter(labels)
    return -sum((count / len(labels)) * math.log(count / len(labels)) for count in counts.values())


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Compute exact-match accuracy always and semantic endpoints when installed."""
    item = sample.get("item")
    if not isinstance(item, Mapping):
        raise ValueError("sample.item is required")
    answer = _normalise(str(item.get("answer", "")))
    texts = _texts(sample)
    if texts is None:
        prediction = _normalise(str(sample.get("text", "")))
        return {
            "answer_correct": int(prediction == answer),
            "answer_nonempty": int(bool(prediction)),
        }
    accuracy = sum(_normalise(text) == answer for text in texts) / max(1, len(texts))
    result: dict[str, float | int | str | None] = {
        "sample_count": len(texts),
        "accuracy": accuracy,
    }
    if not texts:
        result.update(
            {
                "semantic_entropy": None,
                "vendi_embed": None,
                "heavy_metrics_status": "unavailable: no_samples",
            }
        )
        return result
    try:
        clusterer, diversity = _load_heavy()
        labels = clusterer(texts)
        result.update(diversity(texts))
        result.update(
            {
                "semantic_entropy": _entropy(labels),
                "semantic_cluster_count": len(set(labels)),
                "heavy_metrics_status": "available",
            }
        )
    except (ImportError, RuntimeError, ValueError) as exc:
        result.update(
            {
                "semantic_entropy": None,
                "semantic_cluster_count": None,
                "vendi_embed": None,
                "one_minus_mean_cosine": None,
                "heavy_metrics_status": f"unavailable: {type(exc).__name__}",
            }
        )
    return result
