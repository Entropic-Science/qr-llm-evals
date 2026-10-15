"""Run lifecycle routes (spec §2.4, §4.7, D-13, D-14).

``POST /runs`` (launch key)
    Quota check → freeze the study (config + prereg) → create the run record
    with the host token's hash → put the BYO keys into the run's ``SecretBag``.
    Returns the host token exactly once.
``GET /runs/{id}``
    The stored record (which structurally cannot hold a secret: ``keys_present``
    booleans only) plus live facts from the registry.
``POST /runs/{id}/control`` (host token)
    ``start`` / ``pause`` / ``resume`` / ``abort``. ``start`` and ``resume`` are
    the only ways into ``running`` (AGENTS.md invariant 6).
``POST /runs/{id}/keys`` (host token)
    Re-supply provider keys after a restart (``needs_keys`` → ``paused``).
"""

from __future__ import annotations

import logging
import secrets as _secrets

from fastapi import APIRouter

from qr_llm_evals.api.deps import AppState, HostRun, LaunchKey, State, new_host_token, sha256_hex
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.schema import (
    LaunchRequest,
    LaunchResponse,
    RunControlRequest,
    RunControlResponse,
    RunKeysRequest,
    RunKeysResponse,
    RunView,
)
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.runner.control import RunControlError
from qr_llm_evals.store.schema import Run

log = logging.getLogger("qr_llm_evals.api.runs")

router = APIRouter(tags=["runs"])

QUOTA_RETRY_AFTER_S = 60
RUN_ID_BYTES = 8


def new_run_id() -> str:
    return "run_" + _secrets.token_hex(RUN_ID_BYTES)


def _check_cards(state: AppState, config: StudyConfig) -> None:
    unknown = [c.card_id for c in config.cards if c.card_id not in state.cards]
    if unknown:
        raise ApiError(400, f"unknown cards: {unknown}", param="config.cards")
    unselectable = [c.card_id for c in config.cards if not state.cards[c.card_id].selectable]
    if unselectable:
        raise ApiError(
            400, f"cards not selectable in this registry: {unselectable}", param="config.cards"
        )


def _check_quota(state: AppState, quota_id: str) -> None:
    active = state.store.index.count_active_runs(quota_id)
    limit = state.settings.max_active_runs_per_key
    if active >= limit:
        raise ApiError(
            429,
            f"this launch key already has {active} active run(s) (limit {limit}); "
            "finish or abort one before launching another",
            headers={"Retry-After": str(QUOTA_RETRY_AFTER_S)},
        )


def _clean_keys(provider_keys: dict[str, str | None]) -> dict[str, str | None]:
    cleaned: dict[str, str | None] = {}
    for name, value in provider_keys.items():
        slot = name.strip().lower()
        if not slot or not slot.isidentifier():
            raise ApiError(400, f"invalid provider key slot {name!r}", param="provider_keys")
        cleaned[slot] = value.strip() if isinstance(value, str) and value.strip() else None
    return cleaned


@router.post("/runs", response_model=LaunchResponse, status_code=201)
async def launch_run(body: LaunchRequest, state: State, key: LaunchKey) -> LaunchResponse:
    _check_cards(state, body.config)
    _check_quota(state, key.quota_id)

    try:
        prereg_md = state.prereg_renderer(body.config, body.readiness)
    except ValueError as err:
        raise ApiError(400, str(err), param="config") from err
    try:
        study_dir = state.store.freeze_study(body.config, prereg_md)
    except FileExistsError as err:
        raise ApiError(409, str(err), param="config") from err
    # The hash of the prereg actually frozen with this config (a study frozen
    # earlier keeps its original document); the host's exported hash must match
    # it, otherwise the config changed after the prereg was rendered.
    frozen_hash = study_dir.prereg_sha256.read_text(encoding="utf-8").split()[0]
    prereg_stale = body.prereg_hash != frozen_hash

    secrets = SecretBag(_clean_keys(body.provider_keys))
    host_token = new_host_token()
    run = Run(
        run_id=new_run_id(),
        study_id=study_dir.study_id,
        kind=body.kind,
        state="created",
        created_at=state.clock(),
        key_name=key.quota_id,
        host_token_hash=sha256_hex(host_token),
        prereg_hash=frozen_hash,
        config_hash=config_hash(body.config),
        prereg_stale=prereg_stale,
        keys_present=secrets.keys_present(),
    )
    run_store = state.store.create_run(run)
    state.registry.register(run_store, secrets)
    log.info(
        "run launched",
        extra={
            "event": "run.launched",
            "run_id": run.run_id,
            "study_id": run.study_id,
            "kind": run.kind,
            "key_name": run.key_name,
            "keys_present": run.keys_present,
            "prereg_stale": prereg_stale,
        },
    )
    return LaunchResponse(
        run_id=run.run_id,
        study_id=run.study_id,
        host_token=host_token,
        join_code=None,
        prereg_stale=prereg_stale,
    )


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, state: State) -> RunView:
    run_store = state.run_store(run_id)
    live = state.registry.get(run_id)
    return RunView(
        run=run_store.read_run(),
        live=live is not None and live.thread_alive,
        control_state=live.control.state if live is not None else None,
        subscribers=live.bus.subscriber_count if live is not None else 0,
        readiness=run_store.read_readiness(),
    )


@router.post("/runs/{run_id}/control", response_model=RunControlResponse)
async def control_run(
    run_id: str, body: RunControlRequest, live: HostRun, state: State
) -> RunControlResponse:
    try:
        if body.action == "start":
            run = live.start(state.orchestrator_factory)
        elif body.action == "pause":
            run = live.pause()
            for controller in state.sessions.for_run(run_id):
                controller.stop()
        elif body.action == "resume":
            run = live.resume(state.orchestrator_factory)
        else:
            run = live.abort()
            for controller in state.sessions.for_run(run_id):
                controller.stop()
    except RunControlError as err:
        raise ApiError(409, str(err), param="action") from err
    log.info(
        "run control",
        extra={"event": "run.control", "run_id": run_id, "action": body.action},
    )
    return RunControlResponse(run_id=run_id, action=body.action, state=run.state)


@router.post("/runs/{run_id}/keys", response_model=RunKeysResponse)
async def supply_keys(run_id: str, body: RunKeysRequest, live: HostRun) -> RunKeysResponse:
    keys = _clean_keys(body.provider_keys)
    if not any(keys.values()):
        raise ApiError(400, "no keys supplied", param="provider_keys")
    run = live.supply_keys(keys)
    log.info(
        "run keys supplied",
        extra={"event": "run.keys", "run_id": run_id, "keys_present": run.keys_present},
    )
    return RunKeysResponse(
        run_id=run_id, keys_present=run.keys_present, state=run.state, needs_keys=run.needs_keys
    )


__all__ = ["new_run_id", "router"]
