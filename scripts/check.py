#!/usr/bin/env python
"""One-command verification gate for qr-llm-evals (spec §5.1).

Runs every oracle this repo is gated on, in order — the same table CI invokes
via ``--only`` so the check set cannot drift between laptops and CI::

    lint → format → types → security → tests → schemas → evals-doc → web → smoke

The smoke row is mandatory: a missing script or an incomplete integration
fails the gate. ``schemas`` runs ``scripts/export_schemas.py --check``
(committed ``packages/contracts`` output equals a fresh export).

Usage::

    python scripts/check.py                 # run everything
    python scripts/check.py --only lint,types
    python scripts/check.py --list
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


@dataclass(frozen=True)
class Check:
    """One gate row: a sequence of commands, or a reason it cannot run yet."""

    name: str
    commands: tuple[tuple[str, ...], ...]
    skip_unless: Callable[[], str | None] = lambda: None  # returns a skip reason or None
    shell: bool = False


def _needs_script(rel: str) -> Callable[[], str | None]:
    def probe() -> str | None:
        return None if (REPO_ROOT / rel).is_file() else f"{rel} does not exist yet"

    return probe


def _needs_pnpm() -> str | None:
    return None if shutil.which("pnpm") else "pnpm not on PATH (install pnpm 10 / enable corepack)"


PNPM = "pnpm"

#: Ordered oracle table. Keys are ``--only`` selectors.
CHECKS: tuple[Check, ...] = (
    Check("lint", ((PY, "-m", "ruff", "check", "."),)),
    Check("format", ((PY, "-m", "ruff", "format", "--check", "."),)),
    Check(
        "types",
        (
            (PY, "-m", "mypy", "--strict", "backend/src/"),
            (PY, "-m", "mypy", "--strict", "studies/thesis_prompt_or_physics/thesis.py"),
        ),
    ),
    Check("security", ((PY, "-m", "bandit", "-c", "pyproject.toml", "-r", "backend/src", "-q"),)),
    Check("tests", ((PY, "-m", "pytest"),)),
    Check(
        "schemas",
        ((PY, "scripts/export_schemas.py", "--check"),),
        skip_unless=_needs_script("scripts/export_schemas.py"),
    ),
    Check(
        "evals-doc",
        ((PY, "scripts/gen_evals_doc.py", "--check"),),
        skip_unless=_needs_script("scripts/gen_evals_doc.py"),
    ),
    Check(
        "web",
        (
            (PNPM, "-r", "typecheck"),
            (PNPM, "-r", "lint"),
            (PNPM, "-r", "test"),
            (PNPM, "--filter", "@qr-evals/web", "build"),
        ),
        skip_unless=_needs_pnpm,
        shell=sys.platform == "win32",  # pnpm is a .cmd shim on Windows
    ),
    Check(
        "smoke",
        ((PY, "scripts/smoke.py"),),
    ),
)
BY_NAME: dict[str, Check] = {c.name: c for c in CHECKS}


def _render(cmd: Sequence[str]) -> str:
    return " ".join(cmd[1:] if cmd[0] == PY else cmd)


def run_check(check: Check) -> str:
    """Run one row; return ``ok`` / ``skipped`` / ``failed``."""
    reason = check.skip_unless()
    if reason:
        print(f"\n=== {check.name}: SKIPPED — {reason} ===", flush=True)
        return "skipped"
    env = {**os.environ, "PYTHONUTF8": "1"}
    for cmd in check.commands:
        print(f"\n=== {check.name}: {_render(cmd)} ===", flush=True)
        result = subprocess.run(  # noqa: S603 - argv from the static table above
            subprocess.list2cmdline(cmd) if check.shell else cmd,
            cwd=REPO_ROOT,
            env=env,
            check=False,
            shell=check.shell,  # noqa: S602 - only for the pnpm .cmd shim on Windows
        )
        if result.returncode != 0:
            return "failed"
    return "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default=None,
        help=f"Comma-separated subset of checks (default: all). Available: {', '.join(BY_NAME)}",
    )
    parser.add_argument("--list", action="store_true", help="List available checks and exit.")
    args = parser.parse_args(argv)

    if args.list:
        for check in CHECKS:
            print(f"{check.name}: " + " && ".join(_render(c) for c in check.commands))
        return 0

    if args.only is None:
        selected = [c.name for c in CHECKS]
    else:
        selected = [name.strip() for name in args.only.split(",") if name.strip()]
        unknown = [name for name in selected if name not in BY_NAME]
        if unknown:
            parser.error(
                f"unknown check(s): {', '.join(unknown)} (available: {', '.join(BY_NAME)})"
            )

    outcomes = {name: run_check(BY_NAME[name]) for name in selected}
    failed = [n for n, o in outcomes.items() if o == "failed"]
    skipped = [n for n, o in outcomes.items() if o == "skipped"]
    print()
    if skipped:
        print(f"SKIPPED: {', '.join(skipped)}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"OK: {', '.join(n for n in selected if n not in skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
