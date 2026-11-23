"""Study/run schemas and the contracts export (spec §4.1, §4.4, §5.2 'Prereg', invariant 7).

- Study 1 fixture round-trips through ``StudyConfig`` and the config's canonical
  bytes / ``config_hash`` are pinned as vectors (any accidental change to the
  canonicalisation or to the fixture flips them);
- cross-field rules on ``Arm`` / ``StudyConfig`` reject the inadmissible cases;
- a config without a ``design`` block serializes without the key (so every
  legacy hash stands), one with a block round-trips, and the block's cross-field
  rules reject the designs that cannot be analysed;
- the designed Study 1 template (``study1.designed.json``) is pinned like the
  legacy one, and its design block is the recommended one, unedited;
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

from qr_llm_evals.api.schema import PreregRenderRequest
from qr_llm_evals.cards import EstimateAssumptions as CardsEstimateAssumptions
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards.design import recommend, recommend_k
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import (
    ALPHA_LEVELS,
    Arm,
    EstimateAssumptions,
    StudyConfig,
    StudyDesign,
    canonical_json,
    config_hash,
)
from qr_llm_evals.store.schema import Provenance, Run, Taint
from qr_llm_evals.supervised.schema import Session, Window
from qr_llm_evals.tier0.schema import Tier0Record

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "packages" / "contracts"
FIXTURE = CONTRACTS / "fixtures" / "study1.json"
DESIGNED_FIXTURE = CONTRACTS / "fixtures" / "study1.designed.json"

#: Pinned vectors. If you changed the fixture or the canonicalisation on
#: purpose, recompute with ``config_hash(StudyConfig.model_validate(fixture))``.
STUDY1_CONFIG_HASH = "2d17abd8a998e06ffa95c3bba98d248a8b49f2cd886e03a270cdb55ee5d9c4e7"
#: The designed Study 1 template, written by ``scripts/gen_design_fixtures.py``.
#: The plan hash moves with anything a designed study registers (hypotheses,
#: sample sizes, the wording of its rules), so a change there is a decision.
#:
#: Both moved when the variance bound stopped counting the told cells of a
#: seeded run as independent values. The config: the template now runs the
#: self-referential probe at k = 24, the fewest repetitions at which its 30
#: prompts are still rated sufficient (at the card's k = 20 they are marginal).
#: The plan: the probe's sample size under the corrected bound, the two new
#: sample-size fields (``control_floor``, ``k_recommended``) and the planned
#: generations that follow from k = 24.
STUDY1_DESIGNED_CONFIG_HASH = "ed46b3ad02fb2817212222dd34c390fc4204b5abc8223e3525bc5fd4186c8126"
STUDY1_DESIGNED_PLAN_HASH = "cbe1e912ddb85fc881fad8e600e76ef80b7a585c59f7a1fd4945ed4075edd02c"
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
        (_arm(source="prng_epsilon", study_seed=1, epsilon=0.7), "requires epsilon"),
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
        (lambda d: d["judge"].__setitem__("temperature", 1.7), "less than or equal to 1"),
        (lambda d: d["judge"].__setitem__("max_tokens", 8), "greater than or equal to 64"),
        (lambda d: d["judge"].__setitem__("thinking", "on"), "disabled"),
        (lambda d: d.__setitem__("data_licence", "MIT"), "data_licence"),
        (lambda d: d.__setitem__("created_at", "2026-09-14T00:00:00"), "timezone"),
    ],
)
def test_study_config_rejections(study1_raw: dict[str, Any], mutate: Any, message: str) -> None:
    raw = json.loads(json.dumps(study1_raw))
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        StudyConfig.model_validate(raw)


# ── the design block ──────────────────────────────────────────────────────────

PIT = {"card_id": "tier0_pit_residuals", "endpoint": "pit_ks_two_sample"}
MMD = {"card_id": "tier0_mmd_hamming", "endpoint": "mmd_hamming"}
PROBE = {"card_id": "self_referential_probe", "endpoint": "experience_claim_rate"}
NEGATION = {"card_id": "sentience_statements", "endpoint": "cross_negation_consistency"}


def _designed(study1_raw: dict[str, Any], **design: Any) -> dict[str, Any]:
    """Study 1 with a design block. Its one ε arm (0.03) moves to a seed of its own."""
    raw: dict[str, Any] = json.loads(json.dumps(study1_raw))
    raw["seeds"] = [1, 7, 42, 1001]
    _arm_by_id(raw, "prng_epsilon_003")["study_seed"] = 1001
    raw["design"] = {"claim": "difference", "confirmatory": [PIT, MMD, PROBE], **design}
    return raw


def _arm_by_id(raw: dict[str, Any], arm_id: str) -> dict[str, Any]:
    return next(a for a in raw["arms"] if a["id"] == arm_id)  # type: ignore[no-any-return]


def _second_cell(raw: dict[str, Any]) -> None:
    """Add a complete t1_minp cell (quantum plus two seeds), without an ε arm."""
    for arm_id in ("qrng_unsupervised", "prng_seed_1", "prng_seed_7"):
        raw["arms"].append({**_arm_by_id(raw, arm_id), "id": f"{arm_id}_t1", "sampling": "t1_minp"})


def _plan_design(alpha: float) -> dict[str, Any]:
    """A minimal designed part of a plan: one hypothesis on the PIT endpoint."""
    return {
        "claim": "difference",
        "alpha": alpha,
        "sensitivity_epsilon": None,
        "families": {"difference": 1, "equivalence": 0, "negative_control": 3},
        "hypotheses": [
            {
                "id": "H1",
                **PIT,
                "label": "Token sampling residuals (KS)",
                "model": "qr-llm",
                "sampling": "creative",
                "test": "ks_two_sample",
                "sided": "two_sided",
                "contrast": 0,
                "negative_controls": [1, 2, 3],
                "positive_control": None,
                "margin": None,
                "margin_source": "none",
                "planned_units": 200,
                "planned_pairs": 200,
                "sample_size": {
                    "n": 200,
                    "n_floor": 6,
                    "mde_standardized": None,
                    "sd_bound": None,
                    "mde_raw": None,
                    "power_at_margin": None,
                    "equivalence_floor": None,
                    "n_recommended": None,
                    "reachable": None,
                    "rating": "unknown",
                    "basis": "no_closed_form",
                },
                "statement": "H1: quantum sampling differs from seeded pseudo-random sampling.",
            }
        ],
    }


def test_design_none_is_omitted(study1: StudyConfig, study1_raw: dict[str, Any]) -> None:
    """No design block, no ``design`` key: the bytes of a legacy study never move."""
    assert study1.design is None
    assert "design" not in study1.model_dump()
    assert "design" not in study1.model_dump(mode="json")
    assert "design" not in json.loads(study1.model_dump_json())
    assert '"design"' not in canonical_json(study1)
    # An explicit null (what the SPA's validator fills in) is the same study.
    explicit = StudyConfig.model_validate({**study1_raw, "design": None})
    assert explicit == study1 and config_hash(explicit) == STUDY1_CONFIG_HASH
    # The key stays out when the config travels inside a request model too.
    nested = PreregRenderRequest(config=study1).model_dump(mode="json")
    assert "design" not in nested["config"] and nested["config"] == study1_raw

    schema = StudyConfig.model_json_schema()
    assert schema["properties"]["design"]["default"] is None
    assert "design" not in schema["required"]
    # The serializer must not swallow the response schema FastAPI documents.
    assert "title" in StudyConfig.model_json_schema(mode="serialization")["properties"]

    plan = StatisticalPlan.from_config(study1, load_cards())
    dumped = plan.model_dump(mode="json")
    assert plan.design is None and "design" not in dumped
    assert dumped["pilot_quantile"] == 0.95
    assert '"design"' not in plan.canonical_json()
    assert "endpoints" in StatisticalPlan.model_json_schema(mode="serialization")["properties"]


def test_design_roundtrip(study1: StudyConfig, study1_raw: dict[str, Any]) -> None:
    designed = StudyConfig.model_validate(_designed(study1_raw))
    assert designed.design is not None
    assert designed.model_dump(mode="json")["design"] == {
        "version": 1,
        "claim": "difference",
        "confirmatory": [PIT, MMD, PROBE],
        "alpha": 0.05,
        "sensitivity_epsilon": None,
        "margins": [],
    }
    again = StudyConfig.model_validate(json.loads(canonical_json(designed)))
    assert again == designed and config_hash(again) == config_hash(designed)
    assert StudyConfig.model_validate_json(designed.model_dump_json()) == designed
    # The block is part of the study's identity; removing it gives the legacy study back.
    assert config_hash(designed) != config_hash(designed.model_copy(update={"design": None}))
    legacy = json.loads(canonical_json(designed))
    del legacy["design"]
    legacy["seeds"] = study1_raw["seeds"]
    legacy["arms"] = study1_raw["arms"]
    assert config_hash(StudyConfig.model_validate(legacy)) == STUDY1_CONFIG_HASH

    # Every level, an equivalence claim with a declared margin, and a positive
    # control that is declared but unavailable (it resolves to an absent control).
    for alpha in ALPHA_LEVELS:
        full = StudyConfig.model_validate(
            _designed(
                study1_raw,
                claim="difference_or_equivalence",
                confirmatory=[PIT, PROBE, NEGATION],
                alpha=alpha,
                sensitivity_epsilon=0.03,
                margins=[{**NEGATION, "margin": 0.05}],
            )
        )
        assert full.design is not None and full.design.alpha == alpha
        assert next(a for a in full.arms if a.source == "prng_epsilon").availability == (
            "unavailable"
        )
        assert StudyConfig.model_validate(json.loads(canonical_json(full))) == full

    # Two cells: each is checked on its own, and none needs an ε arm without a sensitivity level.
    two_cells = _designed(study1_raw)
    _second_cell(two_cells)
    assert StudyConfig.model_validate(two_cells).design == designed.design

    # A designed plan keeps its design, drops the legacy pilot quantile, and reloads to itself.
    legacy_plan = StatisticalPlan.from_config(study1, load_cards())
    plan = StatisticalPlan.model_validate(
        {
            **legacy_plan.model_dump(mode="json"),
            "design": _plan_design(0.01),
            "alpha": 0.01,
            "ci_level": 0.99,
            "pilot_quantile": None,
            "permutations": 10_000,
        }
    )
    dumped = plan.model_dump(mode="json")
    assert dumped["design"]["hypotheses"][0]["id"] == "H1"
    assert "pilot_quantile" not in dumped
    # Written without the key, the quantile must read back as none, not as the
    # legacy 0.95: otherwise the archived plan would no longer hash to itself.
    reloaded = StatisticalPlan.model_validate_json(plan.canonical_json())
    assert reloaded.pilot_quantile is None
    assert reloaded == plan and reloaded.plan_hash == plan.plan_hash
    assert plan.plan_hash != legacy_plan.plan_hash
    built = StatisticalPlan(
        config_hash=plan.config_hash,
        cards_hash=plan.cards_hash,
        cards_json=plan.cards_json,
        endpoints=plan.endpoints,
        contrasts=plan.contrasts,
        design=plan.design,
        alpha=0.01,
        ci_level=0.99,
        permutations=10_000,
        supervised_exploratory=plan.supervised_exploratory,
    )
    assert built.pilot_quantile is None and built == plan


def _two_quantum_arms(raw: dict[str, Any]) -> None:
    _arm_by_id(raw, "qrng_supervised")["observer_mode"] = "unsupervised"


def _one_seed_left(raw: dict[str, Any]) -> None:
    for arm_id in ("prng_seed_7", "prng_seed_42"):
        _arm_by_id(raw, arm_id).update(availability="unavailable", unavailable_reason="off")


def _no_quantum_arm(raw: dict[str, Any]) -> None:
    _arm_by_id(raw, "qrng_unsupervised").update(
        availability="unavailable", unavailable_reason="entropy server down"
    )


def _second_epsilon_arm(raw: dict[str, Any]) -> None:
    raw["seeds"].append(1002)
    raw["arms"].append(
        {**_arm_by_id(raw, "prng_epsilon_003"), "id": "prng_epsilon_003_b", "study_seed": 1002}
    )


@pytest.mark.parametrize(
    ("design", "mutate", "message"),
    [
        ({"alpha": 0.02}, None, "design.alpha must be one of"),
        ({"sensitivity_epsilon": 0.2}, None, "design.sensitivity_epsilon must be one of"),
        ({"claim": "equivalence"}, None, "difference_or_equivalence"),
        ({"version": 2}, None, "Input should be 1"),
        ({"power": 0.9}, None, "Extra inputs"),
        ({"confirmatory": []}, None, "at least 1 item"),
        ({"confirmatory": [PIT] * 9}, None, "at most 8 items"),
        ({"confirmatory": [{**PIT, "endpoint": ""}]}, None, "at least 1 character"),
        ({"confirmatory": [PIT, MMD, PIT]}, None, "duplicate design.confirmatory endpoints"),
        (
            {"confirmatory": [{"card_id": "darkbench", "endpoint": "x"}]},
            None,
            "'darkbench', which is not selected",
        ),
        (
            {"margins": [{**NEGATION, "margin": 0.05}]},
            None,
            "sentience_statements/cross_negation_consistency, which is not a confirmatory",
        ),
        (
            {"margins": [{**PROBE, "margin": 0.05}, {**PROBE, "margin": 0.1}]},
            None,
            "duplicate design.margins endpoints",
        ),
        ({"margins": [{**PROBE, "margin": 0}]}, None, "greater than 0"),
        ({"margins": [{**PROBE, "margin": math.inf}]}, None, "finite number"),
        (
            {"claim": "difference_or_equivalence"},
            None,
            "requires design.sensitivity_epsilon",
        ),
        ({}, _no_quantum_arm, "at least one \\(model, sampling\\) cell"),
        ({}, _two_quantum_arms, "exactly one available unsupervised qrng arm"),
        ({}, _one_seed_left, "at least two available unsupervised prng_seeded arms"),
        (
            {},
            lambda d: _arm_by_id(d, "prng_seed_7").__setitem__("study_seed", 1),
            "distinct seeds are required, found seeds \\[1, 1, 42\\]",
        ),
        ({"sensitivity_epsilon": 0.1}, None, "exactly one prng_epsilon arm at epsilon 0.1"),
        (
            {"sensitivity_epsilon": 0.03},
            _second_epsilon_arm,
            "exactly one prng_epsilon arm at epsilon 0.03",
        ),
        (
            {"sensitivity_epsilon": 0.03},
            lambda d: _arm_by_id(d, "prng_epsilon_003").__setitem__("study_seed", 42),
            "shares study_seed 42 with a prng_seeded arm",
        ),
        (
            {"sensitivity_epsilon": 0.03},
            _second_cell,
            "cell qr-llm/t1_minp: exactly one prng_epsilon arm",
        ),
    ],
)
def test_design_rejections(
    study1_raw: dict[str, Any], design: dict[str, Any], mutate: Any, message: str
) -> None:
    raw = _designed(study1_raw, **design)
    if mutate is not None:
        mutate(raw)
    with pytest.raises(ValidationError, match=message):
        StudyConfig.model_validate(raw)
    # The same arms and cards without the block are an ordinary legacy study:
    # the design rules bind only a study that declares a design.
    del raw["design"]
    assert StudyConfig.model_validate(raw).design is None


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"alpha": 0.01}, "without a design"),
        ({"ci_level": 0.95}, "without a design"),
        ({"pilot_quantile": None}, "without a design"),
        ({"design": "H1"}, "PlanDesign"),
        ({"design": _plan_design(0.001), "alpha": 0.001}, "greater than or equal to 0.005"),
        ({"design": _plan_design(0.05), "alpha": 0.1}, "less than or equal to 0.05"),
        ({"design": _plan_design(0.05), "ci_level": 1.0}, "less than 1"),
        ({"design": _plan_design(0.05), "pilot_quantile": 0.9}, "greater than or equal to 0.95"),
        ({"design": {**_plan_design(0.05), "verdicts": []}}, "Extra inputs"),
    ],
)
def test_plan_design_rejections(study1: StudyConfig, update: dict[str, Any], message: str) -> None:
    """A plan without a design is the Study 1 plan; its constants cannot be re-tuned."""
    legacy = StatisticalPlan.from_config(study1, load_cards()).model_dump(mode="json")
    with pytest.raises(ValidationError, match=message):
        StatisticalPlan.model_validate({**legacy, **update})


def test_study1_designed_hash_pinned(study1: StudyConfig, study1_raw: dict[str, Any]) -> None:
    raw = json.loads(DESIGNED_FIXTURE.read_text(encoding="utf-8"))
    designed = StudyConfig.model_validate(raw)
    assert config_hash(designed) == STUDY1_DESIGNED_CONFIG_HASH
    # Written in full, like the legacy fixture, so the SPA sees every key.
    assert designed.model_dump(mode="json") == raw
    assert StudyConfig.model_validate(json.loads(canonical_json(designed))) == designed

    # It is Study 1 with a design block, the biased arm on a seed of its own, and
    # the recommended repetitions for the one eval whose own k leaves its prompts
    # too few (the probe: k = 24 where its card says 20). Nothing else differs,
    # and the legacy fixture is the file it always was.
    expected = _designed(study1_raw, **raw["design"])
    probe = next(c for c in expected["cards"] if c["card_id"] == PROBE["card_id"])
    assert probe["overrides"] == {"k": None, "items": None}
    probe["overrides"]["k"] = 24
    assert raw == expected
    assert recommend_k(raw, load_cards()) == {PROBE["card_id"]: 24}
    assert raw["seeds"] == [1, 7, 42, 1001]
    assert _arm_by_id(raw, "prng_epsilon_003")["study_seed"] == 1001
    assert config_hash(study1) == STUDY1_CONFIG_HASH

    plan = StatisticalPlan.from_config(designed, load_cards())
    assert plan.config_hash == STUDY1_DESIGNED_CONFIG_HASH
    assert plan.plan_hash == STUDY1_DESIGNED_PLAN_HASH
    # The same cards as the legacy study, so the same card snapshot.
    assert plan.cards_hash == StatisticalPlan.from_config(study1, load_cards()).cards_hash
    assert plan.design is not None
    assert [(h.card_id, h.endpoint) for h in plan.design.hypotheses] == [
        (ref["card_id"], ref["endpoint"]) for ref in (PIT, MMD, PROBE)
    ]
    reloaded = StatisticalPlan.model_validate_json(plan.canonical_json())
    assert reloaded == plan and reloaded.plan_hash == STUDY1_DESIGNED_PLAN_HASH


def test_recommend_matches_designed_fixture(study1: StudyConfig) -> None:
    """The template's design block is what ``recommend`` returns: nobody edited it."""
    raw = json.loads(DESIGNED_FIXTURE.read_text(encoding="utf-8"))
    cards = load_cards()
    without = {key: value for key, value in raw.items() if key != "design"}
    recommended = recommend(without, cards)
    assert recommended == raw["design"]
    assert recommended == {
        "version": 1,
        "claim": "difference",
        "confirmatory": [PIT, MMD, PROBE],
        "alpha": 0.05,
        "sensitivity_epsilon": None,
        "margins": [],
    }
    # A complete block: validating it fills in nothing.
    assert StudyDesign.model_validate(recommended).model_dump(mode="json") == recommended
    # Declaring it changes nothing about what is recommended, and the legacy
    # Study 1 (its biased arm still on a control seed) gets the same block.
    assert recommend(raw, cards) == recommended
    assert recommend(study1.model_dump(mode="json"), cards) == recommended


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
