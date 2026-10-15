"""Provenance labelling (spec §5.2, AGENTS.md invariant 2): every source maps
to a label; ``quantum=True`` only for ``qrng`` with zero fallbacks and no
degradation; ``pipeline_pinned=False`` for public lanes / external providers.
The layer-0 field builder is validated through the layer-1 ``Provenance`` record.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from qr_llm_evals.models.catalogue import load_catalogue, parse_model_id
from qr_llm_evals.models.envelope import SampleContext, build_envelope, envelope_hash
from qr_llm_evals.models.provenance import (
    LABEL_TEXT,
    is_quantum_source,
    label_for_source,
    provenance_fields,
    quantum_claim,
)
from qr_llm_evals.models.schema import SOURCE_LABELS, Arm, ModelRef, Source, SourceLabel
from qr_llm_evals.store.schema import Provenance

QR_LLM = ModelRef(
    id="qr-llm", family="qr_llm", provider="qr_server", served_name="qr-llm", checkpoint="ckpt@rev"
)
CTX = SampleContext(card_id="creative_diversity", prompt_id="p0001", epoch=0, max_tokens=512)


def _arm(source: str, **kw: Any) -> Arm:
    extra: dict[str, Any] = {}
    if source in ("prng_seeded", "prng_epsilon"):
        extra["study_seed"] = 7
    if source == "prng_epsilon":
        extra["epsilon"] = 0.01
    model = "openai:gpt-4o" if source == "provider" else "qr-llm"
    return Arm.model_validate(
        {
            "id": f"arm_{source}",
            "model": model,
            "sampling": "creative",
            "source": source,
            **extra,
            **kw,
        }
    )


def _fields(source: str, **kw: Any) -> dict[str, Any]:
    arm = _arm(source)
    if source == "provider":
        model = parse_model_id("openai:gpt-4o")
        envelope: dict[str, Any] = {}
        pinned = False
    else:
        model = QR_LLM
        envelope = build_envelope(arm, CTX)
        pinned = True
    kw.setdefault("pipeline_pinned", pinned)
    return provenance_fields(arm, model, envelope, qr_seed=envelope.get("qr_seed"), **kw)


# ── label table ──────────────────────────────────────────────────────────────


def test_every_source_maps_to_exactly_one_label() -> None:
    sources = set(get_args(Source))
    labels = set(get_args(SourceLabel))
    assert set(SOURCE_LABELS) == sources
    assert set(SOURCE_LABELS.values()) == labels
    assert set(LABEL_TEXT) == labels
    for source in sources:
        assert label_for_source(source) == SOURCE_LABELS[source]


def test_label_table_is_the_spec_table() -> None:
    assert SOURCE_LABELS == {
        "qrng": "quantum",
        "prng_seeded": "seeded_prng",
        "prng_system": "system_prng",
        "prng_epsilon": "seeded_prng_biased",
        "provider": "provider_prng",
    }


def test_only_qrng_is_quantum() -> None:
    assert [s for s in get_args(Source) if is_quantum_source(s)] == ["qrng"]
    for label, text in LABEL_TEXT.items():
        assert (label == "quantum") != ("not quantum" in text)


def test_unknown_source_raises() -> None:
    with pytest.raises(KeyError):
        label_for_source("qrng2")


# ── quantum claim ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", sorted(get_args(Source)))
def test_quantum_true_only_for_clean_qrng(source: str) -> None:
    fields = _fields(source)
    prov = Provenance.model_validate(fields)
    assert prov.source_label == SOURCE_LABELS[source]
    assert prov.quantum is (source == "qrng")


@pytest.mark.parametrize(
    "kw",
    [
        {"engine_fallback_delta": 1},
        {"engine_degraded": True},
        {"engine_fallback_delta": 3, "engine_degraded": True},
    ],
)
def test_fallback_or_degradation_removes_quantum_claim(kw: dict[str, Any]) -> None:
    prov = Provenance.model_validate(_fields("qrng", **kw))
    assert prov.source_label == "quantum" and prov.quantum is False
    assert quantum_claim("qrng", engine_fallback_delta=0, engine_degraded=False)
    assert not quantum_claim("qrng", engine_fallback_delta=1, engine_degraded=False)
    assert not quantum_claim("prng_seeded", engine_fallback_delta=0, engine_degraded=False)


def test_store_schema_rejects_laundered_claim() -> None:
    fields = _fields("prng_seeded")
    fields["quantum"] = True
    with pytest.raises(ValueError, match="impossible"):
        Provenance.model_validate(fields)


def test_negative_fallback_delta_rejected() -> None:
    with pytest.raises(ValueError):
        _fields("qrng", engine_fallback_delta=-1)


# ── fields ───────────────────────────────────────────────────────────────────


def test_seeded_fields_carry_study_and_request_seed() -> None:
    prov = Provenance.model_validate(_fields("prng_seeded"))
    assert prov.study_seed == 7
    assert prov.qr_seed == build_envelope(_arm("prng_seeded"), CTX)["qr_seed"]
    assert prov.preset == "creative_sampling"
    assert prov.epsilon is None
    assert prov.envelope_hash == envelope_hash(build_envelope(_arm("prng_seeded"), CTX))
    assert prov.checkpoint == "ckpt@rev" and prov.served_name == "qr-llm"


def test_epsilon_fields() -> None:
    prov = Provenance.model_validate(_fields("prng_epsilon"))
    assert (
        prov.epsilon == 0.01 and prov.study_seed == 7 and prov.source_label == "seeded_prng_biased"
    )


def test_qrng_fields_have_no_seed() -> None:
    prov = Provenance.model_validate(_fields("qrng", engine_last_source="quantum_grpc"))
    assert prov.study_seed is None and prov.qr_seed is None
    assert prov.engine_last_source == "quantum_grpc"
    assert prov.determinism_tier == "none"


# ── pipeline_pinned ──────────────────────────────────────────────────────────


def test_provider_arms_are_not_pinned_and_have_no_preset() -> None:
    prov = Provenance.model_validate(_fields("provider"))
    assert prov.pipeline_pinned is False
    assert prov.preset is None
    assert prov.source_label == "provider_prng" and prov.quantum is False


def test_external_model_cannot_claim_pinned() -> None:
    with pytest.raises(ValueError, match="cannot be pipeline_pinned"):
        _fields("provider", pipeline_pinned=True)


def test_catalogue_pins_only_the_loopback_target() -> None:
    cat = load_catalogue()
    assert cat.is_pinned("qr-llm") is True
    assert cat.is_pinned("openrouter:anthropic/claude-sonnet-4") is False
    for entry in cat.entries:
        assert entry.pipeline_pinned == (entry.provider == "qr_server")
