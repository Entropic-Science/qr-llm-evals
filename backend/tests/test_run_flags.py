"""Result checks on one sample of a run: what is flagged, what is tainted, in what order.

A flag is written to ``flags.jsonl`` and excludes nothing: the flagged sample
is still completed, scored and judged. A taint excludes, and is raised only in
the cases the registered exclusion rule names. Every test drives the real
orchestrator against the stub engine.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import httpx
import pytest

import run_check_helpers as h
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.runner import ListEventSink, RunEventBus
from stub_engine import Reply, StubEngine

QRNG = "qrng/stub_free"
PRNG = "prng_seed_7/stub_free"
MARK = "UPSTREAM-TEXT-THAT-MUST-NOT-LEAK"


def _by_ref(replies: dict[str, Reply]) -> Any:
    return lambda body: replies.get(str(body.get("request_id")))


def _rows(rs: Any, arm: str) -> dict[str, Any]:
    return {row.sample_ref: row for row in rs.generations(arm, h.CARD)}


def _score_rows(rs: Any, arm: str) -> dict[str, dict[str, Any]]:
    return {str(row["sample_ref"]): row for row in rs.score_rows(arm, h.CARD, "task")}


def _tier0_refs(rs: Any, arm: str) -> set[str]:
    return {str(row["sample_ref"]) for row in rs.iter_tier0_rows(arm, h.CARD)}


# ── generation flags ─────────────────────────────────────────────────────────


def test_generation_flags_are_stored_ride_on_the_event_and_exclude_nothing(tmp_path: Path) -> None:
    replies = {
        f"{QRNG}/p1/0/told_qrng": Reply(text=""),
        f"{QRNG}/p1/0/told_prng": Reply(finish="length"),
        f"{QRNG}/p1/0/told_nothing": Reply(text=" ".join(["loop"] * 100), finish="stop"),
        f"{QRNG}/p1/1/told_qrng": Reply(text="ab��� cd ef gh ij kl mn op"),
        f"{QRNG}/p1/1/told_prng": Reply(text="<think>a plan</think> the answer"),
    }
    expected = {
        f"{QRNG}/p1/0/told_qrng": {"empty_output"},
        f"{QRNG}/p1/0/told_prng": {"truncated"},
        f"{QRNG}/p1/0/told_nothing": {"repetition"},
        f"{QRNG}/p1/1/told_qrng": {"garbled"},
        f"{QRNG}/p1/1/told_prng": {"reasoning_in_output"},
    }
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    sink = ListEventSink()
    orch = h.orchestrator(rs, config, cards, StubEngine(script=_by_ref(replies)), sink=sink)
    run = orch.run()

    assert run.state == "complete" and run.halts == []
    stored: dict[str, set[str]] = {}
    for flag in rs.flags():
        stored.setdefault(flag.sample_ref, set()).add(flag.kind)
        assert flag.phase == "generation" == rc.FLAG_PHASE[flag.kind]
        assert (flag.arm_id, flag.card_id, flag.at) == ("qrng", h.CARD, h.NOW)
    assert stored == expected
    loop = next(f for f in rs.flags() if f.kind == "repetition")
    assert loop.count == 100 and "5 characters" in loop.detail

    # A flag is not an exclusion: every flagged sample is completed, scored, judged, untainted.
    rows, scores = _rows(rs, "qrng"), _score_rows(rs, "qrng")
    judged = {r.sample_ref for r in rs.judge_rows("qrng", h.CARD, "study_judge") if r.score}
    for ref in expected:
        assert rows[ref].finish_reason in ("stop", "length")
        assert "n_words" in scores[ref] and ref in judged
    assert rs.taints() == []

    # The flags ride on the sample's own finished event; a clean sample carries an empty list.
    finished = {e.data["sample_ref"]: e.data["flags"] for e in sink.of_type("sample.finished")}
    for ref, kinds in expected.items():
        assert {flag["kind"] for flag in finished[ref]} == kinds
        assert all(set(flag) == {"kind", "detail", "count"} for flag in finished[ref])
    assert finished[f"{PRNG}/p1/0/told_qrng"] == []


# ── Tier 0 flags ─────────────────────────────────────────────────────────────


def test_a_sample_that_asked_for_a_token_log_and_got_none_is_flagged_not_tainted(
    tmp_path: Path,
) -> None:
    missing = f"{QRNG}/p1/0/told_qrng"
    engine = StubEngine()
    log_dir = tmp_path / "token-log"
    h.engine_writes_logs(
        engine,
        log_dir,
        lambda ref: None if ref == missing else [h.log_row(i) for i in range(engine.n_tokens)],
    )
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path / "data", config)
    orch = h.orchestrator(
        rs, config, cards, engine, runner_config=h.log_config(log_dir), tier0_source="engine_log"
    )
    assert orch.run().state == "complete"

    assert [(f.sample_ref, f.kind, f.phase) for f in rs.flags()] == [
        (missing, "tier0_missing", "tier0")
    ]
    # No token records for that sample (not even ones from the API's log-probabilities,
    # whose token ids are of another kind), and its reply and score stand.
    assert missing not in _tier0_refs(rs, "qrng") and len(_tier0_refs(rs, "qrng")) == 11
    assert _rows(rs, "qrng")[missing].finish_reason == "stop"
    assert "n_words" in _score_rows(rs, "qrng")[missing]
    assert rs.taints() == []


def test_a_run_not_recorded_as_engine_log_keeps_the_api_records_and_raises_no_flag(
    tmp_path: Path,
) -> None:
    """Without a record that says ``engine_log`` a missing file is the degraded path, as before."""
    engine = StubEngine()
    log_dir = tmp_path / "token-log"
    h.engine_writes_logs(engine, log_dir, lambda ref: None)
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path / "data", config)
    orch = h.orchestrator(rs, config, cards, engine, runner_config=h.log_config(log_dir))
    assert orch.run().state == "complete"
    assert h.flags(rs) == [] and rs.taints() == []
    assert len(_tier0_refs(rs, "qrng")) == 12


def test_a_token_without_a_log_probability_gets_no_records_and_a_flag(tmp_path: Path) -> None:
    bad = f"{PRNG}/p2/1/told_nothing"
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    engine = StubEngine(script=_by_ref({bad: Reply(drop_logprob=True)}))
    assert h.orchestrator(rs, config, cards, engine).run().state == "complete"

    assert [(f.sample_ref, f.kind) for f in rs.flags()] == [(bad, "tier0_missing")]
    assert "log-probability" in rs.flags()[0].detail
    assert bad not in _tier0_refs(rs, "prng_seed_7") and len(_tier0_refs(rs, "prng_seed_7")) == 11
    # Before, such a token was stored with log-probability 0.0, that is probability 1.
    stored = [row["logprob"] for row in rs.iter_tier0_rows("prng_seed_7", h.CARD)]
    assert stored and all(value < 0 for value in stored)
    assert rs.taints() == [] and bad in rs.completed_refs("prng_seed_7", h.CARD)


def test_a_draw_outside_its_bracket_is_flagged_and_the_records_are_kept(tmp_path: Path) -> None:
    odd = f"{QRNG}/p2/0/told_prng"
    engine = StubEngine()
    log_dir = tmp_path / "token-log"

    def rows_for(ref: str) -> list[dict[str, Any]]:
        rows = [h.log_row(i) for i in range(engine.n_tokens)]
        if ref == odd:
            rows[1]["u_value"] = 0.9  # the bracket is [0, 0.5]
            rows[4]["u_value"] = 0.51
        return rows

    h.engine_writes_logs(engine, log_dir, rows_for)
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path / "data", config)
    orch = h.orchestrator(
        rs, config, cards, engine, runner_config=h.log_config(log_dir), tier0_source="engine_log"
    )
    assert orch.run().state == "complete"

    (flag,) = rs.flags()
    assert (flag.sample_ref, flag.kind, flag.phase, flag.count) == (
        odd,
        "u_outside_bracket",
        "tier0",
        2,
    )
    # The records are the evidence, so they are written; and it is a flag, not a taint.
    assert odd in _tier0_refs(rs, "qrng") and rs.taints() == []


# ── score flags ──────────────────────────────────────────────────────────────


def test_a_scorer_that_raises_or_returns_nan_leaves_an_error_row_and_a_flag(tmp_path: Path) -> None:
    raises, nan, unwritable = (
        f"{QRNG}/p1/0/told_qrng",
        f"{QRNG}/p1/0/told_prng",
        f"{QRNG}/p1/0/told_nothing",
    )

    def score(sample: dict[str, Any]) -> dict[str, Any]:
        if sample["sample_ref"] == raises:
            raise KeyError(MARK)
        if sample["sample_ref"] == nan:
            return {"n_words": 3, "mean": math.nan, "spread": math.inf}
        if sample["sample_ref"] == unwritable:
            return {"n_words": 3, "seen": {1, 2}}
        return {"n_words": 3}

    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(orch, cards[h.CARD], score=score)
    # Before, a NaN in a score row aborted the whole run for good.
    assert orch.run().state == "complete"

    details = {f.sample_ref: f.detail for f in rs.flags() if f.kind == "score_error"}
    assert details == {
        raises: "KeyError",
        nan: "a value that is not finite in mean, spread",
        unwritable: "a value that cannot be written as JSON",
    }
    scores = _score_rows(rs, "qrng")
    for ref in details:
        assert set(scores[ref]) == {"sample_ref", "error"}
        assert ref in rs.completed_refs("qrng", h.CARD), "the generation is kept"
    # The scorer's own message stays in the score row; a flag names the class only.
    assert MARK in scores[raises]["error"] and MARK not in h.run_text(rs)
    assert rs.taints() == []


@pytest.mark.parametrize(
    ("card_id", "score_row", "detail"),
    [
        (h.CARD, {"answer_valid": 0, "answer_value": None}, "answer_valid 0"),
        (h.CARD, {"verifier_status": "unavailable: no nltk data"}, "verifier_status unavailable"),
        ("aime2026", {"parse_status": "unparseable", "pass_at_1": 0}, "parse_status unparseable"),
        # On a card the catalogue does not list, parse_status is not the analysed value's extraction.
        (h.CARD, {"parse_status": "unparseable"}, None),
        ("aime2026", {"parse_status": "parsed", "pass_at_1": 1}, None),
    ],
)
def test_a_failed_extraction_the_card_reports_is_flagged_unparsed(
    tmp_path: Path, card_id: str, score_row: dict[str, Any], detail: str | None
) -> None:
    one = f"qrng/{card_id}/p1/0/told_qrng"
    config = h.study(card_id, seeded=0)
    cards = {card_id: h.card(card_id=card_id, judged=False)}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(rs, config, cards, StubEngine())
    h.with_task(
        orch,
        cards[card_id],
        score=lambda sample: score_row if sample["sample_ref"] == one else {"n_words": 1},
    )
    assert orch.run().state == "complete"
    found = [(f.sample_ref, f.kind, f.phase, f.detail) for f in rs.flags()]
    assert found == ([(one, "unparsed", "score", detail)] if detail else [])
    # The score row is stored as the card gave it.
    stored = {str(row["sample_ref"]): row for row in rs.score_rows("qrng", card_id, "task")}
    assert stored[one] == {"sample_ref": one, **score_row}


# ── taints ───────────────────────────────────────────────────────────────────


def test_a_stream_without_a_finish_reason_is_an_error_and_is_tried_again(tmp_path: Path) -> None:
    cut = f"{PRNG}/p1/0/told_prng"
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    first = h.orchestrator(rs, config, cards, StubEngine(script=_by_ref({cut: Reply(finish=None)})))
    assert first.run().state == "complete"  # one missing sample of twelve is under the threshold

    row = next(r for r in rs.generations("prng_seed_7", h.CARD) if r.sample_ref == cut)
    # Before, this row was stored as a clean `stop`, scored and judged.
    assert row.finish_reason == "error" and row.output, "what arrived is kept on the row"
    assert [(t.kind, t.detail) for t in rs.taints()] == [
        ("stream_error", "the stream ended without a finish reason")
    ]
    assert cut not in _score_rows(rs, "prng_seed_7")
    assert cut not in {r.sample_ref for r in rs.judge_rows("prng_seed_7", h.CARD, "study_judge")}
    assert cut not in rs.completed_refs("prng_seed_7", h.CARD)

    engine = StubEngine()
    again = h.orchestrator(h.reopen(rs), config, cards, engine)
    again.run()
    assert engine.request_ids == [cut], "only the sample without a completed row is generated"
    assert cut in rs.completed_refs("prng_seed_7", h.CARD)


def test_a_malformed_frame_is_a_stream_error_and_no_longer_aborts_the_run(tmp_path: Path) -> None:
    bad = f"{QRNG}/p2/0/told_qrng"
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    orch = h.orchestrator(
        rs, config, cards, StubEngine(script=_by_ref({bad: Reply(malformed=True)}))
    )
    run = orch.run()
    assert run.state == "complete", "one bad frame used to end the run as aborted"
    assert h.finishes(rs, config) == {"stop": 23, "error": 1}
    (taint,) = rs.taints()
    assert (taint.sample_ref, taint.kind) == (bad, "stream_error")
    assert "not valid JSON" in taint.detail and "chatcmpl-cut" not in taint.detail
    assert bad not in rs.completed_refs("qrng", h.CARD)


def test_an_attempt_that_did_not_complete_writes_only_its_stream_error_taint(
    tmp_path: Path,
) -> None:
    """A dead engine: the request fails and the entropy probe cannot be reached either.

    The failed attempt of a quantum sample used to get ``qrng_degraded`` as
    well, which went on excluding the sample after it was tried again and
    succeeded, on the quantum arm only.
    """

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    class Dead:
        transport = httpx.MockTransport(refused)

    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    stopped = h.orchestrator(rs, config, cards, Dead()).run()
    assert stopped.state == "paused" and stopped.halts[0].rule == "generation_errors"
    assert set(h.taint_kinds(rs)) == {"stream_error"}
    assert {t.sample_ref.split("/")[0] for t in rs.taints()} == {"qrng", "prng_seed_7"}

    assert h.orchestrator(h.reopen(rs), config, cards, StubEngine()).run().state == "complete"
    for arm in h.arm_ids(config):
        done = rs.completed_refs(arm, h.CARD)
        assert len(done) == 12
        stale = {t.sample_ref for t in rs.taints() if t.kind != "stream_error"}
        assert not (done & stale), "a retried sample carries no taint that still excludes it"
    quantum = [r for r in rs.generations("qrng", h.CARD) if r.finish_reason == "stop"]
    assert quantum and all(r.provenance.quantum for r in quantum)


def test_a_token_record_marked_fallback_taints_the_sample_and_withholds_quantum(
    tmp_path: Path,
) -> None:
    fell = f"{QRNG}/p1/1/told_nothing"
    engine = StubEngine()
    log_dir = tmp_path / "token-log"

    def rows_for(ref: str) -> list[dict[str, Any]]:
        rows = [h.log_row(i) for i in range(engine.n_tokens)]
        if ref == fell:
            rows[3]["entropy_is_fallback"] = True
        return rows

    h.engine_writes_logs(engine, log_dir, rows_for)
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path / "data", config)
    orch = h.orchestrator(
        rs, config, cards, engine, runner_config=h.log_config(log_dir), tier0_source="engine_log"
    )
    assert orch.run().state == "complete"

    # The engine's health counter never moved, so before this the sample was labelled quantum.
    assert engine.fallback_count == 0
    (taint,) = rs.taints()
    assert (taint.sample_ref, taint.kind) == (fell, "engine_fallback")
    assert "1 of 6 token records" in taint.detail
    rows = _rows(rs, "qrng")
    assert rows[fell].provenance.quantum is False
    assert rows[fell].provenance.engine_fallback_delta == 1
    assert all(row.provenance.quantum for ref, row in rows.items() if ref != fell)


@pytest.mark.parametrize(
    "bad",
    [
        {"u_value": "abc"},
        {"u_value": 1.5},
        {"cdf_lo": 0.9, "cdf_hi": 0.1},
        {"token_prob": 1.2},
        {"num_candidates": 0},
        {"logprob": float("-inf")},
        {"shannon_entropy": float("inf")},
        {"top_entries": [[1]]},
    ],
    ids=lambda bad: next(iter(bad)),
)
def test_a_token_log_value_the_record_cannot_hold_taints_the_sample_instead_of_aborting(
    tmp_path: Path, bad: dict[str, Any]
) -> None:
    odd = f"{QRNG}/p1/0/told_qrng"
    engine = StubEngine()
    log_dir = tmp_path / "token-log"

    def rows_for(ref: str) -> list[dict[str, Any]]:
        rows = [h.log_row(i) for i in range(engine.n_tokens)]
        if ref == odd:
            rows[2].update(bad)
        return rows

    h.engine_writes_logs(engine, log_dir, rows_for)
    config, cards = h.study(), {h.CARD: h.card()}
    rs = h.run_store(tmp_path / "data", config)
    orch = h.orchestrator(
        rs, config, cards, engine, runner_config=h.log_config(log_dir), tier0_source="engine_log"
    )
    run = orch.run()

    assert run.state == "complete", "one odd log row used to abort the run and lose the sample"
    (taint,) = rs.taints()
    assert (taint.sample_ref, taint.kind) == (odd, "stateless_row")
    assert odd in rs.completed_refs("qrng", h.CARD) and odd in _score_rows(rs, "qrng")
    assert odd not in _tier0_refs(rs, "qrng") and len(_tier0_refs(rs, "qrng")) == 11
    assert h.flags(rs) == []


def test_taints_are_on_disk_before_the_generation_row(tmp_path: Path) -> None:
    """A write that fails half-way must not leave a completed row whose taint is missing."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    engine = StubEngine()
    orch = h.orchestrator(rs, config, cards, engine)
    ref = orch.plan_arm(config.arms[0])[0].sample_ref  # the first sample that is sent
    engine.fallback_refs.add(ref)

    def disk_full(*args: Any, **kwargs: Any) -> int:
        raise OSError("no space left on device")

    orch.store.append_tier0 = disk_full  # type: ignore[method-assign]
    with pytest.raises(OSError, match="no space"):
        orch.run()
    assert rs.read_run().state == "aborted"
    assert ref in rs.completed_refs("qrng", h.CARD), "the row was written before the failure"
    assert [(t.sample_ref, t.kind) for t in rs.taints()] == [(ref, "engine_fallback")]


# ── the event sink changes nothing ───────────────────────────────────────────


def test_artifacts_with_flags_and_a_stop_are_byte_identical_with_and_without_a_sink(
    tmp_path: Path,
) -> None:
    """One arm, so the order of every file is fixed; the run flags samples and then stops."""
    config, cards = h.study(seeded=0), {h.CARD: h.card(k=4)}
    replies = {
        f"{QRNG}/p1/0/told_qrng": Reply(text=""),
        f"{QRNG}/p1/0/told_prng": Reply(finish="length"),
        f"{QRNG}/p1/1/told_qrng": Reply(finish=None),
    }

    def score(sample: dict[str, Any]) -> dict[str, Any]:
        if "/p1/" in sample["sample_ref"]:
            return {"n_words": 1}
        raise RuntimeError("scorer is broken from the second prompt on")

    def go(root: Path, sink: Any) -> Any:
        rs = h.run_store(root, config)
        engine = StubEngine(script=_by_ref(replies), fallback_refs={f"{QRNG}/p1/1/told_prng"})
        orch = h.orchestrator(rs, config, cards, engine, sink=sink)
        h.with_task(orch, cards[h.CARD], score=score)
        assert orch.run().state == "paused"
        return rs

    bus = RunEventBus()
    sub = bus.subscribe()
    headless, viewed = go(tmp_path / "a", None), go(tmp_path / "b", bus)
    for rs in (headless, viewed):
        assert [halt.rule for halt in rs.read_run().halts] == ["scorer_errors"]
        assert h.flag_kinds(rs) == {"empty_output": 1, "truncated": 1, "score_error": 10}
        assert h.taint_kinds(rs) == {"stream_error": 1, "engine_fallback": 1}
    a, b = headless.arm_card("qrng", h.CARD), viewed.arm_card("qrng", h.CARD)
    for name in ("generations_jsonl", "tier0_jsonl"):
        assert getattr(a, name).read_bytes() == getattr(b, name).read_bytes(), name
    assert a.scores_jsonl("task").read_bytes() == b.scores_jsonl("task").read_bytes()
    for name in ("flags_jsonl", "taints_jsonl", "run_json"):
        written = getattr(headless.run_dir, name).read_bytes()
        assert written and written == getattr(viewed.run_dir, name).read_bytes(), name
    assert {"check.stopped", "taint", "sample.finished"} <= {e.type for e in sub.drain()}
