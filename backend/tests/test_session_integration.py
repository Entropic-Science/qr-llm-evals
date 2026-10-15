"""Production factory regressions: session permission controls real dispatch, not just labels."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from qr_llm_evals.api import create_app
from qr_llm_evals.api.app import default_orchestrator_factory
from qr_llm_evals.api.deps import sha256_hex
from qr_llm_evals.cards import load_cards
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.runner.control import RunControl
from qr_llm_evals.runner.events import NullEventSink
from qr_llm_evals.runner.orchestrator import Orchestrator
from qr_llm_evals.store import Run, RunStore, Store
from qr_llm_evals.store.artifacts import iter_jsonl
from qr_llm_evals.supervised.session import SessionController
from stub_engine import StubEngine
from test_supervised import CARD_FIXTURES, NOW, ROOT, _study

HOST = {"X-Host-Token": "integration_host"}
OBSERVER = {"X-Observer-Id": "host"}
PRE = {"influence_belief": 4, "calmness": 3, "focus": 3}
POST = {"meaningful_coincidence": 1, "unusual_relevance": 1, "surprise": 1}


async def eventually(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < deadline, "production worker did not reach expected state"
        await asyncio.sleep(0.01)


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    engine: StubEngine
    run_store: RunStore

    async def run_control(self, action: str) -> None:
        response = await self.client.post(
            "/api/runs/run/control", headers=HOST, json={"action": action}
        )
        assert response.status_code == 200, response.text

    async def session(self, *, display: str = "single") -> SessionController:
        response = await self.client.post(
            "/api/runs/run/sessions",
            headers=HOST,
            json={"display": display, "intention": "Observe the synthetic sequence"},
        )
        assert response.status_code == 201, response.text
        return self.app.state.qr.sessions.get(response.json()["session"]["session_id"])

    async def answer(self, controller: SessionController, phase: str) -> None:
        response = await self.client.post(
            f"/api/sessions/{controller.session_id}/questionnaire",
            headers=OBSERVER,
            json={"phase": phase, "answers": PRE if phase == "pre" else POST},
        )
        assert response.status_code == 200, response.text

    async def session_control(
        self, controller: SessionController, action: str, expected: int = 200
    ) -> None:
        response = await self.client.post(
            f"/api/sessions/{controller.session_id}/control",
            headers=HOST,
            json={"action": action},
        )
        assert response.status_code == expected, response.text

    def supervised_requests(self) -> list[str]:
        return [ref for ref in self.engine.request_ids if ref.startswith("qrng_supervised/")]


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    settings = load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
            "QR_EVALS_MAX_INFLIGHT": "3",
        }
    )
    store = Store(settings.data_dir)
    card = load_cards(CARD_FIXTURES)["stub_free"].model_copy(
        update={
            "task_module": "backend/tests/fixtures/registry/evals/stub_free/task.py",
            "judge": load_cards(CARD_FIXTURES)["stub_free"].judge.model_copy(
                update={"kind": "none"}
            ),
        }
    )
    cards = {card.id: card}
    engine = StubEngine(chunk_delay_s=0.01)
    factory = default_orchestrator_factory(
        settings,
        store,
        cards,
        repo_root=ROOT,
        transport=engine.transport,
    )
    app = create_app(settings, store=store, cards=cards, orchestrator_factory=factory)
    async with app.router.lifespan_context(app):
        study = _study()
        control_arm = study.arms[0].model_copy(
            update={
                "id": "control",
                "observer_mode": "unsupervised",
                "source": "prng_seeded",
                "study_seed": 7,
            }
        )
        study = study.model_copy(update={"arms": [*study.arms, control_arm]})
        frozen = store.freeze_study(study, "synthetic prereg", study_id="study")
        run_store = store.create_run(
            Run(
                run_id="run",
                study_id=frozen.study_id,
                kind="pilot",
                state="ready",
                created_at=NOW,
                key_name="test",
                host_token_hash=sha256_hex(HOST["X-Host-Token"]),
                config_hash="2" * 64,
            )
        )
        app.state.qr.registry.register(run_store, SecretBag())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield Harness(app, client, engine, run_store)


@pytest.mark.parametrize("boundary", ["manual", "natural", "pause"])
async def test_production_dispatch_boundaries_and_multi_sitting_resume(
    harness: Harness,
    boundary: str,
) -> None:
    h = harness
    await h.run_control("start")
    # Waiting for a session must not monopolize the engine semaphore.
    await eventually(lambda: len(h.engine.requests) >= 2)
    assert not h.supervised_requests()
    first = await h.session()
    await h.session_control(first, "start", 409)
    await h.answer(first, "pre")
    await asyncio.sleep(0.1)
    assert not h.supervised_requests(), "pre submission is not a host start"
    await h.session_control(first, "start")
    await eventually(lambda: bool(h.supervised_requests()))
    initial_estimate = first.estimator.expected_gen_s("stub_free")
    if boundary == "manual":
        await h.session_control(first, "stop_at_boundary")
    elif boundary == "pause":
        await h.run_control("pause")
    else:
        # Freeze just *before* the target: only the next-job estimate can stop
        # admission here, not the elapsed-time tick. Keep the observer present.
        cutoff = first._session_start_perf + first.record.target_s - 0.001
        first.perf = lambda: cutoff
        first.presence.heartbeat("host", "Host", cutoff)
    await eventually(lambda: first.state == "break")
    if boundary == "natural":
        assert first.record.overrun_s < 0
    assert first.estimator.expected_gen_s("stub_free") != initial_estimate
    refs = h.supervised_requests()
    assert 0 < len(refs) < 12
    await asyncio.sleep(0.2)
    assert h.supervised_requests() == refs
    if boundary == "pause":
        await h.run_control("resume")
        await asyncio.sleep(0.1)
        assert h.supervised_requests() == refs, "run resume bypassed session boundary"
    first.tick(now=first.perf() + 10000)
    assert first.state == "break"
    # A sitting cannot be reused; its post questionnaire is per-session.
    await h.session_control(first, "end_break")
    first.tick(now=first.perf() + 10000)
    await h.session_control(first, "resume", 409)
    second = await h.session()
    await h.answer(second, "pre")
    await h.session_control(second, "resume", 409)
    await h.answer(first, "post")
    await asyncio.sleep(0.1)
    assert h.supervised_requests() == refs
    await h.session_control(second, "resume")
    await eventually(lambda: h.run_store.read_run().state == "complete")
    all_refs = h.supervised_requests()
    assert len(all_refs) == len(set(all_refs)) == 12
    rows = list(h.run_store.generations("qrng_supervised", "stub_free"))
    assert {row.session_id for row in rows} == {first.session_id, second.session_id}
    assert all(row.window == "supervised" for row in rows)
    tier0 = list(iter_jsonl(h.run_store.arm_card("qrng_supervised", "stub_free").tier0_jsonl))
    by_ref = {row.sample_ref: row for row in rows}
    assert tier0 and all(
        record["window"] == "supervised"
        and record["session_id"] == by_ref[record["sample_ref"]].session_id
        for record in tier0
    )
    assert all(
        row.window == "unsupervised" and row.session_id is None
        for row in h.run_store.generations("control", "stub_free")
    )


async def test_abort_wakes_session_waiters_and_cancels_inflight(harness: Harness) -> None:
    h = harness
    first = await h.session()
    await h.answer(first, "pre")
    await h.session_control(first, "start")
    await h.run_control("start")
    await eventually(lambda: bool(h.supervised_requests()))
    await h.run_control("abort")
    await eventually(lambda: not h.app.state.qr.registry.get("run").thread_alive)
    assert h.run_store.read_run().state == "aborted"
    assert first.state == "break"
    await h.session_control(first, "resume", 409)


async def test_no_host_or_post_gate_bypass(harness: Harness) -> None:
    h = harness
    first = await h.session()
    await h.answer(first, "pre")
    path = f"/api/sessions/{first.session_id}"
    for headers, status in (({}, 401), ({"X-Host-Token": "wrong"}, 403)):
        response = await h.client.post(path + "/control", headers=headers, json={"action": "start"})
        assert response.status_code == status
    response = await h.client.post(
        path + "/questionnaire", headers=OBSERVER, json={"phase": "post", "answers": POST}
    )
    assert response.status_code == 409

    # Expired host presence cannot turn the pre gate into an empty-set success.
    first.presence.mark_left("host", time.monotonic())
    await h.session_control(first, "start", 409)
    response = await h.client.post(
        "/api/runs/run/sessions", headers=HOST, json={"display": "single", "intention": "bypass"}
    )
    assert response.status_code == 409


async def test_inspect_uses_same_admission_completion_and_resume_path(harness: Harness) -> None:
    h = harness
    controller = await h.session()
    await h.answer(controller, "pre")
    control = RunControl()
    control.start()
    orch = h.app.state.qr.orchestrator_factory(h.run_store, SecretBag(), control, NullEventSink())
    assert isinstance(orch, Orchestrator)
    job = orch.plan_arm(orch.study.arms[0])[0]
    messages = job.task.build_messages(job.item, job.told)
    pending = asyncio.create_task(
        asyncio.to_thread(orch.dispatch_inspect, job.arm, job.card, job.ctx, messages)
    )
    try:
        await asyncio.sleep(0.15)
        assert not h.engine.requests
        await h.session_control(controller, "start")
        await eventually(lambda: bool(h.supervised_requests()))
        await h.session_control(controller, "stop_at_boundary")
        row = await asyncio.wait_for(pending, 5)
        assert controller.state == "break"
        assert row.session_id == controller.session_id and row.window == "supervised"
        cached = await asyncio.to_thread(
            orch.dispatch_inspect, job.arm, job.card, job.ctx, messages
        )
        assert cached == row
        assert h.supervised_requests() == [row.sample_ref]
    finally:
        control.abort()
        await asyncio.gather(pending, return_exceptions=True)


async def test_runtime_timers_and_late_observer_pre_gate(harness: Harness) -> None:
    h = harness
    controller = await h.session()
    timer_events: list[dict[str, object]] = []
    controller.intention_s = 0
    emit = controller.emit

    def capture(kind: str, data: dict[str, object]) -> None:
        emit(kind, data)
        if kind == "session.timer":
            timer_events.append(data)

    controller.emit = capture
    await eventually(lambda: bool(timer_events))
    assert timer_events[-1]["remaining_s"] < 0
    assert controller.state == "ready" and not h.engine.requests
    await h.answer(controller, "pre")
    await h.session_control(controller, "start")
    h.engine.chunk_delay_s = 0.03
    await h.run_control("start")
    await eventually(lambda: bool(h.supervised_requests()))
    path = f"/api/sessions/{controller.session_id}"
    response = await h.client.post(
        path + "/join",
        json={
            "join_code": controller.join_code,
            "display_name": "Late observer",
            "observer_id": "late",
            "consent": True,
        },
    )
    assert response.status_code == 201, response.text
    await eventually(lambda: bool(h.run_store.completed_refs("qrng_supervised", "stub_free")))
    count = len(h.supervised_requests())
    await asyncio.sleep(0.2)
    assert len(h.supervised_requests()) == count, "late observer's missing pre was ignored"
    response = await h.client.post(
        path + "/questionnaire",
        headers={"X-Observer-Id": "late"},
        json={"phase": "pre", "answers": PRE},
    )
    assert response.status_code == 200, response.text
    await eventually(lambda: len(h.supervised_requests()) > count)
    assert any(len(w.observers_present) == 2 for w in controller.record.windows)
    await h.run_control("abort")
    await eventually(lambda: not h.app.state.qr.registry.get("run").thread_alive)


async def test_tiled_dispatch_caps_shared_slots_and_drains_real_jobs(harness: Harness) -> None:
    h = harness
    controller = await h.session(display="tiled")
    await h.answer(controller, "pre")
    await h.session_control(controller, "start")
    control = RunControl()
    control.start()
    orch = h.app.state.qr.orchestrator_factory(h.run_store, SecretBag(), control, NullEventSink())
    assert isinstance(orch, Orchestrator)
    jobs = orch.plan_arm(orch.study.arms[0])[:4]
    h.engine.chunk_delay_s = 0.04
    pending = [asyncio.create_task(asyncio.to_thread(orch.dispatch, job)) for job in jobs]
    try:
        await eventually(lambda: len(h.supervised_requests()) >= 2)
        assert len(h.supervised_requests()) == controller.streams == 2
        await h.session_control(controller, "stop_at_boundary")
        assert controller.state == "boundary_pending"
        await eventually(lambda: controller.state == "break")
        await asyncio.sleep(0.15)
        assert len(h.supervised_requests()) == 2
        rows = list(h.run_store.generations("qrng_supervised", "stub_free"))
        assert len(rows) == 2 and all(row.finish_reason == "stop" for row in rows)
    finally:
        control.abort()
        await asyncio.wait_for(asyncio.gather(*pending), 5)


async def test_restart_restores_post_gates_not_running_state_and_skips_paid_samples(
    harness: Harness,
) -> None:
    h = harness
    first = await h.session()
    await h.answer(first, "pre")
    await h.session_control(first, "start")
    await h.run_control("start")
    await eventually(lambda: bool(h.supervised_requests()))
    await h.run_control("pause")
    await eventually(lambda: first.state == "break")
    paid = h.run_store.completed_refs("qrng_supervised", "stub_free")
    assert paid
    # Close process-local runtime; a fresh app must reconstruct persisted gates.
    await asyncio.to_thread(h.app.state.qr.registry.get("run").close)
    state = h.app.state.qr
    factory = default_orchestrator_factory(
        state.settings, state.store, state.cards, repo_root=ROOT, transport=h.engine.transport
    )
    recovered_app = create_app(
        state.settings, store=state.store, cards=state.cards, orchestrator_factory=factory
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=recovered_app), base_url="http://recovered"
    ) as client:
        recovered = Harness(recovered_app, client, h.engine, h.run_store)
        response = await client.post(
            "/api/runs/run/keys",
            headers=HOST,
            json={"provider_keys": {"anthropic": "synthetic-key"}},
        )
        assert response.status_code == 200, response.text
        old = recovered_app.state.qr.sessions.get(first.session_id)
        assert old is not None and old.state == "complete"
        assert all(window.end is not None for window in old.record.windows)
        await recovered.run_control("resume")
        count = len(h.supervised_requests())
        await asyncio.sleep(0.15)
        assert len(h.supervised_requests()) == count
        second = await recovered.session()
        assert second.record.index == first.record.index + 1
        await recovered.answer(second, "pre")
        await recovered.session_control(second, "resume", 409)
        await recovered.answer(old, "post")
        await recovered.session_control(second, "resume")
        await eventually(lambda: h.run_store.read_run().state == "complete")
        assert all(h.supervised_requests().count(ref) == 1 for ref in paid)
        assert len(h.run_store.completed_refs("qrng_supervised", "stub_free")) == 12
        recovered_app.state.qr.registry.close_all()
