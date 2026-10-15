"""Offline re-analysis command for an extracted study bundle."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from qr_llm_evals.analysis.run import load_plan, run


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Re-run a qr-llm-evals analysis bundle")
    result.add_argument(
        "--bundle", required=True, type=Path, help="extracted study bundle directory"
    )
    result.add_argument(
        "--run", dest="run_id", help="run id (required if the bundle has multiple runs)"
    )
    result.add_argument(
        "--plan", type=Path, help="frozen plan JSON; defaults to the bundle's archived plan"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    bundle = args.bundle.resolve()
    plan_path = args.plan
    if plan_path is None:
        candidates = [bundle / "prereg-plan.json", bundle / "plan.json"]
        if args.run_id:
            candidates.insert(0, bundle / "runs" / args.run_id / "plan.json")
        plan_path = next((path for path in candidates if path.is_file()), None)
    if plan_path is None:
        raise SystemExit("bundle does not contain a frozen plan; pass --plan")
    analysis_id = run(bundle, load_plan(plan_path), run_id=args.run_id)
    print(analysis_id)
    return 0


__all__ = ["main", "parser"]
