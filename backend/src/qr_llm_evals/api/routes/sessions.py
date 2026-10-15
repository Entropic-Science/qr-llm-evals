"""Supervised session, group-presence and questionnaire routes (spec §4.7)."""

from __future__ import annotations

import asyncio
import secrets
import time

from fastapi import APIRouter, Header, Request

from qr_llm_evals.api.deps import HOST_TOKEN_HEADER, State, host_token_matches
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import enforce
from qr_llm_evals.api.schema import (
    CreateSessionRequest,
    SessionControlRequest,
    SessionControlResponse,
    SessionJoinRequest,
    SessionJoinResponse,
    SessionPresenceRequest,
    SessionPresenceResponse,
    SessionQuestionnaireRequest,
    SessionView,
)
from qr_llm_evals.runner.events import RunEvent
from qr_llm_evals.store.store import RunStore
from qr_llm_evals.supervised.boundary import BoundaryEstimator
from qr_llm_evals.supervised.groups import new_join_code
from qr_llm_evals.supervised.questionnaires import load_questionnaires, validate_answers
from qr_llm_evals.supervised.schema import Observer, Questionnaire, Session
from qr_llm_evals.supervised.session import (
    SessionControlError,
    SessionController,
    new_session_id,
)
from qr_llm_evals.supervised.targets import (
    TargetDrawUnavailable,
    draw_target,
    load_target_pool,
)

router = APIRouter(tags=["sessions"])


def _controller(state: State, session_id: str) -> SessionController:
    controller = state.sessions.get(session_id)
    if controller is None:
        saved = state.store.index.get_session(session_id)
        if saved is not None:
            state.live_run(str(saved["run_id"]))
            controller = state.sessions.get(session_id)
    if controller is None:
        raise ApiError(404, f"session {session_id!r} not found", param="session_id")
    return controller


def _require_session_host(controller: SessionController, token: str | None) -> None:
    if not token:
        raise ApiError(401, f"{HOST_TOKEN_HEADER} header is required for host actions")
    if not host_token_matches(token.strip(), controller.store.read_run().host_token_hash):
        raise ApiError(403, "host token does not match this run")


def _observer_for(controller: SessionController, observer_id: str) -> dict[str, object]:
    for observer in controller.store.index.list_observers(controller.record.run_id):
        if observer["observer_id"] == observer_id:
            return observer
    raise ApiError(403, "observer is not joined to this run")


@router.post("/runs/{run_id}/sessions", response_model=SessionView, status_code=201)
async def create_session(
    run_id: str,
    body: CreateSessionRequest,
    state: State,
    x_host_token: str | None = Header(default=None),
) -> SessionView:
    live = state.live_run(run_id)
    _require_session_host_for_run(live.read().host_token_hash, x_host_token)
    study = state.store.load_study(live.read().study_id)
    config = study.supervised
    if config is None:
        raise ApiError(409, "this study has no supervised configuration")
    if live.read().state in {"complete", "aborted", "analysing"}:
        raise ApiError(409, "this run no longer accepts new sessions")
    if any(c.record.ended_at is None for c in state.sessions.for_run(run_id)):
        raise ApiError(409, "end the current sitting before creating the next session")
    settings = getattr(config.settings_by_display, body.display)
    root = state.repo_root
    if root is None:
        raise ApiError(500, "repository root is unavailable")
    questionnaires = load_questionnaires(root / "config" / "questionnaires.yaml")
    targets = load_target_pool(root / "evals" / "_pools" / "targets_200.yaml")
    if config.questionnaires_hash and config.questionnaires_hash != questionnaires.sha256:
        raise ApiError(409, "frozen questionnaire hash does not match the server file")
    if config.target_pool_hash and config.target_pool_hash != targets.sha256:
        raise ApiError(409, "frozen target-pool hash does not match the server file")
    run = live.read()
    session_id = new_session_id()
    try:
        drawer = state.target_uniform_drawer
        uniform = None if drawer is None else lambda: drawer(run_id, session_id)
        target = await asyncio.to_thread(
            draw_target,
            targets.pool,
            tier0_source=run.tier0_source,
            qrng_uniform=uniform,
        )
    except TargetDrawUnavailable as err:
        raise ApiError(503, str(err), headers={"Retry-After": "5"}) from err
    existing = state.sessions.for_run(run_id)
    # Target acquisition yields to the event loop; recheck before committing.
    if any(c.record.ended_at is None for c in existing):
        raise ApiError(409, "end the current sitting before creating the next session")
    session = Session(
        session_id=session_id,
        run_id=run_id,
        index=len(existing),
        display=body.display,
        target_s=settings.session_min * 60,
        target=target,
        host_observer_id=body.host_observer_id,
    )
    tps = run_store_tps(live.run_store, study.estimates.tps_by_model)
    estimates = {
        card_id: float(state.cards[card_id].est_tokens_per_sample)
        for card_id in (selection.card_id for selection in study.cards)
        if card_id in state.cards and state.cards[card_id].est_tokens_per_sample > 0
    }
    if not estimates:
        raise ApiError(409, "supervised study has no generative cards")
    controller = SessionController(
        session,
        live.run_store,
        BoundaryEstimator(tps, estimates),
        streams=settings.streams,
        break_s=settings.break_min * 60,
        refocus_s=config.refocus_s,
        intention_s=config.intention_timer_min * 60,
        join_code=new_join_code(),
        emit=lambda type_, data: live.bus.emit(RunEvent(type_, data)),
        clock=state.clock,
    )
    controller.set_intention(body.intention)
    state.sessions.add(controller)
    observer = Observer(
        observer_id=body.host_observer_id,
        run_id=run_id,
        display_name=body.host_display_name,
        joined_at=state.clock(),
        consent_at=state.clock(),
    )
    live.run_store.append_observer(observer)
    controller.presence.heartbeat(observer.observer_id, observer.display_name, time.monotonic())
    controller.presence_changed(now=time.monotonic())
    return SessionView(
        session=controller.record,
        state=controller.state,
        join_code=controller.join_code,
        questionnaires_hash=questionnaires.sha256,
        target_pool_hash=targets.sha256,
    )


def _require_session_host_for_run(stored_hash: str, token: str | None) -> None:
    if not token:
        raise ApiError(401, f"{HOST_TOKEN_HEADER} header is required for host actions")
    if not host_token_matches(token.strip(), stored_hash):
        raise ApiError(403, "host token does not match this run")


def run_store_tps(run_store: RunStore, fallbacks: dict[str, float]) -> float:
    report = run_store.read_readiness()
    if report is not None and report.measured_tps is not None:
        return report.measured_tps
    return max(fallbacks.values(), default=1.0)


@router.post("/sessions/{session_id}/control", response_model=SessionControlResponse)
async def control_session(
    session_id: str,
    body: SessionControlRequest,
    state: State,
    x_host_token: str | None = Header(default=None),
) -> SessionControlResponse:
    controller = _controller(state, session_id)
    _require_session_host(controller, x_host_token)
    if body.action in {"start", "resume"} and controller.store.read_run().state in {
        "complete",
        "aborted",
        "needs_keys",
        "analysing",
    }:
        raise ApiError(409, "run is not available for session start/resume")
    now = time.monotonic()
    present = {item.observer_id for item in controller.current_present(now)}
    if body.action in {"start", "resume"} and controller.record.host_observer_id not in present:
        raise ApiError(409, "host presence is required before starting a session")
    for prior in state.sessions.for_run(controller.record.run_id):
        if body.action not in {"start", "resume"} or prior.record.index >= controller.record.index:
            continue
        if prior.record.ended_at is None:
            raise ApiError(409, "the previous sitting has not ended")
        prior_present = {
            item.observer_id
            for window in prior.record.windows
            if window.kind == "supervised"
            for item in window.observers_present
        }
        try:
            prior.questionnaires.require(prior.session_id, "post", prior_present)
        except ValueError as err:
            raise ApiError(409, str(err), param="action") from err
    try:
        controller.control(body.action, present_observer_ids=present)
    except SessionControlError as err:
        raise ApiError(409, str(err), param="action") from err
    return SessionControlResponse(session_id=session_id, action=body.action, state=controller.state)


@router.post("/sessions/{session_id}/join", response_model=SessionJoinResponse, status_code=201)
async def join_session(
    session_id: str, body: SessionJoinRequest, request: Request, state: State
) -> SessionJoinResponse:
    enforce(state.limiter, request, scope="session-join")
    controller = _controller(state, session_id)
    if body.join_code != controller.join_code:
        raise ApiError(403, "join code does not match this session")
    observer_id = body.observer_id or "obs_" + secrets.token_hex(8)
    now = state.clock()
    observer = Observer(
        observer_id=observer_id,
        run_id=controller.record.run_id,
        display_name=body.display_name,
        joined_at=now,
        consent_at=now,
    )
    controller.store.append_observer(observer)
    controller.questionnaires.mark_present(observer_id)
    mono = time.monotonic()
    changed = controller.presence.heartbeat(observer_id, body.display_name, mono)
    if changed:
        controller.presence_changed(now=mono)
    return SessionJoinResponse(observer=observer, session_id=session_id)


@router.post("/sessions/{session_id}/presence", response_model=SessionPresenceResponse)
async def heartbeat_presence(
    session_id: str,
    body: SessionPresenceRequest,
    state: State,
    x_observer_id: str | None = Header(default=None),
) -> SessionPresenceResponse:
    controller = _controller(state, session_id)
    if not x_observer_id:
        raise ApiError(401, "X-Observer-Id header is required")
    observer = _observer_for(controller, x_observer_id.strip())
    now = time.monotonic()
    if body.left:
        changed = controller.presence.mark_left(x_observer_id.strip(), now)
        controller.questionnaires.mark_left(x_observer_id.strip())
    else:
        controller.questionnaires.mark_present(x_observer_id.strip())
        changed = controller.presence.heartbeat(
            x_observer_id.strip(), str(observer["display_name"]), now
        )
    if changed:
        controller.presence_changed(now=now)
    present = controller.current_present(now)
    return SessionPresenceResponse(session_id=session_id, present=present)


@router.post("/sessions/{session_id}/questionnaire", response_model=Questionnaire)
async def submit_questionnaire(
    session_id: str,
    body: SessionQuestionnaireRequest,
    state: State,
    x_observer_id: str | None = Header(default=None),
) -> Questionnaire:
    controller = _controller(state, session_id)
    if not x_observer_id:
        raise ApiError(401, "X-Observer-Id header is required")
    observer_id = x_observer_id.strip()
    _observer_for(controller, observer_id)
    if state.repo_root is None:
        raise ApiError(500, "repository root is unavailable")
    loaded = load_questionnaires(state.repo_root / "config" / "questionnaires.yaml")
    try:
        validate_answers(loaded.config, body.phase, body.answers)
    except ValueError as err:
        raise ApiError(422, str(err), param="answers") from err
    record = Questionnaire(
        observer_id=observer_id,
        session_id=session_id,
        phase=body.phase,
        items_hash=loaded.sha256,
        answers=body.answers,
        free_text=body.free_text,
        at=state.clock(),
    )
    try:
        if body.phase == "post" and controller.record.ended_at is None:
            raise ApiError(409, "post questionnaire requires an ended sitting", param="phase")
        controller.questionnaires.record(record)
    except ValueError as err:
        raise ApiError(409, str(err), param="phase") from err
    controller.store.append_questionnaire(record)
    return record


__all__ = ["router"]
