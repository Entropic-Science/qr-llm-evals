"""Study/run schemas and the contracts export (spec §4.1, §4.4, §5.2 'Prereg', invariant 7).

- Study 1 fixture round-trips through ``StudyConfig`` and the config's canonical
  bytes / ``config_hash`` are pinned as vectors (any accidental change to the
  canonicalisation or to the fixture flips them);
- cross-field rules on ``Arm`` / ``StudyConfig`` reject the inadmissible cases;
- ``Provenance`` can never say ``quantum=true`` for a PRNG label or a tainted
  sample (AGENTS.md invariant 2);
- the two ``EstimateAssumptions`` declarations (``models`` vs ``cards``, forced
  apart by the layering rule) have identical JSON Schemas;
- the committed ``packages/contracts`` output equals a fresh export (drift).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from qr_llm_evals.cards import EstimateAssumptions as CardsEstimateAssumptions
from qr_llm_evals.cards import load_cards
from qr_llm_evals.models.schema import (
    Arm,
    EstimateAssumptions,
    StudyConfig,
    canonical_json,
    config_hash,
)
from qr_llm_evals.store.schema import Provenance, Run, Taint
from qr_llm_evals.supervised.schema import Session, Window
from qr_llm_evals.tier0.schema import Tier0Record

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "packages" / "contracts"
FIXTURE = CONTRACTS / "fixtures" / "study1.json"

#: Pinned vectors. If you changed the fixture or the canonicalisation on
#: purpose, recompute with ``config_hash(StudyConfig.model_validate(fixture))``.
STUDY1_CONFIG_HASH = "fc1dbe45830a54759790926f98d2f80d72c78b9aaf46469d31b6f2016004ef9c"
VECTOR_IN: dict[str, Any] = {"b": 1, "a": [1.5, "é", None, True], "c": {"z": 0, "y": "x"}}
VECTOR_CANONICAL = '{"a":[1.5,"é",null,true],"b":1,"c":{"y":"x","z":0}}'
VECTOR_HASH = "4f803db5b8386ed36f104e25faf1c1573685235b2ebc255251526df9ded94836"

NOW = datetime(2026, 9, 14, tzinfo=UTC)
SHA = "0" * 64


@pytest.fixture(scope="module")
def study1_raw() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def study1(study1_raw: dict[str, Any]) -> StudyConfig:
    return StudyConfig.model_validate(study1_raw)


def _arm(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "a", "model": "qr-llm", "sampling": "creative", "source": "qrng"}
    return {**base, **overrides}


# ── canonical JSON + config_hash ─────────────────────────────────────────────


def test_canonical_json_vector() -> None:
    assert canonical_json(VECTOR_IN) == VECTOR_CANONICAL
    assert config_hash(VECTOR_IN) == VECTOR_HASH


def test_canonical_json_is_order_independent() -> None:
    a = {"x": {"b": 1, "a": 2}, "y": [3]}
    b = {"y": [3], "x": {"a": 2, "b": 1}}
    assert canonical_json(a) == canonical_json(b)
    assert config_hash(a) == config_hash(b)


def test_canonical_json_forbids_nan_and_infinity() -> None:
    with pytest.raises(ValueError):
        canonical_json({"v": math.nan})
    with pytest.raises(ValueError):
        canonical_json({"v": math.inf})


def test_study1_fixture_round_trip_and_hash(
    study1: StudyConfig, study1_raw: dict[str, Any]
) -> None:
    assert config_hash(study1) == STUDY1_CONFIG_HASH
    again = StudyConfig.model_validate(json.loads(canonical_json(study1)))
    assert again == study1
    assert config_hash(again) == STUDY1_CONFIG_HASH
    # The fixture is written in full (no defaults omitted) so the SPA sees every key.
    assert study1.model_dump(mode="json") == study1_raw


def test_any_config_change_flips_the_hash(study1: StudyConfig) -> None:
    changed = study1.model_copy(update={"expectations": "we expect nothing"})
    assert config_hash(changed) != STUDY1_CONFIG_HASH


def test_study1_fixture_matches_prd(study1: StudyConfig) -> None:
    assert study1.seeds == [1, 7, 42]
    by_id = {a.id: a for a in study1.arms}
    assert by_id["qrng_supervised"].observer_mode == "supervised"
    assert {by_id[f"prng_seed_{s}"].study_seed for s in (1, 7, 42)} == {1, 7, 42}
    assert all(a.sampling == "creative" for a in study1.arms)
    registry = load_cards()
    for sel in study1.cards:
        assert sel.card_id in registry, sel.card_id


# ── Arm / StudyConfig admission ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("arm", "message"),
    [
        (_arm(source="prng_seeded"), "requires study_seed"),
        (_arm(source="qrng", study_seed=1), "meaningless"),
        (_arm(source="prng_epsilon", study_seed=1), "requires epsilon"),
        (_arm(source="prng_epsilon", study_seed=1, epsilon=0.5), "requires epsilon"),
        (_arm(source="prng_seeded", study_seed=1, epsilon=0.01), "only valid for source"),
        (_arm(sampling="greedy", source="qrng"), "greedy consults no randomness"),
        (_arm(availability="unavailable"), "unavailable_reason"),
        (_arm(source="prng_seeded", study_seed=2**63), "less than"),
        (_arm(sampling="nucleus"), "sampling"),
        (_arm(unknown=1), "Extra inputs"),
    ],
)
def test_arm_rejections(arm: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Arm.model_validate(arm)


def test_arm_accepts_the_study1_shapes() -> None:
    assert Arm.model_validate(_arm()).source_label == "quantum"
    seeded = Arm.model_validate(_arm(source="prng_seeded", study_seed=7))
    assert seeded.source_label == "seeded_prng"
    eps = Arm.model_validate(_arm(source="prng_epsilon", study_seed=1, epsilon=0.03))
    assert eps.source_label == "seeded_prng_biased"
    assert Arm.model_validate(_arm(sampling="greedy", source="prng_seeded", study_seed=1))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["arms"].append(dict(d["arms"][0])), "duplicate arm ids"),
        (lambda d: d["arms"][0].__setitem__("model", "ghost"), "unknown model"),
        (lambda d: d.__setitem__("supervised", None), "supervised config is null"),
        (lambda d: d["arms"][2].__setitem__("study_seed", 99), "not in seeds"),
        (lambda d: d.__setitem__("seeds", [1, 1]), "distinct"),
        (lambda d: d["cards"].append(dict(d["cards"][0])), "duplicate card ids"),
        (lambda d: d["judge"].__setitem__("temperature", 0.7), "less than or equal to 0"),
        (lambda d: d.__setitem__("data_licence", "MIT"), "data_licence"),
        (lambda d: d.__setitem__("created_at", "2026-09-14T00:00:00"), "timezone"),
    ],
)
def test_study_config_rejections(study1_raw: dict[str, Any], mutate: Any, message: str) -> None:
    raw = json.loads(json.dumps(study1_raw))
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        StudyConfig.model_validate(raw)


def test_estimate_assumptions_mirror_is_identical() -> None:
    """Layer 0 forbids models→cards; the mirror must not drift."""
    a = EstimateAssumptions.model_json_schema()
    b = CardsEstimateAssumptions.model_json_schema()
    for s in (a, b):
        s.pop("description", None)
    assert a == b
    assert CardsEstimateAssumptions.model_validate(EstimateAssumptions().model_dump())


# ── run-time records ──────────────────────────────────────────────────────────


def _prov(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model_id": "qr-llm",
        "served_name": "qr-llm",
        "sampling": "creative",
        "preset": "creative_sampling",
        "envelope_hash": SHA,
        "source_label": "quantum",
        "quantum": True,
        "determinism_tier": "sampling_deterministic",
        "pipeline_pinned": True,
    }
    return {**base, **overrides}


def test_provenance_quantum_only_for_clean_qrng() -> None:
    assert Provenance.model_validate(_prov()).quantum is True
    for bad in (
        _prov(source_label="seeded_prng", study_seed=1, qr_seed=5),
        _prov(source_label="provider_prng"),
        _prov(engine_fallback_delta=1),
        _prov(engine_degraded=True),
    ):
        with pytest.raises(ValidationError, match="quantum"):
            Provenance.model_validate(bad)
    tainted = Provenance.model_validate(_prov(quantum=False, engine_fallback_delta=3))
    assert tainted.quantum is False


def test_taint_needs_a_target() -> None:
    with pytest.raises(ValidationError, match="sample_ref or a window_id"):
        Taint.model_validate({"kind": "engine_fallback", "at": NOW})
    assert Taint.model_validate({"kind": "engine_fallback", "at": NOW, "window_id": "w1"})


def test_run_has_no_secret_slot() -> None:
    run = Run.model_validate(
        {
            "run_id": "r1",
            "study_id": "s1",
            "kind": "pilot",
            "created_at": NOW,
            "key_name": "alice",
            "host_token_hash": SHA,
            "config_hash": STUDY1_CONFIG_HASH,
            "keys_present": {"judge": True},
        }
    )
    assert run.state == "created"
    with pytest.raises(ValidationError, match="Extra inputs"):
        Run.model_validate({**run.model_dump(mode="json"), "keys": {"judge": "sk-ant-…"}})
    with pytest.raises(ValidationError, match="Extra inputs"):
        Run.model_validate({**run.model_dump(mode="json"), "host_token": "abc"})


def test_tier0_record_cdf_interval() -> None:
    base = {
        "sample_ref": "a/b/c/0",
        "token_index": 0,
        "token_id": 1,
        "logprob": -0.1,
        "rank_raw": 0,
        "entropy_raw_top20": 0.5,
        "timestamp_ns": 1,
    }
    rec = Tier0Record.model_validate({**base, "u": 0.4, "cdf_lo": 0.3, "cdf_hi": 0.6})
    assert rec.window == "unsupervised"
    with pytest.raises(ValidationError, match="together"):
        Tier0Record.model_validate({**base, "cdf_lo": 0.3})
    with pytest.raises(ValidationError, match="<="):
        Tier0Record.model_validate({**base, "cdf_lo": 0.7, "cdf_hi": 0.6})


def test_session_and_window_shapes() -> None:
    win = Window.model_validate(
        {"window_id": "w1", "kind": "supervised", "start": NOW, "observers_present": []}
    )
    session = Session.model_validate(
        {
            "session_id": "s1",
            "run_id": "r1",
            "index": 0,
            "display": "single",
            "target_s": 1800,
            "host_observer_id": "obs1",
            "windows": [win.model_dump()],
        }
    )
    assert session.overrun_s == 0.0
    with pytest.raises(ValidationError, match="precedes"):
        Window.model_validate(
            {
                "window_id": "w1",
                "kind": "break",
                "start": NOW,
                "end": datetime(2020, 1, 1, tzinfo=UTC),
            }
        )


# ── contracts export drift (invariant 7) ──────────────────────────────────────


def _export_module() -> Any:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import export_schemas
    finally:
        sys.path.pop(0)
    return export_schemas


def test_contracts_python_outputs_are_current() -> None:
    """Every file Python writes into packages/contracts equals the committed copy."""
    mod = _export_module()
    stale = [
        rel.as_posix()
        for rel, text in mod.render_python_outputs().items()
        if (CONTRACTS / rel).read_text(encoding="utf-8") != text
    ]
    assert not stale, f"run `python scripts/export_schemas.py`; stale: {stale}"


def test_cards_export_equals_registry() -> None:
    exported = json.loads(
        (CONTRACTS / "src" / "generated" / "cards.json").read_text(encoding="utf-8")
    )
    registry = load_cards()
    assert [c["id"] for c in exported] == sorted(registry)
    for entry in exported:
        assert entry == registry[entry["id"]].model_dump(mode="json")


def test_every_export_has_schema_and_bundle_entry() -> None:
    mod = _export_module()
    bundle = json.loads((CONTRACTS / "schema" / "_bundle.json").read_text(encoding="utf-8"))
    names = [m.__name__ for m in mod.EXPORTS]
    assert list(bundle["properties"]) == names
    for name in names:
        assert (CONTRACTS / "schema" / f"{name}.json").is_file()
        assert name in bundle["$defs"]
