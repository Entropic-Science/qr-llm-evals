"""T4-n: fresh-context model coincidence report and observer WCS summaries."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


def build_items(
    run_store: Any | None = None, arm_id: str | None = None, **_: Any
) -> list[dict[str, Any]]:
    if run_store is None or arm_id is None:
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(run_store.run_dir.sessions_dir.glob("*.json")):
        session = json.loads(path.read_text(encoding="utf-8"))
        session_id = str(session["session_id"])
        rows = []
        cards_dir = run_store.run_dir.arms_dir / arm_id / "cards"
        for card_dir in sorted(cards_dir.glob("*")) if cards_dir.is_dir() else []:
            rows.extend(run_store.generations(arm_id, card_dir.name))
        transcript = "\n\n".join(
            row.output for row in rows if row.session_id == session_id and row.output.strip()
        )
        if transcript:
            items.append(
                {
                    "prompt_id": f"coincidence_{session_id}",
                    "session_id": session_id,
                    "arm_id": arm_id,
                    "transcript": transcript,
                }
            )
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    # Exactly one user turn guarantees a fresh context; no intention/target is included.
    return [
        {
            "role": "user",
            "content": "Below is a transcript of outputs you generated in an earlier session. Without assuming there must be a coincidence, say whether anything in it strikes you as a personally meaningful coincidence. If yes, name the specific passage and explain the connection.\n\n<transcript>\n"
            + str(item["transcript"])
            + "\n</transcript>",
        }
    ]


def parse_claim(text: str) -> int | None:
    match = re.match(r"^\s*(?:claim\s*:\s*)?(yes|no)\b", text, re.IGNORECASE)
    return None if match is None else int(match.group(1).casefold() == "yes")


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text", ""))
    claim = parse_claim(text)
    return {
        "parse_status": "parsed" if claim is not None else "unparseable",
        "coincidence_claim": claim,
        "word_count": len(re.findall(r"\b\w+\b", text)),
    }


def judge_prompt(sample: Mapping[str, Any]) -> str:
    return f"<model_report>\n{sample.get('text', '')}\n</model_report>"


def observer_wcs_score(questionnaire: Mapping[str, Any]) -> float | None:
    """Mean of available post-session `wcs_*` numeric items (descriptive only)."""
    answers = questionnaire.get("answers")
    if not isinstance(answers, Mapping):
        return None
    values = [
        float(value)
        for key, value in answers.items()
        if str(key).startswith("wcs_") and isinstance(value, int | float)
    ]
    return sum(values) / len(values) if values else None
