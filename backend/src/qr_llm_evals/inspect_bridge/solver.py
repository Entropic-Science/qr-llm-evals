"""Sample-scoped context with explicit Inspect (1-based) to harness epoch mapping."""

from inspect_ai.solver import Generate, Solver, TaskState, solver

from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.inspect_bridge.provider import SampleContext, runtime, sample_context


@solver
def qr_sample_context(card: EvalCard) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        prompt_id = str(state.sample_id)
        if not prompt_id or any(c in prompt_id for c in "/\\|") or state.epoch < 1:
            raise ValueError("invalid Inspect sample id/epoch")
        sample_context.set(
            SampleContext(
                card_id=card.id,
                prompt_id=prompt_id,
                epoch=state.epoch - 1,
                max_tokens=card.max_tokens,
                thinking=card.thinking,
                arm=runtime.get().arm,
            )
        )
        state.metadata["qr_join_key"] = [card.id, prompt_id, state.epoch - 1]
        return state

    return solve
