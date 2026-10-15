"""stream_deviation_windows — T4-l stream deviation during observed windows (registry C.4).

Zero-generation card. Consumes the Tier 0 records of the supervised arm
(``session_id`` / ``window`` tags set by the supervised runner) and reports,
per session, the Stouffer-Z of per-second bit fractions during supervised
windows against the unsupervised windows, the cumulative-deviation series and a
window-label permutation p. Needs the engine token log (``u`` with engine
timestamps); unavailable on the degraded path.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from qr_llm_evals.tier0 import endpoints
from qr_llm_evals.tier0.schema import Tier0Record

CARD_ID = "stream_deviation_windows"
ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"] | None


def build_items() -> list[dict[str, Any]]:
    return []


def build_messages(item: dict[str, Any], told: ToldCondition = None) -> list[dict[str, str]]:
    raise RuntimeError(f"{CARD_ID} generates nothing; build_messages must never be called")


def score(sample: dict[str, Any]) -> dict[str, float | int | str | None]:
    return {}


def score_from_tier0(
    records: Iterable[Tier0Record],
    *,
    tier0_source: str | None = None,
    permutations: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """T4-l table for one arm's records (all sessions)."""
    return endpoints.stream_deviation_table(
        records,
        n_permutations=permutations,
        seed=seed,
        tier0_source=tier0_source,  # type: ignore[arg-type]
    )
