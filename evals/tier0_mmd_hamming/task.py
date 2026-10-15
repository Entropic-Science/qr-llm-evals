"""tier0_mmd_hamming — T0-b Model Equality Testing MMD with a Hamming kernel (registry B.2).

Zero-generation card. The permutation test itself runs in the analysis layer
(``qr_llm_evals.analysis.mmd.mmd_hamming_permutation_test``) on the padded
inputs this module prepares; ``task_args.permutations`` (≥ 2000) is passed
through by the analysis step.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from qr_llm_evals.tier0 import endpoints
from qr_llm_evals.tier0.schema import Tier0Record

CARD_ID = "tier0_mmd_hamming"
ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"] | None


def build_items() -> list[dict[str, Any]]:
    return []


def build_messages(item: dict[str, Any], told: ToldCondition = None) -> list[dict[str, str]]:
    raise RuntimeError(f"{CARD_ID} generates nothing; build_messages must never be called")


def score(sample: dict[str, Any]) -> dict[str, float | int | str | None]:
    return {}


def score_from_tier0(
    records_by_arm: Mapping[str, Sequence[Tier0Record]],
    *,
    tier0_source: str | None = None,
    permutations: int = 2000,
) -> dict[str, Any]:
    """Descriptive T0-b table; ``permutations`` is echoed for the analysis step."""
    table = endpoints.t0b_table(records_by_arm, tier0_source=tier0_source)  # type: ignore[arg-type]
    table["n_permutations"] = int(permutations)
    return table


def mmd_inputs(
    records_by_arm: Mapping[str, Sequence[Tier0Record]],
    arms_a: str | Sequence[str],
    arms_b: str | Sequence[str],
) -> endpoints.MmdInputs:
    """Padded token-id matrices for one contrast (QRNG vs pooled PRNG, or seed vs seed)."""
    return endpoints.mmd_inputs(records_by_arm, arms_a, arms_b)
