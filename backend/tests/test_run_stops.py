"""Stop rules of the result checks, end to end on the stub engine.

Each test makes the engine, the scorer or the judge fail the way the rule is
for, and checks the same four things: the run ends ``paused`` with the right
halt stored in ``run.json``, nothing that was generated is lost, a resume (a
new orchestrator on the same run) tries the failed work again, and the run
then completes.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import run_check_helpers as h
from qr_llm_evals.api.registry import LiveRun, RunRegistry, mark_orphans
from qr_llm_evals.api.session_runtime import SessionDispatch
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.envelope import SampleContext
from qr_llm_evals.runner import (
    EVENT_TYPES,
    CheckStopped,
    ListEventSink,
    RunControl,
    RunControlError,
)
from qr_llm_evals.runner.judge_phase import PREFLIGHT_USER, judge_preflight
from qr_llm_evals.store import Run, RunStore
from qr_llm_evals.store.schema import Halt
from stub_engine import PREFLIGHT_USER as STUB_PREFLIGHT_USER
from stub_engine import JudgeSay, Reply, StubEngine, StubJudge

MARK = "UPSTREAM-TEXT-THAT-MUST-NOT-LEAK"


def _dead(body: dict[str, Any]) -> Reply:
    return Reply(status=500, error_body=MARK)


def _stopped(run: Run, rule: str, *, scope: str, scope_id: str | None, phase: str) -> Halt:
    """The one open halt of a run a check stopped."""
    assert run.state == "paused"
    (halt,) = run.open_halts()
    assert halt is run.halts[-1]
    assert (halt.rule, halt.scope, halt.scope_id, halt.phase) == (rule, scope, scope_id, phase)
    assert halt.at == h.NOW and halt.resumed_at is None
    assert halt.recovery == rc.STOP_RULES[rule].recovery
    assert halt.sentence and "{" not in halt.sentence
    return halt


def _resume(
    rs: RunStore, config: Any, cards: Any, engine: Any = None, **kwargs: Any
) -> tuple[Run, Any]:
    """What the host's Resume does: a new orchestrator on the stored run."""
    engine = engine or StubEngine()
    orch = h.orchestrator(h.reopen(rs), config, cards, engine, **kwargs)
    return orch.run(), engine


def _judged(rs: RunStore, arm: str = "qrng", card_id: str = h.CARD) -> dict[str, Any]:
    """The last judge row of each sample."""
    return {row.sample_ref: row for row in rs.judge_rows(arm, card_id, "study_judge")}


class _BySample:
    """A judge script that answers the n-th distinct sample (by its text) as ``plan(n)`` says."""

    def __init__(
        self, plan: Callable[[int], JudgeSay | None], preflight: JudgeSay | None = None
    ) -> None:
        self.plan = plan
        self.preflight = preflight
        self.seen: dict[str, int] = {}

    def __call__(self, body: dict[str, Any]) -> JudgeSay | None:
        user = str(body["messages"][0]["content"])
        if user == PREFLIGHT_USER:
            return self.preflight
        return self.plan(self.seen.setdefault(user, len(self.seen)))


def test_the_stub_recognises_the_request_made_before_the_first_generation() -> None:
    assert STUB_PREFLIGHT_USER == PREFLIGHT_USER
    assert "check.stopped" in EVENT_TYPES


# ── generation_errors ────────────────────────────────────────────────────────


def test_a_dead_engine_stops_the_run_and_resume_tries_every_failed_sample_again(
    tmp_path: Path,
) -> None:
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge()
    run = h.orchestrator(rs, config, cards, StubEngine(script=_dead), judge=judge).run()

    # Before, the run walked its whole queue into error rows and ended `complete`,
    # which cannot be resumed.
    assert run.state == "paused"
    (halt,) = run.open_halts()  # whichever arm reached eight first
    assert (halt.rule, halt.scope, halt.phase) == ("generation_errors", "arm", "generation")
    assert halt.scope_id in ("qrng", "prng_seed_7")
    assert halt.counts == {"in_a_row": 8} and halt.causes == {"HTTP 500": 8}
    assert len(halt.sample_refs) == 3 and all(
        ref.startswith(f"{halt.scope_id}/") for ref in halt.sample_refs
    )
    assert halt.sentence == (
        f"8 generations in a row on arm {halt.scope_id} ended in an error before a reply "
        "was complete."
    )
    assert halt.recovery == rc.STOP_RULES["generation_errors"].recovery
    assert run.checks_version == rc.CHECKS_VERSION
    assert set(h.finishes(rs, config)) == {"error"} and judge.calls == []
    assert len(rs.generations(halt.scope_id, h.CARD)) == 8, "the arm stopped at the eighth"
    # The upstream body is kept in the taint, as before. It reaches no flag and no halt.
    assert any(MARK in taint.detail for taint in rs.taints())
    assert MARK not in h.run_text(rs) and h.JUDGE_KEY not in h.run_text(rs)

    resumed, _ = _resume(rs, config, cards)
    assert resumed.state == "complete"
    assert [(x.rule, x.resumed_at) for x in resumed.halts] == [("generation_errors", h.NOW)]
    assert h.never_completed(rs, config) == []
    for arm in h.arm_ids(config):
        assert len(rs.completed_refs(arm, h.CARD)) == 12
        assert len([r for r in _judged(rs, arm).values() if r.status == "ok"]) == 12


def test_a_second_outage_after_a_resume_stops_the_run_again(tmp_path: Path) -> None:
    """A resume starts the counts again; it does not switch the rule off for the run."""
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    assert h.orchestrator(rs, config, cards, StubEngine(script=_dead)).run().state == "paused"

    later = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    again, _ = _resume(rs, config, cards, StubEngine(script=_dead), clock=lambda: later)
    assert again.state == "paused"
    assert [(x.rule, x.at, x.resumed_at) for x in again.halts] == [
        ("generation_errors", h.NOW, later),
        ("generation_errors", later, None),
    ]
    assert again.halts[1].counts == {"in_a_row": 8}

    done, _ = _resume(rs, config, cards)
    assert done.state == "complete" and all(x.resumed_at is not None for x in done.halts)
    assert h.never_completed(rs, config) == []


def test_one_dead_arm_among_five_is_seen(tmp_path: Path) -> None:
    """Five arms share three slots, so a dead arm's failures never come eight in a row
    in the run's order. Counted per arm they do."""
    config, cards = h.study(seeded=4), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    engine = StubEngine(
        script=lambda body: _dead(body) if str(body["request_id"]).startswith("qrng/") else None
    )
    run = h.orchestrator(rs, config, cards, engine, max_inflight=3).run()

    halt = _stopped(run, "generation_errors", scope="arm", scope_id="qrng", phase="generation")
    assert halt.counts == {"in_a_row": 8}
    # The dead arm used to burn its whole queue (twelve here) while the run went on to `complete`.
    dead = rs.generations("qrng", h.CARD)
    assert len(dead) == 8 and all(row.finish_reason == "error" for row in dead)
    healthy = [r for arm in h.arm_ids(config)[1:] for r in rs.generations(arm, h.CARD)]
    assert healthy and all(row.finish_reason == "stop" for row in healthy)

    resumed, engine2 = _resume(rs, config, cards, max_inflight=3)
    assert resumed.state == "complete"
    assert all(len(rs.completed_refs(arm, h.CARD)) == 12 for arm in h.arm_ids(config))
    assert not ({r.sample_ref for r in healthy} & set(engine2.request_ids)), "nothing regenerated"


def test_failures_spread_over_the_run_trip_the_share_clause(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    two_in_three = StubEngine(
        script=lambda body: (
            None if str(body["request_id"]).endswith("told_nothing") else _dead(body)
        )
    )
    run = h.orchestrator(rs, config, cards, two_in_three).run()
    halt = _stopped(run, "generation_errors", scope="run", scope_id=None, phase="generation")
    assert halt.counts["of_last"] == 20 and halt.counts["failed"] >= 10
    assert sum(h.finishes(rs, config).values()) == 20, "checked from the twentieth generation on"
    assert _resume(rs, config, cards)[0].state == "complete"


# ── entropy_degraded ─────────────────────────────────────────────────────────


def test_ten_quantum_samples_in_a_row_tainted_for_degraded_entropy_stop_the_run(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    every = {job.sample_ref for job in orch.plan_arm(config.arms[0])}
    orch = h.orchestrator(rs, config, cards, StubEngine(degrade_refs=every))
    run = orch.run()

    halt = _stopped(run, "entropy_degraded", scope="arm", scope_id="qrng", phase="generation")
    assert halt.counts == {"in_a_row": 10} and halt.causes == {"qrng_degraded": 10}
    assert "tainted" in halt.sentence
    tainted = rs.completed_refs("qrng", h.CARD)
    assert len(tainted) == 10 and h.taint_kinds(rs) == {"qrng_degraded": 10}

    resumed, engine2 = _resume(rs, config, cards)
    assert resumed.state == "complete"
    # The tainted samples are complete: they stay, excluded by their taint, and are not redrawn.
    assert not (tainted & set(engine2.request_ids)) and len(engine2.request_ids) == 2


@pytest.mark.parametrize("health", ["degraded", "unreachable"])
def test_three_samples_that_start_on_a_degraded_source_stop_the_run(
    tmp_path: Path, health: str
) -> None:
    """The probe before the request already says so: no need to pay for ten whole samples."""

    class Sick(StubEngine):
        def _health(self) -> httpx.Response:
            if health == "unreachable":
                return httpx.Response(503)
            body = {"rpc_ok": True, "currently_degraded": True, "fallback_count": 0}
            return httpx.Response(200, json=body)

    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    run = h.orchestrator(rs, config, cards, Sick()).run()
    halt = _stopped(run, "entropy_degraded", scope="arm", scope_id="qrng", phase="generation")
    assert halt.counts == {"in_a_row": 3} and "entropy check" in halt.sentence
    assert len(rs.generations("qrng", h.CARD)) == 3
    assert _resume(rs, config, cards)[0].state == "complete"


# ── tier0_failed ─────────────────────────────────────────────────────────────


def test_ten_samples_in_a_row_without_token_records_stop_the_run(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path / "data", config)
    log_dir = tmp_path / "token-log"
    silent = StubEngine()
    h.engine_writes_logs(silent, log_dir, lambda ref: None)
    kwargs: dict[str, Any] = {"runner_config": h.log_config(log_dir), "tier0_source": "engine_log"}
    run = h.orchestrator(rs, config, cards, silent, **kwargs).run()

    halt = _stopped(run, "tier0_failed", scope="arm", scope_id="qrng", phase="generation")
    assert halt.counts == {"in_a_row": 10} and halt.causes == {"tier0_missing": 10}
    flagged = rs.completed_refs("qrng", h.CARD)
    assert len(flagged) == 10 and h.flag_kinds(rs) == {"tier0_missing": 10}
    assert rs.taints() == [], "a missing log is a flag: the replies and scores stand"

    logging = StubEngine()
    h.engine_writes_logs(logging, log_dir)
    resumed, _ = _resume(rs, config, cards, logging, **kwargs)
    assert resumed.state == "complete"
    assert not (flagged & set(logging.request_ids)), "completed samples are not generated again"
    assert len({str(r["sample_ref"]) for r in rs.iter_tier0_rows("qrng", h.CARD)}) == 2


# ── scorer_errors ────────────────────────────────────────────────────────────


def test_a_scorer_that_fails_ten_times_in_a_row_stops_the_run(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)

    def broken(sample: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(MARK)

    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(orch, cards[h.CARD], score=broken)
    run = orch.run()

    halt = _stopped(run, "scorer_errors", scope="card", scope_id=h.CARD, phase="generation")
    assert halt.counts == {"in_a_row": 10} and halt.causes == {"ValueError": 10}
    assert MARK not in h.run_text(rs)
    assert h.finishes(rs, config) == {"stop": 10} and h.flag_kinds(rs) == {"score_error": 10}

    resumed, _ = _resume(rs, config, cards)
    assert resumed.state == "complete"
    errors = [
        row
        for arm in h.arm_ids(config)
        for row in rs.score_rows(arm, h.CARD, "task")
        if "error" in row
    ]
    assert len(errors) == 10, "a stored reply is not scored again"


# ── empty_outputs ────────────────────────────────────────────────────────────


def _empty(body: dict[str, Any]) -> Reply:
    return Reply(text="")


def test_empty_replies_from_every_arm_stop_the_card_once(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card(items=3)}
    rs = h.run_store(tmp_path, config)
    run = h.orchestrator(rs, config, cards, StubEngine(script=_empty)).run()

    halt = _stopped(run, "empty_outputs", scope="card", scope_id=h.CARD, phase="generation")
    assert halt.counts["in_a_row"] >= 20
    assert halt.counts["arms"] == 2 and halt.counts["prompts"] == 3
    stored = sum(h.finishes(rs, config).values())
    assert stored == halt.counts["in_a_row"] and h.flag_kinds(rs) == {"empty_output": stored}
    assert rs.taints() == [], "an empty reply is a completed sample, not an excluded one"

    # The rule looked at this card once. A resume goes on even though replies are still empty.
    resumed, _ = _resume(rs, config, cards, StubEngine(script=_empty))
    assert resumed.state == "complete" and len(resumed.halts) == 1
    assert h.finishes(rs, config) == {"stop": 36}


def test_empty_replies_from_one_arm_only_never_stop_the_run(tmp_path: Path) -> None:
    """A stop that reads replies needs the same thing from more than one arm."""
    config, cards = h.study(), {h.CARD: h.card(items=3)}
    rs = h.run_store(tmp_path, config)
    one_arm = StubEngine(
        script=lambda body: _empty(body) if str(body["request_id"]).startswith("qrng/") else None
    )
    run = h.orchestrator(rs, config, cards, one_arm).run()
    assert run.state == "complete" and run.halts == []
    assert h.flag_kinds(rs) == {"empty_output": 18}


def test_replies_that_spent_their_budget_reasoning_do_not_count_as_empty(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card(items=3)}
    rs = h.run_store(tmp_path, config)
    thinking = StubEngine(
        script=lambda body: Reply(text="", thinking="working through it", finish="length")
    )
    run = h.orchestrator(rs, config, cards, thinking).run()
    assert run.state == "complete" and run.halts == []
    assert h.flag_kinds(rs) == {"empty_output": 36, "truncated": 36}


# ── nothing_parsed ───────────────────────────────────────────────────────────


def _unparsed(sample: dict[str, Any]) -> dict[str, Any]:
    return {"answer_value": None, "answer_valid": 0}


def test_a_card_whose_first_thirty_samples_all_fail_extraction_stops(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card(items=3, judged=False)}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(orch, cards[h.CARD], score=_unparsed)
    run = orch.run()

    halt = _stopped(run, "nothing_parsed", scope="card", scope_id=h.CARD, phase="generation")
    assert halt.counts["scored"] == 30 and halt.counts["prompts"] >= 2
    assert h.finishes(rs, config) == {"stop": 30} and h.flag_kinds(rs) == {"unparsed": 30}

    again = h.orchestrator(h.reopen(rs), config, cards, StubEngine())
    h.with_task(again, cards[h.CARD], score=_unparsed)
    resumed = again.run()
    assert resumed.state == "complete" and len(resumed.halts) == 1, "not looked at again"
    assert h.finishes(rs, config) == {"stop": 36}


def test_a_card_whose_first_prompt_alone_fills_thirty_samples_is_still_stopped(
    tmp_path: Path,
) -> None:
    """Thirty samples on the first prompt used to switch the rule off for the card.

    The runner generates every repetition of a prompt before the next prompt,
    so this is the shape of nearly every real study. All the samples were
    generated, none parsed, and the run ended complete.
    """
    config, cards = h.study(seeded=0), {h.CARD: h.card(items=2, k=10, judged=False)}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(orch, cards[h.CARD], score=_unparsed)
    run = orch.run()

    halt = _stopped(run, "nothing_parsed", scope="card", scope_id=h.CARD, phase="generation")
    assert halt.counts == {"scored": 31, "prompts": 2}
    prompts = Counter(row.prompt_id for row in rs.generations("qrng", h.CARD))
    assert sorted(prompts.values()) == [1, 30], "thirty on the first prompt, one on the second"

    again = h.orchestrator(h.reopen(rs), config, cards, StubEngine())
    h.with_task(again, cards[h.CARD], score=_unparsed)
    resumed = again.run()
    assert resumed.state == "complete" and len(resumed.halts) == 1, "not looked at again"
    assert h.finishes(rs, config) == {"stop": 60}


def test_one_sample_that_parses_on_the_first_prompt_ends_the_check_for_the_card(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card(items=2, k=10, judged=False)}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    seen: list[str] = []

    def one_parses(sample: dict[str, Any]) -> dict[str, Any]:
        seen.append(str(sample["sample_ref"]))
        return {"answer_value": 1.0, "answer_valid": 1} if len(seen) == 12 else _unparsed(sample)

    h.with_task(orch, cards[h.CARD], score=one_parses)
    run = orch.run()
    assert run.state == "complete" and run.halts == []
    assert h.flag_kinds(rs) == {"unparsed": 59}


def test_a_window_past_thirty_is_counted_from_stored_rows_across_a_restart(tmp_path: Path) -> None:
    """Thirty-six samples on the first prompt, a kill after thirty-three, then a new process."""
    config, cards = h.study(seeded=0), {h.CARD: h.card(items=2, k=12, judged=False)}
    rs = h.run_store(tmp_path, config)
    control = RunControl()
    engine = StubEngine()
    engine.on_request = lambda body: control.abort("killed") if len(engine.requests) == 34 else None
    first = h.orchestrator(rs, config, cards, engine, control=control)
    h.with_task(first, cards[h.CARD], score=_unparsed)
    assert first.run().state == "aborted"
    assert h.finishes(rs, config)["stop"] == 33

    second = h.orchestrator(h.reopen(rs), config, cards, StubEngine())
    h.with_task(second, cards[h.CARD], score=_unparsed)
    run = second.run()
    halt = _stopped(run, "nothing_parsed", scope="card", scope_id=h.CARD, phase="generation")
    # All thirty-three stored samples count, not the first thirty of them only.
    assert halt.counts == {"scored": 37, "prompts": 2}
    assert h.finishes(rs, config)["stop"] == 37

    # A parsed sample among the stored rows, past the thirtieth, settles the card after a restart.
    other = h.run_store(tmp_path / "settled", config)
    killer = RunControl()
    engine3 = StubEngine()
    engine3.on_request = lambda body: (
        killer.abort("killed") if len(engine3.requests) == 34 else None
    )
    third = h.orchestrator(other, config, cards, engine3, control=killer)
    count = 0

    def thirty_second_parses(sample: dict[str, Any]) -> dict[str, Any]:
        nonlocal count
        count += 1
        return {"answer_value": 1.0, "answer_valid": 1} if count == 32 else _unparsed(sample)

    h.with_task(third, cards[h.CARD], score=thirty_second_parses)
    assert third.run().state == "aborted"
    fourth = h.orchestrator(h.reopen(other), config, cards, StubEngine())
    h.with_task(fourth, cards[h.CARD], score=_unparsed)
    settled = fourth.run()
    assert settled.state == "complete" and settled.halts == []


def test_a_judged_card_is_not_stopped_for_what_its_scorer_extracts(tmp_path: Path) -> None:
    """On a judged card the analysed value comes from the judge."""
    config, cards = h.study(), {h.CARD: h.card(items=3)}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(orch, cards[h.CARD], score=_unparsed)
    run = orch.run()
    assert run.state == "complete" and run.halts == []
    assert h.flag_kinds(rs) == {"unparsed": 36}


def test_a_short_card_that_parsed_nothing_stops_when_its_queue_ends(tmp_path: Path) -> None:
    """Twenty-four samples never fill the window of thirty; the verdict comes at the end.

    The rule fires when the last sample of the queue finishes, so no loop is
    left to notice a pause. The run still ends paused.
    """
    config, cards = h.study(), {h.CARD: h.card(judged=False)}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(orch, cards[h.CARD], score=_unparsed)
    run = orch.run()

    halt = _stopped(run, "nothing_parsed", scope="card", scope_id=h.CARD, phase="generation")
    assert halt.counts == {"scored": 24, "prompts": 2}
    assert "end of its queue" in halt.sentence
    assert h.finishes(rs, config) == {"stop": 24}

    resumed, engine2 = _resume(rs, config, cards)
    assert resumed.state == "complete" and len(resumed.halts) == 1
    assert engine2.request_ids == [], "every sample was already complete"


def test_the_first_thirty_are_counted_from_stored_rows_across_a_restart(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card(items=3, judged=False)}
    rs = h.run_store(tmp_path, config)
    control = RunControl()
    engine = StubEngine()
    engine.on_request = lambda body: control.abort("killed") if len(engine.requests) == 16 else None
    first = h.orchestrator(rs, config, cards, engine, control=control)
    h.with_task(first, cards[h.CARD], score=_unparsed)
    assert first.run().state == "aborted"
    before = h.finishes(rs, config)["stop"]
    assert 10 <= before < 30

    second = h.orchestrator(h.reopen(rs), config, cards, StubEngine())
    h.with_task(second, cards[h.CARD], score=_unparsed)
    run = second.run()
    halt = run.open_halts()[0]
    assert (halt.rule, halt.counts["scored"]) == ("nothing_parsed", 30)
    # The window is the card's first thirty, not thirty more after the restart.
    assert h.finishes(rs, config)["stop"] == 30


# ── unfinished ───────────────────────────────────────────────────────────────


def test_a_small_run_in_which_every_generation_failed_does_not_end_complete(
    tmp_path: Path,
) -> None:
    """Three samples can never make eight failures in a row."""
    config, cards = h.study(seeded=0), {h.CARD: h.card(items=1, k=1)}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge()
    run = h.orchestrator(rs, config, cards, StubEngine(script=_dead), judge=judge).run()

    halt = _stopped(run, "unfinished", scope="phase", scope_id=None, phase="generation")
    assert halt.counts == {"missing": 3, "planned": 3, "cells": 1}
    assert halt.causes == {f"qrng/{h.CARD}": 3} and len(halt.sample_refs) == 3
    assert judge.calls == [], "the stop is between the phases: judging has not begun"

    resumed, _ = _resume(rs, config, cards)
    assert resumed.state == "complete" and h.never_completed(rs, config) == []


def test_errors_below_every_streak_are_not_left_behind(tmp_path: Path) -> None:
    """Eight requests fail while the engine restarts, four on each arm.

    No streak rule fires. The run used to go on to the judge and end
    `complete` with those eight samples missing for good.
    """
    calls = 0

    def restarting(body: dict[str, Any]) -> Reply | None:
        nonlocal calls
        calls += 1
        return _dead(body) if calls <= 8 else None

    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge()
    run = h.orchestrator(rs, config, cards, StubEngine(script=restarting), judge=judge).run()

    halt = _stopped(run, "unfinished", scope="phase", scope_id=None, phase="generation")
    assert halt.counts == {"missing": 8, "planned": 24, "cells": 2}
    assert len(h.never_completed(rs, config)) == 8 and judge.calls == []

    resumed, engine2 = _resume(rs, config, cards)
    assert resumed.state == "complete"
    assert h.never_completed(rs, config) == [] and len(engine2.request_ids) == 8


def test_unfinished_stops_generation_once_then_lets_the_run_go_on(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    refs = [job.sample_ref for job in orch.plan_arm(config.arms[0])]
    lost = set(refs[::4])  # every fourth: three of twelve, never two in a row
    run = h.orchestrator(rs, config, cards, StubEngine(fail_refs=lost)).run()
    assert _stopped(run, "unfinished", scope="phase", scope_id=None, phase="generation").counts == {
        "missing": 3,
        "planned": 12,
        "cells": 1,
    }

    # Still failing after the resume: the run goes on without them, as the halt's text says.
    resumed, _ = _resume(rs, config, cards, StubEngine(fail_refs=lost))
    assert resumed.state == "complete" and len(resumed.halts) == 1
    assert set(h.never_completed(rs, config)) == lost
    assert len(_judged(rs)) == 9


def _filtered(body: dict[str, Any]) -> Reply:
    return Reply(finish="content_filter")


def test_replies_the_provider_ended_for_its_own_reason_are_not_missing_samples(
    tmp_path: Path,
) -> None:
    """A finish reason the harness does not map is not a failed generation for the stop rules.

    Four of twelve replies end on a content filter. They used to count as
    missing: the run paused with `unfinished` and the resume drew all four
    again, so how a reply ended selected which samples got a second draw.
    """
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    refs = [job.sample_ref for job in orch.plan_arm(config.arms[0])]
    filtered = set(refs[::3])
    engine = StubEngine(
        script=lambda body: _filtered(body) if str(body["request_id"]) in filtered else None
    )
    judge = StubJudge()
    run = h.orchestrator(rs, config, cards, engine, judge=judge).run()

    assert run.state == "complete" and run.halts == []
    assert h.finishes(rs, config) == {"stop": 8, "error": 4}
    assert rs.taints() == [], "no taint: the stream did not fail"
    assert sorted(engine.request_ids) == sorted(refs), "each sample was requested once"
    assert len(judge.calls) == 8

    # An arm whose every reply ends that way is not stopped for it either.
    all_filtered = h.run_store(tmp_path / "all", config)
    ended = h.orchestrator(all_filtered, config, cards, StubEngine(script=_filtered)).run()
    assert ended.state == "complete" and ended.halts == []


def test_failed_generations_beside_such_replies_are_still_missing_samples(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    refs = [job.sample_ref for job in orch.plan_arm(config.arms[0])]
    filtered, dead = {refs[1], refs[6]}, {refs[0], refs[4], refs[8]}

    def script(body: dict[str, Any]) -> Reply | None:
        ref = str(body["request_id"])
        return _filtered(body) if ref in filtered else _dead(body) if ref in dead else None

    run = h.orchestrator(rs, config, cards, StubEngine(script=script)).run()
    halt = _stopped(run, "unfinished", scope="phase", scope_id=None, phase="generation")
    # The two filtered replies are neither planned samples that are missing nor named in the halt.
    assert halt.counts == {"missing": 3, "planned": 10, "cells": 1}
    assert set(halt.sample_refs) == dead


# ── the judge: preflight and judge_rejected ──────────────────────────────────


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_judge_that_refuses_the_request_before_the_first_generation_stops_the_run(
    tmp_path: Path, status: int
) -> None:
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    engine = StubEngine()
    judge = StubJudge(script=lambda body: JudgeSay(status=status, error_body=MARK))
    run = h.orchestrator(rs, config, cards, engine, judge=judge).run()

    halt = _stopped(run, "judge_rejected", scope="run", scope_id=None, phase="preflight")
    assert halt.counts == {"status": status} and halt.causes == {f"HTTP {status}": 1}
    assert engine.requests == [], "nothing was generated for a judge that cannot be used"
    assert len(judge.preflights) == 1 and judge.calls == []
    text = h.run_text(rs)
    assert MARK not in text and h.JUDGE_KEY not in text

    # The host supplies a key that works and resumes: the new run uses the key of now.
    working = StubJudge()
    resumed, _ = _resume(
        rs, config, cards, judge=working, secrets=SecretBag({"anthropic": "sk-ant-new-key"})
    )
    assert resumed.state == "complete" and resumed.halts[0].resumed_at == h.NOW
    assert set(working.keys) == {"sk-ant-new-key"} and len(working.preflights) == 1
    assert len(working.calls) == 24


def test_a_judge_that_is_down_before_the_first_generation_does_not_stop_the_run(
    tmp_path: Path,
) -> None:
    """A 500 is not a refusal of the key, the model or the settings."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(script=_BySample(lambda n: None, preflight=JudgeSay(status=500)))
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()
    assert run.state == "complete" and run.halts == []


class _OddPreflight:
    """A judge that answers the request before the first generation in a way nobody expects."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        self.answer = answer
        self.judge = StubJudge()
        self.preflights = 0

    @property
    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if PREFLIGHT_USER.encode("utf-8") in request.content:
                self.preflights += 1
                return self.answer(request)
            return self.judge.handle(request)

        return httpx.MockTransport(handle)


def _a_list(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[1, 2])


def _not_an_httpx_error(request: httpx.Request) -> httpx.Response:
    raise RuntimeError(MARK)


def _a_page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>Service unavailable</html>")


def _a_redirect(request: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"location": "https://example.invalid/login"})


def _a_refused_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    "answer",
    [_a_list, _not_an_httpx_error, _a_page, _a_redirect, _a_refused_connection],
)
def test_an_answer_before_the_first_generation_that_is_not_a_refusal_lets_the_run_start(
    tmp_path: Path, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Only a status of 400, 401, 403 or 404 stops a run there.

    A 200 whose body is a JSON list, and an error from the transport that is
    not an httpx error, used to leave `run_generation` as an exception: the
    run stayed `running` with no thread, no halt and nothing generated.
    """
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = _OddPreflight(answer)
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()

    assert judge.preflights >= 1
    assert run.state == "complete" and run.halts == []
    assert h.finishes(rs, config) == {"stop": 12} and len(judge.judge.calls) == 12
    assert rs.read_run().state == "complete" and MARK not in h.run_text(rs)


def test_the_request_before_the_first_generation_answers_a_status_or_nothing() -> None:
    def ask(answer: Callable[[httpx.Request], httpx.Response]) -> int | None:
        return judge_preflight(
            h.study().judge,
            SecretBag({"anthropic": h.JUDGE_KEY}),
            run_id="run1",
            transport=httpx.MockTransport(answer),
        )

    assert [ask(lambda request, s=s: httpx.Response(s, json={})) for s in (400, 401, 403, 404)] == [
        400,
        401,
        403,
        404,
    ]
    for answer in (_a_list, _not_an_httpx_error, _a_page, _a_refused_connection):
        assert ask(answer) is None
    # Only the four statuses above stop a run; the Breaker lets every other answer through.
    for status in (ask(_a_redirect), ask(lambda request: httpx.Response(500, json={})), None):
        assert rc.Breaker().judge_preflight(status) is None


def test_without_a_judge_key_no_request_is_made_before_the_first_generation(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge()
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge, secrets=SecretBag()).run()
    assert run.state == "needs_keys" and judge.preflights == [] and judge.calls == []


def test_a_judge_that_refuses_during_judging_stops_the_run(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(script=_BySample(lambda n: JudgeSay(status=403, error_body=MARK)))
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()

    halt = _stopped(run, "judge_rejected", scope="run", scope_id=None, phase="judge")
    assert halt.counts == {"status": 403} and len(halt.sample_refs) == 1
    assert len(judge.calls) == 1, "the first refusal is enough"
    assert h.finishes(rs, config) == {"stop": 12}
    assert [r.status for r in rs.judge_rows("qrng", h.CARD, "study_judge")] == ["failed"]
    assert MARK not in h.run_text(rs)

    working = StubJudge()
    resumed, engine2 = _resume(rs, config, cards, judge=working)
    assert resumed.state == "complete" and engine2.request_ids == []
    assert len(working.calls) == 12
    assert all(row.status == "ok" for row in _judged(rs).values())


def test_a_400_for_one_judgement_is_a_failed_judgement_not_a_refusal(tmp_path: Path) -> None:
    """A 400 can be about one over-long sample; only before the first generation is it the settings."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(script=_BySample(lambda n: JudgeSay(status=400) if n == 5 else None))
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()
    assert run.state == "complete" and run.halts == []
    assert [(f.kind, f.phase, f.detail) for f in rs.flags()] == [
        ("judge_failed", "judge", "HTTP 400")
    ]


# ── judge_unusable, nothing_judged, judge_phase_error, unfinished ────────────


def test_a_judge_that_gives_no_reply_ten_times_in_a_row_stops_the_run(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(script=_BySample(lambda n: JudgeSay(status=500, error_body=MARK)))
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()

    halt = _stopped(run, "judge_unusable", scope="run", scope_id=None, phase="judge")
    assert halt.counts == {"in_a_row": 10} and halt.causes == {"HTTP 500": 10}
    rows = rs.judge_rows("qrng", h.CARD, "study_judge")
    assert [r.status for r in rows] == ["failed"] * 10
    assert h.flag_kinds(rs) == {"judge_failed": 10} and MARK not in h.run_text(rs)

    working = StubJudge()
    resumed, _ = _resume(rs, config, cards, judge=working)
    assert resumed.state == "complete"
    assert len(working.calls) == 12, "the ten without a reply and the two never asked"
    assert sum(1 for row in _judged(rs).values() if row.status == "ok") == 12


def test_a_judge_that_fails_two_calls_in_three_trips_the_share_clause(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card(k=4)}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge(script=_BySample(lambda n: None if n % 3 == 2 else JudgeSay(status=500)))
    run = h.orchestrator(rs, config, cards, StubEngine(), judge=judge).run()
    halt = _stopped(run, "judge_unusable", scope="run", scope_id=None, phase="judge")
    assert halt.counts == {"failed": 14, "of_last": 20}
    assert _resume(rs, config, cards)[0].state == "complete"


def test_a_card_whose_first_thirty_judge_replies_cannot_be_read_stops_once(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card(items=3, k=4)}
    rs = h.run_store(tmp_path, config)

    def prose(body: dict[str, Any]) -> JudgeSay | None:
        if body["messages"][0]["content"] == PREFLIGHT_USER:
            return None
        return JudgeSay(text="I would say this is a 3 out of 10.")

    run = h.orchestrator(rs, config, cards, StubEngine(), judge=StubJudge(script=prose)).run()
    halt = _stopped(run, "nothing_judged", scope="card", scope_id=h.CARD, phase="judge")
    assert halt.counts == {"replies": 30}
    rows = rs.judge_rows("qrng", h.CARD, "study_judge")
    assert [(r.status, r.score) for r in rows] == [("unreadable", None)] * 30
    assert all("3 out of 10" in r.raw for r in rows), "the reply is kept"

    # A reply that was received is final: only the six never asked are asked now.
    still_prose = StubJudge(script=prose)
    resumed, _ = _resume(rs, config, cards, judge=still_prose)
    assert len(still_prose.calls) == 6
    assert resumed.state == "complete" and len(resumed.halts) == 1, "not looked at again"


def test_an_error_in_the_judge_phase_is_a_stored_stop_every_time(tmp_path: Path) -> None:
    """A missing rubric file used to leave the run `running` with no thread behind it."""
    base = h.card()
    gone = base.model_copy(
        update={"judge": base.judge.model_copy(update={"rubric": "evals/stub_free/gone.md"})}
    )
    config, cards = h.study(seeded=0), {h.CARD: gone}
    rs = h.run_store(tmp_path, config)
    run = h.orchestrator(rs, config, cards, StubEngine()).run()

    halt = _stopped(run, "judge_phase_error", scope="run", scope_id=None, phase="judge")
    assert halt.causes == {"FileNotFoundError": 1} and "gone.md" not in h.run_text(rs)
    assert h.finishes(rs, config) == {"stop": 12}

    again, _ = _resume(rs, config, cards)
    assert again.state == "paused", "this check is never switched off"
    assert [x.rule for x in again.halts] == ["judge_phase_error"] * 2
    assert again.halts[0].resumed_at is not None and again.halts[1].resumed_at is None

    fixed, _ = _resume(rs, config, {h.CARD: base})
    assert fixed.state == "complete" and len(_judged(rs)) == 12


def test_judgements_without_a_reply_stop_the_run_once_at_the_end_of_judging(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)

    def every_fourth() -> StubJudge:
        return StubJudge(script=_BySample(lambda n: JudgeSay(status=500) if n % 4 == 0 else None))

    run = h.orchestrator(rs, config, cards, StubEngine(), judge=every_fourth()).run()
    halt = _stopped(run, "unfinished", scope="phase", scope_id=None, phase="judge")
    assert halt.counts == {"missing": 3, "planned": 12, "cells": 1}
    assert len(halt.sample_refs) == 3

    failing_again = StubJudge(script=_BySample(lambda n: JudgeSay(status=500)))
    resumed, _ = _resume(rs, config, cards, judge=failing_again)
    assert len(failing_again.calls) == 3 * 3, "only the three without a reply, with retries"
    assert resumed.state == "complete" and len(resumed.halts) == 1
    assert sum(1 for row in _judged(rs).values() if row.status == "failed") == 3


# ── Inspect cards ────────────────────────────────────────────────────────────


def _inspect_card() -> Any:
    real = load_cards()["novelty_bench"]
    return real.model_copy(
        update={
            "k": 2,
            "task_args": {**real.task_args, "num_generations": 2},
            "dataset": real.dataset.model_copy(update={"items": 1}),
        }
    )


def _inspect_executor(runner: Any, arm: Any, selected: Any) -> None:
    for epoch in range(2):
        runner.dispatch_inspect(
            arm,
            selected,
            SampleContext(selected.id, "p1", epoch, selected.max_tokens),
            [{"role": "user", "content": "hi"}],
        )


def test_an_inspect_sample_that_does_not_complete_pauses_the_run(tmp_path: Path) -> None:
    card = _inspect_card()
    config, cards = h.study(card.id), {card.id: card}
    rs = h.run_store(tmp_path, config)
    bad = f"prng_seed_7/{card.id}/p1/1"
    orch = h.orchestrator(
        rs, config, cards, StubEngine(fail_refs={bad}), inspect_executor=_inspect_executor
    )
    run = orch.run()  # used to raise, with the run aborted for good

    halt = _stopped(
        run, "generation_errors", scope="arm", scope_id="prng_seed_7", phase="generation"
    )
    assert halt.counts == {"failed": 1} and halt.causes == {"HTTP 500": 1}
    assert halt.sample_refs == [bad] and "Inspect" in halt.sentence
    kept = rs.completed_refs("qrng", card.id) | rs.completed_refs("prng_seed_7", card.id)
    assert len(kept) == 3

    engine2 = StubEngine()
    resumed = h.orchestrator(
        h.reopen(rs), config, cards, engine2, inspect_executor=_inspect_executor
    ).run()
    assert resumed.state == "complete" and engine2.request_ids == [bad]


def test_a_stop_reaches_the_inspect_bridge_as_an_error_that_closes_its_child(
    tmp_path: Path,
) -> None:
    card = _inspect_card()
    config, cards = h.study(card.id, seeded=0), {card.id: card}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine(script=_dead))
    ctx = SampleContext(card.id, "p1", 0, card.max_tokens)
    with pytest.raises(CheckStopped, match="did not complete"):
        orch.dispatch_inspect(config.arms[0], card, ctx, [{"role": "user", "content": "hi"}])
    orch._note_trip(orch._breaker.inspect_incomplete("qrng", "qrng/x/p1/0", "HTTP 500"))
    with pytest.raises(CheckStopped, match="stopped the run"):
        orch.dispatch_inspect(config.arms[0], card, ctx, [{"role": "user", "content": "hi"}])


def test_an_inspect_failure_that_is_not_about_a_sample_still_aborts(tmp_path: Path) -> None:
    card = _inspect_card()
    config, cards = h.study(card.id), {card.id: card}
    rs = h.run_store(tmp_path, config)

    def broken(runner: Any, arm: Any, selected: Any) -> None:
        raise RuntimeError("the child could not start")

    orch = h.orchestrator(rs, config, cards, StubEngine(), inspect_executor=broken)
    with pytest.raises(RuntimeError, match="could not start"):
        orch.run()
    assert rs.read_run().state == "aborted" and rs.read_run().halts == []


# ── mechanics of a stop ──────────────────────────────────────────────────────


def _broken_after(n_good: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    seen = 0
    lock = threading.Lock()

    def score(sample: dict[str, Any]) -> dict[str, Any]:
        nonlocal seen
        with lock:
            seen += 1
            if seen > n_good:
                raise RuntimeError("scorer broke")
        return {"n_words": 1}

    return score


def test_a_rule_that_fires_on_the_last_sample_of_the_queue_still_pauses_the_run(
    tmp_path: Path,
) -> None:
    """No loop is left to notice it. The run used to go on to the judge and end `complete`."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    judge = StubJudge()
    orch = h.orchestrator(rs, config, cards, StubEngine(), judge=judge)
    h.with_task(orch, cards[h.CARD], score=_broken_after(2))
    run = orch.run()

    _stopped(run, "scorer_errors", scope="card", scope_id=h.CARD, phase="generation")
    assert h.finishes(rs, config) == {"stop": 12}, "the queue was empty when the rule fired"
    assert judge.calls == [], "no judgement was made past the stop"

    working = StubJudge()
    resumed, engine2 = _resume(rs, config, cards, judge=working)
    assert resumed.state == "complete" and engine2.request_ids == [] and len(working.calls) == 12


@pytest.mark.parametrize("paused_by", ["host", "quota"])
def test_a_check_stop_during_a_host_or_quota_pause_ends_the_run_thread(
    tmp_path: Path, paused_by: str
) -> None:
    """The sample in flight when the run was paused trips a rule.

    A pause is only left on a resume or an abort, so a stop that was itself a
    pause would leave the thread waiting for ever, with nobody told why. The
    host pauses through the control route; a spent quota pauses from the hook
    the store calls after every generation row. Both set the same flag.
    """
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    control = RunControl()
    engine = StubEngine()
    if paused_by == "host":
        engine.on_request = lambda body: control.pause() if len(engine.requests) == 10 else None
    else:
        rows = 0

        def quota_spent(note: Any) -> None:
            nonlocal rows
            rows += 1
            if rows == 10:
                control.pause()

        rs.store.usage_hook = quota_spent
    orch = h.orchestrator(rs, config, cards, engine, control=control)
    h.with_task(orch, cards[h.CARD], score=_broken_after(0))
    thread = threading.Thread(target=orch.run, daemon=True)
    thread.start()
    thread.join(timeout=20)

    assert not thread.is_alive(), "the orchestrator returned although the pause was never lifted"
    assert control.paused
    _stopped(rs.read_run(), "scorer_errors", scope="card", scope_id=h.CARD, phase="generation")
    assert len(engine.requests) == 10


def test_one_event_tells_the_observers_and_the_hook_ends_the_sittings(tmp_path: Path) -> None:
    class Hooks:
        stops = 0

        def admit(self, job: Any) -> tuple[str, None]:
            return "unsupervised", None

        def finished(self, job: Any, outcome: Any) -> None:
            return None

        def stopped(self) -> None:
            self.stops += 1

    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    sink, hooks = ListEventSink(), Hooks()
    run = h.orchestrator(
        rs, config, cards, StubEngine(script=_dead), sink=sink, dispatch_hooks=hooks, max_inflight=2
    ).run()

    # Samples of the other arm finish after the rule fired; they add no second halt.
    assert len(run.halts) == 1 and hooks.stops == 1
    (event,) = sink.of_type("check.stopped")
    assert event.data == {"run_id": rs.run_id, **run.halts[0].model_dump(mode="json")}
    states = [e.data["state"] for e in sink.of_type("run.state")]
    assert states == ["running", "paused"]
    assert sink.events.index(event) < sink.events.index(sink.of_type("run.state")[-1])


def test_a_check_stop_ends_supervised_sittings_as_a_host_pause_does() -> None:
    class Controller:
        def __init__(self, run_id: str) -> None:
            self.run_id, self.stopped = run_id, 0

        def stop(self) -> None:
            self.stopped += 1

    class Sessions:
        def __init__(self) -> None:
            self.all = [Controller("run1"), Controller("run1"), Controller("other")]

        def for_run(self, run_id: str) -> list[Controller]:
            return [c for c in self.all if c.run_id == run_id]

    sessions = Sessions()
    SessionDispatch(sessions, "run1").stopped()  # type: ignore[arg-type]
    assert [c.stopped for c in sessions.all] == [1, 1, 0]


def test_checks_version_is_set_at_the_first_start_only(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    fresh = h.run_store(tmp_path / "fresh", config)
    assert fresh.read_run().checks_version is None
    assert h.orchestrator(fresh, config, cards, StubEngine()).run().checks_version == 1
    assert rc.CHECKS_VERSION == 1
    again = h.orchestrator(h.reopen(fresh), config, cards, StubEngine()).run()
    assert again.checks_version == 1

    # A run that was started before result checks existed keeps none when it is resumed,
    # so its summary can say that its earlier samples were never looked at.
    old = h.run_store(tmp_path / "old", config)
    old.write_run(old.read_run().model_copy(update={"state": "paused"}))
    resumed = h.orchestrator(h.reopen(old), config, cards, StubEngine()).run()
    assert resumed.state == "complete" and resumed.checks_version is None
    assert "checks_version" not in old.run_dir.run_json.read_text(encoding="utf-8")


# ── the registry: what the host's Resume does ────────────────────────────────


def _live(rs: RunStore, secrets: SecretBag | None = None) -> LiveRun:
    return LiveRun(rs, secrets or SecretBag({"anthropic": h.JUDGE_KEY}), queue_size=2000)


def _factory(
    config: Any,
    cards: Any,
    engine: Any,
    *,
    judge: Any = None,
    built: list[Any] | None = None,
    **kwargs: Any,
) -> Any:
    def build(run_store: RunStore, secrets: SecretBag, control: RunControl, sink: Any) -> Any:
        orch = h.orchestrator(
            run_store,
            config,
            cards,
            engine,
            judge=judge,
            sink=sink,
            control=control,
            secrets=secrets,
            started=False,
            **kwargs,
        )
        if built is not None:
            built.append(orch)
        return orch

    return build


def _wait(pred: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_resume_after_a_check_stop_builds_a_new_orchestrator(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    live, built = _live(rs), []
    live.start(_factory(config, cards, StubEngine(script=_dead), built=built))
    live.join(30)
    assert not live.thread_alive and live.read().state == "paused"
    assert len(live.read().open_halts()) == 1

    run = live.resume(_factory(config, cards, StubEngine(), built=built))
    live.join(30)
    done = live.read()
    assert len(built) == 2, "resume did not lift a pause: it started a new orchestrator"
    assert done.state == "complete" and done.open_halts() == []
    assert h.never_completed(rs, config) == [] and run.run_id == rs.run_id


def test_resume_is_refused_while_samples_in_flight_are_still_finishing(tmp_path: Path) -> None:
    """Between the halt and the end of the run thread a resume would do nothing at all."""
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    slow = StubEngine(
        chunk_delay_s=0.2,
        script=lambda body: _dead(body) if str(body["request_id"]).startswith("qrng/") else None,
    )
    live = _live(rs)
    live.start(_factory(config, cards, slow, max_inflight=2))
    _wait(lambda: bool(live.read().open_halts()))
    assert live.thread_alive and live.read().state == "running"
    with pytest.raises(RunControlError, match="still finishing"):
        live.resume(_factory(config, cards, StubEngine()))
    live.join(30)
    assert live.read().state == "paused"
    assert h.finishes(rs, config)["stop"] >= 1, "the sample in flight finished and was kept"

    live.resume(_factory(config, cards, StubEngine()))
    live.join(30)
    assert live.read().state == "complete"


def test_a_host_resume_of_a_live_run_stamps_nothing_and_builds_nothing(tmp_path: Path) -> None:
    """Lifting a host or quota pause is not a resume past a check stop."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    live, built = _live(rs), []
    live.start(_factory(config, cards, StubEngine(script=_dead), built=built))
    live.join(30)
    assert live.read().state == "paused"

    first = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    ticks = iter([first] + [datetime(2026, 9, 14, 14, 0, tzinfo=UTC)] * 10_000)
    engine = StubEngine(chunk_delay_s=0.02)
    engine.on_request = lambda body: live.control.pause() if len(engine.requests) == 2 else None
    live.resume(_factory(config, cards, engine, built=built, clock=lambda: next(ticks)))
    _wait(lambda: len(engine.requests) >= 2 and live.read().state == "paused")
    assert live.thread_alive, "a host pause keeps the orchestrator waiting"
    stamped = live.read().halts[0].resumed_at
    assert stamped == first

    live.resume(_factory(config, cards, StubEngine(), built=built))  # the thread is alive
    live.join(30)
    done = live.read()
    assert done.state == "complete" and len(built) == 2
    assert done.halts[0].resumed_at == first, "the later resume left the halt as it was"


def test_a_server_restart_between_the_stop_and_the_resume_keeps_the_halt(tmp_path: Path) -> None:
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    assert h.orchestrator(rs, config, cards, StubEngine(script=_dead)).run().state == "paused"

    # A new process: nothing in memory, every unfinished run that held a key needs it again.
    after = h.reopen(rs)
    held = after.read_run().model_copy(update={"keys_present": {"anthropic": True}})
    assert mark_orphans([held], after.write_run) == [rs.run_id]
    live = RunRegistry().adopt(after)
    parked = live.read()
    assert parked.state == "needs_keys" and len(parked.open_halts()) == 1
    factory = _factory(config, cards, StubEngine())
    with pytest.raises(RunControlError, match="keys"):
        live.resume(factory)

    assert live.supply_keys({"anthropic": h.JUDGE_KEY}).state == "paused"
    assert len(live.read().open_halts()) == 1, "the notice is keyed on the halt, not the state"
    live.resume(factory)
    live.join(30)
    done = live.read()
    assert done.state == "complete" and done.halts[0].resumed_at == h.NOW
    assert h.never_completed(rs, config) == []


def test_a_run_without_keys_is_resumed_after_a_restart_with_nothing_to_supply(
    tmp_path: Path,
) -> None:
    """Seen on the live engine: such a run was ``needs_keys`` and no key could be supplied."""
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    assert h.orchestrator(rs, config, cards, StubEngine(script=_dead)).run().state == "paused"

    after = h.reopen(rs)
    assert not any(after.read_run().keys_present.values())
    assert mark_orphans([after.read_run()], after.write_run) == [rs.run_id]
    live = RunRegistry().adopt(after)
    parked = live.read()
    assert parked.state == "paused" and not parked.needs_keys
    assert len(parked.open_halts()) == 1
    live.resume(_factory(config, cards, StubEngine()))
    live.join(30)
    done = live.read()
    # Resume was taken and every sample generated. The test study has a judged eval, so the
    # run then asks for the judge key it never had, which is the ordinary needs-keys stop.
    assert done.halts[0].resumed_at is not None and h.never_completed(rs, config) == []
    assert done.state == "needs_keys"
