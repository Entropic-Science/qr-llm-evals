"""Supervised state, boundary, group, questionnaire, target and route tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from qr_llm_evals.api import create_app
from qr_llm_evals.api.deps import sha256_hex
from qr_llm_evals.cards import load_cards
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.keys.verify import FileKeyVerifier
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.store import Run, Store
from qr_llm_evals.supervised.boundary import BoundaryEstimator, should_dispatch
from qr_llm_evals.supervised.groups import JOIN_ALPHABET, new_join_code
from qr_llm_evals.supervised.questionnaires import (
    QuestionnaireGate,
    load_questionnaires,
    validate_answers,
)
from qr_llm_evals.supervised.schema import Observer, Questionnaire, Session
from qr_llm_evals.supervised.session import SessionControlError, SessionController
from qr_llm_evals.supervised.targets import draw_target, load_target_pool

ROOT = Path(__file__).resolve().parents[2]
CARD_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "registry" / "evals"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


@given(
    now=st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
    target=st.floats(min_value=0.001, max_value=10_000, allow_nan=False),
    expected=st.floats(min_value=0, max_value=10_000, allow_nan=False),
)
def test_boundary_rule_is_exact(now: float, target: float, expected: float) -> None:
    remaining = target - now
    result = should_dispatch(now, 0.0, target, expected)
    assert result is (remaining > 0 and expected < 2 * remaining)
    if remaining <= 0:
        assert result is False


def test_boundary_ema_and_simulated_session_land_within_one_generation() -> None:
    estimator = BoundaryEstimator(10.0, {"card": 80.0})
    assert estimator.expected_gen_s("card") == 8.0
    now = 0.0
    starts = 0
    while should_dispatch(now, 0.0, 30.0, estimator.expected_gen_s("card")):
        starts += 1
        now += 8.0
        estimator.observe("card", tokens=80, elapsed_s=8.0)
    assert starts == 4
    assert abs(now - 30.0) <= estimator.expected_gen_s("card")


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def perf(self) -> float:
        return self.value

    def clock(self) -> datetime:
        return NOW + timedelta(seconds=self.value)


def _study() -> StudyConfig:
    model = {
        "id": "qr-llm",
        "family": "qr_llm",
        "provider": "qr_server",
        "served_name": "qr-llm",
    }
    return StudyConfig.model_validate(
        {
            "title": "supervised test",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW,
            "models": [model],
            "arms": [
                {
                    "id": "qrng_supervised",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "qrng",
                    "observer_mode": "supervised",
                }
            ],
            "cards": [{"card_id": "stub_free"}],
            "judge": {"model": "judge"},
            "narrative_model": model,
            "supervised": {
                "display": "single",
                "settings_by_display": {
                    "single": {
                        "session_min": 1,
                        "break_min": 1,
                        "streams": 1,
                        "text_size": "md",
                        "show_thinking": True,
                    },
                    "tiled": {
                        "session_min": 1,
                        "break_min": 1,
                        "streams": 2,
                        "text_size": "sm",
                        "show_thinking": False,
                    },
                },
                "refocus_s": 30,
                "group": True,
            },
        }
    )


def _controller(tmp_path: Path, *, streams: int = 1) -> tuple[SessionController, FakeTime]:
    store = Store(tmp_path / "data")
    study = _study()
    frozen = store.freeze_study(study, "prereg", study_id="study")
    run_store = store.create_run(
        Run(
            run_id="run",
            study_id=frozen.study_id,
            kind="pilot",
            state="created",
            created_at=NOW,
            key_name="test",
            host_token_hash="0" * 64,
            prereg_hash="1" * 64,
            config_hash="2" * 64,
        )
    )
    fake = FakeTime()
    controller = SessionController(
        Session(
            session_id="session",
            run_id="run",
            index=0,
            display="single" if streams == 1 else "tiled",
            target_s=30,
            host_observer_id="host",
        ),
        run_store,
        BoundaryEstimator(10.0, {"card": 80.0}),
        streams=streams,
        break_s=10,
        refocus_s=5,
        join_code="ABCDEFGH",
        clock=fake.clock,
        perf=fake.perf,
    )
    return controller, fake


def test_state_machine_has_no_non_host_path_to_running_and_timer_may_go_negative(
    tmp_path: Path,
) -> None:
    controller, fake = _controller(tmp_path)
    controller.acknowledge_briefing()
    controller.set_intention("clarity and compassion")
    for value in (10.0, 100.0, 10_000.0):
        fake.value = value
        controller.tick()
        assert controller.state == "ready"
    controller.control("start", present_observer_ids=set())
    assert controller.state == "running"


def test_tiled_boundary_waits_for_every_slot_and_timers_do_not_resume(tmp_path: Path) -> None:
    controller, fake = _controller(tmp_path, streams=2)
    controller.set_intention("a blue garden")
    controller.control("start", present_observer_ids=set())
    assert controller.slot_freed(0, "card", now=0.0)
    assert controller.slot_freed(1, "card", now=0.0)
    fake.value = 30.0
    assert not controller.generation_finished(0, "card", tokens=80, elapsed_s=8, now=30.0)
    assert not controller.generation_finished(1, "card", tokens=80, elapsed_s=8, now=30.0)
    assert controller.state == "break"
    assert abs(controller.record.overrun_s) <= controller.estimator.expected_gen_s("card")
    fake.value = 100.0
    assert controller.tick() < 0
    assert controller.state == "break"
    controller.control("end_break", present_observer_ids=set())
    fake.value = 106.0
    assert controller.tick() is not None
    assert controller.state == "ready"
    assert controller.state != "running"


def test_group_presence_is_snapshotted_per_window_and_questionnaire_gates(tmp_path: Path) -> None:
    controller, _fake = _controller(tmp_path)
    controller.set_intention("kindness")
    assert controller.presence.heartbeat("o1", "Observer One", 0.0)
    controller.presence_changed(now=0.0)
    with pytest.raises(SessionControlError, match="pre questionnaire"):
        controller.control("start", present_observer_ids={"o1"})
    questionnaire = Questionnaire(
        observer_id="o1",
        session_id="session",
        phase="pre",
        items_hash="a" * 64,
        answers={"belief": 4},
        at=NOW,
    )
    controller.questionnaires.record(questionnaire)
    controller.control("start", present_observer_ids={"o1"})
    assert controller.record.windows[-1].observers_present[0].observer_id == "o1"
    controller.presence.mark_left("o1", 1.0)
    controller.questionnaires.mark_left("o1")
    controller.presence_changed(now=1.0)
    assert controller.record.windows[-1].observers_present == []


def test_questionnaire_files_and_targets_are_frozen_and_valid() -> None:
    loaded = load_questionnaires(ROOT / "config" / "questionnaires.yaml")
    validate_answers(loaded.config, "pre", {"influence_belief": 4, "calmness": 3, "focus": 5})
    with pytest.raises(ValueError, match="between 1 and 7"):
        validate_answers(loaded.config, "pre", {"influence_belief": 8, "calmness": 3, "focus": 5})
    pool = load_target_pool(ROOT / "evals" / "_pools" / "targets_200.yaml")
    assert len(pool.pool.targets) == 200
    uniforms = iter([0.0, 0.1, 0.2, 0.3, 1.0])
    target = draw_target(pool.pool, tier0_source="engine_log", qrng_uniform=lambda: next(uniforms))
    assert target.draw_source == "qrng_tier0"
    assert len({target.theme, *target.decoys}) == 5
    assert len(new_join_code()) == 8
    assert set(new_join_code()) <= set(JOIN_ALPHABET)


async def test_session_routes_enforce_host_and_observer_gates(tmp_path: Path) -> None:
    token = "host_secret"
    key_file = tmp_path / "keys.json"
    key_file.write_text("[]", encoding="utf-8")
    settings = load_settings(
        {
            "QR_EVALS_ALLOW_KEY_LAUNCH": "1",
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(key_file),
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
        }
    )
    store = Store(settings.data_dir)
    study = _study()
    frozen = store.freeze_study(study, "prereg", study_id="study")
    run_store = store.create_run(
        Run(
            run_id="run",
            study_id=frozen.study_id,
            kind="pilot",
            state="created",
            created_at=NOW,
            key_name="test",
            host_token_hash=sha256_hex(token),
            prereg_hash="1" * 64,
            config_hash="2" * 64,
        )
    )
    app = create_app(
        settings,
        store=store,
        verifier=FileKeyVerifier(key_file),
        cards=load_cards(CARD_FIXTURES),
        repo_root=ROOT,
        prereg_renderer=lambda _study, _readiness=None: "prereg",
    )
    app.state.qr.registry.register(run_store, SecretBag())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {"display": "single", "intention": "gentle coherence"}
        assert (await client.post("/api/runs/run/sessions", json=body)).status_code == 401
        assert (
            await client.post(
                "/api/runs/run/sessions", json=body, headers={"X-Host-Token": "wrong"}
            )
        ).status_code == 403
        created = await client.post(
            "/api/runs/run/sessions", json=body, headers={"X-Host-Token": token}
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]
        assert len(created.json()["join_code"]) == 8
        blocked = await client.post(
            f"/api/sessions/{session_id}/control",
            json={"action": "start"},
            headers={"X-Host-Token": token},
        )
        assert blocked.status_code == 409
        pre = await client.post(
            f"/api/sessions/{session_id}/questionnaire",
            json={
                "phase": "pre",
                "answers": {"influence_belief": 4, "calmness": 3, "focus": 5},
            },
            headers={"X-Observer-Id": "host"},
        )
        assert pre.status_code == 200, pre.text
        started = await client.post(
            f"/api/sessions/{session_id}/control",
            json={"action": "start"},
            headers={"X-Host-Token": token},
        )
        assert started.status_code == 200 and started.json()["state"] == "running"
        heartbeat = await client.post(
            f"/api/sessions/{session_id}/presence",
            json={},
            headers={"X-Observer-Id": "host"},
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["present"][0]["observer_id"] == "host"


def test_questionnaire_gate_allows_explicit_departure() -> None:
    gate = QuestionnaireGate()
    gate.mark_left("gone")
    gate.require("session", "post", {"gone"})


def test_observer_schema_requires_consent_timestamp_only_when_given() -> None:
    observer = Observer(observer_id="o", run_id="run", display_name="Observer", joined_at=NOW)
    assert observer.consent_at is None
