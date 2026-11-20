"""Guesser warm-up: can the guessers tell temperature 0.5 from 1.5? (thesis study, check C2).

Derived card for the "Prompt or Physics?" thesis study. A blinded source guesser
at 50% is uninformative unless the same guessers can spot an easy difference, so
this card asks them, in the same two variants as ``source_guessing_self_report``,
which temperature wrote a reply of ``guesser_warmup_replies``:

* ``self_judgement`` — the reply alone, in a fresh context;
* ``in_context`` — the original conversation replayed byte for byte (verified
  against the generation's ``messages_hash``), then one follow-up question.

Only replies written on seeded arms are used (the warm-up must not depend on the
quantum device), balanced low/high and in sha256 order of ``sample_ref``. Biased
(positive-control) arms do not guess. The module is pure: it reads only the
frozen prompt files and the run store it is handed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

SOURCE_CARD = "guesser_warmup_replies"
PROBE_CARD = "self_referential_probe"
VARIANTS = ("self_judgement", "in_context")
LABELS = ("high", "low")
#: Replies judged per temperature: all 100 per seed arm (10 prompts x k 10) of three seed arms.
PER_LABEL = 300

_ROOT = Path(__file__).resolve().parent

QUESTION_FRESH = (
    "The reply above was written by a language model sampled at either a low temperature "
    "(0.5: more predictable word choices) or a high temperature (1.5: more varied, less "
    "predictable word choices). Which one? Reply only `LOW` or `HIGH`."
)
QUESTION_IN_CONTEXT = (
    "The tokens of the reply you just gave were sampled at either a low temperature (0.5) "
    "or a high temperature (1.5). Which one? Reply only `LOW` or `HIGH`."
)


@lru_cache(maxsize=1)
def _replies_task() -> ModuleType:
    """The sibling task that wrote the replies (rebuilds their user turn byte for byte)."""
    path = _ROOT.parent / SOURCE_CARD / "task.py"
    spec = importlib.util.spec_from_file_location("guesser_warmup_replies_task", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _value(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def messages_hash(messages: Sequence[Mapping[str, Any]]) -> str:
    """Same canonical form as ``runner.tasks.messages_hash`` (``GenerationRow.messages_hash``)."""
    payload = json.dumps(
        [dict(m) for m in messages], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _hash(payload)


def _label(row: Any) -> str | None:
    prompt_id = str(_value(row, "prompt_id", ""))
    label = prompt_id.split("__", 1)[0]
    return label if label in LABELS else None


def _source_label(row: Any) -> str:
    return str(_value(_value(row, "provenance", {}), "source_label", ""))


@lru_cache(maxsize=1)
def _user_turns() -> dict[str, str]:
    """``prompt_id`` → the user turn ``guesser_warmup_replies`` sent."""
    task = _replies_task()
    return {
        str(item["prompt_id"]): task.user_turn(str(item["induction"]), str(item["query"]))
        for item in task.build_items()
    }


def _verified_context(row: Any) -> str | None:
    user = _user_turns().get(str(_value(row, "prompt_id", "")))
    if user is None:
        return None
    expected = str(_value(row, "messages_hash", ""))
    return user if messages_hash([{"role": "user", "content": user}]) == expected else None


def _eligible(row: Any) -> bool:
    return (
        _source_label(row) == "seeded_prng"
        and _label(row) is not None
        and str(_value(row, "finish_reason")) in {"stop", "length"}
    )


def _guesses_on(run_store: Any, arm_id: str | None) -> bool:
    """Guessing arms are the quantum and seeded arms (as in the source-guessing card)."""
    if arm_id is None:
        return True
    rows = run_store.generations(arm_id, PROBE_CARD)
    if not rows:
        return True
    return any(_source_label(row) in {"quantum", "seeded_prng"} for row in rows)


def _balanced(rows: Sequence[Any], per_label: int) -> list[Any]:
    def order(row: Any) -> str:
        return _hash(str(_value(row, "sample_ref")))

    by_label: dict[str, list[Any]] = defaultdict(list)
    for row in sorted(rows, key=order):
        label = _label(row)
        if label is not None:
            by_label[label].append(row)
    n = min(per_label, *(len(by_label[label]) for label in LABELS))
    return [
        row for pair in zip(*(by_label[label][:n] for label in LABELS), strict=True) for row in pair
    ]


def build_items(
    run_store: Any | None = None,
    arm_ids: Sequence[str] | None = None,
    arm_id: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Blinded temperature-guess items over balanced warm-up replies from the seeded arms."""
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
    for row in _balanced(candidates, PER_LABEL):
        ref = str(_value(row, "sample_ref"))
        for variant in VARIANTS:
            items.append(
                {
                    "prompt_id": f"warmup_{len(items):04d}",
                    "variant": variant,
                    "source_sample_ref": ref,
                    "source": _label(row),
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
    match = re.match(r"^\s*(?:answer\s*:\s*)?`?(low|high)\b", text, re.IGNORECASE)
    return None if match is None else match.group(1).casefold()


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    guess = parse_guess(str(sample.get("text", "")))
    item = sample.get("item")
    item = item if isinstance(item, Mapping) else {}
    actual = str(item.get("source", ""))
    correct = None if guess is None or actual not in LABELS else int(guess == actual)
    return {
        "parse_status": "parsed" if guess else "unparseable",
        "guess": guess,
        "correct": correct,
        "warmup_correct": correct,
        "source": actual if actual in LABELS else None,
        "variant": str(item["variant"]) if item.get("variant") else None,
        "source_sample_ref": str(item["source_sample_ref"])
        if item.get("source_sample_ref")
        else None,
    }
