"""Thin ``httpx`` client for an OpenAI-compatible chat-completions endpoint (spec §2.3).

A port of ``qr_llm_research.client.openai_client`` — deliberately *not* the
``openai`` SDK (forbidden by spec §1.2), because the harness needs control the
SDK abstracts away:

1. the non-standard ``vllm_xargs`` / ``priority`` / ``request_id`` /
   ``chat_template_kwargs`` body fields (qr-sampler and vLLM read them);
2. **streaming cancellation** — leaving the ``httpx`` streaming context closes
   the response, which makes vLLM abort the sequence server-side. A
   :class:`~qr_llm_evals.client.cancel.CancelToken` is checked between chunks
   and trips exactly that exit.

Extensions over the research client: per-chunk ``logprobs`` capture,
``reasoning_content`` → ``delta_thinking``, an ``on_delta`` callback for the
event bus, and :meth:`OpenAIClient.generate` / :meth:`OpenAIClient.agenerate`
which stream and fold the chunks into one :class:`Completion`.

The client takes a **prepared body** (``models.envelope.build_request_body``
composes it; ``client`` may not import ``models``) and only fixes the
``stream`` flags. Sync and async variants share the body and SSE helpers so
they cannot drift. Transports are injectable for ``httpx.MockTransport`` tests.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from qr_llm_evals.client.cancel import CancelToken, GenerationCancelled
from qr_llm_evals.client.profiles import CHAT_COMPLETIONS_PATH
from qr_llm_evals.client.sse import (
    ChatChunk,
    TokenLogprob,
    chunk_from_obj,
    message_from_response,
    parse_sse_line,
)

DEFAULT_TIMEOUT_S = 600.0
ERROR_EXCERPT_CHARS = 500

OnDelta = Callable[[ChatChunk], None]


class UpstreamError(Exception):
    """The endpoint answered with a non-2xx status.

    ``excerpt`` is the first :data:`ERROR_EXCERPT_CHARS` of the response body —
    server text only; request headers (the bearer key) are never included.
    """

    def __init__(self, status: int, excerpt: str, *, url: str) -> None:
        super().__init__(f"upstream {status} from {url}: {excerpt}")
        self.status = status
        self.excerpt = excerpt
        self.url = url


@dataclass(slots=True)
class Completion:
    """A finished (or cancelled) generation folded from its stream."""

    text: str = ""
    thinking: str = ""
    logprobs: list[TokenLogprob] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    chunks: int = 0
    cancelled: bool = False
    cancel_reason: str | None = None

    def absorb(self, chunk: ChatChunk) -> None:
        self.text += chunk.delta_text
        self.thinking += chunk.delta_thinking
        self.logprobs.extend(chunk.logprobs)
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason
        if chunk.usage is not None:
            self.usage = chunk.usage
        self.chunks += 1

    @property
    def completion_tokens(self) -> int | None:
        if self.usage and isinstance(self.usage.get("completion_tokens"), int):
            return int(self.usage["completion_tokens"])
        return None


def prepare_body(body: Mapping[str, Any], *, stream: bool) -> dict[str, Any]:
    """Deep-copy ``body`` and pin the ``stream`` flags (``include_usage`` on when streaming)."""
    out: dict[str, Any] = copy.deepcopy(dict(body))
    out["stream"] = stream
    if stream:
        options = dict(out.get("stream_options") or {})
        options.setdefault("include_usage", True)
        out["stream_options"] = options
    else:
        out.pop("stream_options", None)
    return out


def _excerpt(text: str) -> str:
    return text[:ERROR_EXCERPT_CHARS]


class OpenAIClient:
    """Minimal OpenAI-compatible chat client over ``httpx``.

    Args:
        base_url: Endpoint origin (``http://127.0.0.1:8000`` or a provider's
            ``…/v1`` — a trailing ``/v1`` is tolerated). ``/v1/chat/completions``
            is appended.
        api_key: Optional bearer token (none for the loopback vLLM).
        timeout: Per-request timeout; for streams it is the read timeout
            between chunks.
        extra_headers: Provider-specific headers (``ProviderTarget.extra_headers``).
        transport / async_transport: Injectable ``httpx`` transports (tests).
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        extra_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        origin = base_url.strip().rstrip("/")
        if origin.endswith("/v1"):
            origin = origin[: -len("/v1")]
        self.base_url = origin.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.extra_headers: dict[str, str] = dict(extra_headers or {})
        self._transport = transport
        self._async_transport = async_transport

    def __repr__(self) -> str:
        return f"OpenAIClient(base_url={self.base_url!r}, key={'set' if self._api_key else 'none'})"

    # ── headers / url ─────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(self.extra_headers)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @property
    def url(self) -> str:
        return self.base_url + CHAT_COMPLETIONS_PATH

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self._transport)

    def _aclient(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._async_transport)

    # ── non-streaming ─────────────────────────────────────────────────────

    def chat_completion(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Blocking non-streaming completion; returns the decoded JSON body."""
        payload = prepare_body(body, stream=False)
        with self._client() as client:
            resp = client.post(self.url, headers=self._headers(), json=payload)
            if resp.status_code >= 400:
                raise UpstreamError(resp.status_code, _excerpt(resp.text), url=self.url)
            data: dict[str, Any] = resp.json()
            return data

    async def achat_completion(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Async non-streaming completion; returns the decoded JSON body."""
        payload = prepare_body(body, stream=False)
        async with self._aclient() as client:
            resp = await client.post(self.url, headers=self._headers(), json=payload)
            if resp.status_code >= 400:
                raise UpstreamError(resp.status_code, _excerpt(resp.text), url=self.url)
            data: dict[str, Any] = resp.json()
            return data

    def complete(self, body: Mapping[str, Any]) -> Completion:
        """Non-streaming request folded into a :class:`Completion`."""
        completion = Completion()
        completion.absorb(message_from_response(self.chat_completion(body)))
        return completion

    async def acomplete(self, body: Mapping[str, Any]) -> Completion:
        completion = Completion()
        completion.absorb(message_from_response(await self.achat_completion(body)))
        return completion

    # ── streaming ─────────────────────────────────────────────────────────

    def stream_chat_completion(
        self, body: Mapping[str, Any], *, cancel: CancelToken | None = None
    ) -> Iterator[ChatChunk]:
        """Blocking stream of :class:`ChatChunk`.

        Closing the generator early, or a tripped ``cancel`` token (checked
        before every frame), exits the ``httpx`` stream context and aborts the
        upstream sequence.
        """
        if cancel is not None:
            cancel.raise_if_cancelled()
        payload = prepare_body(body, stream=True)
        with (
            self._client() as client,
            client.stream("POST", self.url, headers=self._headers(), json=payload) as resp,
        ):
            if resp.status_code >= 400:
                resp.read()
                raise UpstreamError(resp.status_code, _excerpt(resp.text), url=self.url)
            for line in resp.iter_lines():
                if cancel is not None:
                    cancel.raise_if_cancelled()
                if line.strip() == "data: [DONE]":
                    break
                obj = parse_sse_line(line)
                if obj is not None:
                    yield chunk_from_obj(obj)
            if cancel is not None:
                cancel.raise_if_cancelled()

    async def astream_chat_completion(
        self, body: Mapping[str, Any], *, cancel: CancelToken | None = None
    ) -> AsyncIterator[ChatChunk]:
        """Async stream of :class:`ChatChunk`; same cancellation semantics as the sync path."""
        if cancel is not None:
            cancel.raise_if_cancelled()
        payload = prepare_body(body, stream=True)
        async with (
            self._aclient() as client,
            client.stream("POST", self.url, headers=self._headers(), json=payload) as resp,
        ):
            if resp.status_code >= 400:
                await resp.aread()
                raise UpstreamError(resp.status_code, _excerpt(resp.text), url=self.url)
            async for line in resp.aiter_lines():
                if cancel is not None:
                    cancel.raise_if_cancelled()
                if line.strip() == "data: [DONE]":
                    break
                obj = parse_sse_line(line)
                if obj is not None:
                    yield chunk_from_obj(obj)
            if cancel is not None:
                cancel.raise_if_cancelled()

    # ── stream + fold ─────────────────────────────────────────────────────

    def generate(
        self,
        body: Mapping[str, Any],
        *,
        on_delta: OnDelta | None = None,
        cancel: CancelToken | None = None,
    ) -> Completion:
        """Stream, forward every chunk to ``on_delta``, fold into a :class:`Completion`.

        A tripped ``cancel`` returns the partial completion with
        ``cancelled=True`` (the stream was closed, so the engine aborted the
        sequence); it does not raise. Upstream errors do raise.
        """
        completion = Completion()
        try:
            for chunk in self.stream_chat_completion(body, cancel=cancel):
                completion.absorb(chunk)
                if on_delta is not None:
                    on_delta(chunk)
        except GenerationCancelled as err:
            completion.cancelled = True
            completion.cancel_reason = err.reason
            completion.finish_reason = "cancelled"
        return completion

    async def agenerate(
        self,
        body: Mapping[str, Any],
        *,
        on_delta: OnDelta | None = None,
        cancel: CancelToken | None = None,
    ) -> Completion:
        """Async twin of :meth:`generate`."""
        completion = Completion()
        try:
            async for chunk in self.astream_chat_completion(body, cancel=cancel):
                completion.absorb(chunk)
                if on_delta is not None:
                    on_delta(chunk)
        except GenerationCancelled as err:
            completion.cancelled = True
            completion.cancel_reason = err.reason
            completion.finish_reason = "cancelled"
        return completion


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "Completion",
    "OnDelta",
    "OpenAIClient",
    "UpstreamError",
    "prepare_body",
]
