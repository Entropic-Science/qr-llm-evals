"""The judge phase, row by row: what is read, what is stored and what is asked again.

A reply that was received is final. Only a judgement that got no reply is
asked again when a run is resumed, so what a reply says can never select which
samples get a second draw from the judge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import run_check_helpers as h
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.runner.judge_phase import PREFLIGHT_USER, JudgePhase
from qr_llm_evals.store.schema import JudgeRow
from stub_engine import JudgeSay, StubEngine, StubJudge

MARK = "TEXT-THAT-MUST-NOT-REACH-A-FLAG"
#: A real card whose rubric allows 0 and 1 only.
RUBRIC_01 = "eval_awareness"


def _plan(replies: dict[int, JudgeSay], default: JudgeSay) -> Any:
    """Answer the n-th distinct sample (by its text) as ``replies[n]`` says."""
    seen: dict[str, int] = {}

    def script(body: dict[str, Any]) -> JudgeSay | None:
        user = str(body["messages"][0]["content"])
        if user == PREFLIGHT_USER:
            return None
        return replies.get(seen.setdefault(user, len(seen)), default)

    return script


def test_every_judgement_gets_a_status_and_a_reply_that_was_received_is_final(
    tmp_path: Path,
) -> None:
    config = h.study(RUBRIC_01, seeded=0)
    cards = {RUBRIC_01: h.card(card_id=RUBRIC_01)}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(
        script=_plan(
            {
                0: JudgeSay(text="The reply lists 3 reasons and names 2 tests."),
                1: JudgeSay(text="SCORE: 7"),
                2: JudgeSay(text="**SCORE: 1**", stop_reason="max_tokens"),
                3: JudgeSay(status=500, error_body=MARK),
            },
            JudgeSay(text="Reasoning.\nSCORE: 0"),
        )
    )
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()
    assert run.state == "complete"

    rows = rs.judge_rows("qrng", RUBRIC_01, "study_judge")
    assert [(r.status, r.score) for r in rows] == [
        ("unreadable", None),  # used to be read as 2.0, the last number in the reply
        ("out_of_range", None),  # used to be stored as 7.0 on a 0-or-1 rubric
        ("ok", 1.0),
        ("failed", None),
        *[("ok", 0.0)] * 8,
    ]
    assert "3 reasons" in rows[0].raw and rows[1].raw == "SCORE: 7", "the raw reply is kept"
    assert rows[3].raw.startswith("error: JudgeError")
    assert [r.stop_reason for r in rows[:4]] == ["end_turn", "end_turn", "max_tokens", None]
    version = rc.JUDGE_READER_VERSION
    assert [r.reader_version for r in rows[:4]] == [version, version, version, None]

    found = {(f.kind, f.sample_ref): f for f in rs.flags()}
    assert {kind for kind, _ in found} == {
        "judge_unreadable",
        "judge_out_of_range",
        "judge_cut",
        "judge_failed",
    }
    assert all(f.phase == "judge" and f.card_id == RUBRIC_01 for f in found.values())
    assert found[("judge_out_of_range", rows[1].sample_ref)].detail == (
        "the reply gave 7, which the rubric does not allow"
    )
    assert found[("judge_cut", rows[2].sample_ref)].detail == "stop reason max_tokens"
    assert found[("judge_failed", rows[3].sample_ref)].detail == "HTTP 500"
    assert MARK not in h.run_text(rs) and h.JUDGE_KEY not in h.run_text(rs)

    # Resume: only the judgement without a reply is asked again. The two replies
    # that gave no usable score were received, so they stand as they are.
    working = StubJudge(script=lambda body: JudgeSay(text="SCORE: 1"))
    again = h.orchestrator(h.reopen(rs), config, cards, StubEngine(), judge=working)
    assert again.run_judge() == {"judged": 1, "skipped": 11, "failed": 0}
    assert [str(c["messages"][0]["content"]) for c in working.calls] == [
        str(judge.calls[3]["messages"][0]["content"])
    ]
    last = {r.sample_ref: r for r in rs.judge_rows("qrng", RUBRIC_01, "study_judge")}
    assert last[rows[0].sample_ref].status == "unreadable"
    assert last[rows[1].sample_ref].status == "out_of_range"
    assert last[rows[3].sample_ref].status == "ok"


def test_an_error_in_the_cards_own_judge_prompt_or_judge_fields_is_one_failed_judgement(
    tmp_path: Path,
) -> None:
    """These used to escape the judge phase and leave the run `running` with no thread."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    refs = [job.sample_ref for job in orch.plan_arm(config.arms[0])]
    no_prompt, no_fields, unread, infinite = refs[0], refs[1], refs[2], refs[3]
    real_prompt = orch._task_for(cards[h.CARD]).module.judge_prompt

    def judge_prompt(sample: dict[str, Any]) -> str:
        if sample["sample_ref"] == no_prompt:
            raise ValueError(MARK)
        return str(real_prompt(sample))

    def judge_fields(sample: dict[str, Any], raw: str) -> dict[str, float] | None:
        if sample["sample_ref"] == no_fields:
            raise KeyError(MARK)
        if sample["sample_ref"] == unread:
            return None
        if sample["sample_ref"] == infinite:
            return {"score": float("inf")}
        return {"score": 1.0, "severity": 0.5}

    h.with_task(orch, cards[h.CARD], judge_prompt=judge_prompt, judge_fields=judge_fields)
    assert orch.run().state == "complete"

    rows = {r.sample_ref: r for r in rs.judge_rows("qrng", h.CARD, "study_judge")}
    assert (rows[no_prompt].status, rows[no_fields].status) == ("failed", "failed")
    assert rows[no_prompt].raw.startswith("error: ValueError")
    # The judge did reply to this one: the reply and its cost are kept on the row.
    assert "SCORE:" in rows[no_fields].raw and rows[no_fields].usage.completion_tokens == 5
    assert (rows[unread].status, rows[infinite].status) == ("unreadable", "unreadable")
    assert rows[refs[4]].status == "ok" and rows[refs[4]].fields == {"severity": 0.5}
    failed = {f.sample_ref: f.detail for f in rs.flags() if f.kind == "judge_failed"}
    assert failed == {no_prompt: "ValueError", no_fields: "KeyError"}
    assert MARK not in h.run_text(rs)


def test_rows_from_before_rows_had_a_status_are_asked_again_only_without_a_score(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    orch.run_generation()
    refs = sorted(rs.completed_refs("qrng", h.CARD))

    def old_row(ref: str, score: float | None) -> JudgeRow:
        return JudgeRow(
            sample_ref=ref,
            judge_id="study_judge",
            model="claude-sonnet-4-6",
            rubric_hash="a" * 64,
            score=score,
            raw="SCORE: 4" if score is not None else "error: JudgeError: 500",
            at=h.NOW,
        )

    rs.append_judge("qrng", h.CARD, old_row(refs[0], 4.0))
    rs.append_judge("qrng", h.CARD, old_row(refs[1], None))
    judge = StubJudge()
    again = h.orchestrator(h.reopen(rs), config, cards, StubEngine(), judge=judge)
    assert again.run_judge() == {"judged": 11, "skipped": 1, "failed": 0}
    assert len(judge.calls) == 11


def test_the_judge_phase_takes_no_more_work_once_its_breaker_has_tripped(tmp_path: Path) -> None:
    """The phase has no pause point of its own; the latch is what ends it."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    orch.run_generation()
    judge = StubJudge(
        script=lambda body: (
            None if body["messages"][0]["content"] == PREFLIGHT_USER else JudgeSay(status=401)
        )
    )
    trips: list[rc.Trip] = []
    phase = JudgePhase(
        rs,
        config,
        orch.secrets,
        vllm_origin="http://stub",
        transport=judge.transport,
        repo_root=h.FIXTURES,
        clock=lambda: h.NOW,
        breaker=rc.Breaker(),
        on_trip=trips.append,
    )
    counts = phase.judge_card(["qrng"], cards[h.CARD], orch._task_for(cards[h.CARD]))
    assert counts == {"judged": 0, "skipped": 0, "failed": 1}
    assert [trip.rule for trip in trips] == ["judge_rejected"] and len(judge.calls) == 1


HUGE_JSON = '{"score": 1' + "0" * 400 + "}"
DEEP_JSON = '{"score": ' + "[" * 5000
HUGE_LINE = "Reasoning.\nSCORE: 1" + "0" * 400


@pytest.mark.parametrize("card_id", [RUBRIC_01, h.CARD])
def test_a_reply_the_reader_cannot_take_in_is_stored_as_unreadable_and_is_final(
    tmp_path: Path, card_id: str
) -> None:
    """A score of hundreds of digits or a thousand nested brackets used to wedge the run.

    The reader raised, the judge phase ended with ``judge_phase_error`` and no
    row for the sample, and every resume asked the same sample and stopped
    again. On a card without allowed values an infinite score reached the row
    and could not be written.
    """
    config = h.study(card_id, seeded=0)
    cards = {card_id: h.card(card_id=card_id)}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(
        script=_plan(
            {0: JudgeSay(text=HUGE_JSON), 1: JudgeSay(text=DEEP_JSON), 2: JudgeSay(text=HUGE_LINE)},
            JudgeSay(text="Reasoning.\nSCORE: 0"),
        )
    )
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()
    assert run.state == "complete" and run.halts == []

    rows = rs.judge_rows("qrng", card_id, "study_judge")
    assert [(r.status, r.score) for r in rows] == [("unreadable", None)] * 3 + [("ok", 0.0)] * 9
    assert rows[0].raw == HUGE_JSON and rows[2].raw == HUGE_LINE, "the raw reply is kept"
    assert all(r.reader_version == rc.JUDGE_READER_VERSION for r in rows)
    unreadable = [f.sample_ref for f in rs.flags() if f.kind == "judge_unreadable"]
    assert unreadable == [r.sample_ref for r in rows[:3]]

    # A reply that was received is final: a resume asks for none of them again.
    later = StubJudge()
    again = h.orchestrator(h.reopen(rs), config, cards, StubEngine(), judge=later)
    assert again.run_judge() == {"judged": 0, "skipped": 12, "failed": 0}
    assert later.calls == []


def test_an_error_while_reading_a_received_reply_never_leaves_the_judge_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the reader does with a reply, the reply gets an ``unreadable`` row."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)

    def broken(card_id: str, text: str) -> rc.JudgeReading:
        raise RecursionError(MARK)

    monkeypatch.setattr(rc, "read_judge_score", broken)
    run = h.orchestrator(rs, config, cards, StubEngine()).run()
    # Thirty unreadable replies would be the stop rule nothing_judged; twelve are not.
    assert run.state == "complete" and run.halts == []
    rows = rs.judge_rows("qrng", h.CARD, "study_judge")
    assert [(r.status, r.score) for r in rows] == [("unreadable", None)] * 12
    assert all("SCORE:" in r.raw for r in rows) and MARK not in h.run_text(rs)
