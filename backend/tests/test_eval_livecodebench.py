"""livecodebench: pinned problems, upstream's prompt, and scoring through the sandbox service.

The problems are synthetic, and so is every program a test runs: the worker is
exercised here with a few lines of trusted code, never with model output.
"""

from __future__ import annotations

import base64
import configparser
import importlib.util
import json
import math
import pickle
import socket
import threading
import zlib
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.cards import REPO_ROOT, load_cards
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.cards.endpoint_meta import meta_for
from qr_llm_evals.runner.tasks import load_task_module

CARD = "livecodebench"


@pytest.fixture(scope="module")
def task() -> Any:
    return load_task_module(load_cards()[CARD]).module


@pytest.fixture(scope="module")
def worker() -> Any:
    path = REPO_ROOT / "evals" / CARD / "sandbox_worker.py"
    spec = importlib.util.spec_from_file_location("lcb_sandbox_worker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _packed(tests: list[dict[str, str]]) -> str:
    """Hidden tests as the dataset stores most of them: JSON in a compressed pickle."""
    return base64.b64encode(zlib.compress(pickle.dumps(json.dumps(tests)))).decode("ascii")


def _test(given: str, expected: str, kind: str = "stdin") -> dict[str, str]:
    return {"input": given, "output": expected, "testtype": kind}


def _row(n: int, *, leetcode: bool = False, date: str = "2025-01-04T00:00:00") -> dict[str, Any]:
    kind = "functional" if leetcode else "stdin"
    return {
        "question_title": f"Problem {n}",
        "question_content": f"Solve problem {n}.",
        "platform": "leetcode" if leetcode else "atcoder",
        "question_id": f"q{n}",
        "contest_id": "c",
        "contest_date": date,
        "starter_code": "class Solution:\n    def twice(self, n: int) -> int:\n        "
        if leetcode
        else "",
        "difficulty": "easy",
        "public_test_cases": json.dumps([_test("1", "2", kind)]),
        "private_test_cases": _packed([_test("2", "4", kind), _test("3", "6", kind)]),
        "metadata": json.dumps({"func_name": "twice"} if leetcode else {}),
    }


def _items(task: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows = [
        _row(0),
        _row(1, leetcode=True, date="2025-03-01T00:00:00"),
        _row(2, date="2025-03-01T00:00:00"),
    ]
    rows[0]["private_test_cases"] = json.dumps([_test("2", "4")])  # the plain-JSON form
    raw = ("\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(task, "ITEM_COUNT", 3)
    return list(task.parse_dataset(raw))


def test_card_is_implemented_and_pinned(task: Any) -> None:
    card = load_cards()[CARD]
    assert card.status == "implemented" and card.selectable and card.engine == "thin"
    assert card.dataset.source == "hf:livecodebench/code_generation_lite"
    assert card.dataset.revision == task.DATASET_REVISION
    assert task.DATASET_REVISION in task.DATASET_URL
    assert card.dataset.items == task.ITEM_COUNT == 175
    assert card.judge.kind == "none" and card.thinking
    meta = meta_for(CARD, "pass_rate_entropy")
    assert meta.level == "prompt_group" and not meta.executable


def test_dataset_is_checked_and_newest_first(task: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="sha256"):
        task.parse_dataset(b"{}\n")
    items = _items(task, monkeypatch)
    assert [item["prompt_id"] for item in items] == ["lcb_q2", "lcb_q1", "lcb_q0"]
    assert items[1]["fn_name"] == "twice" and items[0]["fn_name"] is None
    assert items[0]["contest_date"] == "2025-03-01"
    monkeypatch.setattr(task, "ITEM_COUNT", 4)
    with pytest.raises(ValueError, match="3 problems"):
        _items_raw = ("\n".join(json.dumps(_row(n)) for n in range(3))).encode("utf-8")
        monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(_items_raw).hexdigest())
        task.parse_dataset(_items_raw)


def test_hidden_tests_decode_without_loading_any_code(
    task: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _items(task, monkeypatch)
    assert task.decode_tests(items[0]) == [["1", "2"], ["2", "4"], ["3", "6"]]
    assert task.decode_tests(items[2]) == [["1", "2"], ["2", "4"]]
    # A pickle that names a function is refused before anything is called.
    hostile = base64.b64encode(zlib.compress(pickle.dumps(print))).decode("ascii")
    with pytest.raises(pickle.UnpicklingError, match="refused"):
        task.decode_tests({**items[0], "private_tests": hostile})
    not_text = base64.b64encode(zlib.compress(pickle.dumps([1, 2]))).decode("ascii")
    with pytest.raises(ValueError, match="did not decode to text"):
        task.decode_tests({**items[0], "private_tests": not_text})


def test_prompt_is_upstreams(task: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    items = _items(task, monkeypatch)
    system, user = task.build_messages(items[0], "told_qrng")
    assert system == {"role": "system", "content": task.SYSTEM_PROMPT}
    assert user["content"] == (
        "### Question:\nSolve problem 2.\n\n"
        "### Format: Read the inputs from stdin solve the problem and write the answer to stdout "
        "(do not directly test on the sample inputs). Enclose your code within delimiters as "
        "follows. Ensure that when the python program runs, it reads the inputs, runs the "
        "algorithm and writes output to STDOUT.\n"
        "```python\n# YOUR CODE HERE\n```\n\n"
        "### Answer: (use the provided format with backticks)\n\n"
    )
    starter = task.build_messages(items[1])[1]["content"]
    assert (
        "### Format: You will use the following starter code to write the solution to the "
        "problem and enclose your code within delimiters.\n```python\nclass Solution:\n"
    ) in starter


def test_code_is_the_last_fenced_block(task: Any) -> None:
    reply = (
        "First try:\n```python\nprint(1)\n```\nBetter:\n```python\nprint(2)\nprint(3)\n```\nDone."
    )
    assert task.extract_code(reply) == "print(2)\nprint(3)"
    assert task.extract_code("print(2)") == ""
    assert task.extract_code("```python\nprint(2)") == ""


def test_score_asks_the_sandbox_and_never_runs_the_program(
    task: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _items(task, monkeypatch)[1]
    jobs: list[dict[str, Any]] = []

    def verdict(job: dict[str, Any]) -> dict[str, Any]:
        jobs.append(job)
        return {"passed": True, "tests_run": 3, "failure": None}

    monkeypatch.setattr(task, "ask_sandbox", verdict)
    reply = "```python\nclass Solution:\n    def twice(self, n):\n        return 2 * n\n```"
    assert task.score({"item": item, "text": reply}) == {
        "verifier_status": "available",
        "answer_valid": 1,
        "difficulty": "easy",
        "platform": "leetcode",
        "pass_at_1": 1,
        "tests_run": 3,
        "failure": None,
    }
    assert jobs == [
        {
            "code": "class Solution:\n    def twice(self, n):\n        return 2 * n",
            "fn_name": "twice",
            "timeout_s": 6,
            "tests": [["1", "2"], ["2", "4"], ["3", "6"]],
        }
    ]
    monkeypatch.setattr(
        task, "ask_sandbox", lambda job: {"passed": False, "tests_run": 2, "failure": "timeout"}
    )
    failed = task.score({"item": item, "text": reply})
    assert (failed["pass_at_1"], failed["failure"]) == (0, "timeout")
    # No fenced block: no program, so the reply fails, and the sandbox is not asked.
    monkeypatch.setattr(task, "ask_sandbox", lambda job: pytest.fail("asked"))
    empty = task.score({"item": item, "text": "I cannot solve this."})
    assert (empty["pass_at_1"], empty["answer_valid"]) == (0, 0)
    assert rc.unparsed(CARD, empty) is not None


def test_without_the_sandbox_a_reply_is_not_scored(
    task: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    item = _items(task, monkeypatch)[0]
    monkeypatch.setattr(task, "SANDBOX_SOCKET", str(tmp_path / "absent.sock"))
    row = task.score({"item": item, "text": "```python\nprint(2)\n```"})
    assert row["pass_at_1"] is None
    assert str(row["verifier_status"]).startswith("unavailable: ")
    assert rc.unparsed(CARD, row) is not None
    assert task.score({"item": {}, "text": "x"})["verifier_status"] == "unavailable: no item"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
def test_a_job_travels_over_the_socket(
    task: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "s.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    received: list[Any] = []

    def serve(reply: bytes) -> None:
        conn, _ = server.accept()
        with conn:
            data = b""
            while chunk := conn.recv(65536):
                data += chunk
            received.append(json.loads(data))
            conn.sendall(reply)

    monkeypatch.setattr(task.time, "sleep", lambda seconds: None)
    started = b'{"started": true}\n'
    cases: list[tuple[list[bytes], Any]] = [
        ([started + b'{"passed": true, "tests_run": 1, "failure": null}\n'], True),
        # The worker started and died: the program it ran brought it down, and fails.
        ([started], {"passed": False, "tests_run": 0, "failure": "sandbox_died"}),
        # Not served twice, then served.
        ([b"", b"", started + b'{"passed": false, "tests_run": 2, "failure": "timeout"}\n'], False),
        ([started + b'{"error": "ValueError"}\n'], None),
        ([b"", b"", b""], None),
    ]
    for replies, expected in cases:

        def serve_all(replies: list[bytes] = replies) -> None:
            for reply in replies:
                serve(reply)

        thread = threading.Thread(target=serve_all)
        thread.start()
        if expected is None:
            with pytest.raises(task.SandboxUnavailable):
                task.ask_sandbox({"code": "x"}, path)
        elif isinstance(expected, dict):
            assert task.ask_sandbox({"code": "x"}, path) == expected
        else:
            assert task.ask_sandbox({"code": "x"}, path)["passed"] is expected
        thread.join()
    assert received[0] == {"code": "x"}
    server.close()


def test_descriptive_endpoints(task: Any) -> None:
    rows = [{"sample_ref": f"a/{CARD}/p1/{e}", "pass_at_1": int(e < 2)} for e in range(4)]
    rows += [{"sample_ref": f"a/{CARD}/p2/{e}", "pass_at_1": 0} for e in range(4)]
    rows.append({"sample_ref": f"a/{CARD}/p3/0", "pass_at_1": None})  # not scored
    out = task.endpoints(rows)
    assert out == {"n_prompts": 2, "pass_at_1": 0.25, "pass_at_k": 0.5, "pass_rate_entropy": 0.5}
    assert math.isnan(task.endpoints([])["pass_at_1"])


# ── the worker (trusted programs only) ───────────────────────────────────────


def test_output_and_value_comparison(worker: Any) -> None:
    assert worker.same_output("6\n0.50\n", " 6 \n0.5")
    assert worker.same_output("1 2 3", "1 2 3\n")
    assert not worker.same_output("6", "6\n7")
    assert not worker.same_output("yes", "no")
    assert not worker.same_output("50000000000000001", "50000000000000000")
    marker = worker._MARKER
    assert worker.same_value(f"debug\n{marker}[1, 2]\n", "[1,2]")
    assert not worker.same_value("[1, 2]\n", "[1, 2]")  # printed by the program, not the caller
    assert not worker.same_value(f"{marker}[1, 2]\n", "[2, 1]")
    assert not worker.same_value(f"{marker}oops\n", "1")


def test_worker_runs_stdin_programs(worker: Any) -> None:
    doubles = "n = int(input())\nprint(n * 2)"
    tests = [["1", "2"], ["2", "4\n"]]
    assert worker.run_job({"code": doubles, "fn_name": None, "tests": tests}) == {
        "passed": True,
        "tests_run": 2,
        "failure": None,
    }
    # Testing stops at the first failure.
    wrong = worker.run_job(
        {"code": doubles, "fn_name": None, "tests": [["1", "2"], ["2", "5"], ["3", "6"]]}
    )
    assert wrong == {"passed": False, "tests_run": 2, "failure": "wrong_answer"}
    crash = worker.run_job({"code": "raise ValueError('x')", "fn_name": None, "tests": tests})
    assert crash == {"passed": False, "tests_run": 1, "failure": "runtime_error"}
    slow = {"code": "while True:\n    pass", "fn_name": None, "timeout_s": 1, "tests": tests}
    assert worker.run_job(slow) == {"passed": False, "tests_run": 1, "failure": "timeout"}
    assert worker.run_job({"code": doubles, "fn_name": None, "tests": []})["passed"] is False


def test_worker_calls_functions(worker: Any) -> None:
    code = (
        "class Solution:\n"
        "    def pair(self, a: int, b: List[int]) -> List[int]:\n"
        "        print('debug output is ignored')\n"
        "        return (a, sum(b))\n"
    )
    job = {"code": code, "fn_name": "pair", "tests": [["1\n[2, 3]", "[1, 5]"]]}
    assert worker.run_job(job) == {"passed": True, "tests_run": 1, "failure": None}
    job["tests"] = [["1\n[2, 3]", "[1, 6]"]]
    assert worker.run_job(job)["failure"] == "wrong_answer"
    job["fn_name"] = "missing"
    assert worker.run_job(job)["failure"] == "runtime_error"


# ── the sandbox units ────────────────────────────────────────────────────────


def _unit(name: str) -> configparser.ConfigParser:
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string((REPO_ROOT / "infra" / name).read_text(encoding="utf-8"))
    return unit


def test_sandbox_units_isolate_the_program(task: Any) -> None:
    sock = _unit("qr-llm-evals-sandbox.socket")["Socket"]
    assert sock["ListenStream"] == task.SANDBOX_SOCKET
    assert (sock["SocketGroup"], sock["SocketMode"], sock["Accept"]) == (
        "qr-llm-evals",
        "0660",
        "yes",
    )
    text = (REPO_ROOT / "infra" / "qr-llm-evals-sandbox@.service").read_text(encoding="utf-8")
    service = _unit("qr-llm-evals-sandbox@.service")["Service"]
    worker_path = service["ExecStart"].split()[-1]
    assert service["ExecStart"].startswith("/usr/bin/python3 -I ")
    assert worker_path == "/opt/qr-llm-evals/current/evals/livecodebench/sandbox_worker.py"
    assert (REPO_ROOT / "evals" / CARD / "sandbox_worker.py").is_file()
    for option in ("DynamicUser", "PrivateNetwork", "PrivateTmp", "ProtectHome", "NoNewPrivileges"):
        assert service[option] == "yes", option
    assert service["ProtectSystem"] == "strict" and service["CapabilityBoundingSet"] == ""
    assert service["RestrictAddressFamilies"] == "none"
    for path in ("/var/lib/qr-llm-evals", "/etc/qr-llm-evals", "/etc/qr-llm-api"):
        assert f"-{path}" in service["InaccessiblePaths"].split()
    for bound in ("RuntimeMaxSec", "MemoryMax", "TasksMax"):
        assert bound in service
    # The worker sets its children's limits, so the filter must leave setrlimit alone.
    assert "@resources" not in text
    assert (service["StandardInput"], service["StandardOutput"]) == ("socket", "socket")


def test_selftest_job_probes_what_the_sandbox_must_close(worker: Any) -> None:
    """The job the setup guide sends through the socket to check the units."""
    job = json.loads((REPO_ROOT / "evals" / CARD / "selftest_job.json").read_text(encoding="utf-8"))
    assert job["tests"] == [["", "closed closed closed closed"]] and job["fn_name"] is None
    for probed in ("1.1.1.1", "/var/lib/qr-llm-evals", "/etc/qr-llm-evals", "/etc/qr-llm-api"):
        assert probed in job["code"]
    # Outside the sandbox the program runs and, with a network, reports it open.
    verdict = worker.run_job(job)
    assert verdict["tests_run"] == 1 and verdict["failure"] in (None, "wrong_answer")
    guide = (REPO_ROOT / "docs" / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    assert "evals/livecodebench/selftest_job.json" in guide
