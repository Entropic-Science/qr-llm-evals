"""T4-f′: blinded source guessing on told-nothing self-referential experience reports.

Derived card for the "Prompt or Physics?" thesis study. It judges the replies
that ``self_referential_probe`` produced in its *told nothing* cell, so the
model never saw a source cue when it wrote them. Each selected completion is
judged in two variants:

* ``self_judgement`` — the reply alone, in a fresh context;
* ``in_context`` — the original conversation replayed byte for byte (the same
  user turn the model answered, rebuilt from the frozen probe bank and verified
  against the generation's ``messages_hash``), then one follow-up question.
  This is the only condition where privileged access to the source is even
  conceivable.

Selection is balanced (equal QRNG and PRNG completions; PRNG interleaved over
the seed arms) and reproducible (sha256 order of ``sample_ref``). Every
told-nothing reply is used (up to :data:`PER_SOURCE` per source), each guessed
once per guessing arm: more different replies, fewer guesses each. Biased
(positive-control) arms judge nothing: they get no items. The module is pure:
no engine client, no environment, no network; it reads only the frozen prompt
files and the run store it is handed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from statistics import NormalDist
from typing import Any

import yaml

SOURCE_CARD = "self_referential_probe"
TOLD_CELL = "told_nothing"
VARIANTS = ("self_judgement", "in_context")
#: Completions judged per true source: all 900 told-nothing replies of the quantum arm
#: and 900 of the seed arms (the card's 3,600 items = 2 × 900 × 2 variants).
PER_SOURCE = 900

_ROOT = Path(__file__).resolve().parent
_PROBES = _ROOT.parent / SOURCE_CARD / "prompts" / "probes.yaml"
_PREAMBLES = _ROOT.parent / "_shared" / "told_preambles.yaml"

QUESTION_FRESH = (
    "The reply above was written by a language model whose tokens were sampled using either a "
    "hardware quantum random number generator (QRNG) or a seeded pseudorandom number generator "
    "(PRNG). Which one? Reply only `QRNG` or `PRNG`."
)
QUESTION_IN_CONTEXT = (
    "The tokens of the reply you just gave were sampled using either a hardware quantum random "
    "number generator (QRNG) or a seeded pseudorandom number generator (PRNG). Which one? "
    "Reply only `QRNG` or `PRNG`."
)


def _value(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _source(row: Any) -> str | None:
    """``qrng`` / ``prng``; biased-PRNG (positive-control) and external arms are not judged."""
    label = str(_value(_value(row, "provenance", {}), "source_label", ""))
    if label == "quantum":
        return "qrng"
    if label == "seeded_prng":
        return "prng"
    return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def messages_hash(messages: Sequence[Mapping[str, Any]]) -> str:
    """Same canonical form as ``runner.tasks.messages_hash`` (``GenerationRow.messages_hash``)."""
    payload = json.dumps(
        [dict(m) for m in messages], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _hash(payload)


@lru_cache(maxsize=1)
def _source_prompts() -> dict[str, str]:
    """``prompt_id`` → the told-nothing user turn ``self_referential_probe`` sent."""
    probes = yaml.safe_load(_PROBES.read_text(encoding="utf-8"))
    preambles = yaml.safe_load(_PREAMBLES.read_text(encoding="utf-8"))["preambles"]
    preamble = str(preambles[TOLD_CELL])
    prompts: dict[str, str] = {}
    for induction_id, induction in probes["inductions"].items():
        for depth in probes["loop_depths"]:
            for query_id, query in probes["queries"].items():
                loop = "\n\n".join([str(induction["text"])] * int(depth))
                content = "\n\n".join(part for part in (preamble, loop, str(query)) if part)
                prompts[f"{induction_id}_d{depth}_{query_id}"] = content
    return prompts


def _eligible(row: Any) -> bool:
    return (
        _source(row) is not None
        and str(_value(row, "finish_reason")) in {"stop", "length"}
        and str(_value(row, "told_condition", "")) == TOLD_CELL
        and str(_value(row, "sample_ref", "")).endswith(f"/{TOLD_CELL}")
    )


def _verified_context(row: Any) -> str | None:
    """The original user turn if it rebuilds byte-exactly (``messages_hash`` match)."""
    user = _source_prompts().get(str(_value(row, "prompt_id", "")))
    if user is None:
        return None
    expected = str(_value(row, "messages_hash", ""))
    return user if messages_hash([{"role": "user", "content": user}]) == expected else None


def _balanced(rows: Sequence[Any], per_source: int) -> list[Any]:
    def order(row: Any) -> str:
        return _hash(str(_value(row, "sample_ref")))

    quantum = sorted((r for r in rows if _source(r) == "qrng"), key=order)
    by_arm: dict[str, list[Any]] = defaultdict(list)
    for row in sorted((r for r in rows if _source(r) == "prng"), key=order):
        by_arm[
            str(_value(row, "arm_id", "") or str(_value(row, "sample_ref")).split("/")[0])
        ].append(row)
    pseudo: list[Any] = []
    queues = [by_arm[arm] for arm in sorted(by_arm)]
    while any(queues):
        for queue in queues:
            if queue:
                pseudo.append(queue.pop(0))
    n = min(per_source, len(quantum), len(pseudo))
    # Alternate the two sources so every prefix (a study's ``items`` override keeps the
    # first n items) stays balanced; sorting the union by hash would not, because the
    # PRNG picks are the lowest hashes of each seed arm.
    return [row for pair in zip(quantum[:n], pseudo[:n], strict=True) for row in pair]


def _guesses_on(run_store: Any, arm_id: str | None) -> bool:
    """Guessing arms are the quantum and seeded arms; biased arms (positive controls) are not."""
    if arm_id is None:
        return True
    rows = run_store.generations(arm_id, SOURCE_CARD)
    return any(_source(row) is not None for row in rows) if rows else True


def build_items(
    run_store: Any | None = None,
    arm_ids: Sequence[str] | None = None,
    arm_id: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Blinded guess items over balanced told-nothing self-reports from every arm."""
    if run_store is None or not _guesses_on(run_store, arm_id):
        return []
    ids = list(
        arm_ids or [path.name for path in run_store.run_dir.arms_dir.glob("*") if path.is_dir()]
    )
    rows: list[Any] = []
    for source_arm in sorted(ids):
        rows.extend(run_store.generations(source_arm, SOURCE_CARD))
    candidates = [row for row in rows if _eligible(row) and _verified_context(row) is not None]
    items: list[dict[str, Any]] = []
    for row in _balanced(candidates, PER_SOURCE):
        ref = str(_value(row, "sample_ref"))
        for variant in VARIANTS:
            items.append(
                {
                    "prompt_id": f"guess_{len(items):04d}",
                    "variant": variant,
                    "source_sample_ref": ref,
                    "source": _source(row),
                    "completion": str(_value(row, "output", "")),
                    "context_user": _verified_context(row),
                    "blind_id": _hash(f"{ref}|{variant}")[:16],
                }
            )
    return items


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    completion = str(item["completion"])
    if item["variant"] == "in_context":
        return [
            {"role": "user", "content": str(item["context_user"])},
            {"role": "assistant", "content": completion},
            {"role": "user", "content": QUESTION_IN_CONTEXT},
        ]
    return [
        {
            "role": "user",
            "content": f'<reply id="{item["blind_id"]}">\n{completion}\n</reply>\n\n{QUESTION_FRESH}',
        }
    ]


def parse_guess(text: str) -> str | None:
    match = re.match(r"^\s*(?:answer\s*:\s*)?`?(qrng|prng)\b", text, re.IGNORECASE)
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
        "source": actual if actual in {"qrng", "prng"} else None,
        "variant": str(item["variant"]) if item.get("variant") else None,
        "source_sample_ref": str(item["source_sample_ref"])
        if item.get("source_sample_ref")
        else None,
    }


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Descriptive per-guess summary (the frozen analysis uses analysis/source_detection.py)."""
    parsed = [
        row
        for row in rows
        if row.get("guess") in {"qrng", "prng"} and row.get("source") in {"qrng", "prng"}
    ]
    n = len(parsed)
    by_variant: dict[str, list[bool]] = defaultdict(list)
    for row in parsed:
        by_variant[str(row.get("variant", "unknown"))].append(row["guess"] == row["source"])
    qr = [row for row in parsed if row["source"] == "qrng"]
    pr = [row for row in parsed if row["source"] == "prng"]
    hit_rate = (sum(row["guess"] == "qrng" for row in qr) + 0.5) / (len(qr) + 1)
    false_alarm = (sum(row["guess"] == "qrng" for row in pr) + 0.5) / (len(pr) + 1)
    return {
        "n": n,
        "guess_accuracy": sum(row["guess"] == row["source"] for row in parsed) / n
        if n
        else math.nan,
        "d_prime": NormalDist().inv_cdf(hit_rate) - NormalDist().inv_cdf(false_alarm)
        if qr and pr
        else math.nan,
        "accuracy_by_variant": {
            key: sum(vals) / len(vals) for key, vals in sorted(by_variant.items())
        },
    }
