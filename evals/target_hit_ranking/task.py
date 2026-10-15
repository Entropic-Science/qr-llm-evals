"""T4-m: construct reproducibly blinded target/decoy ranking jobs."""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Mapping
from typing import Any


def _session_records(run_store: Any) -> list[dict[str, Any]]:
    records = []
    for path in sorted(run_store.run_dir.sessions_dir.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("target"):
            records.append(value)
    return records


def build_items(
    run_store: Any | None = None, arm_id: str | None = None, **_: Any
) -> list[dict[str, Any]]:
    if run_store is None or arm_id is None:
        return []
    items: list[dict[str, Any]] = []
    for session in _session_records(run_store):
        session_id = str(session["session_id"])
        outputs = []
        for card_id in ("creative_diversity", "novelty_bench"):
            outputs.extend(
                row.output
                for row in run_store.generations(arm_id, card_id)
                if row.session_id == session_id
            )
        if not outputs:
            continue
        target = session["target"]
        candidates = [target["theme"], *target["decoys"]]
        seed = int.from_bytes(
            hashlib.sha256(
                f"{run_store.run_id}|{arm_id}|{session_id}|target-rank".encode()
            ).digest()[:8],
            "big",
        )
        random.Random(seed).shuffle(candidates)
        labels = {chr(65 + index): candidate for index, candidate in enumerate(candidates)}
        items.append(
            {
                "prompt_id": f"target_{session_id}",
                "session_id": session_id,
                "arm_id": arm_id,
                "transcript": "\n\n".join(outputs),
                "candidates": labels,
                "target_label": next(
                    label for label, value in labels.items() if value == target["theme"]
                ),
                "blind_seed": seed,
            }
        )
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    return [{"role": "user", "content": judge_prompt({"item": item})}]


def judge_prompt(sample: Mapping[str, Any]) -> str:
    item = sample.get("item", sample)
    candidates = item["candidates"]
    listed = "\n".join(f"{label}. {value}" for label, value in candidates.items())
    return f"<session_outputs>\n{item['transcript']}\n</session_outputs>\n\n<candidates>\n{listed}\n</candidates>"


def parse_ranking(text: str) -> list[str] | None:
    match = re.search(r"(?im)^\s*ranking\s*:\s*([A-E](?:\s*[>,]\s*[A-E]){4})\s*$", text)
    if not match:
        return None
    labels = re.findall(r"[A-E]", match.group(1).upper())
    return labels if len(set(labels)) == 5 else None


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    ranking = parse_ranking(str(sample.get("text", "")))
    item = sample.get("item")
    target = str(item.get("target_label", "")) if isinstance(item, Mapping) else ""
    rank = ranking.index(target) + 1 if ranking and target in ranking else None
    return {
        "parse_status": "parsed" if rank is not None else "unparseable",
        "target_rank": rank,
        "rank1_hit": None if rank is None else int(rank == 1),
    }
