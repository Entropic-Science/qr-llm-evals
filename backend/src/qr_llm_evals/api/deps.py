"""Application state and the FastAPI dependencies (auth gates) — spec §2.4, §4.7, D-13.

Four identities cross the wire, each checked by its own dependency:

``Authorization: Bearer qrs_…``  → :func:`require_user`
    A login session token from ``POST /auth/login`` or ``/auth/signup``. Only
    its SHA-256 is stored. Gates the account routes.
``Authorization: Bearer qrs_… | qrk_…``  → :func:`require_launcher`
    Gates ``POST /runs`` and ``POST /readiness``. A session token launches as
    that account; the account's linked key is re-checked by digest when the
    verifier can do that (file mode), so disabling a key locks its account
    out. A bare ``qrk_`` api.entropic.science key is accepted only when
    ``QR_EVALS_ALLOW_KEY_LAUNCH`` is on (operator tooling: such a run has no
    owner, is not metered and bypasses token quotas). It is verified through
    :class:`~qr_llm_evals.keys.verify.KeyVerifier` and only its
    :class:`~qr_llm_evals.keys.verify.KeyIdentity` (name / fingerprint) is kept.
``X-Host-Token``                  → :func:`require_host`
    The random token returned once at launch; the record stores its SHA-256
    and the check is a constant-time digest compare.
``X-Observer-Id``                 → :func:`require_observer_id`
    The self-chosen observer id (localStorage); no account behind it.

Failures are ``401`` (nothing presented / not recognised) or ``403``
(presented, recognised, but not allowed here), always in the error envelope.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Protocol

import httpx
from fastapi import Depends, Header, Request

from qr_llm_evals.accounts import SESSION_PREFIX, Accounts, UsageLedger, User
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.api.registry import LiveRun, OrchestratorFactory, RunRegistry
from qr_llm_evals.api.session_runtime import restore_sessions
from qr_llm_evals.api.storage import StorageMeter
from qr_llm_evals.api.usage import UsageRecorder
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.config import Settings
from qr_llm_evals.keys.verify import (
    DigestVerifier,
    KeyIdentity,
    KeyRefused,
    KeyVerifier,
    KeyVerifyUnavailable,
    parse_bearer,
)
from qr_llm_evals.models.catalogue import ModelCatalogue
from qr_llm_evals.models.providers import ProviderProfile
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.store.schema import ReadinessReport
from qr_llm_evals.store.store import RunStore, Store
from qr_llm_evals.supervised.session import SessionRegistry

HOST_TOKEN_HEADER = "X-Host-Token"  # nosec B105 - a header name, not a credential
OBSERVER_HEADER = "X-Observer-Id"
HOST_TOKEN_BYTES = 32
OBSERVER_ID_MAX = 128


#: Renders the pre-registration Markdown for a config at launch (S12 provides the real one).
class PreregRenderer(Protocol):
    def __call__(
        self, study_config: StudyConfig, readiness: ReadinessReport | None = None
    ) -> str: ...


Clock = Callable[[], datetime]
TargetUniformDrawer = Callable[[str, str], float]


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_host_token() -> str:
    return "host_" + _secrets.token_urlsafe(HOST_TOKEN_BYTES)


def host_token_matches(presented: str, stored_hash: str) -> bool:
    return hmac.compare_digest(sha256_hex(presented), stored_hash)


@dataclass
class AppState:
    """Everything a route needs, built once by ``create_app`` and injected."""

    settings: Settings
    store: Store
    verifier: KeyVerifier
    accounts: Accounts
    ledger: UsageLedger
    usage: UsageRecorder
    cards: dict[str, EvalCard]
    catalogue: ModelCatalogue
    providers: dict[str, ProviderProfile]
    orchestrator_factory: OrchestratorFactory
    prereg_renderer: PreregRenderer
    registry: RunRegistry = field(default_factory=RunRegistry)
    sessions: SessionRegistry = field(default_factory=SessionRegistry)
    limiter: TokenBucketLimiter = field(default_factory=TokenBucketLimiter)
    storage: StorageMeter = field(default_factory=StorageMeter)
    engine_transport: httpx.AsyncBaseTransport | None = None
    readiness_transport: httpx.BaseTransport | None = None
    analysis_transport: httpx.BaseTransport | None = None
    judge_transport: httpx.BaseTransport | None = None
    repo_root: Path | None = None
    version: str = "0.0.0"
    commit: str | None = None
    clock: Clock = lambda: datetime.now(UTC)
    target_uniform_drawer: TargetUniformDrawer | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def run_store(self, run_id: str) -> RunStore:
        rs = self.store.find_run(run_id)
        if rs is None:
            raise ApiError(404, f"run {run_id!r} not found", param="run_id")
        return rs

    def live_run(self, run_id: str) -> LiveRun:
        """The live handle for ``run_id`` — adopted from disk if this process has none."""
        live = self.registry.get(run_id)
        if live is None:
            live = self.registry.adopt(self.run_store(run_id))
        config = self.store.load_study(live.read().study_id).supervised
        if config is not None:
            restore_sessions(
                self.sessions, live.run_store, config, self.cards, live.bus, self.clock
            )
        return live


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.qr
    return state


State = Annotated[AppState, Depends(get_state)]


# ── launch key ────────────────────────────────────────────────────────────────


async def require_launch_key(
    state: State,
    authorization: Annotated[str | None, Header()] = None,
) -> KeyIdentity:
    key = parse_bearer(authorization)
    if key is None:
        raise ApiError(
            401,
            "a launch key is required: Authorization: Bearer qrk_…",
            headers={"WWW-Authenticate": 'Bearer realm="qr-llm-evals"'},
        )
    try:
        return await state.verifier.averify(key)
    except KeyRefused as err:
        raise ApiError(
            err.status_code,
            err.reason,
            headers={"WWW-Authenticate": "Bearer"} if err.status_code == 401 else None,
        ) from err
    except KeyVerifyUnavailable as err:
        raise ApiError(
            503,
            f"launch keys cannot be verified right now: {err}",
            headers={"Retry-After": "10"},
        ) from err


LaunchKey = Annotated[KeyIdentity, Depends(require_launch_key)]


# ── accounts ──────────────────────────────────────────────────────────────────

_SESSION_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="qr-llm-evals"'}


def _session_user(state: AppState, authorization: str | None) -> User:
    token = parse_bearer(authorization)
    if token is None:
        raise ApiError(
            401, "sign in first: Authorization: Bearer qrs_…", headers=_SESSION_CHALLENGE
        )
    user = state.accounts.user_for_session(token)
    if user is None:
        raise ApiError(
            401, "session is invalid or has expired; sign in again", headers=_SESSION_CHALLENGE
        )
    return user


async def require_user(
    state: State,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    return _session_user(state, authorization)


CurrentUser = Annotated[User, Depends(require_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ApiError(403, "this route needs an admin account")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


@dataclass(frozen=True, slots=True)
class Launcher:
    """Who may launch: the quota bucket and the account behind it (if any)."""

    quota_id: str
    owner_id: str | None


def recheck_linked_key(state: AppState, user: User) -> str:
    """The quota bucket for ``user``; raises 403 if their linked key stopped being valid.

    Only a verifier that can look a key up by digest (file mode) re-checks; in
    probe mode the key was verified when it was linked and is trusted since.
    """
    verifier = state.verifier
    if not isinstance(verifier, DigestVerifier):
        return user.quota_id
    try:
        return verifier.verify_digest(user.key_sha256).quota_id
    except KeyRefused as err:
        raise ApiError(
            403,
            "the API key linked to this account is no longer valid "
            f"({err.reason}); link a working key first",
        ) from err
    except KeyVerifyUnavailable as err:
        raise ApiError(
            503,
            f"launch keys cannot be verified right now: {err}",
            headers={"Retry-After": "10"},
        ) from err


async def require_launcher(
    state: State,
    authorization: Annotated[str | None, Header()] = None,
) -> Launcher:
    token = parse_bearer(authorization)
    if token is not None and token.startswith(SESSION_PREFIX):
        user = _session_user(state, authorization)
        return Launcher(quota_id=recheck_linked_key(state, user), owner_id=user.user_id)
    if not state.settings.allow_key_launch:
        raise ApiError(
            401,
            "sign in to launch: send the session token from POST /api/auth/login as "
            "Authorization: Bearer qrs_…",
            headers=_SESSION_CHALLENGE,
        )
    identity = await require_launch_key(state, authorization)
    return Launcher(quota_id=identity.quota_id, owner_id=None)


LaunchAuth = Annotated[Launcher, Depends(require_launcher)]


# ── host token ────────────────────────────────────────────────────────────────


async def require_host(
    run_id: str,
    state: State,
    x_host_token: Annotated[str | None, Header()] = None,
) -> LiveRun:
    live = state.live_run(run_id)
    if not x_host_token:
        raise ApiError(401, f"{HOST_TOKEN_HEADER} header is required for host actions")
    run = live.read()
    if not host_token_matches(x_host_token.strip(), run.host_token_hash):
        raise ApiError(403, "host token does not match this run")
    return live


HostRun = Annotated[LiveRun, Depends(require_host)]


# ── observer id ───────────────────────────────────────────────────────────────


async def require_observer_id(
    x_observer_id: Annotated[str | None, Header()] = None,
) -> str:
    if not x_observer_id or not x_observer_id.strip():
        raise ApiError(401, f"{OBSERVER_HEADER} header is required")
    value = x_observer_id.strip()
    if len(value) > OBSERVER_ID_MAX or not value.isprintable():
        raise ApiError(400, f"{OBSERVER_HEADER} must be a printable id ≤ {OBSERVER_ID_MAX} chars")
    return value


ObserverId = Annotated[str, Depends(require_observer_id)]


def request_headers_without_secrets(headers: Mapping[str, str]) -> dict[str, str]:
    """For log lines: never the authorization / host-token values."""
    return {
        k: ("[REDACTED]" if k.lower() in ("authorization", "x-host-token") else v)
        for k, v in headers.items()
    }


__all__ = [
    "HOST_TOKEN_HEADER",
    "OBSERVER_HEADER",
    "AdminUser",
    "AppState",
    "CurrentUser",
    "HostRun",
    "LaunchAuth",
    "LaunchKey",
    "Launcher",
    "ObserverId",
    "PreregRenderer",
    "State",
    "TargetUniformDrawer",
    "get_state",
    "host_token_matches",
    "new_host_token",
    "recheck_linked_key",
    "require_admin",
    "request_headers_without_secrets",
    "require_host",
    "require_launch_key",
    "require_launcher",
    "require_observer_id",
    "require_user",
    "sha256_hex",
]
