"""``GET /cards``, ``GET /cards/{id}`` and ``GET /cards/{id}/items`` (spec §4.7).

The registry with computed estimates, and a card's prompts where they can be
read before a run. All three are open, read-only and touch nothing outside
this repository's files.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from qr_llm_evals.api.deps import AppState, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.schema import (
    CardEstimateSummary,
    CardItem,
    CardItemsResponse,
    CardsResponse,
    CardView,
)
from qr_llm_evals.cards.estimates import EstimateAssumptions, estimate_selection
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.store.schema import ToldCondition

router = APIRouter(tags=["cards"])

#: The reference target every card's headline estimate is computed for.
DEFAULT_MODEL_ID = "qr-llm"
ITEMS_DEFAULT_LIMIT = 50
ITEMS_MAX_LIMIT = 100


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


@router.get("/cards/{card_id}/items", response_model=CardItemsResponse)
async def get_card_items(
    card_id: str,
    state: State,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=ITEMS_MAX_LIMIT)] = ITEMS_DEFAULT_LIMIT,
    told: ToldCondition | None = None,
) -> CardItemsResponse:
    """A page of the card's prompts, or the reason they cannot be listed before a run.

    A card whose prompts are not readable here is an ordinary ``200`` with
    ``listable: false``; so is one whose task module fails to load.
    """
    card = state.cards.get(card_id)
    if card is None:
        raise ApiError(404, f"unknown card {card_id!r}", param="card_id")
    # The first request for a card imports its task module and reads its
    # prompt files; that stays off the event loop.
    preview = await run_in_threadpool(state.previews.get, card, state.repo_root)
    items = preview.items(told)
    return CardItemsResponse(
        card_id=card.id,
        listable=preview.listable,
        source=preview.source,
        reason=preview.reason,
        total=len(items),
        offset=offset,
        limit=limit,
        told=preview.told_for(told),
        items=[
            CardItem(
                index=item.index,
                prompt_id=item.prompt_id,
                messages=[dict(message) for message in item.messages],
                truncated=item.truncated,
            )
            for item in items[offset : offset + limit]
        ],
    )


__all__ = ["DEFAULT_MODEL_ID", "ITEMS_DEFAULT_LIMIT", "ITEMS_MAX_LIMIT", "card_view", "router"]
