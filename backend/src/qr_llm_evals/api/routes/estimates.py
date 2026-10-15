"""``POST /estimates`` — per-arm/card totals and the supervised line (spec §4.2, §4.7).

Open route (no key) ⇒ per-IP token bucket. The estimator is the shared
formula in ``cards/estimates.py`` (mirrored in ``apps/web/src/lib/estimates.ts``);
this route only assembles it per arm and adds the supervised wall-clock line.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Request

from qr_llm_evals.api.deps import AppState, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import enforce
from qr_llm_evals.api.schema import EstimatesResponse, EstimateTotals, SupervisedEstimate
from qr_llm_evals.cards.estimates import (
    CardOverrides,
    EstimateAssumptions,
    SelectionEstimate,
    estimate_selection,
)
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.models.schema import StudyConfig

router = APIRouter(tags=["estimates"])


def _selected_cards(state: AppState, config: StudyConfig) -> list[EvalCard]:
    cards: list[EvalCard] = []
    for sel in config.cards:
        card = state.cards.get(sel.card_id)
        if card is None:
            raise ApiError(400, f"unknown card {sel.card_id!r}", param="cards")
        cards.append(card)
    return cards


def _assumptions(state: AppState, config: StudyConfig) -> EstimateAssumptions:
    tps = dict(state.catalogue.tps_by_model())
    tps.update(config.estimates.tps_by_model)
    return EstimateAssumptions(
        tps_by_model=tps,
        reading_tps=config.estimates.reading_tps,
        tiled_streams=config.estimates.tiled_streams,
        session_min=config.estimates.session_min,
    )


def estimate_study(state: AppState, config: StudyConfig) -> EstimatesResponse:
    cards = _selected_cards(state, config)
    overrides = {
        sel.card_id: CardOverrides(k=sel.overrides.k, items=sel.overrides.items)
        for sel in config.cards
    }
    assumptions = _assumptions(state, config)
    by_arm: dict[str, SelectionEstimate] = {}
    for arm in config.arms:
        try:
            by_arm[arm.id] = estimate_selection(cards, arm.model, assumptions, overrides)
        except ValueError as err:
            raise ApiError(400, str(err), param=f"arms.{arm.id}") from err

    totals = EstimateTotals(
        tokens=sum(e.tokens_per_arm for e in by_arm.values()),
        time_unsupervised_h=sum(e.time_unsupervised_h for e in by_arm.values()),
        observer_hours_single=sum(e.observer_hours_single for e in by_arm.values()),
        observer_hours_tiled=sum(e.observer_hours_tiled for e in by_arm.values()),
        sessions_single=sum(e.sessions_single for e in by_arm.values()),
        sessions_tiled=sum(e.sessions_tiled for e in by_arm.values()),
    )

    supervised: SupervisedEstimate | None = None
    sup_arms = [a.id for a in config.arms if a.observer_mode == "supervised"]
    if sup_arms and config.supervised is not None:
        settings = config.supervised.settings
        display = config.supervised.display
        hours = sum(
            by_arm[a].observer_hours_tiled
            if display == "tiled"
            else by_arm[a].observer_hours_single
            for a in sup_arms
        )
        sessions = math.ceil(hours * 60.0 / settings.session_min) if hours > 0 else 0
        wall_clock_h = (
            sessions * settings.session_min + max(0, sessions - 1) * settings.break_min
        ) / 60.0
        supervised = SupervisedEstimate(
            arms=sup_arms,
            display=display,
            observer_hours=hours,
            sessions=sessions,
            session_min=settings.session_min,
            break_min=settings.break_min,
            wall_clock_h=wall_clock_h,
        )
    return EstimatesResponse(by_arm=by_arm, totals=totals, supervised=supervised)


@router.post("/estimates", response_model=EstimatesResponse)
async def post_estimates(config: StudyConfig, request: Request, state: State) -> EstimatesResponse:
    enforce(state.limiter, request, scope="estimates")
    return estimate_study(state, config)


__all__ = ["estimate_study", "router"]
