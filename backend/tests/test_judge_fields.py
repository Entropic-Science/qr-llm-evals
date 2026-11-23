"""``judge_fields``: a card reads several named values from one judge reply.

The hook is optional on a task module. Its ``score`` stays the judge row's
score, the other values are stored beside it, and the analysis joins them to
the sample's score row under their own names. Driven against the stub engine
and stub judge in a ``tmp_path`` data root.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.analysis.endpoints import JUDGE_SCORE_FIELD, load_data
from qr_llm_evals.runner.judge_phase import JudgePhase
from qr_llm_evals.runner.tasks import LoadedTask, TaskModuleError, load_task_module
from qr_llm_evals.store.schema import JudgeRow
from stub_engine import StubEngine, StubJudge
from test_runner import (
    ARMS,
    FIXTURES,
    NOW,
    PER_ARM_TOTAL,
    _cards,
    _orchestrator,
    _run_store,
    _secrets,
    _study,
)

CARD = "stub_free"


def _task(judge_fields: Any = None) -> LoadedTask:
    task = load_task_module(_cards()[CARD], FIXTURES)
    if judge_fields is not None:
        task.module.judge_fields = judge_fields
    return task


def _phase(tmp_path: Path) -> JudgePhase:
    orch = _orchestrator(_run_store(tmp_path), StubEngine())
    orch.run_generation()
    return JudgePhase(
        orch.store,
        _study(),
        _secrets(),
        vllm_origin="http://stub",
        transport=StubJudge().transport,
        repo_root=FIXTURES,
        clock=lambda: NOW,
    )


def _rows(phase: JudgePhase) -> list[JudgeRow]:
    store = phase._store
    return [row for arm in ARMS for row in store.judge_rows(arm, CARD, "study_judge")]


def test_a_card_without_the_hook_is_judged_as_before(tmp_path: Path) -> None:
    task = _task()
    assert not task.has_judge_fields
    phase = _phase(tmp_path)
    counts = phase.judge_card(ARMS, _cards()[CARD], task)
    assert counts == {"judged": 2 * PER_ARM_TOTAL, "skipped": 0, "failed": 0}
    rows = _rows(phase)
    assert all(row.fields == {} and row.score is not None for row in rows)
    # A row written before the field existed still loads.
    old = rows[0].model_dump(mode="json")
    del old["fields"]
    assert JudgeRow.model_validate(old).fields == {}


def test_named_values_are_stored_and_reach_the_analysis(tmp_path: Path) -> None:
    def judge_fields(sample: Mapping[str, Any], raw: str) -> dict[str, float | None]:
        number = int(raw.rsplit(":", 1)[1])
        assert sample["sample_ref"] and "item" in sample
        return {"score": number % 2, "raw_number": number, "not_applicable": None}

    phase = _phase(tmp_path)
    counts = phase.judge_card(ARMS, _cards()[CARD], _task(judge_fields))
    assert counts == {"judged": 2 * PER_ARM_TOTAL, "skipped": 0, "failed": 0}
    rows = _rows(phase)
    for row in rows:
        assert row.score in (0.0, 1.0)
        assert row.fields["raw_number"] is not None and row.fields["raw_number"] % 2 == row.score
        assert row.fields["not_applicable"] is None
        assert "score" not in row.fields

    data = load_data(phase._store.run_dir.arms_dir.parent)
    merged = [row for arm in ARMS for row in data.scores[CARD][arm]]
    assert len(merged) == 2 * PER_ARM_TOTAL
    by_ref = {row.sample_ref: row for row in rows}
    for row in merged:
        judged = by_ref[row["sample_ref"]]
        assert row[JUDGE_SCORE_FIELD] == judged.score
        assert row["raw_number"] == judged.fields["raw_number"]
        # A value that does not apply to the sample is absent, not zero.
        assert "not_applicable" not in row


def test_a_reply_the_card_cannot_read_is_kept_without_a_score_and_not_asked_again(
    tmp_path: Path,
) -> None:
    unread: set[str] = set()

    def judge_fields(sample: Mapping[str, Any], raw: str) -> dict[str, float | None] | None:
        if len(unread) < 2 or sample["sample_ref"] in unread:
            unread.add(sample["sample_ref"])
            # Once with no reading at all, once with a reading that has no score.
            return None if len(unread) == 1 else {"raw_number": 3.0}
        return {"score": 1.0}

    phase = _phase(tmp_path)
    card = _cards()[CARD]
    counts = phase.judge_card(ARMS, card, _task(judge_fields))
    assert counts == {"judged": 2 * PER_ARM_TOTAL - 2, "skipped": 0, "failed": 2}
    failed = [row for row in _rows(phase) if row.score is None]
    assert len(failed) == 2 and all(row.fields == {} for row in failed)
    assert all(row.raw.startswith("Reasoning.") for row in failed), "the reply is kept"
    assert [row.status for row in failed] == ["unreadable", "unreadable"]
    # A reply that was received is final. A resumed phase used to ask these two
    # again, which is a second draw from the judge chosen by what the first said.
    again = phase.judge_card(ARMS, card, _task(lambda sample, raw: {"score": 0.0}))
    assert again == {"judged": 0, "skipped": 2 * PER_ARM_TOTAL, "failed": 0}


def test_a_derived_card_is_judged_on_the_items_it_builds_from_the_run(tmp_path: Path) -> None:
    """The judge sees what the generation phase saw: items built per arm from the store."""
    base = _cards()[CARD]
    card = base.model_copy(
        update={"dataset": base.dataset.model_copy(update={"source": f"derived:{CARD}"})}
    )
    task = _task(lambda sample, raw: {"score": 1.0})
    plain = task.module.build_items
    asked: list[tuple[str | None, bool]] = []
    seen: set[str] = set()

    def build_items(
        run_store: Any = None, arm_id: str | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        asked.append((arm_id, run_store is not None))
        return [{**item, "built_for": arm_id} for item in plain()]

    def judge_prompt(sample: Mapping[str, Any]) -> str:
        arm = str(sample["sample_ref"]).split("/")[0]
        assert sample["item"]["built_for"] == arm
        seen.add(arm)
        return "judge this"

    phase = _phase(tmp_path)
    task.module.build_items = build_items
    task.module.judge_prompt = judge_prompt
    counts = phase.judge_card(ARMS, card, task)
    assert counts == {"judged": 2 * PER_ARM_TOTAL, "skipped": 0, "failed": 0}
    assert asked == [(arm, True) for arm in ARMS] and seen == set(ARMS)


@pytest.mark.parametrize(
    "bad",
    [["score", 1.0], {"score": "high"}, {"score": True}, {3: 1.0}],
    ids=["not a mapping", "text value", "bool value", "non-text name"],
)
def test_a_malformed_reading_is_a_task_module_error(bad: Any) -> None:
    task = _task(lambda sample, raw: bad)
    with pytest.raises(TaskModuleError, match="judge_fields"):
        task.judge_fields({"sample_ref": "a/b/c/0"}, "raw")


def test_numbers_become_floats_and_none_passes_through() -> None:
    task = _task(lambda sample, raw: {"score": 1, "half": 0.5, "none": None})
    assert task.judge_fields({}, "raw") == {"score": 1.0, "half": 0.5, "none": None}
    assert _task(lambda sample, raw: None).judge_fields({}, "raw") is None
    with pytest.raises(TaskModuleError, match="no judge_fields"):
        _task().judge_fields({}, "raw")
