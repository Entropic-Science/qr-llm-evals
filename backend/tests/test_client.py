"""Generation client (spec §2.3): SSE parsing with logprobs + reasoning deltas,
cancellation closing the stream, usage capture, error excerpts, body prep and
provider targets. Everything runs against ``httpx.MockTransport``; no network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from qr_llm_evals.client.cancel import CancelToken, GenerationCancelled
from qr_llm_evals.client.openai_client import Completion, OpenAIClient, UpstreamError, prepare_body
from qr_llm_evals.client.profiles import (
    CUSTOM_ID,
    ProviderTarget,
    known_targets,
    qr_server_target,
    target_for,
)
from qr_llm_evals.client.sse import (
    ChatChunk,
    chunk_from_obj,
    message_from_response,
    parse_sse_line,
)

# ── fixtures: an SSE stream with thinking, logprobs and a usage frame ─────────


def _frame(delta: dict[str, Any], **choice_extra: Any) -> str:
    return (
        "data: "
        + json.dumps({"id": "x", "choices": [{"index": 0, "delta": delta, **choice_extra}]})
        + "\n\n"
    )


def _lp(token: str, logprob: float, *tops: tuple[str, float]) -> dict[str, Any]:
    return {
        "content": [
            {
                "token": token,
                "logprob": logprob,
                "bytes": list(token.encode()),
                "top_logprobs": [{"token": t, "logprob": lp} for t, lp in tops],
            }
        ]
    }


SSE_FRAMES: list[str] = [
    _frame({"role": "assistant", "content": ""}),
    _frame({"reasoning_content": "let me "}),
    _frame({"reasoning_content": "think"}),
    _frame({"content": "Hel"}, logprobs=_lp("Hel", -0.1, ("Hel", -0.1), ("Hi", -2.3))),
    ": keepalive\n\n",
    _frame({"content": "lo"}, logprobs=_lp("lo", -0.05, ("lo", -0.05))),
    _frame({}, finish_reason="stop"),
    "data: "
    + json.dumps({"id": "x", "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}})
    + "\n\n",
    "data: [DONE]\n\n",
]


class _SyncStream(httpx.SyncByteStream):
    """A byte stream that records whether the client closed it (the vLLM abort signal)."""

    def __init__(self, frames: list[str]) -> None:
        self.frames = frames
        self.closed = False
        self.yielded = 0

    def __iter__(self) -> Iterator[bytes]:
        for f in self.frames:
            self.yielded += 1
            yield f.encode()

    def close(self) -> None:
        self.closed = True


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, frames: list[str]) -> None:
        self.frames = frames
        self.closed = False
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for f in self.frames:
            self.yielded += 1
            yield f.encode()

    async def aclose(self) -> None:
        self.closed = True


class _Recorder:
    """Captures the request the client sent and serves a canned stream."""

    def __init__(self, frames: list[str] | None = None, status: int = 200) -> None:
        self.frames = frames if frames is not None else SSE_FRAMES
        self.status = status
        self.requests: list[httpx.Request] = []
        self.stream: _SyncStream | None = None
        self.astream: _AsyncStream | None = None

    def sync(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        self.stream = _SyncStream(self.frames)
        return httpx.Response(
            200, stream=self.stream, headers={"content-type": "text/event-stream"}
        )

    async def asynch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        self.astream = _AsyncStream(self.frames)
        return httpx.Response(
            200, stream=self.astream, headers={"content-type": "text/event-stream"}
        )

    def client(self, **kw: Any) -> OpenAIClient:
        return OpenAIClient(
            "http://127.0.0.1:8000",
            transport=httpx.MockTransport(self.sync),
            async_transport=httpx.MockTransport(self.asynch),
            **kw,
        )


BODY: dict[str, Any] = {
    "model": "qr-llm",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 8,
    "logprobs": True,
    "top_logprobs": 20,
    "vllm_xargs": {"qr_preset": "creative_sampling"},
}


# ── SSE parsing ───────────────────────────────────────────────────────────────


def test_parse_sse_line_skips_control_frames() -> None:
    assert parse_sse_line("") is None
    assert parse_sse_line(": keepalive") is None
    assert parse_sse_line("data: [DONE]") is None
    assert parse_sse_line("event: ping") is None
    assert parse_sse_line('data: {"a": 1}') == {"a": 1}


def test_chunk_captures_thinking_logprobs_and_finish() -> None:
    obj = json.loads(SSE_FRAMES[3][len("data: ") :])
    chunk = chunk_from_obj(obj)
    assert chunk.delta_text == "Hel"
    assert chunk.delta_thinking == ""
    assert len(chunk.logprobs) == 1
    tok = chunk.logprobs[0]
    assert tok.token == "Hel" and tok.logprob == pytest.approx(-0.1)
    assert [t.token for t in tok.top_logprobs] == ["Hel", "Hi"]
    assert tok.bytes_ == tuple(b"Hel")
    assert tok.as_record() == {
        "token": "Hel",
        "logprob": -0.1,
        "top_logprobs": [["Hel", -0.1], ["Hi", -2.3]],
    }

    thinking = chunk_from_obj(json.loads(SSE_FRAMES[1][len("data: ") :]))
    assert thinking.delta_thinking == "let me " and thinking.delta_text == ""

    final = chunk_from_obj(json.loads(SSE_FRAMES[6][len("data: ") :]))
    assert final.finish_reason == "stop" and final.is_control


def test_chunk_accepts_reasoning_alias_and_missing_choices() -> None:
    assert chunk_from_obj({"choices": [{"delta": {"reasoning": "r"}}]}).delta_thinking == "r"
    usage_only = chunk_from_obj({"choices": [], "usage": {"completion_tokens": 3}})
    assert usage_only.usage == {"completion_tokens": 3} and usage_only.is_control


def test_message_from_response_folds_non_streaming_shape() -> None:
    chunk = message_from_response(
        {
            "choices": [
                {
                    "message": {"content": "answer", "reasoning_content": "why"},
                    "finish_reason": "length",
                    "logprobs": _lp("answer", -0.5),
                }
            ],
            "usage": {"completion_tokens": 1},
        }
    )
    assert (chunk.delta_text, chunk.delta_thinking, chunk.finish_reason) == (
        "answer",
        "why",
        "length",
    )
    assert chunk.logprobs[0].token == "answer" and chunk.usage == {"completion_tokens": 1}


# ── body preparation ──────────────────────────────────────────────────────────


def test_prepare_body_pins_stream_flags_without_mutating_input() -> None:
    src = dict(BODY)
    streamed = prepare_body(src, stream=True)
    assert streamed["stream"] is True
    assert streamed["stream_options"] == {"include_usage": True}
    assert "stream" not in src and "stream_options" not in src
    streamed["vllm_xargs"]["qr_preset"] = "mutated"
    assert src["vllm_xargs"]["qr_preset"] == "creative_sampling"  # deep copy

    plain = prepare_body({**BODY, "stream_options": {"include_usage": True}}, stream=False)
    assert plain["stream"] is False and "stream_options" not in plain


# ── streaming (sync + async) ──────────────────────────────────────────────────


def test_generate_streams_thinking_text_logprobs_and_usage() -> None:
    rec = _Recorder()
    seen: list[ChatChunk] = []
    result = rec.client().generate(BODY, on_delta=seen.append)

    assert result.text == "Hello"
    assert result.thinking == "let me think"
    assert [t.token for t in result.logprobs] == ["Hel", "lo"]
    assert result.finish_reason == "stop"
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 2}
    assert result.completion_tokens == 2
    assert not result.cancelled
    assert len(seen) == result.chunks == 7  # every JSON frame, control frames included

    sent = json.loads(rec.requests[0].content)
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert sent["vllm_xargs"] == {"qr_preset": "creative_sampling"}
    assert rec.requests[0].url.path == "/v1/chat/completions"
    assert "authorization" not in rec.requests[0].headers  # loopback vLLM: no key
    assert rec.stream is not None and rec.stream.closed


async def test_agenerate_matches_sync_path() -> None:
    rec = _Recorder()
    result = await rec.client().agenerate(BODY)
    assert result.text == "Hello" and result.thinking == "let me think"
    assert [t.token for t in result.logprobs] == ["Hel", "lo"]
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 2}
    assert rec.astream is not None and rec.astream.closed


def test_cancel_token_closes_stream_and_returns_partial() -> None:
    rec = _Recorder()
    cancel = CancelToken()

    def stop_after_first_text(chunk: ChatChunk) -> None:
        if chunk.delta_text:
            cancel.cancel("host pressed stop")

    result = rec.client().generate(BODY, on_delta=stop_after_first_text, cancel=cancel)
    assert result.cancelled and result.cancel_reason == "host pressed stop"
    assert result.finish_reason == "cancelled"
    assert result.text == "Hel"  # partial output kept
    assert result.usage is None
    assert rec.stream is not None
    assert rec.stream.closed, "closing the response is what aborts the vLLM sequence"
    assert rec.stream.yielded < len(SSE_FRAMES)


async def test_async_cancel_closes_stream() -> None:
    rec = _Recorder()
    cancel = CancelToken()
    result = await rec.client().agenerate(BODY, cancel=cancel, on_delta=lambda _: cancel.cancel())
    assert result.cancelled and result.text == ""
    assert rec.astream is not None and rec.astream.closed


def test_stream_iterator_raises_generation_cancelled() -> None:
    rec = _Recorder()
    cancel = CancelToken()
    gen = rec.client().stream_chat_completion(BODY, cancel=cancel)
    first = next(gen)
    assert first.raw["choices"][0]["delta"]["role"] == "assistant"
    cancel.cancel("abort")
    with pytest.raises(GenerationCancelled) as err:
        next(gen)
    assert err.value.reason == "abort"
    assert rec.stream is not None and rec.stream.closed


def test_breaking_out_of_stream_closes_it() -> None:
    rec = _Recorder()
    for _ in rec.client().stream_chat_completion(BODY):
        break
    assert rec.stream is not None and rec.stream.closed


def test_cancel_token_is_one_shot() -> None:
    tok = CancelToken()
    assert not tok.cancelled and "active" in repr(tok)
    tok.raise_if_cancelled()
    tok.cancel("first")
    tok.cancel("second")
    assert tok.cancelled and tok.reason == "first"
    with pytest.raises(GenerationCancelled):
        tok.raise_if_cancelled()


# ── errors and non-streaming ──────────────────────────────────────────────────


def test_upstream_error_carries_status_and_body_excerpt_not_key() -> None:
    rec = _Recorder(status=503)
    client = rec.client(api_key="sk-test-secret-value-1234567890")
    with pytest.raises(UpstreamError) as err:
        client.generate(BODY)
    assert err.value.status == 503
    assert "nope" in err.value.excerpt
    assert "sk-test-secret" not in str(err.value)
    assert rec.requests[0].headers["authorization"] == "Bearer sk-test-secret-value-1234567890"
    assert "sk-test-secret" not in repr(client)


async def test_async_upstream_error() -> None:
    rec = _Recorder(status=429)
    with pytest.raises(UpstreamError) as err:
        await rec.client().achat_completion(BODY)
    assert err.value.status == 429


def test_chat_completion_non_streaming_and_complete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert sent["stream"] is False and "stream_options" not in sent
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 1},
            },
        )

    client = OpenAIClient("http://127.0.0.1:8000/v1/", transport=httpx.MockTransport(handler))
    assert client.base_url == "http://127.0.0.1:8000"
    assert client.chat_completion(BODY)["choices"][0]["message"]["content"] == "pong"
    folded = client.complete(BODY)
    assert (
        isinstance(folded, Completion) and folded.text == "pong" and folded.finish_reason == "stop"
    )


def test_extra_headers_and_key_are_sent() -> None:
    rec = _Recorder()
    client = rec.client(
        api_key="sk-test-abcdefghijklmnop", extra_headers={"X-Title": "qr-llm-evals"}
    )
    client.generate(BODY)
    headers = rec.requests[0].headers
    assert headers["authorization"] == "Bearer sk-test-abcdefghijklmnop"
    assert headers["x-title"] == "qr-llm-evals"


# ── provider targets ──────────────────────────────────────────────────────────


def test_known_targets_come_from_providers_yaml() -> None:
    targets = known_targets()
    assert {"openai", "anthropic_compat"} <= set(targets)
    assert "openrouter" not in targets  # archived: archive/openrouter/RESTORE.md
    assert targets["openai"].base_url == "https://api.openai.com"
    assert targets["openai"].secret_name == "openai"
    assert targets["anthropic_compat"].secret_name == "anthropic"
    assert targets["openai"].chat_url == "https://api.openai.com/v1/chat/completions"


def test_target_for_resolution() -> None:
    qr = target_for("qr_server", vllm_origin="http://127.0.0.1:8000/v1")
    assert qr == qr_server_target("http://127.0.0.1:8000") and not qr.needs_key
    with pytest.raises(ValueError):
        target_for("qr_server")

    by_url = target_for("custom", "https://api.openai.com/v1")
    assert by_url.id == "openai" and by_url.secret_name == "openai"

    unknown = target_for("custom", "https://llm.example.org/v1/")
    assert unknown == ProviderTarget(
        id=CUSTOM_ID, base_url="https://llm.example.org", secret_name=CUSTOM_ID
    )
    with pytest.raises(ValueError):
        target_for("custom")


def test_target_for_with_injected_table(tmp_path: Path) -> None:
    yaml_file = tmp_path / "providers.yaml"
    yaml_file.write_text(
        "profiles:\n  mine:\n    base_url: https://mine.test/v1\n  unknown:\n    base_url: null\n",
        encoding="utf-8",
    )
    targets = known_targets(yaml_file)
    assert set(targets) == {"mine"} and targets["mine"].secret_name == CUSTOM_ID
    assert target_for("mine", targets=targets).base_url == "https://mine.test"


def test_done_closes_without_reading_another_frame() -> None:
    recorder = _Recorder([*SSE_FRAMES, "data: invalid-after-done\n\n"])
    result = recorder.client().generate(BODY)
    assert result.text == "Hello" and result.completion_tokens == 2
    assert recorder.stream is not None and recorder.stream.closed
    assert recorder.stream.yielded == len(SSE_FRAMES)


async def test_async_done_closes_without_reading_another_frame() -> None:
    recorder = _Recorder([*SSE_FRAMES, "data: invalid-after-done\n\n"])
    result = await recorder.client().agenerate(BODY)
    assert result.text == "Hello" and result.completion_tokens == 2
    assert recorder.astream is not None and recorder.astream.closed
    assert recorder.astream.yielded == len(SSE_FRAMES)


def test_precancelled_generation_never_dispatches() -> None:
    recorder = _Recorder()
    token = CancelToken()
    token.cancel("host abort")
    result = recorder.client().generate(BODY, cancel=token)
    assert result.cancelled and result.cancel_reason == "host abort"
    assert not recorder.requests


async def test_async_precancelled_generation_never_dispatches() -> None:
    recorder = _Recorder()
    token = CancelToken()
    token.cancel("host abort")
    result = await recorder.client().agenerate(BODY, cancel=token)
    assert result.cancelled and result.cancel_reason == "host abort"
    assert not recorder.requests
