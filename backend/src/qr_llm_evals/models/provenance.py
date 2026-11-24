"""Provenance labels: what a sample's randomness honestly was (spec §2.2, §4.4; AGENTS.md invariant 2).

Every source maps to exactly one label; ``quantum`` is the only label that may
carry ``quantum=True``, and only when the engine reported zero fallbacks and no
degradation for the sample. Anything else is labelled for what it is
(``seeded_prng``, ``system_prng``, ``seeded_prng_biased``, ``provider_prng``)
and never laundered.

Layer 0: the pydantic ``Provenance`` record lives in :mod:`qr_llm_evals.store.schema`
(layer 1), so :func:`provenance_fields` returns the plain field dict that record
validates. The invariant is checked here *and* there.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from qr_llm_evals.models.envelope import SAMPLING_TABLE, envelope_hash
from qr_llm_evals.models.schema import SOURCE_LABELS, Arm, ModelRef, Source, SourceLabel

#: Human-readable badge text per label for the SPA / prereg / results tables.
LABEL_TEXT: dict[SourceLabel, str] = {
    "quantum": "quantum (hardware QRNG, fresh entropy per token)",
    "seeded_prng": "seeded PRNG — not quantum",
    "system_prng": "system PRNG (os.urandom) — not quantum",
    "seeded_prng_biased": "seeded PRNG with bias ε — not quantum",
    "resampled_prng": "seeded PRNG replaying windows of a recorded device pool — not quantum",
    "provider_prng": "external provider's own PRNG — not quantum",
}


def label_for_source(source: Source | str) -> SourceLabel:
    """The one label for a source; raises ``KeyError`` for an unknown source."""
    label = SOURCE_LABELS[str(source)]
    return label  # type: ignore[return-value]


def is_quantum_source(source: Source | str) -> bool:
    return label_for_source(source) == "quantum"


def quantum_claim(
    source: Source | str, *, engine_fallback_delta: int, engine_degraded: bool
) -> bool:
    """``True`` only for the qrng source with zero fallbacks and no degradation."""
    return is_quantum_source(source) and engine_fallback_delta == 0 and not engine_degraded


def provenance_fields(
    arm: Arm,
    model: ModelRef,
    envelope: Mapping[str, Any],
    *,
    qr_seed: int | None,
    pipeline_pinned: bool,
    determinism_tier: str = "none",
    engine_fallback_delta: int = 0,
    engine_degraded: bool = False,
    engine_last_source: str | None = None,
) -> dict[str, Any]:
    """The ``Provenance`` field dict for one generation (validated by ``store.schema.Provenance``)."""
    if engine_fallback_delta < 0:
        raise ValueError("engine_fallback_delta must be >= 0")
    if model.provider != "qr_server" and pipeline_pinned:
        raise ValueError(
            f"model {model.id!r} on provider {model.provider!r} cannot be pipeline_pinned"
        )
    preset, _pins = SAMPLING_TABLE[arm.sampling]
    return {
        "model_id": model.id,
        "served_name": model.served_name,
        "checkpoint": model.checkpoint,
        "sampling": arm.sampling,
        "preset": preset if model.provider == "qr_server" else None,
        "envelope_hash": envelope_hash(envelope),
        "source_label": label_for_source(arm.source),
        "quantum": quantum_claim(
            arm.source, engine_fallback_delta=engine_fallback_delta, engine_degraded=engine_degraded
        ),
        "study_seed": arm.study_seed,
        "qr_seed": qr_seed,
        "epsilon": arm.epsilon,
        "engine_fallback_delta": engine_fallback_delta,
        "engine_degraded": engine_degraded,
        "engine_last_source": engine_last_source,
        "determinism_tier": determinism_tier,
        "pipeline_pinned": pipeline_pinned,
    }


__all__ = [
    "LABEL_TEXT",
    "is_quantum_source",
    "label_for_source",
    "provenance_fields",
    "quantum_claim",
]
