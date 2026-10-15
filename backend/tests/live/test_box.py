"""Opt-in real-engine acceptance. No mocks, no key provisioning, no deletions.

Run on qr-server or with explicit engine/API origins (e.g. SSH forwards).
Private key files contain raw keys, NOT the server's SHA-256 key registry;
these tests cannot reconstruct keys from digests and never modify that file.
Only connectivity/missing-credential prerequisites skip. Reachable unhealthy
services, rejected envelopes and non-deterministic replays FAIL acceptance.
"""

from __future__ import annotations

import faulthandler
import json
import secrets
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from qr_llm_evals.cards import load_cards
from qr_llm_evals.client.openai_client import OpenAIClient
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.readiness import EngineProbe, ReadinessConfig, replays_identical, run_readiness
from qr_llm_evals.readiness.determinism import replay_solo
from qr_llm_evals.runner import Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.store import Run, Store

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def live_wall_clock_deadline() -> Iterator[None]:
    # HTTP read timeouts are not total deadlines: endless idle SSE frames can
    # keep a request alive forever. On Windows a hard process deadline is the
    # reliable last resort; retained real artifacts must never be in tmp_path.
    faulthandler.dump_traceback_later(300, file=sys.__stderr__, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def _reachable(url: str) -> httpx.Response:
    try:
        response = httpx.get(url, timeout=5)
    except (httpx.ConnectError, httpx.TimeoutException) as error:
        pytest.skip(f"service unreachable: {url} ({type(error).__name__})")
    assert response.status_code == 200, (
        f"reachable service returned {response.status_code} at {url}"
    )
    return response


@pytest.fixture(scope="module")
def box_settings(request: pytest.FixtureRequest) -> Settings:
    settings = load_settings()
    origin = request.config.getoption("--live-engine") or settings.vllm_origin
    settings = settings.model_copy(update={"vllm_origin": origin})
    _reachable(origin.rstrip("/") + "/health/entropy")
    return settings


@pytest.fixture(scope="module")
def study(request: pytest.FixtureRequest) -> StudyConfig:
    config = yaml.safe_load((ROOT / "studies/study1.yaml").read_text(encoding="utf-8"))
    config["arms"] = [arm for arm in config["arms"] if arm.get("availability") != "unavailable"]
    config["models"][0]["served_name"] = request.config.getoption("--live-model")
    config["title"] = "Live acceptance probe — not a research study"
    config["contact"] = "acceptance@example.invalid"
    config["cards"] = [{"card_id": "creative_diversity", "overrides": {"items": 2}}]
    return StudyConfig.model_validate(config)


def test_real_engine_readiness_and_each_seed_replay(
    box_settings: Settings, study: StudyConfig
) -> None:
    report = run_readiness(study, ReadinessConfig.from_settings(box_settings))
    assert report.entropy.rpc_ok and not report.entropy.currently_degraded
    assert report.entropy.source_type == "quantum_grpc"
    assert report.equivalence_ok
    assert set(report.arms) == {arm.id for arm in study.arms}
    assert len(report.arms) == 5 and all(arm.available for arm in report.arms.values())
    assert report.determinism_tier != "none"
    assert report.tier0_source in {"api_logprobs", "engine_log"}
    client = OpenAIClient(box_settings.vllm_origin, timeout=120)
    probe = EngineProbe(client, {model.id: model for model in study.models})
    # The readiness report measures one representative seed. Acceptance must
    # independently test all three Study 1 seeded arms, five solo replays each.
    for arm in study.arms:
        if arm.source == "prng_seeded":
            results = replay_solo(probe, arm, replays=5, max_tokens=64)
            assert replays_identical(results), f"five solo replays diverged for {arm.id}"


def test_real_64_token_generation_per_arm(
    box_settings: Settings,
    study: StudyConfig,
) -> None:
    original = load_cards(ROOT / "backend/tests/fixtures/registry/evals")["stub_free"]
    data = original.model_dump()
    data.update(
        k=1,
        max_tokens=64,
        told_actual=False,
        judge={"kind": "none"},
        dataset={**data["dataset"], "items": 1},
        est_gen_tokens_per_arm=20,
        task_module="backend/tests/fixtures/registry/evals/stub_free/task.py",
    )
    card = type(original).model_validate(data)
    config = study.model_dump()
    config["cards"] = [{"card_id": card.id}]
    probe_study = StudyConfig.model_validate(config)
    # Real generations cost GPU/QRNG time: never put them under pytest's
    # auto-cleaned tmp_path. Keep this separate from research study data.
    store = Store(Path(box_settings.data_dir) / "live-acceptance")
    try:
        frozen = store.freeze_study(
            probe_study, "# Live acceptance probe; not a preregistered experiment\n"
        )
        run_store = store.create_run(
            Run(
                run_id="live_" + secrets.token_hex(8),
                study_id=frozen.study_id,
                kind="pilot",
                state="created",
                created_at=datetime.now(UTC),
                key_name="local-live-acceptance",
                host_token_hash="0" * 64,
                config_hash=config_hash(probe_study),
            )
        )
        control = RunControl()
        control.start()
        Orchestrator(
            probe_study,
            {card.id: card},
            run_store,
            RunnerConfig(vllm_origin=box_settings.vllm_origin, max_inflight=1, repo_root=ROOT),
            control=control,
            tier0_source="api_logprobs",
        ).run()
        assert run_store.read_run().state == "complete"
        for arm in probe_study.arms:
            rows = run_store.generations(arm.id, card.id)
            assert len(rows) == 1
            row = rows[0]
            assert row.finish_reason in {"stop", "length"}
            # EOS before the requested 64-token cap is legitimate.
            assert 0 < row.usage.completion_tokens <= 64
            records = [
                json.loads(line)
                for line in run_store.arm_card(arm.id, card.id)
                .tier0_jsonl.read_text(encoding="utf-8")
                .splitlines()
            ]
            assert records, f"missing logprobs for {arm.id}"
            provenance = row.provenance
            assert provenance.pipeline_pinned
            if arm.source == "qrng":
                assert provenance.engine_last_source == "quantum_grpc"
                assert provenance.quantum is (
                    provenance.engine_fallback_delta == 0 and not provenance.engine_degraded
                )
            else:
                assert provenance.source_label == "seeded_prng"
                assert provenance.quantum is False
                assert provenance.study_seed == arm.study_seed and row.qr_seed is not None
    finally:
        store.close()


@pytest.fixture(scope="module")
def api(request: pytest.FixtureRequest) -> str:
    base = request.config.getoption("--live-api").rstrip("/")
    base = base if base.endswith("/api") else base + "/api"
    _reachable(base + "/health")
    return base


def _key(request: pytest.FixtureRequest, option: str) -> str:
    path = request.config.getoption(option)
    if not path:
        pytest.skip(
            f"requires {option}; raw key cannot be recovered from the server digest registry"
        )
    key = Path(path).read_text(encoding="utf-8").strip()
    if not key.startswith("qrk_"):
        pytest.fail(f"{option} must contain one raw launch key (never a registry JSON)")
    return key


def test_unknown_key_is_401(api: str, study: StudyConfig) -> None:
    with httpx.Client(timeout=30) as client:
        response = client.post(
            api + "/runs",
            json={"config": study.model_dump(mode="json"), "kind": "pilot"},
            headers={"Authorization": "Bearer qrk_unknown_" + secrets.token_hex(24)},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == 401


def test_disabled_key_is_403(api: str, study: StudyConfig, request: pytest.FixtureRequest) -> None:
    key = _key(request, "--live-disabled-key-file")
    with httpx.Client(timeout=30) as client:
        response = client.post(
            api + "/runs",
            json={"config": study.model_dump(mode="json"), "kind": "pilot"},
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == 403


def test_valid_key_is_201_then_abort_idle_probe(
    api: str,
    study: StudyConfig,
    request: pytest.FixtureRequest,
) -> None:
    key = _key(request, "--live-key-file")
    with httpx.Client(timeout=30) as client:
        response = client.post(
            api + "/runs",
            json={"config": study.model_dump(mode="json"), "kind": "pilot"},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert response.status_code == 201
        launched = response.json()
        try:
            view = client.get(api + f"/runs/{launched['run_id']}")
            assert view.status_code == 200
            assert view.json()["run"]["state"] == "created"
            assert key not in view.text
        finally:
            stopped = client.post(
                api + f"/runs/{launched['run_id']}/control",
                json={"action": "abort"},
                headers={"X-Host-Token": launched["host_token"]},
            )
            assert stopped.status_code == 200  # retained, never deleted
