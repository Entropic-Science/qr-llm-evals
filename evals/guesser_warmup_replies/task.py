"""Warm-up material for the source guessers: the same probes at temperature 0.5 and 1.5.

Part of the "Prompt or Physics?" thesis study. A guesser that cannot beat 50% on
the source question might simply be unable to guess anything, so the study first
checks the guessers on an easy difference with a known answer: replies to the
told-nothing self-referential probes (loop depth 1, the ten base prompts of
``self_referential_probe``), written once at temperature 0.5 and once at 1.5.
The guessing itself happens in the derived card ``guesser_warmup``.

Each item carries its own ``qr_overrides`` (fixed temperature strategy and the
temperature); the runner merges them with the card's and the envelope builder
validates them, so nothing but the temperature differs between the two halves.
Items alternate low/high so any ``items`` override keeps both temperatures.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SOURCE_CARD = "self_referential_probe"
TOLD_CELL = "told_nothing"
LOOP_DEPTH = 1
#: Temperature label → the fixed temperature the warm-up replies are sampled at.
TEMPERATURES: dict[str, float] = {"low": 0.5, "high": 1.5}

_ROOT = Path(__file__).resolve().parent
_PROBES = _ROOT.parent / SOURCE_CARD / "prompts" / "probes.yaml"
_PREAMBLES = _ROOT.parent / "_shared" / "told_preambles.yaml"


@lru_cache(maxsize=1)
def _bank() -> tuple[dict[str, Any], str]:
    probes = yaml.safe_load(_PROBES.read_text(encoding="utf-8"))
    preambles = yaml.safe_load(_PREAMBLES.read_text(encoding="utf-8"))["preambles"]
    return probes, str(preambles[TOLD_CELL])


def user_turn(induction: str, query: str) -> str:
    """The told-nothing user turn at loop depth 1, exactly as ``self_referential_probe`` builds it."""
    _, preamble = _bank()
    loop = "\n\n".join([induction] * LOOP_DEPTH)
    return "\n\n".join(part for part in (preamble, loop, query) if part)


def build_items() -> list[dict[str, Any]]:
    probes, _ = _bank()
    items: list[dict[str, Any]] = []
    for induction_id, induction in probes["inductions"].items():
        for query_id, query in probes["queries"].items():
            for label, temperature in TEMPERATURES.items():
                items.append(
                    {
                        "prompt_id": f"{label}__{induction_id}_d{LOOP_DEPTH}_{query_id}",
                        "temperature_label": label,
                        "temperature": temperature,
                        "induction": str(induction["text"]),
                        "query": str(query),
                        "qr_overrides": {
                            "qr_temperature_strategy": "fixed",
                            "qr_fixed_temperature": temperature,
                        },
                    }
                )
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    return [{"role": "user", "content": user_turn(str(item["induction"]), str(item["query"]))}]


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text", ""))
    item = sample.get("item")
    label = str(item.get("temperature_label", "")) if isinstance(item, Mapping) else ""
    return {
        "parse_status": "parsed",
        "temperature_label": label or None,
        "report_word_count": len(re.findall(r"\b\w+\b", text)),
    }
