"""Shared Python/TypeScript design vectors (``packages/contracts/fixtures/design.json``).

The web app drafts hypotheses, recommends a design and marks unreachable
verdicts in the browser, with code that mirrors ``cards/design.py`` and is
checked against this file. Here the backend is checked against it as well:
every case is recomputed from its config, floats to 1e-9 and everything else
exactly. The gate's ``schemas`` row compares the bytes; this test says which
case moved.

That is all these recomputations prove. The fixture is what ``cards/design.py``
returned when ``scripts/gen_design_fixtures.py`` ran, so a wrong formula
regenerates into a wrong fixture and both sides then agree on it. Whether the
numbers are right is tested where the expectation is written by hand:
``test_design.py`` (families, floors and margins worked out in comments),
``test_power.py``, and the ``hand_derived`` rows of ``power.json``. What this
module does prove is coverage: every problem code and every refusal the
browser mirrors has a case here, so the two sides cannot drift on one
unnoticed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.cards import design, load_cards
from qr_llm_evals.cards.design_schema import HypothesisPlan
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "packages" / "contracts" / "fixtures"
TOLERANCE = 1e-9


def _generator() -> Any:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import gen_design_fixtures
    finally:
        sys.path.pop(0)
    return gen_design_fixtures


def _same(actual: Any, expected: Any, where: str) -> None:
    """Equal, with floats compared to :data:`TOLERANCE`."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and list(actual) == list(expected), where
        for key, value in expected.items():
            _same(actual[key], value, f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list | tuple) and len(actual) == len(expected), where
        for index, value in enumerate(expected):
            _same(actual[index], value, f"{where}[{index}]")
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=TOLERANCE), where
    else:
        assert actual == expected, where


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "design.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_design_fixture_matches_backend(payload: dict[str, Any]) -> None:
    fresh = json.loads(json.dumps(_generator().design_fixture()))
    assert [case["name"] for case in fresh["cases"]] == [case["name"] for case in payload["cases"]]
    for actual, expected in zip(fresh["cases"], payload["cases"], strict=True):
        _same(actual, expected, expected["name"])
    _same(
        {k: v for k, v in fresh.items() if k != "cases"},
        {k: v for k, v in payload.items() if k != "cases"},
        "header",
    )


def test_design_fixture_cases_are_what_they_say(payload: dict[str, Any]) -> None:
    """Each case is recomputed from its own config with the public functions."""
    cards = load_cards()
    assert payload["max_permutations"] == design.MAX_PERMUTATIONS
    assert {key for key, value in payload["eligible"].items() if value} == {
        f"{card.id}/{endpoint}"
        for card in cards.values()
        for endpoint in card.endpoints.primary
        if design.eligible(card, endpoint)
    }
    for case in payload["cases"]:
        name, config = case["name"], case["config"]
        hypotheses = design.draft_hypotheses(config, cards)
        _same([h.model_dump(mode="json") for h in hypotheses], case["hypotheses"], name)
        assert design.recommend(config, cards) == case["recommend"], name
        assert design.recommend_k(config, cards) == case["recommend_k"], name
        assert design.problems(config, cards) == case["problems"], name
        claim = (config.get("design") or {}).get("claim")
        for row in case["hypotheses"]:
            hypothesis = HypothesisPlan.model_validate(row)
            assert (
                design.reachable_verdicts(hypothesis, claim)
                == (case["reachable_verdicts"][hypothesis.id])
            ), name
        # A config the backend accepts builds the plan the case names, or is refused as it says.
        if case["config_valid"]:
            study = StudyConfig.model_validate(config)
            if case["plan_refused"] is None:
                assert StatisticalPlan.from_config(study, cards).plan_hash == case["plan_hash"], (
                    name
                )
            else:
                with pytest.raises(ValueError) as refusal:
                    StatisticalPlan.from_config(study, cards)
                assert str(refusal.value) == case["plan_refused"], name
        else:
            with pytest.raises(ValueError):
                StudyConfig.model_validate(config)
            assert case["plan_hash"] is None, name


def test_design_fixture_covers_the_contract(payload: dict[str, Any]) -> None:
    cases = {case["name"]: case for case in payload["cases"]}
    codes = {problem["code"] for case in cases.values() for problem in case["problems"]}
    # Every code the backend can report has a shared case, and the list the
    # browser holds itself to (``problem_codes``) is the backend's own.
    assert payload["problem_codes"] == list(design.PROBLEM_CODES)
    assert codes == set(design.PROBLEM_CODES)
    assert len(set(design.PROBLEM_CODES)) == len(design.PROBLEM_CODES) == 22
    messages = {problem["message"] for case in cases.values() for problem in case["problems"]}
    # Both forms of an override out of bounds, and every reason a margin is refused.
    for ending in (
        ": k must be between 10 and 30.",
        ": prompts must be between 1 and 30.",
        " has no paired difference a margin could apply to.",
        " takes its margin from the eval card.",
        " has no fixed range to state a margin against.",
        " cannot take a margin above 0.1 (10% of its range).",
        " cannot take a margin below 0.001 (0.1% of its range).",
        " With k = 27, the 30 prompts of the eval are enough.",
    ):
        assert any(message.endswith(ending) for message in messages), ending
    refusals = " ".join(case["plan_refused"] or "" for case in cases.values())
    for clause in (
        "has no paired difference a margin could apply to",
        "takes its margin from the eval card",
        "has no fixed range to state a margin against",
        "cannot take a margin above 0.1 (10% of its range)",
        "cannot take a margin below 0.001 (0.1% of its range)",
        "seed-pair control tests at alpha 0.005 need 120000 permutations each",
    ):
        assert clause in refusals, clause
    reasons = {
        reason
        for case in cases.values()
        for verdicts in case["reachable_verdicts"].values()
        for reason in verdicts.values()
    }
    assert reasons >= {
        None,
        "below_n_floor",
        "control_cannot_fail",
        "equivalence_not_claimed",
        "equivalence_not_testable",
        "no_margin",
        "positive_control_absent",
    }
    ratings = {
        row["sample_size"]["rating"] for case in cases.values() for row in case["hypotheses"]
    }
    assert ratings == {"sufficient", "marginal", "insufficient", "unknown"}
    sources = {row["margin_source"] for case in cases.values() for row in case["hypotheses"]}
    assert sources == {"card_sesoi", "declared", "none"}
    # Legacy, recommended, refused and draft configs are all present.
    assert cases["study1_legacy"]["hypotheses"] == [] and cases["study1_legacy"]["config_valid"]
    assert cases["study1_designed"]["config"] == json.loads(
        (FIXTURES / "study1.designed.json").read_text(encoding="utf-8")
    )
    assert cases["nothing_eligible"]["recommend"] is None
    assert cases["too_many_tests"]["plan_refused"] and cases["too_many_tests"]["config_valid"]
    assert not cases["draft_not_eligible"]["config_valid"]
    assert len(cases["two_cells"]["hypotheses"]) == 6
    # A plan is built at every level a study can register, not only at 0.05.
    built = {
        case["config"]["design"]["alpha"]
        for case in cases.values()
        if case["plan_hash"] and case["config"].get("design")
    }
    assert built == {0.05, 0.01, 0.005}
    # The template's nine seed pairs add nothing to what its three difference tests ask for.
    assert (cases["alpha_0_01"]["permutations"], cases["alpha_0_005"]["permutations"]) == (
        10_000,
        12_000,
    )
    # Seven control seeds: the seed pairs decide the count, and the plan is built.
    seven = cases["seven_seeds"]
    assert (seven["config"]["design"]["alpha"], seven["families"]["negative_control"]) == (
        0.005,
        63,
    )
    assert seven["permutations"] == 25_200 and seven["plan_hash"] and not seven["plan_refused"]
    # Four designs the setup can build, each with the count of its difference tests.
    for name, families, permutations in (
        ("five_seeds_alpha_0_005", (3, 30), 12_000),
        ("three_comparisons_alpha_0_005", (9, 27), 36_000),
        ("five_seeds_six_hypotheses_alpha_0_01", (6, 60), 12_000),
        ("four_seeds_five_hypotheses_alpha_0_005", (5, 30), 20_000),
    ):
        case = cases[name]
        assert (
            case["families"]["difference"],
            case["families"]["negative_control"],
        ) == families, name
        assert case["permutations"] == permutations, name
        assert case["config_valid"] and case["plan_hash"] and case["plan_refused"] is None, name
        assert "design.too_many_tests" not in {p["code"] for p in case["problems"]}, name
    # A study on the built-in plan is warned about its comparison, where a designed one is
    # refused for it: the severity is what the server does with the study.
    for name, code in (
        ("legacy_one_seed", "compare.too_few_seeds"),
        ("legacy_no_cell", "compare.no_cell"),
    ):
        case = cases[name]
        assert "design" not in case["config"] and case["config_valid"] and case["plan_hash"], name
        assert [p["severity"] for p in case["problems"] if p["code"] == code] == ["warning"], name
    for name, code in (
        ("draft_epsilon_and_seeds", "compare.too_few_seeds"),
        ("no_cell", "compare.no_cell"),
    ):
        case = cases[name]
        assert not case["config_valid"], name
        assert [p["severity"] for p in case["problems"] if p["code"] == code] == ["error"], name
    # Token-stream outcomes in a study with no eval on a prompt list of its own plan nothing.
    unplanned = cases["no_planned_data"]
    assert unplanned["config_valid"] and unplanned["plan_hash"]
    assert [row["planned_units"] for row in unplanned["hypotheses"]] == [0, 0]
    assert [p["code"] for p in unplanned["problems"] if p["severity"] == "error"] == [
        "design.no_planned_data",
        "design.no_planned_data",
    ]
    # Two biased arms can run and neither is 0.10: the larger is the recommended control.
    fallback = cases["epsilon_fallback"]["recommend"]
    assert (fallback["sensitivity_epsilon"], fallback["claim"]) == (
        0.5,
        "difference_or_equivalence",
    )
    # Recommended repetitions: the template's own, a larger family's, and none.
    assert cases["study1_designed"]["recommend_k"] == {"self_referential_probe": 24}
    assert cases["fewer_repetitions"]["recommend_k"] == {"self_referential_probe": 27}
    assert cases["alpha_0_005"]["recommend_k"] == {}
    # Not in the fixture: a study over the cap of eight outcomes, and the
    # k-locked form of ``measure.out_of_bounds``, which needs an eligible
    # k-locked outcome, of which there is none. Both are tested with a patched
    # registry in test_design.py. The registry itself has had more eligible
    # outcomes than the cap since the judged evals were implemented.
    eligible = [key for key, value in payload["eligible"].items() if value]
    assert len(eligible) > design.MAX_CONFIRMATORY


def test_committed_design_fixtures_are_current() -> None:
    """Every file equals a fresh render, to the byte (the check of the gate's ``schemas`` row)."""
    rendered = _generator().render()
    assert sorted(path.relative_to(REPO_ROOT).as_posix() for path in rendered) == [
        "backend/tests/fixtures/prereg/golden_designed.md",
        "packages/contracts/fixtures/design.json",
        "packages/contracts/fixtures/power.json",
        "packages/contracts/fixtures/study1.designed.json",
    ]
    stale = [
        path.name for path, text in rendered.items() if path.read_bytes() != text.encode("utf-8")
    ]
    assert not stale, f"run `python scripts/gen_design_fixtures.py`; stale: {stale}"


def test_generator_check_catches_a_stale_designed_prereg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pinned pre-registration of the designed Study 1 is under ``--check`` too."""
    generator = _generator()
    assert generator.check_all() == 0
    capsys.readouterr()

    # The same document with one planned count changed, and with CRLF endings:
    # each is a different document to a hash, and each must fail the check.
    carriage_return, line_feed = bytes([13]), bytes([10])
    current = generator.GOLDEN_DESIGNED.read_bytes()
    assert b"89860 generations per arm" in current and carriage_return not in current
    for stale in (
        current.replace(b"89860 generations per arm", b"95700 generations per arm"),
        current.replace(line_feed, carriage_return + line_feed),
    ):
        copy = tmp_path / "golden_designed.md"
        copy.write_bytes(stale)
        monkeypatch.setattr(generator, "GOLDEN_DESIGNED", copy)
        assert generator.check_all() == 1
        assert "differs: " in capsys.readouterr().out
    # The legacy golden beside it is not one of the generator's files.
    assert not any(path.name == "golden.md" for path in generator.render())
