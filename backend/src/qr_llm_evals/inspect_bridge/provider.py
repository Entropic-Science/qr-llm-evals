"""Inspect providers using the harness dispatch callback (never a provider SDK).

In production the callback is an IPC proxy to the thin runner in the parent.
This keeps keys in SecretBag and makes generation/Tier-0 rows identical by
construction, rather than maintaining a second provenance implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from inspect_ai.model import (
    ChatCompletionChoice,
    ChatMessage,
    ChatMessageAssistant,
    ContentReasoning,
    ContentText,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    modelapi,
)
from inspect_ai.tool import ToolChoice, ToolInfo

from qr_llm_evals.models.envelope import SampleContext as EnvelopeContext
from qr_llm_evals.models.envelope import build_envelope, request_seed
from qr_llm_evals.models.schema import Arm
from qr_llm_evals.store.schema import GenerationRow


@dataclass(frozen=True, kw_only=True)
class SampleContext(EnvelopeContext):
    arm: Arm


sample_context: ContextVar[SampleContext] = ContextVar("qr_sample_context")
Dispatch = Callable[[EnvelopeContext, list[dict[str, str]]], GenerationRow]
Grader = Callable[[list[dict[str, str]]], str]


@dataclass(frozen=True)
class Runtime:
    arm: Arm
    dispatch: Dispatch
    grader: Grader | None = None


runtime: ContextVar[Runtime] = ContextVar("qr_inspect_runtime")


@contextmanager
def bind_runtime(value: Runtime) -> Iterator[None]:
    token = runtime.set(value)
    try:
        yield
    finally:
        runtime.reset(token)


def text_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
    result = []
    for message in messages:
        if message.role not in {"user", "system", "assistant"} or not isinstance(
            message.content, str
        ):
            raise ValueError("qrllm supports plain text messages only")
        result.append({"role": message.role, "content": message.content})
    return result


@modelapi(name="qrllm")
class QrLlmModelAPI(ModelAPI):
    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        ctx = sample_context.get()  # Fail closed outside the prepended solver.
        bound = runtime.get()
        if self.model_name != bound.arm.id or ctx.arm != bound.arm:
            raise ValueError("Inspect arm/context mismatch")
        if tools or config.num_choices not in (None, 1):
            raise ValueError("qrllm requires one free generation per epoch, without tools")
        # Validate the same envelope/seed here; dispatch builds the actual body.
        # Never forward Inspect extra_body, temperature, seed or other overrides.
        if ctx.arm.source != "provider":
            build_envelope(ctx.arm, ctx)
        seed = request_seed(ctx.arm, ctx)
        row = await asyncio.to_thread(bound.dispatch, ctx, text_messages(input))
        if row.sample_ref != ctx.sample_ref(ctx.arm.id) or row.qr_seed != seed:
            raise ValueError("harness returned a mismatched sample")
        if row.finish_reason not in ("stop", "length"):
            raise RuntimeError("harness generation did not complete")
        return ModelOutput(
            model=self.model_name,
            choices=[
                ChatCompletionChoice(
                    message=ChatMessageAssistant(
                        content=(
                            [ContentReasoning(reasoning=row.thinking), ContentText(text=row.output)]
                            if row.thinking
                            else row.output
                        )
                    ),
                    stop_reason="max_tokens" if row.finish_reason == "length" else "stop",
                )
            ],
            usage=ModelUsage(
                input_tokens=row.usage.prompt_tokens,
                output_tokens=row.usage.completion_tokens,
                total_tokens=row.usage.prompt_tokens + row.usage.completion_tokens,
            ),
        )


@modelapi(name="qrgrader")
class QrGraderModelAPI(ModelAPI):
    """Fixed judge via parent callback; keys never enter Inspect logs/model args."""

    async def generate(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        grader = runtime.get().grader
        if grader is None or config.temperature != 0 or tools:
            raise ValueError("a fixed temperature-zero harness grader is required")
        text = await asyncio.to_thread(grader, text_messages(input))
        return ModelOutput(
            model=self.model_name,
            choices=[
                ChatCompletionChoice(message=ChatMessageAssistant(content=text), stop_reason="stop")
            ],
        )
