"""The verdicts of a designed study (``analysis/decisions.py``, decision matrix version 1).

- every verdict and every reason of the matrix is reached by a synthetic case,
  and the rows are tried in the registered order: the first match wins;
- a test that could not be run keeps its slot in its Holm family;
- a pilot run, and any full run after the first, resolves its rows but
  confirms nothing;
- the study roll-up follows the rows;
- executed end to end, a designed study computes the confidence level and the
  equivalence test it registered (fixed margin, plan alpha, no pilot file),
  names the cell of every comparison, carries no raw ``significant_at_05``
  flag, and writes ``tables/decisions.json``;
- a narrative that copies numbers from the decisions table passes
  ``verify_numbers``;
- in a study with two (model, sampling) cells every hypothesis is decided on
  the rows of its own cell, and the equivalence family is corrected over all
  its members;
- samples the run tainted decide nothing, however many of an arm they are, and
  a comparison left with one prompt is unavailable, not a crash.

The matrix cases run on hand-written comparison rows, so each one states the
p-values it is about. The end-to-end cases run the executor on a small
generated run.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from qr_llm_evals.analysis import decisions
from qr_llm_evals.analysis.endpoints import (
    ONE_PROMPT_REASON,
    _generations_bracketed,
    _generations_present,
    exclude_tainted,
    execute,
    load_data,
)
from qr_llm_evals.analysis.narrative import load_tables, numeric_tokens, verify_numbers
from qr_llm_evals.analysis.run import run
from qr_llm_evals.analysis.tables import table_bytes
from qr_llm_evals.cards import EvalCard, load_cards
from qr_llm_evals.cards.design import VERDICT_RULES
from qr_llm_evals.cards.design_schema import DecisionRow, DecisionsTable, HypothesisPlan
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig

FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"

Ref = tuple[str, str]
PIT: Ref = ("tier0_pit_residuals", "pit_ks_two_sample")
MMD: Ref = ("tier0_mmd_hamming", "mmd_hamming")
PROBE: Ref = ("self_referential_probe", "experience_claim_rate")
OPINION: Ref = ("opinion_quantum_sampling", "actual_main_effect_axis_score")
MEQ: Ref = ("altered_state_meq", "meq30_total")

EPSILON_ARM = "prng_epsilon_003"
#: Raw p-values that are clearly below and clearly above any level a study registers.
SMALL = 0.0001
LARGE = 0.5

Config = dict[str, Any]
Table = dict[str, Any]


@pytest.fixture(scope="module")
def cards() -> dict[str, EvalCard]:
    return load_cards()


def _config(
    *,
    claim: str = "difference_or_equivalence",
    epsilon: float | None = 0.03,
    control_available: bool = True,
    confirmatory: tuple[Ref, ...] = (PIT, MMD, PROBE),
    alpha: float = 0.05,
) -> Config:
    """The designed Study 1 template with its claim, controls and outcomes set."""
    config: Config = json.loads((FIXTURES / "study1.designed.json").read_text(encoding="utf-8"))
    config["design"].update(
        claim=claim,
        sensitivity_epsilon=epsilon,
        alpha=alpha,
        confirmatory=[
            {"card_id": card_id, "endpoint": endpoint} for card_id, endpoint in confirmatory
        ],
    )
    if control_available:
        arm = next(a for a in config["arms"] if a["id"] == EPSILON_ARM)
        arm.update(availability="available", unavailable_reason=None)
    return config


def _plan(config: Config, cards: dict[str, EvalCard]) -> StatisticalPlan:
    return StatisticalPlan.from_config(StudyConfig.model_validate(config), cards)


def _write_run(
    run_dir: Path,
    *,
    kind: str = "full",
    created_at: str = "2026-10-01T12:00:00+00:00",
    state: str | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {"run_id": run_dir.name, "kind": kind, "created_at": created_at}
    if state is not None:
        record["state"] = state
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
    return run_dir


def _planned_generations(plan: StatisticalPlan) -> dict[str, int]:
    """Per selected eval that generates, the replies one arm is planned to produce."""
    planned: dict[str, int] = {}
    for snapshot in json.loads(plan.cards_json):
        if snapshot["est_tokens_per_sample"] <= 0:
            continue
        endpoint = next(e for e in plan.endpoints if e.card_id == snapshot["id"])
        cells = 3 if snapshot["told_actual"] else 1
        planned[snapshot["id"]] = endpoint.items * (endpoint.k or 1) * cells
    return planned


class Case:
    """A designed plan with comparison rows in which nothing differs and nothing is missing.

    Every comparison starts out computed with p = 0.5 on the full planned
    data. A test then changes the few rows it is about and calls ``decide``.
    """

    def __init__(self, plan: StatisticalPlan, run_dir: Path) -> None:
        assert plan.design is not None
        self.plan = plan
        self.run_dir = run_dir
        self.hypotheses = plan.design.hypotheses
        self.tables: dict[str, Table] = {}
        for h in self.hypotheses:
            rows: list[dict[str, Any]] = []
            for index, contrast in enumerate(plan.contrasts):
                row: dict[str, Any] = {
                    "id": f"{contrast.kind}:{index}",
                    "kind": contrast.kind,
                    "available": True,
                    "effect": 0.01,
                    "p_value": LARGE,
                }
                if h.test == "paired_permutation":
                    row.update(n_pairs=h.planned_pairs, ci_lower=-0.02, ci_upper=0.04)
                    if h.margin is not None:
                        row["equivalence"] = {"margin": h.margin, "p_value": LARGE}
                rows.append(row)
            self.tables[f"{h.card_id}--{h.endpoint}"] = {"comparisons": rows}
        # Every arm produced, on every eval that generates, what the eval was
        # planned to: eval -> arm -> generations with token records.
        arms = {arm for c in plan.contrasts for arm in (*c.a, *c.b)}
        self.generations: dict[str, dict[str, int]] = {
            card_id: dict.fromkeys(arms, planned)
            for card_id, planned in _planned_generations(plan).items()
        }
        # Of those, the generations the KS test can read. ``None``: all of them.
        self.bracketed: dict[str, dict[str, int]] | None = None

    def hypothesis(self, ref: Ref, sampling: str | None = None) -> HypothesisPlan:
        """The hypothesis on ``ref``: the first cell's, or that of the ``sampling`` cell."""
        return next(
            h
            for h in self.hypotheses
            if (h.card_id, h.endpoint) == ref and sampling in (None, h.sampling)
        )

    def _rows(self, ref: Ref) -> list[dict[str, Any]]:
        return self.tables[f"{ref[0]}--{ref[1]}"]["comparisons"]  # type: ignore[no-any-return]

    def source(self, ref: Ref, sampling: str | None = None) -> dict[str, Any]:
        """The quantum-vs-control comparison of the hypothesis on ``ref``."""
        return self._rows(ref)[self.hypothesis(ref, sampling).contrast]

    def seed_pairs(self, ref: Ref, sampling: str | None = None) -> list[dict[str, Any]]:
        return [self._rows(ref)[i] for i in self.hypothesis(ref, sampling).negative_controls]

    def positive(self, ref: Ref, sampling: str | None = None) -> dict[str, Any]:
        index = self.hypothesis(ref, sampling).positive_control
        assert index is not None
        return self._rows(ref)[index]

    def missing(self, row: dict[str, Any]) -> None:
        """Turn a comparison into one the executor could not compute."""
        row.clear()
        row.update(available=False, reason="required rows are unavailable")

    def decide(self) -> DecisionsTable:
        tables = copy.deepcopy(self.tables)
        decisions.apply(
            tables,
            self.plan,
            run_dir=self.run_dir,
            generations_present=self.generations,
            generations_bracketed=self.bracketed,
        )
        return DecisionsTable.model_validate(tables["decisions"])

    def row(self, ref: Ref, sampling: str | None = None) -> DecisionRow:
        wanted = self.hypothesis(ref, sampling).id
        return next(r for r in self.decide().rows if r.hypothesis_id == wanted)


@pytest.fixture()
def case(cards: dict[str, EvalCard], tmp_path: Path) -> Callable[..., Case]:
    """Build a :class:`Case` for a variant of the designed Study 1 config."""

    def build(**variant: Any) -> Case:
        return Case(_plan(_config(**variant), cards), _write_run(tmp_path / "runs" / "run_full"))

    return build


# ── one case per verdict and per reason ──────────────────────────────────────


def test_unavailable_when_the_comparison_was_not_produced(case: Callable[..., Case]) -> None:
    study = case()
    study.missing(study.source(PROBE))
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("unavailable", "not_produced")
    assert row.wording == "Not assessed: the required data were not produced."
    # Nothing is reported for a comparison that does not exist.
    assert row.estimate is None and row.p_value is None and row.p_adjusted is None
    assert row.completeness is None and row.above_seed_noise is None


def test_invalid_when_too_few_planned_pairs_were_analysed(case: Callable[..., Case]) -> None:
    study = case()
    planned = study.hypothesis(PROBE).planned_pairs
    study.source(PROBE)["n_pairs"] = int(planned * 0.89)
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("invalid", "incomplete")
    assert row.completeness == pytest.approx(int(planned * 0.89) / planned)
    assert row.wording == "No conclusion: fewer than 90% of the planned pairs were analysed."

    study.source(PROBE)["n_pairs"] = int(planned * 0.9)
    assert study.row(PROBE).reason != "incomplete"


def test_token_stream_completeness_is_the_scarcest_arm(case: Callable[..., Case]) -> None:
    study = case()
    planned = study.hypothesis(PIT).planned_pairs
    assert planned == study.hypothesis(MMD).planned_pairs > 0
    assert study.row(PIT).completeness == study.row(MMD).completeness == 1.0
    # One arm lost every reply of the largest eval: 40 000 of its 89 500.
    lost = study.generations["thurstonian_self_outcomes"].pop("prng_seed_7")
    assert lost == 40_000
    for ref in (PIT, MMD):
        row = study.row(ref)
        assert (row.verdict, row.reason) == ("invalid", "incomplete")
        assert row.completeness == pytest.approx((planned - lost) / planned)
    # The paired endpoint counts its own pairs, not the run's generations.
    assert study.row(PROBE).completeness == 1.0

    # An arm with no token records at all is an arm with nothing present.
    for by_arm in study.generations.values():
        by_arm.pop("prng_seed_7", None)
    assert study.row(PIT).completeness == 0.0


def test_token_stream_completeness_counts_the_planned_evals_only(
    case: Callable[..., Case],
) -> None:
    """What was planned and what is present are counted on the same evals."""
    study = case()
    planned = study.hypothesis(PIT).planned_pairs
    counted = decisions.stream_cards(study.plan)
    derived = {"source_guessing", "self_reported_coincidence"}
    assert derived <= set(study.generations) and not derived & counted
    # The plan froze the count of exactly these evals.
    assert sum(_planned_generations(study.plan)[card] for card in counted) == planned
    assert not any(card.startswith("tier0_") for card in counted)

    # A derived eval asks as many prompts as its source eval's replies allow.
    # Producing none, or far more than expected, says nothing about whether
    # the planned data are complete.
    for card in derived:
        study.generations[card] = dict.fromkeys(study.generations[card], 0)
    assert study.row(PIT).completeness == study.row(MMD).completeness == 1.0
    assert study.row(PIT).reason != "incomplete"
    for card in derived:
        study.generations[card] = dict.fromkeys(study.generations[card], 10**6)
    assert study.row(PIT).completeness == 1.0

    # And extra replies of a derived eval do not stand in for missing planned ones.
    short = planned * 0.85
    lost = study.generations["thurstonian_self_outcomes"]["qrng_unsupervised"] - 30_000
    study.generations["thurstonian_self_outcomes"]["qrng_unsupervised"] = 30_000
    assert planned - lost < short + 5_000
    row = study.row(MMD)
    assert (row.verdict, row.reason) == ("invalid", "incomplete")
    assert row.completeness == pytest.approx((planned - lost) / planned)

    # Token records filed under an eval the plan does not know are ignored.
    study.generations["not_in_the_plan"] = dict.fromkeys(
        study.generations["source_guessing"], 10**6
    )
    assert study.row(MMD).completeness == pytest.approx((planned - lost) / planned)


def test_invalid_when_two_seeds_differ(case: Callable[..., Case]) -> None:
    study = case()
    study.seed_pairs(PROBE)[1]["p_value"] = 1e-6
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("invalid", "negative_control")
    assert row.negative_control.passed is False
    assert row.negative_control.n_pairs == 3
    # One Holm family over 3 seed pairs x 3 hypotheses.
    assert row.negative_control.min_p_adjusted == pytest.approx(9e-6)
    assert row.wording.startswith("No conclusion: two seeds of the same pseudo-random generator")
    # The seed pairs of another endpoint are that endpoint's own control.
    assert study.row(PIT).negative_control.passed is True


def test_invalid_when_a_seed_control_is_missing(case: Callable[..., Case]) -> None:
    study = case()
    study.missing(study.seed_pairs(PIT)[0])
    row = study.row(PIT)
    assert (row.verdict, row.reason) == ("invalid", "negative_control")
    assert row.negative_control.passed is False
    assert row.negative_control.min_p_adjusted == pytest.approx(1.0)


def test_effect_within_margin(case: Callable[..., Case]) -> None:
    study = case()
    study.source(PROBE)["p_value"] = SMALL
    study.source(PROBE)["equivalence"]["p_value"] = SMALL
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("effect_within_margin", None)
    assert row.p_adjusted == pytest.approx(3 * SMALL)
    # Only the probe has a margin, so its equivalence family has one member.
    assert row.p_equivalence_adjusted == pytest.approx(SMALL)
    assert row.wording == (
        "A difference on Experience-claim rate is detectable but smaller than ±0.05."
    )


def test_effect(case: Callable[..., Case]) -> None:
    study = case()
    study.source(PROBE)["p_value"] = SMALL
    study.source(PIT)["p_value"] = SMALL
    probe, pit = study.row(PROBE), study.row(PIT)
    assert (probe.verdict, probe.reason) == ("effect", None)
    assert probe.wording == "Quantum and pseudo-random sampling differ on Experience-claim rate."
    # A test without a margin can only ever show a difference.
    assert (pit.verdict, pit.reason) == ("effect", None)
    assert pit.margin is None and pit.p_equivalence is None


def test_equivalent(case: Callable[..., Case]) -> None:
    study = case()
    study.source(PROBE)["equivalence"]["p_value"] = SMALL
    study.positive(PROBE)["p_value"] = SMALL
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("equivalent", None)
    assert row.positive_control.status == "passed"
    assert row.positive_control.epsilon == 0.03
    assert row.wording == (
        "No difference larger than ±0.05 on Experience-claim rate; "
        "the assay detected a sampler bias of ε = 0.03."
    )


def test_inconclusive_equivalence_not_claimed(case: Callable[..., Case]) -> None:
    # A study that registered only a difference claim never returns "equivalent",
    # however tight the interval.
    study = case(claim="difference", epsilon=None, control_available=False)
    study.source(PROBE)["equivalence"]["p_value"] = SMALL
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("inconclusive", "equivalence_not_claimed")
    assert row.wording == (
        "Neither a difference nor its absence is established on Experience-claim rate "
        "(the study registered no equivalence claim)."
    )


def test_inconclusive_equivalence_not_testable(case: Callable[..., Case]) -> None:
    study = case()
    study.positive(PIT)["p_value"] = SMALL
    for ref in (PIT, MMD):
        row = study.row(ref)
        assert (row.verdict, row.reason) == ("inconclusive", "equivalence_not_testable")
    assert study.row(PIT).wording.endswith("(this test has no equivalence margin).")


def test_inconclusive_no_margin(case: Callable[..., Case]) -> None:
    study = case(confirmatory=(PROBE, OPINION))
    assert study.hypothesis(OPINION).margin is None
    study.positive(OPINION)["p_value"] = SMALL
    row = study.row(OPINION)
    assert (row.verdict, row.reason) == ("inconclusive", "no_margin")
    assert row.p_equivalence is None and row.p_equivalence_adjusted is None
    assert row.wording.endswith("(no margin was registered for this outcome).")


def test_inconclusive_interval_exceeds_margin(case: Callable[..., Case]) -> None:
    study = case()
    study.positive(PROBE)["p_value"] = SMALL
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("inconclusive", "interval_exceeds_margin")
    assert row.p_equivalence == LARGE
    assert row.wording.endswith("(the equivalence test did not pass after correction).")


def test_inconclusive_positive_control_absent(case: Callable[..., Case]) -> None:
    # The biased arm is registered but switched off: the plan has no contrast for it.
    study = case(control_available=False)
    assert study.hypothesis(PROBE).positive_control is None
    study.source(PROBE)["equivalence"]["p_value"] = SMALL
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("inconclusive", "positive_control_absent")
    assert row.positive_control.status == "absent"
    assert row.positive_control.p_value is None
    assert row.wording.endswith("(the positive control was not run).")

    # Planned, but the run did not produce it: absent all the same.
    study = case()
    study.source(PROBE)["equivalence"]["p_value"] = SMALL
    study.missing(study.positive(PROBE))
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("inconclusive", "positive_control_absent")


def test_inconclusive_positive_control_failed(case: Callable[..., Case]) -> None:
    study = case()
    study.source(PROBE)["equivalence"]["p_value"] = SMALL
    study.positive(PROBE)["p_value"] = 0.2
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("inconclusive", "positive_control_failed")
    assert row.positive_control.status == "failed"
    assert row.positive_control.p_value == 0.2
    assert row.wording.endswith("(the positive control was not detected).")


def test_every_verdict_and_reason_is_covered() -> None:
    """The matrix has no row or reason beyond the ones the cases above reach."""
    reached = {
        ("unavailable", "not_produced"),
        ("invalid", "incomplete"),
        ("invalid", "negative_control"),
        ("invalid", "negative_control_cannot_fail"),
        ("effect_within_margin", None),
        ("effect", None),
        ("equivalent", None),
        ("inconclusive", "equivalence_not_claimed"),
        ("inconclusive", "equivalence_not_testable"),
        ("inconclusive", "no_margin"),
        ("inconclusive", "interval_exceeds_margin"),
        ("inconclusive", "positive_control_absent"),
        ("inconclusive", "positive_control_failed"),
    }
    registered = {
        (rule.verdict, reason) for rule in VERDICT_RULES for reason in (rule.reasons or (None,))
    }
    assert registered == reached


# ── order, families, confirmatory runs, roll-up ──────────────────────────────


def test_first_match_order(case: Callable[..., Case]) -> None:
    """Every later row's condition holds too; the earliest row decides."""
    study = case()
    planned = study.hypothesis(PROBE).planned_pairs
    source = study.source(PROBE)
    source.update(p_value=SMALL, n_pairs=planned // 2)
    source["equivalence"]["p_value"] = SMALL
    study.seed_pairs(PROBE)[0]["p_value"] = 1e-6
    study.positive(PROBE)["p_value"] = SMALL
    kept = copy.deepcopy(source)

    study.missing(source)
    assert study.row(PROBE).verdict == "unavailable"

    source.clear()
    source.update(kept)
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("invalid", "incomplete")

    source["n_pairs"] = planned
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("invalid", "negative_control")

    study.seed_pairs(PROBE)[0]["p_value"] = LARGE
    assert study.row(PROBE).verdict == "effect_within_margin"

    source["equivalence"]["p_value"] = LARGE
    assert study.row(PROBE).verdict == "effect"

    source["p_value"] = LARGE
    source["equivalence"]["p_value"] = SMALL
    assert study.row(PROBE).verdict == "equivalent"

    study.positive(PROBE)["p_value"] = LARGE
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("inconclusive", "positive_control_failed")


def test_matrix_rows_are_tried_in_registered_order() -> None:
    everything_wrong = decisions.Gates(
        available=False,
        complete=False,
        negative_passed=False,
        differs=True,
        margin=True,
        within_margin=True,
        equivalence_claimed=True,
        paired=True,
        positive="passed",
    )
    assert [rule.number for rule in VERDICT_RULES] == [1, 2, 3, 4, 5, 6, 7]
    gates = everything_wrong
    # Repairing the gate a row asks about hands the hypothesis to the next row.
    repairs: list[dict[str, Any]] = [
        {},
        {"available": True},
        {"complete": True},
        {"negative_passed": True},
        {"within_margin": False},
        {"differs": False, "within_margin": True},
        {"positive": "failed"},
    ]
    numbers = []
    for repair in repairs:
        gates = dataclasses.replace(gates, **repair)
        numbers.append(decisions.resolve(gates)[0].number)
    assert numbers == [1, 2, 3, 4, 5, 6, 7]


def test_unavailable_keeps_family_slot(case: Callable[..., Case]) -> None:
    study = case()
    study.source(PIT)["p_value"] = 0.02
    study.missing(study.source(MMD))
    for pair in study.seed_pairs(MMD):
        study.missing(pair)
    study.seed_pairs(PROBE)[0]["p_value"] = 0.006

    pit, mmd, probe = study.row(PIT), study.row(MMD), study.row(PROBE)
    # Three difference tests were registered; the one that was not produced
    # still counts. In a family of two, 0.02 would have passed at 0.04.
    assert pit.p_adjusted == pytest.approx(0.06)
    assert pit.verdict == "inconclusive"
    assert mmd.verdict == "unavailable" and mmd.p_adjusted is None
    # Nine seed pairs were registered. In a family of six, 0.006 would have
    # failed the control at 0.036.
    assert probe.negative_control.min_p_adjusted == pytest.approx(0.054)
    assert probe.negative_control.passed is True
    assert study.decide().families.model_dump() == {
        "difference": 3,
        "equivalence": 1,
        "negative_control": 9,
    }


def test_pilot_run_not_confirmatory(cards: dict[str, EvalCard], tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path / "runs" / "run_pilot", kind="pilot")
    study = Case(_plan(_config(), cards), run_dir)
    study.source(PROBE)["p_value"] = SMALL
    table = study.decide()
    assert table.confirmatory is False
    assert table.non_confirmatory_reason == "pilot_run"
    assert table.study_verdict == "not_confirmatory"
    # The rows still resolve, so a pilot shows what the full run would decide,
    # but every sentence says it confirms nothing.
    probe = next(r for r in table.rows if r.card_id == PROBE[0])
    assert probe.verdict == "effect"
    assert probe.wording == (
        "Not confirmatory: Quantum and pseudo-random sampling differ on Experience-claim rate."
    )
    assert all(r.wording.startswith("Not confirmatory: ") for r in table.rows)


def test_second_full_run_not_confirmatory(cards: dict[str, EvalCard], tmp_path: Path) -> None:
    plan = _plan(_config(), cards)
    runs = tmp_path / "runs"
    pilot = _write_run(runs / "run_a_pilot", kind="pilot", created_at="2026-10-01T09:00:00+00:00")
    first = _write_run(runs / "run_b_full", created_at="2026-10-02T09:00:00+00:00")
    second = _write_run(runs / "run_c_full", created_at="2026-10-03T09:00:00+00:00")

    assert Case(plan, pilot).decide().non_confirmatory_reason == "pilot_run"

    # An earlier pilot does not use up the confirmatory run.
    table = Case(plan, first).decide()
    assert table.confirmatory is True and table.non_confirmatory_reason is None
    assert table.study_verdict != "not_confirmatory"
    assert not any(r.wording.startswith("Not confirmatory") for r in table.rows)

    table = Case(plan, second).decide()
    assert table.confirmatory is False
    assert table.non_confirmatory_reason == "not_first_full_run"
    assert table.study_verdict == "not_confirmatory"
    assert all(r.wording.startswith("Not confirmatory: ") for r in table.rows)

    # Offsets are compared as instants, not as text: 08:30+02:00 is before 09:00Z.
    earlier = _write_run(runs / "run_d_full", created_at="2026-10-02T08:30:00+02:00")
    assert Case(plan, earlier).decide().confirmatory is True
    assert Case(plan, first).decide().non_confirmatory_reason == "not_first_full_run"


def test_study_rollup(case: Callable[..., Case]) -> None:
    def rollup(*verdicts: str) -> str:
        return decisions.study_verdict(verdicts, confirmatory=True)  # type: ignore[arg-type]

    assert rollup("effect", "invalid", "equivalent") == "invalid"
    assert rollup("effect", "equivalent", "inconclusive", "unavailable") == "effect"
    assert rollup("equivalent", "equivalent") == "no_effect_within_margins"
    assert rollup("equivalent", "inconclusive") == "mixed_or_inconclusive"
    assert rollup("equivalent", "unavailable") == "mixed_or_inconclusive"
    # A difference inside the margin is neither an effect nor its absence.
    assert rollup("effect_within_margin", "equivalent") == "mixed_or_inconclusive"
    assert rollup() == "mixed_or_inconclusive"
    assert decisions.study_verdict(["effect"], confirmatory=False) == "not_confirmatory"

    # The same through the table. Nothing differs: mixed.
    study = case()
    assert study.decide().study_verdict == "mixed_or_inconclusive"
    # One effect decides the study.
    study.source(PIT)["p_value"] = SMALL
    assert study.decide().study_verdict == "effect"
    # One failed control invalidates it, the effect notwithstanding.
    study.seed_pairs(MMD)[2]["p_value"] = 1e-6
    table = study.decide()
    assert table.study_verdict == "invalid"
    assert {r.verdict for r in table.rows} == {"effect", "invalid", "inconclusive"}

    # Absence is the study's verdict only when every hypothesis shows it.
    only_probe = case(confirmatory=(PROBE,))
    only_probe.source(PROBE)["equivalence"]["p_value"] = SMALL
    only_probe.positive(PROBE)["p_value"] = SMALL
    assert only_probe.decide().study_verdict == "no_effect_within_margins"


def test_above_seed_noise_is_reported_not_gated(case: Callable[..., Case]) -> None:
    study = case()
    source = study.source(PROBE)
    source.update(p_value=SMALL, effect=-0.03)
    for pair, effect in zip(study.seed_pairs(PROBE), (0.01, -0.05, 0.02), strict=True):
        pair["effect"] = effect
    row = study.row(PROBE)
    # Smaller than the largest seed-pair difference, and an effect all the same.
    assert row.above_seed_noise is False
    assert row.verdict == "effect"

    source["effect"] = -0.06
    assert study.row(PROBE).above_seed_noise is True


def test_legacy_plan_has_no_decisions(cards: dict[str, EvalCard], tmp_path: Path) -> None:
    config = _config()
    del config["design"]
    plan = _plan(config, cards)
    with pytest.raises(ValueError, match="design block"):
        decisions.apply({}, plan, run_dir=tmp_path, generations_present={})


# ── end to end ───────────────────────────────────────────────────────────────

ALPHA = 0.01
PROMPTS, EPOCHS, TOKENS = 12, 10, 6
TOLD = ("told_qrng", "told_prng", "told_nothing")
CARD = "self_referential_probe"
RUN_ID = "run_full"


def _small_config(*, design: bool = True) -> Config:
    """Designed Study 1 cut down to the three confirmatory cards and 12 prompts."""
    config = _config(alpha=ALPHA)
    config["cards"] = [
        {"card_id": "tier0_pit_residuals", "overrides": {"k": None, "items": None}},
        {"card_id": "tier0_mmd_hamming", "overrides": {"k": None, "items": None}},
        {"card_id": CARD, "overrides": {"k": EPOCHS, "items": PROMPTS}},
    ]
    if not design:
        del config["design"]
    return config


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_bundle(root: Path, plan: StatisticalPlan) -> Path:
    """A study directory with one full run in which only the biased arm differs.

    Judge scores are common random numbers per (prompt, epoch, told) with a few
    arm-specific flips, so quantum and seeded arms agree closely; the biased
    arm always scores 1. Tokens and their CDF brackets are drawn alike on every
    arm except the biased one.
    """
    rng = np.random.default_rng(20261001)
    base = rng.integers(0, 2, size=(PROMPTS, EPOCHS, len(TOLD)))
    arms = sorted({arm for contrast in plan.contrasts for arm in (*contrast.a, *contrast.b)})
    run_dir = _write_run(root / "runs" / RUN_ID)
    (root / "prereg-plan.json").write_text(
        plan.canonical_json() + "\n", encoding="utf-8", newline="\n"
    )
    for arm in arms:
        biased = arm == EPSILON_ARM
        generations, judged, tokens = [], [], []
        for p in range(PROMPTS):
            for e in range(EPOCHS):
                for t, told in enumerate(TOLD):
                    ref = f"{arm}/{CARD}/p{p}/{e}/{told}"
                    flipped = rng.random() < 0.005
                    score = 1 if biased else int(base[p, e, t]) ^ int(flipped)
                    generations.append({"sample_ref": ref, "finish_reason": "stop"})
                    judged.append({"sample_ref": ref, "score": score})
                    for index in range(TOKENS):
                        lo = float(rng.uniform(0.6, 0.8) if biased else rng.uniform(0.0, 0.8))
                        tokens.append(
                            {
                                "sample_ref": ref,
                                "token_index": index,
                                "token_id": int(rng.integers(0, 2 if biased else 6)),
                                "logprob": -0.5,
                                "rank_raw": 0,
                                "entropy_raw_top20": 0.5,
                                "u": lo + 0.1,
                                "cdf_lo": lo,
                                "cdf_hi": lo + 0.2,
                                "timestamp_ns": 1_000_000_000,
                            }
                        )
        card_dir = run_dir / "arms" / arm / "cards" / CARD
        _jsonl(card_dir / "generations.jsonl", generations)
        _jsonl(card_dir / "judge" / "study_judge.jsonl", judged)
        _jsonl(card_dir / "tier0.jsonl", tokens)
    return run_dir


@pytest.fixture(scope="module")
def designed_run(
    cards: dict[str, EvalCard], tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, StatisticalPlan, dict[str, Table]]:
    """The generated run, analysed once: its directory, plan and tables as written."""
    root = tmp_path_factory.mktemp("designed_bundle")
    plan = _plan(_small_config(), cards)
    _write_bundle(root, plan)
    analysis_id = run(root, plan, run_id=RUN_ID)
    assert analysis_id == f"a_{plan.plan_hash[:16]}"
    directory = root / "runs" / RUN_ID / "analysis" / analysis_id / "tables"
    tables = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    }
    return root, plan, tables


@pytest.fixture(scope="module")
def legacy_tables(
    cards: dict[str, EvalCard], designed_run: tuple[Path, StatisticalPlan, dict[str, Table]]
) -> dict[str, Table]:
    """The same run analysed under the plan it would have had without a design block."""
    root, _plan_, _tables = designed_run
    legacy = _plan(_small_config(design=False), cards)
    return execute(load_data(root / "runs" / RUN_ID), legacy)


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def test_designed_run_writes_decisions(
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
) -> None:
    _root, plan, tables = designed_run
    assert plan.design is not None
    table = DecisionsTable.model_validate(tables["decisions"])
    assert [r.hypothesis_id for r in table.rows] == ["H1", "H2", "H3"]
    assert (table.alpha, table.claim) == (ALPHA, "difference_or_equivalence")
    assert table.confirmatory is True and table.non_confirmatory_reason is None
    assert table.families == plan.design.families

    pit, mmd, probe = table.rows
    # Every generation of the run left token records, on every arm.
    assert pit.completeness == mmd.completeness == probe.completeness == 1.0
    for row in table.rows:
        assert row.negative_control.passed is True and row.negative_control.n_pairs == 3
        assert row.positive_control.status == "passed"
        assert row.ci_level == 0.99
    # Quantum and seeded arms were drawn alike and the biased arm was detected.
    assert (pit.verdict, pit.reason) == ("inconclusive", "equivalence_not_testable")
    assert (mmd.verdict, mmd.reason) == ("inconclusive", "equivalence_not_testable")
    assert (probe.verdict, probe.reason) == ("equivalent", None)
    assert table.study_verdict == "mixed_or_inconclusive"
    assert probe.planned_mde_raw == pytest.approx(
        plan.design.hypotheses[2].sample_size.mde_raw, abs=1e-6
    )

    # Each row restates the comparison it was decided on.
    source = tables[f"{CARD}--experience_claim_rate"]["comparisons"][
        plan.design.hypotheses[2].contrast
    ]
    assert source["kind"] == "source"
    assert probe.estimate == source["effect"]
    assert (probe.ci_lower, probe.ci_upper) == (source["ci_lower"], source["ci_upper"])
    assert probe.p_value == source["p_value"]
    assert probe.p_equivalence == source["equivalence"]["p_value"]


def test_designed_rows_use_the_registered_level_and_margin(
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
) -> None:
    _root, plan, tables = designed_run
    assert plan.design is not None
    # Three difference tests at alpha 0.01 ask for 20 x 3 / 0.01 = 6 000 and the nine
    # seed-pair tests for 2 x 9 / 0.01 = 1 800, so the floor of 10 000 applies.
    assert plan.permutations == 10_000
    rows = tables[f"{CARD}--experience_claim_rate"]["comparisons"]
    assert len(rows) == len(plan.contrasts)
    for row, contrast in zip(rows, plan.contrasts, strict=True):
        assert (row["model"], row["sampling"]) == (contrast.model, contrast.sampling)
        assert row["n_permutations"] == plan.permutations
        # The interval is at 1 - alpha, not the 95% every legacy table carries.
        assert row["ci_level"] == 0.99
        # TOST at the plan's alpha against the margin fixed at registration:
        # a 1 - 2 alpha interval, and a verdict at alpha rather than at 0.05.
        equivalence = row["equivalence"]
        assert equivalence["margin"] == 0.05
        assert equivalence["ci_level"] == 0.98
        assert equivalence["equivalent"] == (equivalence["p_value"] < ALPHA)
    # An endpoint without a registered margin has no equivalence test at all.
    for name in ("tier0_pit_residuals--pit_ks_two_sample", f"{CARD}--told_main_effect"):
        assert all("equivalence" not in row for row in tables[name]["comparisons"])
        assert all("model" in row and "sampling" in row for row in tables[name]["comparisons"])


def test_designed_margin_needs_no_pilot_file(
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
    legacy_tables: dict[str, Table],
) -> None:
    """The legacy plan on the same run has a SESOI and no pilot file: no equivalence test."""
    root, _plan_, _tables = designed_run
    assert not (root / "runs" / "pilot").exists()
    assert "decisions" not in legacy_tables
    for row in legacy_tables[f"{CARD}--experience_claim_rate"]["comparisons"]:
        assert row["equivalence"]["available"] is False
        assert row["ci_level"] == 0.95
        assert "model" not in row and "sampling" not in row


def test_no_significant_at_05(
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
    legacy_tables: dict[str, Table],
) -> None:
    _root, _plan_, tables = designed_run
    assert len(tables) > 3
    assert "significant_at_05" not in _keys(tables)
    # The flag is still what the primitives return: the legacy analysis of the
    # same run carries it, in comparison rows and in nested blocks alike.
    probe = legacy_tables[f"{CARD}--experience_claim_rate"]
    assert "significant_at_05" in _keys(probe["comparisons"])
    assert "significant_at_05" in _keys(probe["told_actual"])
    assert "significant_at_05" in _keys(legacy_tables["tier0_mmd_hamming--mmd_hamming"])


def test_numbers_pass_verify_numbers(
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
) -> None:
    root, plan, tables = designed_run
    written = load_tables(
        root / "runs" / RUN_ID / "analysis" / f"a_{plan.plan_hash[:16]}" / "tables"
    )
    assert written["decisions"].encode("utf-8") == table_bytes(tables["decisions"])

    table = tables["decisions"]
    probe = table["rows"][2]
    narrative = "\n".join(
        [
            f"At alpha {table['alpha']} the study registered {table['families']['difference']} "
            f"confirmatory tests and {table['families']['negative_control']} seed controls",
            *(row["wording"] for row in table["rows"]),
            f"{probe['hypothesis_id']}: estimate {probe['estimate']} "
            f"({probe['ci_level']} interval {probe['ci_lower']} to {probe['ci_upper']}), "
            f"adjusted p {probe['p_adjusted']}, equivalence p {probe['p_equivalence_adjusted']} "
            f"against ±{probe['margin']}, completeness {probe['completeness']}, "
            f"positive control at ε {probe['positive_control']['epsilon']} "
            f"with p {probe['positive_control']['p_value']}, "
            f"smallest adjusted seed-control p {probe['negative_control']['min_p_adjusted']} "
            f"and a planned detectable difference of {probe['planned_mde_raw']}",
        ]
    )
    # The sentences carry the table's numbers as numbers the verifier reads.
    cited = [
        table["alpha"],
        probe["estimate"],
        probe["ci_level"],
        probe["ci_lower"],
        probe["ci_upper"],
        probe["p_adjusted"],
        probe["p_equivalence_adjusted"],
        probe["margin"],
        probe["completeness"],
        probe["positive_control"]["epsilon"],
        probe["positive_control"]["p_value"],
        probe["negative_control"]["min_p_adjusted"],
        probe["planned_mde_raw"],
    ]
    assert all(isinstance(value, float) for value in cited)
    assert {str(value) for value in cited} <= set(numeric_tokens(narrative))
    assert verify_numbers(narrative, written) == []
    assert verify_numbers(narrative, {"decisions": table}) == []
    # A number the table does not hold is still caught.
    invented = narrative + "\nThe margin was ±0.07 on this outcome"
    assert verify_numbers(invented, written) == ["0.07"]


# ── the confirmatory status travels with the run ─────────────────────────────


def test_run_order_is_recorded_once_and_survives_without_the_other_runs(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    plan = _plan(_config(), cards)
    runs = tmp_path / "study" / "runs"
    _write_run(runs / "run_a_pilot", kind="pilot", created_at="2026-10-01T09:00:00+00:00")
    first = _write_run(runs / "run_b_full", created_at="2026-10-02T09:00:00+00:00")
    second = _write_run(runs / "run_c_full", created_at="2026-10-03T09:00:00+00:00")

    for run_dir in (first, second):
        decisions.record_run_order(run_dir)
    record = json.loads((second / decisions.RUN_ORDER_FILE).read_text(encoding="utf-8"))
    assert record["schema_version"] == 1 and record["run_id"] == "run_c_full"
    assert record["created_at"] == "2026-10-03T09:00:00+00:00"
    # The pilot is not a full run and does not use up the confirmatory one.
    assert record["earlier_full_runs"] == ["run_b_full"]
    assert (
        json.loads((first / decisions.RUN_ORDER_FILE).read_text(encoding="utf-8"))[
            "earlier_full_runs"
        ]
        == []
    )

    # A copy of the second run alone, as someone unpacks one run of a bundle.
    # Without the record nothing there says that another full run came first.
    alone = tmp_path / "elsewhere" / "runs" / "run_c_full"
    shutil.copytree(second, alone)
    assert decisions.non_confirmatory_reason(alone) == "not_first_full_run"
    table = Case(plan, alone).decide()
    assert (table.confirmatory, table.non_confirmatory_reason) == (False, "not_first_full_run")
    assert table.study_verdict == "not_confirmatory"
    (alone / decisions.RUN_ORDER_FILE).rename(alone / "set-aside.json")
    assert decisions.non_confirmatory_reason(alone) is None

    # What the first analysis found stands: the record is not rewritten when
    # the directory around the run changes.
    written = (first / decisions.RUN_ORDER_FILE).read_bytes()
    _write_run(runs / "run_0_restored", created_at="2026-09-30T09:00:00+00:00")
    decisions.record_run_order(first)
    assert (first / decisions.RUN_ORDER_FILE).read_bytes() == written
    assert decisions.non_confirmatory_reason(first) is None
    # A run analysed for the first time after that is compared with what is there now.
    third = _write_run(runs / "run_d_full", created_at="2026-10-04T09:00:00+00:00")
    decisions.record_run_order(third)
    assert json.loads((third / decisions.RUN_ORDER_FILE).read_text(encoding="utf-8"))[
        "earlier_full_runs"
    ] == ["run_0_restored", "run_b_full", "run_c_full"]


def test_run_order_is_not_recorded_for_a_pilot_or_an_undated_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    pilot = _write_run(runs / "run_pilot", kind="pilot")
    decisions.record_run_order(pilot)
    assert not (pilot / decisions.RUN_ORDER_FILE).exists()
    assert decisions.non_confirmatory_reason(pilot) == "pilot_run"
    # A pilot stays a pilot whatever a record beside it claims.
    (pilot / decisions.RUN_ORDER_FILE).write_text(
        json.dumps({"schema_version": 1, "earlier_full_runs": []}), encoding="utf-8"
    )
    assert decisions.non_confirmatory_reason(pilot) == "pilot_run"

    undated = runs / "run_undated"
    undated.mkdir()
    (undated / "run.json").write_text(json.dumps({"kind": "full"}), encoding="utf-8")
    decisions.record_run_order(undated)
    assert not (undated / decisions.RUN_ORDER_FILE).exists()

    # A record that cannot be read is no record: the runs beside this one decide.
    first = _write_run(runs / "run_a_full", created_at="2026-10-02T09:00:00+00:00")
    second = _write_run(runs / "run_b_full", created_at="2026-10-03T09:00:00+00:00")
    for content in ("not json", json.dumps({"schema_version": 2, "earlier_full_runs": []})):
        (second / decisions.RUN_ORDER_FILE).write_text(content, encoding="utf-8")
        assert decisions.non_confirmatory_reason(second) == "not_first_full_run"
    assert decisions.non_confirmatory_reason(first) is None


def test_analysis_records_the_run_order_of_a_designed_study_only(
    cards: dict[str, EvalCard],
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
    tmp_path: Path,
) -> None:
    root, plan, tables = designed_run
    run_dir = root / "runs" / RUN_ID
    record = json.loads((run_dir / decisions.RUN_ORDER_FILE).read_text(encoding="utf-8"))
    assert record["run_id"] == RUN_ID and record["earlier_full_runs"] == []
    # The record is an input of the analysis, hashed like every other.
    manifest = json.loads(
        (run_dir / "analysis" / f"a_{plan.plan_hash[:16]}" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert f"runs/{RUN_ID}/{decisions.RUN_ORDER_FILE}" in manifest["inputs"]

    # The same study with a full run that came first. Analysed where both runs
    # are, the later run confirms nothing; a copy of it alone says the same,
    # and its decisions table is the same to the byte.
    study = tmp_path / "study"
    shutil.copytree(root, study, ignore=shutil.ignore_patterns("analysis", "run-order.json"))
    later = study / "runs" / "run_later"
    shutil.copytree(study / "runs" / RUN_ID, later)
    _write_run(later, created_at="2026-10-05T12:00:00+00:00")
    analysis_id = run(study, plan, run_id="run_later")
    served = (later / "analysis" / analysis_id / "tables" / "decisions.json").read_bytes()
    assert json.loads(served)["non_confirmatory_reason"] == "not_first_full_run"

    alone = tmp_path / "unpacked" / "study"
    shutil.copytree(
        study,
        alone,
        ignore=lambda directory, names: [RUN_ID] if Path(directory).name == "runs" else [],
    )
    assert sorted(p.name for p in (alone / "runs").iterdir()) == ["run_later"]
    shutil.rmtree(alone / "runs" / "run_later" / "analysis")
    assert run(alone, plan, run_id="run_later") == analysis_id
    replayed = alone / "runs" / "run_later" / "analysis" / analysis_id / "tables"
    assert (replayed / "decisions.json").read_bytes() == served
    assert {path.name: path.read_bytes() for path in sorted(replayed.glob("*.json"))} == {
        path.name: path.read_bytes()
        for path in sorted((later / "analysis" / analysis_id / "tables").glob("*.json"))
    }

    # A study without a design block has no first-full-run rule and gets no record.
    legacy_root = tmp_path / "legacy"
    shutil.copytree(
        root,
        legacy_root,
        ignore=shutil.ignore_patterns("analysis", "run-order.json", "prereg-plan.json"),
    )
    run(legacy_root, _plan(_small_config(design=False), cards), run_id=RUN_ID)
    assert not (legacy_root / "runs" / RUN_ID / decisions.RUN_ORDER_FILE).exists()


def test_executor_counts_generations_per_eval_and_arm(
    designed_run: tuple[Path, StatisticalPlan, dict[str, Table]],
) -> None:
    """What the executor hands to the decisions: token records by eval, then by arm."""
    root, plan, _tables = designed_run
    present = _generations_present(load_data(root / "runs" / RUN_ID))
    arms = {arm for contrast in plan.contrasts for arm in (*contrast.a, *contrast.b)}
    assert set(present) == {CARD}
    assert set(present[CARD]) == arms
    # Every told cell of every prompt and epoch, before the told cells are thinned.
    assert set(present[CARD].values()) == {PROMPTS * EPOCHS * len(TOLD)}
    assert decisions.stream_cards(plan) == {CARD}


# ── a control that could not have failed ─────────────────────────────────────


def test_invalid_when_a_seed_control_could_not_have_failed(case: Callable[..., Case]) -> None:
    """A seed pair whose test cannot return a small enough p-value is no control."""
    study = case()
    family = study.plan.design.families.negative_control if study.plan.design else 0
    assert (family, study.plan.alpha, study.plan.permutations) == (9, 0.05, 10_000)

    # Nine seed-pair tests at 0.05: a paired test needs ten prompts before a pair can fail.
    for pair in study.seed_pairs(PROBE):
        pair.update(n_prompts=8, n_permutations=10_000)
    row = study.row(PROBE)
    assert (row.verdict, row.reason) == ("invalid", "negative_control_cannot_fail")
    assert row.negative_control.passed is False and row.negative_control.can_fail is False
    # No pair differed: the control is not failed, it is empty.
    assert row.negative_control.min_p_adjusted == pytest.approx(1.0)
    assert row.wording == (
        "No conclusion: two seeds of the same pseudo-random generator differed, "
        "or a seed control is missing or could not have failed."
    )
    assert study.decide().study_verdict == "invalid"
    # The seed pairs of the other outcomes are their own controls.
    assert study.row(PIT).negative_control.can_fail is True
    assert study.row(PIT).reason == "equivalence_not_testable"

    # A significant difference does not get past it: the row is tried before row 5.
    study.source(PROBE)["p_value"] = SMALL
    assert study.row(PROBE).verdict == "invalid"

    # With enough prompts the same rows pass, and the difference is an effect.
    for pair in study.seed_pairs(PROBE):
        pair["n_prompts"] = 10
    row = study.row(PROBE)
    assert row.negative_control.passed is True and row.negative_control.can_fail is True
    assert row.verdict == "effect"

    # One pair that cannot fail is enough, and a pair that did differ is named first.
    study.seed_pairs(PROBE)[2]["n_prompts"] = 9
    assert study.row(PROBE).reason == "negative_control_cannot_fail"
    study.seed_pairs(PROBE)[0]["p_value"] = 1e-6
    assert study.row(PROBE).reason == "negative_control"


def test_invalid_when_the_permutations_are_too_few_for_the_control(
    case: Callable[..., Case],
) -> None:
    """A permutation test returns no p below 1 / (permutations + 1), whatever the data."""
    study = case()
    # 9 / (179 + 1) = 0.05 is not below alpha; 9 / (180 + 1) is.
    for pair in study.seed_pairs(MMD):
        pair["n_permutations"] = 179
    row = study.row(MMD)
    assert (row.verdict, row.reason) == ("invalid", "negative_control_cannot_fail")
    for pair in study.seed_pairs(MMD):
        pair["n_permutations"] = 180
    assert study.row(MMD).negative_control.passed is True
    # The KS test has an analytic p-value: nothing in its rows limits it.
    for pair in study.seed_pairs(PIT):
        pair.update(n_prompts=2, n_permutations=1)
    assert study.row(PIT).negative_control.can_fail is True


def test_seeds_that_differ_maximally_on_too_few_prompts_do_not_pass_the_control(
    cards: dict[str, EvalCard], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight prompts, two seeds that disagree on every reply: the run resolves to invalid.

    The sign-flip test on eight prompts cannot return a p below 2/256, and nine
    seed-pair tests need p < 0.05/9. So the control used to pass, and the
    quantum arm, which agreed with one of the seeds, was reported as an effect
    beside seeds that differed by the whole range of the outcome.
    """
    module = __import__(__name__)
    monkeypatch.setattr(module, "PROMPTS", 8)
    monkeypatch.setattr(module, "ALPHA", 0.05)
    plan = _plan(_small_config(), cards)
    assert plan.design is not None
    probe = plan.design.hypotheses[2]
    assert (probe.sample_size.n, probe.sample_size.n_floor, probe.sample_size.control_floor) == (
        8,
        7,
        10,
    )
    run_dir = _write_bundle(tmp_path, plan)
    forced = {"prng_seed_1": 1, "prng_seed_7": 0, "prng_seed_42": 0, "qrng_unsupervised": 1}
    for arm, score in forced.items():
        path = run_dir / "arms" / arm / "cards" / CARD / "judge" / "study_judge.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        _jsonl(path, [{**row, "score": score} for row in rows])
    tables = execute(load_data(run_dir), plan)

    comparisons = tables[f"{CARD}--experience_claim_rate"]["comparisons"]
    pairs = [comparisons[i] for i in probe.negative_controls]
    # Two of the three pairs differ on every reply, and none can say so.
    assert sorted(abs(pair["effect"]) for pair in pairs) == [0.0, 1.0, 1.0]
    assert {pair["n_prompts"] for pair in pairs} == {8}
    assert min(pair["p_value"] for pair in pairs) * 9 >= 0.05

    table = DecisionsTable.model_validate(tables["decisions"])
    row = table.rows[2]
    assert row.estimate == pytest.approx(2 / 3)
    assert row.p_adjusted is not None and row.p_adjusted < 0.05
    assert row.negative_control.passed is False and row.negative_control.can_fail is False
    assert (row.verdict, row.reason) == ("invalid", "negative_control_cannot_fail")
    assert table.study_verdict == "invalid"


# ── tainted samples, one prompt, token records the KS test cannot read ───────


def _arm_refs(run_dir: Path, arm: str) -> list[str]:
    path = run_dir / "arms" / arm / "cards" / CARD / "generations.jsonl"
    return [json.loads(line)["sample_ref"] for line in path.read_text("utf-8").splitlines()]


def _taint(run_dir: Path, refs: list[str]) -> None:
    _jsonl(run_dir / "taints.jsonl", [{"sample_ref": ref, "kind": "qrng_degraded"} for ref in refs])


def test_a_fully_tainted_arm_decides_nothing(cards: dict[str, EvalCard], tmp_path: Path) -> None:
    """Every quantum sample of the eval is tainted: no hypothesis is decided on them.

    The loader dropped a tainted score only while the arm had a clean sample
    left on the card. With none left it kept them all, and the paired
    hypothesis came out "equivalent" at completeness 1.0 on samples whose
    provenance is not quantum.
    """
    plan = _plan(_small_config(), cards)
    assert plan.design is not None
    run_dir = _write_bundle(tmp_path, plan)
    quantum = plan.contrasts[plan.design.hypotheses[2].contrast].a[0]
    refs = _arm_refs(run_dir, quantum)
    assert len(refs) == PROMPTS * EPOCHS * len(TOLD)
    _taint(run_dir, refs)

    clean = load_data(run_dir)
    assert clean.scores[CARD][quantum] == []
    assert not clean.tier0[CARD].get(quantum)
    assert len(clean.scores[CARD]["prng_seed_7"]) == len(refs)

    table = DecisionsTable.model_validate(execute(clean, plan)["decisions"])
    for row in table.rows:
        assert (row.verdict, row.reason) == ("unavailable", "not_produced")
        assert row.p_value is None and row.estimate is None and row.completeness is None
    assert table.study_verdict == "mixed_or_inconclusive"

    # The "nothing left out" load still has them, and its clean view is the clean load.
    everything = load_data(run_dir, include_tainted=True)
    assert len(everything.scores[CARD][quantum]) == len(refs)
    view = exclude_tainted(everything)
    assert view.scores == clean.scores
    assert {arm: len(records) for arm, records in view.tier0[CARD].items() if records} == {
        arm: len(records) for arm, records in clean.tier0[CARD].items()
    }


def test_pairs_left_on_one_prompt_are_unavailable_not_a_crash(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    """Taints leave the quantum arm clean replies on a single prompt."""
    plan = _plan(_small_config(), cards)
    assert plan.design is not None
    run_dir = _write_bundle(tmp_path, plan)
    probe = plan.design.hypotheses[2]
    quantum = plan.contrasts[probe.contrast].a[0]
    refs = _arm_refs(run_dir, quantum)
    kept = [ref for ref in refs if ref.split("/")[2] == f"p{PROMPTS - 1}"]
    assert len(kept) == EPOCHS * len(TOLD)
    _taint(run_dir, [ref for ref in refs if ref not in kept])

    # The prompt is the unit the paired test resamples: one prompt is no sample.
    tables = execute(load_data(run_dir), plan)
    source = tables[f"{CARD}--experience_claim_rate"]["comparisons"][probe.contrast]
    assert source == {
        "id": f"source:{probe.contrast}",
        "kind": "source",
        "model": probe.model,
        "sampling": probe.sampling,
        "arms_a": [quantum],
        "arms_b": list(plan.contrasts[probe.contrast].b),
        "epsilon": None,
        "available": False,
        "reason": ONE_PROMPT_REASON,
    }
    table = DecisionsTable.model_validate(tables["decisions"])
    assert (table.rows[2].verdict, table.rows[2].reason) == ("unavailable", "not_produced")
    # The seed pairs do not involve the quantum arm and were computed as usual.
    pairs = [
        tables[f"{CARD}--experience_claim_rate"]["comparisons"][i] for i in probe.negative_controls
    ]
    assert all(pair["available"] and pair["n_prompts"] == PROMPTS for pair in pairs)


def test_ks_completeness_counts_the_generations_the_test_can_read(
    case: Callable[..., Case],
) -> None:
    study = case()
    planned = study.hypothesis(PIT).planned_pairs
    # Every generation left token records, and on one arm most of them have no
    # CDF bracket (the engine's token log was not read in time).
    study.bracketed = copy.deepcopy(study.generations)
    lost = study.bracketed["thurstonian_self_outcomes"]["qrng_unsupervised"] - 1_000
    study.bracketed["thurstonian_self_outcomes"]["qrng_unsupervised"] = 1_000
    pit, mmd = study.row(PIT), study.row(MMD)
    assert (pit.verdict, pit.reason) == ("invalid", "incomplete")
    assert pit.completeness == pytest.approx((planned - lost) / planned)
    # The MMD test reads token ids, which those records do have.
    assert mmd.completeness == 1.0 and mmd.reason != "incomplete"
    # Without the second count every generation present is taken to be readable.
    study.bracketed = None
    assert study.row(PIT).completeness == 1.0


def test_generations_without_brackets_are_missing_data_for_the_ks_test(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    """Four fifths of the quantum arm's replies kept token records without CDF brackets."""
    plan = _plan(_small_config(), cards)
    assert plan.design is not None
    run_dir = _write_bundle(tmp_path, plan)
    quantum = plan.contrasts[plan.design.hypotheses[0].contrast].a[0]
    path = run_dir / "arms" / quantum / "cards" / CARD / "tier0.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    refs = sorted({row["sample_ref"] for row in rows})
    stripped = set(refs[: len(refs) * 4 // 5])
    _jsonl(
        path,
        [
            {k: v for k, v in row.items() if k not in ("u", "cdf_lo", "cdf_hi")}
            if row["sample_ref"] in stripped
            else row
            for row in rows
        ],
    )

    data = load_data(run_dir)
    assert _generations_present(data)[CARD][quantum] == len(refs)
    assert _generations_bracketed(data)[CARD][quantum] == len(refs) - len(stripped)
    assert _generations_bracketed(data)[CARD]["prng_seed_7"] == len(refs)

    tables = execute(data, plan)
    table = DecisionsTable.model_validate(tables["decisions"])
    pit, mmd, _probe = table.rows
    # The KS test ran on a fifth of the arm. That is too little of the planned data.
    assert tables["tier0_pit_residuals--pit_ks_two_sample"]["comparisons"][0]["available"]
    assert (pit.verdict, pit.reason) == ("invalid", "incomplete")
    assert pit.completeness == pytest.approx(0.2)
    assert mmd.completeness == 1.0
    assert table.study_verdict == "invalid"


# ── a study with two (model, sampling) cells ─────────────────────────────────

SECOND_CELL = "t1_minp"
SECOND_CELL_SUFFIX = "__qr_llm__t1_minp"


def _fixture_config(name: str) -> Config:
    """The study of one case of the shared design fixture."""
    cases = json.loads((FIXTURES / "design.json").read_text(encoding="utf-8"))["cases"]
    config: Config = copy.deepcopy(next(c["config"] for c in cases if c["name"] == name))
    return config


@pytest.fixture()
def two_cells(cards: dict[str, EvalCard], tmp_path: Path) -> Case:
    """Creative and temperature-1 sampling, each compared on its own arms.

    The plan has contrasts 0 to 4 for the creative cell and the rest for the
    second one, so a row index is only right for the cell it belongs to.
    """
    study = Case(
        _plan(_fixture_config("two_cells"), cards), _write_run(tmp_path / "runs" / "run_full")
    )
    cells = [(h.id, h.sampling) for h in study.hypotheses]
    assert cells == [
        ("H1", "creative"),
        ("H2", SECOND_CELL),
        ("H3", "creative"),
        ("H4", SECOND_CELL),
        ("H5", "creative"),
        ("H6", SECOND_CELL),
    ]
    first, second = study.hypothesis(PROBE), study.hypothesis(PROBE, SECOND_CELL)
    assert first.contrast != second.contrast
    assert not set(first.negative_controls) & set(second.negative_controls)
    assert (len(first.negative_controls), len(second.negative_controls)) == (3, 1)
    return study


def test_each_cell_is_decided_on_its_own_comparison(two_cells: Case) -> None:
    study = two_cells
    study.source(PROBE, SECOND_CELL)["p_value"] = 1e-5
    table = study.decide()
    by_id = {row.hypothesis_id: row for row in table.rows}
    assert by_id["H6"].verdict == "effect"
    # Six difference tests in one family.
    assert by_id["H6"].p_adjusted == pytest.approx(6e-5)
    assert by_id["H6"].sampling == SECOND_CELL
    # The creative cell's hypothesis on the same outcome saw nothing.
    assert by_id["H5"].verdict == "inconclusive" and by_id["H5"].p_value == LARGE
    assert [by_id[h].verdict for h in ("H1", "H2", "H3", "H4")] == ["inconclusive"] * 4
    assert table.study_verdict == "effect"


def test_each_cell_has_its_own_seed_control(two_cells: Case) -> None:
    study = two_cells
    study.seed_pairs(PIT)[1]["p_value"] = 1e-7
    creative, second = study.row(PIT), study.row(PIT, SECOND_CELL)
    assert (creative.verdict, creative.reason) == ("invalid", "negative_control")
    assert creative.negative_control.n_pairs == 3
    # One family over the twelve seed pairs of the study.
    assert creative.negative_control.min_p_adjusted == pytest.approx(12e-7)
    # The seeds of the second cell agree, and that cell's hypothesis stands.
    assert second.negative_control.passed is True and second.negative_control.n_pairs == 1
    assert second.verdict == "inconclusive"

    # And the other way round.
    study.seed_pairs(PIT)[1]["p_value"] = LARGE
    study.seed_pairs(PIT, SECOND_CELL)[0]["p_value"] = 1e-7
    creative, second = study.row(PIT), study.row(PIT, SECOND_CELL)
    assert creative.negative_control.passed is True and creative.verdict == "inconclusive"
    assert (second.verdict, second.reason) == ("invalid", "negative_control")
    # The failed pair is a pair of that outcome: the cell's other outcomes keep their control.
    assert study.row(MMD, SECOND_CELL).negative_control.passed is True


def test_each_cell_has_its_own_positive_control(cards: dict[str, EvalCard], tmp_path: Path) -> None:
    """The biased arm ran in one cell only: only that cell can show equivalence."""
    config = _fixture_config("two_cells")
    config["design"].update(claim="difference_or_equivalence", sensitivity_epsilon=0.03)
    declared = next(a for a in config["arms"] if a["id"] == EPSILON_ARM)
    assert declared["availability"] == "unavailable"
    config["arms"].append(
        {
            **declared,
            "id": EPSILON_ARM + SECOND_CELL_SUFFIX,
            "sampling": SECOND_CELL,
            "availability": "available",
            "unavailable_reason": None,
        }
    )
    study = Case(_plan(config, cards), _write_run(tmp_path / "runs" / "run_full"))
    assert study.hypothesis(PROBE).positive_control is None
    index = study.hypothesis(PROBE, SECOND_CELL).positive_control
    assert index is not None
    control = study.plan.contrasts[index]
    assert EPSILON_ARM + SECOND_CELL_SUFFIX in (*control.a, *control.b)

    for sampling in ("creative", SECOND_CELL):
        study.source(PROBE, sampling)["equivalence"]["p_value"] = SMALL
    study.positive(PROBE, SECOND_CELL)["p_value"] = SMALL
    creative, second = study.row(PROBE, "creative"), study.row(PROBE, SECOND_CELL)
    assert (second.verdict, second.positive_control.status) == ("equivalent", "passed")
    assert (creative.verdict, creative.reason) == ("inconclusive", "positive_control_absent")
    assert creative.positive_control.status == "absent"
    # The probe has a margin in both cells: an equivalence family of two.
    assert study.decide().families.equivalence == 2
    assert second.p_equivalence_adjusted == pytest.approx(2 * SMALL)

    # A control that ran and was not detected fails its own cell.
    study.positive(PROBE, SECOND_CELL)["p_value"] = LARGE
    assert study.row(PROBE, SECOND_CELL).reason == "positive_control_failed"


def test_each_cell_counts_the_generations_of_its_own_arms(two_cells: Case) -> None:
    study = two_cells
    lost = study.generations["thurstonian_self_outcomes"].pop("prng_seed_7" + SECOND_CELL_SUFFIX)
    planned = study.hypothesis(PIT, SECOND_CELL).planned_pairs
    for ref in (PIT, MMD):
        second = study.row(ref, SECOND_CELL)
        assert (second.verdict, second.reason) == ("invalid", "incomplete")
        assert second.completeness == pytest.approx((planned - lost) / planned)
        # The creative cell compares other arms, and all of theirs are present.
        assert study.row(ref, "creative").completeness == 1.0
        assert study.row(ref, "creative").verdict == "inconclusive"


# ── the equivalence family with more than one member ─────────────────────────


@pytest.fixture()
def two_margins(cards: dict[str, EvalCard], tmp_path: Path) -> Case:
    """Two outcomes with a margin: the probe's from its eval, the MEQ's declared."""
    config = _config(confirmatory=(PROBE, MEQ))
    config["design"]["margins"] = [{"card_id": MEQ[0], "endpoint": MEQ[1], "margin": 0.1}]
    study = Case(_plan(config, cards), _write_run(tmp_path / "runs" / "run_full"))
    assert [h.margin for h in study.hypotheses] == [0.05, 0.1]
    for ref in (PROBE, MEQ):
        study.positive(ref)["p_value"] = SMALL
    return study


def test_equivalence_p_values_are_corrected_over_the_family(two_margins: Case) -> None:
    study = two_margins
    assert study.decide().families.model_dump() == {
        "difference": 2,
        "equivalence": 2,
        "negative_control": 6,
    }
    # Each raw p is below 0.05, and neither passes once corrected over two tests.
    study.source(PROBE)["equivalence"]["p_value"] = 0.03
    study.source(MEQ)["equivalence"]["p_value"] = 0.04
    table = study.decide()
    for row in table.rows:
        assert row.p_equivalence_adjusted == pytest.approx(0.06)
        assert (row.verdict, row.reason) == ("inconclusive", "interval_exceeds_margin")
    assert [row.p_equivalence for row in table.rows] == [0.03, 0.04]
    # The reason names the rule that was applied. The raw test did pass here,
    # so its interval does lie inside the margin.
    assert table.rows[0].wording == (
        "Neither a difference nor its absence is established on Experience-claim rate "
        "(the equivalence test did not pass after correction)."
    )
    assert table.study_verdict == "mixed_or_inconclusive"

    # Holm: the smaller p is doubled, the larger one may not fall below it.
    study.source(PROBE)["equivalence"]["p_value"] = 0.02
    table = study.decide()
    assert [row.p_equivalence_adjusted for row in table.rows] == [pytest.approx(0.04)] * 2
    assert [row.verdict for row in table.rows] == ["equivalent", "equivalent"]
    # Every hypothesis of this study has a margin, so absence is the study's verdict.
    assert table.study_verdict == "no_effect_within_margins"


def test_an_equivalence_test_that_was_not_produced_keeps_its_slot(two_margins: Case) -> None:
    study = two_margins
    study.source(PROBE)["equivalence"]["p_value"] = 0.03
    study.source(MEQ)["equivalence"] = {"available": False, "reason": ONE_PROMPT_REASON}
    probe, meq = study.row(PROBE), study.row(MEQ)
    # In a family of one, 0.03 would have passed.
    assert probe.p_equivalence_adjusted == pytest.approx(0.06)
    assert (probe.verdict, probe.reason) == ("inconclusive", "interval_exceeds_margin")
    assert meq.p_equivalence is None and meq.p_equivalence_adjusted is None
    assert (meq.verdict, meq.reason) == ("inconclusive", "interval_exceeds_margin")


# ── a hypothesis that planned no data ────────────────────────────────────────


def test_a_hypothesis_that_planned_no_data_has_no_completeness(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    """Token-stream outcomes in a study with no eval on a fixed prompt list.

    The plan counts the planned generations on the evals whose prompts are
    fixed before the run. Here there is none, so nothing was planned and there
    is no share to compute: the completeness gate does not apply, and the row
    says so by carrying no completeness.
    """
    config = _config(claim="difference", epsilon=None, control_available=False)
    config["design"]["confirmatory"] = [
        {"card_id": card_id, "endpoint": endpoint} for card_id, endpoint in (PIT, MMD)
    ]
    config["cards"] = [
        selection
        for selection in config["cards"]
        if selection["card_id"] in {PIT[0], MMD[0], "source_guessing"}
    ]
    study = Case(_plan(config, cards), _write_run(tmp_path / "runs" / "run_full"))
    assert [h.planned_pairs for h in study.hypotheses] == [0, 0]
    assert decisions.stream_cards(study.plan) == frozenset()

    # Whatever token records the run left, on whichever eval, the gate has nothing to ask.
    for present in ({}, {"source_guessing": {"qrng_unsupervised": 5}}):
        study.generations = present
        for row in study.decide().rows:
            assert row.completeness is None
            assert (row.verdict, row.reason) == ("inconclusive", "equivalence_not_claimed")
    study.source(PIT)["p_value"] = SMALL
    assert study.row(PIT).verdict == "effect"
    # The other gates still apply: a comparison that was not produced is unavailable.
    study.missing(study.source(MMD))
    assert study.row(MMD).verdict == "unavailable"


# ── a full run that was never started ────────────────────────────────────────


def test_an_unstarted_full_run_does_not_use_up_the_confirmatory_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    unstarted = _write_run(
        runs / "run_a_unstarted", created_at="2026-10-01T09:00:00+00:00", state="created"
    )
    real = _write_run(runs / "run_b_full", created_at="2026-10-02T09:00:00+00:00", state="complete")

    # Launched first and never started: it generated nothing, so it uses up nothing.
    assert decisions.non_confirmatory_reason(real) is None
    decisions.record_run_order(real)
    record = json.loads((real / decisions.RUN_ORDER_FILE).read_text(encoding="utf-8"))
    assert record["earlier_full_runs"] == []

    # Started after all, it is not a second first run: the other one was
    # analysed as the study's full run while this one had produced nothing.
    _write_run(unstarted, created_at="2026-10-01T09:00:00+00:00", state="complete")
    assert decisions.non_confirmatory_reason(unstarted) == "not_first_full_run"
    decisions.record_run_order(unstarted)
    late = json.loads((unstarted / decisions.RUN_ORDER_FILE).read_text(encoding="utf-8"))
    assert late["earlier_full_runs"] == ["run_b_full"]
    assert decisions.non_confirmatory_reason(real) is None

    # Any other end of an earlier full run still counts, as the stopping rule says.
    for state in ("aborted", "complete", "running", "needs_keys"):
        study = tmp_path / state / "runs"
        _write_run(study / "run_a", created_at="2026-10-01T09:00:00+00:00", state=state)
        later = _write_run(study / "run_b", created_at="2026-10-02T09:00:00+00:00")
        assert decisions.non_confirmatory_reason(later) == "not_first_full_run"

    # An unstarted run that was created later never counted, and still does not.
    study = tmp_path / "later" / "runs"
    first = _write_run(study / "run_a", created_at="2026-10-01T09:00:00+00:00")
    _write_run(study / "run_b", created_at="2026-10-02T09:00:00+00:00", state="created")
    assert decisions.non_confirmatory_reason(first) is None
