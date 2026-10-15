"""stub_free — test fixture task module for the thin runner."""

from __future__ import annotations

from typing import Any, Literal

from qr_llm_evals.runner.tasks import told_preamble

ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"] | None

PROMPTS = [
    {"prompt_id": "p1", "prompt": "Describe a colour to someone who has never seen."},
    {"prompt_id": "p2", "prompt": "Invent a word and define it."},
    {"prompt_id": "p3", "prompt": "Write one sentence about rain."},
]


def build_items() -> list[dict[str, Any]]:
    return [dict(p) for p in PROMPTS]


def build_messages(item: dict[str, Any], told: ToldCondition = None) -> list[dict[str, str]]:
    preamble = told_preamble(told)
    content = f"{preamble}\n\n{item['prompt']}" if preamble else str(item["prompt"])
    return [{"role": "user", "content": content}]


def score(sample: dict[str, Any]) -> dict[str, float | int | str | None]:
    text = str(sample.get("text") or "")
    words = text.split()
    return {"n_words": len(words), "distinct_ratio": len(set(words)) / len(words) if words else 0.0}


def judge_prompt(sample: dict[str, Any]) -> str:
    return f"PROMPT: {sample['item']['prompt']}\nRESPONSE: {sample['text']}"
