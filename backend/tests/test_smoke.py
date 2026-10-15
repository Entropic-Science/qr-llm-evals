"""Offline regressions for the acceptance oracle and the disabled-key fix."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest
import zstandard
from scripts import smoke

from qr_llm_evals.api import create_app
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys.verify import FileKeyVerifier, KeyRefused, ProbeKeyVerifier, sha256_hex
from qr_llm_evals.readiness import fetch_entropy_health
from qr_llm_evals.runner.orchestrator import EntropyProbe


@pytest.mark.parametrize("degraded", [False, True])
def test_deployed_entropy_health_shape_is_read_honestly(degraded: bool) -> None:
    body = {
        "rpc_ok": True,
        "fallback_count": 33,
        "last_source_used": "quantum_grpc",
        "source_type": "legacy_source",
        "currently_degraded": False,
        "sampler": {"currently_degraded": degraded},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    readiness = fetch_entropy_health("http://engine", transport=transport)
    sample = EntropyProbe("http://engine", transport=transport).probe()
    assert sample is not None
    for health in (readiness, sample):
        assert health.source_type == "quantum_grpc"
        assert health.currently_degraded is degraded
        assert health.fallback_count == 33


@pytest.mark.parametrize("mode", ["file", "probe"])
async def test_disabled_launch_key_preserves_403(mode: str, tmp_path: Path) -> None:
    key = "qrk_disabled_test_only"
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps([{"name": "disabled", "key_sha256": sha256_hex(key), "disabled": True}]),
        encoding="utf-8",
    )
    verifier = (
        FileKeyVerifier(path)
        if mode == "file"
        else ProbeKeyVerifier(
            "http://edge",
            async_transport=httpx.MockTransport(lambda request: httpx.Response(403)),
        )
    )
    settings = load_settings(
        {"QR_EVALS_DATA_DIR": str(tmp_path / "data"), "QR_EVALS_KEYS_FILE": str(path)}
    )
    app = create_app(settings, verifier=verifier)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/runs",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "config": smoke.study_config(("creative_diversity",)),
                "kind": "pilot",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["type"] == "permission_error"
        assert "www-authenticate" not in response.headers
        assert key not in response.text
        assert app.state.qr.store.list_runs() == []


@pytest.mark.parametrize("status", [401, 403])
def test_probe_refusal_retains_edge_status(status: int) -> None:
    with pytest.raises(KeyRefused) as error:
        ProbeKeyVerifier._interpret(status, "qrk_test_only")
    assert error.value.status_code == status


@pytest.mark.parametrize(
    "name,kind", [("../escaped", "file"), ("/absolute", "file"), ("link", "link")]
)
def test_bundle_extraction_rejects_unsafe_members(tmp_path: Path, name: str, kind: str) -> None:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        member = tarfile.TarInfo(name)
        if kind == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "outside"
        archive.addfile(member)
    compressed = zstandard.ZstdCompressor().compress(payload.getvalue())
    with pytest.raises(AssertionError, match="unsafe|link/special"):
        smoke.unpack_bundle(compressed, tmp_path / "extracted")


class Frames(httpx.AsyncByteStream):
    def __init__(self, clock: list[float], *, active: bool = False, interval: float = 10) -> None:
        self.clock, self.active, self.interval = clock, active, interval

    async def __aiter__(self):
        for _ in range(6):
            self.clock[0] += self.interval
            yield b"event: sample.delta\n\n" if self.active else b": keep-alive\n\n"


@pytest.mark.parametrize(
    "active,interval,passes",
    [(False, 10, True), (True, 10, False), (False, 30, False), (False, 1, False)],
)
async def test_live_sse_checks_elapsed_time_heartbeat_spacing_and_idle_state(
    monkeypatch: pytest.MonkeyPatch,
    active: bool,
    interval: float,
    passes: bool,
) -> None:
    from types import SimpleNamespace

    now = [0.0]
    # Patch the smoke module's clock, not asyncio's process-wide time module.
    monkeypatch.setattr(smoke, "time", SimpleNamespace(monotonic=lambda: now[0]))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-accel-buffering": "no"},
            stream=Frames(now, active=active, interval=interval),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        if passes:
            assert await smoke.check_idle_sse(client, "http://test/events") == 6
        else:
            with pytest.raises(AssertionError):
                await smoke.check_idle_sse(client, "http://test/events")


def test_smoke_is_a_mandatory_gate_row() -> None:
    from scripts.check import BY_NAME

    assert BY_NAME["smoke"].skip_unless() is None


def test_live_url_accepts_origin_or_api_prefix() -> None:
    assert smoke.api_base("https://example.invalid/") == "https://example.invalid/api"
    assert smoke.api_base("https://example.invalid/api/") == "https://example.invalid/api"
