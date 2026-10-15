"""SSE frame parsing for OpenAI-compatible chat-completions streams (spec §2.3).

Shared by the sync and async paths of :mod:`qr_llm_evals.client.openai_client`
so they cannot drift. Ported from ``qr_llm_research.client.openai_client`` and
extended with:

* **per-chunk logprobs** — ``choices[0].logprobs.content[]`` → a list of
  :class:`TokenLogprob` (token, logprob, top-k alternatives). This is the
  degraded Tier-0 source (spec D-8) when the engine token log is unavailable;
* **reasoning deltas** — vLLM's reasoning parser puts thinking in
  ``delta.reasoning_content`` (some servers use ``delta.reasoning``); both
  surface as :attr:`ChatChunk.delta_thinking` for the supervised viewer;
* the final usage-only frame (``stream_options.include_usage``) is a chunk
  with empty deltas and :attr:`ChatChunk.usage` set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SSE_DATA_PREFIX = "data:"
SSE_DONE = "[DONE]"


@dataclass(frozen=True, slots=True)
class TopLogprob:
    token: str
    logprob: float


@dataclass(frozen=True, slots=True)
class TokenLogprob:
    """One sampled token with its logprob and the top-k alternatives the server returned."""

    token: str
    logprob: float
    top_logprobs: tuple[TopLogprob, ...] = ()
    bytes_: tuple[int, ...] | None = None

    def as_record(self) -> dict[str, Any]:
        """Plain-JSON form for ``tier0.jsonl`` (``api_logprobs`` source)."""
        return {
            "token": self.token,
            "logprob": self.logprob,
            "top_logprobs": [[t.token, t.logprob] for t in self.top_logprobs],
        }


@dataclass(slots=True)
class ChatChunk:
    """One parsed streaming delta.

    Attributes:
        delta_text: Incremental visible text (``""`` for control chunks).
        delta_thinking: Incremental reasoning text (``""`` when absent).
        logprobs: Logprob entries for the tokens in this chunk (empty when the
            server sent none).
        finish_reason: ``"stop"`` / ``"length"`` / … on the terminal chunk.
        usage: Token-usage dict when the server includes one (vLLM: on the
            final frame), else ``None``.
        raw: The full decoded chunk object.
    """

    delta_text: str = ""
    delta_thinking: str = ""
    logprobs: list[TokenLogprob] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_control(self) -> bool:
        """True for frames carrying no text/thinking/logprobs (usage-only, keepalive)."""
        return not (self.delta_text or self.delta_thinking or self.logprobs)


def parse_sse_line(line: str) -> dict[str, Any] | None:
    """Parse one SSE ``data:`` line; ``None`` for blanks, comments and ``[DONE]``."""
    line = line.strip()
    if not line or not line.startswith(SSE_DATA_PREFIX):
        return None
    payload = line[len(SSE_DATA_PREFIX) :].strip()
    if not payload or payload == SSE_DONE:
        return None
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_logprobs(obj: Any) -> list[TokenLogprob]:
    if not isinstance(obj, dict):
        return []
    content = obj.get("content")
    if not isinstance(content, list):
        return []
    out: list[TokenLogprob] = []
    for item in content:
        if not isinstance(item, dict) or "token" not in item:
            continue
        try:
            logprob = float(item.get("logprob", 0.0))
        except (TypeError, ValueError):
            continue
        tops: list[TopLogprob] = []
        for alt in item.get("top_logprobs") or []:
            if isinstance(alt, dict) and "token" in alt:
                try:
                    tops.append(TopLogprob(str(alt["token"]), float(alt.get("logprob", 0.0))))
                except (TypeError, ValueError):
                    continue
        raw_bytes = item.get("bytes")
        bytes_ = (
            tuple(int(b) for b in raw_bytes)
            if isinstance(raw_bytes, list) and all(isinstance(b, int) for b in raw_bytes)
            else None
        )
        out.append(
            TokenLogprob(
                token=str(item["token"]), logprob=logprob, top_logprobs=tuple(tops), bytes_=bytes_
            )
        )
    return out


def chunk_from_obj(obj: dict[str, Any]) -> ChatChunk:
    """Turn a decoded streaming object into a :class:`ChatChunk`."""
    choices = obj.get("choices") or []
    chunk = ChatChunk(
        usage=obj.get("usage") if isinstance(obj.get("usage"), dict) else None, raw=obj
    )
    if choices and isinstance(choices[0], dict):
        choice = choices[0]
        delta = choice.get("delta") or {}
        if isinstance(delta, dict):
            chunk.delta_text = str(delta.get("content") or "")
            thinking = delta.get("reasoning_content")
            if thinking is None:
                thinking = delta.get("reasoning")
            chunk.delta_thinking = str(thinking or "")
        fr = choice.get("finish_reason")
        chunk.finish_reason = str(fr) if fr is not None else None
        chunk.logprobs = _parse_logprobs(choice.get("logprobs"))
    return chunk


def message_from_response(obj: dict[str, Any]) -> ChatChunk:
    """Non-streaming ``choices[0].message`` folded into the same :class:`ChatChunk` shape."""
    choices = obj.get("choices") or []
    chunk = ChatChunk(
        usage=obj.get("usage") if isinstance(obj.get("usage"), dict) else None, raw=obj
    )
    if choices and isinstance(choices[0], dict):
        choice = choices[0]
        message = choice.get("message") or {}
        if isinstance(message, dict):
            chunk.delta_text = str(message.get("content") or "")
            thinking = message.get("reasoning_content")
            if thinking is None:
                thinking = message.get("reasoning")
            chunk.delta_thinking = str(thinking or "")
        fr = choice.get("finish_reason")
        chunk.finish_reason = str(fr) if fr is not None else None
        chunk.logprobs = _parse_logprobs(choice.get("logprobs"))
    return chunk


__all__ = [
    "SSE_DATA_PREFIX",
    "SSE_DONE",
    "ChatChunk",
    "TokenLogprob",
    "TopLogprob",
    "chunk_from_obj",
    "message_from_response",
    "parse_sse_line",
]
