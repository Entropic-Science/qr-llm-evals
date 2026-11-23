"""No key in the run folder or in an event, whatever an upstream answer says (invariant 3).

An engine or a judge that fails may repeat the key it was sent in its error
body, and an exception may carry it in its message. Each test puts the run's
keys into such a text and then reads every file of the run folder and every
event: the keys must be in none of them, and the rest of the text must still
be there.

The keys used here have no provider shape, so only the run's own ``SecretBag``
can know them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import run_check_helpers as h
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.logging_utils import REDACTED, redact, unregister_secret
from qr_llm_evals.runner import ListEventSink
from qr_llm_evals.runner.judge_phase import PREFLIGHT_USER
from qr_llm_evals.store import RunStore
from qr_llm_evals.store.artifacts import iter_jsonl
from stub_engine import JudgeSay, Reply, StubEngine, StubJudge

JUDGE_KEY = "judgekey-7f3a9c1e5b2d4a6f-ZX9"
ENGINE_KEY = "enginekey-0d1c2b3a4f5e6d7c-QW8"
KEYS = (JUDGE_KEY, ENGINE_KEY)
ECHO = f"invalid x-api-key {JUDGE_KEY} (also seen: {ENGINE_KEY}); request refused"


def _bag() -> SecretBag:
    return SecretBag({"anthropic": JUDGE_KEY, "custom": ENGINE_KEY})


def _files(rs: RunStore) -> dict[str, str]:
    """Every file under the run folder, as text."""
    root = rs.run_dir.path
    return {
        str(path.relative_to(root)): path.read_bytes().decode("utf-8", errors="replace")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_no_key(rs: RunStore, sink: ListEventSink) -> None:
    files = _files(rs)
    assert files, "the run folder holds files"
    for name, text in files.items():
        for key in KEYS:
            assert key not in text, f"a key is in {name}"
    assert sink.events, "the run emitted events"
    for event in sink.events:
        sent = json.dumps(event.data, default=str)
        for key in KEYS:
            assert key not in sent, f"a key is in a {event.type} event"


def _judge_rows(rs: RunStore) -> list[dict[str, Any]]:
    return [
        row for path in sorted(rs.run_dir.path.rglob("judge/*.jsonl")) for row in iter_jsonl(path)
    ]


# ── the bag ──────────────────────────────────────────────────────────────────


def test_the_bag_takes_its_own_values_out_of_a_text() -> None:
    bag = _bag()
    try:
        assert bag.redact(ECHO) == (
            f"invalid x-api-key {REDACTED} (also seen: {REDACTED}); request refused"
        )
        assert bag.redact("HTTP 500: nothing secret") == "HTTP 500: nothing secret"
        # A key of a provider shape goes too, in the bag or not.
        assert "sk-ant-api03-abcdefghijklmnop" not in bag.redact(
            "key sk-ant-api03-abcdefghijklmnop"
        )
    finally:
        bag.clear()


def test_the_bag_knows_its_keys_without_the_log_filter() -> None:
    """The bag removes its own values even when the log filter has lost them."""
    bag = _bag()
    try:
        unregister_secret(JUDGE_KEY)
        assert JUDGE_KEY in redact(ECHO), "the log filter was expected to have lost the key"
        assert JUDGE_KEY not in bag.redact(ECHO) and ENGINE_KEY not in bag.redact(ECHO)
    finally:
        bag.clear()


# ── generation ───────────────────────────────────────────────────────────────


def test_an_engine_error_body_that_repeats_a_key_is_stored_and_sent_without_it(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    sink = ListEventSink()
    bag = _bag()
    engine = StubEngine(script=lambda body: Reply(status=500, error_body=ECHO))
    try:
        run = h.orchestrator(rs, config, cards, engine, sink=sink, secrets=bag).run()
    finally:
        bag.clear()

    assert run.state == "paused" and run.halts[-1].rule == "generation_errors"
    assert run.halts[-1].causes == {"HTTP 500": rc.GENERATION_ERRORS_STREAK}
    _assert_no_key(rs, sink)
    taints = rs.taints()
    assert taints and all(taint.kind == "stream_error" for taint in taints)
    for taint in taints:
        # What is stored is the same text with the keys taken out, and nothing else changed.
        assert f"invalid x-api-key {REDACTED} (also seen: {REDACTED}); request refused" in (
            taint.detail
        )
        assert "500" in taint.detail
    sent = sink.of_type("taint")
    assert len(sent) == len(taints)
    assert [event.data["detail"] for event in sent] == [taint.detail for taint in taints]


def test_a_transport_error_whose_message_holds_a_key_is_stored_without_it(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    sink = ListEventSink()
    bag = _bag()

    class _Refusing:
        requests: list[Any] = []

        @property
        def transport(self) -> httpx.MockTransport:
            def handle(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/chat/completions"):
                    raise httpx.ConnectError(f"proxy refused {ENGINE_KEY}", request=request)
                return httpx.Response(404, json={})

            return httpx.MockTransport(handle)

    try:
        run = h.orchestrator(rs, config, cards, _Refusing(), sink=sink, secrets=bag).run()
    finally:
        bag.clear()

    assert run.state == "paused"
    _assert_no_key(rs, sink)
    details = [taint.detail for taint in rs.taints() if taint.kind == "stream_error"]
    assert details and all(f"proxy refused {REDACTED}" in detail for detail in details)


def test_a_scorer_error_whose_message_holds_a_key_is_stored_without_it(tmp_path: Path) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card(judged=False)}
    rs = h.run_store(tmp_path, config)
    sink = ListEventSink()
    bag = _bag()

    def score(sample: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(f"lookup failed with {ENGINE_KEY}")

    orch = h.orchestrator(rs, config, cards, StubEngine(), sink=sink, secrets=bag)
    h.with_task(orch, cards[h.CARD], score=score)
    try:
        orch.run()
    finally:
        bag.clear()

    _assert_no_key(rs, sink)
    scores = [
        row for path in sorted(rs.run_dir.path.rglob("scores/*.jsonl")) for row in iter_jsonl(path)
    ]
    assert scores and all(
        row["error"] == f"ValueError: lookup failed with {REDACTED}" for row in scores
    )


# ── the judge ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [401, 500])
def test_a_judge_error_body_that_repeats_the_key_is_stored_without_it(
    tmp_path: Path, status: int
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    sink = ListEventSink()
    bag = _bag()

    def script(body: dict[str, Any]) -> JudgeSay | None:
        if str(body["messages"][0]["content"]) == PREFLIGHT_USER:
            return None  # the request before the first generation is answered
        return JudgeSay(status=status, error_body=ECHO)

    judge = StubJudge(script=script)
    try:
        run = h.orchestrator(
            rs, config, cards, StubEngine(), judge=judge, sink=sink, secrets=bag
        ).run()
    finally:
        bag.clear()

    assert run.state == "paused" and run.halts[-1].phase == "judge"
    assert set(judge.keys) == {JUDGE_KEY}, "the judge was sent the real key"
    _assert_no_key(rs, sink)
    rows = _judge_rows(rs)
    assert rows and all(row["status"] == "failed" for row in rows)
    for row in rows:
        assert row["raw"].startswith("error: JudgeError: ")
        assert (
            f"invalid x-api-key {REDACTED} (also seen: {REDACTED}); request refused" in row["raw"]
        )


def test_a_judge_that_echoes_the_key_before_the_first_generation_leaves_no_key(
    tmp_path: Path,
) -> None:
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    sink = ListEventSink()
    bag = _bag()
    engine = StubEngine()
    judge = StubJudge(script=lambda body: JudgeSay(status=401, error_body=ECHO))
    try:
        run = h.orchestrator(rs, config, cards, engine, judge=judge, sink=sink, secrets=bag).run()
    finally:
        bag.clear()

    assert run.state == "paused" and run.halts[-1].rule == "judge_rejected"
    assert run.halts[-1].causes == {"HTTP 401": 1}
    assert engine.requests == [] and len(judge.preflights) == 1
    _assert_no_key(rs, sink)
    assert [event.data["causes"] for event in sink.of_type("check.stopped")] == [{"HTTP 401": 1}]


def test_a_judge_reply_is_stored_as_it_came(tmp_path: Path) -> None:
    """Only the text of a failed call is redacted; a received reply is stored as it came."""
    config, cards = h.study(seeded=0), {h.CARD: h.card()}
    rs = h.run_store(tmp_path, config)
    bag = _bag()
    # "Bearer authentication" has the shape the log redaction takes for a token.
    reply = "The answer explains Bearer authentication well.\nSCORE: 1"

    def script(body: dict[str, Any]) -> JudgeSay | None:
        if str(body["messages"][0]["content"]) == PREFLIGHT_USER:
            return None
        return JudgeSay(text=reply)

    try:
        run = h.orchestrator(
            rs, config, cards, StubEngine(), judge=StubJudge(script=script), secrets=bag
        ).run()
    finally:
        bag.clear()
    assert run.state == "complete"
    rows = _judge_rows(rs)
    assert rows and all(row["raw"] == reply and row["status"] == "ok" for row in rows)
