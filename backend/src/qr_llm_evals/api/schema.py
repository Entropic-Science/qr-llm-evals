"""Request / response bodies of the HTTP API (spec §4.7).

These are the *wire* shapes of the routes S11 owns; the domain records they
carry (``StudyConfig``, ``Run``, ``ReadinessReport``, ``SelectionEstimate``)
live in their own ``schema.py`` modules. ``scripts/export_schemas.py``
registers the response models here so ``@qr-evals/contracts`` has typed
clients for every route.

Secrets: :class:`LaunchRequest.provider_keys` is the only place a key enters
the process. The model never leaves a handler; ``LaunchResponse`` carries the
host token exactly once, and :class:`RunView` carries ``keys_present``
booleans only (spec D-14).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from qr_llm_evals.cards.estimates import SelectionEstimate
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.store.schema import ReadinessReport, Run, RunKind
from qr_llm_evals.supervised.schema import (
    AnswerValue,
    Observer,
    ObserverPresence,
    Session,
)

ControlAction = Literal["start", "pause", "resume", "abort"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── GET /health ───────────────────────────────────────────────────────────────


class EngineStatus(_Strict):
    origin: str
    reachable: bool
    detail: str | None = None
    checked_at: str


class HealthResponse(_Strict):
    status: Literal["ok", "degraded"]
    service: str = "qr-llm-evals"
    version: str
    commit: str | None = None
    key_verify: Literal["file", "probe"]
    engine: EngineStatus
    active_runs: int = Field(ge=0)


# ── GET /cards ────────────────────────────────────────────────────────────────


class CardEstimateSummary(_Strict):
    """A card's cost on the default qr-llm target at catalogue throughput."""

    model_id: str
    tokens_per_arm: int = Field(ge=0)
    time_unsupervised_h: float = Field(ge=0)
    observer_hours_single: float = Field(ge=0)
    observer_hours_tiled: float = Field(ge=0)


class CardView(_Strict):
    card: EvalCard
    estimate: CardEstimateSummary | None = None


class CardsResponse(_Strict):
    cards: list[CardView]
    count: int = Field(ge=0)


# ── GET /models ───────────────────────────────────────────────────────────────


class ModelEntryView(_Strict):
    id: str
    family: str
    provider: str
    served_name: str
    checkpoint: str | None = None
    tps: float = Field(gt=0)
    pipeline_pinned: bool
    description: str = ""
    key_slot: str | None = Field(
        default=None, description="Which ``provider_keys`` slot this target needs, if any."
    )


class ProviderProfileView(_Strict):
    id: str
    base_url: str | None = None
    accepted_params: list[str]
    seed_honoured: bool
    logprobs_honoured: bool
    top_logprobs_cap: int = Field(ge=0)
    notes: str | None = None


class ModelsResponse(_Strict):
    models: list[ModelEntryView]
    providers: list[ProviderProfileView]
    default_tps_by_model: dict[str, float]
    reading_tps: float = Field(gt=0)


# ── POST /estimates ───────────────────────────────────────────────────────────


class EstimateTotals(_Strict):
    tokens: int = Field(ge=0)
    time_unsupervised_h: float = Field(ge=0)
    observer_hours_single: float = Field(ge=0)
    observer_hours_tiled: float = Field(ge=0)
    sessions_single: int = Field(ge=0)
    sessions_tiled: int = Field(ge=0)


class SupervisedEstimate(_Strict):
    """Observer time for the supervised arms only (the SPA's second line)."""

    arms: list[str]
    display: Literal["single", "tiled"] | None = None
    observer_hours: float = Field(ge=0)
    sessions: int = Field(ge=0)
    session_min: int = Field(ge=1)
    break_min: int = Field(ge=0)
    wall_clock_h: float = Field(ge=0, description="Sessions plus breaks, host-paced.")


class EstimatesResponse(_Strict):
    by_arm: dict[str, SelectionEstimate]
    totals: EstimateTotals
    supervised: SupervisedEstimate | None = None


# ── POST /readiness ───────────────────────────────────────────────────────────


class ReadinessRequest(_Strict):
    config: StudyConfig
    run_id: str | None = Field(default=None, description="Persist the report on this run.")
    throughput_probe: bool = False


# ── POST /runs ────────────────────────────────────────────────────────────────


class PreregRenderRequest(_Strict):
    config: StudyConfig
    readiness: ReadinessReport | None = None
    expectations: str | None = Field(default=None, max_length=20000)


class PreregRenderResponse(_Strict):
    markdown: str
    prereg_hash: str
    config_hash: str
    plan_hash: str
    plan: StatisticalPlan


class LaunchRequest(_Strict):
    config: StudyConfig
    readiness: ReadinessReport | None = None
    kind: RunKind = "full"
    prereg_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Hash of the prereg the host exported; mismatch ⇒ prereg_stale.",
    )
    provider_keys: dict[str, str | None] = Field(
        default_factory=dict,
        description="BYO keys (openai, anthropic, custom); memory only.",
    )


class LaunchResponse(_Strict):
    run_id: str
    study_id: str
    host_token: str = Field(description="Shown once; the record stores only its SHA-256.")
    join_code: str | None = None
    prereg_stale: bool


class RunControlRequest(_Strict):
    action: ControlAction


class RunControlResponse(_Strict):
    run_id: str
    action: ControlAction
    state: str


class RunKeysRequest(_Strict):
    provider_keys: dict[str, str | None]


class RunKeysResponse(_Strict):
    run_id: str
    keys_present: dict[str, bool]
    state: str
    needs_keys: bool


class RunView(_Strict):
    """``GET /runs/{id}``: the stored record plus live facts the record does not hold."""

    run: Run
    live: bool = Field(description="An orchestrator for this run exists in this process.")
    control_state: str | None = None
    subscribers: int = Field(default=0, ge=0)
    readiness: ReadinessReport | None = None


class RunListResponse(_Strict):
    runs: list[Run]


# â”€â”€ analysis and public study results (Â§4.7) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class AnalyseResponse(_Strict):
    run_id: str
    study_id: str
    analysis_id: str
    state: Literal["analysing"] = "analysing"


class StudyView(_Strict):
    study_id: str
    study: dict[str, Any]
    runs: list[dict[str, Any]] = Field(
        description="Public run summaries with host/key authentication fields stripped."
    )
    analyses: dict[str, list[str]] = Field(
        description="Completed or in-progress analysis ids keyed by run id."
    )
    withdrawn: bool = Field(description="True when every archived run is withdrawn.")


class WithdrawRequest(_Strict):
    reason: str = Field(min_length=1, max_length=20000)
    run_id: str | None = Field(
        default=None,
        description="Optional when the host token identifies exactly one run in the study.",
    )


class WithdrawResponse(_Strict):
    study_id: str
    run_id: str
    withdrawn: Literal[True] = True


# ── supervised sessions (§4.7) ───────────────────────────────────────────────


class CreateSessionRequest(_Strict):
    display: Literal["single", "tiled"]
    intention: str = Field(min_length=1, max_length=10000)
    host_observer_id: str = Field(default="host", min_length=1, max_length=128)
    host_display_name: str = Field(default="Host", min_length=1, max_length=64)


class SessionView(_Strict):
    session: Session
    state: str
    join_code: str
    questionnaires_hash: str
    target_pool_hash: str


class SessionControlRequest(_Strict):
    action: Literal["start", "resume", "stop_at_boundary", "end_break"]


class SessionControlResponse(_Strict):
    session_id: str
    action: Literal["start", "resume", "stop_at_boundary", "end_break"]
    state: str


class SessionJoinRequest(_Strict):
    join_code: str = Field(min_length=8, max_length=8)
    display_name: str = Field(min_length=1, max_length=64)
    observer_id: str | None = Field(default=None, max_length=128)
    consent: Literal[True]


class SessionJoinResponse(_Strict):
    observer: Observer
    session_id: str
    pre_required: bool = True


class SessionPresenceRequest(_Strict):
    left: bool = False


class SessionPresenceResponse(_Strict):
    session_id: str
    present: list[ObserverPresence]


class SessionQuestionnaireRequest(_Strict):
    phase: Literal["pre", "post"]
    answers: dict[str, AnswerValue]
    free_text: str | None = Field(default=None, max_length=20000)


def as_json(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


# ── accounts: /auth/*, /me ────────────────────────────────────────────────────


class SignupRequest(_Strict):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=256)
    api_key: str = Field(
        min_length=8,
        max_length=256,
        description="A working api.entropic.science key. Verified once; only its SHA-256 is kept.",
    )


class LoginRequest(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LinkKeyRequest(_Strict):
    api_key: str = Field(min_length=8, max_length=256)


class UserView(_Strict):
    """What the API says about an account. No password hash, no key, no key digest."""

    user_id: str
    username: str
    key_name: str | None = Field(description="The linked key's name in the keys file, if known.")
    key_fingerprint: str = Field(description="First 8 hex of the linked key's SHA-256.")
    key_linked_at: AwareDatetime
    is_admin: bool
    created_at: AwareDatetime


class AuthResponse(_Strict):
    user: UserView
    session_token: str = Field(description="Shown once; the server stores only its SHA-256.")
    expires_at: AwareDatetime


class LogoutResponse(_Strict):
    ok: bool = True


# ── usage and quota: /me/usage, /admin/users ─────────────────────────────────


class UsageTotalsView(_Strict):
    calls: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    charged_tokens: int = Field(
        ge=0, description="Tokens counted against the quota (qr-server generations only)."
    )


class UsageBucketView(_Strict):
    key: str
    totals: UsageTotalsView


class QuotaView(_Strict):
    limit: int = Field(ge=0, description="Lifetime token budget; 0 = unlimited.")
    used: int = Field(ge=0)
    remaining: int | None = Field(description="None when unlimited.")
    exhausted: bool


class UsageResponse(_Strict):
    quota: QuotaView
    totals: UsageTotalsView
    by_kind: list[UsageBucketView] = Field(description="generation / judge / narrative.")
    by_source: list[UsageBucketView] = Field(
        description="Generations by provenance source label (quantum vs the PRNG arms)."
    )
    by_day: list[UsageBucketView] = Field(description="UTC days, newest first (30 at most).")
    by_run: list[UsageBucketView] = Field(description="Most recent runs first (20 at most).")


class AdminUserView(_Strict):
    user: UserView
    disabled: bool
    token_quota: int | None = Field(description="Per-account override; None = the default.")
    quota: QuotaView
    totals: UsageTotalsView


class AdminUsersResponse(_Strict):
    default_token_quota: int = Field(ge=0)
    users: list[AdminUserView]


class AdminUserUpdate(_Strict):
    """Only the fields present in the body are changed."""

    token_quota: int | None = Field(
        default=None, ge=0, description="null = back to the default; 0 = unlimited."
    )
    disabled: bool | None = None
    is_admin: bool | None = None


__all__ = [
    "AdminUserUpdate",
    "AdminUserView",
    "AdminUsersResponse",
    "AuthResponse",
    "QuotaView",
    "UsageBucketView",
    "UsageResponse",
    "UsageTotalsView",
    "LinkKeyRequest",
    "LoginRequest",
    "LogoutResponse",
    "SignupRequest",
    "UserView",
    "CardEstimateSummary",
    "CardView",
    "CardsResponse",
    "ControlAction",
    "AnalyseResponse",
    "CreateSessionRequest",
    "EngineStatus",
    "EstimateTotals",
    "EstimatesResponse",
    "HealthResponse",
    "LaunchRequest",
    "LaunchResponse",
    "ModelEntryView",
    "ModelsResponse",
    "ProviderProfileView",
    "ReadinessRequest",
    "RunControlRequest",
    "RunControlResponse",
    "RunKeysRequest",
    "RunKeysResponse",
    "RunListResponse",
    "RunView",
    "SessionControlRequest",
    "SessionControlResponse",
    "SessionJoinRequest",
    "SessionJoinResponse",
    "SessionPresenceRequest",
    "SessionPresenceResponse",
    "SessionQuestionnaireRequest",
    "SessionView",
    "StudyView",
    "SupervisedEstimate",
    "WithdrawRequest",
    "WithdrawResponse",
    "as_json",
]
