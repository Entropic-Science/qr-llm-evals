"""LiveCodeBench: programming problems scored by running the program on hidden tests.

The 175 problems that release v6 added to ``livecodebench/code_generation_lite``
(Jain et al., "LiveCodeBench", arXiv 2403.07974), from contests held between
January and April 2025. The prompt, the code extraction and the tests are those
of the authors' runner (``LiveCodeBench/LiveCodeBench``, commit
28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24, MIT).

The problems are read when a run builds the task and refused unless the file's
SHA-256 matches. The file is 134 MB, most of it hidden tests, which stay
encoded in memory until a reply is scored.

**Running the programs.** A reply is a program written by a model. This module
never runs it. ``score()`` hands the program and its tests to the sandbox
service over a Unix socket at a fixed path (``SANDBOX_SOCKET``), and that
service runs it under a throwaway user without network or access to the
harness's data (``infra/qr-llm-evals-sandbox@.service``,
``sandbox_worker.py``). Where the socket does not exist, a reply is not scored
and its row says ``verifier_status: unavailable``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import pickle
import socket
import time
import urllib.request
import zlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

DATASET_REVISION = "0fe84c3912ea0c4d4a78037083943e8f0c4dd505"
DATASET_URL = (
    "https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/"
    f"{DATASET_REVISION}/test6.jsonl"
)
DATASET_SHA256 = "bb4c364f71921c4495a6ad15abe1a927350b720009f4933e2e71f8af0f6fd1f5"
ITEM_COUNT = 175

#: Where the sandbox service listens (``infra/qr-llm-evals-sandbox.socket``).
SANDBOX_SOCKET = "/run/qr-llm-evals-sandbox/sandbox.sock"
#: Upstream's time limit for one test, and how long this side waits for a whole
#: verdict (the worker's own budget is shorter, so it normally answers first).
TEST_TIMEOUT_S = 6
VERDICT_TIMEOUT_S = 300.0
CONNECT_ATTEMPTS = 3

SYSTEM_PROMPT = (
    "You are an expert Python programmer. You will be given a question (problem specification) "
    "and will generate a correct Python program that matches the specification and passes all "
    "tests."
)
FORMAT_WITH_STARTER = (
    "You will use the following starter code to write the solution to the problem and enclose "
    "your code within delimiters."
)
FORMAT_WITHOUT_STARTER = (
    "Read the inputs from stdin solve the problem and write the answer to stdout (do not "
    "directly test on the sample inputs). Enclose your code within delimiters as follows. "
    "Ensure that when the python program runs, it reads the inputs, runs the algorithm and "
    "writes output to STDOUT."
)


class SandboxUnavailable(RuntimeError):
    """The sandbox service gave no verdict; the message says why."""


class _NoGlobals(pickle.Unpickler):
    """Unpickles plain values only: a pickle that names any class or function is refused."""

    def find_class(self, module: str, name: str) -> Any:
        raise pickle.UnpicklingError(f"refused to load {module}.{name}")


def parse_dataset(raw: bytes) -> list[dict[str, Any]]:
    """Problems from the bytes of the pinned file, newest contest first; other bytes are refused."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATASET_SHA256:
        raise ValueError(f"test6.jsonl has sha256 {digest}, expected {DATASET_SHA256}")
    items: list[dict[str, Any]] = []
    for line in raw.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        fn_name = json.loads(row["metadata"]).get("func_name")
        items.append(
            {
                "prompt_id": f"lcb_{row['question_id']}",
                "question": str(row["question_content"]),
                "starter_code": str(row["starter_code"]),
                "fn_name": str(fn_name) if fn_name else None,
                "platform": str(row["platform"]),
                "difficulty": str(row["difficulty"]),
                "contest_date": str(row["contest_date"])[:10],
                "public_tests": str(row["public_test_cases"]),
                "private_tests": str(row["private_test_cases"]),
            }
        )
    if len(items) != ITEM_COUNT:
        raise ValueError(
            f"pinned LiveCodeBench file has {len(items)} problems, expected {ITEM_COUNT}"
        )
    # Newest first, so a study that runs fewer problems runs the most recent ones.
    items.sort(key=lambda item: (item["contest_date"], item["prompt_id"]), reverse=True)
    return items


@lru_cache(maxsize=1)
def _download_items() -> tuple[dict[str, Any], ...]:
    # Network I/O happens only when a run builds this task, never on import.
    with urllib.request.urlopen(DATASET_URL, timeout=600) as response:  # noqa: S310
        return tuple(parse_dataset(response.read()))


def build_items() -> list[dict[str, Any]]:
    """The 175 problems, newest contest first."""
    return [dict(item) for item in _download_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    """Upstream's generic chat prompt."""
    del told
    prompt = f"### Question:\n{item['question']}\n\n"
    if item["starter_code"]:
        prompt += f"### Format: {FORMAT_WITH_STARTER}\n```python\n{item['starter_code']}\n```\n\n"
    else:
        prompt += f"### Format: {FORMAT_WITHOUT_STARTER}\n```python\n# YOUR CODE HERE\n```\n\n"
    prompt += "### Answer: (use the provided format with backticks)\n\n"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]


def extract_code(text: str) -> str:
    """The reply's last fenced block, as upstream takes it; empty when there is none."""
    lines = text.split("\n")
    fences = [index for index, line in enumerate(lines) if "```" in line]
    if len(fences) < 2:  # noqa: PLR2004 - a block needs an opening and a closing fence
        return ""
    return "\n".join(lines[fences[-2] + 1 : fences[-1]])


def decode_tests(item: Mapping[str, Any]) -> list[list[str]]:
    """``[input, expected output]`` of every public and hidden test of a problem.

    Hidden tests are JSON, or JSON inside a compressed pickle. The pickle is
    read with an unpickler that loads no class and no function.
    """
    hidden = str(item["private_tests"])
    try:
        private = json.loads(hidden)
    except ValueError:
        blob = zlib.decompress(base64.b64decode(hidden.encode("utf-8")))
        text = _NoGlobals(io.BytesIO(blob)).load()
        if not isinstance(text, str):
            raise ValueError("hidden tests did not decode to text") from None
        private = json.loads(text)
    tests = [*json.loads(str(item["public_tests"])), *private]
    return [[str(test["input"]), str(test["output"])] for test in tests]


def _exchange(job: Mapping[str, Any], path: str, family: int) -> list[Any]:
    """The JSON lines the sandbox service sent back for one job."""
    with socket.socket(family, socket.SOCK_STREAM) as conn:
        conn.settimeout(VERDICT_TIMEOUT_S)
        conn.connect(path)
        conn.sendall(json.dumps(job).encode("utf-8"))
        conn.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while chunk := conn.recv(65536):
            chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def ask_sandbox(job: Mapping[str, Any], path: str | None = None) -> dict[str, Any]:
    """Send one job to the sandbox service and return its verdict, or ``SandboxUnavailable``.

    The worker answers ``{"started": true}`` and then the verdict. A worker
    that started and died before its verdict was brought down by the program
    it ran, and that program fails (``sandbox_died``). A connection that got
    no answer at all was not served (the service takes a few jobs at a time):
    it is tried again, then reported as unavailable.
    """
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        raise SandboxUnavailable("this platform has no Unix sockets")
    lines: list[Any] = []
    for attempt in range(CONNECT_ATTEMPTS):
        try:
            lines = _exchange(job, path or SANDBOX_SOCKET, family)
        except (OSError, ValueError) as err:
            raise SandboxUnavailable(type(err).__name__) from err
        if lines:
            break
        time.sleep(attempt + 1)
    verdict = lines[-1] if lines else None
    if isinstance(verdict, dict) and isinstance(verdict.get("passed"), bool):
        return verdict
    if verdict == {"started": True}:
        return {"passed": False, "tests_run": 0, "failure": "sandbox_died"}
    raise SandboxUnavailable("the sandbox returned no verdict")


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Per reply: whether its program passes every test of the problem.

    A reply without a fenced block has no program and fails, as upstream. A
    reply the sandbox gave no verdict on is not scored.
    """
    item = sample.get("item")
    item = item if isinstance(item, Mapping) else {}
    row: dict[str, float | int | str | None] = {
        "difficulty": str(item["difficulty"]) if item.get("difficulty") else None,
        "platform": str(item["platform"]) if item.get("platform") else None,
        "pass_at_1": None,
        "tests_run": None,
        "failure": None,
    }
    if not item.get("private_tests"):
        return {"verifier_status": "unavailable: no item", **row}
    code = extract_code(str(sample.get("text", "")))
    if not code.strip():
        return {"verifier_status": "not needed", "answer_valid": 0, **row, "pass_at_1": 0}
    job = {
        "code": code,
        "fn_name": item.get("fn_name"),
        "timeout_s": TEST_TIMEOUT_S,
        "tests": decode_tests(item),
    }
    try:
        verdict = ask_sandbox(job)
    except SandboxUnavailable as err:
        return {"verifier_status": f"unavailable: {err}", "answer_valid": 1, **row}
    failure = verdict.get("failure")
    return {
        "verifier_status": "available",
        "answer_valid": 1,
        **row,
        "pass_at_1": int(verdict["passed"]),
        "tests_run": int(verdict.get("tests_run") or 0),
        "failure": str(failure) if failure else None,
    }


def _prompt(row: Mapping[str, Any]) -> str:
    parts = str(row.get("sample_ref", "")).split("/")
    return str(row.get("prompt_id") or (parts[2] if len(parts) >= 4 else ""))


def _binary_entropy(p: float) -> float:
    return 0.0 if p in (0.0, 1.0) else -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def endpoints(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per arm: pass@1, pass@k and the entropy of pass against fail, averaged over problems.

    Descriptive: the analysis reads ``pass_at_1`` per reply and does not call
    this. ``pass_rate_entropy`` is the binary entropy, in bits, of a problem's
    pass rate over its k replies; ``pass_at_k`` is the share of problems with
    at least one passing reply.
    """
    by_problem: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row.get("pass_at_1") is not None:
            by_problem[_prompt(row)].append(int(row["pass_at_1"]))
    n = len(by_problem)
    rates = [sum(values) / len(values) for values in by_problem.values()]
    return {
        "n_prompts": n,
        "pass_at_1": sum(rates) / n if n else math.nan,
        "pass_at_k": sum(any(values) for values in by_problem.values()) / n if n else math.nan,
        "pass_rate_entropy": sum(_binary_entropy(rate) for rate in rates) / n if n else math.nan,
    }
