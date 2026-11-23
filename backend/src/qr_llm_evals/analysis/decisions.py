"""Resolve every registered hypothesis of a designed study to one verdict.

A study with a ``design`` block registers its hypotheses in the plan
(``plan.design.hypotheses``). Once the comparison tables exist, this module
reads each hypothesis's quantum-vs-control comparison, its seed-vs-seed
negative controls and its biased-sampler positive control, corrects the
p-values within their Holm families, and walks the decision matrix
(``cards.design.VERDICT_RULES``, the same rows the prereg prints) until the
first row matches. The result is the ``decisions`` table: one row per
hypothesis and one roll-up for the study.

Nothing here computes a test. The numbers come from the comparison rows the
executor already wrote, so a verdict can always be traced to a row of an
endpoint table.

Two things a verdict depends on are not in those rows, and both are settled
here. Which evals the planned data of a token-stream test are counted on
(:func:`stream_cards`) is read from the plan, so what was planned and what is
present are one set. Whether a run is the study's first full run
(:func:`record_run_order`) is written beside the run at its first analysis,
so a copy of the run alone still knows.

Imports ``cards`` and ``analysis.stats`` only.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qr_llm_evals.analysis.stats import holm_bonferroni
from qr_llm_evals.cards.design import (
    INCONCLUSIVE_REASON_TEXT,
    VERDICT_RULES,
    VerdictRule,
    control_can_fail,
    in_planned_stream,
)
from qr_llm_evals.cards.design_schema import (
    DecisionRow,
    DecisionsTable,
    HypothesisPlan,
    NegativeControl,
    NonConfirmatoryReason,
    PositiveControl,
    PositiveControlStatus,
    Reason,
    StudyVerdict,
    Verdict,
)
from qr_llm_evals.cards.statistical_plan import Contrast, StatisticalPlan

Table = dict[str, Any]

#: Name of the table, and so of ``tables/decisions.json``.
TABLE_NAME = "decisions"
PRECISION = 6
#: The only test with a paired difference: the one a margin and a pair count apply to.
PAIRED_TEST = "paired_permutation"
#: The token-stream test that reads CDF brackets, which not every token record has.
BRACKET_TEST = "ks_two_sample"
EQUIVALENCE_CLAIM = "difference_or_equivalence"
#: Put before every wording of a run that cannot confirm anything.
NOT_CONFIRMATORY_PREFIX = "Not confirmatory: "
#: Beside ``run.json``: which full runs preceded this one, as its first analysis found them.
RUN_ORDER_FILE = "run-order.json"
#: The state of a run that was launched and never started. It generated nothing.
NEVER_STARTED = "created"


@dataclass(frozen=True)
class Gates:
    """What the decision matrix asks about one hypothesis."""

    #: The quantum-vs-control comparison was computed.
    available: bool
    #: At least the registered share of the planned pairs was analysed.
    complete: bool
    #: Every planned seed pair was computed and none differs after correction.
    negative_passed: bool
    #: Holm-adjusted difference p below alpha.
    differs: bool
    #: A margin was registered for the endpoint.
    margin: bool
    #: Holm-adjusted equivalence p below alpha.
    within_margin: bool
    #: The study registered a claim of absence within a margin.
    equivalence_claimed: bool
    #: The test has a paired difference, so a margin can apply to it at all.
    paired: bool
    positive: PositiveControlStatus
    #: Every computed seed pair could have failed: its test had the prompts and
    #: the permutations to return a p-value below the corrected level.
    negative_can_fail: bool = True


#: When each row of the matrix matches, by row number. The order in which the
#: rows are tried is that of ``VERDICT_RULES``, not of this mapping.
_MATCHES: dict[int, Callable[[Gates], bool]] = {
    1: lambda g: not g.available,
    2: lambda g: not g.complete,
    3: lambda g: not g.negative_passed or not g.negative_can_fail,
    4: lambda g: g.differs and g.margin and g.within_margin,
    5: lambda g: g.differs,
    6: lambda g: g.equivalence_claimed and g.margin and g.within_margin and g.positive == "passed",
    7: lambda g: True,
}

#: When each reason applies. A row records the first of its reasons that does.
_APPLIES: dict[Reason, Callable[[Gates], bool]] = {
    "not_produced": lambda g: not g.available,
    "incomplete": lambda g: not g.complete,
    "negative_control": lambda g: not g.negative_passed,
    "negative_control_cannot_fail": lambda g: not g.negative_can_fail,
    "equivalence_not_claimed": lambda g: not g.equivalence_claimed,
    "equivalence_not_testable": lambda g: not g.paired,
    "no_margin": lambda g: not g.margin,
    "interval_exceeds_margin": lambda g: not g.within_margin,
    "positive_control_absent": lambda g: g.positive == "absent",
    "positive_control_failed": lambda g: g.positive == "failed",
}


def resolve(gates: Gates) -> tuple[VerdictRule, Reason | None]:
    """The first row of the decision matrix that matches, and the reason it records."""
    for rule in VERDICT_RULES:
        if not _MATCHES[rule.number](gates):
            continue
        if not rule.reasons:
            return rule, None
        for reason in rule.reasons:
            if _APPLIES[reason](gates):
                return rule, reason
        raise RuntimeError(f"decision matrix row {rule.number} matched without a reason")
    raise RuntimeError("the decision matrix has no closing row")


def study_verdict(verdicts: Sequence[Verdict], *, confirmatory: bool) -> StudyVerdict:
    """The study in one word, from the verdicts of its hypotheses.

    One invalid hypothesis invalidates the study: a failed control says the
    run cannot be trusted, whatever the other rows show. ``effect`` needs one
    hypothesis with an effect outside any margin; absence is claimed only when
    every hypothesis is equivalent.
    """
    if not confirmatory:
        return "not_confirmatory"
    if "invalid" in verdicts:
        return "invalid"
    if "effect" in verdicts:
        return "effect"
    if verdicts and all(verdict == "equivalent" for verdict in verdicts):
        return "no_effect_within_margins"
    return "mixed_or_inconclusive"


# ── which runs may confirm ───────────────────────────────────────────────────


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _created_at(run: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(run.get("created_at")))
    except ValueError:
        return None


def _is_earlier(other: datetime, other_id: str, ours: datetime, our_id: str) -> bool:
    try:
        return (other, other_id) < (ours, our_id)
    except TypeError:
        # One timestamp carries an offset and the other does not: no order.
        return False


def _earlier_full_runs(run_dir: Path) -> list[str]:
    """Ids of the full runs stored beside this one that come before it.

    "Before" is by ``created_at``, ties by run id. Anything that is not a pilot
    run was a full run, whatever became of it, with one exception: a run that
    was launched and never started (``state`` still ``created``) generated
    nothing, so it uses up nothing. A full run that was left unstarted does
    not cost the study its confirmatory run.

    That exception must not make two runs the first. A run passed over while
    it was unstarted may be started later, and it was created earlier. So a
    full run that has already recorded its own order without this run in it
    also comes before this run: it was analysed as a full run of the study
    while this one had produced nothing.

    Empty when this run has no readable ``created_at``: there is then no order
    to speak of.
    """
    ours = _created_at(_read_object(run_dir / "run.json"))
    if ours is None:
        return []
    earlier = []
    for path in sorted(run_dir.parent.glob("*/run.json")):
        if path.parent.resolve() == run_dir.resolve():
            continue
        other = _read_object(path)
        created = _created_at(other)
        if other.get("kind") == "pilot" or created is None:
            continue
        if _is_earlier(created, path.parent.name, ours, run_dir.name):
            if other.get("state") != NEVER_STARTED:
                earlier.append(path.parent.name)
            continue
        recorded = _recorded_earlier_full_runs(path.parent)
        if recorded is not None and run_dir.name not in recorded:
            earlier.append(path.parent.name)
    return earlier


def _recorded_earlier_full_runs(run_dir: Path) -> list[str] | None:
    """What :func:`record_run_order` wrote for this run; ``None`` if nothing usable."""
    record = _read_object(run_dir / RUN_ORDER_FILE)
    earlier = record.get("earlier_full_runs")
    if record.get("schema_version") != 1 or not isinstance(earlier, list):
        return None
    return [str(run_id) for run_id in earlier]


def record_run_order(run_dir: Path) -> None:
    """Write down, once, which full runs of the study came before this one.

    Whether a run is the study's first full run depends on the other runs of
    the study, and a copy of one run does not hold them. So the first analysis
    of a full run records what it finds beside the run, in ``run-order.json``
    next to ``run.json``. The file is an input of every later analysis and is
    shipped in the bundle: a re-analysis from a downloaded bundle, or from a
    single run directory, then gives the confirmatory status the first
    analysis gave. The file is never rewritten. A pilot run needs none: its
    own ``run.json`` already says that it confirms nothing.
    """
    run = _read_object(run_dir / "run.json")
    path = run_dir / RUN_ORDER_FILE
    if run.get("kind") == "pilot" or _created_at(run) is None or path.exists():
        return
    record = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": run.get("created_at"),
        "earlier_full_runs": _earlier_full_runs(run_dir),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        # Another analysis of the same run got there first; its record stands.
        return


def non_confirmatory_reason(run_dir: Path) -> NonConfirmatoryReason | None:
    """Why this run cannot confirm a hypothesis; ``None`` if it can.

    The stopping rule registers that only the first full run of a study is
    confirmatory and that pilot runs never are. Which full runs came first is
    read from the run's own record (:func:`record_run_order`) where there is
    one, so the answer does not change with what else is in the directory. A
    run without a record is compared with the runs stored beside it.
    """
    if _read_object(run_dir / "run.json").get("kind") == "pilot":
        return "pilot_run"
    earlier = _recorded_earlier_full_runs(run_dir)
    if earlier is None:
        earlier = _earlier_full_runs(run_dir)
    return "not_first_full_run" if earlier else None


# ── reading the comparison rows ──────────────────────────────────────────────


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _comparison(
    tables: Mapping[str, Table], hypothesis: HypothesisPlan, index: int | None
) -> Mapping[str, Any]:
    """The comparison row of one of the hypothesis's contrasts; empty when there is none.

    The executor writes one row per contrast of the plan, in plan order, so a
    contrast index is a row index.
    """
    if index is None:
        return {}
    table = tables.get(f"{hypothesis.card_id}--{hypothesis.endpoint}") or {}
    rows = table.get("comparisons") or []
    row = rows[index] if 0 <= index < len(rows) else None
    return row if isinstance(row, Mapping) else {}


def _p_value(row: Mapping[str, Any]) -> float | None:
    """The raw p-value of a comparison; ``None`` when it could not be computed."""
    return _number(row.get("p_value")) if row.get("available") else None


def _equivalence_p(row: Mapping[str, Any]) -> float | None:
    """The TOST p-value of a comparison; ``None`` when there is no equivalence test."""
    result = row.get("equivalence")
    if not row.get("available") or not isinstance(result, Mapping):
        return None
    return None if result.get("available") is False else _number(result.get("p_value"))


def _count(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _could_fail(
    hypothesis: HypothesisPlan, row: Mapping[str, Any], plan: StatisticalPlan, family: int
) -> bool:
    """Whether one seed-pair comparison could have failed the negative control.

    A control that passes only because its test cannot return a small enough
    p-value says nothing about the seeds. The limits are read from the row the
    executor wrote (the prompts it paired and the permutations it ran), so the
    answer is about the data that were analysed, not about the plan.
    """
    permutations = _count(row.get("n_permutations"))
    return control_can_fail(
        hypothesis.test,
        prompts=_count(row.get("n_prompts")),
        permutations=plan.permutations if permutations is None else permutations,
        alpha=plan.alpha,
        family=family,
    )


def _holm(slots: Sequence[float | None]) -> list[float]:
    """Holm-adjusted p-values of one family.

    A test that could not be run keeps its slot at p = 1, for the correction
    only: dropping it would shrink the family, and a study could then lower
    its bar by failing to produce data.
    """
    if not slots:
        return []
    return [float(p) for p in holm_bonferroni([1.0 if p is None else p for p in slots])]


def stream_cards(plan: StatisticalPlan) -> frozenset[str]:
    """The evals whose generations count as the planned data of a token-stream test.

    Read from the card snapshots frozen in the plan with the rule that set
    ``planned_pairs`` at registration (``cards.design.in_planned_stream``): the
    evals that run on a prompt list fixed before the run. What was planned and
    what is present are therefore counted on the same evals, whatever the
    registry says by the time the run is analysed.
    """
    try:
        snapshots = json.loads(plan.cards_json)
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(snapshots, list):
        return frozenset()
    return frozenset(
        str(card.get("id"))
        for card in snapshots
        if isinstance(card, Mapping) and in_planned_stream(card)
    )


def _completeness(
    hypothesis: HypothesisPlan,
    row: Mapping[str, Any],
    contrast: Contrast,
    generations_present: Mapping[str, Mapping[str, int]],
    generations_bracketed: Mapping[str, Mapping[str, int]],
    counted: frozenset[str],
) -> float | None:
    """The share of the planned data that the comparison analysed.

    A paired test counts its complete pairs. A token-stream test is planned on
    the generations of the ``counted`` evals, so its share is that of the arm
    with the fewest of those generations present. What "present" means follows
    the test: the MMD test reads token ids, which every token record has; the
    KS test reads CDF brackets, so only a generation with a bracketed record
    is present for it. ``None`` when nothing was planned to compare against.
    """
    if hypothesis.planned_pairs <= 0:
        return None
    if hypothesis.test == PAIRED_TEST:
        return (_number(row.get("n_pairs")) or 0.0) / hypothesis.planned_pairs
    usable = generations_bracketed if hypothesis.test == BRACKET_TEST else generations_present

    def present(arm: str) -> int:
        return sum(usable.get(card, {}).get(arm, 0) for card in counted)

    return min(present(arm) for arm in (*contrast.a, *contrast.b)) / hypothesis.planned_pairs


def _wording(
    rule: VerdictRule, hypothesis: HypothesisPlan, reason: Reason | None, epsilon: float | None
) -> str:
    return rule.wording.format(
        label=hypothesis.label,
        margin="" if hypothesis.margin is None else f"{hypothesis.margin:g}",
        epsilon="" if epsilon is None else f"{epsilon:.2f}",
        reason_text="" if reason is None else INCONCLUSIVE_REASON_TEXT.get(reason, ""),
    )


# ── the decisions table ──────────────────────────────────────────────────────


def apply(
    tables: dict[str, Table],
    plan: StatisticalPlan,
    *,
    run_dir: Path,
    generations_present: Mapping[str, Mapping[str, int]],
    generations_bracketed: Mapping[str, Mapping[str, int]] | None = None,
) -> None:
    """Add the ``decisions`` table of a designed study to ``tables``.

    ``generations_present`` counts, per eval and arm, the generations of the
    run that left token records; ``generations_bracketed`` counts those among
    them with a record the KS test can read (a CDF bracket). Without the
    second, every generation present is taken to be readable. Which evals
    enter the completeness of a token-stream test is decided here
    (:func:`stream_cards`), not by the caller. The comparison tables are read,
    not changed.
    """
    design = plan.design
    if design is None:
        raise ValueError("only a plan with a design block resolves to decisions")
    hypotheses = design.hypotheses
    alpha = plan.alpha
    counted = stream_cards(plan)

    sources = [_comparison(tables, h, h.contrast) for h in hypotheses]
    raw_difference = [_p_value(row) for row in sources]
    p_difference = _holm(raw_difference)

    with_margin = [i for i, h in enumerate(hypotheses) if h.margin is not None]
    raw_equivalence = {i: _equivalence_p(sources[i]) for i in with_margin}
    p_equivalence = dict(
        zip(with_margin, _holm([raw_equivalence[i] for i in with_margin]), strict=True)
    )

    # One family over every seed pair of every hypothesis.
    controls = [[_comparison(tables, h, i) for i in h.negative_controls] for h in hypotheses]
    raw_controls = [[_p_value(row) for row in rows] for rows in controls]
    adjusted = iter(_holm([p for ps in raw_controls for p in ps]))
    p_controls = [[next(adjusted) for _ in ps] for ps in raw_controls]
    family_size = sum(len(ps) for ps in raw_controls)

    why_not = non_confirmatory_reason(run_dir)
    prefix = "" if why_not is None else NOT_CONFIRMATORY_PREFIX

    rows: list[DecisionRow] = []
    for i, hypothesis in enumerate(hypotheses):
        row = sources[i]
        raw = raw_difference[i]
        available = raw is not None
        completeness = (
            _completeness(
                hypothesis,
                row,
                plan.contrasts[hypothesis.contrast],
                generations_present,
                generations_present if generations_bracketed is None else generations_bracketed,
                counted,
            )
            if available
            else None
        )

        produced = [
            adjusted_p
            for adjusted_p, raw_p in zip(p_controls[i], raw_controls[i], strict=True)
            if raw_p is not None
        ]
        # A cell without a seed pair has no control, which is a missing control.
        clean = bool(produced) and len(produced) == len(raw_controls[i]) and min(produced) >= alpha
        # A pair that could not have failed is no control either. Only computed
        # pairs are asked: a missing one already fails the gate as missing.
        can_fail = all(
            _could_fail(hypothesis, control, plan, family_size)
            for control, raw_p in zip(controls[i], raw_controls[i], strict=True)
            if raw_p is not None
        )
        negative = NegativeControl(
            passed=clean and can_fail,
            n_pairs=len(raw_controls[i]),
            min_p_adjusted=min(produced) if produced else None,
            can_fail=can_fail,
        )

        positive_p = _p_value(_comparison(tables, hypothesis, hypothesis.positive_control))
        status: PositiveControlStatus = (
            "absent" if positive_p is None else "passed" if positive_p < alpha else "failed"
        )

        estimate = _number(row.get("effect")) if available else None
        seed_noise = [
            abs(effect)
            for control in controls[i]
            if control.get("available") and (effect := _number(control.get("effect"))) is not None
        ]

        equivalence = p_equivalence.get(i) if raw_equivalence.get(i) is not None else None
        rule, reason = resolve(
            Gates(
                available=available,
                complete=completeness is None or completeness >= design.min_completeness,
                negative_passed=clean,
                negative_can_fail=can_fail,
                differs=available and p_difference[i] < alpha,
                margin=hypothesis.margin is not None,
                within_margin=equivalence is not None and equivalence < alpha,
                equivalence_claimed=design.claim == EQUIVALENCE_CLAIM,
                paired=hypothesis.test == PAIRED_TEST,
                positive=status,
            )
        )
        rows.append(
            DecisionRow(
                hypothesis_id=hypothesis.id,
                card_id=hypothesis.card_id,
                endpoint=hypothesis.endpoint,
                label=hypothesis.label,
                model=hypothesis.model,
                sampling=hypothesis.sampling,
                test=hypothesis.test,
                estimate=estimate,
                ci_lower=_number(row.get("ci_lower")) if available else None,
                ci_upper=_number(row.get("ci_upper")) if available else None,
                ci_level=plan.ci_level,
                p_value=raw,
                # The p = 1 of an empty slot corrects the others; it is not a result.
                p_adjusted=p_difference[i] if available else None,
                margin=hypothesis.margin,
                p_equivalence=raw_equivalence.get(i),
                p_equivalence_adjusted=equivalence,
                completeness=completeness,
                negative_control=negative,
                positive_control=PositiveControl(
                    epsilon=design.sensitivity_epsilon, status=status, p_value=positive_p
                ),
                # Reported beside the verdict, never a gate: the seed pairs are
                # single arms and the treatment is a pooled mean, so the two
                # magnitudes are not on one scale.
                above_seed_noise=(
                    None if estimate is None or not seed_noise else abs(estimate) > max(seed_noise)
                ),
                planned_mde_raw=hypothesis.sample_size.mde_raw,
                planned_mde_standardized=hypothesis.sample_size.mde_standardized,
                verdict=rule.verdict,
                reason=reason,
                wording=prefix + _wording(rule, hypothesis, reason, design.sensitivity_epsilon),
            )
        )

    table = DecisionsTable(
        precision=PRECISION,
        confirmatory=why_not is None,
        non_confirmatory_reason=why_not,
        claim=design.claim,
        alpha=alpha,
        power=design.power,
        min_completeness=design.min_completeness,
        sensitivity_epsilon=design.sensitivity_epsilon,
        families=design.families,
        study_verdict=study_verdict([r.verdict for r in rows], confirmatory=why_not is None),
        rows=tuple(rows),
    )
    tables[TABLE_NAME] = table.model_dump(mode="json")


__all__ = [
    "NOT_CONFIRMATORY_PREFIX",
    "RUN_ORDER_FILE",
    "TABLE_NAME",
    "Gates",
    "apply",
    "non_confirmatory_reason",
    "record_run_order",
    "resolve",
    "stream_cards",
    "study_verdict",
]
