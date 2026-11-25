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
* **Legacy bytes.** A config without a ``design`` block is a study from before
  the block existed and serializes without the key, so its hash, its study id
  and its frozen ``study.json`` are what they always were
  (``backend/tests/test_legacy_bytes.py``). A new optional field must follow the
  same rule: ``None`` by default and omitted when ``None``.

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

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

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
Source = Literal["qrng", "prng_seeded", "prng_system", "prng_epsilon", "prng_resample", "provider"]
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
    "quantum",
    "seeded_prng",
    "system_prng",
    "seeded_prng_biased",
    "resampled_prng",
    "provider_prng",
]
SOURCE_LABELS: dict[str, str] = {
    "qrng": "quantum",
    "prng_seeded": "seeded_prng",
    "prng_system": "system_prng",
    "prng_epsilon": "seeded_prng_biased",
    "prng_resample": "resampled_prng",
    "provider": "provider_prng",
}

#: Entropy conditioning (what the entropy server does to its raw bytes before
#: serving them). SOURCE: qr-sampler ``src/qr_sampler/config/conditioning.py``
#: ``CONDITIONING_MODES`` / ``CONDITIONING_HASHES`` (digest size in bytes).
ConditioningMode = Literal["raw", "hash_truncate"]
ConditioningHash = Literal["sha256", "sha512", "sha3_256", "blake2b"]
CONDITIONING_DIGEST_BYTES: dict[str, int] = {
    "sha256": 32,
    "sha512": 64,
    "sha3_256": 32,
    "blake2b": 64,
}
#: Largest raw block one digest may be computed over (Qbert0G
#: ``conditioning.max_in_bytes`` on the box).
CONDITIONING_MAX_IN_BYTES = 4096
#: Sources whose bytes come from the entropy server, so conditioning applies.
CONDITIONED_SOURCES: frozenset[str] = frozenset({"qrng", "prng_resample"})

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
#: Significance levels a designed study may register.
ALPHA_LEVELS: tuple[float, ...] = (0.05, 0.01, 0.005)
#: The most a study may set as an eval's token limit. ``cards/token_limits.py``
#: owns the number (``TOKEN_LIMIT_MAX``); it is repeated here because layer 0
#: modules do not import each other, and ``test_token_limits.py`` compares them.
MAX_TOKENS_CEILING = 64_000


def _on_grid(value: float, levels: tuple[float, ...]) -> bool:
    """Floats on a registered grid are compared with a tolerance, never as literals."""
    return any(abs(value - level) < 1e-12 for level in levels)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


#: Validation-context key of a draft read (:func:`read_draft`): a list that the
#: cross-field checks of ``StudyConfig`` add their problems to instead of raising.
DRAFT_PROBLEMS = "draft_problems"


def _settle(problems: list[str], info: ValidationInfo) -> None:
    """Raise the cross-field problems of a study, or hand them to a draft read."""
    if not problems:
        return
    collected = (info.context or {}).get(DRAFT_PROBLEMS)
    if isinstance(collected, list):
        collected.extend(problems)
        return
    raise ValueError("; ".join(problems))


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


class Conditioning(_Strict):
    """How entropy-server bytes are conditioned for every arm that draws them.

    ``hash_truncate`` (the default of a new study): the server cuts its raw
    read into ``in_bytes`` blocks, hashes each on its own and serves the first
    ``keep_bytes`` of each digest, so the amplifier sees uniform bytes whatever
    the device's own byte distribution is. ``raw`` serves the measured bytes
    untouched; the parameters are then unused and keep their defaults.

    Two rules are fixed and mirror the server's: at most half of every digest
    is kept, and every block holds at least twice the bytes it yields.
    """

    mode: ConditioningMode = "hash_truncate"
    hash: ConditioningHash = "sha256"
    in_bytes: int = Field(default=128, ge=2, le=CONDITIONING_MAX_IN_BYTES)
    keep_bytes: int = Field(default=16, ge=1)

    @model_validator(mode="after")
    def _check_rules(self) -> Conditioning:
        if self.mode == "raw":
            return self
        half = CONDITIONING_DIGEST_BYTES[self.hash] // 2
        if self.keep_bytes > half:
            raise ValueError(
                "Serving more than half of each digest may compromise the integrity of the "
                f"signal; keep at most {half} bytes of a {self.hash} digest "
                f"(conditioning.keep_bytes is {self.keep_bytes})."
            )
        if self.in_bytes < 2 * self.keep_bytes:
            raise ValueError(
                "Every hashed block must hold at least twice the bytes kept from it; "
                f"conditioning.in_bytes must be at least {2 * self.keep_bytes}, "
                f"got {self.in_bytes}."
            )
        return self

    def read_ratio(self) -> str:
        """Raw device bytes read per byte served, as text (``8``, ``2.5``; ``1`` for raw)."""
        if self.mode == "raw":
            return "1"
        return f"{self.in_bytes / self.keep_bytes:g}"


class CardOverrides(_Strict):
    """Frozen per-card overrides, applied within the card's declared bounds."""

    k: int | None = Field(default=None, ge=1)
    items: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_TOKENS_CEILING,
        description=(
            "Token limit of one reply of this eval; absent means the card's own max_tokens. "
            "Never serialized as null, so a study that sets none keeps its bytes."
        ),
    )

    # No return annotation, for the reason given at ``StudyConfig._omit_absent_design``.
    @model_serializer(mode="wrap")
    def _omit_absent_max_tokens(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        data = handler(self)
        if data.get("max_tokens") is None:
            data.pop("max_tokens", None)
        return data


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


# ── the design block ─────────────────────────────────────────────────────────


class EndpointRef(_Strict):
    """One endpoint of one selected card, named as the card names it."""

    card_id: CardId
    endpoint: str = Field(min_length=1)


class MarginDecl(EndpointRef):
    """An equivalence margin declared for an endpoint whose card states no SESOI."""

    margin: float = Field(gt=0, allow_inf_nan=False)


class StudyDesign(_Strict):
    """What the study claims and tests, declared by the researcher before launch.

    Without this block the analysis plan is the built-in Study 1 plan. With it,
    the plan is projected from these declarations: the confirmatory endpoints,
    the significance level, whether the absence of an effect may be claimed, and
    the biased-sampler level that has to be detected for that claim to stand.
    Whether an endpoint can carry a confirmatory test is a property of its card
    and is checked where the plan is built; this layer cannot see the cards.
    """

    version: Literal[1] = 1
    claim: Literal["difference", "difference_or_equivalence"]
    confirmatory: list[EndpointRef] = Field(min_length=1, max_length=8)
    alpha: float = Field(default=0.05, json_schema_extra={"enum": list(ALPHA_LEVELS)})
    sensitivity_epsilon: float | None = Field(
        default=None,
        json_schema_extra={"enum": [*EPSILON_LEVELS, None]},
        description="The ε of the biased arm that serves as the positive control.",
    )
    margins: list[MarginDecl] = Field(default_factory=list)

    # A level off its grid is a malformed value of one field, like a claim that
    # is not one of the two. It is refused here and not with the cross-field
    # checks of ``StudyConfig``, which a draft read reports instead of raising:
    # the setup page holds both fields as enums and could not show such a draft.
    @field_validator("alpha")
    @classmethod
    def _alpha_on_grid(cls, value: float) -> float:
        if not _on_grid(value, ALPHA_LEVELS):
            raise ValueError(f"design.alpha must be one of {list(ALPHA_LEVELS)}")
        return value

    @field_validator("sensitivity_epsilon")
    @classmethod
    def _epsilon_on_grid(cls, value: float | None) -> float | None:
        if value is not None and not _on_grid(value, EPSILON_LEVELS):
            raise ValueError(
                f"design.sensitivity_epsilon must be one of {list(EPSILON_LEVELS)} or null"
            )
        return value


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
    design: StudyDesign | None = Field(
        default=None,
        description="Absent on studies from before the design block; never serialized as null.",
    )
    conditioning: Conditioning | None = Field(
        default=None,
        description=(
            "Entropy conditioning of the arms that draw from the entropy server. Absent "
            "on studies from before the field (they ran on raw bytes); never serialized as null."
        ),
    )
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
    def _check_cross_references(self, info: ValidationInfo) -> StudyConfig:
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
        _settle(problems, info)
        return self

    @model_validator(mode="after")
    def _check_design(self, info: ValidationInfo) -> StudyConfig:
        design = self.design
        if design is None:
            return self
        problems: list[str] = []
        epsilon = design.sensitivity_epsilon
        if design.claim == "difference_or_equivalence" and epsilon is None:
            problems.append(
                "design.claim 'difference_or_equivalence' requires design.sensitivity_epsilon"
            )

        selected = {c.card_id for c in self.cards}
        confirmatory = [(r.card_id, r.endpoint) for r in design.confirmatory]
        margins = [(m.card_id, m.endpoint) for m in design.margins]
        for label, refs in (("confirmatory", confirmatory), ("margins", margins)):
            dupes = sorted({"/".join(ref) for ref in refs if refs.count(ref) > 1})
            if dupes:
                problems.append(f"duplicate design.{label} endpoints: {dupes}")
        for card_id in dict.fromkeys(card_id for card_id, _ in confirmatory):
            if card_id not in selected:
                problems.append(
                    f"design.confirmatory names card {card_id!r}, which is not selected"
                )
        for ref in dict.fromkeys(margins):
            if ref not in confirmatory:
                problems.append(
                    f"design.margins names {'/'.join(ref)}, which is not a confirmatory endpoint"
                )

        # A source cell is one (model, sampling) pair in which quantum sampling
        # is compared with seeded pseudo-random sampling. Cells are never pooled.
        def compared(arm: Arm, source: str) -> bool:
            return (
                arm.source == source
                and arm.observer_mode == "unsupervised"
                and arm.availability == "available"
            )

        cells = sorted({(a.model, a.sampling) for a in self.arms if compared(a, "qrng")})
        if not cells:
            problems.append(
                "design needs at least one (model, sampling) cell with an available "
                "unsupervised qrng arm"
            )
        for model, sampling in cells:
            cell = f"cell {model}/{sampling}"
            arms = [a for a in self.arms if a.model == model and a.sampling == sampling]
            quantum = [a.id for a in arms if compared(a, "qrng")]
            if len(quantum) != 1:
                problems.append(
                    f"{cell}: exactly one available unsupervised qrng arm is required, "
                    f"found {quantum}"
                )
            seeds = [a.study_seed for a in arms if compared(a, "prng_seeded")]
            if len(seeds) < 2 or len(set(seeds)) != len(seeds):
                problems.append(
                    f"{cell}: at least two available unsupervised prng_seeded arms with "
                    f"distinct seeds are required, found seeds {seeds}"
                )
            if epsilon is None:
                continue
            # The positive control may be unavailable (it then resolves to an
            # absent control), but it must be declared, and on a seed of its
            # own: sharing a seed with a control arm ties its stream to that arm.
            biased = [
                a
                for a in arms
                if a.source == "prng_epsilon"
                and a.epsilon is not None
                and abs(a.epsilon - epsilon) < 1e-12
            ]
            if len(biased) != 1:
                problems.append(
                    f"{cell}: exactly one prng_epsilon arm at epsilon {epsilon} is required, "
                    f"found {[a.id for a in biased]}"
                )
            elif biased[0].study_seed in {a.study_seed for a in arms if a.source == "prng_seeded"}:
                problems.append(
                    f"{cell}: prng_epsilon arm {biased[0].id!r} shares study_seed "
                    f"{biased[0].study_seed} with a prng_seeded arm"
                )
        _settle(problems, info)
        return self

    # No return annotation on purpose: pydantic would take it as the model's
    # serialization schema, and the OpenAPI document would lose every field.
    @model_serializer(mode="wrap")
    def _omit_absent_design(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        data = handler(self)
        if data.get("design") is None:
            data.pop("design", None)
        if data.get("conditioning") is None:
            data.pop("conditioning", None)
        return data


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


def read_draft(raw: Any) -> tuple[StudyConfig, list[str]]:
    """``raw`` as a study that may still break the rules that span several fields.

    A study that is being put together can be well formed and not yet
    consistent: an outcome names an eval that was just unticked, the claim
    needs a control that is not there yet. The setup page holds such a draft
    and saves it to a file, so the file has to load again. This reads it:
    every field is validated exactly as ``StudyConfig.model_validate`` does
    (types, ranges, required fields, unknown keys, a level that is not on its
    grid, and the rules of a single arm), so anything malformed still raises
    ``ValidationError``, and the two
    cross-field checks of ``StudyConfig`` (references between models, arms,
    seeds and evals, and the design block against the arms and evals) report
    what they find instead of raising.

    Returns the study with its defaults filled in and the problems, in the
    words the strict validation uses. No problems means the study is valid. A
    study that has problems is a draft: it must not be launched, frozen or
    planned, and every other caller keeps the strict validation.
    """
    problems: list[str] = []
    study = StudyConfig.model_validate(raw, context={DRAFT_PROBLEMS: problems})
    return study, problems


__all__ = [
    "ALPHA_LEVELS",
    "CONDITIONED_SOURCES",
    "CONDITIONING_DIGEST_BYTES",
    "CONDITIONING_MAX_IN_BYTES",
    "Conditioning",
    "ConditioningHash",
    "ConditioningMode",
    "DEFAULT_SEEDS",
    "DRAFT_PROBLEMS",
    "EPSILON_LEVELS",
    "MAX_TOKENS_CEILING",
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
    "EndpointRef",
    "EstimateAssumptions",
    "DEFAULT_JUDGE_MODEL",
    "JudgeSpec",
    "JudgeThinking",
    "MarginDecl",
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
    "StudyDesign",
    "StudyTemplate",
    "SupervisedConfig",
    "TextSize",
    "Tier0Source",
    "canonical_json",
    "config_hash",
]
