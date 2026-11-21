"""Anthropic judge client (spec §2.3, D-9): the study's model and call
parameters, a frozen system prompt, ``metadata.user_id`` = judge run id, native
Messages headers, retries and error excerpts that never carry the key.
``httpx.MockTransport`` only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from qr_llm_evals.cards.loader import load_cards
from qr_llm_evals.client.anthropic_judge import (
    ANTHROPIC_VERSION,
    DEFAULT_JUDGE_MODEL,
    AnthropicJudgeClient,
    JudgeError,
    JudgeRequest,
)
from qr_llm_evals.models.schema import DEFAULT_JUDGE_MODEL as SPEC_DEFAULT_JUDGE_MODEL
from qr_llm_evals.models.schema import JudgeSpec
from qr_llm_evals.runner.judge_phase import judge_client, needs_study_judge

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "sk-ant-" + "api03-judge-secret-0123456789"
RUBRIC = "You are a frozen rubric. Score 1-5. Answer with JSON."
REQ = JudgeRequest(system=RUBRIC, user="PROMPT: x\nOUTPUT: y", judge_run_id="judge_run_abc")


def _ok(text: str = '{"score": 4}') -> dict[str, Any]:
    return {
        "id": "msg_1",
        "model": DEFAULT_JUDGE_MODEL,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 12, "output_tokens": 5},
    }


class _Recorder:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def client(self, **kw: Any) -> AnthropicJudgeClient:
        transport = httpx.MockTransport(self)
        return AnthropicJudgeClient(
            KEY, transport=transport, async_transport=transport, sleep=False, **kw
        )


def test_default_body_is_the_current_haiku_in_a_single_pass() -> None:
    body = AnthropicJudgeClient(KEY).build_body(REQ)
    assert body == {
        "model": "claude-haiku-5-5",
        "max_tokens": 1024,
        "system": RUBRIC,
        "messages": [{"role": "user", "content": REQ.user}],
        "metadata": {"user_id": "judge_run_abc"},
        "thinking": {"type": "disabled"},
    }
    # current Claude models answer a sampling parameter with a 400, so none is sent
    assert "temperature" not in body
    assert DEFAULT_JUDGE_MODEL == SPEC_DEFAULT_JUDGE_MODEL == "claude-haiku-5-5"
    assert len(REQ.system_sha256) == 64


def test_the_study_settings_reach_the_request_body() -> None:
    spec = JudgeSpec(
        model="claude-sonnet-4-6", temperature=0.0, max_tokens=300, thinking="model_default"
    )
    body = judge_client(spec, KEY).build_body(REQ)
    assert body["model"] == "claude-sonnet-4-6"
    assert body["temperature"] == 0.0 and body["max_tokens"] == 300
    assert "thinking" not in body, "'model_default' sends nothing and lets the model decide"

    default = judge_client(JudgeSpec(), KEY).build_body(REQ)
    assert default == AnthropicJudgeClient(KEY).build_body(REQ)


def test_a_request_can_override_the_output_limit() -> None:
    req = JudgeRequest(system=RUBRIC, user="u", judge_run_id="r", max_tokens=64)
    assert AnthropicJudgeClient(KEY, max_tokens=900).build_body(req)["max_tokens"] == 64


@pytest.mark.parametrize(
    "bad",
    [{"temperature": 1.5}, {"temperature": -0.1}, {"max_tokens": 8}, {"thinking": "on"}],
)
def test_judge_spec_refuses_out_of_range_settings(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        JudgeSpec.model_validate(bad)


def test_only_llm_cards_on_the_shared_judge_need_the_key() -> None:
    cards = load_cards(FIXTURES / "registry/evals")
    assert needs_study_judge(cards.values()) is True
    assert needs_study_judge(c for c in cards.values() if c.judge.kind != "llm") is False


def test_stop_sequences_are_forwarded() -> None:
    req = JudgeRequest(system=RUBRIC, user="u", judge_run_id="r", stop_sequences=("</json>",))
    assert AnthropicJudgeClient(KEY).build_body(req)["stop_sequences"] == ["</json>"]


def test_judge_sends_native_messages_request_and_parses_text_blocks() -> None:
    rec = _Recorder([httpx.Response(200, json=_ok(), headers={"request-id": "req_9"})])
    resp = rec.client().judge(REQ)

    request = rec.requests[0]
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
    assert "authorization" not in request.headers
    sent = json.loads(request.content)
    assert "temperature" not in sent and sent["thinking"] == {"type": "disabled"}
    assert sent["metadata"] == {"user_id": "judge_run_abc"}
    assert sent["system"] == RUBRIC

    assert resp.text == '{"score": 4}'
    assert resp.model == DEFAULT_JUDGE_MODEL
    assert resp.stop_reason == "end_turn"
    assert resp.usage == {"input_tokens": 12, "output_tokens": 5}
    assert resp.request_id == "req_9"


def test_multiple_text_blocks_concatenate_and_non_text_ignored() -> None:
    body = _ok()
    body["content"] = [
        {"type": "thinking", "thinking": "hidden"},
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
    ]
    rec = _Recorder([httpx.Response(200, json=body)])
    assert rec.client().judge(REQ).text == "ab"


async def test_ajudge_matches_sync() -> None:
    rec = _Recorder([httpx.Response(200, json=_ok("x"))])
    resp = await rec.client().ajudge(REQ)
    assert resp.text == "x"
    assert json.loads(rec.requests[0].content)["metadata"]["user_id"] == "judge_run_abc"


def test_retries_on_429_then_succeeds() -> None:
    rec = _Recorder(
        [
            httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow"}}),
            httpx.Response(529, json={"error": {"message": "overloaded"}}),
            httpx.Response(200, json=_ok("ok")),
        ]
    )
    assert rec.client(max_retries=2).judge(REQ).text == "ok"
    assert len(rec.requests) == 3


def test_error_after_retries_exhausted_and_no_key_in_message() -> None:
    rec = _Recorder([httpx.Response(500, json={"error": {"message": "boom"}})])
    with pytest.raises(JudgeError) as err:
        rec.client(max_retries=1).judge(REQ)
    assert err.value.status == 500 and "boom" in err.value.excerpt
    assert KEY not in str(err.value)
    assert len(rec.requests) == 2


def test_4xx_is_not_retried() -> None:
    rec = _Recorder([httpx.Response(400, json={"error": {"message": "bad"}})])
    with pytest.raises(JudgeError) as err:
        rec.client().judge(REQ)
    assert err.value.status == 400 and len(rec.requests) == 1


async def test_async_error_path() -> None:
    rec = _Recorder([httpx.Response(401, json={"error": {"message": "unauthorized"}})])
    with pytest.raises(JudgeError) as err:
        await rec.client().ajudge(REQ)
    assert err.value.status == 401


def test_requires_key_and_repr_hides_it() -> None:
    with pytest.raises(ValueError):
        AnthropicJudgeClient("")
    assert KEY not in repr(AnthropicJudgeClient(KEY))
