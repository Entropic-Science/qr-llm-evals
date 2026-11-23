"""IFBench: 300 prompts with out-of-distribution constraints, checked by code.

Each prompt carries one or two constraints ("use the keyword in the 25th
sentence", "no word may repeat") and AI2's verifier decides, without a judge,
whether a reply satisfies them. Data and verifier both come from one commit of
``github.com/allenai/IFBench`` (Apache-2.0; arXiv 2507.02833):

* the prompts are ``ifbench/data/IFBench_test.jsonl`` at that commit, read when
  a run builds the task and refused unless the file's SHA-256 matches. The 300
  rows equal the Hugging Face copy ``allenai/IFBench_test``;
* the verifier is the ``ifbench`` package installed from the same commit (the
  ``[ifbench]`` extra). It is imported only inside ``score()``.

Importing the verifier downloads NLTK data when it is missing. ``score()``
therefore looks for that data first and reports ``unavailable`` rather than let
a download happen in the middle of a run; the same answer is given when the
package is absent or was installed from another commit. Scores are then
``None`` and can be recomputed from the stored replies.

The two scores are upstream's (``evaluation_lib.py``): *strict* checks the reply
as written; *loose* also accepts it with the first line, the last line or both
removed and with asterisks removed, and is the benchmark's headline.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

VERIFIER_COMMIT = "488918665d53f867fa908a293769ec6e71e74aec"
DATASET_URL = (
    "https://raw.githubusercontent.com/allenai/IFBench/"
    f"{VERIFIER_COMMIT}/ifbench/data/IFBench_test.jsonl"
)
DATASET_SHA256 = "a2bf7b8e7ed11d39c65b4ec781da8b99663ed0121f97033d70fcecb61f8a9d85"
ITEM_COUNT = 300
#: What ``ifbench.instructions_util`` downloads on import when it is not there.
NLTK_RESOURCES = (
    "tokenizers/punkt",
    "tokenizers/punkt_tab",
    "corpora/stopwords",
    "taggers/averaged_perceptron_tagger_eng",
)


class VerifierUnavailable(RuntimeError):
    """The pinned verifier cannot be used as installed; the message says why."""


def parse_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Items from the bytes of the pinned file; any other bytes are refused."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError(f"IFBench_test.jsonl has sha256 {digest}, expected {DATASET_SHA256}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != ITEM_COUNT:
        raise ValueError(f"pinned IFBench file has {len(rows)} rows, expected {ITEM_COUNT}")
    items: list[dict[str, Any]] = []
    for row in rows:
        ids = [str(value) for value in row["instruction_id_list"]]
        # The file states every possible argument per constraint, null where unused.
        kwargs = [
            {key: value for key, value in arguments.items() if value is not None}
            for arguments in row["kwargs"]
        ]
        if len(ids) != len(kwargs) or not ids:
            raise ValueError(f"IFBench row {row['key']!r}: constraints and arguments do not align")
        items.append(
            {
                "prompt_id": f"ifb_{row['key']}",
                "prompt": str(row["prompt"]),
                "instruction_id_list": ids,
                "kwargs": kwargs,
            }
        )
    return items


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O happens only when a run builds this task, never on import.
    with urllib.request.urlopen(DATASET_URL, timeout=30) as response:  # noqa: S310
        return tuple(parse_dataset(response.read()))


def build_items() -> list[dict[str, Any]]:
    """The 300 prompts in file order, from the pinned commit."""
    return [json.loads(json.dumps(item)) for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    del told
    # The constraint is already part of the prompt text; nothing is added.
    return [{"role": "user", "content": str(item["prompt"])}]


def _installed_commit() -> str | None:
    """The git commit the installed ``ifbench`` was built from, if pip recorded one."""
    try:
        text = importlib.metadata.distribution("ifbench").read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        info = json.loads(text or "{}").get("vcs_info", {})
    except ValueError:
        return None
    commit = info.get("commit_id") if isinstance(info, Mapping) else None
    return str(commit) if commit else None


@lru_cache(maxsize=1)
def _load_registry() -> Mapping[str, Any]:
    """``instruction id -> checker class`` of the pinned verifier, or ``VerifierUnavailable``."""
    commit = _installed_commit()
    if commit != VERIFIER_COMMIT:
        found = "not installed" if commit is None else f"installed from {commit}"
        raise VerifierUnavailable(f"ifbench is {found}; this card needs {VERIFIER_COMMIT}")
    try:
        nltk = importlib.import_module("nltk")
    except ImportError as err:
        raise VerifierUnavailable("nltk is not installed") from err
    for resource in NLTK_RESOURCES:
        try:
            nltk.data.find(resource)
        except LookupError as err:
            raise VerifierUnavailable(f"NLTK data missing: {resource}") from err
    try:
        registry = importlib.import_module("ifbench.instructions_registry").INSTRUCTION_DICT
    except ImportError as err:
        raise VerifierUnavailable(f"ifbench cannot be imported: {err}") from err
    return dict(registry)


def _checkers(item: Mapping[str, Any], registry: Mapping[str, Any]) -> list[Any]:
    """One configured checker per constraint, built the way ``evaluation_lib`` builds them."""
    checkers: list[Any] = []
    for instruction_id, kwargs in zip(item["instruction_id_list"], item["kwargs"], strict=True):
        checker = registry[instruction_id](instruction_id)
        checker.build_description(**kwargs)
        arguments = checker.get_instruction_args()
        if arguments and "prompt" in arguments:
            checker.build_description(prompt=item["prompt"])
        checkers.append(checker)
    return checkers


def loose_variants(text: str) -> list[str]:
    """The reply and the seven relaxed readings upstream's loose score accepts."""
    lines = text.split("\n")
    trimmed = [
        "\n".join(lines[1:]).strip(),
        "\n".join(lines[:-1]).strip(),
        "\n".join(lines[1:-1]).strip(),
    ]
    return [text, text.replace("*", ""), *trimmed, *(v.replace("*", "") for v in trimmed)]


def verify(item: Mapping[str, Any], text: str, registry: Mapping[str, Any]) -> dict[str, Any]:
    """Which constraints ``text`` satisfies, strictly and loosely."""
    checkers = _checkers(item, registry)
    strict = [bool(text.strip() and checker.check_following(text)) for checker in checkers]
    variants = loose_variants(text)
    loose = [
        any(variant.strip() and checker.check_following(variant) for variant in variants)
        for checker in checkers
    ]
    return {"strict": strict, "loose": loose}


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Per reply: all constraints met loosely (``pass_at_1``) and strictly (``strict_accuracy``)."""
    empty: dict[str, float | int | str | None] = {
        "pass_at_1": None,
        "strict_accuracy": None,
        "instruction_pass_rate": None,
        "n_instructions": None,
    }
    item = sample.get("item")
    if not isinstance(item, Mapping) or not item.get("instruction_id_list"):
        return {"verifier_status": "unavailable: no item", **empty}
    try:
        registry = _load_registry()
    except VerifierUnavailable as err:
        return {"verifier_status": f"unavailable: {err}", **empty}
    result = verify(item, str(sample.get("text", "")), registry)
    return {
        "verifier_status": "available",
        "pass_at_1": int(all(result["loose"])),
        "strict_accuracy": int(all(result["strict"])),
        "instruction_pass_rate": sum(result["loose"]) / len(result["loose"]),
        "n_instructions": len(result["loose"]),
    }


def _prompt(row: Mapping[str, Any]) -> str:
    parts = str(row.get("sample_ref", "")).split("/")
    return str(row.get("prompt_id") or (parts[2] if len(parts) >= 4 else ""))


def _binary_entropy(p: float) -> float:
    return -sum(x * math.log2(x) for x in (p, 1.0 - p) if x > 0.0)


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-prompt pass rate over the k replies and the entropy of pass against fail.

    Descriptive: the frozen analysis reads one value per reply (``pass_at_1``,
    ``strict_accuracy``) and does not call this. ``constraint_pass_entropy`` is
    the binary entropy, in bits, of a prompt's loose pass rate: 0 when the k
    replies all pass or all fail, 1 when half of them pass.
    """
    loose: dict[str, list[int]] = defaultdict(list)
    strict: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row.get("pass_at_1") is None or row.get("strict_accuracy") is None:
            continue
        prompt = _prompt(row)
        loose[prompt].append(int(row["pass_at_1"]))
        strict[prompt].append(int(row["strict_accuracy"]))
    n = len(loose)
    entropy = {
        prompt: _binary_entropy(sum(values) / len(values)) for prompt, values in loose.items()
    }
    return {
        "n_prompts": n,
        "constraint_pass_entropy": sum(entropy.values()) / n if n else math.nan,
        "pass_at_1": sum(sum(v) / len(v) for v in loose.values()) / n if n else math.nan,
        "strict_accuracy": sum(sum(v) / len(v) for v in strict.values()) / n if n else math.nan,
        "constraint_pass_entropy_by_prompt": dict(sorted(entropy.items())),
    }
