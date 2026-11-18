"""Run-time records persisted by the store (spec §4.4, §4.5).

``Run`` lives in ``runs/<run_id>/run.json`` (and the SQLite ``runs`` index),
``GenerationRow`` / ``JudgeRow`` are JSONL lines per ``(arm, card)``, ``Taint``
lines go to ``taints.jsonl``, and ``ReadinessReport`` is ``readiness.json``.

Two AGENTS.md invariants are enforced *here*, in the schema, so no code path can
violate them by accident:

* **Never label PRNG output as quantum** — :class:`Provenance` rejects
  ``quantum=true`` unless ``source_label == "quantum"`` **and** the engine
  reported zero fallbacks and no degradation for the sample.
* **Never persist keys** — :class:`Run` carries ``keys_present`` booleans only;
  there is no field a secret could be written into.

Layer 1: may import ``cards`` / ``models`` (layer 0).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from qr_llm_evals.models.schema import (
    SEED_MAX_EXCLUSIVE,
    ArmId,
    CardId,
    DeterminismTier,
    ModelId,
    Sampling,
    Sha256Hex,
    SourceLabel,
    Tier0Source,
)

RunKind = Literal["pilot", "full"]
RunState = Literal[
    "created", "ready", "running", "paused", "needs_keys", "analysing", "complete", "aborted"
]
TaintKind = Literal["engine_fallback", "qrng_degraded", "stateless_row", "stream_error"]
ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"]
#: Which timing window a generation was dispatched in (spec §2.9).
GenerationWindow = Literal["supervised", "break", "unsupervised"]
FinishReason = Literal["stop", "length", "abort", "error"]

#: ``<arm_id>/<card_id>/<prompt_id>/<epoch>[/<told>]`` — the join key shared by
#: generations, Tier 0 records and judge rows; also sent as the engine ``request_id``.
SampleRef = Annotated[str, Field(min_length=1, max_length=512)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Taint(_Record):
    """A sample or window whose quantum claim (or integrity) is compromised."""

    sample_ref: SampleRef | None = None
    window_id: Identifier | None = None
    kind: TaintKind
    detail: str = ""
    at: AwareDatetime

    @model_validator(mode="after")
    def _check_target(self) -> Taint:
        if self.sample_ref is None and self.window_id is None:
            raise ValueError("taint must name a sample_ref or a window_id")
        return self


class CardProgress(_Record):
    done: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    aborted: int = Field(default=0, ge=0)


class ArmProgress(_Record):
    by_card: dict[CardId, CardProgress] = Field(default_factory=dict)


class Progress(_Record):
    by_arm: dict[ArmId, ArmProgress] = Field(default_factory=dict)


class EntropyHealth(_Record):
    """Snapshot of ``GET /health/entropy`` (spec §2.7 step 1)."""

    rpc_ok: bool
    currently_degraded: bool
    fallback_count: int = Field(ge=0)
    source_type: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ArmReadiness(_Record):
    available: bool
    reason: str | None = None
    resolved_qr_args: dict[str, Any] = Field(
        default_factory=dict, description="The qr_* envelope the engine accepted for this arm."
    )


class ReadinessReport(_Record):
    """Result of ``POST /api/readiness`` (spec §2.7)."""

    at: AwareDatetime
    entropy: EntropyHealth
    arms: dict[ArmId, ArmReadiness] = Field(default_factory=dict)
    equivalence_ok: bool = Field(
        description="All qr-llm arms differ only in entropy source / seed / epsilon."
    )
    determinism_tier: DeterminismTier
    tier0_source: Tier0Source
    epsilon_supported: bool
    measured_tps: float | None = Field(default=None, gt=0)


class Provenance(_Record):
    """Where a sample's randomness came from (spec §4.4, AGENTS.md invariant 2)."""

    model_id: ModelId
    served_name: str
    checkpoint: str | None = None
    sampling: Sampling
    preset: str | None = Field(default=None, description="qr_preset sent, if a qr-llm target.")
    envelope_hash: Sha256Hex
    source_label: SourceLabel
    quantum: bool
    study_seed: int | None = Field(default=None, ge=0, lt=SEED_MAX_EXCLUSIVE)
    qr_seed: int | None = Field(default=None, ge=0, lt=SEED_MAX_EXCLUSIVE)
    epsilon: float | None = None
    engine_fallback_delta: int = Field(default=0, ge=0)
    engine_degraded: bool = False
    engine_last_source: str | None = None
    determinism_tier: DeterminismTier
    pipeline_pinned: bool

    @model_validator(mode="after")
    def _never_label_prng_as_quantum(self) -> Provenance:
        if self.quantum:
            if self.source_label != "quantum":
                raise ValueError(
                    f"quantum=true is impossible for source_label {self.source_label!r}"
                )
            if self.engine_fallback_delta > 0 or self.engine_degraded:
                raise ValueError("quantum=true requires zero engine fallbacks and no degradation")
        return self


class Usage(_Record):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class Timing(_Record):
    started: AwareDatetime
    first_token: AwareDatetime | None = None
    finished: AwareDatetime | None = None
    tps: float | None = Field(default=None, ge=0)


class GenerationRow(_Record):
    """One line of ``generations.jsonl``."""

    sample_ref: SampleRef
    arm_id: ArmId
    card_id: CardId
    prompt_id: Identifier
    epoch: int = Field(ge=0)
    told_condition: ToldCondition | None = None
    messages_hash: Sha256Hex
    output: str
    thinking: str | None = None
    finish_reason: FinishReason
    usage: Usage = Field(default_factory=Usage)
    provenance: Provenance
    timing: Timing
    session_id: Identifier | None = None
    window: GenerationWindow = "unsupervised"
    qr_seed: int | None = Field(default=None, ge=0, lt=SEED_MAX_EXCLUSIVE)
    request_id: str


class JudgeRow(_Record):
    """One line of ``judge/<judge_id>.jsonl``."""

    sample_ref: SampleRef
    judge_id: Identifier
    model: str
    rubric_hash: Sha256Hex
    score: float | None = None
    raw: str = ""
    usage: Usage = Field(default_factory=Usage)
    at: AwareDatetime


class Run(_Record):
    """``runs/<run_id>/run.json``. Secrets never appear here: ``keys_present`` only."""

    run_id: Identifier
    study_id: Identifier
    kind: RunKind
    state: RunState = "created"
    created_at: AwareDatetime
    key_name: str = Field(description="Name of the launch key that gated the run (never the key).")
    owner_id: str | None = Field(
        default=None,
        description="Account that launched the run; None for a run launched with a bare key.",
    )
    host_token_hash: Sha256Hex
    prereg_hash: Sha256Hex | None = None
    config_hash: Sha256Hex
    prereg_stale: bool = False
    readiness: ReadinessReport | None = None
    tier0_source: Tier0Source | None = None
    determinism_tier: DeterminismTier = "none"
    progress: Progress = Field(default_factory=Progress)
    taints: list[Taint] = Field(default_factory=list)
    withdrawn: bool = False
    needs_keys: bool = False
    keys_present: dict[str, bool] = Field(
        default_factory=dict, description="e.g. {'judge': true, 'openai': false}."
    )


__all__ = [
    "ArmProgress",
    "ArmReadiness",
    "CardProgress",
    "EntropyHealth",
    "FinishReason",
    "GenerationRow",
    "GenerationWindow",
    "Identifier",
    "JudgeRow",
    "Progress",
    "Provenance",
    "ReadinessReport",
    "Run",
    "RunKind",
    "RunState",
    "SampleRef",
    "Taint",
    "TaintKind",
    "Timing",
    "ToldCondition",
    "Usage",
]
