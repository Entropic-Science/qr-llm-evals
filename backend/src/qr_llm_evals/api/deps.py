"""Application state and the FastAPI dependencies (auth gates) — spec §2.4, §4.7, D-13.

Three identities cross the wire, each carried by its own header and checked
by its own dependency:

``Authorization: Bearer qrk_…``  → :func:`require_launch_key`
    The api.entropic.science key gating ``POST /runs`` and ``POST /readiness``.
    Verified through :class:`~qr_llm_evals.keys.verify.KeyVerifier`; only the
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

from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.api.registry import LiveRun, OrchestratorFactory, RunRegistry
from qr_llm_evals.api.session_runtime import restore_sessions
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.config import Settings
from qr_llm_evals.keys.verify import (
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
    cards: dict[str, EvalCard]
    catalogue: ModelCatalogue
    providers: dict[str, ProviderProfile]
    orchestrator_factory: OrchestratorFactory
    prereg_renderer: PreregRenderer
    registry: RunRegistry = field(default_factory=RunRegistry)
    sessions: SessionRegistry = field(default_factory=SessionRegistry)
    limiter: TokenBucketLimiter = field(default_factory=TokenBucketLimiter)
    engine_transport: httpx.AsyncBaseTransport | None = None
    readiness_transport: httpx.BaseTransport | None = None
    analysis_transport: httpx.BaseTransport | None = None
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
    "AppState",
    "HostRun",
    "LaunchKey",
    "ObserverId",
    "PreregRenderer",
    "State",
    "TargetUniformDrawer",
    "get_state",
    "host_token_matches",
    "new_host_token",
    "request_headers_without_secrets",
    "require_host",
    "require_launch_key",
    "require_observer_id",
    "sha256_hex",
]
