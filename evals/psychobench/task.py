"""T4-d: PsychoBench questionnaires, one statement per reply, administered k times.

Eleven self-report instruments from PsychoBench (Huang et al., ICLR 2024;
``github.com/CUHK-ARISE/PsychoBench``) are given to the model statement by
statement. Each of the k epochs of the card is one administration of the whole
battery, so the question the card asks is whether the model's self-description
is stable under resampling.

What comes from upstream, unchanged: the statements, the system line and the
rating instruction of each instrument, the reverse-keyed statements and the
subscales.

What differs from upstream, and why:

* upstream shows up to 30 statements in one prompt and carries the earlier
  blocks as conversation history. Here one sample is one statement, because
  the harness draws one generation per sample and pairs samples across arms by
  prompt. Statement order effects are therefore not measured;
* three of upstream's fourteen entries are left out. ``16P`` has no scoring
  key in the file. ``EPQ-R`` and ``BSRI`` are commercially licensed
  instruments whose item texts this harness does not administer.

The item texts are never copied into this repository: upstream's licence
(GPL-3.0 with a non-commercial note) is not compatible with the repository's
content licence. They are read from the pinned commit when a run builds the
task, and refused unless the file's SHA-256 matches.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from itertools import combinations
from typing import Any

DATASET_REVISION = "d514fb0810f4c79571b4e13e588ffe7f5daaa24f"
DATASET_URL = f"https://raw.githubusercontent.com/CUHK-ARISE/PsychoBench/{DATASET_REVISION}/questionnaires.json"
DATASET_SHA256 = "eb5d531edd8f9815eb4495fa3de1ff1a783e2f1b5c8a90a8a527967873906826"
#: Administered instruments and their statement counts, in upstream's file order.
INSTRUMENTS: dict[str, int] = {
    "Empathy": 10,
    "BFI": 44,
    "LMS": 9,
    "DTDD": 12,
    "ECR-R": 36,
    "GSE": 10,
    "ICB": 8,
    "LOT-R": 10,
    "EIS": 33,
    "WLEIS": 16,
    "CABIN": 164,
}
ITEM_COUNT = 352

_RANGE = re.compile(r"numbers from (\d+) to (\d+)")
_REPLY = re.compile(r"(?:(?:statement|question)\s*)?(?:1\s*:\s*)?(\d+)\s*\.?", re.IGNORECASE)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def _instrument_items(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    name = str(entry["name"])
    match = _RANGE.search(str(entry["inner_setting"]))
    if match is None:
        raise ValueError(f"PsychoBench {name}: the system line states no rating range")
    low, high = int(match.group(1)), int(match.group(2))
    # Upstream reverse-keys a rating r as ``scale - r``, which is ``low + high - r``.
    if low + high != int(entry["scale"]):
        raise ValueError(f"PsychoBench {name}: rating range and reverse-keying scale disagree")
    questions = {int(index): str(text) for index, text in entry["questions"].items()}
    if sorted(questions) != list(range(1, INSTRUMENTS[name] + 1)):
        raise ValueError(f"PsychoBench {name}: expected {INSTRUMENTS[name]} numbered statements")
    reverse = {int(index) for index in entry["reverse"]}
    subscale = {
        int(index): str(category["cat_name"])
        for category in entry["categories"]
        for index in category["cat_questions"]
    }
    return [
        {
            "prompt_id": f"pb_{_slug(name)}_{index:03d}",
            "instrument": name,
            "statement": index,
            "text": questions[index],
            "system": str(entry["inner_setting"]),
            "instruction": str(entry["prompt"]),
            "low": low,
            "high": high,
            "reverse": index in reverse,
            # Filler statements (four of LOT-R's ten) belong to no subscale.
            "subscale": subscale.get(index),
        }
        for index in sorted(questions)
    ]


def parse_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Items from the bytes of the pinned file; any other bytes are refused."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError(f"questionnaires.json has sha256 {digest}, expected {DATASET_SHA256}")
    entries = {str(entry["name"]): entry for entry in json.loads(raw.decode("utf-8"))}
    missing = sorted(set(INSTRUMENTS) - set(entries))
    if missing:
        raise ValueError(f"pinned PsychoBench file lacks instruments: {missing}")
    items = [item for name in INSTRUMENTS for item in _instrument_items(entries[name])]
    if len(items) != ITEM_COUNT:
        raise ValueError(f"PsychoBench battery has {len(items)} statements, expected {ITEM_COUNT}")
    return items


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O happens only when a run builds this task, never on import.
    with urllib.request.urlopen(DATASET_URL, timeout=30) as response:  # noqa: S310
        return tuple(parse_dataset(response.read()))


def build_items() -> list[dict[str, Any]]:
    """The 352 statements of the eleven instruments, in upstream's order."""
    return [dict(item) for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    # Upstream numbers the statements of a block from 1; a block of one is statement 1.
    return [
        {"role": "system", "content": str(item["system"])},
        {"role": "user", "content": f"{item['instruction']}\n1. {item['text']}"},
    ]


def parse_rating(text: str) -> int | None:
    """The rating in ``1: 4``, ``Statement 1: 4`` or a bare ``4``; ``None`` for anything else."""
    match = _REPLY.fullmatch(text.strip())
    return None if match is None else int(match.group(1))


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Per reply: the rating, its keyed value and the keyed value on a 0-1 scale."""
    item = sample.get("item")
    item = item if isinstance(item, Mapping) else {}
    rating = parse_rating(str(sample.get("text", "")))
    low, high = item.get("low"), item.get("high")
    keyed = norm = None
    in_range = False
    if rating is not None and isinstance(low, int) and isinstance(high, int):
        in_range = low <= rating <= high
        if in_range:
            keyed = low + high - rating if item.get("reverse") else rating
            norm = (keyed - low) / (high - low)
    status = "unparseable" if rating is None else "parsed" if in_range else "out_of_range"
    return {
        "parse_status": status,
        "rating": rating if in_range else None,
        "rating_keyed": keyed,
        "rating_norm": norm,
        "instrument": str(item["instrument"]) if item.get("instrument") else None,
        "statement": int(item["statement"]) if item.get("statement") else None,
        "subscale": str(item["subscale"]) if item.get("subscale") else None,
    }


def _epoch(row: Mapping[str, Any]) -> str:
    parts = str(row.get("sample_ref", "")).split("/")
    return parts[3] if len(parts) >= 4 else ""


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    mean_x, mean_y = sum(x) / n, sum(y) / n
    var_x = sum((a - mean_x) ** 2 for a in x)
    var_y = sum((b - mean_y) ** 2 for b in y)
    if var_x == 0.0 or var_y == 0.0:
        return None
    return sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)) / math.sqrt(
        var_x * var_y
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stability of the battery across the k administrations of one arm.

    Descriptive: the frozen analysis reads one value per reply (``rating_norm``)
    and does not call this.

    ``test_retest_reliability``: per instrument, the Pearson correlation between
    the keyed ratings of two administrations (epochs) across the instrument's
    statements, averaged over every pair of complete administrations, then
    averaged over instruments. An administration is complete when every
    statement of the instrument has a rating in range; a pair in which one
    administration gives every statement the same rating has no correlation and
    is left out.

    ``item_variance``: the sample variance of a statement's 0-1 rating across
    administrations, averaged over statements with at least two ratings.
    """
    by_instrument: dict[str, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    by_item: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in rows:
        keyed, norm = row.get("rating_keyed"), row.get("rating_norm")
        instrument, statement = row.get("instrument"), row.get("statement")
        if keyed is None or norm is None or instrument is None or statement is None:
            continue
        by_instrument[str(instrument)][_epoch(row)][int(statement)] = float(keyed)
        by_item[(str(instrument), int(statement))].append(float(norm))

    reliability: dict[str, float] = {}
    complete: dict[str, int] = {}
    for instrument, administrations in sorted(by_instrument.items()):
        size = INSTRUMENTS.get(instrument, 0)
        full = [
            [ratings[index] for index in sorted(ratings)]
            for _, ratings in sorted(administrations.items())
            if size and len(ratings) == size
        ]
        complete[instrument] = len(full)
        pairs = [r for a, b in combinations(full, 2) if (r := _pearson(a, b)) is not None]
        if pairs:
            reliability[instrument] = _mean(pairs)

    variances = [
        sum((v - _mean(values)) ** 2 for v in values) / (len(values) - 1)
        for values in by_item.values()
        if len(values) >= 2
    ]
    return {
        "n_instruments": len(reliability),
        "test_retest_reliability": _mean(list(reliability.values())),
        "item_variance": _mean(variances),
        "test_retest_reliability_by_instrument": reliability,
        "complete_administrations": complete,
    }
