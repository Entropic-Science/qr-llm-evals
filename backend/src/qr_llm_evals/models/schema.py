"""The frozen study configuration and its canonical hash (spec §4.1, §2.8, D-11).

A :class:`StudyConfig` is what the SPA builds, the prereg is rendered from and
the study record freezes at launch. Two properties matter more than anything
else here:

* **Strictness.** ``extra="forbid"`` and closed ``Literal`` enums, so an
  unknown key or an inadmissible combination (``greedy`` on the QRNG source, a
  seeded arm without a study seed, an ε value outside the pre-registered grid)
  fails at parse time rather than at run time.
* **Canonical bytes.** :func:`canonical_json` renders a config with sorted keys,
  no whitespace and NaN forbidden; :func:`config_hash` is the SHA-256 of those
  UTF-8 bytes. The prereg embeds this hash, and any later config diff flips
  ``prereg_stale`` on the run (spec §2.8). The vectors in
  ``backend/tests/test_schemas.py`` pin the byte layout.

Layer 0: this module imports nothing internal. ``EstimateAssumptions`` is
therefore declared here as well as in :mod:`qr_llm_evals.cards.estimates`;
``test_schemas.py`` asserts the two JSON Schemas are identical so they cannot
drift apart.

``scripts/export_schemas.py`` exports these models for ``@qr-evals/contracts``;
keep field names stable and add a CHANGELOG entry when they change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

# ── shared identifiers and enums ─────────────────────────────────────────────

Slug = Annotated[str, Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=64)]
CardId = Slug
ArmId = Slug
#: ``"qr-llm"`` | ``"openai:gpt-…"`` | ``"custom:<base_url>#<model>"``.
ModelId = Annotated[str, Field(min_length=1, max_length=256)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

ModelFamily = Literal["qr_llm", "external"]
Provider = Literal["qr_server", "openai", "anthropic_compat", "custom"]
Sampling = Literal["creative", "t1_minp", "greedy"]
Source = Literal["qrng", "prng_seeded", "prng_system", "prng_epsilon", "provider"]
ObserverMode = Literal["supervised", "unsupervised"]
Availability = Literal["available", "unavailable"]
Display = Literal["single", "tiled"]
TextSize = Literal["sm", "md", "lg"]
StudyTemplate = Literal["study1"]
DataLicence = Literal["CC-BY-4.0"]

#: Achieved engine determinism (spec §2.2, §2.7 step 4); recorded, never assumed.
DeterminismTier = Literal["none", "sampling_deterministic", "serialized_replay", "load_invariant"]
#: Where Tier 0 records come from (spec D-8).
Tier0Source = Literal["engine_log", "api_logprobs"]
#: Provenance labels per source (spec §2.2). ``quantum`` is the only label that
#: may ever carry ``quantum=true`` (AGENTS.md invariant 2).
SourceLabel = Literal[
    "quantum", "seeded_prng", "system_prng", "seeded_prng_biased", "provider_prng"
]
SOURCE_LABELS: dict[str, str] = {
    "qrng": "quantum",
    "prng_seeded": "seeded_prng",
    "prng_system": "system_prng",
    "prng_epsilon": "seeded_prng_biased",
    "provider": "provider_prng",
}

#: Study 1 seeds (PRD §7.1); each seed runs the full suite.
DEFAULT_SEEDS: tuple[int, ...] = (1, 7, 42)
#: Pre-registered ε grid for the biased-PRNG positive control (spec §4.1 Arm.epsilon).
#: ε is a shift of the amplifier's z in standard errors (``u' = Φ(z + ε)``,
#: ``models/envelope.bent_population_mean``); 0.50 is the thesis study's strong bend.
EPSILON_LEVELS: tuple[float, ...] = (0.01, 0.03, 0.10, 0.50)
#: ``qr_seed`` and study seeds are non-negative 63-bit integers (spec §2.2 derive_seed).
SEED_MAX_EXCLUSIVE = 2**63
#: Sources whose randomness is fixed by a study seed.
SEEDED_SOURCES: frozenset[str] = frozenset({"prng_seeded", "prng_epsilon"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── §4.1 nested models ───────────────────────────────────────────────────────


class ModelRef(_Strict):
    """A model the study talks to; ``served_name`` is the exact wire string."""

    id: ModelId
    family: ModelFamily
    provider: Provider
    served_name: str = Field(min_length=1)
    checkpoint: str | None = Field(
        default=None, description="e.g. HF id + revision, frozen at readiness."
    )


class Arm(_Strict):
    """``(model, sampling, source, determinism, observer_mode, seed?)`` — spec §2.2."""

    id: ArmId
    model: ModelId = Field(description="A ModelRef.id from StudyConfig.models.")
    sampling: Sampling
    source: Source
    study_seed: int | None = Field(
        default=None,
        ge=0,
        lt=SEED_MAX_EXCLUSIVE,
        description="Required for prng_seeded / prng_epsilon; per-request qr_seed is derived from it.",
    )
    epsilon: float | None = Field(
        default=None, description="prng_epsilon only; one of EPSILON_LEVELS."
    )
    deterministic: bool = False
    observer_mode: ObserverMode = "unsupervised"
    availability: Availability = Field(
        default="available",
        description="Filled by the readiness check; unavailable arms are listed, not run.",
    )
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _check_combination(self) -> Arm:
        if self.source in SEEDED_SOURCES and self.study_seed is None:
            raise ValueError(f"arm {self.id!r}: source {self.source!r} requires study_seed")
        if self.source not in SEEDED_SOURCES and self.study_seed is not None:
            raise ValueError(
                f"arm {self.id!r}: study_seed is meaningless for source {self.source!r}"
            )
        if self.source == "prng_epsilon":
            if self.epsilon is None or not any(
                abs(self.epsilon - e) < 1e-12 for e in EPSILON_LEVELS
            ):
                raise ValueError(
                    f"arm {self.id!r}: prng_epsilon requires epsilon in {list(EPSILON_LEVELS)}"
                )
        elif self.epsilon is not None:
            raise ValueError(f"arm {self.id!r}: epsilon is only valid for source 'prng_epsilon'")
        if self.sampling == "greedy" and self.source == "qrng":
            raise ValueError(f"arm {self.id!r}: greedy consults no randomness (not a QRNG arm)")
        if self.availability == "unavailable" and not self.unavailable_reason:
            raise ValueError(f"arm {self.id!r}: unavailable arms must carry unavailable_reason")
        return self

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS[self.source]


class CardOverrides(_Strict):
    """Frozen per-card overrides, applied within the card's declared bounds."""

    k: int | None = Field(default=None, ge=1)
    items: int | None = Field(default=None, ge=1)


class CardSelection(_Strict):
    card_id: CardId
    overrides: CardOverrides = Field(default_factory=CardOverrides)


#: The study judge when a study names none: the current Claude Haiku.
DEFAULT_JUDGE_MODEL = "claude-haiku-5-5"
JudgeThinking = Literal["disabled", "model_default"]


class JudgeSpec(_Strict):
    """The study's shared judge: an Anthropic Messages API call on the user's own key.

    Every field is part of the study configuration and so of its
    pre-registration hash; a judge cannot be re-tuned after registration
    without the hashes changing.
    """

    provider: Literal["anthropic"] = "anthropic"
    model: str = Field(default=DEFAULT_JUDGE_MODEL, min_length=1)
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Sampling temperature sent with each judge call; None sends none. Current Claude "
            "models reject the parameter, so it is only for older models that accept it."
        ),
    )
    max_tokens: int = Field(
        default=1024, ge=64, le=16000, description="Output cap of one judge call."
    )
    thinking: JudgeThinking = Field(
        default="disabled",
        description=(
            "'disabled' asks for a single pass with no extended thinking; 'model_default' sends "
            "nothing and lets the model decide (needed for models that refuse 'disabled')."
        ),
    )
    rubric_hash: Sha256Hex | None = None


class DisplaySettings(_Strict):
    """Per-display supervised settings (FR-5.5)."""

    session_min: int = Field(ge=1)
    break_min: int = Field(ge=0)
    streams: int = Field(ge=1)
    text_size: TextSize
    show_thinking: bool


class SettingsByDisplay(_Strict):
    single: DisplaySettings = Field(
        default_factory=lambda: DisplaySettings(
            session_min=30, break_min=10, streams=1, text_size="md", show_thinking=True
        )
    )
    tiled: DisplaySettings = Field(
        default_factory=lambda: DisplaySettings(
            session_min=30, break_min=10, streams=4, text_size="sm", show_thinking=False
        )
    )


class SupervisedConfig(_Strict):
    display: Display = "single"
    settings_by_display: SettingsByDisplay = Field(default_factory=SettingsByDisplay)
    intention_timer_min: int = Field(default=3, ge=1, description="2–5 recommended.")
    refocus_s: int = Field(default=30, ge=0)
    group: bool = False
    target_pool_hash: Sha256Hex | None = Field(
        default=None, description="sha256 of evals/_pools/targets_200.yaml (LF bytes)."
    )
    questionnaires_hash: Sha256Hex | None = Field(
        default=None, description="sha256 of config/questionnaires.yaml (LF bytes)."
    )

    @property
    def settings(self) -> DisplaySettings:
        return getattr(self.settings_by_display, self.display)  # type: ignore[no-any-return]


class EstimateAssumptions(_Strict):
    """Mirror of ``cards.estimates.EstimateAssumptions`` (layer-0 rule forbids the import)."""

    tps_by_model: dict[str, float] = Field(
        default_factory=lambda: {"qr-llm": 400.0},
        description="Aggregate generated tokens/s assumed per model id.",
    )
    reading_tps: float = Field(default=45.0, gt=0)
    tiled_streams: int = Field(default=4, ge=1)
    session_min: int = Field(default=30, ge=1)


class PilotConfig(_Strict):
    enabled: bool = True
    prompts: int = Field(default=200, ge=1)


# ── §4.1 StudyConfig ─────────────────────────────────────────────────────────


class StudyConfig(_Strict):
    """Everything the prereg freezes. ``config_hash(study)`` is its identity."""

    schema_version: Literal[1] = 1
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    contact: str = Field(min_length=1)
    created_at: AwareDatetime
    template: StudyTemplate | None = None
    models: list[ModelRef] = Field(min_length=1)
    arms: list[Arm] = Field(min_length=1)
    cards: list[CardSelection] = Field(min_length=1)
    judge: JudgeSpec
    narrative_model: ModelRef
    supervised: SupervisedConfig | None = None
    seeds: list[int] = Field(default_factory=lambda: list(DEFAULT_SEEDS), min_length=1)
    estimates: EstimateAssumptions = Field(default_factory=EstimateAssumptions)
    pilot: PilotConfig = Field(default_factory=PilotConfig)
    expectations: str = Field(
        default="", description="Researchers' prior expectations, free text, frozen."
    )
    data_licence: DataLicence = "CC-BY-4.0"

    @model_validator(mode="after")
    def _check_cross_references(self) -> StudyConfig:
        problems: list[str] = []
        model_ids = [m.id for m in self.models]
        for label, ids in (
            ("model", model_ids),
            ("arm", [a.id for a in self.arms]),
            ("card", [c.card_id for c in self.cards]),
        ):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                problems.append(f"duplicate {label} ids: {dupes}")
        for seed in self.seeds:
            if not 0 <= seed < SEED_MAX_EXCLUSIVE:
                problems.append(f"seed {seed} outside [0, 2**63)")
        if len(set(self.seeds)) != len(self.seeds):
            problems.append("seeds must be distinct")
        for arm in self.arms:
            if arm.model not in model_ids:
                problems.append(f"arm {arm.id!r} references unknown model {arm.model!r}")
            if arm.observer_mode == "supervised" and self.supervised is None:
                problems.append(f"arm {arm.id!r} is supervised but supervised config is null")
            if arm.study_seed is not None and arm.study_seed not in self.seeds:
                problems.append(f"arm {arm.id!r} study_seed {arm.study_seed} not in seeds")
        if problems:
            raise ValueError("; ".join(problems))
        return self


# ── canonical JSON + hash (spec §2.8, D-11) ──────────────────────────────────


def canonical_json(obj: BaseModel | Mapping[str, Any]) -> str:
    """Sorted keys, no whitespace, NaN/Infinity forbidden, UTF-8 (no ASCII escaping).

    The same bytes for the same config on every platform; this is what
    :func:`config_hash` hashes and what ``study.json`` stores.
    """
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else dict(obj)
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def config_hash(obj: BaseModel | Mapping[str, Any]) -> str:
    """``sha256(canonical_json(obj))`` as lowercase hex."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_SEEDS",
    "EPSILON_LEVELS",
    "SEED_MAX_EXCLUSIVE",
    "SEEDED_SOURCES",
    "SOURCE_LABELS",
    "Arm",
    "ArmId",
    "Availability",
    "CardId",
    "CardOverrides",
    "CardSelection",
    "DataLicence",
    "DeterminismTier",
    "Display",
    "DisplaySettings",
    "EstimateAssumptions",
    "DEFAULT_JUDGE_MODEL",
    "JudgeSpec",
    "JudgeThinking",
    "ModelFamily",
    "ModelId",
    "ModelRef",
    "ObserverMode",
    "PilotConfig",
    "Provider",
    "Sampling",
    "SettingsByDisplay",
    "Sha256Hex",
    "Source",
    "SourceLabel",
    "StudyConfig",
    "StudyTemplate",
    "SupervisedConfig",
    "TextSize",
    "Tier0Source",
    "canonical_json",
    "config_hash",
]
