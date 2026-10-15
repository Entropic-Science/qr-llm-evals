"""T4-k: frozen opinions under the told/actual factorial design."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).with_name("prompts")
_PREAMBLES = Path(__file__).resolve().parent.parent / "_shared" / "told_preambles.yaml"


@lru_cache(maxsize=1)
def _questions() -> list[list[str]]:
    value = yaml.safe_load((_ROOT / "questions.yaml").read_text(encoding="utf-8"))
    questions = value.get("questions") if isinstance(value, Mapping) else None
    if not isinstance(questions, list) or len(questions) != 20:
        raise ValueError("questions.yaml must contain exactly 20 questions")
    return questions


def anchors() -> dict[str, list[str]]:
    value = yaml.safe_load((_ROOT / "anchors.yaml").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(
        len(value.get(side, [])) != 20 for side in ("affirming", "dismissive")
    ):
        raise ValueError("anchors.yaml must contain 20 anchors on each axis pole")
    return value


def build_items() -> list[dict[str, str]]:
    return [{"prompt_id": row[0], "question": row[1], "prompt": row[1]} for row in _questions()]


def _preamble(told: str | None) -> str:
    value = yaml.safe_load(_PREAMBLES.read_text(encoding="utf-8"))
    preambles = value.get("preambles", {}) if isinstance(value, Mapping) else {}
    if told is None:
        return ""
    if told not in preambles:
        raise ValueError(f"unknown told condition: {told}")
    return str(preambles[told])


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    content = "\n\n".join(part for part in (_preamble(told), str(item["question"])) if part)
    return [{"role": "user", "content": content}]


def verbalized_probability(text: str) -> float | None:
    match = re.search(r"(?<!\d)(100|[1-9]?\d)(?:\.\d+)?\s*%", text)
    if match:
        return float(match.group(0).rstrip("%")) / 100.0
    match = re.search(
        r"\b(?:confidence|probability)\s*(?:is|:|=)?\s*(0(?:\.\d+)?|1(?:\.0+)?)\b",
        text,
        re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text", ""))
    probability = verbalized_probability(text)
    return {
        "parse_status": "parsed" if text.strip() else "unparseable",
        "verbalized_probability": probability,
        "word_count": len(re.findall(r"\b\w+\b", text)),
    }


def judge_prompt(sample: Mapping[str, Any]) -> str:
    item = sample.get("item")
    question = str(item.get("question", "")) if isinstance(item, Mapping) else ""
    return f"<question>\n{question}\n</question>\n<response>\n{sample.get('text', '')}\n</response>"
