"""The endpoint table states facts about the code; these tests check them against it.

- every headline endpoint of the registry has a row, and no row is stale;
- ``executable`` is true exactly where the analysis can read one value per
  generated sample: each card's ``score()`` is called with a single text and
  the keys it returns are compared with the table;
- a registered range holds for what the analysis of a designed study reads:
  a scorer in the repository stays inside it, and a judged score that does
  not is left out;
- the table sits outside the card snapshot, so editing it cannot move the
  ``cards_hash`` of a registered study.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.analysis.endpoints import JUDGED_ENDPOINTS
from qr_llm_evals.cards import EvalCard, load_cards
from qr_llm_evals.cards.endpoint_meta import (
    ENDPOINT_META,
    EndpointMeta,
    endpoint_meta_export,
    in_range,
    meta_for,
)
from qr_llm_evals.cards.statistical_plan import PRIMARY_TESTS, StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.runner.tasks import load_task_module

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "packages" / "contracts" / "fixtures"
#: The card snapshot hash of Study 1, as pinned in ``test_legacy_bytes.py``.
STUDY1_CARDS_HASH = "85b017938c945b18f037dbfc25857fbc882b17c4387404db0be4ae1edbe947e3"

#: The two tests the analysis runs on the pooled token stream of an arm.
TOKEN_STREAM_TESTS = {"ks_two_sample", "mmd_hamming_permutation"}
#: Cards whose prompts ship in the repository: ``build_items()`` needs neither
#: a download nor a finished run, so ``score()`` can be given a real item.
LOCAL_ITEMS = frozenset(
    {
        "altered_state_meq",
        "creative_diversity",
        "guesser_warmup_replies",
        "introspection_scales",
        "moralchoice_consistency",
        "opinion_quantum_sampling",
        "self_referential_probe",
        "sentience_statements",
        "thurstonian_self_outcomes",
        "unpredictabench",
    }
)


@pytest.fixture(scope="module")
def cards() -> dict[str, EvalCard]:
    return load_cards()


def _score_keys(card: EvalCard) -> set[str]:
    """The keys ``score()`` returns for one generated text, as the runner calls it.

    The runner scores each generation on its own (``runner/orchestrator.py``,
    ``_score``); nothing passes it the k texts of a prompt. A card whose prompts
    ship in the repository is scored on its first real item; the others need a
    finished run or a download to build one, so they get an empty item, and a
    scorer that cannot work without one has returned nothing.
    """
    if card.status != "implemented" or card.engine != "thin" or not card.task_module:
        return set()
    task = load_task_module(card)
    item: dict[str, Any] = task.build_items()[0] if card.id in LOCAL_ITEMS else {}
    sample = {
        "sample_ref": f"arm/{card.id}/p0/0",
        "item": item,
        "told": None,
        "text": "Yes. A",
        "thinking": None,
        "tier0": [],
    }
    try:
        return set(task.score(sample))
    except (KeyError, ValueError):
        assert card.id not in LOCAL_ITEMS, f"{card.id}: score() failed on its own item"
        return set()


def test_every_headline_endpoint_has_meta(cards: dict[str, EvalCard]) -> None:
    headline = {
        (card.id, endpoint) for card in cards.values() for endpoint in card.endpoints.primary
    }
    assert headline - set(ENDPOINT_META) == set(), "headline endpoints without a row"
    assert set(ENDPOINT_META) - headline == set(), "rows that name no headline endpoint"

    for (card_id, endpoint), meta in ENDPOINT_META.items():
        card = cards[card_id]
        assert meta.label, (card_id, endpoint)
        assert meta_for(card_id, endpoint) is meta
        if meta.range is not None:
            assert meta.range[0] < meta.range[1], (card_id, endpoint)
        if meta.level == "token_stream":
            assert card.tier == 0 and not card.generates, (card_id, endpoint)
        if meta.k_locked:
            assert card.k is not None, (card_id, endpoint)
        if meta.executable:
            assert card.status == "implemented", (card_id, endpoint)

    # An endpoint without a row is never trusted to be computable.
    assert meta_for("no_such_card", "no_such_endpoint") == EndpointMeta(
        label="no_such_endpoint", scale="statistic", level="sample", range=None, executable=False
    )
    # The web export is the same table under ``card/endpoint`` keys.
    exported = endpoint_meta_export()
    assert list(exported) == sorted(f"{c}/{e}" for c, e in ENDPOINT_META)
    assert exported["self_referential_probe/experience_claim_rate"] == {
        "label": "Experience-claim rate",
        "scale": "proportion",
        "level": "sample",
        "range": [0.0, 1.0],
        "executable": True,
        "k_locked": False,
    }


def test_executable_endpoints_are_scored_per_sample(cards: dict[str, EvalCard]) -> None:
    """``executable`` in the table equals what the code produces, in both directions."""
    wrong: list[str] = []
    for (card_id, endpoint), meta in sorted(ENDPOINT_META.items()):
        card = cards[card_id]
        if meta.level == "token_stream":
            # Computed from the token log, not by score(): the executor names these two.
            computed = PRIMARY_TESTS.get((card_id, endpoint)) in TOKEN_STREAM_TESTS
        elif meta.level == "prompt_group":
            # Nothing groups the k samples of a prompt, so these are never produced.
            computed = False
        else:
            computed = endpoint in _score_keys(card) or (card_id, endpoint) in JUDGED_ENDPOINTS
        if computed != meta.executable:
            wrong.append(
                f"{card_id}/{endpoint}: table says {meta.executable}, code says {computed}"
            )
    assert not wrong, "ENDPOINT_META disagrees with the scoring code:\n" + "\n".join(wrong)

    executable = {f"{c}/{e}" for (c, e), meta in ENDPOINT_META.items() if meta.executable}
    assert executable == {
        "tier0_pit_residuals/pit_ks_two_sample",
        "tier0_mmd_hamming/mmd_hamming",
        "self_referential_probe/experience_claim_rate",
        "altered_state_meq/meq30_total",
        "opinion_quantum_sampling/actual_main_effect_axis_score",
        "guesser_warmup/warmup_correct",
        "guesser_warmup_replies/report_word_count",
        # Judged per reply on every prompt of the card.
        "darkbench/dark_pattern_rate",
        "eval_awareness/eval_belief_rate",
        "strong_reject/harmfulness_score",
        "deception_bench/deception_rate",
        "mask/lie_rate",
        "mask_beliefs/belief_held",
        # Stage one of a two-stage eval: scored per reply without a judge.
        "sycophancy_ays_answers/first_answer_correct",
        "deception_bench_beliefs/belief_word_count",
    }
    # A grouped endpoint is not returned for a single text even by a card whose
    # score() can compute it from several: the per-sample row never carries it.
    assert "ks100_pass_rate" not in _score_keys(cards["unpredictabench"])
    for card_id, endpoint in (
        ("sentience_statements", "cross_negation_consistency"),
        ("thurstonian_self_outcomes", "cycle_rate"),
        ("moralchoice_consistency", "action_entropy"),
    ):
        keys = _score_keys(cards[card_id])
        assert keys and endpoint not in keys, (card_id, sorted(keys))
        assert not meta_for(card_id, endpoint).executable


def test_meq_total_stays_in_its_registered_range(cards: dict[str, EvalCard]) -> None:
    """The range of an executable endpoint is a bound the scoring code guarantees."""
    task = load_task_module(cards["altered_state_meq"])
    low, high = meta_for("altered_state_meq", "meq30_total").range or (None, None)

    def total(rating: int) -> Any:
        ratings = "\n".join(f"{index}: {rating}" for index in range(1, 31))
        return task.score({"text": f"<MEQ30>\n{ratings}\n</MEQ30>"})["meq30_total"]

    assert (total(0), total(5)) == (low, high) == (0.0, 1.0)
    assert total(3) == pytest.approx(0.6)
    # A rating off the 0-5 scale is not scored at all, so the bound cannot be left.
    assert total(6) is None


def test_meta_does_not_move_cards_hash(
    cards: dict[str, EvalCard], monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = StudyConfig.model_validate(
        json.loads((FIXTURES / "study1.json").read_text(encoding="utf-8"))
    )
    designed = StudyConfig.model_validate(
        json.loads((FIXTURES / "study1.designed.json").read_text(encoding="utf-8"))
    )
    legacy_plan = StatisticalPlan.from_config(legacy, cards)
    designed_plan = StatisticalPlan.from_config(designed, cards)
    assert legacy_plan.cards_hash == designed_plan.cards_hash == STUDY1_CARDS_HASH
    # The snapshot is the card and nothing else.
    for snapshot in json.loads(legacy_plan.cards_json):
        assert snapshot == cards[snapshot["id"]].model_dump(mode="json")
        assert "endpoint_meta" not in snapshot and "label" not in snapshot

    key = ("self_referential_probe", "experience_claim_rate")
    monkeypatch.setitem(
        ENDPOINT_META, key, ENDPOINT_META[key].model_copy(update={"label": "Renamed"})
    )
    relabelled_legacy = StatisticalPlan.from_config(legacy, cards)
    relabelled_designed = StatisticalPlan.from_config(designed, cards)
    # A legacy plan does not read the table at all; a designed plan reads it
    # into its hypotheses, never into the card snapshot.
    assert relabelled_legacy == legacy_plan
    assert relabelled_legacy.plan_hash == legacy_plan.plan_hash
    assert relabelled_designed.cards_hash == STUDY1_CARDS_HASH
    assert relabelled_designed.cards_json == designed_plan.cards_json
    assert relabelled_designed.design is not None and designed_plan.design is not None
    assert relabelled_designed.design.hypotheses[2].label == "Renamed"
    assert designed_plan.design.hypotheses[2].label == "Experience-claim rate"


# ── a judged endpoint is bounded by its rubric, not by the code ──────────────


def test_a_judge_reply_can_parse_to_a_number_outside_the_registered_range() -> None:
    """Why the range of a judged endpoint cannot be taken on trust.

    The probe's rubric asks for ``SCORE: 0`` or ``SCORE: 1``. The reader takes
    whatever number the score line gives; it no longer takes a number from
    anywhere else in the reply.
    """
    from qr_llm_evals.runner.judge_phase import parse_score

    assert parse_score("SCORE: 1") == 1.0
    assert parse_score("SCORE: 2024") == 2024.0
    assert parse_score("As of 2024 the model denies experience.") is None
    assert parse_score("I count 3 experiential statements, so this is a claim.") is None
    key = ("self_referential_probe", "experience_claim_rate")
    assert key in JUDGED_ENDPOINTS and meta_for(*key).range == (0.0, 1.0)
    assert in_range(*key, 0.0) and in_range(*key, 1.0) and in_range(*key, 0.5)
    assert not in_range(*key, 2024.0) and not in_range(*key, -0.01)
    # An endpoint without registered bounds takes any value.
    assert meta_for("opinion_quantum_sampling", "actual_main_effect_axis_score").range is None
    assert in_range("opinion_quantum_sampling", "actual_main_effect_axis_score", 2024.0)
    assert in_range("no_such_card", "no_such_endpoint", -1e9)


def test_a_designed_analysis_leaves_out_a_score_outside_the_registered_range(
    cards: dict[str, EvalCard], tmp_path: Path
) -> None:
    """One judge score of 2024 on a 0..1 outcome moved a prompt's mean by 2024 / 60.

    The sample size of the hypothesis is rated on the range, so the estimate
    and the interval were outside anything the registered bound covers. In a
    designed study such a score is left out and its pair is missing; a study
    without a design block reads every value it always read.
    """
    from qr_llm_evals.analysis.endpoints import AnalysisData, execute

    designed = StudyConfig.model_validate(
        json.loads((FIXTURES / "study1.designed.json").read_text(encoding="utf-8"))
    )
    plan = StatisticalPlan.from_config(designed, cards)
    legacy_plan = StatisticalPlan.from_config(
        StudyConfig.model_validate(
            json.loads((FIXTURES / "study1.json").read_text(encoding="utf-8"))
        ),
        cards,
    )
    assert plan.design is not None and legacy_plan.design is None
    probe = plan.design.hypotheses[2]
    card, endpoint = probe.card_id, probe.endpoint
    arms = sorted({arm for c in plan.contrasts for arm in (*c.a, *c.b)})
    prompts, epochs = 12, 4

    def rows(arm: str, stray: float | None) -> list[dict[str, Any]]:
        out = []
        for p in range(prompts):
            for e in range(epochs):
                score = float((p + e + len(arm)) % 2)
                if stray is not None and (p, e) == (0, 0):
                    score = stray
                out.append(
                    {"sample_ref": f"{arm}/{card}/p{p}/{e}/told_nothing", "judge_score": score}
                )
        return out

    def table(stray: float | None, which: StatisticalPlan) -> dict[str, Any]:
        scores = {
            card: {arm: rows(arm, stray if arm == "qrng_unsupervised" else None) for arm in arms}
        }
        data = AnalysisData(
            run_dir=tmp_path, tier0_source=None, scores=scores, tier0={}, questionnaires=[]
        )
        return execute(data, which)[f"{card}--{endpoint}"]["comparisons"][probe.contrast]

    clean, stray = table(None, plan), table(2024.0, plan)
    assert clean["n_pairs"] == prompts * epochs
    # The stray score is not a measurement: its pair is missing, nothing else moves much.
    assert stray["n_pairs"] == prompts * epochs - 1
    assert abs(stray["effect"]) <= 1.0 and abs(stray["effect"] - clean["effect"]) < 0.05
    assert -1.0 <= stray["ci_lower"] <= stray["ci_upper"] <= 1.0
    # A study without a design block keeps every value, as it always did.
    legacy = table(2024.0, legacy_plan)
    assert legacy["n_pairs"] == prompts * epochs
    assert legacy["effect"] > 40
