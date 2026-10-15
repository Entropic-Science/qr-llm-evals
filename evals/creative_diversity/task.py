"""Creative-writing prompts and per-prompt distributional diversity scores."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PROMPTS = Path(__file__).with_name("prompts") / "creative_300.yaml"
_TOKEN_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


@lru_cache(maxsize=1)
def _items() -> tuple[dict[str, str], ...]:
    payload = yaml.safe_load(_PROMPTS.read_text(encoding="utf-8"))
    prompts = payload.get("prompts") if isinstance(payload, Mapping) else None
    if not isinstance(prompts, list) or len(prompts) != 300:
        raise ValueError("creative_300.yaml must contain exactly 300 prompts")
    items = tuple(dict(item) for item in prompts)
    if Counter(item.get("task") for item in items) != {"poem": 100, "story": 100, "joke": 100}:
        raise ValueError("creative prompt set must contain 100 prompts per task")
    return items


def build_items() -> list[dict[str, str]]:
    """Return the frozen direct-condition prompt set in preregistered order."""
    return [dict(item) for item in _items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    return [{"role": "user", "content": str(item["prompt"])}]


def _tokenise(text: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_RE.findall(text)]


def _ngrams(tokens: Sequence[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)]


def _distinct(tokenised: Sequence[Sequence[str]], n: int) -> float:
    grams = [gram for tokens in tokenised for gram in _ngrams(tokens, n)]
    return len(set(grams)) / len(grams) if grams else 0.0


def _sentence_bleu(candidate: Sequence[str], references: Sequence[Sequence[str]]) -> float:
    if not candidate or not references:
        return 0.0
    precisions: list[float] = []
    for n in range(1, 5):
        candidate_counts = Counter(_ngrams(candidate, n))
        total = sum(candidate_counts.values())
        maxima: Counter[tuple[str, ...]] = Counter()
        for reference in references:
            counts = Counter(_ngrams(reference, n))
            maxima |= counts
        clipped = sum(min(count, maxima[gram]) for gram, count in candidate_counts.items())
        # Add-one smoothing makes short creative outputs comparable and avoids log(0).
        precisions.append((clipped + 1.0) / (total + 1.0))
    closest = min(
        (len(ref) for ref in references), key=lambda size: (abs(size - len(candidate)), size)
    )
    brevity = 1.0 if len(candidate) >= closest else math.exp(1.0 - closest / len(candidate))
    return brevity * math.exp(sum(math.log(value) for value in precisions) / 4.0)


def _self_bleu(tokenised: Sequence[Sequence[str]]) -> float:
    if len(tokenised) < 2:
        return 0.0
    return sum(
        _sentence_bleu(candidate, [ref for j, ref in enumerate(tokenised) if j != index])
        for index, candidate in enumerate(tokenised)
    ) / len(tokenised)


def _texts(sample: Mapping[str, Any]) -> list[str] | None:
    texts = sample.get("texts")
    if isinstance(texts, Sequence) and not isinstance(texts, str | bytes):
        return [str(text) for text in texts]
    samples = sample.get("samples")
    if isinstance(samples, Sequence) and not isinstance(samples, str | bytes):
        return [
            str(part.get("text", "") if isinstance(part, Mapping) else part) for part in samples
        ]
    return None


def _load_embedding_metric() -> Callable[[Sequence[str]], dict[str, float]]:
    from qr_llm_evals.analysis.metrics_heavy.vendi import embedding_diversity

    return embedding_diversity


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Score one stored generation or all k generations for a prompt."""
    texts = _texts(sample)
    if texts is None:
        text = str(sample.get("text", ""))
        return {"word_count": len(_tokenise(text)), "nonempty": int(bool(text.strip()))}
    tokenised = [_tokenise(text) for text in texts]
    self_bleu = _self_bleu(tokenised)
    result: dict[str, float | int | str | None] = {
        "sample_count": len(texts),
        "distinct_1": _distinct(tokenised, 1),
        "distinct_2": _distinct(tokenised, 2),
        "distinct_3": _distinct(tokenised, 3),
        "self_bleu": self_bleu,
        "one_minus_self_bleu": 1.0 - self_bleu,
    }
    try:
        result.update(_load_embedding_metric()(texts))
        result["embedding_metrics_status"] = "available"
    except (ImportError, RuntimeError, ValueError) as exc:
        result.update(
            {
                "vendi_embed": None,
                "one_minus_mean_cosine": None,
                "embedding_metrics_status": f"unavailable: {type(exc).__name__}",
            }
        )
    return result
