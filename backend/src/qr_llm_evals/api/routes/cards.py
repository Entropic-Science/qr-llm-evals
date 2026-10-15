"""``GET /cards`` and ``GET /cards/{id}`` — the registry with computed estimates (spec §4.7)."""

from __future__ import annotations

from fastapi import APIRouter

from qr_llm_evals.api.deps import AppState, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.schema import CardEstimateSummary, CardsResponse, CardView
from qr_llm_evals.cards.estimates import EstimateAssumptions, estimate_selection
from qr_llm_evals.cards.schema import EvalCard

router = APIRouter(tags=["cards"])

#: The reference target every card's headline estimate is computed for.
DEFAULT_MODEL_ID = "qr-llm"


def _assumptions(state: AppState) -> EstimateAssumptions:
    return EstimateAssumptions(
        tps_by_model=state.catalogue.tps_by_model(), reading_tps=state.catalogue.reading_tps
    )


def card_view(state: AppState, card: EvalCard) -> CardView:
    model_id = DEFAULT_MODEL_ID if DEFAULT_MODEL_ID in state.catalogue.ids() else None
    if model_id is None and state.catalogue.entries:
        model_id = state.catalogue.entries[0].id
    estimate: CardEstimateSummary | None = None
    if model_id is not None:
        try:
            sel = estimate_selection([card], model_id, _assumptions(state))
        except ValueError:
            sel = None
        if sel is not None:
            estimate = CardEstimateSummary(
                model_id=model_id,
                tokens_per_arm=sel.tokens_per_arm,
                time_unsupervised_h=sel.time_unsupervised_h,
                observer_hours_single=sel.observer_hours_single,
                observer_hours_tiled=sel.observer_hours_tiled,
            )
    return CardView(card=card, estimate=estimate)


@router.get("/cards", response_model=CardsResponse)
async def list_cards(state: State) -> CardsResponse:
    views = [card_view(state, card) for _, card in sorted(state.cards.items())]
    return CardsResponse(cards=views, count=len(views))


@router.get("/cards/{card_id}", response_model=CardView)
async def get_card(card_id: str, state: State) -> CardView:
    card = state.cards.get(card_id)
    if card is None:
        raise ApiError(404, f"unknown card {card_id!r}", param="card_id")
    return card_view(state, card)


__all__ = ["DEFAULT_MODEL_ID", "card_view", "router"]
