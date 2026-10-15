"""tier0_logprob_endpoints — T0-c free logprob endpoints (registry B.2).

Zero-generation card: per-sample detector features (mean logprob, entropy,
rank, argmax fraction, min-p tail, length, repetition) from the Tier 0
records of every other card, averaged per prompt for the paired permutation
test the analysis step runs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from qr_llm_evals.tier0 import endpoints
from qr_llm_evals.tier0.schema import Tier0Record

CARD_ID = "tier0_logprob_endpoints"
ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"] | None


def build_items() -> list[dict[str, Any]]:
    return []


def build_messages(item: dict[str, Any], told: ToldCondition = None) -> list[dict[str, str]]:
    raise RuntimeError(f"{CARD_ID} generates nothing; build_messages must never be called")


def score(sample: dict[str, Any]) -> dict[str, float | int | str | None]:
    """Detector features for one sample when its Tier 0 records are supplied inline."""
    records = sample.get("tier0") or []
    if not records:
        return {}
    seq = [r if isinstance(r, Tier0Record) else Tier0Record.model_validate(r) for r in records]
    return dict(endpoints.sample_features(sorted(seq, key=lambda r: r.token_index)))


def score_from_tier0(
    records_by_arm: Mapping[str, Sequence[Tier0Record]],
    *,
    tier0_source: str | None = None,
) -> dict[str, Any]:
    return endpoints.t0c_table(records_by_arm, tier0_source=tier0_source)  # type: ignore[arg-type]
