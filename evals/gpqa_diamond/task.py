"""GPQA Diamond: 198 graduate-level questions, answered with chain of thought.

The prompt, the fixed choice order and the answer parser are those of
``inspect_evals/gpqa_diamond`` (commit c2d3d5e1335c07fb8032bcfc7869e9437e635c4c;
``multiple_choice(cot=True)`` and ``choice()`` of inspect-ai 0.3.263),
re-implemented so the card runs on the thin runner without the Inspect extra.
``backend/tests/test_eval_gpqa_diamond.py`` compares the choice order and the
parser with Inspect's when it is installed.

The questions are read when a run builds the task, from the same public CSV
upstream uses, and refused unless the file's SHA-256 matches. They carry a
canary string and must never be copied into this repository.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import random
import re
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

DATASET_URL = "https://openaipublic.blob.core.windows.net/simple-evals/gpqa_diamond.csv"
DATASET_SHA256 = "41d1213cd7a4998605a26c2798500652572007161b3a92817ba46b35befcd305"
ITEM_COUNT = 198
#: Upstream's default: the raw file lists the correct answer first, so the
#: choices are shuffled once with a fixed seed and every arm sees the same exam.
SHUFFLE_SEED = 42
LETTERS = "ABCD"
_CHOICE_COLUMNS = (
    "Correct Answer",
    "Incorrect Answer 1",
    "Incorrect Answer 2",
    "Incorrect Answer 3",
)

PROMPT_TEMPLATE = (
    "Answer the following multiple choice question. The last line of your response should be "
    "of the following format: 'ANSWER: $LETTER' (without quotes) where LETTER is one of "
    "{letters}. Think step by step before answering.\n\n{question}\n\n{choices}"
)

_STRICT = re.compile(r"(?i)^ANSWER\s*:\s*([A-Za-z\d ,]+)\s*(?:$|\n|\.)", re.MULTILINE)
_LOOSE = re.compile(r"(?i)ANSWER\s*:\s*([A-Za-z\d ,]+)(?:[^\w]|\n|$|\.)")


def shuffled_items(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """Items in file order with the fixed choice order of ``Dataset.shuffle_choices(42)``.

    One generator is advanced through every row in file order, so the order of
    a question's choices depends on its position in the file; the rows must not
    be filtered or reordered before this call.
    """
    rand = random.Random(SHUFFLE_SEED)  # noqa: S311 - a fixed exam layout, not a secret
    items: list[dict[str, Any]] = []
    for row in rows:
        positions = [0, 1, 2, 3]
        rand.shuffle(positions)
        items.append(
            {
                "prompt_id": f"gpqa_{row['Record ID']}",
                "question": str(row["Question"]),
                "choices": [str(row[_CHOICE_COLUMNS[index]]) for index in positions],
                "target": LETTERS[positions.index(0)],
                "domain": str(row["High-level domain"]),
                "subdomain": str(row["Subdomain"]),
            }
        )
    return items


def parse_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Items from the bytes of the pinned file; any other bytes are refused."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError(f"gpqa_diamond.csv has sha256 {digest}, expected {DATASET_SHA256}")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")))
    if len(rows) != ITEM_COUNT:
        raise ValueError(f"pinned GPQA Diamond file has {len(rows)} rows, expected {ITEM_COUNT}")
    return shuffled_items(rows)


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O happens only when a run builds this task, never on import.
    with urllib.request.urlopen(DATASET_URL, timeout=60) as response:  # noqa: S310
        return tuple(parse_dataset(response.read()))


def build_items() -> list[dict[str, Any]]:
    """The 198 questions in file order, each with its fixed choice order."""
    return [{**item, "choices": list(item["choices"])} for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    choices = "\n".join(
        f"{letter}) {choice}" for letter, choice in zip(LETTERS, item["choices"], strict=True)
    )
    content = PROMPT_TEMPLATE.format(
        letters=",".join(LETTERS), question=item["question"], choices=choices
    )
    return [{"role": "user", "content": content}]


def parse_answer(text: str) -> str | None:
    """The letter of the last ``ANSWER:`` line, or ``None`` if it names no single choice."""
    matches = _STRICT.findall(text) or _LOOSE.findall(text)
    if not matches:
        return None
    matched = matches[-1].strip().rstrip(".").upper()
    tokens = [token for token in matched.split(",") if token]
    return tokens[0] if len(tokens) == 1 and tokens[0] in set(LETTERS) else None


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Per reply: the chosen letter and whether it is the correct choice."""
    item = sample.get("item")
    target = str(item.get("target", "")) if isinstance(item, Mapping) else ""
    answer = parse_answer(str(sample.get("text", "")))
    return {
        "parse_status": "parsed" if answer is not None else "unparseable",
        "answer": answer,
        "pass_at_1": int(answer == target) if target in set(LETTERS) else None,
    }


def _prompt(row: Mapping[str, Any]) -> str:
    parts = str(row.get("sample_ref", "")).split("/")
    return str(row.get("prompt_id") or (parts[2] if len(parts) >= 4 else ""))


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-question answer entropy, pass rate and majority vote over the k replies.

    Descriptive: the frozen analysis reads one value per reply (``pass_at_1``)
    and does not call this. An unparseable reply counts as its own answer
    category, so the entropy of one question is at most ``log2(5)`` bits.
    """
    answers: dict[str, list[str]] = defaultdict(list)
    passes: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row.get("pass_at_1") is None:
            continue
        prompt = _prompt(row)
        answers[prompt].append(str(row.get("answer")))
        passes[prompt].append(int(row["pass_at_1"]))
    entropy: dict[str, float] = {}
    majority: dict[str, int] = {}
    for prompt, values in answers.items():
        counts = Counter(values)
        entropy[prompt] = -sum(
            (count / len(values)) * math.log2(count / len(values)) for count in counts.values()
        )
        # A tie for the most frequent answer is not a majority.
        (top, top_count), *rest = counts.most_common(2)
        unique = not rest or rest[0][1] < top_count
        correct = {a for a, p in zip(values, passes[prompt], strict=True) if p}
        majority[prompt] = int(unique and top in correct)
    n = len(answers)
    return {
        "n_prompts": n,
        "answer_entropy": sum(entropy.values()) / n if n else math.nan,
        "pass_at_1": sum(sum(v) / len(v) for v in passes.values()) / n if n else math.nan,
        "maj_at_k": sum(majority.values()) / n if n else math.nan,
        "answer_entropy_by_prompt": dict(sorted(entropy.items())),
    }
