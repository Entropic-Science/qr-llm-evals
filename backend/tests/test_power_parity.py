"""Shared Python/TypeScript power vectors (``packages/contracts/fixtures/power.json``).

The web app mirrors ``cards/power.py`` and is checked against this file. Two
kinds of rows are in it, and they prove different things:

- the generated rows are what ``cards/power.py`` returned when the fixture was
  written. Recomputing them here can only fail when the committed file is
  stale, and it then says which row moved. They are not evidence that the
  numbers are right: a wrong formula regenerates into a wrong fixture.
- the rows of ``hand_derived`` were worked out by hand in
  ``scripts/gen_design_fixtures.py`` (the derivation is the comment above
  ``HAND_DERIVED``) and are not produced by the code. Both sides are checked
  against them, so the two sides cannot be wrong about the same number
  without a test failing. ``test_power.py`` holds further hand-written
  expectations for the backend alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from scipy.stats import norm

from qr_llm_evals.cards import power

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "packages" / "contracts" / "fixtures" / "power.json"
TOLERANCE = 1e-9
FUNCTIONS = (
    "z",
    "n_floor",
    "control_floor",
    "mde_standardized",
    "sd_bound",
    "k_needed",
    "power_at",
    "prompts_needed",
    "equivalence_floor",
)


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_power_fixture_constants(payload: dict[str, Any]) -> None:
    assert payload["tolerance"] == TOLERANCE
    assert payload["precision"] == power.PRECISION
    assert payload["constants"] == {
        "POWER": power.POWER,
        "REFERENCE_EFFECT": power.REFERENCE_EFFECT,
        "MIN_COMPLETENESS": power.MIN_COMPLETENESS,
        "MARGIN_CAP_FRACTION": power.MARGIN_CAP_FRACTION,
        "MARGIN_FLOOR_FRACTION": power.MARGIN_FLOOR_FRACTION,
        "MARGINAL_POWER": power.MARGINAL_POWER,
        "EQUIVALENCE_SIDE_POWER": power.EQUIVALENCE_SIDE_POWER,
    }


@pytest.mark.parametrize("name", FUNCTIONS)
def test_power_functions_match_fixture(payload: dict[str, Any], name: str) -> None:
    """Names the row of a stale fixture. Not a check of the formula: see the module docstring."""
    rows = payload[name]
    assert rows, name
    function = getattr(power, name)
    for row in rows:
        value = function(**row["args"])
        if row["value"] is None or isinstance(row["value"], int):
            assert value == row["value"], row["args"]
        else:
            assert value == pytest.approx(row["value"], abs=TOLERANCE), row["args"]


def test_power_functions_match_values_derived_by_hand(payload: dict[str, Any]) -> None:
    """Expected values that the code under test did not produce."""
    rows = payload["hand_derived"]
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import gen_design_fixtures
    finally:
        sys.path.pop(0)
    # The fixture carries the generator's literals unchanged.
    assert rows == json.loads(json.dumps(gen_design_fixtures.HAND_DERIVED))
    for row in rows:
        value = getattr(power, row["function"])(**row["args"])
        if "tolerance" in row:
            assert value == pytest.approx(row["value"], abs=row["tolerance"]), row
        else:
            assert value == row["value"], row
    # Every function of the module has at least one such row.
    assert {row["function"] for row in rows} == set(FUNCTIONS)


def test_quantiles_agree_with_an_independent_implementation(payload: dict[str, Any]) -> None:
    """``statistics.NormalDist`` against scipy's quantile, on every probability of the fixture."""
    for row in payload["z"]:
        p = row["args"]["p"]
        assert row["value"] == pytest.approx(float(norm.ppf(p)), abs=1e-9), p
    # The power function is the normal distribution function of a difference of two numbers.
    for row in payload["power_at"][:60]:
        args = row["args"]
        shift = args["effect"] * args["n"] ** 0.5 / args["sd"]
        level = float(norm.ppf(1 - args["alpha"] / (2 * args["m"])))
        assert row["value"] == pytest.approx(float(norm.cdf(shift - level)), abs=1e-9), args


def test_power_fixture_covers_the_strict_end(payload: dict[str, Any]) -> None:
    """The grid reaches the strictest level and the largest family a study can register."""
    for name in ("n_floor", "mde_standardized", "prompts_needed"):
        assert any(r["args"]["alpha"] == 0.005 and r["args"]["m"] == 32 for r in payload[name]), (
            name
        )
    assert any(r["args"]["alpha"] == 0.005 and r["args"]["m"] == 32 for r in payload["rate"])
    # Told cells are in the variance grid, and both outcomes of the repetition search.
    assert any(r["args"]["told_cells"] == 3 for r in payload["sd_bound"])
    assert {r["value"] is None for r in payload["k_needed"]} == {True, False}


def test_rate_matches_fixture(payload: dict[str, Any]) -> None:
    seen: set[tuple[str, str]] = set()
    for row in payload["rate"]:
        args = dict(row["args"])
        args["range"] = None if args["range"] is None else tuple(args["range"])
        result = power.rate(**args).model_dump(mode="json")
        expected = row["value"]
        assert set(result) == set(expected)
        for key, value in expected.items():
            if isinstance(value, float):
                assert result[key] == pytest.approx(value, abs=TOLERANCE), (key, row["args"])
            else:
                assert result[key] == value, (key, row["args"])
        seen.add((expected["rating"], expected["basis"]))
    # Every rung of the rating ladder is in the fixture.
    assert seen == {
        ("unknown", "no_closed_form"),
        ("sufficient", "margin_worst_case"),
        ("marginal", "margin_worst_case"),
        ("insufficient", "margin_worst_case"),
        ("marginal", "standardized_reference"),
        ("insufficient", "standardized_reference"),
    }
    # The fixture holds a hypothesis below the control floor and one that is recommended more k.
    values = [row["value"] for row in payload["rate"]]
    assert any(
        v["control_floor"] is not None and v["n_floor"] <= v["n"] < v["control_floor"]
        for v in values
    )
    assert any(
        v["k_recommended"] not in (None, row["args"]["k"])
        for v, row in zip(values, payload["rate"], strict=True)
    )
