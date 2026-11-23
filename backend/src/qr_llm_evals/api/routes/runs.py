"""Run lifecycle routes (spec §2.4, §4.7, D-13, D-14).

``POST /runs`` (session or launch key)
    Quota check → freeze the study (config + prereg, and for a designed study
    its analysis plan) → create the run record with the host token's hash →
    put the BYO keys into the run's ``SecretBag``. Returns the host token
    exactly once. A study that is already frozen with the same config is not
    frozen again: it keeps its pre-registration and plan and gets the new run.
    A designed study takes runs only from the launcher who registered it.
``GET /runs/{id}``
    The stored record (which structurally cannot hold a secret: ``keys_present``
    booleans only) plus live facts from the registry. The record carries the
    run's ``halts`` (every stop by a result check) and its ``checks_version``.
``GET /runs/{id}/health``
    The health summary of the run (``store.health``): counts per arm and card,
    the halts and the cautions. Open, like ``GET /runs/{id}``.
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

from qr_llm_evals.api.deps import (
    AppState,
    HostRun,
    LaunchAuth,
    Launcher,
    State,
    new_host_token,
    sha256_hex,
)
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.gates import check_external_models, check_owner_can_generate, has_charged_arms
from qr_llm_evals.api.routes.analysis import archive_plan, frozen_plan
from qr_llm_evals.api.routes.estimates import estimate_study
from qr_llm_evals.api.routes.run_health import run_health
from qr_llm_evals.api.schema import (
    LaunchRequest,
    LaunchResponse,
    RunControlRequest,
    RunControlResponse,
    RunKeysRequest,
    RunKeysResponse,
    RunView,
)
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.runner.control import RunControlError
from qr_llm_evals.runner.judge_phase import needs_study_judge
from qr_llm_evals.store.artifacts import read_json
from qr_llm_evals.store.bundle import derive_study_id, prereg_sha256
from qr_llm_evals.store.health import HealthSummary
from qr_llm_evals.store.paths import StudyDir
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
    unselectable = [c.card_id for c in config.cards if not _launchable(state.cards[c.card_id])]
    if unselectable:
        raise ApiError(
            400, f"cards not selectable in this registry: {unselectable}", param="config.cards"
        )


def _launchable(card: EvalCard) -> bool:
    """May a study name this card?

    ``selectable`` says whether a person may tick the card on its own. The
    derived cards (Tier 0 and the stream-deviation windows) are not: they
    generate nothing and are computed from the records of the other cards, so
    they only make sense inside a study that lists them next to those cards.
    Such a study must still be launchable, which is how Study 1 and the thesis
    study are written. A planned card is never launchable.
    """
    if card.selectable:
        return True
    return card.status == "implemented" and card.dataset.scheme == "derived"


def _check_judge_key(state: AppState, config: StudyConfig, secrets: SecretBag) -> None:
    """Refuse a study the shared judge must score when no Anthropic key came with it.

    The judge runs on the launching user's own key. Without this check the
    run would generate everything and only then stop for the key.
    """
    cards = [state.cards[c.card_id] for c in config.cards]
    if needs_study_judge(cards) and not secrets.get("anthropic"):
        raise ApiError(
            400,
            "this study has evals scored by an LLM judge, which runs on your own Anthropic "
            "key; add the key (provider_keys.anthropic) and launch again",
            param="provider_keys",
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


def _check_token_quota(state: AppState, launcher: Launcher, body: LaunchRequest) -> None:
    """Refuse a full run whose estimate the owner's remaining budget cannot cover.

    :func:`check_owner_can_generate` has already refused a spent budget. Only
    qr-server arms are charged. A pilot is not held to the estimate, so it
    only needs a budget that is not already spent. That is a choice, not a
    saving: the run kind does not shorten a run, and a pilot generates the
    study exactly as it is configured. Any run that outgrows the budget is
    paused when it reaches zero (:mod:`qr_llm_evals.api.usage`); two launches
    at once are not reserved against each other, and that pause is what bounds
    the overshoot.
    """
    if launcher.owner_id is None or body.kind != "full" or not has_charged_arms(body.config):
        return
    quota = state.usage.quota_for(launcher.owner_id)
    if quota is None or quota.unlimited:
        return
    providers = {model.id: model.provider for model in body.config.models}
    by_arm = estimate_study(state, body.config).by_arm
    needed = sum(
        by_arm[arm.id].tokens_per_arm
        for arm in body.config.arms
        if providers.get(arm.model) == "qr_server"
    )
    remaining = quota.remaining or 0
    if needed > remaining:
        raise ApiError(
            403,
            f"this study is estimated at {needed:,} qr-server generation tokens but the "
            f"account has {remaining:,} left of {quota.limit:,}; reduce the study or ask an "
            "admin to raise the quota",
        )


def _clean_keys(provider_keys: dict[str, str | None]) -> dict[str, str | None]:
    cleaned: dict[str, str | None] = {}
    for name, value in provider_keys.items():
        slot = name.strip().lower()
        if not slot or not slot.isidentifier():
            raise ApiError(400, f"invalid provider key slot {name!r}", param="provider_keys")
        cleaned[slot] = value.strip() if isinstance(value, str) and value.strip() else None
    return cleaned


def _frozen_study(state: AppState, config: StudyConfig) -> StudyDir | None:
    """The study already frozen with exactly this config; ``None`` if there is none."""
    try:
        study_dir = state.store.study(derive_study_id(config))
    except FileNotFoundError:
        return None
    recorded = read_json(study_dir.study_json).get("config_hash")
    return study_dir if recorded == config_hash(config) else None


def _launched_by(owner_id: str | None, key_name: str) -> tuple[str, str]:
    """Who launched: the account, or the launch key of a run launched without one."""
    return ("account", owner_id) if owner_id is not None else ("key", key_name)


def _check_registered_by(state: AppState, study_dir: StudyDir, launcher: Launcher) -> None:
    """Refuse a new run of a designed study that somebody else registered.

    A study is identified by its config alone, and a frozen config is public.
    Only the first full run of a designed study is confirmatory, so a run
    added by anybody else could use up that run, or stand before it. The
    study therefore takes runs only from the launcher of the runs it has.
    """
    ours = _launched_by(launcher.owner_id, launcher.quota_id)
    for run_id in study_dir.run_ids():
        try:
            run = state.store.run(study_dir.study_id, run_id).read_run()
        except (OSError, ValueError):
            continue  # an unreadable record names no launcher
        if _launched_by(run.owner_id, run.key_name) != ours:
            raise ApiError(
                409,
                f"study {study_dir.study_id!r} was registered by another account, and only "
                "that account can add runs to it. To run this design as your own study, "
                "change its title or its creation time (created_at) and launch it again; "
                "it is then registered under your account as a study of its own.",
                param="config",
            )


def _relaunch_is_stale(state: AppState, body: LaunchRequest, frozen_hash: str) -> bool:
    """Whether the document the host exported belongs to another study than the frozen one.

    The pre-registration prints the readiness report it was rendered with, so
    the same study rendered for a later launch has other bytes and another
    hash. That is not a stale document: the config is the frozen one, and the
    frozen document is the one the run is recorded under. The flag is raised
    only when the host's hash is neither the frozen document's nor that of
    this launch's own rendering, or when the host presented no hash at all.
    """
    if body.prereg_hash is None:
        return True
    if body.prereg_hash == frozen_hash:
        return False
    try:
        rendered = state.prereg_renderer(body.config, body.readiness)
    except ValueError:
        return True  # nothing to compare the host's hash with
    return body.prereg_hash != prereg_sha256(rendered)


@router.post("/runs", response_model=LaunchResponse, status_code=201)
async def launch_run(body: LaunchRequest, state: State, key: LaunchAuth) -> LaunchResponse:
    _check_cards(state, body.config)
    secrets = SecretBag(_clean_keys(body.provider_keys))
    check_external_models(state, body.config)
    _check_quota(state, key.quota_id)
    check_owner_can_generate(state, key.owner_id, body.config)
    _check_token_quota(state, key, body)
    _check_judge_key(state, body.config, secrets)

    study_dir = _frozen_study(state, body.config)
    if study_dir is not None:
        # A relaunch: a pilot and then the full run, or a run after an aborted
        # one. The study was registered at its first launch and stays as it is.
        # Nothing is frozen or compared with the frozen document here, because
        # a document rendered now carries this launch's readiness report and
        # would never equal it.
        if body.config.design is not None:
            _check_registered_by(state, study_dir, key)
            try:
                # The registered plan, checked against the frozen document. A
                # plan file lost since the first launch is restored only if the
                # registry still gives the registered plan.
                frozen_plan(state, study_dir.study_id)
            except (OSError, ValueError) as err:
                raise ApiError(
                    409, f"frozen analysis plan is unavailable: {err}", param="config"
                ) from err
        frozen_hash = study_dir.prereg_sha256.read_text(encoding="utf-8").split()[0]
        prereg_stale = _relaunch_is_stale(state, body, frozen_hash)
    else:
        try:
            prereg_md = state.prereg_renderer(body.config, body.readiness)
            # A designed study's verdicts are read off its plan. The plan is built
            # before anything is frozen, so a design the plan refuses leaves no
            # study behind, and it is archived with the study instead of at the
            # first analysis, so code deployed in between cannot change it. A
            # study without a design block keeps the lazy write.
            plan = (
                StatisticalPlan.from_config(body.config, state.cards)
                if body.config.design is not None
                else None
            )
        except ValueError as err:
            raise ApiError(400, str(err), param="config") from err
        try:
            study_dir = state.store.freeze_study(body.config, prereg_md)
        except FileExistsError as err:
            raise ApiError(409, str(err), param="config") from err
        if plan is not None:
            archive_plan(study_dir.path, plan)
        # The hash of the prereg frozen with this config; the host's exported
        # hash must match it, otherwise the config changed after the prereg was
        # rendered.
        frozen_hash = study_dir.prereg_sha256.read_text(encoding="utf-8").split()[0]
        prereg_stale = body.prereg_hash != frozen_hash

    host_token = new_host_token()
    run = Run(
        run_id=new_run_id(),
        study_id=study_dir.study_id,
        kind=body.kind,
        state="created",
        created_at=state.clock(),
        key_name=key.quota_id,
        owner_id=key.owner_id,
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
            "owner_id": run.owner_id,
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
    run = run_store.read_run()
    quota = state.usage.quota_for(run.owner_id) if run.owner_id is not None else None
    return RunView(
        # This route is open: the launching account's id stays server-side.
        run=run.model_copy(update={"owner_id": None}),
        owner_quota_exhausted=quota is not None and quota.exhausted,
        live=live is not None and live.thread_alive,
        control_state=live.control.state if live is not None else None,
        subscribers=live.bus.subscriber_count if live is not None else 0,
        readiness=run_store.read_readiness(),
    )


@router.get("/runs/{run_id}/health", response_model=HealthSummary)
def get_run_health(run_id: str, state: State) -> HealthSummary:
    """What the run folder holds and what a reader should be careful about.

    As open as ``GET /runs/{id}``: the summary holds counts, the halts and
    sentences the harness wrote, and nothing about the launching account. It
    reads every stored row, so the handler runs on a worker thread.
    """
    return run_health(state.run_store(run_id))


@router.post("/runs/{run_id}/control", response_model=RunControlResponse)
async def control_run(
    run_id: str, body: RunControlRequest, live: HostRun, state: State
) -> RunControlResponse:
    if body.action in ("start", "resume"):
        record = live.read()
        check_owner_can_generate(state, record.owner_id, state.store.load_study(record.study_id))
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
