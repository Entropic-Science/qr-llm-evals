"""Token / time / observer-hour estimates (spec §4.2, PRD FR-2.1–2.2, FR-8).

Formulas (mirrored in ``apps/web/src/lib/estimates.ts`` and contract-tested on
shared fixtures)::

    tokens_per_arm(card)    = items × k × est_tokens_per_sample × (told_cells if told_actual else 1)
    time_unsupervised_h     = Σ tokens / tps_by_model[model] / 3600
    observer_hours_single   = Σ tokens / reading_tps / 3600
    observer_hours_tiled(N) = Σ tokens / min(N × reading_tps, tps_by_model[model]) / 3600
    sessions(display)       = ceil(observer_hours(display) × 60 / session_min)

Every result carries ``assumptions`` next to the numbers so the UI and the
prereg can show *what* the estimate rests on (FR-8: never a bare number).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from qr_llm_evals.cards.schema import TOLD_CELLS, EvalCard

#: Documented defaults until the readiness probe measures the box (spec §6).
DEFAULT_TPS_BY_MODEL: dict[str, float] = {"qr-llm": 400.0}
DEFAULT_READING_TPS = 45.0
DEFAULT_TILED_STREAMS = 4
DEFAULT_SESSION_MIN = 30

SECONDS_PER_HOUR = 3600.0


class EstimateAssumptions(BaseModel):
    """The knobs an estimate rests on (``StudyConfig.estimates``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tps_by_model: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_TPS_BY_MODEL),
        description="Aggregate generated tokens/s assumed per model id.",
    )
    reading_tps: float = Field(default=DEFAULT_READING_TPS, gt=0)
    tiled_streams: int = Field(default=DEFAULT_TILED_STREAMS, ge=1)
    session_min: int = Field(default=DEFAULT_SESSION_MIN, ge=1)


class CardOverrides(BaseModel):
    """Frozen per-card overrides a study may apply (``CardSelection``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    k: int | None = None
    items: int | None = None


class CardEstimate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    card_id: str
    k: int | None
    items: int
    tokens_per_arm: int
    time_unsupervised_h: float
    observer_hours_single: float
    observer_hours_tiled: float


class SelectionEstimate(BaseModel):
    """Totals for a set of selected cards on one model (per arm)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    tokens_per_arm: int
    time_unsupervised_h: float
    observer_hours_single: float
    observer_hours_tiled: float
    sessions_single: int
    sessions_tiled: int
    per_card: list[CardEstimate]
    assumptions: dict[str, Any]


def _effective(card: EvalCard, overrides: CardOverrides | None) -> tuple[int | None, int]:
    """Resolve (k, items) after overrides, enforcing the card's bounds."""
    k = card.k
    items = card.dataset.items
    if overrides is not None:
        if overrides.k is not None:
            if card.k is None:
                raise ValueError(f"{card.id}: k cannot be overridden on a card that has k=null")
            lo, hi = card.k_bounds if card.k_bounds is not None else (card.k, card.k)
            if not lo <= overrides.k <= hi:
                raise ValueError(f"{card.id}: k={overrides.k} outside k_bounds [{lo}, {hi}]")
            k = overrides.k
        if overrides.items is not None:
            if not 1 <= overrides.items <= card.dataset.items:
                raise ValueError(
                    f"{card.id}: items={overrides.items} outside [1, {card.dataset.items}]"
                )
            items = overrides.items
    return k, items


def tokens_per_arm(card: EvalCard, overrides: CardOverrides | None = None) -> int:
    """``items × k × est_tokens_per_sample × told_cells`` (0 for non-generating cards)."""
    k, items = _effective(card, overrides)
    if not card.generates:
        return 0
    return items * (k if k is not None else 1) * card.est_tokens_per_sample * card.told_cells


def _hours(tokens: int, tps: float) -> float:
    return tokens / tps / SECONDS_PER_HOUR


def _sessions(hours: float, session_min: int) -> int:
    return math.ceil(hours * 60.0 / session_min)


def estimate_selection(
    cards: Sequence[EvalCard],
    model_id: str,
    assumptions: EstimateAssumptions | None = None,
    overrides: Mapping[str, CardOverrides] | None = None,
) -> SelectionEstimate:
    """Per-arm totals for ``cards`` run on ``model_id`` under ``assumptions``.

    Raises ``ValueError`` when the model has no throughput assumption — the
    caller must not guess a number the user never saw.
    """
    a = assumptions if assumptions is not None else EstimateAssumptions()
    if model_id not in a.tps_by_model:
        raise ValueError(
            f"no throughput assumption for model {model_id!r}; known: {sorted(a.tps_by_model)}"
        )
    tps = a.tps_by_model[model_id]
    if tps <= 0:
        raise ValueError(f"tps_by_model[{model_id!r}] must be > 0, got {tps}")
    tiled_tps = min(a.tiled_streams * a.reading_tps, tps)

    per_card: list[CardEstimate] = []
    total = 0
    for card in cards:
        ov = overrides.get(card.id) if overrides else None
        k, items = _effective(card, ov)
        tokens = tokens_per_arm(card, ov)
        total += tokens
        per_card.append(
            CardEstimate(
                card_id=card.id,
                k=k,
                items=items,
                tokens_per_arm=tokens,
                time_unsupervised_h=_hours(tokens, tps),
                observer_hours_single=_hours(tokens, a.reading_tps),
                observer_hours_tiled=_hours(tokens, tiled_tps),
            )
        )

    single_h = _hours(total, a.reading_tps)
    tiled_h = _hours(total, tiled_tps)
    return SelectionEstimate(
        model_id=model_id,
        tokens_per_arm=total,
        time_unsupervised_h=_hours(total, tps),
        observer_hours_single=single_h,
        observer_hours_tiled=tiled_h,
        sessions_single=_sessions(single_h, a.session_min),
        sessions_tiled=_sessions(tiled_h, a.session_min),
        per_card=per_card,
        assumptions={
            "model_id": model_id,
            "tps_model": tps,
            "reading_tps": a.reading_tps,
            "tiled_streams": a.tiled_streams,
            "tiled_effective_tps": tiled_tps,
            "session_min": a.session_min,
            "told_cells": TOLD_CELLS,
            "formula": {
                "tokens_per_arm": (
                    "items × k × est_tokens_per_sample × (told_cells if told_actual else 1)"
                ),
                "time_unsupervised_h": "Σ tokens / tps_model / 3600",
                "observer_hours_single": "Σ tokens / reading_tps / 3600",
                "observer_hours_tiled": (
                    "Σ tokens / min(tiled_streams × reading_tps, tps_model) / 3600"
                ),
                "sessions": "ceil(observer_hours × 60 / session_min)",
            },
            "caveats": [
                "Token counts are registry estimates per arm; the pilot refines them.",
                "Judge calls, thinking-token overhead and engine queueing are not included.",
                (
                    "Throughput figures are assumptions shown to the user, not measurements, "
                    "until the readiness probe records them."
                ),
            ],
        },
    )


__all__ = [
    "DEFAULT_READING_TPS",
    "DEFAULT_SESSION_MIN",
    "DEFAULT_TILED_STREAMS",
    "DEFAULT_TPS_BY_MODEL",
    "TOLD_CELLS",
    "CardEstimate",
    "CardOverrides",
    "EstimateAssumptions",
    "SelectionEstimate",
    "estimate_selection",
    "tokens_per_arm",
]
