"""Every interval of a designed analysis is at the level the study registered.

A study that registers alpha 0.01 tests at 0.01 and reports 99% intervals. The
comparison rows already did; the blocks beside them (told/actual, the headline
effect, source detection, the cumulative-deviation envelope) were still at a
fixed 95%. These tests run at alpha 0.01:

- each primitive computes the level it is given and names it;
- without a level it returns what it always returned, so the tables of the
  studies already analysed keep their bytes;
- the executor passes the plan's level to every one of them, and only for a
  plan with a design block.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy import stats as sp

from qr_llm_evals.analysis import endpoints
from qr_llm_evals.analysis.figures import write_figures
from qr_llm_evals.analysis.source_detection import (
    chance_test,
    outside_observer,
    privileged_access,
)
from qr_llm_evals.analysis.stats import binomial_ci
from qr_llm_evals.analysis.told_actual import decompose
from qr_llm_evals.cards import EvalCard, load_cards
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.tier0 import endpoints as tier0_endpoints
from qr_llm_evals.tier0.schema import Tier0Record
from test_decisions import _config as designed_config
from test_source_detection import _guesses, _records
from test_tier0 import synth

ALPHA = 0.01
LEVEL = 0.99


@pytest.fixture(scope="module")
def cards() -> dict[str, EvalCard]:
    return load_cards()


def _plans(cards: dict[str, EvalCard]) -> tuple[StatisticalPlan, StatisticalPlan]:
    """Designed Study 1 at alpha 0.01, and the same study without its design block."""
    config = designed_config(alpha=ALPHA)
    designed = StatisticalPlan.from_config(StudyConfig.model_validate(config), cards)
    del config["design"]
    legacy = StatisticalPlan.from_config(StudyConfig.model_validate(config), cards)
    assert designed.design is not None and designed.ci_level == LEVEL
    assert legacy.design is None
    return designed, legacy


def _levels(value: Any, path: str = "") -> dict[str, Any]:
    """Every ``ci_level`` (and ``envelope_level``) in a table, by where it stands."""
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}/{key}"
            if key in {"ci_level", "envelope_level"}:
                found[where] = item
            found.update(_levels(item, where))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(_levels(item, f"{path}/{index}"))
    return found


def _width(interval: Any) -> float:
    lower, upper = interval
    return float(upper) - float(lower)


# ── the primitives ───────────────────────────────────────────────────────────


def test_chance_test_intervals_follow_the_level() -> None:
    rows = _guesses(0.65)
    usual = chance_test(rows, quantum_arms=["qrng"], n_permutations=200, seed=42)
    wider = chance_test(rows, quantum_arms=["qrng"], n_permutations=200, seed=42, ci=LEVEL)

    splits = [("overall", usual, wider)]
    for group in ("by_variant", "by_guesser_source"):
        assert set(usual[group]) == set(wider[group]) and usual[group]
        splits += [(name, usual[group][name], wider[group][name]) for name in usual[group]]
    for name, at_95, at_99 in splits:
        # The level is named where the caller chose it, and nowhere else.
        assert "ci_level" not in at_95, name
        assert at_99["ci_level"] == LEVEL, name
        hits = round(at_99["raw_accuracy"] * at_99["n_guesses"])
        assert at_99["raw_accuracy_ci"] == list(binomial_ci(hits, at_99["n_guesses"], LEVEL))
        assert at_95["raw_accuracy_ci"] == list(binomial_ci(hits, at_95["n_guesses"]))
        for key in ("raw_accuracy_ci", "completion_accuracy_ci"):
            assert at_99[key][0] < at_95[key][0] and at_95[key][1] < at_99[key][1], (name, key)
        # Nothing but the intervals moves with the level.
        for key in ("balanced_accuracy", "p_value", "raw_accuracy", "completion_accuracy"):
            assert at_99[key] == at_95[key], (name, key)

    # No level is the 95% it always was, to the byte.
    assert chance_test(rows, quantum_arms=["qrng"], n_permutations=200, seed=42, ci=None) == usual
    explicit = chance_test(rows, quantum_arms=["qrng"], n_permutations=200, seed=42, ci=0.95)
    assert explicit.pop("ci_level") == 0.95
    assert explicit["raw_accuracy_ci"] == usual["raw_accuracy_ci"]
    assert explicit["completion_accuracy_ci"] == usual["completion_accuracy_ci"]


def _observer_inputs() -> tuple[dict[str, list[Tier0Record]], dict[str, str]]:
    rng = random.Random(5)
    labels: dict[str, str] = {}
    records: dict[str, list[Tier0Record]] = {}
    for c in range(60):
        ref = f"arm/self_referential_probe/p{c}/0/told_nothing"
        labels[ref] = "qrng" if c % 2 == 0 else "prng"
        shift = -0.8 if labels[ref] == "qrng" else -1.2
        logprob = min(-0.01, shift + rng.gauss(0, 0.4))
        records[ref] = _records(ref, logprob, n=rng.randint(10, 16), rng=rng)
    return records, labels


def test_outside_observer_interval_follows_the_level() -> None:
    records, labels = _observer_inputs()
    usual, correct = outside_observer(records, labels, n_permutations=20, seed=42)
    wider, correct_again = outside_observer(records, labels, n_permutations=20, seed=42, ci=LEVEL)
    assert usual["available"] and correct == correct_again
    assert "ci_level" not in usual and wider["ci_level"] == LEVEL
    right, total = sum(correct.values()), len(correct)
    assert usual["accuracy_ci"] == list(binomial_ci(right, total))
    assert wider["accuracy_ci"] == list(binomial_ci(right, total, LEVEL))
    assert _width(wider["accuracy_ci"]) > _width(usual["accuracy_ci"])
    assert {k: v for k, v in wider.items() if k not in {"ci_level", "accuracy_ci"}} == {
        k: v for k, v in usual.items() if k != "accuracy_ci"
    }


def test_privileged_access_interval_follows_the_level() -> None:
    rows = _guesses(0.7, completions=60)
    refs = sorted({r["source_sample_ref"] for arm in rows.values() for r in arm})
    rng = random.Random(9)
    observer = {ref: int(rng.random() < 0.55) for ref in refs}
    usual = privileged_access(rows, observer, n_resamples=2000, seed=42)
    wider = privileged_access(rows, observer, n_resamples=2000, seed=42, ci=LEVEL)
    assert usual["available"]
    assert (usual["ci_level"], wider["ci_level"]) == (0.95, LEVEL)
    assert wider["ci_lower"] < usual["ci_lower"] < usual["ci_upper"] < wider["ci_upper"]
    assert (wider["effect"], wider["p_value"]) == (usual["effect"], usual["p_value"])
    assert privileged_access(rows, observer, n_resamples=2000, seed=42, ci=None) == usual


def _told_rows() -> dict[str, list[dict[str, Any]]]:
    rng = np.random.default_rng(4)
    arms = ("qrng_unsupervised", "prng_seed_1", "prng_seed_7", "prng_seed_42")
    return {
        arm: [
            {
                "sample_ref": f"{arm}/self_referential_probe/p{p}/{e}/{told}",
                "judge_score": float(rng.integers(0, 2)),
            }
            for p in range(8)
            for e in range(4)
            for told in ("told_qrng", "told_prng", "told_nothing")
        ]
        for arm in arms
    }


EFFECTS = (
    "told_source_priming",
    "told_qrng_vs_nothing",
    "told_prng_vs_nothing",
    "actual_source_main_effect",
    "told_by_actual_interaction",
)


def test_told_actual_intervals_follow_the_level() -> None:
    rows = _told_rows()
    arms = (["qrng_unsupervised"], ["prng_seed_1", "prng_seed_7", "prng_seed_42"])
    usual = decompose(rows, "judge_score", *arms, n_permutations=200)
    wider = decompose(rows, "judge_score", *arms, n_permutations=200, ci=LEVEL)
    for effect in EFFECTS:
        at_95, at_99 = usual[effect], wider[effect]
        assert at_95["available"] and (at_95["ci_level"], at_99["ci_level"]) == (0.95, LEVEL)
        assert at_99["effect"] == at_95["effect"] and at_99["p_value"] == at_95["p_value"]
        # Same standard error, so the widths are in the ratio of the t quantiles.
        df = at_95["n_prompts"] - 1
        ratio = sp.t.ppf(0.995, df) / sp.t.ppf(0.975, df)
        assert _width((at_99["ci_lower"], at_99["ci_upper"])) == pytest.approx(
            ratio * _width((at_95["ci_lower"], at_95["ci_upper"]))
        )


def _session_records() -> list[Tier0Record]:
    records: list[Tier0Record] = synth.synthesize(
        "qrng_sup",
        seed=11,
        epochs=6,
        tokens=30,
        session_id="sess1",
        window_by_epoch=("supervised", "unsupervised", "break"),
    )
    return records


def test_cumulative_deviation_envelope_follows_the_level() -> None:
    records = _session_records()
    usual = tier0_endpoints.stream_deviation_table(
        records, n_permutations=50, tier0_source="engine_log"
    )
    wider = tier0_endpoints.stream_deviation_table(
        records, n_permutations=50, tier0_source="engine_log", envelope_level=LEVEL
    )
    at_95 = usual["sessions"]["sess1"]["cumulative_deviation"]
    at_99 = wider["sessions"]["sess1"]["cumulative_deviation"]
    # The table of a study without a registered level has the keys it always had.
    assert "envelope_95" in at_95 and "envelope" not in at_95 and "envelope_level" not in at_95
    assert "envelope_95" not in at_99 and at_99["envelope_level"] == LEVEL
    assert len(at_99["envelope"]) == len(at_95["envelope_95"]) > 0
    assert at_99["envelope"] == pytest.approx(
        [value * sp.norm.ppf(0.995) / 1.96 for value in at_95["envelope_95"]]
    )
    assert at_99["cumdev"] == at_95["cumdev"]
    json.dumps(wider)


def test_envelope_figure_names_its_level(tmp_path: Path) -> None:
    records = _session_records()
    for level, label in (
        (None, "95% envelope"),
        (LEVEL, "99% envelope"),
        (0.995, "99.5% envelope"),
    ):
        special = tier0_endpoints.stream_deviation_table(
            records, n_permutations=10, tier0_source="engine_log", envelope_level=level
        )
        table = {"card_id": "stream_deviation_windows", "comparisons": [], "special": special}
        directory = tmp_path / str(level)
        write_figures(directory, {"stream_deviation_windows--cumulative_deviation": table})
        drawn = [path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.svg"))]
        assert len(drawn) == 1
        # matplotlib writes legend text as a comment beside its glyphs.
        assert label in drawn[0]


# ── the executor ─────────────────────────────────────────────────────────────


def _guessing_data(tmp_path: Path) -> endpoints.AnalysisData:
    """Guesses on completions of the card that source_guessing is derived from."""
    guesses = _guesses(0.7, completions=60)
    refs = sorted({r["source_sample_ref"] for arm in guesses.values() for r in arm})
    rng = random.Random(3)
    completions = {
        "judged": [
            record
            for index, ref in enumerate(refs)
            for record in _records(
                ref,
                min(-0.01, (-0.8 if index % 2 == 0 else -1.2) + rng.gauss(0, 0.4)),
                n=12,
                rng=rng,
            )
        ]
    }
    return endpoints.AnalysisData(
        run_dir=tmp_path,
        tier0_source="engine_log",
        scores={"source_guessing": guesses, "self_referential_probe": _told_rows()},
        tier0={"creative_diversity": completions},
        questionnaires=[],
    )


def test_executor_passes_the_registered_level_to_source_detection(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    designed, legacy = _plans(cards)
    data = _guessing_data(tmp_path)
    blocks = {}
    for name, plan in (("designed", designed), ("legacy", legacy)):
        endpoint = next(
            e
            for e in plan.endpoints
            if (e.card_id, e.endpoint) == ("source_guessing", "guess_accuracy")
        )
        blocks[name] = endpoints._source_guessing_block(
            data, endpoint, plan, endpoints._card_snapshots(plan)
        )
        for part in ("chance_test", "outside_observer", "privileged_access"):
            assert blocks[name][part]["available"], (name, part)

    found = _levels(blocks["designed"])
    assert set(found.values()) == {LEVEL}
    # The overall test and each of its splits, the observer, and the
    # privileged-access interval. (The fixture's guesses were all sampled by
    # arms on the pseudo-random side, so there is one guesser split.)
    assert set(found) == {
        "/chance_test/ci_level",
        "/chance_test/by_variant/in_context/ci_level",
        "/chance_test/by_variant/self_judgement/ci_level",
        "/chance_test/by_guesser_source/guesses_sampled_by_prng/ci_level",
        "/outside_observer/ci_level",
        "/privileged_access/ci_level",
    }

    # A study without a design block: 95% everywhere, named only where it was.
    assert _levels(blocks["legacy"]) == {"/privileged_access/ci_level": 0.95}
    for part in ("chance_test", "outside_observer"):
        wide = blocks["designed"][part]
        key = "raw_accuracy_ci" if part == "chance_test" else "accuracy_ci"
        assert _width(wide[key]) > _width(blocks["legacy"][part][key])


def test_executor_passes_the_registered_level_to_the_told_blocks(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    designed, legacy = _plans(cards)
    data = _guessing_data(tmp_path)
    for plan, level in ((designed, LEVEL), (legacy, 0.95)):
        endpoint = next(
            e
            for e in plan.endpoints
            if (e.card_id, e.endpoint) == ("self_referential_probe", "experience_claim_rate")
        )
        block = endpoints._told_actual_block(data, endpoint, plan)
        assert list(block) == ["source:0"]
        found = _levels(block)
        assert len(found) == len(EFFECTS) and set(found.values()) == {level}


def test_every_interval_of_a_designed_table_is_at_the_plan_level(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    """Executed whole: no table of a designed analysis carries a 95% interval."""
    designed, legacy = _plans(cards)
    data = _guessing_data(tmp_path)

    tables = endpoints.execute(data, designed)
    found = _levels(tables)
    # TOST reports the 1 - 2 alpha interval that its decision at alpha is read from.
    tost = {where: level for where, level in found.items() if "/equivalence/" in where}
    rest = {where: level for where, level in found.items() if where not in tost}
    assert tost and all(level == pytest.approx(1 - 2 * ALPHA) for level in tost.values())
    assert set(rest.values()) == {LEVEL}
    # The told/actual blocks, their headline effects, the source-detection
    # block and the decisions are all among them.
    for fragment in (
        "/self_referential_probe--experience_claim_rate/told_actual/",
        "/self_referential_probe--told_main_effect/headline_effect/",
        "/source_guessing--guess_accuracy/chance_test/",
        "/source_guessing--guess_accuracy/privileged_access/",
        "/decisions/rows/",
    ):
        assert any(fragment in where for where in rest), fragment

    # The same run under the plan without a design block: 95% wherever a level is named.
    usual = _levels(endpoints.execute(data, legacy))
    assert usual and set(usual.values()) == {0.95}
