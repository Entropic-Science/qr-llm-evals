#!/usr/bin/env python
"""Offline end-to-end acceptance oracle; optional deployed HTTP/SSE check.

Default: production app/factory, temporary synthetic data, five Study 1 arms,
two thin fixture cards, observer session, analysis and offline bundle replay.
Only engine/judge transports and test cards are substituted. Never touches
paid-for results. A failed scientific integration is a failure, not a skip.

Live: --live https://evals-api.entropic.science [--run-id ID]. An existing run
must remain idle for 60 seconds. Without --run-id, prompt for a launch key
(or read --launch-key-file), create an idle pilot and abort it in finally;
no generation is started and the record is retained on the server.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
import zstandard

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from qr_llm_evals.api import create_app  # noqa: E402
from qr_llm_evals.api.app import default_orchestrator_factory  # noqa: E402
from qr_llm_evals.api.ratelimit import TokenBucketLimiter  # noqa: E402
from qr_llm_evals.cards import load_cards  # noqa: E402
from qr_llm_evals.config import load_settings  # noqa: E402
from qr_llm_evals.models.schema import StudyConfig  # noqa: E402
from qr_llm_evals.store import Store  # noqa: E402
from stub_engine import StubEngine, StubJudge  # noqa: E402


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def checked(response: httpx.Response, expected: int = 200) -> dict[str, Any]:
    # Never include request headers or credential-bearing response bodies.
    require(
        response.status_code == expected,
        f"{response.request.method} {response.request.url.path}: "
        f"expected {expected}, got {response.status_code}",
    )
    return response.json()


def study_config(card_ids: tuple[str, ...]) -> dict[str, Any]:
    config = yaml.safe_load((ROOT / "studies" / "study1.yaml").read_text(encoding="utf-8"))
    config["arms"] = [arm for arm in config["arms"] if arm.get("availability") != "unavailable"]
    require(len(config["arms"]) == 5, "Study 1 must have five available baseline arms")
    config["title"] = "Synthetic smoke pilot — not research data"
    config["contact"] = "smoke@example.invalid"
    config["cards"] = [{"card_id": card_id, "overrides": {"items": 2}} for card_id in card_ids]
    config["pilot"] = {"enabled": True, "prompts": 2}
    return StudyConfig.model_validate(config).model_dump(mode="json")


async def wait_run(
    client: httpx.AsyncClient, run_id: str, *, timeout: float = 60
) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            view = checked(await client.get(f"/api/runs/{run_id}"))
            state = view["run"]["state"]
            require(state not in {"failed", "aborted", "needs_keys"}, f"run stopped: {state}")
            if state == "complete" and not view["live"]:
                return view
            await asyncio.sleep(0.05)


def unpack_bundle(payload: bytes, destination: Path) -> None:
    """Accept only ordinary files/directories inside the fresh extraction root."""
    root = destination.resolve()
    with (
        zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)) as reader,
        tarfile.open(fileobj=reader, mode="r|") as archive,
    ):
        for member in archive:
            path = (root / member.name).resolve()
            require(path.is_relative_to(root), "unsafe bundle path")
            require(member.isdir() or member.isfile(), "bundle contains a link/special file")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                stream = archive.extractfile(member)
                require(stream is not None, "bundle file has no data")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(stream.read())


def table_bytes(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


async def offline_smoke(temporary: Path) -> None:
    key = "qrk_synthetic_smoke_only"
    disabled = "qrk_synthetic_disabled_only"
    keys_path = temporary / "keys.json"
    keys_path.write_text(
        json.dumps(
            [
                {"name": "smoke", "key_sha256": hashlib.sha256(key.encode()).hexdigest()},
                {
                    "name": "disabled",
                    "key_sha256": hashlib.sha256(disabled.encode()).hexdigest(),
                    "disabled": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(
        {
            "QR_EVALS_ALLOW_KEY_LAUNCH": "1",
            "QR_EVALS_DATA_DIR": str(temporary / "data"),
            "QR_EVALS_KEYS_FILE": str(keys_path),
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
            "QR_EVALS_MAX_INFLIGHT": "1",
        }
    )
    original = load_cards(ROOT / "backend/tests/fixtures/registry/evals")["stub_free"]
    cards = {}
    for card_id, thinking in (("smoke_free", False), ("smoke_thinking", True)):
        payload = original.model_dump()
        payload.update(
            id=card_id,
            thinking=thinking,
            told_actual=False,
            task_module="backend/tests/fixtures/registry/evals/stub_free/task.py",
            judge={"kind": "none"},
            est_gen_tokens_per_arm=80,
            endpoints={
                "primary": ["distinct_ratio"],
                "secondary": ["n_words"],
                "requires_tier0": "none",
            },
        )
        cards[card_id] = type(original).model_validate(payload)
    thin_ids = tuple(cards)
    # Passive primary consumes the thin generations' Tier 0 records. With the
    # stub's API-logprob-only path its unavailable result still needs a figure.
    cards["tier0_pit_residuals"] = load_cards()["tier0_pit_residuals"].model_copy(
        update={"selectable": True}
    )
    config = study_config(tuple(cards))
    config["cards"][-1]["overrides"] = {}
    engine = StubEngine(chunk_delay_s=0.01)
    store = Store(settings.data_dir)

    def narrative(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Synthetic smoke data; no scientific conclusion.",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    app = create_app(
        settings,
        store=store,
        cards=cards,
        repo_root=ROOT,
        orchestrator_factory=default_orchestrator_factory(
            settings,
            store,
            cards,
            repo_root=ROOT,
            transport=engine.transport,
            judge_transport=StubJudge().transport,
        ),
        engine_transport=engine.transport,
        readiness_transport=engine.transport,
        analysis_transport=httpx.MockTransport(narrative),
        limiter=TokenBucketLimiter(rate_per_s=1000, burst=1000),
    )
    failures: list[str] = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://smoke"
        ) as client,
    ):
        checked(await client.get("/api/health"))
        require(
            len(checked(await client.get("/api/cards"))["cards"]) == 3,
            "two thin smoke cards and passive primary missing",
        )
        auth = {"Authorization": f"Bearer {key}"}
        for bearer, status in (("qrk_unknown_smoke", 401), (disabled, 403)):
            checked(
                await client.post(
                    "/api/runs",
                    json={"config": config, "kind": "pilot"},
                    headers={"Authorization": f"Bearer {bearer}"},
                ),
                status,
            )
        readiness = checked(
            await client.post("/api/readiness", json={"config": config}, headers=auth)
        )
        require(readiness["equivalence_ok"], "sampler equivalence failed")
        require(readiness["entropy"]["rpc_ok"], "entropy readiness failed")
        require(all(arm["available"] for arm in readiness["arms"].values()), "arm unavailable")
        require(len(readiness["arms"]) == 5, "not all five arms checked")
        require(
            readiness["tier0_source"] == "api_logprobs", "stub must not claim engine token logs"
        )
        print(
            "PASS health, cards, key gate 201/401/403 pending launch, five-arm readiness",
            flush=True,
        )

        prereg = checked(
            await client.post("/api/prereg/render", json={"config": config, "readiness": readiness})
        )
        launched = checked(
            await client.post(
                "/api/runs",
                headers=auth,
                json={
                    "config": config,
                    "kind": "pilot",
                    "readiness": readiness,
                    "prereg_hash": prereg["prereg_hash"],
                },
            ),
            201,
        )
        run_id, study_id = launched["run_id"], launched["study_id"]
        host = {"X-Host-Token": launched["host_token"]}
        require(not launched["prereg_stale"], "fresh prereg marked stale")
        # Readiness persistence is exercised through the real route.
        checked(
            await client.post(
                "/api/readiness", headers=auth, json={"config": config, "run_id": run_id}
            )
        )
        session = checked(
            await client.post(
                f"/api/runs/{run_id}/sessions",
                headers=host,
                json={
                    "display": "single",
                    "intention": "Observe clearly; synthetic smoke only.",
                    "host_observer_id": "smoke_observer",
                    "host_display_name": "Synthetic observer",
                },
            ),
            201,
        )
        session_id = session["session"]["session_id"]
        session_base = f"/api/sessions/{session_id}"
        require(session["state"] == "ready", "session auto-started")
        checked(
            await client.post(session_base + "/control", headers=host, json={"action": "start"}),
            409,
        )
        checked(await client.post(session_base + "/control", json={"action": "start"}), 401)
        observer = {"X-Observer-Id": "smoke_observer"}
        checked(
            await client.post(
                session_base + "/questionnaire",
                headers=observer,
                json={
                    "phase": "pre",
                    "answers": {"influence_belief": 4, "calmness": 3, "focus": 3},
                },
            )
        )
        before = len(engine.requests)
        await asyncio.sleep(0.05)
        require(len(engine.requests) == before, "generation started without host control")
        checked(
            await client.post(f"/api/runs/{run_id}/control", headers=host, json={"action": "start"})
        )
        supervised_arm = next(a["id"] for a in config["arms"] if a["observer_mode"] == "supervised")

        def supervised_refs() -> list[str]:
            return [ref for ref in engine.request_ids if ref.startswith(supervised_arm + "/")]

        await asyncio.sleep(0.1)
        require(not supervised_refs(), "supervised dispatch bypassed session start")
        checked(
            await client.post(session_base + "/control", headers=host, json={"action": "start"})
        )
        async with asyncio.timeout(10):
            while not supervised_refs():
                await asyncio.sleep(0.005)
        checked(
            await client.post(
                session_base + "/control", headers=host, json={"action": "stop_at_boundary"}
            )
        )
        first_controller = app.state.qr.sessions.get(session_id)
        require(first_controller is not None, "first sitting disappeared")
        async with asyncio.timeout(10):
            while first_controller.state != "break":
                await asyncio.sleep(0.01)
        first_refs = set(supervised_refs())
        first_id = session_id
        require(0 < len(first_refs) < 8, "boundary did not interrupt the supervised queue")
        first_controller.tick(now=first_controller.perf() + 10000)
        await asyncio.sleep(0.2)
        require(set(supervised_refs()) == first_refs, "generation dispatched during the break")
        next_session = checked(
            await client.post(
                f"/api/runs/{run_id}/sessions",
                headers=host,
                json={
                    "display": "single",
                    "intention": "Observe clearly; second synthetic sitting.",
                    "host_observer_id": "smoke_observer",
                    "host_display_name": "Synthetic observer",
                },
            ),
            201,
        )
        next_id = next_session["session"]["session_id"]
        next_base = f"/api/sessions/{next_id}"
        checked(
            await client.post(
                next_base + "/questionnaire",
                headers=observer,
                json={
                    "phase": "pre",
                    "answers": {"influence_belief": 4, "calmness": 3, "focus": 3},
                },
            )
        )
        checked(
            await client.post(next_base + "/control", headers=host, json={"action": "resume"}), 409
        )
        checked(
            await client.post(
                session_base + "/questionnaire",
                headers=observer,
                json={
                    "phase": "post",
                    "answers": {"meaningful_coincidence": 1, "unusual_relevance": 1, "surprise": 1},
                },
            )
        )
        await asyncio.sleep(0.1)
        require(set(supervised_refs()) == first_refs, "questionnaires auto-resumed generation")
        checked(await client.post(next_base + "/control", headers=host, json={"action": "resume"}))
        session_id, session_base = next_id, next_base
        # Keep the SSE subscription open throughout generation; ASGITransport
        # buffers it, so an outer deadline is essential for a stuck producer.
        async with asyncio.timeout(60):
            events = await client.get(f"/api/runs/{run_id}/events")
        require(
            events.status_code == 200 and "event: sample.delta" in events.text,
            "missing streamed output",
        )
        require('"thinking"' in events.text, "thinking deltas missing")
        view = await wait_run(client, run_id)
        run_store = store.run(study_id, run_id)
        for arm in config["arms"]:
            for card_id in thin_ids:
                rows = run_store.generations(arm["id"], card_id)
                require(len(rows) == 4, f"wrong sample count for {arm['id']}/{card_id}")
                require(
                    all(row.finish_reason in {"stop", "length"} for row in rows),
                    "incomplete generation",
                )
                require(
                    all(row.provenance.quantum == (arm["source"] == "qrng") for row in rows),
                    "dishonest source label",
                )
                require(
                    run_store.arm_card(arm["id"], card_id).tier0_jsonl.stat().st_size > 0,
                    "Tier 0 missing",
                )
                if arm["observer_mode"] == "supervised" and not all(
                    row.window == "supervised"
                    and row.session_id == (first_id if row.sample_ref in first_refs else session_id)
                    for row in rows
                ):
                    failures.append(
                        f"{arm['id']}/{card_id}: production runner omitted supervised window/session tags"
                    )
        require(
            len(supervised_refs()) == len(set(supervised_refs())) == 8,
            "multi-sitting resume repeated or omitted completed samples",
        )
        require(view["run"]["prereg_hash"] == prereg["prereg_hash"], "run prereg hash differs")
        require(view["run"]["config_hash"] == prereg["config_hash"], "run config hash differs")
        print(
            "PASS pilot: 40 generations, two thin cards, five arms, streamed thinking and Tier 0",
            flush=True,
        )

        # Exercise the HTTP boundary and post gate as well as checking that
        # actual production samples were attached to this observer window.
        boundary = checked(
            await client.post(
                session_base + "/control", headers=host, json={"action": "stop_at_boundary"}
            )
        )
        require(boundary["state"] == "break", "completed generations did not reach a boundary")
        controller = app.state.qr.sessions.get(session_id)
        require(controller is not None, "session disappeared")
        controller.tick(now=controller.perf() + 10000)
        require(controller.state == "break", "elapsed break auto-resumed")
        require(controller.record.windows[0].observers_present, "observer presence missing")
        checked(
            await client.post(
                session_base + "/questionnaire",
                headers=observer,
                json={
                    "phase": "post",
                    "answers": {
                        "meaningful_coincidence": 1,
                        "unusual_relevance": 1,
                        "surprise": 1,
                    },
                },
            )
        )

        analysed = checked(await client.post(f"/api/runs/{run_id}/analyse", headers=host), 202)
        await wait_run(client, run_id)
        analysis_id = analysed["analysis_id"]
        output = run_store.run_dir.analysis_dir(analysis_id)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        require(manifest["plan_hash"] == prereg["plan_hash"], "analysis diverged from prereg plan")
        require(manifest["narrative"]["published"], "smoke narrative withheld")
        originals = table_bytes(output / "tables")
        require(
            originals and list((output / "figures").glob("*.svg")),
            "analysis tables/figures missing",
        )
        public = await client.get(f"/api/studies/{study_id}/prereg.md")
        require(public.status_code == 200, "prereg download failed")
        require(
            hashlib.sha256(public.content).hexdigest() == prereg["prereg_hash"],
            "download hash differs",
        )
        bundle = await client.get(f"/api/studies/{study_id}/bundle.tar.zst")
        require(bundle.status_code == 200, "bundle download failed")
        extracted = temporary / "replay"
        unpack_bundle(bundle.content, extracted)
        roots = list(extracted.rglob("study.json"))
        require(len(roots) == 1, "bundle study root missing/ambiguous")
        bundle_root = roots[0].parent
        replay_output = bundle_root / "runs" / run_id / "analysis" / analysis_id
        # Move the archived output aside: matching pre-existing files cannot
        # masquerade as a successful offline recomputation.
        replay_output.rename(replay_output.with_name(analysis_id + "_archived"))
        replay = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "qr_llm_evals.analysis",
                "--bundle",
                str(bundle_root),
                "--run",
                run_id,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        require(replay.returncode == 0, "offline analysis CLI failed: " + replay.stderr)
        require(
            table_bytes(replay_output / "tables") == originals,
            "bundle replay tables differ byte-for-byte",
        )
        print(
            "PASS analysis, figures, narrative, prereg/hash persistence, bundle and byte-identical offline tables",
            flush=True,
        )
        print(
            json.dumps(
                {
                    "config_hash": prereg["config_hash"],
                    "prereg_hash": prereg["prereg_hash"],
                    "plan_hash": prereg["plan_hash"],
                    "tables": len(originals),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    require(not failures, "Supervised E2E integration failed: " + "; ".join(failures))


def api_base(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("/api") else base + "/api"


async def check_idle_sse(client: httpx.AsyncClient, url: str, *, duration: float = 60) -> int:
    start = time.monotonic()
    last_keepalive = start
    keepalives = 0
    async with asyncio.timeout(duration + 20):
        async with client.stream("GET", url) as response:
            require(response.status_code == 200, f"SSE returned {response.status_code}")
            require(
                response.headers.get("content-type", "").startswith("text/event-stream"), "not SSE"
            )
            require(
                response.headers.get("x-accel-buffering") == "no", "SSE buffering header missing"
            )
            async for line in response.aiter_lines():
                now = time.monotonic()
                require(now - last_keepalive <= 25, "idle SSE heartbeat gap exceeded 25 seconds")
                require(not line.startswith("event: sample."), "SSE run is not idle")
                if line.startswith(":"):
                    keepalives += 1
                    last_keepalive = now
                    if now - start >= duration:
                        require(keepalives >= 5, "too few idle keepalives")
                        return keepalives
    raise AssertionError("SSE closed before the full idle observation interval")


async def live_smoke(url: str, run_id: str | None, key_file: Path | None) -> None:
    base = api_base(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10)) as client:
        checked(await client.get(base + "/health"))
        require(checked(await client.get(base + "/cards"))["cards"], "empty deployed registry")
        print("PASS deployed health and cards", flush=True)
        created = None
        if run_id is None:
            if key_file:
                key = key_file.read_text(encoding="utf-8").strip()
            else:
                require(
                    sys.stdin.isatty(),
                    "provide --run-id for an idle run or --launch-key-file (no key on command line)",
                )
                key = getpass.getpass(
                    "Launch key (create an idle pilot, then abort; no generation): "
                )
            created = checked(
                await client.post(
                    base + "/runs",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "config": study_config(("creative_diversity",)),
                        "kind": "pilot",
                    },
                ),
                201,
            )
            run_id = created["run_id"]
        try:
            view = checked(await client.get(base + f"/runs/{run_id}"))
            require(
                view["run"]["state"] in {"created", "paused", "needs_keys"} and not view["live"],
                "choose a non-running idle run",
            )
            count = await check_idle_sse(client, base + f"/runs/{run_id}/events")
            print(f"PASS 60-second idle SSE through deployment ({count} keepalives)", flush=True)
        finally:
            if created:
                checked(
                    await client.post(
                        base + f"/runs/{run_id}/control",
                        json={"action": "abort"},
                        headers={"X-Host-Token": created["host_token"]},
                    )
                )
                print("Idle probe run aborted and retained (not deleted).", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", metavar="BASE_URL")
    parser.add_argument("--run-id", help="Existing idle run; never controlled by this script")
    parser.add_argument(
        "--launch-key-file", type=Path, help="Private raw launch-key file; never printed"
    )
    args = parser.parse_args(argv)
    if not args.live and (args.run_id or args.launch_key_file):
        parser.error("--run-id and --launch-key-file require --live")
    try:
        if args.live:
            asyncio.run(live_smoke(args.live, args.run_id, args.launch_key_file))
        else:
            with tempfile.TemporaryDirectory(prefix="qr-evals-synthetic-smoke-") as directory:
                asyncio.run(offline_smoke(Path(directory)))
    except (AssertionError, TimeoutError, httpx.HTTPError, OSError, ValueError) as error:
        print(f"FAIL smoke: {error}", file=sys.stderr, flush=True)
        return 1
    print("PASS smoke", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
