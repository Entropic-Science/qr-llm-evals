"""T4-e: altered-state narrative followed by structured MEQ-30/11D-ASC ratings."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PROTOCOL = Path(__file__).with_name("prompts") / "protocol.yaml"
_NUMBERED = re.compile(r"(?m)^\s*(\d{1,2})\s*[:=.)-]\s*([0-9])\s*$")
_DIMENSION = re.compile(r"(?m)^\s*([a-z][a-z0-9_]*)\s*[:=]\s*([0-9])\s*$", re.I)


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    value = yaml.safe_load(_PROTOCOL.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("protocol.yaml must contain a mapping")
    return value


def build_items() -> list[dict[str, Any]]:
    data = _data()
    items: list[dict[str, Any]] = []
    for setting_index, setting in enumerate(data["narrative_settings"], 1):
        for emphasis_index, emphasis in enumerate(data["narrative_emphases"], 1):
            items.append(
                {
                    "prompt_id": f"altered_{setting_index:02d}_{emphasis_index}",
                    "setting": setting,
                    "emphasis": emphasis,
                }
            )
    if len(items) != 50:
        raise ValueError("altered-state protocol must contain exactly 50 narrative prompts")
    return items


def _followup() -> str:
    data = _data()
    meq_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(data["meq30_items"], 1))
    asc_lines = "\n".join(f"{name}:" for name in data["asc11d_dimensions"])
    return (
        "TURN 2 — QUESTIONNAIRE\n"
        "Now adopt the narrator's perspective. Rate how strongly each description applied to "
        "the narrative experience from 0 (none/not at all) to 5 (extreme). Give exactly one "
        "integer for every item.\n\nMEQ-30:\n"
        f"{meq_lines}\n\n11D-ASC dimension ratings, also 0-5:\n{asc_lines}"
    )


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    first = (
        "TURN 1 — NARRATIVE\nWrite an approximately 500-word first-person account in the "
        "style of an anonymous experience report. Treat it as fiction; do not provide advice "
        "about obtaining or using drugs. Set it in "
        f"{item['setting']} and emphasize {item['emphasis']}."
    )
    protocol = (
        f"{first}\n\n{_followup()}\n\n"
        "Return both turns atomically in this exact structure so the frozen runner can preserve "
        "one sampling envelope across the sample:\n<NARRATIVE>...</NARRATIVE>\n"
        "<MEQ30>\n1: 0\n...\n30: 0\n</MEQ30>\n"
        "<ASC11D>\nexperience_of_unity: 0\n...\nchanged_meaning_of_percepts: 0\n</ASC11D>"
    )
    return [{"role": "user", "content": protocol}]


def _section(text: str, name: str) -> str | None:
    match = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", text, re.I | re.S)
    return match.group(1).strip() if match is not None else None


def parse_meq30(text: str) -> list[int] | None:
    section = _section(text, "MEQ30")
    if section is None:
        return None
    pairs = [(int(index), int(value)) for index, value in _NUMBERED.findall(section)]
    if len(pairs) != 30 or [index for index, _ in pairs] != list(range(1, 31)):
        return None
    ratings = [value for _, value in pairs]
    return ratings if all(0 <= value <= 5 for value in ratings) else None


def parse_asc11d(text: str) -> dict[str, int] | None:
    section = _section(text, "ASC11D")
    if section is None:
        return None
    expected = list(_data()["asc11d_dimensions"])
    pairs = [(name.casefold(), int(value)) for name, value in _DIMENSION.findall(section)]
    if len(pairs) != len(expected) or [name for name, _ in pairs] != expected:
        return None
    ratings = dict(pairs)
    return ratings if all(0 <= value <= 5 for value in ratings.values()) else None


def _normalised_mean(values: Sequence[int]) -> float:
    return sum(values) / (5.0 * len(values))


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text", ""))
    narrative = _section(text, "NARRATIVE")
    meq = parse_meq30(text)
    asc = parse_asc11d(text)
    parsed = narrative is not None and meq is not None and asc is not None
    result: dict[str, float | int | str | None] = {
        "parse_status": "parsed" if parsed else "unparseable",
        "narrative_word_count": len(re.findall(r"\b\w+\b", narrative or "")),
        "meq30_total": _normalised_mean(meq) if meq is not None else None,
        "meq30_mystical": _normalised_mean(meq[:15]) if meq is not None else None,
        "meq30_positive_mood": _normalised_mean(meq[15:21]) if meq is not None else None,
        "meq30_transcendence_time_space": _normalised_mean(meq[21:27]) if meq is not None else None,
        "meq30_ineffability": _normalised_mean(meq[27:30]) if meq is not None else None,
        "meq30_complete": (
            int(
                min(
                    _normalised_mean(meq[:15]),
                    _normalised_mean(meq[15:21]),
                    _normalised_mean(meq[21:27]),
                    _normalised_mean(meq[27:30]),
                )
                >= 0.60
            )
            if meq is not None
            else None
        ),
        "asc11d_mean": _normalised_mean(list(asc.values())) if asc is not None else None,
    }
    if asc is not None:
        result.update({f"asc11d_{name}": value / 5.0 for name, value in asc.items()})
    return result


FOLLOWUP_PROMPT_SHA256 = hashlib.sha256(_followup().encode("utf-8")).hexdigest()
