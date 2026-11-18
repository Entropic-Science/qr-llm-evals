"""Checks shared by the routes that can spend engine time on an account's behalf.

Three questions, asked at launch, at readiness and at start/resume:

* **Is every external model really external?** A ``custom:<base_url>#<model>``
  id names an arbitrary base URL. Pointed at this box (loopback, a private
  address, the engine's or the edge's own origin) it would reach the shared
  engine without being a ``qr_server`` arm, so it would generate unmetered and
  outside the pinned pipeline. Custom base URLs must therefore be ``https://``
  on a public host; the engine speaks plain HTTP on loopback, so a hostname
  that merely resolves to this box fails the TLS handshake instead of
  reaching it.
* **Does this study use the engine at all?** Only ``qr_server`` arms are
  charged. A study with none is never held back by the token quota.
* **May this account generate right now?** Not if it is disabled, its linked
  key stopped working, or its quota is spent on a study that would be charged.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from qr_llm_evals.accounts import Quota, UnknownUser
from qr_llm_evals.api.deps import AppState, recheck_linked_key
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.models.catalogue import custom_base_url
from qr_llm_evals.models.schema import ModelRef, StudyConfig

_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def _host(url: str) -> str | None:
    host = urlsplit(url).hostname
    return host.lower().rstrip(".") if host else None


def _custom_problem(state: AppState, model: ModelRef) -> str | None:
    base = custom_base_url(model)
    if not base:
        return "a custom model id must be custom:<base_url>#<model>"
    parts = urlsplit(base)
    host = _host(base)
    if parts.scheme != "https" or not host:
        return "a custom base URL must start with https://"
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return "a custom base URL cannot point at this machine or a private network"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return "a custom base URL cannot point at this machine or a private network"
    own = {_host(state.settings.vllm_origin), _host(state.settings.edge_origin)}
    if host in own:
        return "a custom base URL cannot be this service's own engine"
    return None


def check_external_models(state: AppState, config: StudyConfig) -> None:
    """Refuse a study whose custom models could reach the shared engine unmetered."""
    for model in (*config.models, config.narrative_model):
        if model.provider != "custom":
            continue
        problem = _custom_problem(state, model)
        if problem is not None:
            raise ApiError(400, f"model {model.id!r}: {problem}", param="config.models")


def has_charged_arms(config: StudyConfig) -> bool:
    """True iff some arm generates on the qr-server engine (the only charged kind)."""
    providers = {model.id: model.provider for model in config.models}
    return any(providers.get(arm.model) == "qr_server" for arm in config.arms)


def quota_message(quota: Quota) -> str:
    return (
        f"this account's token quota is used up ({quota.used:,} of {quota.limit:,} qr-server "
        "generation tokens); ask an admin to raise it"
    )


def check_owner_can_generate(state: AppState, owner_id: str | None, config: StudyConfig) -> None:
    """Refuse (403) when the run's owner may not put work on the engine right now.

    ``owner_id`` is ``None`` for an operator's bare-key run, which has no
    account to check. A study without ``qr_server`` arms is not held back by
    the quota, because nothing it generates is charged.
    """
    if owner_id is None:
        return
    try:
        user = state.accounts.get_user(owner_id)
    except UnknownUser as err:
        raise ApiError(403, "the account that launched this run no longer exists") from err
    if user.disabled:
        raise ApiError(403, "the account that launched this run is disabled")
    recheck_linked_key(state, user)
    if not has_charged_arms(config):
        return
    quota = state.ledger.quota(user)
    if quota.exhausted:
        raise ApiError(403, quota_message(quota))


__all__ = [
    "check_external_models",
    "check_owner_can_generate",
    "has_charged_arms",
    "quota_message",
]
