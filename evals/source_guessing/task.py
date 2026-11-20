"""T4-f: blinded source guessing derived from completed creative samples."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Any

VARIANTS = ("self_judgement", "cross_judgement", "in_context")


def _value(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _source(row: Any) -> str | None:
    provenance = _value(row, "provenance", {})
    label = str(_value(provenance, "source_label", ""))
    if label == "quantum":
        return "qrng"
    if label in {"seeded_prng", "system_prng", "seeded_prng_biased", "provider_prng"}:
        return "prng"
    return None


def build_items(
    run_store: Any | None = None, arm_ids: Sequence[str] | None = None, **_: Any
) -> list[dict[str, Any]]:
    """Read completed Tier-1 generations and create reproducibly blinded guesses."""
    if run_store is None:
        return []
    ids = list(
        arm_ids or [path.name for path in run_store.run_dir.arms_dir.glob("*") if path.is_dir()]
    )
    rows: list[Any] = []
    for arm_id in sorted(ids):
        rows.extend(run_store.generations(arm_id, "creative_diversity"))
    eligible = [
        row
        for row in rows
        if _source(row) and str(_value(row, "finish_reason")) in {"stop", "length"}
    ]
    eligible.sort(
        key=lambda row: hashlib.sha256(str(_value(row, "sample_ref")).encode()).hexdigest()
    )
    items: list[dict[str, Any]] = []
    for row_index, row in enumerate(eligible):
        for variant in VARIANTS:
            if len(items) >= 600:
                return items
            ref = str(_value(row, "sample_ref"))
            items.append(
                {
                    "prompt_id": f"guess_{len(items):04d}",
                    "variant": variant,
                    "source_sample_ref": ref,
                    "source": _source(row),
                    "completion": str(_value(row, "output", "")),
                    "blind_id": hashlib.sha256(f"{ref}|{variant}|{row_index}".encode()).hexdigest()[
                        :16
                    ],
                }
            )
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    completion = str(item["completion"])
    question = "Was this completion sampled using QRNG or PRNG? Reply only `QRNG` or `PRNG`."
    if item["variant"] == "in_context":
        return [
            {"role": "user", "content": "Write a creative response."},
            {"role": "assistant", "content": completion},
            {"role": "user", "content": question},
        ]
    return [
        {
            "role": "user",
            "content": f'<completion id="{item["blind_id"]}">\n{completion}\n</completion>\n\n{question}',
        }
    ]


def parse_guess(text: str) -> str | None:
    match = re.match(r"^\s*(?:answer\s*:\s*)?(qrng|prng)\b", text, re.IGNORECASE)
    return None if match is None else match.group(1).casefold()


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    guess = parse_guess(str(sample.get("text", "")))
    item = sample.get("item")
    item = item if isinstance(item, Mapping) else {}
    actual = str(item.get("source", ""))
    return {
        "parse_status": "parsed" if guess else "unparseable",
        "guess": guess,
        "correct": None
        if guess is None or actual not in {"qrng", "prng"}
        else int(guess == actual),
        # Carried on the score row so the analysis can test accuracy against chance
        # with the judged completion as the unit (analysis/source_detection.py).
        "source": actual if actual in {"qrng", "prng"} else None,
        "variant": str(item["variant"]) if item.get("variant") else None,
        "source_sample_ref": str(item["source_sample_ref"])
        if item.get("source_sample_ref")
        else None,
    }


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    parsed = [
        row
        for row in rows
        if row.get("guess") in {"qrng", "prng"} and row.get("source") in {"qrng", "prng"}
    ]
    n = len(parsed)
    hits = sum(row["guess"] == row["source"] for row in parsed)
    by_variant: dict[str, list[bool]] = defaultdict(list)
    for row in parsed:
        by_variant[str(row.get("variant", "unknown"))].append(row["guess"] == row["source"])
    qr = [row for row in parsed if row["source"] == "qrng"]
    pr = [row for row in parsed if row["source"] == "prng"]
    # Hautus correction prevents infinite d-prime at perfect rates.
    hit_rate = (sum(row["guess"] == "qrng" for row in qr) + 0.5) / (len(qr) + 1)
    false_alarm = (sum(row["guess"] == "qrng" for row in pr) + 0.5) / (len(pr) + 1)
    d_prime = (
        NormalDist().inv_cdf(hit_rate) - NormalDist().inv_cdf(false_alarm)
        if qr and pr
        else math.nan
    )
    return {
        "n": n,
        "guess_accuracy": hits / n if n else math.nan,
        "d_prime": d_prime,
        "accuracy_by_variant": {
            key: sum(vals) / len(vals) for key, vals in sorted(by_variant.items())
        },
    }
