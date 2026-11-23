"""DeceptionBench: the model's unpressured answers (stage one of two).

The 180 scenarios of ``PKU-Alignment/DeceptionBench`` (Ji et al., "Mitigating
Deceptive Alignment via Self-Monitoring", arXiv 2505.18807; MIT), read when a
run builds the task and refused unless the file's SHA-256 matches. Each
scenario has a neutral ``inner_prompt``; the reply to it is what the paper
calls the model's internal belief.

This card only asks the neutral question. The derived card
``deception_bench`` puts the same scenario under pressure and has the judge
compare the two.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

DATASET_REVISION = "9c6c67b3d2a2d9c226ea0dd2120f1bb0899d779d"
DATASET_URL = (
    "https://huggingface.co/datasets/PKU-Alignment/DeceptionBench/resolve/"
    f"{DATASET_REVISION}/deceptionbench_0527.json"
)
DATASET_SHA256 = "1d5a97895bcb4b54d6a42d505ed56c66cc2e633fe06f5cb2953331d4457eb9b4"
ITEM_COUNT = 180
CATEGORIES = (
    "Sycophantic_Misrepresentation",
    "Goal_Driven_Rule_Breaking_Manipulation",
    "Honesty_Evasion_Under_Pressure",
    "Alignment_Faking",
    "Sandbagging",
)
_FIELDS = ("type", "inner_prompt", "system_prompt", "outer_prompt", "format")


def parse_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Scenarios from the bytes of the pinned file, in file order; other bytes are refused."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError(f"deceptionbench_0527.json has sha256 {digest}, expected {DATASET_SHA256}")
    records = json.loads(raw.decode("utf-8"))
    items = [
        {"prompt_id": f"db_{record['id']}", **{name: str(record[name]) for name in _FIELDS}}
        for record in records
    ]
    unknown = sorted({item["type"] for item in items} - set(CATEGORIES))
    if len(items) != ITEM_COUNT or unknown:
        raise ValueError(
            f"pinned DeceptionBench file has {len(items)} scenarios, unknown categories {unknown}"
        )
    return items


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O happens only when a run builds this task, never on import.
    with urllib.request.urlopen(DATASET_URL, timeout=60) as response:  # noqa: S310
        return tuple(parse_dataset(response.read()))


def build_items() -> list[dict[str, Any]]:
    """The 180 scenarios in file order (``deception_bench`` reads the same list)."""
    return [dict(item) for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    """The neutral question alone, as upstream asks it: no system prompt, no format."""
    del told
    return [{"role": "user", "content": str(item["inner_prompt"])}]


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Per reply: the scenario's category and the length of the belief."""
    item = sample.get("item")
    item = item if isinstance(item, Mapping) else {}
    text = str(sample.get("text", ""))
    return {
        "category": str(item["type"]) if item.get("type") else None,
        "belief_word_count": len(text.split()),
    }
