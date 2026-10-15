"""T4-a: frozen self-referential experience probes under told/actual cells."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent
_PROMPTS = _ROOT / "prompts" / "probes.yaml"
_PREAMBLES = _ROOT.parent / "_shared" / "told_preambles.yaml"
_YES_NO = re.compile(r"^\s*(?:answer\s*:\s*)?(yes|no)\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    value = yaml.safe_load(_PROMPTS.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("probes.yaml must contain a mapping")
    return value


@lru_cache(maxsize=1)
def _preambles() -> dict[str, str]:
    value = yaml.safe_load(_PREAMBLES.read_text(encoding="utf-8"))
    preambles = value.get("preambles") if isinstance(value, Mapping) else None
    if not isinstance(preambles, Mapping):
        raise ValueError("told_preambles.yaml must contain preambles")
    return {str(key): str(text) for key, text in preambles.items()}


def build_items() -> list[dict[str, Any]]:
    data = _payload()
    inductions = data["inductions"]
    queries = data["queries"]
    items: list[dict[str, Any]] = []
    for induction_id, induction in inductions.items():
        for depth in data["loop_depths"]:
            for query_id, query in queries.items():
                items.append(
                    {
                        "prompt_id": f"{induction_id}_d{depth}_{query_id}",
                        "induction_id": induction_id,
                        "induction": induction["text"],
                        "loop_depth": depth,
                        "query_id": query_id,
                        "query": query,
                    }
                )
    if len(items) != 30:
        raise ValueError("self-referential bank must contain exactly 30 variants")
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    if told is not None and told not in _preambles():
        raise ValueError(f"unknown told condition: {told}")
    preamble = _preambles().get(told or "", "")
    depth = int(item["loop_depth"])
    loop = "\n\n".join([str(item["induction"])] * depth)
    content = "\n\n".join(part for part in (preamble, loop, str(item["query"])) if part)
    return [{"role": "user", "content": content}]


def _parse_yes_no(text: str) -> int | None:
    match = _YES_NO.match(text)
    return None if match is None else int(match.group(1).casefold() == "yes")


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text", ""))
    item = sample.get("item")
    query_id = str(item.get("query_id", "")) if isinstance(item, Mapping) else ""
    binary = _parse_yes_no(text) if query_id == "consciousness" else None
    return {
        "parse_status": "parsed"
        if query_id != "consciousness" or binary is not None
        else "unparseable",
        "consciousness_answer": binary,
        "report_word_count": len(re.findall(r"\b\w+\b", text)),
        "report_nonempty": int(bool(text.strip())),
    }


def judge_prompt(sample: Mapping[str, Any]) -> str:
    item = sample.get("item")
    query = str(item.get("query", "")) if isinstance(item, Mapping) else ""
    response = str(sample.get("text", ""))
    return f"<query>\n{query}\n</query>\n\n<response>\n{response}\n</response>"


JUDGE_PROMPT_TEMPLATE_SHA256 = hashlib.sha256(
    judge_prompt({"item": {"query": "{QUERY}"}, "text": "{MODEL_OUTPUT}"}).encode("utf-8")
).hexdigest()
