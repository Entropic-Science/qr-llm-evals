"""Deterministic fake vLLM + qr-sampler for runner tests (spec §5.2, §5.1 "stub engine").

``StubEngine.transport`` is an ``httpx.MockTransport`` serving:

* ``POST /v1/chat/completions`` — validates the body like qr-sampler would
  (``400`` for unknown ``qr_*`` keys in ``vllm_xargs``), then streams SSE
  chunks with ``logprobs`` / ``top_logprobs``, optional ``reasoning_content``
  (when ``chat_template_kwargs.enable_thinking``), a ``finish_reason`` chunk,
  a usage frame and ``[DONE]``. Output is a pure function of ``qr_seed`` when
  present, else of the ``request_id`` — so two runs of the same study produce
  identical bytes and the same seed yields the same text (D-6 replayability).
* ``GET /health/entropy`` — ``rpc_ok``, ``currently_degraded``,
  ``fallback_count``, ``source_type``; ``fallback_refs`` bump the counter while
  serving those requests, ``degrade_refs`` raise the degraded flag until the
  next health read after the request finished, so a taint lands on exactly
  the affected sample.

Knobs: ``chunk_delay_s`` (lets tests pause/abort mid-stream), ``fail_refs``
(``500``), ``on_request`` (hook to drive ``RunControl`` from inside the
engine), and ``closed_early`` records streams the client closed before
completion (abort ⇒ engine-side sequence abort).

``script`` makes one request misbehave the way a real engine or proxy can: it
is called with the request body and answers with a :class:`Reply` (an HTTP
status, a fixed text, another finish reason or none, a frame that is not JSON,
tokens without a log-probability, reasoning text), or ``None`` for the normal
deterministic reply.

``StubJudge`` is the Anthropic Messages API twin: ``SCORE: <n>`` derived from
the user text, with ``fail_first`` calls answering ``500``. Its ``script``
answers one call with a :class:`JudgeSay` (a status, a text, a stop reason).
The request the harness makes before the first generation is recognised by
its user text and kept in ``preflights``, apart from the judgements in
``calls``.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

#: Per-request ``qr_*`` keys the real plugin accepts (mirror of qr-sampler's allowlist).
ALLOWED_QR_KEYS: frozenset[str] = frozenset(
    {
        "qr_entropy_source_type",
        "qr_preset",
        "qr_seed",
        "qr_bypass",
        "qr_bias_epsilon",
        "qr_population_mean",
        "qr_population_std",
        "qr_signal_amplifier_type",
        "qr_zscore_calibration_samples",
        "qr_entropy_prefetch",
        "qr_sample_count",
        "qr_conditioning_mode",
        "qr_conditioning_hash",
        "qr_conditioning_in_bytes",
        "qr_conditioning_keep_bytes",
        "qr_top_p",
        "qr_min_p_base",
        "qr_fixed_temperature",
        "qr_temperature_strategy",
        "qr_token_log",
        "qr_request_id",
    }
)

WORDS = (
    "quantum", "entropy", "branch", "observer", "stream", "token", "sample", "field",
    "signal", "noise", "measure", "choice", "path", "collapse", "coherent", "random",
)  # fmt: skip


#: ``Reply.finish`` value that keeps the finish reason the stub would give.
DEFAULT_FINISH = "default"
#: User text of the request the harness makes before the first generation
#: (``runner.judge_phase.PREFLIGHT_USER``; a test pins that they are equal).
PREFLIGHT_USER = "Reply with the single word OK."


@dataclass(frozen=True)
class Reply:
    """How the engine answers one request when a test scripts it."""

    #: Not 200: the request is answered with this status and ``error_body``.
    status: int = 200
    error_body: str = "stub engine failure"
    #: The visible reply, split into tokens at spaces. ``None`` keeps the seeded words.
    text: str | None = None
    #: ``None`` ends the stream without a finish reason.
    finish: str | None = DEFAULT_FINISH
    #: Reasoning text, sent before the reply as ``reasoning_content``.
    thinking: str | None = None
    #: Put a ``data:`` line that is not JSON after the first token.
    malformed: bool = False
    #: Send the tokens without a ``logprob`` key.
    drop_logprob: bool = False


def _seed_for(body: dict[str, Any]) -> int:
    xargs = body.get("vllm_xargs") or {}
    if isinstance(xargs.get("qr_seed"), int):
        return int(xargs["qr_seed"])
    key = str(body.get("request_id") or json.dumps(body.get("messages"), sort_keys=True))
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def _sse(obj: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n"


class _FrameStream(httpx.SyncByteStream):
    """Yields SSE frames with an optional delay; records early close."""

    def __init__(
        self, frames: list[bytes], delay_s: float, on_close_early: Callable[[], None]
    ) -> None:
        self._frames = frames
        self._delay = delay_s
        self._on_close_early = on_close_early
        self._done = False

    def __iter__(self) -> Iterator[bytes]:
        for frame in self._frames:
            if self._delay:
                time.sleep(self._delay)
            yield frame
        self._done = True

    def close(self) -> None:
        if not self._done:
            self._on_close_early()


class StubEngine:
    def __init__(
        self,
        *,
        n_tokens: int = 6,
        chunk_delay_s: float = 0.0,
        fail_refs: Iterable[str] = (),
        fallback_refs: Iterable[str] = (),
        degrade_refs: Iterable[str] = (),
        on_request: Callable[[dict[str, Any]], None] | None = None,
        allowed_qr_keys: frozenset[str] = ALLOWED_QR_KEYS,
        source_type: str = "quantum_grpc",
        script: Callable[[dict[str, Any]], Reply | None] | None = None,
    ) -> None:
        self.n_tokens = n_tokens
        self.chunk_delay_s = chunk_delay_s
        self.fail_refs = set(fail_refs)
        self.fallback_refs = set(fallback_refs)
        self.degrade_refs = set(degrade_refs)
        self.on_request = on_request
        self.allowed_qr_keys = allowed_qr_keys
        self.source_type = source_type
        self.script = script
        self.requests: list[dict[str, Any]] = []
        self.closed_early: set[str] = set()
        self.health_reads = 0
        self.fallback_count = 0
        self._degraded_pending: set[str] = set()
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @property
    def request_ids(self) -> list[str]:
        return [str(b.get("request_id")) for b in self.requests]

    # ── routing ───────────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/entropy" and request.method == "GET":
            return self._health()
        if request.url.path == "/v1/chat/completions" and request.method == "POST":
            return self._chat(request)
        return httpx.Response(404, json={"error": {"message": "not found"}})

    def _health(self) -> httpx.Response:
        with self._lock:
            self.health_reads += 1
            degraded = bool(self._degraded_pending)
            # clear flags whose request already finished (they were observed once)
            self._degraded_pending = {r for r in self._degraded_pending if r in self._in_flight}
            body = {
                "rpc_ok": True,
                "currently_degraded": degraded,
                "fallback_count": self.fallback_count,
                "source_type": self.source_type,
            }
        return httpx.Response(200, json=body)

    def _chat(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        ref = str(body.get("request_id") or _seed_for(body))
        with self._lock:
            self.requests.append(body)
        problems = self._validate(body)
        if problems:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "; ".join(problems),
                        "type": "invalid_request_error",
                        "code": 400,
                    }
                },
            )
        if self.on_request is not None:
            self.on_request(body)
        if ref in self.fail_refs:
            return httpx.Response(500, json={"error": {"message": "stub engine failure"}})
        reply = self.script(body) if self.script is not None else None
        if reply is not None and reply.status != 200:
            return httpx.Response(reply.status, json={"error": {"message": reply.error_body}})
        with self._lock:
            self._in_flight.add(ref)
            if ref in self.fallback_refs:
                self.fallback_count += 1
            if ref in self.degrade_refs:
                self._degraded_pending.add(ref)
        # Subclasses in other tests override ``_frames(body)``; only a scripted
        # request needs the second argument.
        frames = self._frames(body) if reply is None else self._frames(body, reply)

        def on_close_early() -> None:
            with self._lock:
                self.closed_early.add(ref)
                self._in_flight.discard(ref)

        stream = _FrameStreamFinishing(
            frames, self.chunk_delay_s, on_close_early, lambda: self._finish(ref)
        )
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})

    def _finish(self, ref: str) -> None:
        with self._lock:
            self._in_flight.discard(ref)

    def _validate(self, body: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            problems.append("messages required")
        xargs = body.get("vllm_xargs")
        if xargs is not None:
            if not isinstance(xargs, dict):
                problems.append("vllm_xargs must be an object")
            else:
                unknown = sorted(k for k in xargs if k not in self.allowed_qr_keys)
                if unknown:
                    problems.append(f"unknown qr_* keys: {unknown}")
                if xargs.get("qr_bypass") and "qr_seed" in xargs:
                    problems.append("qr_seed is not allowed with qr_bypass")
        return problems

    # ── content ───────────────────────────────────────────────────────────

    def _frames(self, body: dict[str, Any], reply: Reply | None = None) -> list[bytes]:
        rng = random.Random(_seed_for(body))  # noqa: S311 - deterministic fixture, not crypto
        max_tokens = int(body.get("max_tokens") or self.n_tokens)
        n = min(self.n_tokens, max_tokens) if max_tokens > 0 else self.n_tokens
        thinking = bool((body.get("chat_template_kwargs") or {}).get("enable_thinking"))
        want_logprobs = bool(body.get("logprobs"))
        cid = f"chatcmpl-{rng.getrandbits(32):08x}"
        frames: list[bytes] = []
        if thinking:
            for piece in ("considering the prompt", "answering"):
                frames.append(_chunk(cid, body, {"reasoning_content": piece + " "}))
        if reply is not None and reply.thinking:
            frames.append(_chunk(cid, body, {"reasoning_content": reply.thinking}))
        scripted: list[str] | None = None
        if reply is not None and reply.text is not None:
            pieces = reply.text.split(" ") if reply.text else []
            scripted = [piece if i == 0 else " " + piece for i, piece in enumerate(pieces)]
            n = len(scripted)
        for i in range(n):
            word = rng.choice(WORDS)
            text = word if i == 0 else " " + word
            if scripted is not None:
                text = scripted[i]
            delta = {"content": text}
            logprobs = None
            if want_logprobs:
                lp = -rng.random() * 3
                alts = [[rng.choice(WORDS), lp - rng.random()] for _ in range(3)]
                entry: dict[str, Any] = {
                    "token": text,
                    "logprob": lp,
                    "top_logprobs": [{"token": text, "logprob": lp}]
                    + [{"token": t, "logprob": lp_alt} for t, lp_alt in alts],
                }
                if reply is not None and reply.drop_logprob:
                    del entry["logprob"]
                logprobs = {"content": [entry]}
            frames.append(_chunk(cid, body, delta, logprobs=logprobs))
            if i == 0 and reply is not None and reply.malformed:
                frames.append(b'data: {"id": "chatcmpl-cut", "choices": [{"delta": {"cont\n\n')
        finish: str | None = "length" if n >= max_tokens > 0 else "stop"
        if reply is not None and reply.finish != DEFAULT_FINISH:
            finish = reply.finish
        if finish is not None:
            frames.append(_chunk(cid, body, {}, finish_reason=finish))
        prompt_tokens = sum(len(str(m.get("content", ""))) // 4 for m in body["messages"])
        frames.append(
            _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "model": body.get("model"),
                    "choices": [],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": n + (2 if thinking else 0),
                        "total_tokens": prompt_tokens + n,
                    },
                }
            )
        )
        frames.append(b"data: [DONE]\n\n")
        return frames


class _FrameStreamFinishing(_FrameStream):
    def __init__(
        self,
        frames: list[bytes],
        delay_s: float,
        on_close_early: Callable[[], None],
        on_finish: Callable[[], None],
    ) -> None:
        super().__init__(frames, delay_s, on_close_early)
        self._on_finish = on_finish

    def __iter__(self) -> Iterator[bytes]:
        yield from super().__iter__()
        self._on_finish()


def _chunk(
    cid: str,
    body: dict[str, Any],
    delta: dict[str, Any],
    *,
    logprobs: dict[str, Any] | None = None,
    finish_reason: str | None = None,
) -> bytes:
    choice: dict[str, Any] = {"index": 0, "delta": delta, "finish_reason": finish_reason}
    if logprobs is not None:
        choice["logprobs"] = logprobs
    return _sse(
        {
            "id": cid,
            "object": "chat.completion.chunk",
            "model": body.get("model"),
            "choices": [choice],
        }
    )


@dataclass(frozen=True)
class JudgeSay:
    """How the judge answers one call when a test scripts it."""

    #: Not 200: the call is answered with this status and ``error_body``.
    status: int = 200
    error_body: str = "stub judge failure"
    #: The reply text. ``None`` keeps the deterministic ``SCORE: <n>``.
    text: str | None = None
    stop_reason: str = "end_turn"


class StubJudge:
    """Anthropic Messages API stub: deterministic ``SCORE: <n>`` per user text.

    ``fail_first`` makes the first N *distinct* samples (by user text) answer
    ``500`` on every attempt, so the client's retries cannot mask the failure.
    ``script`` is called with the request body of every call, the one before
    the first generation included, and answers with a :class:`JudgeSay` or
    ``None`` for the normal reply.
    """

    def __init__(
        self,
        *,
        fail_first: int = 0,
        script: Callable[[dict[str, Any]], JudgeSay | None] | None = None,
    ) -> None:
        self.fail_first = fail_first
        self.script = script
        self.calls: list[dict[str, Any]] = []
        #: Requests made before the first generation; not judgements of a sample.
        self.preflights: list[dict[str, Any]] = []
        #: The ``x-api-key`` of every request, in order.
        self.keys: list[str] = []
        self._failing: dict[str, None] = {}
        self._lock = threading.Lock()

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        user = str(body["messages"][0]["content"])
        preflight = user == PREFLIGHT_USER
        with self._lock:
            self.keys.append(request.headers.get("x-api-key", ""))
            if preflight:
                self.preflights.append(body)
            else:
                self.calls.append(body)
                if user not in self._failing and len(self._failing) < self.fail_first:
                    self._failing[user] = None
            failing = user in self._failing
        if "x-api-key" not in request.headers:
            return httpx.Response(401, json={"error": {"message": "missing key"}})
        say = self.script(body) if self.script is not None else None
        if say is not None and say.status != 200:
            return httpx.Response(
                say.status, json={"error": {"type": "api_error", "message": say.error_body}}
            )
        if failing:
            return httpx.Response(500, json={"error": {"type": "api_error", "message": "boom"}})
        score = int(hashlib.sha256(user.encode("utf-8")).digest()[0]) % 10 + 1
        text = "OK" if preflight else f"Reasoning.\nSCORE: {score}"
        if say is not None and say.text is not None:
            text = say.text
        return httpx.Response(
            200,
            json={
                "id": "msg_stub",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": [{"type": "text", "text": text}],
                "stop_reason": say.stop_reason if say is not None else "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )


__all__ = ["ALLOWED_QR_KEYS", "JudgeSay", "Reply", "StubEngine", "StubJudge"]
