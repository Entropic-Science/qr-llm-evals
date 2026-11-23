"""Shared, immutable analysis contract; no execution or higher-layer imports.

The analysis facade and the layer-one prereg renderer use this *same* class.
StudyConfig is consumed through its Pydantic JSON representation to preserve
the layer-zero isolation between cards and models.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from itertools import combinations
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from qr_llm_evals.cards.availability import CARD_ENDPOINTS, ENDPOINT_REQUIREMENTS
from qr_llm_evals.cards.design_schema import PlanDesign
from qr_llm_evals.cards.estimates import CardOverrides, tokens_per_arm
from qr_llm_evals.cards.schema import EvalCard

PRIMARY_TESTS = {
    ("tier0_pit_residuals", "pit_ks_two_sample"): "ks_two_sample",
    ("tier0_mmd_hamming", "mmd_hamming"): "mmd_hamming_permutation",
    ("unpredictabench", "ks100_pass_rate"): "paired_permutation",
    ("novelty_bench", "distinct_k"): "paired_permutation",
}
CONFIRMATORY_SECONDARIES = {
    ("source_guessing", "guess_accuracy"),
    # T4-f variant on self-reports (thesis study); same construct as source_guessing.
    ("source_guessing_self_report", "guess_accuracy"),
    ("opinion_quantum_sampling", "actual_main_effect_axis_score"),
}

SPECIAL_TESTS = {
    ("tier0_pit_residuals", "pit_ks_one_sample"): "ks_uniform",
    ("tier0_pit_residuals", "pit_anderson_darling"): "anderson_darling_uniform",
    ("tier0_pit_residuals", "pit_lag1_autocorr"): "lag1_autocorrelation",
    ("tier0_pit_residuals", "pit_first_token_ks"): "first_token_ks_uniform",
    ("tier0_pit_residuals", "u_uniformity"): "ks_uniform",
    ("tier0_pit_residuals", "u_vs_r_agreement"): "u_vs_residual_agreement",
    ("tier0_mmd_hamming", "mmd_hamming_seed_vs_seed"): "mmd_hamming_permutation",
    ("stream_deviation_windows", "stouffer_z_supervised_vs_unsupervised"): "stouffer_z_windows",
    ("stream_deviation_windows", "window_permutation_p"): "window_permutation",
    ("stream_deviation_windows", "cumulative_deviation"): "descriptive_cumulative_deviation",
    ("target_hit_ranking", "rank1_hit_rate"): "blinded_rank_hit_vs_chance",
    ("target_hit_ranking", "mean_rank"): "blinded_rank_vs_chance",
    ("self_reported_coincidence", "observer_wcs_score"): "descriptive_by_belief",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class EndpointPlan(_Frozen):
    card_id: str
    endpoint: str
    tier: int
    role: Literal["primary", "secondary_confirmatory", "exploratory"]
    test: str
    correction: Literal["holm", "bh", "none"]
    family: str
    requires_tier0: str
    sesoi: float | None = Field(default=None, ge=0)
    equivalence: Literal["tost", "mmd_bootstrap", "unavailable"]
    k: int | None
    items: int
    told_effect_first: bool = False


class Contrast(_Frozen):
    kind: Literal["source", "seed_vs_seed", "epsilon", "supervised"]
    model: str
    sampling: str
    a: tuple[str, ...]
    b: tuple[str, ...]
    epsilon: float | None = None


class StatisticalPlan(_Frozen):
    schema_version: Literal[1] = 1
    config_hash: str
    cards_hash: str
    cards_json: str = Field(
        description="Canonical selected card snapshots; archive, do not reload a newer registry."
    )
    endpoints: tuple[EndpointPlan, ...]
    contrasts: tuple[Contrast, ...]
    design: PlanDesign | None = Field(
        default=None,
        description="Set for a study with a design block; absent on a legacy plan.",
    )
    alpha: float = Field(default=0.05, ge=0.005, le=0.05)
    permutations: int = Field(default=2000, ge=2000)
    bootstrap_replicates: Literal[10000] = 10000
    random_seed: Literal[42] = 42
    ci_level: float = Field(default=0.90, ge=0.90, lt=1)
    pilot_quantile: float | None = Field(
        default=0.95,
        ge=0.95,
        le=0.95,
        description="Legacy pilot-derived margins only; a designed plan fixes its margins.",
    )
    margin_rule: str = (
        "max(card SESOI, pilot 95th percentile of absolute seed-vs-seed paired differences)"
    )
    missing_margin_rule: str = (
        "No equivalence claim without a frozen SESOI and independent pilot differences."
    )
    pairing: str = "card/prompt/epoch/told; labels permuted within prompt; prompt-clustered SEs"
    supervised_unit: str = "session; observers sharing a session are not independent replicates"
    source_family: str = (
        "Holm across all selected primary source tests, including unavailable members"
    )
    supervised_family: str = "Separate Holm family across the same primary endpoints for A vs B"
    claim_rule: str = "Claim a QRNG effect only after Holm correction and if its magnitude exceeds the maximum of all three seed-vs-seed contrasts; missing controls preclude the claim."
    epsilon_rule: str = "Report each primary's epsilon curve only for available biased arms; otherwise explicitly unavailable."
    readiness_rule: str = "Configured contrasts are conditional on supplied readiness and recorded provenance; missing/failed arm or endpoint readiness is not a measured zero. Epsilon contrasts require epsilon_supported."
    supervised_exploratory: tuple[str, ...] = ()
    intent_embedding: str | None = None
    intent_rule: str = "Intent cosine unavailable until an exact embedding model revision and intention texts are frozen before generation."
    exclusion_rule: str = "Exclude fallback/degraded/error/aborted samples from clean primary analysis; report taints, counts and sensitivity analysis separately; never delete artifacts."
    stopping_rule: str = "Fixed selected items and k for each available arm; no significance-based stopping; pilot excluded from full-run tests; interruptions and incomplete pairs reported."
    unavailable_family_rule: str = "Unavailable tests retain their family slots (p=1 for correction only, not fabricated measurements)."

    @model_validator(mode="before")
    @classmethod
    def _no_pilot_quantile_when_designed(cls, data: Any) -> Any:
        # A designed plan has no pilot quantile and is written without the key.
        # Read back, the missing key must mean "none", not the legacy default,
        # or the reloaded plan would hash differently from the registered one.
        if (
            isinstance(data, Mapping)
            and data.get("design") is not None
            and "pilot_quantile" not in data
        ):
            return {**data, "pilot_quantile": None}
        return data

    @model_validator(mode="after")
    def _check_legacy_constants(self) -> StatisticalPlan:
        # The bounds above are wide enough for a designed plan. A plan without
        # a design is the Study 1 plan, and its three constants never moved.
        if self.design is None and (self.alpha, self.ci_level, self.pilot_quantile) != (
            0.05,
            0.90,
            0.95,
        ):
            raise ValueError(
                "a plan without a design must keep alpha 0.05, ci_level 0.90 and "
                "pilot_quantile 0.95"
            )
        return self

    # An archived legacy plan has no "design" key and its hash names its
    # analysis, so the key stays out when there is no design. No return
    # annotation on purpose: pydantic would take it as the model's
    # serialization schema, and the OpenAPI document would lose every field.
    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        data = handler(self)
        for key in ("design", "pilot_quantile"):
            if data.get(key) is None:
                data.pop(key, None)
        return data

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_config(cls, config: BaseModel, cards: Mapping[str, EvalCard]) -> StatisticalPlan:
        data = config.model_dump(mode="json")
        if data.get("design") is None:
            return cls._legacy(data, cards)
        # Imported here: the projection is built on this module.
        from qr_llm_evals.cards.design import project

        return project(cls, data, cards)

    @classmethod
    def _legacy(cls, data: Mapping[str, Any], cards: Mapping[str, EvalCard]) -> StatisticalPlan:
        """The built-in Study 1 plan: the plan of every study without a design block.

        Its bytes are pinned (``backend/tests/test_legacy_bytes.py``). A designed
        study starts from the contrasts and card snapshots built here.
        """
        endpoints: list[EndpointPlan] = []
        snapshots = []
        for selection in data["cards"]:
            card_id = selection["card_id"]
            if card_id not in cards:
                raise ValueError(f"unknown card {card_id!r}")
            card = cards[card_id]
            overrides = CardOverrides.model_validate(selection["overrides"])
            tokens_per_arm(card, overrides)  # Validate k/items even for unavailable cards.
            k = overrides.k if overrides.k is not None else card.k
            items = overrides.items if overrides.items is not None else card.dataset.items
            snapshots.append(card.model_dump(mode="json"))
            for endpoint in card.endpoints.all:
                key = (card.id, endpoint)
                primary = key in PRIMARY_TESTS
                confirmatory = key in CONFIRMATORY_SECONDARIES
                role: Literal["primary", "secondary_confirmatory", "exploratory"] = (
                    "primary"
                    if primary
                    else "secondary_confirmatory"
                    if confirmatory
                    else "exploratory"
                )
                correction: Literal["holm", "bh", "none"] = (
                    "holm" if primary else "none" if card.tier == 4 and not confirmatory else "bh"
                )
                test = {**SPECIAL_TESTS, **PRIMARY_TESTS}.get(
                    key,
                    "paired_binomial"
                    if card.paired_stat == "paired_binomial"
                    else "paired_permutation",
                )
                endpoints.append(
                    EndpointPlan(
                        card_id=card.id,
                        endpoint=endpoint,
                        tier=card.tier,
                        role=role,
                        test=test,
                        correction=correction,
                        family="source_primaries" if primary else f"tier_{card.tier}_secondaries",
                        requires_tier0=ENDPOINT_REQUIREMENTS[endpoint]
                        if card.id in CARD_ENDPOINTS
                        else card.endpoints.requires_tier0,
                        sesoi=card.sesoi.get(endpoint),
                        equivalence="mmd_bootstrap"
                        if endpoint == "mmd_hamming"
                        else "tost"
                        if endpoint in card.sesoi
                        else "unavailable",
                        k=k,
                        items=items,
                        told_effect_first=card.told_actual,
                    )
                )
        contrasts: list[Contrast] = []
        arms = data["arms"]
        # Never pool different models, sampling presets or unavailable arms.
        for model, sampling in sorted({(a["model"], a["sampling"]) for a in arms}):
            group = [
                a
                for a in arms
                if a["model"] == model
                and a["sampling"] == sampling
                and a["availability"] == "available"
            ]
            prng = [
                a
                for a in group
                if a["source"] == "prng_seeded" and a["observer_mode"] == "unsupervised"
            ]
            qrng = [a for a in group if a["source"] == "qrng"]
            baseline = [a for a in qrng if a["observer_mode"] == "unsupervised"]

            def add(
                kind: Literal["source", "seed_vs_seed", "epsilon", "supervised"],
                a: tuple[str, ...],
                b: tuple[str, ...],
                epsilon: float | None = None,
                model: str = model,
                sampling: str = sampling,
            ) -> None:
                if a and b:
                    contrasts.append(
                        Contrast(
                            kind=kind, model=model, sampling=sampling, a=a, b=b, epsilon=epsilon
                        )
                    )

            for a in baseline or qrng:
                add("source", (a["id"],), tuple(p["id"] for p in prng))
            for a, b in combinations(prng, 2):
                if a["study_seed"] != b["study_seed"]:
                    add("seed_vs_seed", (a["id"],), (b["id"],))
            for a in group:
                if a["source"] == "prng_epsilon":
                    add("epsilon", (a["id"],), tuple(p["id"] for p in prng), a["epsilon"])
            for a in qrng:
                if a["observer_mode"] == "supervised":
                    for b in baseline:
                        add("supervised", (a["id"],), (b["id"],))

        def digest(value: object) -> str:
            encoded = json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        supervised = data.get("supervised") is not None
        return cls(
            config_hash=digest(data),
            cards_hash=digest(snapshots),
            cards_json=json.dumps(
                snapshots,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            endpoints=tuple(endpoints),
            contrasts=tuple(contrasts),
            supervised_exploratory=(
                "intent_cosine",
                "stouffer_z_windows",
                "cumulative_deviation",
                "blinded_target_rank",
                "questionnaire_descriptives_by_belief",
            )
            if supervised
            else (),
        )


def plan_hash(plan: StatisticalPlan) -> str:
    return plan.plan_hash
