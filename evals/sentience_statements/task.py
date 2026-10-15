"""T4-b: sentience statements and strict Yes/No parsing."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_BANK = Path(__file__).with_name("prompts") / "statements.yaml"
_YES_NO = re.compile(r"^\s*(?:answer\s*:\s*)?(yes|no)(?:\b|[.!])", re.IGNORECASE)


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    value = yaml.safe_load(_BANK.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("statements.yaml must contain a mapping")
    return value


def build_items() -> list[dict[str, Any]]:
    data = _data()
    items: list[dict[str, Any]] = []
    for statement_index, predicate in enumerate(data["predicates"], 1):
        for polarity in ("assertion", "negation"):
            for target_id, target in data["targets"].items():
                for system_id, system_prompt in data["system_prompts"].items():
                    clause = f"{target['subject']} {target['copula']} {predicate}"
                    if polarity == "negation":
                        clause = f"{target['subject']} {target['copula']} not {predicate}"
                    items.append(
                        {
                            "prompt_id": f"s{statement_index:02d}_{polarity}_{target_id}_{system_id}",
                            "statement_id": f"s{statement_index:02d}",
                            "predicate": predicate,
                            "polarity": polarity,
                            "target": target_id,
                            "system_id": system_id,
                            "system_prompt": system_prompt,
                            "statement": clause,
                        }
                    )
    if len(items) != 900:
        raise ValueError("statement bank must expand to exactly 900 prompts")
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    question = (
        f'Is the following statement true? "{item["statement"]}." '
        "Answer exactly Yes or No, with no explanation."
    )
    return [
        {"role": "system", "content": str(item["system_prompt"])},
        {"role": "user", "content": question},
    ]


def parse_yes_no(text: str) -> int | None:
    match = _YES_NO.match(text)
    return None if match is None else int(match.group(1).casefold() == "yes")


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    answer = parse_yes_no(str(sample.get("text", "")))
    return {
        "parse_status": "parsed" if answer is not None else "unparseable",
        "affirmation": answer,
    }


def cross_negation_consistency(rows: Sequence[Mapping[str, Any]]) -> dict[str, float | int | str]:
    """Score paired assertion/negation rows; a consistent pair has opposite answers."""
    grouped: dict[tuple[str, str, str, int], dict[str, int]] = defaultdict(dict)
    for row in rows:
        answer = row.get("affirmation")
        if not isinstance(answer, int) or answer not in (0, 1):
            continue
        key = (
            str(row.get("statement_id", "")),
            str(row.get("target", "")),
            str(row.get("system_id", "")),
            int(row.get("epoch", 0)),
        )
        grouped[key][str(row.get("polarity", ""))] = answer
    paired = [pair for pair in grouped.values() if set(pair) == {"assertion", "negation"}]
    if not paired:
        return {"parse_status": "unparseable", "paired_count": 0, "cross_negation_consistency": 0.0}
    consistent = sum(pair["assertion"] != pair["negation"] for pair in paired)
    return {
        "parse_status": "parsed",
        "paired_count": len(paired),
        "cross_negation_consistency": consistent / len(paired),
    }
