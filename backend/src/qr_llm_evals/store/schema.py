"""Run-time records persisted by the store (spec §4.4, §4.5).

``Run`` lives in ``runs/<run_id>/run.json`` (and the SQLite ``runs`` index),
``GenerationRow`` / ``JudgeRow`` are JSONL lines per ``(arm, card)``, ``Taint``
lines go to ``taints.jsonl``, ``ResultFlag`` lines to ``flags.jsonl``, and
``ReadinessReport`` is ``readiness.json``. A ``Halt`` is one stop of the run by
a result check and is kept in ``Run.halts``.

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

import re
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from qr_llm_evals.cards.result_checks import (
    CAUSE_PHRASES,
    FLAG_PHASE,
    HALT_SAMPLE_REFS,
    FlagKind,
    FlagPhase,
    HaltPhase,
    JudgeStatus,
    StopRule,
    StopScope,
)
from qr_llm_evals.logging_utils import redact
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

    #: Fields left out of the file when they are ``None`` or empty
    #: (``store.artifacts.dump_row``). A field added to a record after files of
    #: it were written is listed here, so a record that does not use it keeps the
    #: bytes it always had and an older harness can still read it.
    OMIT_WHEN_EMPTY: ClassVar[frozenset[str]] = frozenset()


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


class ResultFlag(_Record):
    """One line of ``flags.jsonl``: a result check found something about a sample.

    A flag is not an exclusion. The sample stays in every analysis exactly as
    before; nothing in ``analysis/`` reads this file. It is joined to the other
    records by ``sample_ref``.
    """

    OMIT_WHEN_EMPTY: ClassVar[frozenset[str]] = frozenset({"count"})

    sample_ref: SampleRef
    kind: FlagKind
    phase: FlagPhase
    card_id: CardId
    arm_id: ArmId
    detail: str = Field(
        default="",
        max_length=300,
        description=(
            "Written by the harness only: lengths, counts, a stop reason, an HTTP status, an "
            "exception class name. Never a key and never an upstream error message."
        ),
    )
    count: int | None = Field(
        default=None, ge=1, description="How many times the thing was seen in the sample."
    )
    at: AwareDatetime

    @model_validator(mode="after")
    def _check_phase(self) -> ResultFlag:
        if FLAG_PHASE[self.kind] != self.phase:
            raise ValueError(
                f"flag kind {self.kind!r} belongs to phase {FLAG_PHASE[self.kind]!r}, "
                f"not {self.phase!r}"
            )
        return self


#: What a key of ``Halt.causes`` or ``Halt.counts`` may look like:
#:
#: * an HTTP status (``HTTP 502``);
#: * a class name, with or without its module: letters only, starting with a
#:   capital (``ReadTimeout``, ``httpx.ConnectError``);
#: * a name in the catalogue's style (a taint or flag kind, a count): one to six
#:   lower-case words of at most 20 letters joined by ``_``, each with at most
#:   one digit at its end (``in_a_row``, ``tier0_missing``);
#: * an arm and an eval (``qrng/creative``).
#:
#: Besides these, one of the catalogue's phrases (``CAUSE_PHRASES``) is kept. No
#: other text of several words is: a sentence from outside the harness is not a
#: name. The shapes leave no room for a long run of mixed letters and digits,
#: which is what a provider key looks like. They cannot tell a single word from
#: outside (``invalid``, ``Unauthorized``) from a name, and they keep any
#: ``x/y`` of lower-case letters, digits and underscores.
_HALT_NAME = re.compile(
    r"HTTP \d{3}"
    r"|(?:[a-z][a-z0-9_]{0,19}\.){0,3}[A-Z][A-Za-z]{2,39}"
    r"|[a-z]{1,20}\d?(?:_[a-z]{1,20}\d?){0,5}"
    r"|[a-z0-9_]{1,64}/[a-z0-9_]{1,64}"
)
#: The key a cause or count is tallied under when it is not such a name.
OTHER_CAUSE = "other"


def _is_halt_name(key: str) -> bool:
    if key in CAUSE_PHRASES:
        return True
    return _HALT_NAME.fullmatch(key) is not None and redact(key) == key


def _halt_names(tally: dict[str, int]) -> dict[str, int]:
    """``tally`` with every key that is not a harness name, or holds a secret, merged."""
    named: dict[str, int] = {}
    for key, n in tally.items():
        name = key if _is_halt_name(key) else OTHER_CAUSE
        named[name] = named.get(name, 0) + n
    return named


class Halt(_Record):
    """One stop of a run by a result check, kept in ``Run.halts``.

    A stop is a pause with a stored reason: nothing is removed and nothing
    resumes by itself. ``resumed_at`` is empty while the halt is open and is
    stamped by the run that the host resumes.

    A halt is written to ``run.json``, sent as an event and served on open
    routes, so it must never hold a key (AGENTS.md invariant 3). The Breaker
    writes fixed sentences and the harness's own names, and the record guards
    what it is given all the same. A cause or count is kept under its own name
    only when that name has the shape of one the harness writes
    (``_HALT_NAME``) or is one of the catalogue's phrases; anything else, such
    as an upstream error message, is tallied as :data:`OTHER_CAUSE`. The
    sentence, the recovery text, ``scope_id`` and the sample refs pass through
    the log redaction, which takes out a key a running run holds and a key of
    a known provider shape.

    The limit of that guard: a key that no run holds any more and that has no
    known shape is not recognised in those four text fields, nor as a cause
    written like an arm and eval pair (``x/y``). Only a hand-edited
    ``run.json`` or a caller that passes free text can put one there.
    """

    rule: StopRule
    scope: StopScope
    scope_id: str | None = Field(
        default=None, description="The arm or the card the rule fired for; empty otherwise."
    )
    phase: HaltPhase
    at: AwareDatetime
    sentence: str = Field(description="What the rule saw, with the real scope and counts.")
    recovery: str = Field(description="What Resume can and cannot recover after this stop.")
    counts: dict[str, int] = Field(default_factory=dict)
    causes: dict[str, int] = Field(
        default_factory=dict,
        description="A tally by cause in the harness's own words (an HTTP status, a class name).",
    )
    sample_refs: list[SampleRef] = Field(default_factory=list, max_length=HALT_SAMPLE_REFS)
    resumed_at: AwareDatetime | None = None

    @field_validator("sentence", "recovery", "scope_id")
    @classmethod
    def _text_without_secrets(cls, value: str | None) -> str | None:
        return None if value is None else redact(value)

    @field_validator("sample_refs")
    @classmethod
    def _refs_without_secrets(cls, value: list[str]) -> list[str]:
        return [redact(ref) for ref in value]

    @field_validator("counts", "causes")
    @classmethod
    def _harness_names_only(cls, value: dict[str, int]) -> dict[str, int]:
        return _halt_names(value)

    @model_validator(mode="after")
    def _check_scope(self) -> Halt:
        if (self.scope in ("arm", "card")) != (self.scope_id is not None):
            raise ValueError("scope_id names the arm or the card, and is empty for any other scope")
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
    """One line of ``judge/<judge_id>.jsonl``.

    A row whose ``status`` says a reply was received (``ok``, ``unreadable``,
    ``out_of_range``) is final: the judge is not asked again for that sample.
    Only a ``failed`` row, and a row from before rows had a status that has no
    score, is asked again when the run is resumed.
    """

    OMIT_WHEN_EMPTY: ClassVar[frozenset[str]] = frozenset(
        {"status", "stop_reason", "reader_version"}
    )

    sample_ref: SampleRef
    judge_id: Identifier
    model: str
    rubric_hash: Sha256Hex
    score: float | None = None
    fields: dict[str, float | None] = Field(
        default_factory=dict,
        description=(
            "Further named values the card's judge_fields() read from the judge's reply; "
            "the analysis joins them to the sample's score row."
        ),
    )
    raw: str = ""
    usage: Usage = Field(default_factory=Usage)
    at: AwareDatetime
    status: JudgeStatus | None = Field(
        default=None,
        description=(
            "ok: a score was read. failed: no reply came. unreadable: a reply came and no "
            "score could be read from it. out_of_range: the value read is not one the rubric "
            "allows. Empty on rows written before result checks."
        ),
    )
    stop_reason: str | None = Field(
        default=None, max_length=64, description="The judge's own stop reason, when it gave one."
    )
    reader_version: int | None = Field(
        default=None, ge=1, description="Version of the reader that read this reply."
    )

    @model_validator(mode="after")
    def _check_status(self) -> JudgeRow:
        if self.status == "ok" and self.score is None:
            raise ValueError("a judge row with status 'ok' needs a score")
        if self.status in ("failed", "unreadable", "out_of_range") and self.score is not None:
            raise ValueError(f"a judge row with status {self.status!r} has no score")
        return self


class Run(_Record):
    """``runs/<run_id>/run.json``. Secrets never appear here: ``keys_present`` only."""

    OMIT_WHEN_EMPTY: ClassVar[frozenset[str]] = frozenset({"halts", "checks_version"})

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
    taints: list[Taint] = Field(
        default_factory=list,
        description=(
            "Never written: always an empty list. The taints of a run are the lines of "
            "taints.jsonl; their counts are on GET /runs/{id}/health and in StudyView.health."
        ),
    )
    withdrawn: bool = False
    needs_keys: bool = False
    keys_present: dict[str, bool] = Field(
        default_factory=dict, description="e.g. {'judge': true, 'openai': false}."
    )
    halts: list[Halt] = Field(
        default_factory=list,
        description="Every stop of the run by a result check, oldest first; never shortened.",
    )
    checks_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Version of the result checks the run started under; empty for a run made "
            "before result checks."
        ),
    )

    def open_halts(self) -> list[Halt]:
        """Halts the host has not resumed past."""
        return [halt for halt in self.halts if halt.resumed_at is None]

    def with_halt(self, halt: Halt) -> Run:
        """A copy of the record with ``halt`` added at the end."""
        return self.model_copy(update={"halts": [*self.halts, halt]})

    def with_halts_resumed(self, at: datetime) -> Run:
        """A copy in which every open halt is stamped as resumed at ``at``."""
        stamped = [
            halt if halt.resumed_at is not None else halt.model_copy(update={"resumed_at": at})
            for halt in self.halts
        ]
        return self.model_copy(update={"halts": stamped})


__all__ = [
    "ArmProgress",
    "ArmReadiness",
    "CardProgress",
    "EntropyHealth",
    "FinishReason",
    "GenerationRow",
    "GenerationWindow",
    "Halt",
    "Identifier",
    "JudgeRow",
    "Progress",
    "Provenance",
    "ReadinessReport",
    "ResultFlag",
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
