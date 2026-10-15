"""S24: frozen-plan analysis, deterministic tables, honest gaps and SVG output."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from qr_llm_evals.analysis.cli import main
from qr_llm_evals.analysis.run import load_plan, run

FIXTURE = Path(__file__).parent / "fixtures" / "bundle_mini"
ANALYSIS_ID = "a_c93ab9c741ab01fb"


def _copy(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle_mini"
    shutil.copytree(FIXTURE, bundle)
    return bundle


def _table_bytes(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


def test_golden_reproduction_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _copy(tmp_path)
    plan = load_plan(bundle / "prereg-plan.json")
    assert run(bundle, plan, run_id="run_mini") == ANALYSIS_ID
    actual = bundle / "runs" / "run_mini" / "analysis" / ANALYSIS_ID / "tables"
    expected = bundle / "golden" / "tables"
    assert _table_bytes(actual) == _table_bytes(expected)

    # Re-analysis overwrites the deterministic analysis id with identical table bytes.
    before = _table_bytes(actual)
    assert main(["--bundle", str(bundle), "--run", "run_mini"]) == 0
    assert capsys.readouterr().out.strip() == ANALYSIS_ID
    assert _table_bytes(actual) == before


def test_plan_object_identity_with_prereg(tmp_path: Path) -> None:
    bundle = _copy(tmp_path)
    plan = load_plan(bundle / "prereg-plan.json")
    changed = plan.model_copy(update={"config_hash": "not-the-preregistered-plan"})
    with pytest.raises(ValueError, match="does not equal"):
        run(bundle, changed, run_id="run_mini")


def test_unavailable_endpoint_is_labelled_not_zeroed(tmp_path: Path) -> None:
    bundle = _copy(tmp_path)
    plan = load_plan(bundle / "prereg-plan.json")
    run(bundle, plan, run_id="run_mini")
    path = (
        bundle
        / "runs"
        / "run_mini"
        / "analysis"
        / ANALYSIS_ID
        / "tables"
        / "unpredictabench--not_yet_scored.json"
    )
    table = json.loads(path.read_text(encoding="utf-8"))
    assert table["available"] is False
    assert "unavailable" in table["reason"]
    assert all(item["available"] is False and "effect" not in item for item in table["comparisons"])


def test_primary_figures_are_valid_svg_with_controls(tmp_path: Path) -> None:
    bundle = _copy(tmp_path)
    plan = load_plan(bundle / "prereg-plan.json")
    run(bundle, plan, run_id="run_mini")
    output = bundle / "runs" / "run_mini" / "analysis" / ANALYSIS_ID
    figures = sorted((output / "figures").glob("*.svg"))
    assert len(figures) == 2
    for path in figures:
        assert ET.parse(path).getroot().tag.endswith("svg")
    for path in (output / "tables").glob("*.json"):
        table = json.loads(path.read_text(encoding="utf-8"))
        if table.get("role") == "primary":
            assert len(table["controls"]) == 3


def test_manifest_hashes_inputs_and_records_tool_versions(tmp_path: Path) -> None:
    bundle = _copy(tmp_path)
    plan = load_plan(bundle / "prereg-plan.json")
    run(bundle, plan, run_id="run_mini")
    manifest_path = bundle / "runs" / "run_mini" / "analysis" / ANALYSIS_ID / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["plan_hash"] == plan.plan_hash
    assert manifest["inputs"]["runs/run_mini/run.json"]
    assert manifest["tools"]["python"]
