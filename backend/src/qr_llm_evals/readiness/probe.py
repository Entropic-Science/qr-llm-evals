"""One-token (and short) probe generations against the qr-llm target (spec §2.7).

Every readiness step that talks to the engine goes through :class:`EngineProbe`
so the body is always composed by ``models.envelope.build_request_body`` (the
harness owns the envelope, AGENTS.md invariant 1) and every request carries a
``readiness/…`` ``request_id`` the engine log and the test stub can recognise.

``extra_xargs`` exists for the two *capability* probes the spec names — is
``qr_token_log`` (DEP-1) accepted, is ``qr_bias_epsilon`` (DEP-2) accepted —
which by design are keys the card-override path refuses. They are added after
the envelope is built, by readiness only; nothing user-supplied reaches them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from qr_llm_evals.client.openai_client import Completion, OpenAIClient, UpstreamError
from qr_llm_evals.models.envelope import TOP_LOGPROBS, SampleContext, build_request_body
from qr_llm_evals.models.schema import Arm, ModelRef

logger = logging.getLogger("qr_llm_evals.readiness")

#: Prefix of every readiness ``request_id`` (engine-log file names start with it).
REQUEST_ID_PREFIX: Final[str] = "readiness"
#: The one prompt every probe sends; thinking stays off (``SampleContext.thinking``).
PROBE_MESSAGES: Final[list[dict[str, Any]]] = [
    {"role": "user", "content": "Reply with a single word."}
]
#: HTTP status qr-sampler's per-request validation answers with for an unknown/refused key.
REJECTED_STATUS: Final[int] = 400


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What one probe request came back with."""

    request_id: str
    accepted: bool
    status: int | None = None
    reason: str | None = None
    completion: Completion | None = None

    @property
    def rejected_by_validation(self) -> bool:
        """``True`` only for a 400 — the engine understood the request and refused a key."""
        return self.status == REJECTED_STATUS

    @property
    def token_signature(self) -> tuple[str, ...]:
        """The generated token sequence (logprob tokens when present, else the text)."""
        if self.completion is None:
            return ()
        if self.completion.logprobs:
            return tuple(lp.token for lp in self.completion.logprobs)
        return (self.completion.text,) if self.completion.text else ()

    @property
    def completion_tokens(self) -> int:
        if self.completion is None:
            return 0
        n = self.completion.completion_tokens
        if n is not None:
            return n
        return len(self.completion.logprobs) or len(self.completion.text.split())


def probe_context(prompt_id: str, *, max_tokens: int = 1) -> SampleContext:
    """A ``SampleContext`` under the reserved ``readiness`` card id.

    The seeded arms' ``qr_seed`` derives from ``(study_seed, card_id, prompt_id,
    epoch)``, so replays that must share a seed share the *context* and differ
    only in the harness-owned ``request_id``.
    """
    return SampleContext(
        card_id=REQUEST_ID_PREFIX, prompt_id=prompt_id, epoch=0, max_tokens=max_tokens
    )


def request_id(arm_id: str, *parts: str | int) -> str:
    """Readable logical prefix; ``generate`` appends a unique dispatch suffix."""
    return "/".join([REQUEST_ID_PREFIX, arm_id, *(str(p) for p in parts)])


class EngineProbe:
    """Sends prepared bodies to the shared vLLM and classifies the answer."""

    def __init__(
        self,
        client: OpenAIClient,
        models: Mapping[str, ModelRef],
        *,
        priority: int = 0,
        top_logprobs: int = TOP_LOGPROBS,
    ) -> None:
        self._client = client
        self._models = dict(models)
        self._priority = priority
        # Probe with the depth the run will request, so an engine whose
        # --max-logprobs is below it fails readiness instead of every generation.
        self._top_logprobs = top_logprobs

    def body(
        self,
        arm: Arm,
        ctx: SampleContext,
        *,
        request_id: str,
        extra_xargs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = self._models[arm.model]
        body = build_request_body(
            arm,
            model,
            ctx,
            list(PROBE_MESSAGES),
            priority=self._priority,
            stream=True,
            top_logprobs=self._top_logprobs,
        )
        body["request_id"] = request_id
        if extra_xargs:
            xargs = dict(body.get("vllm_xargs") or {})
            xargs.update(extra_xargs)
            body["vllm_xargs"] = xargs
        return body

    def generate(
        self,
        arm: Arm,
        ctx: SampleContext,
        *,
        request_id: str,
        extra_xargs: Mapping[str, Any] | None = None,
    ) -> ProbeResult:
        """Stream one generation; never raises for engine-side answers."""
        # A replay repeats the sampling context, never the engine's routing ID.
        # Also prevent a prior capability probe's token log from passing this run.
        request_id = f"{request_id}/{uuid.uuid4().hex}"
        body = self.body(arm, ctx, request_id=request_id, extra_xargs=extra_xargs)
        try:
            completion = self._client.generate(body)
        except UpstreamError as err:
            logger.info("readiness probe %s rejected: %s %s", request_id, err.status, err.excerpt)
            return ProbeResult(
                request_id=request_id,
                accepted=False,
                status=err.status,
                reason=f"engine answered {err.status}: {err.excerpt}",
            )
        except httpx.HTTPError as err:
            logger.warning("readiness probe %s failed: %s", request_id, err)
            return ProbeResult(
                request_id=request_id,
                accepted=False,
                reason=f"engine unreachable: {type(err).__name__}: {err}",
            )
        if completion.cancelled or completion.finish_reason not in {"stop", "length"}:
            return ProbeResult(
                request_id=request_id,
                accepted=False,
                reason="stream did not finish successfully",
                completion=completion,
            )
        return ProbeResult(request_id=request_id, accepted=True, status=200, completion=completion)


__all__ = [
    "PROBE_MESSAGES",
    "REJECTED_STATUS",
    "REQUEST_ID_PREFIX",
    "EngineProbe",
    "ProbeResult",
    "probe_context",
    "request_id",
]
