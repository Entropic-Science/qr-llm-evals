#!/usr/bin/env python
"""Thesis study helpers for real runs: the pilot check and the decision table.

Both commands read a run directory written by the harness, the frozen analysis
plan (the ``plan.json`` exported with the preregistration; never a plan rebuilt
from a newer registry) and the frozen thesis settings (tolerance, margin, the
first-word token ids; ``studies/thesis_prompt_or_physics/settings.json``).

    # after the pilot (seeded arms only): base rates, bent doses, no quantum data
    python studies/thesis_prompt_or_physics/thesis_report.py pilot-check --run-dir <study>/runs/<pilot_run_id> \
        --plan plan.json --settings studies/thesis_prompt_or_physics/settings.json

    # after the full run: execute the frozen plan, run the checks and decide
    python studies/thesis_prompt_or_physics/thesis_report.py decide --run-dir <study>/runs/<full_run_id> --plan plan.json \
        --settings studies/thesis_prompt_or_physics/settings.json --out <study>/runs/<full_run_id>/thesis

``decide`` writes ``tables/*.json``, ``decisions.json`` and ``decisions.md`` under
``--out`` (the decision is computed on the clean data and again with nothing left
out). ``pilot-check`` writes ``pilot_check.{json,md}`` next to the pilot run. Neither
changes anything in the run directory itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from thesis import (
    decide,
    load_settings,
    pilot_check,
    render_markdown,
    render_pilot_markdown,
)

from qr_llm_evals.analysis.endpoints import exclude_tainted, execute, load_data
from qr_llm_evals.analysis.tables import write_tables
from qr_llm_evals.cards.statistical_plan import StatisticalPlan


def _plan(path: Path) -> StatisticalPlan:
    return StatisticalPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _run_dir(path: Path) -> Path:
    if not (path / "run.json").is_file():
        raise SystemExit(f"{path} is not a run directory (no run.json)")
    return path


def cmd_pilot_check(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_dir.resolve())
    report = pilot_check(load_data(run_dir), _plan(args.plan), load_settings(args.settings))
    out = (args.out or run_dir.parent / f"{run_dir.name}_pilot_check").resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "pilot_check.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = render_pilot_markdown(report)
    (out / "pilot_check.md").write_text(summary, encoding="utf-8", newline="\n")
    print(summary)
    if not report["ordinary_numbers_only"]:
        print("the pilot used quantum numbers; it must run on seeded arms only", file=sys.stderr)
        return 1
    return 0


def cmd_hash(args: argparse.Namespace) -> int:
    print(load_settings(args.settings).sha256)
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)
    if settings.sha256 != args.settings_sha256.strip().lower():
        raise SystemExit(
            f"settings hash {settings.sha256} differs from the registered "
            f"{args.settings_sha256}: the decision constants changed after registration"
        )
    run_dir = _run_dir(args.run_dir.resolve())
    plan = _plan(args.plan)
    out = (args.out or run_dir / "thesis").resolve()
    out.mkdir(parents=True, exist_ok=True)
    # One load of the token records serves both versions of the analysis.
    data_all = load_data(run_dir, include_tainted=True)
    data = exclude_tainted(data_all)
    tables = execute(data, plan)
    write_tables(out / "tables", tables)
    report = decide(data, plan, tables, settings=settings, data_all=data_all)
    (out / "decisions.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    summary = render_markdown(report)
    (out / "decisions.md").write_text(summary, encoding="utf-8", newline="\n")
    print(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pilot = sub.add_parser("pilot-check", help="what the pilot must show before the lock")
    pilot.add_argument("--run-dir", type=Path, required=True)
    pilot.add_argument("--plan", type=Path, required=True)
    pilot.add_argument("--settings", type=Path, required=True)
    pilot.add_argument("--out", type=Path)
    pilot.set_defaults(func=cmd_pilot_check)
    decide_cmd = sub.add_parser("decide", help="checks, H1-H4 and the decision for a full run")
    decide_cmd.add_argument("--run-dir", type=Path, required=True)
    decide_cmd.add_argument("--plan", type=Path, required=True)
    decide_cmd.add_argument("--settings", type=Path, required=True)
    decide_cmd.add_argument(
        "--settings-sha256",
        required=True,
        help="hash of the settings as written in the preregistration (see the hash command)",
    )
    decide_cmd.add_argument("--out", type=Path)
    decide_cmd.set_defaults(func=cmd_decide)
    hash_cmd = sub.add_parser("hash", help="print the settings hash to write into the prereg")
    hash_cmd.add_argument("--settings", type=Path, required=True)
    hash_cmd.set_defaults(func=cmd_hash)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
