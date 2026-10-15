"""Top-level analysis runner: frozen plan in, archived tables/figures out."""

from __future__ import annotations

import json
from pathlib import Path

from qr_llm_evals.analysis.endpoints import execute, load_data
from qr_llm_evals.analysis.figures import write_figures
from qr_llm_evals.analysis.manifest import build_manifest
from qr_llm_evals.analysis.tables import write_tables
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.store.artifacts import write_json


def _resolve_run(study_dir: Path, run_id: str | None) -> Path:
    if (study_dir / "run.json").is_file():
        return study_dir
    runs = study_dir / "runs"
    if run_id is not None:
        path = runs / run_id
        if not (path / "run.json").is_file():
            raise FileNotFoundError(f"run {run_id!r} does not exist in {runs}")
        return path
    candidates = sorted(path for path in runs.glob("*") if (path / "run.json").is_file())
    if len(candidates) != 1:
        raise ValueError("--run is required when a bundle contains zero or multiple runs")
    return candidates[0]


def _preregistered_plan(study_dir: Path, run_dir: Path) -> StatisticalPlan | None:
    for path in (run_dir / "plan.json", study_dir / "plan.json", study_dir / "prereg-plan.json"):
        if path.is_file():
            return StatisticalPlan.model_validate_json(path.read_text(encoding="utf-8"))
    return None


def run(study_dir: str | Path, plan: StatisticalPlan, *, run_id: str | None = None) -> str:
    """Execute *plan itself* and return its deterministic analysis id."""
    root = Path(study_dir).resolve()
    run_dir = _resolve_run(root, run_id)
    study_root = root if run_dir != root else run_dir.parent.parent
    frozen = _preregistered_plan(study_root, run_dir)
    if frozen is not None and frozen != plan:
        raise ValueError("analysis plan does not equal the archived preregistration plan")
    analysis_id = f"a_{plan.plan_hash[:16]}"
    output = run_dir / "analysis" / analysis_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "plan.json").write_text(plan.canonical_json() + "\n", encoding="utf-8", newline="\n")
    tables = execute(load_data(run_dir), plan)
    write_tables(output / "tables", tables)
    write_figures(output / "figures", tables)
    write_json(output / "manifest.json", build_manifest(study_root, run_dir, plan.plan_hash))
    return analysis_id


def load_plan(path: Path) -> StatisticalPlan:
    return StatisticalPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))


__all__ = ["load_plan", "run"]
