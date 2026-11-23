"""Generation and judge HTTP clients (spec §2.3, D-9).

Layer 0: imports nothing internal except :mod:`qr_llm_evals.config`. The
request *body* is composed by ``models.envelope`` (also layer 0); this package
only puts it on the wire, parses the stream, and cancels it.
"""

from __future__ import annotations

from qr_llm_evals.client.anthropic_judge import (
    DEFAULT_JUDGE_MODEL,
    AnthropicJudgeClient,
    JudgeError,
    JudgeRequest,
    JudgeResponse,
)
from qr_llm_evals.client.cancel import CancelToken, GenerationCancelled
from qr_llm_evals.client.openai_client import Completion, OnDelta, OpenAIClient, UpstreamError
from qr_llm_evals.client.profiles import ProviderTarget, qr_server_target, target_for
from qr_llm_evals.client.sse import ChatChunk, MalformedFrameError, TokenLogprob, TopLogprob

__all__ = [
    "DEFAULT_JUDGE_MODEL",
    "AnthropicJudgeClient",
    "CancelToken",
    "ChatChunk",
    "Completion",
    "GenerationCancelled",
    "JudgeError",
    "JudgeRequest",
    "JudgeResponse",
    "MalformedFrameError",
    "OnDelta",
    "OpenAIClient",
    "ProviderTarget",
    "TokenLogprob",
    "TopLogprob",
    "UpstreamError",
    "qr_server_target",
    "target_for",
]
