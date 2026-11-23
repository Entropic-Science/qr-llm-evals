"""Runs one program against its tests. Meant to run only inside the sandbox unit.

``infra/qr-llm-evals-sandbox@.service`` starts this file once per connection,
under a throwaway user without network or access to the harness's data, with
the connection as stdin and stdout. It reads one JSON job, runs the program
once per test in a child process with resource limits, and writes one JSON
verdict. Standard library only: the unit runs it with the system Python.

The program is written by a model. Nothing here makes running it safe; the
unit does. Do not run this file with real model output anywhere else.

Job::

    {"code": str, "fn_name": str | null, "timeout_s": number,
     "tests": [[input, expected_output], ...]}

Reply, two lines: ``{"started": true}`` as soon as the job is read, then the
verdict::

    {"passed": bool, "tests_run": int, "failure": str | null}

A caller that got the first line and no verdict knows the worker died while
the program ran (the program runs as the worker's user and can kill it, or
use up the unit's memory), which is the program's failure and not an outage.

``failure`` is ``wrong_answer``, ``runtime_error``, ``timeout`` or ``budget``
(the job's total time ran out). Testing stops at the first test that fails,
as LiveCodeBench's own runner does.

The comparison rules are those of LiveCodeBench's ``testing_util.py`` (commit
28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24, MIT): a program that reads stdin
must print the expected lines, compared after stripping, or the same numbers;
a function must return the expected JSON value, a tuple counting as a list.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from typing import Any

#: Total seconds one job may spend running tests, whatever its per-test timeout.
BUDGET_S = 240.0
MEMORY_BYTES = 2 * 1024**3
OUTPUT_BYTES = 64 * 1024**2

#: What LiveCodeBench puts in front of every program (``import_string``).
PRELUDE = (
    "from string import *\nfrom re import *\nfrom datetime import *\nfrom collections import *\n"
    "from heapq import *\nfrom bisect import *\nfrom copy import *\nfrom math import *\n"
    "from random import *\nfrom statistics import *\nfrom itertools import *\n"
    "from functools import *\nfrom operator import *\nfrom io import *\nfrom sys import *\n"
    "from json import *\nfrom builtins import *\nfrom typing import *\n"
    "import string\nimport re\nimport datetime\nimport collections\nimport heapq\nimport bisect\n"
    "import copy\nimport math\nimport random\nimport statistics\nimport itertools\nimport functools\n"
    "import operator\nimport io\nimport sys\nimport json\nsys.setrecursionlimit(50000)\n"
)
#: Appended to a function-style program: call the function with the arguments
#: on stdin (one JSON value per line) and print the result as the last line.
_CALL = (
    "\n\nif __name__ == '__main__':\n"
    "    import json as _json, sys as _sys\n"
    "    _args = [_json.loads(_line) for _line in _sys.stdin.read().split('\\n')]\n"
    "    _target = Solution() if 'Solution' in globals() else _sys.modules['__main__']\n"
    "    _result = getattr(_target, {fn_name!r})(*_args)\n"
    "    if isinstance(_result, tuple):\n"
    "        _result = list(_result)\n"
    "    print('\\n' + {marker!r} + _json.dumps(_result))\n"
)
_MARKER = "__qr_result__ "


def _limits(cpu_s: int) -> Any:
    """A ``preexec_fn`` that caps the child's CPU time, memory and file size.

    The number of processes is capped by the unit (``TasksMax``), not here.
    """
    try:
        import resource  # noqa: PLC0415 - POSIX only; the sandbox unit is Linux
    except ImportError:
        return None

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_BYTES, OUTPUT_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _run(path: str, stdin: str, timeout_s: float, cwd: str) -> tuple[str, str | None]:
    """``(stdout, failure)`` of one run of the program at ``path``.

    The program writes to a file, not a pipe: the file-size limit then bounds
    its output, and a program that prints without end fails instead of
    filling this process's memory.
    """
    out_path = os.path.join(cwd, "stdout")
    try:
        with open(out_path, "wb") as out:
            done = subprocess.run(  # noqa: S603 - the point of this file; see the module docstring
                [sys.executable, "-I", path],
                input=stdin.encode("utf-8"),
                stdout=out,
                stderr=subprocess.DEVNULL,
                timeout=timeout_s,
                cwd=cwd,
                env={"PATH": "/usr/bin:/bin", "OMP_NUM_THREADS": "1"},
                preexec_fn=_limits(int(timeout_s) + 1),  # noqa: PLW1509 - single-threaded parent
                check=False,
            )
    except subprocess.TimeoutExpired:
        return "", "timeout"
    if done.returncode != 0:
        return "", "runtime_error"
    with open(out_path, "rb") as out:
        return out.read(OUTPUT_BYTES).decode("utf-8", errors="replace"), None


def _decimals(line: str) -> list[Decimal] | None:
    try:
        return [Decimal(token) for token in line.split()]
    except InvalidOperation:
        return None


def same_output(printed: str, expected: str) -> bool:
    """Whether a program's stdout is the expected one, line by line."""
    got = [line.strip() for line in printed.strip().split("\n")]
    want = [line.strip() for line in expected.strip().split("\n")]
    if len(got) != len(want):
        return False
    for mine, theirs in zip(got, want, strict=True):
        if mine == theirs:
            continue
        numbers, wanted = _decimals(mine), _decimals(theirs)
        if numbers is None or wanted is None or numbers != wanted:
            return False
    return True


def same_value(printed: str, expected: str) -> bool:
    """Whether a function's printed result is the expected JSON value."""
    line = printed.rstrip("\n").rsplit("\n", 1)[-1]
    if not line.startswith(_MARKER):
        return False
    try:
        return bool(json.loads(line[len(_MARKER) :]) == json.loads(expected))
    except ValueError:
        return False


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    fn_name = job.get("fn_name")
    timeout_s = float(job.get("timeout_s") or 6)
    program = PRELUDE + "\n" + str(job["code"])
    if fn_name:
        program += _CALL.format(fn_name=str(fn_name), marker=_MARKER)
    same = same_value if fn_name else same_output
    started = time.monotonic()
    tests_run = 0
    # A killed program can hold its output file for a moment; the unit's private
    # /tmp goes with the instance, so a failed clean-up costs nothing.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:
        path = os.path.join(cwd, "solution.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(program)
        for stdin, expected in job["tests"]:
            left = BUDGET_S - (time.monotonic() - started)
            if left <= 0:
                return {"passed": False, "tests_run": tests_run, "failure": "budget"}
            printed, failure = _run(path, str(stdin), min(timeout_s, left), cwd)
            tests_run += 1
            if failure is None and not same(printed, str(expected)):
                failure = "wrong_answer"
            if failure is not None:
                return {"passed": False, "tests_run": tests_run, "failure": failure}
    return {"passed": tests_run > 0, "tests_run": tests_run, "failure": None}


def main() -> None:
    try:
        job = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        sys.stdout.write('{"started": true}\n')
        sys.stdout.flush()
        verdict = run_job(job)
    except Exception as err:  # noqa: BLE001 - the caller gets a verdict or an error, never silence
        verdict = {"error": type(err).__name__}
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
