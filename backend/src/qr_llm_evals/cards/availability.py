"""Which Tier 0 endpoints a run's ``tier0_source`` can honestly compute (spec §2.6, PRD A-3).

The matrix is the single source of truth consumed by the readiness report,
the prereg renderer, the results page and :mod:`~qr_llm_evals.tier0.endpoints`
itself. An endpoint that needs the engine token log is reported
**unavailable** on the degraded ``api_logprobs`` path — never approximated.
"""

from __future__ import annotations

from typing import Literal, get_args

from qr_llm_evals.cards.schema import RequiresTier0

Availability = Literal["available", "unavailable"]
Tier0Source = Literal["engine_log", "api_logprobs"]

#: Card id → its endpoints (must match ``endpoints.primary + secondary`` in the yaml).
CARD_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "tier0_pit_residuals": (
        "pit_ks_two_sample",
        "pit_ks_one_sample",
        "pit_anderson_darling",
        "pit_lag1_autocorr",
        "pit_first_token_ks",
        "u_uniformity",
        "u_vs_r_agreement",
    ),
    "tier0_mmd_hamming": ("mmd_hamming", "mmd_hamming_seed_vs_seed"),
    "tier0_logprob_endpoints": (
        "mean_logprob",
        "mean_entropy",
        "mean_rank",
        "argmax_fraction",
        "tail_fraction_p001",
        "sequence_length",
        "repetition_rate",
    ),
    "stream_deviation_windows": (
        "stouffer_z_supervised_vs_unsupervised",
        "cumulative_deviation",
        "window_permutation_p",
    ),
}

#: Endpoint → the least capable source that can compute it.
ENDPOINT_REQUIREMENTS: dict[str, RequiresTier0] = {
    # T0-a needs u / cdf brackets: engine log only.
    "pit_ks_two_sample": "engine_log",
    "pit_ks_one_sample": "engine_log",
    "pit_anderson_darling": "engine_log",
    "pit_lag1_autocorr": "engine_log",
    "pit_first_token_ks": "engine_log",  # nosec B105 - endpoint id, not a credential
    "u_uniformity": "engine_log",
    "u_vs_r_agreement": "engine_log",
    # T0-b needs token ids only.
    "mmd_hamming": "api_logprobs",
    "mmd_hamming_seed_vs_seed": "api_logprobs",
    # T0-c: raw-logprob subset from the API; post-truncation quantities from the engine.
    "mean_logprob": "api_logprobs",
    "mean_entropy": "engine_log",
    "mean_rank": "api_logprobs",
    "argmax_fraction": "api_logprobs",
    "tail_fraction_p001": "engine_log",
    "sequence_length": "api_logprobs",
    "repetition_rate": "api_logprobs",
    # T4-l needs u values with engine timestamps.
    "stouffer_z_supervised_vs_unsupervised": "engine_log",
    "cumulative_deviation": "engine_log",
    "window_permutation_p": "engine_log",
}

_SOURCE_RANK: dict[str, int] = {"none": 0, "api_logprobs": 1, "engine_log": 2}
TIER0_SOURCES: tuple[Tier0Source, ...] = get_args(Tier0Source)


def requirement_for(endpoint: str) -> RequiresTier0:
    try:
        return ENDPOINT_REQUIREMENTS[endpoint]
    except KeyError as err:
        raise KeyError(f"unknown Tier 0 endpoint {endpoint!r}") from err


def is_available(endpoint: str, tier0_source: Tier0Source | None) -> bool:
    """``engine_log`` satisfies everything; ``api_logprobs`` only the token/logprob subset."""
    have = _SOURCE_RANK["none" if tier0_source is None else tier0_source]
    return have >= _SOURCE_RANK[requirement_for(endpoint)]


def endpoints_for(tier0_source: Tier0Source | None) -> dict[str, Availability]:
    """The full availability matrix for one source, endpoint → ``available|unavailable``."""
    return {
        ep: ("available" if is_available(ep, tier0_source) else "unavailable")
        for ep in ENDPOINT_REQUIREMENTS
    }


def available_endpoints(tier0_source: Tier0Source | None) -> frozenset[str]:
    return frozenset(ep for ep, a in endpoints_for(tier0_source).items() if a == "available")


def card_availability(card_id: str, tier0_source: Tier0Source | None) -> dict[str, Availability]:
    """Availability of one Tier 0 card's endpoints (``KeyError`` for a non-Tier-0 card)."""
    return {ep: endpoints_for(tier0_source)[ep] for ep in CARD_ENDPOINTS[card_id]}


CardStatus = Literal["available", "partial", "unavailable"]


def card_status(card_id: str, tier0_source: Tier0Source | None) -> CardStatus:
    values = set(card_availability(card_id, tier0_source).values())
    if values == {"available"}:
        return "available"
    if values == {"unavailable"}:
        return "unavailable"
    return "partial"


__all__ = [
    "CARD_ENDPOINTS",
    "ENDPOINT_REQUIREMENTS",
    "TIER0_SOURCES",
    "CardStatus",
    "available_endpoints",
    "card_availability",
    "card_status",
    "endpoints_for",
    "is_available",
    "requirement_for",
]
