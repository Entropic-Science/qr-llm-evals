"""HTTP/SSE API contract tests (spec §4.7, §4.8, §2.4, §5.2 "Secrets"; AGENTS.md invariants 3, 6).

Every test drives the FastAPI app in-process through ``httpx.ASGITransport``
against a ``tmp_path`` data root, a fixture keys file and the stub engine /
stub judge transports from ``stub_engine.py``. The orchestrator factory is
injected so a launched run really executes (on a daemon thread) and the SSE
stream carries real ``sample.*`` events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from qr_llm_evals import preflight
from qr_llm_evals.api import create_app
from qr_llm_evals.api.deps import AppState, host_token_matches, new_host_token
from qr_llm_evals.api.ratelimit import RateLimited, TokenBucketLimiter
from qr_llm_evals.api.registry import mark_orphans
from qr_llm_evals.api.sse import KEEPALIVE_FRAME, event_frames, merge_deltas
from qr_llm_evals.cards import load_cards
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.keys.verify import FileKeyVerifier, sha256_hex
from qr_llm_evals.logging_utils import REDACTED, SecretFilter, _clear_registry_for_tests
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.runner import NullEventSink, Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.runner.events import EventSink, RunEvent, RunEventBus
from qr_llm_evals.store import Run, Store
from qr_llm_evals.store.store import RunStore
from stub_engine import StubEngine, StubJudge

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "registry"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
GOOD_KEY = "qrk_" + "good_key_0123456789abcdef"
BAD_KEY = "qrk_" + "not_in_the_file_0123456789"
ANTHROPIC_SECRET = "sk-ant-" + "api03-supersecret-0123456789"
API = "/api"


# ── fixtures ─────────────────────────────────────────────────────────────────


def _study_dict(**overrides: Any) -> dict[str, Any]:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    base: dict[str, Any] = {
        "title": "API test study",
        "authors": ["Entropic Science"],
        "contact": "hello@entropic.science",
        "created_at": NOW.isoformat(),
        "models": [model],
        "arms": [
            {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"},
            {
                "id": "prng_seed_7",
                "model": "qr-llm",
                "sampling": "creative",
                "source": "prng_seeded",
                "study_seed": 7,
            },
        ],
        "cards": [{"card_id": "stub_free"}],
        "judge": {"model": "claude-sonnet-4-6"},
        "narrative_model": model,
    }
    base.update(overrides)
    return base


@pytest.fixture
def keys_file(tmp_path: Path) -> Path:
    path = tmp_path / "api-keys.json"
    path.write_text(
        json.dumps([{"name": "alice", "key_sha256": sha256_hex(GOOD_KEY)}]), encoding="utf-8"
    )
    return path


@pytest.fixture
def settings(tmp_path: Path, keys_file: Path) -> Settings:
    return load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys_file),
            "QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY": "1",
            "QR_EVALS_MAX_INFLIGHT": "2",
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
            "QR_EVALS_COMMIT": "deadbeef",
        }
    )


class Harness:
    """One app + the stub services behind it."""

    def __init__(self, settings: Settings, *, engine: StubEngine | None = None) -> None:
        self.settings = settings
        self.engine = engine or StubEngine()
        self.judge = StubJudge()
        self.store = Store(settings.data_dir)
        self.app: FastAPI = create_app(
            settings,
            store=self.store,
            verifier=FileKeyVerifier(settings.keys_file),
            cards=load_cards(FIXTURES / "evals"),
            orchestrator_factory=self.factory,
            limiter=TokenBucketLimiter(rate_per_s=1000.0, burst=1000),
            engine_transport=httpx.MockTransport(self._engine_health),
            readiness_transport=self.engine.transport,
            repo_root=FIXTURES,
        )

    @property
    def state(self) -> AppState:
        state: AppState = self.app.state.qr
        return state

    def _engine_health(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    def factory(
        self, run_store: RunStore, secrets: SecretBag, control: RunControl, sink: EventSink
    ) -> Orchestrator:
        run = run_store.read_run()
        study = self.store.load_study(run.study_id)
        return Orchestrator(
            study,
            load_cards(FIXTURES / "evals"),
            run_store,
            RunnerConfig(vllm_origin="http://stub", max_inflight=2, repo_root=FIXTURES),
            secrets=secrets,
            sink=sink,
            control=control,
            transport=self.engine.transport,
            judge_transport=self.judge.transport,
            clock=lambda: NOW,
            perf=lambda: 0.0,
        )


@pytest.fixture
async def harness(settings: Settings) -> AsyncIterator[Harness]:
    h = Harness(settings)
    async with h.app.router.lifespan_context(h.app):
        yield h


@pytest.fixture
async def client(harness: Harness) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=harness.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(key: str = GOOD_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _launch(
    client: httpx.AsyncClient, *, keys: dict[str, str] | None = None, **body: Any
) -> dict[str, Any]:
    payload = {"config": _study_dict(), "kind": "pilot", "provider_keys": keys or {}, **body}
    resp = await client.post(f"{API}/runs", json=payload, headers=_auth())
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def _wait_state(
    client: httpx.AsyncClient, run_id: str, wanted: set[str], timeout: float = 15.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = (await client.get(f"{API}/runs/{run_id}")).json()
        if view["run"]["state"] in wanted:
            return view
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {wanted}")


def _assert_envelope(resp: httpx.Response, status: int, type_: str | None = None) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert set(err) == {"message", "type", "param", "code"}
    assert err["code"] == status
    if type_:
        assert err["type"] == type_
    assert isinstance(err["message"], str) and err["message"]
    return dict(err)


# ── open routes ───────────────────────────────────────────────────────────────


async def test_health_reports_engine_and_commit(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{API}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["engine"]["reachable"] is True
    assert body["commit"] == "deadbeef"
    assert body["key_verify"] == "file"
    assert body["active_runs"] == 0


async def test_cards_list_and_detail_with_estimates(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{API}/cards")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    view = body["cards"][0]
    assert view["card"]["id"] == "stub_free"
    # 2 items × k 2 × 20 tokens × 3 told cells (card fixture) on qr-llm at 400 tps.
    assert view["estimate"]["tokens_per_arm"] == 2 * 2 * 20 * 3
    assert view["estimate"]["model_id"] == "qr-llm"
    detail = await client.get(f"{API}/cards/stub_free")
    assert detail.json() == view
    _assert_envelope(await client.get(f"{API}/cards/nope"), 404, "not_found_error")


async def test_models_catalogue(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{API}/models")).json()
    ids = [m["id"] for m in body["models"]]
    assert "qr-llm" in ids
    qr = next(m for m in body["models"] if m["id"] == "qr-llm")
    assert qr["pipeline_pinned"] is True and qr["key_slot"] is None
    assert "unknown" in {p["id"] for p in body["providers"]}
    assert body["default_tps_by_model"]["qr-llm"] == 400.0


async def test_estimates_per_arm_and_supervised_line(client: httpx.AsyncClient) -> None:
    config = _study_dict(
        arms=[
            {
                "id": "qrng_supervised",
                "model": "qr-llm",
                "sampling": "creative",
                "source": "qrng",
                "observer_mode": "supervised",
            },
            {
                "id": "prng_seed_7",
                "model": "qr-llm",
                "sampling": "creative",
                "source": "prng_seeded",
                "study_seed": 7,
            },
        ],
        supervised={"display": "single"},
    )
    resp = await client.post(f"{API}/estimates", json=config)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["by_arm"]) == {"qrng_supervised", "prng_seed_7"}
    per_arm = body["by_arm"]["qrng_supervised"]["tokens_per_arm"]
    assert body["totals"]["tokens"] == 2 * per_arm
    sup = body["supervised"]
    assert sup["arms"] == ["qrng_supervised"] and sup["display"] == "single"
    assert sup["session_min"] == 30 and sup["break_min"] == 10
    assert sup["observer_hours"] == pytest.approx(per_arm / 45.0 / 3600.0)


async def test_estimates_unknown_card_and_validation_envelopes(client: httpx.AsyncClient) -> None:
    bad_card = _study_dict(cards=[{"card_id": "ghost"}])
    err = _assert_envelope(await client.post(f"{API}/estimates", json=bad_card), 400)
    assert err["param"] == "cards"
    err = _assert_envelope(
        await client.post(f"{API}/estimates", json={"title": 1}), 400, "invalid_request_error"
    )
    assert err["param"] is not None
    _assert_envelope(await client.get(f"{API}/no-such-route"), 404)


async def test_open_post_is_rate_limited_per_ip(settings: Settings) -> None:
    h = Harness(settings)
    h.state.limiter = TokenBucketLimiter(rate_per_s=0.5, burst=2)
    async with h.app.router.lifespan_context(h.app):
        transport = httpx.ASGITransport(app=h.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(2):
                assert (await c.post(f"{API}/estimates", json=_study_dict())).status_code == 200
            resp = await c.post(f"{API}/estimates", json=_study_dict())
            err = _assert_envelope(resp, 429, "rate_limit_error")
            assert int(resp.headers["Retry-After"]) >= 1
            assert "retry" in err["message"]
            # Another address has its own bucket.
            other = await c.post(
                f"{API}/estimates", json=_study_dict(), headers={"X-Forwarded-For": "10.0.0.9"}
            )
            assert other.status_code == 200


def test_token_bucket_refills() -> None:
    now = [0.0]
    limiter = TokenBucketLimiter(rate_per_s=1.0, burst=2, clock=lambda: now[0])
    limiter.take("a")
    limiter.take("a")
    with pytest.raises(RateLimited) as exc:
        limiter.take("a")
    assert exc.value.retry_after_s == pytest.approx(1.0)
    now[0] = 1.0
    limiter.take("a")  # refilled one token


# ── launch gate: auth matrix ──────────────────────────────────────────────────


async def test_launch_requires_a_known_key(client: httpx.AsyncClient) -> None:
    body = {"config": _study_dict(), "kind": "pilot"}
    resp = await client.post(f"{API}/runs", json=body)
    _assert_envelope(resp, 401, "authentication_error")
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")
    _assert_envelope(
        await client.post(f"{API}/runs", json=body, headers=_auth(BAD_KEY)),
        401,
        "authentication_error",
    )
    _assert_envelope(
        await client.post(f"{API}/runs", json=body, headers={"Authorization": "Basic abc"}),
        401,
    )
    _assert_envelope(await client.post(f"{API}/readiness", json={"config": _study_dict()}), 401)


async def test_launch_returns_host_token_once_and_hashes_it(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    launched = await _launch(client)
    assert launched["run_id"].startswith("run_")
    assert launched["host_token"].startswith("host_")
    assert launched["prereg_stale"] is True  # no prereg hash was presented
    view = (await client.get(f"{API}/runs/{launched['run_id']}")).json()
    run = view["run"]
    assert run["state"] == "created" and run["key_name"] == "alice"
    assert host_token_matches(launched["host_token"], run["host_token_hash"])
    assert launched["host_token"] not in json.dumps(view)
    assert run["prereg_hash"] is not None and run["prereg_stale"] is True
    # A matching prereg hash clears the flag (same config ⇒ same frozen study).
    await client.post(
        f"{API}/runs/{launched['run_id']}/control",
        json={"action": "abort"},
        headers={"X-Host-Token": launched["host_token"]},
    )
    again = await _launch(client, prereg_hash=run["prereg_hash"])
    assert again["prereg_stale"] is False


async def test_launch_quota_is_429_with_retry_after(client: httpx.AsyncClient) -> None:
    await _launch(client)
    resp = await client.post(
        f"{API}/runs", json={"config": _study_dict(), "kind": "pilot"}, headers=_auth()
    )
    err = _assert_envelope(resp, 429, "rate_limit_error")
    assert resp.headers["Retry-After"] == "60"
    assert "active run" in err["message"]


async def test_launch_refuses_unknown_cards(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"{API}/runs",
        json={"config": _study_dict(cards=[{"card_id": "ghost"}]), "kind": "pilot"},
        headers=_auth(),
    )
    err = _assert_envelope(resp, 400, "invalid_request_error")
    assert err["param"] == "config.cards"


async def test_key_verify_unavailable_is_503(settings: Settings, keys_file: Path) -> None:
    keys_file.unlink()
    h = Harness(settings)
    async with h.app.router.lifespan_context(h.app):
        transport = httpx.ASGITransport(app=h.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                f"{API}/runs", json={"config": _study_dict(), "kind": "pilot"}, headers=_auth()
            )
            _assert_envelope(resp, 503)
            assert "Retry-After" in resp.headers


# ── host token matrix ─────────────────────────────────────────────────────────


async def test_host_actions_need_the_host_token(client: httpx.AsyncClient) -> None:
    launched = await _launch(client)
    url = f"{API}/runs/{launched['run_id']}/control"
    _assert_envelope(await client.post(url, json={"action": "start"}), 401)
    _assert_envelope(
        await client.post(url, json={"action": "start"}, headers={"X-Host-Token": "host_wrong"}),
        403,
        "permission_error",
    )
    keys_url = f"{API}/runs/{launched['run_id']}/keys"
    _assert_envelope(await client.post(keys_url, json={"provider_keys": {"anthropic": "x"}}), 401)
    _assert_envelope(
        await client.post(
            keys_url,
            json={"provider_keys": {"anthropic": "x"}},
            headers={"X-Host-Token": new_host_token()},
        ),
        403,
    )
    _assert_envelope(
        await client.post(
            f"{API}/runs/run_missing/control",
            json={"action": "start"},
            headers={"X-Host-Token": launched["host_token"]},
        ),
        404,
    )


async def test_control_invalid_transitions_are_409(client: httpx.AsyncClient) -> None:
    launched = await _launch(client)
    url = f"{API}/runs/{launched['run_id']}/control"
    host = {"X-Host-Token": launched["host_token"]}
    _assert_envelope(await client.post(url, json={"action": "pause"}, headers=host), 409)
    _assert_envelope(await client.post(url, json={"action": "jump"}, headers=host), 400)
    aborted = await client.post(url, json={"action": "abort"}, headers=host)
    assert aborted.status_code == 200 and aborted.json()["state"] == "aborted"
    _assert_envelope(await client.post(url, json={"action": "start"}, headers=host), 409)
    view = (await client.get(f"{API}/runs/{launched['run_id']}")).json()
    assert view["run"]["state"] == "aborted" and view["live"] is False


# ── secrets ───────────────────────────────────────────────────────────────────


async def test_provider_keys_never_leave_the_process(
    client: httpx.AsyncClient, harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_registry_for_tests()
    # A filter on a Logger only sees that logger's own records; the production
    # setup puts SecretFilter on the root *handler*, so do the same on caplog's.
    flt = SecretFilter()
    caplog.handler.addFilter(flt)
    caplog.set_level(logging.DEBUG)
    try:
        launched = await _launch(client, keys={"anthropic": ANTHROPIC_SECRET})
        run_id = launched["run_id"]
        view = (await client.get(f"{API}/runs/{run_id}")).json()
        assert view["run"]["keys_present"]["anthropic"] is True
        assert view["run"]["keys_present"]["openai"] is False
        text = json.dumps(view)
        assert ANTHROPIC_SECRET not in text
        run_json = harness.store.find_run(run_id)
        assert run_json is not None
        on_disk = run_json.run_dir.run_json.read_text(encoding="utf-8")
        assert ANTHROPIC_SECRET not in on_disk
        assert "keys_present" in on_disk
        # Force a log line that mentions the bag and the raw value.
        live = harness.state.registry.get(run_id)
        assert live is not None
        logging.getLogger("qr_llm_evals.api").info("bag %r key %s", live.secrets, ANTHROPIC_SECRET)
        # Error path: an ApiError message carrying the secret is still redacted in logs.
        logging.getLogger("qr_llm_evals.api").error("boom", exc_info=RuntimeError(ANTHROPIC_SECRET))
    finally:
        caplog.handler.removeFilter(flt)
    joined = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert ANTHROPIC_SECRET not in joined
    assert REDACTED in joined


async def test_unhandled_error_never_echoes_the_exception(settings: Settings) -> None:
    h = Harness(settings)

    def boom(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"upstream said {ANTHROPIC_SECRET}")

    h.state.engine_transport = httpx.MockTransport(boom)
    async with h.app.router.lifespan_context(h.app):
        transport = httpx.ASGITransport(app=h.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(f"{API}/health")
    err = _assert_envelope(resp, 500, "server_error")
    assert ANTHROPIC_SECRET not in resp.text
    assert err["message"] == "internal error; see the server log"


# ── run lifecycle + SSE ───────────────────────────────────────────────────────


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in text.split("\n\n"):
        if not frame.strip() or frame.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_start_runs_to_completion_and_streams_events(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    launched = await _launch(client, keys={"anthropic": ANTHROPIC_SECRET})
    run_id, host = launched["run_id"], {"X-Host-Token": launched["host_token"]}
    # Nothing runs until the host says so (invariant 6).
    await asyncio.sleep(0.1)
    assert harness.engine.requests == []
    assert (await client.get(f"{API}/runs/{run_id}")).json()["run"]["state"] == "created"

    started = await client.post(
        f"{API}/runs/{run_id}/control", json={"action": "start"}, headers=host
    )
    assert started.status_code == 200, started.text
    resp = await client.get(f"{API}/runs/{run_id}/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    types = [t for t, _ in events]
    assert types[0] == "run.state"
    assert "sample.started" in types and "sample.finished" in types and "sample.delta" in types
    assert types[-1] == "run.state" and events[-1][1]["state"] == "complete"
    finished = [d for t, d in events if t == "sample.finished"]
    assert len(finished) == 2 * 2 * 2 * 3  # 2 arms × 2 items × k 2 × 3 told cells
    for d in finished:
        prov = d["provenance"]
        assert prov["quantum"] is (prov["source_label"] == "quantum")
    assert ANTHROPIC_SECRET not in resp.text

    view = await _wait_state(client, run_id, {"complete"})
    assert view["live"] is False
    assert len(harness.engine.requests) == len(finished)
    # A late viewer still gets a header frame and a clean end of stream.
    late = await client.get(f"{API}/runs/{run_id}/events")
    late_events = _parse_sse(late.text)
    assert late_events and late_events[-1][0] == "run.state"
    assert late_events[-1][1]["state"] == "complete"


async def test_missing_judge_key_parks_run_in_needs_keys_then_resumes(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    launched = await _launch(client)  # no anthropic key
    run_id, host = launched["run_id"], {"X-Host-Token": launched["host_token"]}
    await client.post(f"{API}/runs/{run_id}/control", json={"action": "start"}, headers=host)
    view = await _wait_state(client, run_id, {"needs_keys"})
    assert view["run"]["needs_keys"] is True
    generated = len(harness.engine.requests)
    assert generated == 24
    # resume without keys is refused with a plain message
    err = _assert_envelope(
        await client.post(f"{API}/runs/{run_id}/control", json={"action": "resume"}, headers=host),
        409,
    )
    assert "keys" in err["message"]
    supplied = await client.post(
        f"{API}/runs/{run_id}/keys",
        json={"provider_keys": {"anthropic": ANTHROPIC_SECRET}},
        headers=host,
    )
    assert supplied.status_code == 200, supplied.text
    assert supplied.json() == {
        "run_id": run_id,
        "keys_present": {"openrouter": False, "openai": False, "anthropic": True, "custom": False},
        "state": "paused",
        "needs_keys": False,
    }
    resumed = await client.post(
        f"{API}/runs/{run_id}/control", json={"action": "resume"}, headers=host
    )
    assert resumed.status_code == 200, resumed.text
    await _wait_state(client, run_id, {"complete"})
    # Resume regenerated nothing: the judge phase used the existing rows.
    assert len(harness.engine.requests) == generated


async def test_pause_and_resume_via_control(client: httpx.AsyncClient, settings: Settings) -> None:
    h = Harness(settings, engine=StubEngine(chunk_delay_s=0.01))
    async with h.app.router.lifespan_context(h.app):
        transport = httpx.ASGITransport(app=h.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            launched = await _launch(c, keys={"anthropic": ANTHROPIC_SECRET})
            run_id, host = launched["run_id"], {"X-Host-Token": launched["host_token"]}
            await c.post(f"{API}/runs/{run_id}/control", json={"action": "start"}, headers=host)
            await _wait_state(c, run_id, {"running"})
            paused = await c.post(
                f"{API}/runs/{run_id}/control", json={"action": "pause"}, headers=host
            )
            assert paused.status_code == 200
            view = await _wait_state(c, run_id, {"paused", "complete"})
            if view["run"]["state"] == "paused":
                assert view["control_state"] == "paused"
                resumed = await c.post(
                    f"{API}/runs/{run_id}/control", json={"action": "resume"}, headers=host
                )
                assert resumed.status_code == 200
            await _wait_state(c, run_id, {"complete"})


async def test_restart_orphans_runs_into_needs_keys(settings: Settings) -> None:
    """D-14: a new process over the same data dir cannot know the old bags."""
    first = Harness(settings)
    async with first.app.router.lifespan_context(first.app):
        transport = httpx.ASGITransport(app=first.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            launched = await _launch(c, keys={"anthropic": ANTHROPIC_SECRET})
    first.store.close()
    run_id, host = launched["run_id"], {"X-Host-Token": launched["host_token"]}

    second = Harness(settings)
    async with second.app.router.lifespan_context(second.app):
        transport = httpx.ASGITransport(app=second.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            view = (await c.get(f"{API}/runs/{run_id}")).json()
            assert view["run"]["state"] == "needs_keys" and view["run"]["needs_keys"] is True
            assert view["live"] is False
            _assert_envelope(
                await c.post(
                    f"{API}/runs/{run_id}/control", json={"action": "resume"}, headers=host
                ),
                409,
            )
            # The host token from the first process still works (it is on disk as a hash).
            ok = await c.post(
                f"{API}/runs/{run_id}/keys",
                json={"provider_keys": {"anthropic": ANTHROPIC_SECRET}},
                headers=host,
            )
            assert ok.status_code == 200 and ok.json()["state"] == "paused"
            resumed = await c.post(
                f"{API}/runs/{run_id}/control", json={"action": "resume"}, headers=host
            )
            assert resumed.status_code == 200, resumed.text
            await _wait_state(c, run_id, {"complete"})


def test_mark_orphans_only_touches_unfinished_runs() -> None:
    def run(run_id: str, state: str) -> Run:
        return Run(
            run_id=run_id,
            study_id="s",
            kind="pilot",
            state=state,  # type: ignore[arg-type]
            created_at=NOW,
            key_name="alice",
            host_token_hash="a" * 64,
            config_hash="b" * 64,
        )

    written: list[Run] = []
    flipped = mark_orphans(
        [run("a", "created"), run("b", "complete"), run("c", "running"), run("d", "aborted")],
        written.append,
    )
    assert flipped == ["a", "c"]
    assert all(r.state == "needs_keys" and r.needs_keys for r in written)


# ── readiness route ───────────────────────────────────────────────────────────


async def test_readiness_route_runs_the_check_and_persists_on_a_run(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    launched = await _launch(client)
    resp = await client.post(
        f"{API}/readiness",
        json={"config": _study_dict(), "run_id": launched["run_id"]},
        headers=_auth(),
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert set(report["arms"]) == {"qrng", "prng_seed_7"}
    assert report["equivalence_ok"] is True
    view = (await client.get(f"{API}/runs/{launched['run_id']}")).json()
    assert view["readiness"]["at"] == report["at"]
    assert view["run"]["determinism_tier"] == report["determinism_tier"]


async def test_readiness_equivalence_refusal_is_409(client: httpx.AsyncClient) -> None:
    config = _study_dict(
        arms=[
            {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"},
            {
                "id": "t1",
                "model": "qr-llm",
                "sampling": "t1_minp",
                "source": "prng_seeded",
                "study_seed": 7,
            },
        ]
    )
    resp = await client.post(f"{API}/readiness", json={"config": config}, headers=_auth())
    err = _assert_envelope(resp, 409, "readiness_error")
    assert "refusing" in err["message"]


# ── SSE unit behaviour ────────────────────────────────────────────────────────


def test_merge_deltas_coalesces_same_sample_only() -> None:
    evs = [
        RunEvent("sample.delta", {"sample_ref": "a", "text": "he"}, 1),
        RunEvent("sample.delta", {"sample_ref": "a", "text": "llo", "thinking": "t"}, 2),
        RunEvent("sample.delta", {"sample_ref": "b", "text": "x"}, 3),
        RunEvent("sample.delta", {"sample_ref": "a", "text": "!"}, 4),
    ]
    merged = merge_deltas(evs)
    assert [e.data for e in merged] == [
        {"sample_ref": "a", "text": "hello", "thinking": "t"},
        {"sample_ref": "b", "text": "x"},
        {"sample_ref": "a", "text": "!"},
    ]
    assert merged[0].seq == 2


async def test_event_frames_keepalive_resync_and_close() -> None:
    bus = RunEventBus(maxsize=3)
    sub = bus.subscribe()
    frames: list[str] = []

    async def consume() -> None:
        async for frame in event_frames(sub, keepalive_s=0.05, poll_s=0.005):
            frames.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.12)  # silence ⇒ keep-alive(s)
    for i in range(5):  # overflow a 3-deep queue ⇒ resync
        bus.emit(RunEvent("run.progress", {"i": i}))
    await asyncio.sleep(0.05)
    bus.close()
    await asyncio.wait_for(task, 2.0)
    assert KEEPALIVE_FRAME in frames
    joined = "".join(frames)
    assert "event: resync" in joined
    assert joined.index("event: resync") < joined.index("event: run.progress")


# ── preflight ─────────────────────────────────────────────────────────────────


def test_preflight_checks(settings: Settings, tmp_path: Path, keys_file: Path) -> None:
    checks = {c.name: c for c in preflight.run_checks(settings, engine=False)}
    assert checks["data_dir"].ok and checks["keys"].ok and checks["registry"].ok
    assert "1 entry" in checks["keys"].detail
    keys_file.write_text("[]", encoding="utf-8")
    bad = {c.name: c for c in preflight.run_checks(settings, engine=False)}
    assert not bad["keys"].ok and bad["keys"].fatal
    unreachable = load_settings({"QR_EVALS_VLLM_ORIGIN": "http://127.0.0.1:9"})
    engine = preflight.check_engine(unreachable)
    assert not engine.ok and not engine.fatal


def test_preflight_main_exit_codes(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[preflight.Check] = [
        preflight.Check("data_dir", True, "ok"),
        preflight.Check("engine", False, "down", fatal=False),
    ]
    monkeypatch.setattr(preflight, "load_settings", lambda: settings)
    monkeypatch.setattr(preflight, "run_checks", lambda s: iter(calls))
    assert preflight.main([]) == preflight.EXIT_WARN
    calls.append(preflight.Check("keys", False, "unreadable"))
    assert preflight.main(["--quiet"]) == preflight.EXIT_REFUSED
    out = capsys.readouterr().err
    assert "[FAIL] keys: unreadable" in out and "REFUSED" in out


def test_entrypoint_refuses_bad_boot(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from qr_llm_evals import entrypoint

    monkeypatch.setattr(entrypoint, "load_settings", lambda: settings)
    monkeypatch.setattr(
        entrypoint,
        "run_checks",
        lambda s: iter([preflight.Check("keys", False, "unreadable")]),
    )
    with pytest.raises(SystemExit) as exc:
        entrypoint.build_asgi_app()
    assert exc.value.code == entrypoint.EXIT_REFUSED
    monkeypatch.setattr(entrypoint, "run_checks", lambda s: iter([]))
    app = entrypoint.build_asgi_app()
    assert app.state.qr.settings is settings


# ── headless equivalence through the API path ────────────────────────────────


async def test_viewed_run_matches_headless_artifacts(
    client: httpx.AsyncClient, harness: Harness, tmp_path: Path
) -> None:
    launched = await _launch(client, keys={"anthropic": ANTHROPIC_SECRET})
    run_id, host = launched["run_id"], {"X-Host-Token": launched["host_token"]}
    await client.post(f"{API}/runs/{run_id}/control", json={"action": "start"}, headers=host)
    await client.get(f"{API}/runs/{run_id}/events")  # a subscribed viewer
    await _wait_state(client, run_id, {"complete"})
    viewed = harness.store.find_run(run_id)
    assert viewed is not None

    other = Store(tmp_path / "headless")
    study = StudyConfig.model_validate(_study_dict())
    sd = other.freeze_study(study, harness.state.prereg_renderer(study))
    rs = other.create_run(viewed.read_run().model_copy(update={"state": "created"}))
    ctl = RunControl()
    ctl.start()
    Orchestrator(
        study,
        load_cards(FIXTURES / "evals"),
        rs,
        RunnerConfig(vllm_origin="http://stub", max_inflight=2, repo_root=FIXTURES),
        secrets=SecretBag({"anthropic": ANTHROPIC_SECRET}),
        sink=NullEventSink(),
        control=ctl,
        transport=StubEngine().transport,
        judge_transport=StubJudge().transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    ).run()
    assert sd.study_id == viewed.study_id
    for arm in ("qrng", "prng_seed_7"):
        a = viewed.arm_card(arm, "stub_free").generations_jsonl.read_bytes()
        b = rs.arm_card(arm, "stub_free").generations_jsonl.read_bytes()
        assert a == b
