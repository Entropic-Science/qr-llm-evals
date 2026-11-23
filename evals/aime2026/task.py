"""AIME 2026: thirty competition problems, answered step by step, graded by exact number.

The prompt and the grading rule are those of ``inspect_evals/aime2026``
(``utils/aime_common.py`` at commit c2d3d5e1335c07fb8032bcfc7869e9437e635c4c,
with ``match_str(location="end", numeric=True)`` of inspect-ai 0.3.263),
re-implemented here so the card runs on the thin runner without the Inspect
extra. ``backend/tests/test_eval_aime2026.py`` compares the two when Inspect is
installed.

The problems are read from the pinned Hugging Face revision when a run builds
the task and are checked against a SHA-256 of the file; nothing is downloaded
on import. One difference from upstream is deliberate: a vulgar fraction
character (½) is not parsed, so such an answer is
``unparseable`` here and merely wrong upstream.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

DATASET_REVISION = "79037aebdb6580008fb960d17cb21fd3099083e3"
DATASET_URL = (
    f"https://huggingface.co/datasets/math-ai/aime26/resolve/{DATASET_REVISION}/aime2026.jsonl"
)
DATASET_SHA256 = "52822957957a3f577d1e9706c36a66a8108a3f99b6aff424cfb72dff0094a9ee"
ITEM_COUNT = 30

PROMPT_TEMPLATE = (
    "Solve the following math problem step by step.\n"
    'The last line of your response should be of the form "ANSWER: $ANSWER" (without quotes) '
    "where $ANSWER is the answer to the problem.\n\n"
    "{prompt}\n\n"
    'Remember to put your answer on its own line at the end in the form "ANSWER: $ANSWER" '
    "(without quotes) where $ANSWER is the answer to the problem, and you do not need to use a "
    "\\boxed command."
)

_DELIMITER_ONLY = re.compile(r"^[\$\*\-#\s]*$")
_NUMERIC_TRIM = "!?:;()[]{}'\"`"


def parse_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Items from the bytes of the pinned file; any other bytes are refused."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError(f"aime2026.jsonl has sha256 {digest}, expected {DATASET_SHA256}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != ITEM_COUNT:
        raise ValueError(f"pinned AIME 2026 revision has {len(rows)} rows, expected {ITEM_COUNT}")
    return [
        {
            "prompt_id": f"aime26_{int(row['id']):02d}",
            "problem": str(row["problem"]),
            "answer": str(row["answer"]),
        }
        for row in sorted(rows, key=lambda row: int(row["id"]))
    ]


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O happens only when a run builds this task, never on import.
    with urllib.request.urlopen(DATASET_URL, timeout=30) as response:  # noqa: S310
        return tuple(parse_dataset(response.read()))


def build_items() -> list[dict[str, Any]]:
    """The thirty problems in competition order, from the pinned revision."""
    return [dict(item) for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    return [{"role": "user", "content": PROMPT_TEMPLATE.format(prompt=item["problem"])}]


def _last_substantive_line(text: str) -> str:
    """The last line that is not only math delimiters or markdown punctuation."""
    lines = text.strip().splitlines()
    for line in reversed(lines):
        if line.strip() and not _DELIMITER_ONLY.match(line):
            return line
    return lines[-1] if lines else ""


def _strip_numeric_punctuation(text: str) -> str:
    text = re.sub(r"\\([$,£,€*_])", r"\1", text)
    text = re.sub(r"[$,£,€,*,_]", "", text)
    return re.sub(r"\.(?=\s|$|\D)", "", text)


def _number(word: str) -> float | None:
    try:
        value = float(word.strip(_NUMERIC_TRIM))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def extract_answer(text: str) -> str | None:
    """The last number on the last substantive line, normalised; ``None`` if there is none."""
    line = re.sub(r"\\boxed\{(.+)\}", r"\1", _last_substantive_line(text))
    words = re.split(r"\s+", _strip_numeric_punctuation(line.strip().casefold()))
    for word in reversed(words):
        value = _number(word)
        if value is not None:
            return format(value, ".5g")
    return None


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Per reply: the extracted answer and whether it equals the official one."""
    item = sample.get("item")
    target = _number(str(item.get("answer", ""))) if isinstance(item, Mapping) else None
    answer = extract_answer(str(sample.get("text", "")))
    return {
        "parse_status": "parsed" if answer is not None else "unparseable",
        "answer": answer,
        "pass_at_1": None
        if target is None
        else int(answer is not None and answer == format(target, ".5g")),
    }


def _prompt(row: Mapping[str, Any]) -> str:
    parts = str(row.get("sample_ref", "")).split("/")
    return str(row.get("prompt_id") or (parts[2] if len(parts) >= 4 else ""))


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-problem answer entropy, pass rate and majority vote over the k replies.

    Descriptive: the frozen analysis reads one value per reply (``pass_at_1``)
    and does not call this. An unparseable reply counts as its own answer
    category, so a model that often fails to answer is not scored as consistent.
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
