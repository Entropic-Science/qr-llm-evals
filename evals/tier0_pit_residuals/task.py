"""tier0_pit_residuals — T0-a probability-integral-transform residuals (registry B.2).

A zero-generation card: it never calls the target. Its endpoints are computed
from the Tier 0 records that every *other* card's generations produce, so
``build_items()`` is empty and the runner has nothing to dispatch. The analysis
step calls :func:`score_from_tier0` with the run's records grouped by arm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from qr_llm_evals.tier0 import endpoints
from qr_llm_evals.tier0.schema import Tier0Record

CARD_ID = "tier0_pit_residuals"
ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"] | None


def build_items() -> list[dict[str, Any]]:
    """No prompts of its own (``dataset.source: derived:tier0_log``)."""
    return []


def build_messages(item: dict[str, Any], told: ToldCondition = None) -> list[dict[str, str]]:
    raise RuntimeError(f"{CARD_ID} generates nothing; build_messages must never be called")


def score(sample: dict[str, Any]) -> dict[str, float | int | str | None]:
    """Per-sample scoring is not defined for a stream-level card."""
    return {}


def score_from_tier0(
    records_by_arm: Mapping[str, Sequence[Tier0Record]],
    *,
    contrasts: Mapping[str, tuple[str | Sequence[str], str | Sequence[str]]] | None = None,
    tier0_source: str | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """T0-a table: per-arm uniformity checks plus two-sample KS for each contrast."""
    return endpoints.t0a_table(
        records_by_arm,
        contrasts,
        seed=seed,
        tier0_source=tier0_source,  # type: ignore[arg-type]
    )
