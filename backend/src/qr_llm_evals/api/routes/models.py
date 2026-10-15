"""``GET /models`` — the catalogue (qr-llm lanes + external targets) and provider profiles."""

from __future__ import annotations

from fastapi import APIRouter

from qr_llm_evals.api.deps import State
from qr_llm_evals.api.schema import ModelEntryView, ModelsResponse, ProviderProfileView

router = APIRouter(tags=["models"])

#: Which BYO key slot a provider draws on (``qr_server`` needs none: it is on loopback).
KEY_SLOT_BY_PROVIDER: dict[str, str | None] = {
    "qr_server": None,
    "openrouter": "openrouter",
    "openai": "openai",
    "anthropic_compat": "anthropic",
    "custom": "custom",
}


@router.get("/models", response_model=ModelsResponse)
async def list_models(state: State) -> ModelsResponse:
    models = [
        ModelEntryView(
            id=e.id,
            family=e.family,
            provider=e.provider,
            served_name=e.served_name,
            checkpoint=e.checkpoint,
            tps=e.tps,
            pipeline_pinned=e.pipeline_pinned,
            description=e.description,
            key_slot=KEY_SLOT_BY_PROVIDER.get(e.provider),
        )
        for e in state.catalogue.entries
    ]
    providers = [
        ProviderProfileView(
            id=p.id,
            base_url=p.base_url,
            accepted_params=list(p.accepted_params),
            seed_honoured=p.seed_honoured,
            logprobs_honoured=p.logprobs_honoured,
            top_logprobs_cap=p.top_logprobs_cap,
            notes=p.notes,
        )
        for _, p in sorted(state.providers.items())
    ]
    return ModelsResponse(
        models=models,
        providers=providers,
        default_tps_by_model=state.catalogue.tps_by_model(),
        reading_tps=state.catalogue.reading_tps,
    )


__all__ = ["KEY_SLOT_BY_PROVIDER", "router"]
