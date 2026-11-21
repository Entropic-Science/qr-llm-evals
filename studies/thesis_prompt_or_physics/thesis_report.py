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

    # figures for the decision table, drawn from decisions.json (needs matplotlib)
    python studies/thesis_prompt_or_physics/thesis_report.py figures --decisions <run>/thesis/decisions.json

    # the text to file on the public registry, from the folder thesis_launch.py lock wrote
    python studies/thesis_prompt_or_physics/thesis_report.py register --lock registration/

``decide`` writes ``tables/*.json``, ``decisions.json`` and ``decisions.md`` under
``--out`` (the decision is computed on the clean data and again with nothing left
out). ``pilot-check`` writes ``pilot_check.{json,md}`` next to the pilot run. Neither
changes anything in the run directory itself. ``figures`` writes ``effects``,
``sampler`` and ``first_word`` as SVG and PNG next to the decisions file.
``register`` writes ``REGISTRATION.md`` into the lock folder.
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


def cmd_figures(args: argparse.Namespace) -> int:
    from thesis_figures import write_figures

    report = json.loads(args.decisions.read_text(encoding="utf-8"))
    out = (args.out or args.decisions.parent / "figures").resolve()
    written = write_figures(report, out)
    if not written:
        print("no figure could be drawn: the report has none of the numbers they need")
        return 1
    for path in written:
        print(path)
    return 0


REGISTRATION_KEYS = ("config_hash", "plan_hash", "prereg_hash", "settings_sha256")
REGISTRATION_MEANING = {
    "config_hash": "the study configuration: models, arms, evals, judge, seeds",
    "plan_hash": "the statistical plan: endpoints, tests, corrections",
    "prereg_hash": "the pre-registration document (prereg.md), byte for byte",
    "settings_sha256": "the decision constants: tolerance, margin, first-word tokens",
}


def render_registration(lock: Path, commit: str | None) -> str:
    """The text that is filed on the public registry, from a lock folder."""
    try:
        hashes = json.loads((lock / "hashes.json").read_text(encoding="utf-8"))
        config = json.loads((lock / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise SystemExit(f"{lock} is not a lock folder ({err.filename} is missing)") from err
    missing = [key for key in REGISTRATION_KEYS if len(str(hashes.get(key, ""))) != 64]
    if missing:
        raise SystemExit(f"{lock / 'hashes.json'} has no valid {', '.join(missing)}")
    lines = [
        f"# Registration: {config.get('title', 'study')}",
        "",
        "This file fixes, before the full run, what will be run and how it will be",
        "judged. Each line below is the SHA-256 of one frozen file. Anyone holding the",
        "files can recompute the hashes; nobody can change a file afterwards without",
        "the hash changing.",
        "",
        "| What | SHA-256 |",
        "| --- | --- |",
    ]
    lines += [f"| {REGISTRATION_MEANING[key]} | `{hashes[key]}` |" for key in REGISTRATION_KEYS]
    lines += [
        "",
        f"Authors: {', '.join(str(a) for a in config.get('authors', []))}",
        f"Contact: {config.get('contact', '')}",
        f"Harness: qr-llm-evals{f' at commit {commit}' if commit else ''}",
        "",
        "## How to check a hash",
        "",
        "- `prereg_hash`: SHA-256 of `prereg.md` with line endings as LF.",
        "- `settings_sha256`: `python studies/thesis_prompt_or_physics/thesis_report.py hash "
        "--settings settings.json`.",
        "- `config_hash` and `plan_hash`: `POST /api/prereg/render` with `config.json` and",
        "  `readiness.json` returns both, next to the document itself.",
        "",
        "## What is filed",
        "",
        "This file and `prereg.md`, on a public registry that records the date. The",
        "configuration, the plan and the settings file are published with the results.",
        "",
    ]
    return "\n".join(lines)


def cmd_register(args: argparse.Namespace) -> int:
    text = render_registration(args.lock, args.commit)
    out = args.out or args.lock / "REGISTRATION.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print(f"written to {out}", file=sys.stderr)
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
    figures = sub.add_parser("figures", help="draw the decision figures from decisions.json")
    figures.add_argument("--decisions", type=Path, required=True)
    figures.add_argument("--out", type=Path, help="folder (default: figures/ next to the file)")
    figures.set_defaults(func=cmd_figures)
    register = sub.add_parser("register", help="write the text to file on the public registry")
    register.add_argument("--lock", type=Path, required=True, help="folder written by the lock")
    register.add_argument("--commit", help="harness commit the study runs on")
    register.add_argument("--out", type=Path)
    register.set_defaults(func=cmd_register)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
