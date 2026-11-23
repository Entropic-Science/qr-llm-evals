"""A two-stage eval through the real runner: the MASK pair on synthetic records.

``mask`` is derived from ``mask_beliefs`` twice over: it generates only where
the first card wrote belief replies, and its judgement needs the judge's
readings of those replies. This drives both cards through generation, the
judge phase and the analysis loader against the stub engine and stub judge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.analysis.endpoints import JUDGE_SCORE_FIELD, load_data
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.models import config_hash
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.runner import NullEventSink, Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.store import Run, RunStore, Store
from stub_engine import JudgeSay, StubEngine, StubJudge
from test_eval_mask import _row
from test_runner import ARMS, NOW, H, _secrets, _study

#: The belief prompts of the first six synthetic records: four binary records
#: with three prompts each, one provided_facts record with none, one numerical with one.
BELIEF_PROMPTS = 13
RECORDS = 6
K = 10


def _two_stage_study() -> StudyConfig:
    data = _study().model_dump(mode="json")
    # The derived card is listed first: the runner must still run it second.
    data["cards"] = [
        {"card_id": "mask"},
        {"card_id": "mask_beliefs", "overrides": {"items": BELIEF_PROMPTS}},
    ]
    return StudyConfig.model_validate(data)


def _run_store(root: Path, study: StudyConfig) -> RunStore:
    store = Store(root)
    study_dir = store.freeze_study(study, "# prereg\n")
    return store.create_run(
        Run(
            run_id="run1",
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash=H,
            config_hash=config_hash(study),
        )
    )


def _judge_reply(body: dict[str, Any]) -> JudgeSay | None:
    """The belief readings say A (or 5), every pressured statement says B (or 5)."""
    text = json.dumps(body)
    if "Final Answer" not in text and "lower_bound" not in text:
        return None  # the call before the first generation
    if "lower_bound" in text:
        return JudgeSay(text='{"lower_bound": 5.0, "upper_bound": 5.0}')
    pressured = "contradicts its usual beliefs" in text
    return JudgeSay(text=f"Reasoning.\nFinal Answer: {'B' if pressured else 'A'}")


def test_mask_pair_runs_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cards = load_cards()
    study = _two_stage_study()
    control = RunControl()
    control.start()
    engine, judge = StubEngine(), StubJudge(script=_judge_reply)
    orch = Orchestrator(
        study,
        cards,
        _run_store(tmp_path, study),
        RunnerConfig(vllm_origin="http://stub", max_inflight=2, repo_root=REPO_ROOT),
        secrets=_secrets(),
        sink=NullEventSink(),
        control=control,
        transport=engine.transport,
        judge_transport=judge.transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    )
    beliefs = orch._task_for(cards["mask_beliefs"]).module
    records = tuple(
        beliefs._record(archetype, short, _row(archetype, n))
        for n in range(2)
        for archetype, (short, _, _) in beliefs.ARCHETYPES.items()
    )
    monkeypatch.setattr(beliefs, "_download_records", lambda: records)

    run = orch.run()
    assert run.state == "complete"

    store = orch.store
    for arm in ARMS:
        assert len(store.completed_refs(arm, "mask_beliefs")) == BELIEF_PROMPTS * K
        # The first six records: the pressured card ran where the first card wrote beliefs.
        refs = store.completed_refs(arm, "mask")
        assert len(refs) == RECORDS * K
        assert {ref.split("/")[2] for ref in refs} == {
            "mask_cont_t0",
            "mask_disi_t0",
            "mask_dd_t0",
            "mask_kf_t0",
            "mask_pf_t0",
            "mask_stat_t0",
        }
        verdicts = store.judge_rows(arm, "mask", "study_judge")
        assert len(verdicts) == RECORDS * K and all(row.status == "ok" for row in verdicts)
        by_record: dict[str, set[float | None]] = {}
        for row in verdicts:
            by_record.setdefault(row.sample_ref.split("/")[2], set()).add(row.score)
        # Belief A and statement B is a lie, also against the facts a provided_facts
        # record states; the numerical statement repeats the believed value.
        assert by_record == {
            "mask_cont_t0": {1.0},
            "mask_disi_t0": {1.0},
            "mask_dd_t0": {1.0},
            "mask_kf_t0": {1.0},
            "mask_pf_t0": {1.0},
            "mask_stat_t0": {0.0},
        }
        honest = [row.fields["honest"] for row in verdicts]
        assert sum(value or 0 for value in honest) == K

    # Every belief reply was judged before the first pressured reply was.
    # (A pressured prompt shows the record's own system prompt, a belief prompt the neutral one.)
    kinds = ["Pressure system" in json.dumps(call) for call in judge.calls]
    assert kinds == sorted(kinds)
    assert (kinds.count(False), kinds.count(True)) == (2 * BELIEF_PROMPTS * K, 2 * RECORDS * K)

    data = load_data(store.run_dir.arms_dir.parent)
    for arm in ARMS:
        rows = data.scores["mask"][arm]
        assert len(rows) == RECORDS * K
        assert sum(row[JUDGE_SCORE_FIELD] for row in rows) == (RECORDS - 1) * K
        held = data.scores["mask_beliefs"][arm]
        assert len(held) == BELIEF_PROMPTS * K and all(
            row[JUDGE_SCORE_FIELD] == 1.0 for row in held
        )


def test_an_items_override_on_a_derived_card_is_a_cap() -> None:
    """A derived card has as many items as the run allows; a fixed list must have enough."""
    from qr_llm_evals.models.schema import CardOverrides
    from qr_llm_evals.runner.tasks import TaskModuleError, select_items

    cards = load_cards()
    two = [{"prompt_id": "a"}, {"prompt_id": "b"}]
    more = CardOverrides(items=5)
    assert select_items(two, cards["mask"], more) == two
    assert select_items(two, cards["mask"], CardOverrides(items=1)) == two[:1]
    with pytest.raises(TaskModuleError, match="exceeds 2 available"):
        select_items(two, cards["mask_beliefs"], more)
