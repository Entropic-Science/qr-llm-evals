"""Anthropic Messages API judge client (spec §2.3, D-9).

The judge is a native Messages-API call over ``httpx`` (``POST /v1/messages``),
never an experimental sampler and never an SDK (D-9: "thin httpx keeps body
control"). What pins a judge, all frozen in the pre-registration:

* the exact model string (:data:`DEFAULT_JUDGE_MODEL`, the current Claude
  Haiku, unless the study names another);
* a **frozen system prompt** (the rubric; :attr:`JudgeRequest.system_sha256`
  goes into the prereg) — the "seed-equivalent", since Messages has no seed;
* ``metadata.user_id = judge_run_id`` so every judge call of one run is
  attributable upstream;
* the call parameters the study chose: ``max_tokens``, ``thinking`` and
  ``temperature``.

``temperature`` is sent only when the study sets one. Current Claude models
(Haiku 5.5, Sonnet 5 and later, Opus 4.7 and later) answer a non-default
sampling parameter with a 400, so the default sends none and the API, not the
harness, fixes the sampling. ``thinking`` is ``{"type": "disabled"}`` by
default, which keeps the judge a single pass; models that refuse that value
need ``thinking=None``, which sends nothing.

Errors surface as :class:`JudgeError` with the status and a body excerpt
(server text only; the ``x-api-key`` header never enters any message). ``429``
and ``5xx`` are retried with the ``retry-after`` hint, at most ``max_retries``.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_JUDGE_MODEL = "claude-haiku-5-5"
DEFAULT_BASE_URL = "https://api.anthropic.com"
MESSAGES_PATH = "/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024
THINKING_DISABLED = "disabled"
DEFAULT_TIMEOUT_S = 120.0
ERROR_EXCERPT_CHARS = 500
RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})


class JudgeError(Exception):
    """The Messages API answered with a non-2xx status (after retries)."""

    def __init__(self, status: int, excerpt: str) -> None:
        super().__init__(f"anthropic judge {status}: {excerpt}")
        self.status = status
        self.excerpt = excerpt


@dataclass(frozen=True, slots=True)
class JudgeRequest:
    """One judge call: frozen ``system`` (rubric) + the ``user`` turn to score."""

    system: str
    user: str
    judge_run_id: str
    #: Per-call override of the client's ``max_tokens``; ``None`` uses the client's.
    max_tokens: int | None = None
    stop_sequences: tuple[str, ...] = ()

    @property
    def system_sha256(self) -> str:
        """Hash of the frozen system prompt (UTF-8 bytes) — recorded in the prereg."""
        return hashlib.sha256(self.system.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class JudgeResponse:
    """Text blocks concatenated, plus the metadata the analysis keeps."""

    text: str
    model: str
    stop_reason: str | None
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _concat_text_blocks(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _retry_after_s(resp: httpx.Response, attempt: int) -> float:
    hint = resp.headers.get("retry-after")
    if hint:
        try:
            return min(60.0, max(0.0, float(hint)))
        except ValueError:
            pass
    return float(min(30, 2**attempt))


class AnthropicJudgeClient:
    """Deterministic judge calls against the Messages API with a BYO key."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_JUDGE_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = 2,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        thinking: str | None = THINKING_DISABLED,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        sleep: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("anthropic judge needs an API key (from the run's SecretBag)")
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking = thinking
        self._transport = transport
        self._async_transport = async_transport
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"AnthropicJudgeClient(model={self.model!r}, key=set)"

    @property
    def url(self) -> str:
        return self.base_url + MESSAGES_PATH

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
        }

    def build_body(self, req: JudgeRequest) -> dict[str, Any]:
        """The exact request body (pure; tests and the prereg pin it)."""
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens or self.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
            "metadata": {"user_id": req.judge_run_id},
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.thinking is not None:
            body["thinking"] = {"type": self.thinking}
        if req.stop_sequences:
            body["stop_sequences"] = list(req.stop_sequences)
        return body

    @staticmethod
    def _parse(resp: httpx.Response) -> JudgeResponse:
        data: dict[str, Any] = resp.json()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return JudgeResponse(
            text=_concat_text_blocks(data),
            model=str(data.get("model") or ""),
            stop_reason=data.get("stop_reason"),
            usage=dict(usage or {}),
            request_id=resp.headers.get("request-id"),
            raw=data,
        )

    def judge(self, req: JudgeRequest) -> JudgeResponse:
        """Blocking judge call with bounded retries on 429/5xx."""
        body = self.build_body(req)
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            attempt = 0
            while True:
                resp = client.post(self.url, headers=self._headers(), json=body)
                if resp.status_code < 400:
                    return self._parse(resp)
                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    if self._sleep:
                        time.sleep(_retry_after_s(resp, attempt))
                    attempt += 1
                    continue
                raise JudgeError(resp.status_code, resp.text[:ERROR_EXCERPT_CHARS])

    async def ajudge(self, req: JudgeRequest) -> JudgeResponse:
        """Async twin of :meth:`judge`."""
        body = self.build_body(req)
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self._async_transport
        ) as client:
            attempt = 0
            while True:
                resp = await client.post(self.url, headers=self._headers(), json=body)
                if resp.status_code < 400:
                    return self._parse(resp)
                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    if self._sleep:
                        await asyncio.sleep(_retry_after_s(resp, attempt))
                    attempt += 1
                    continue
                raise JudgeError(resp.status_code, resp.text[:ERROR_EXCERPT_CHARS])


__all__ = [
    "ANTHROPIC_VERSION",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_MAX_TOKENS",
    "THINKING_DISABLED",
    "AnthropicJudgeClient",
    "JudgeError",
    "JudgeRequest",
    "JudgeResponse",
]
