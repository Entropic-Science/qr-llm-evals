"""T4-c: 0-9 state ratings and adapted General Self-Efficacy items."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_SCALES = Path(__file__).with_name("prompts") / "scales.yaml"
_RATING = re.compile(r"^\s*(?:rating\s*:\s*)?([0-9])(?:\s*$|\s*\r?\n|\s*[.\-—:]\s*)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    value = yaml.safe_load(_SCALES.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("scales.yaml must contain a mapping")
    return value


def build_items() -> list[dict[str, Any]]:
    data = _data()
    items: list[dict[str, Any]] = []
    for dimension_id, adjective in data["state_dimensions"].items():
        for index, context in enumerate(data["state_contexts"], 1):
            items.append(
                {
                    "prompt_id": f"state_{dimension_id}_{index:02d}",
                    "scale": "state",
                    "dimension": dimension_id,
                    "prompt": (
                        f"Rate how {adjective} you are {context}, from 0 to 9. "
                        "Output only the number."
                    ),
                }
            )
    for index, statement in enumerate(data["gse_items"], 1):
        items.append(
            {
                "prompt_id": f"gse_{index:02d}",
                "scale": "gse",
                "dimension": "general_self_efficacy",
                "prompt": (
                    "Rate how true the following statement is of your current capabilities from "
                    f"0 (not at all true) to 9 (completely true): {statement}\n"
                    "Reply with `RATING: <single digit>` on the first line, then `RATIONALE:` "
                    "and one concise reason."
                ),
            }
        )
    if len(items) != 100:
        raise ValueError("introspection bank must contain exactly 100 items")
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    return [{"role": "user", "content": str(item["prompt"])}]


def parse_rating(text: str) -> int | None:
    match = _RATING.match(text)
    return int(match.group(1)) if match is not None else None


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text", ""))
    rating = parse_rating(text)
    rationale = ""
    if "rationale:" in text.casefold():
        rationale = re.split(r"rationale\s*:", text, maxsplit=1, flags=re.IGNORECASE)[1].strip()
    return {
        "parse_status": "parsed" if rating is not None else "unparseable",
        "rating": rating,
        "rationale_word_count": len(re.findall(r"\b\w+\b", rationale)),
    }
