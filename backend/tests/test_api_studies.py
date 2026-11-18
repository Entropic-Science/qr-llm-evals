"""S25: background analysis, public artifacts, safe bundles, and withdrawal."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
import zstandard

from qr_llm_evals.api import create_app
from qr_llm_evals.api.deps import sha256_hex
from qr_llm_evals.cards import load_cards
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys.verify import FileKeyVerifier
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.store import Run, Store

FIXTURE = Path(__file__).parent / "fixtures" / "bundle_mini"
REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_TOKEN = "host_test_token"


def _narrative(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["model"] == "qr-llm"
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": "No numeric claims."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        },
    )


def _setup(tmp_path: Path) -> tuple[Store, Run, object]:
    settings = load_settings(
        {
            "QR_EVALS_ALLOW_KEY_LAUNCH": "1",
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(tmp_path / "keys.json"),
            "QR_EVALS_VLLM_ORIGIN": "http://narrative",
        }
    )
    Path(settings.keys_file).write_text("[]\n", encoding="utf-8")
    study = StudyConfig.model_validate(
        yaml.safe_load((REPO_ROOT / "studies" / "study1.yaml").read_text(encoding="utf-8"))
    )
    store = Store(settings.data_dir)
    study_dir = store.freeze_study(study, "# Frozen preregistration\n")
    shutil.copy2(FIXTURE / "prereg-plan.json", study_dir.path / "prereg-plan.json")
    run = Run(
        run_id="run_results",
        study_id=study_dir.study_id,
        kind="pilot",
        state="complete",
        created_at=datetime(2026, 9, 15, tzinfo=UTC),
        key_name="test-key",
        host_token_hash=sha256_hex(HOST_TOKEN),
        prereg_hash=study_dir.prereg_sha256.read_text(encoding="utf-8").split()[0],
        config_hash=config_hash(study),
        tier0_source="engine_log",
    )
    run_store = store.create_run(run)
    shutil.copytree(FIXTURE / "runs" / "run_mini" / "arms", run_store.run_dir.arms_dir)
    app = create_app(
        settings,
        store=store,
        verifier=FileKeyVerifier(settings.keys_file),
        cards=load_cards(),
        analysis_transport=httpx.MockTransport(_narrative),
    )
    return store, run, app


async def _wait_complete(client: httpx.AsyncClient, run_id: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        body = (await client.get(f"/api/runs/{run_id}")).json()
        if body["run"]["state"] == "complete":
            return
        await asyncio.sleep(0.02)
    raise AssertionError("analysis did not finish")


async def test_analysis_results_bundle_and_withdrawal(tmp_path: Path) -> None:
    store, run, app = _setup(tmp_path)
    async with app.router.lifespan_context(app):
        live = app.state.qr.live_run(run.run_id)
        progress = live.bus.subscribe()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            started = await client.post(
                f"/api/runs/{run.run_id}/analyse",
                headers={"X-Host-Token": HOST_TOKEN},
            )
            assert started.status_code == 202, started.text
            analysis_id = started.json()["analysis_id"]
            await _wait_complete(client, run.run_id)
            assert any(event.type == "analysis.progress" for event in progress.drain())

            study = await client.get(f"/api/studies/{run.study_id}")
            assert study.status_code == 200
            public_run = study.json()["runs"][0]
            assert analysis_id in study.json()["analyses"][run.run_id]
            assert "host_token_hash" not in public_run and "key_name" not in public_run

            output = store.run(run.study_id, run.run_id).run_dir.analysis_dir(analysis_id)
            table_name = next((output / "tables").glob("*.json")).stem
            figure_name = next((output / "figures").glob("*.svg")).stem
            table = await client.get(
                f"/api/studies/{run.study_id}/analysis/{analysis_id}/tables/{table_name}"
            )
            figure = await client.get(
                f"/api/studies/{run.study_id}/analysis/{analysis_id}/figures/{figure_name}.svg"
            )
            narrative = await client.get(
                f"/api/studies/{run.study_id}/analysis/{analysis_id}/narrative.md"
            )
            prereg = await client.get(f"/api/studies/{run.study_id}/prereg.md")
            assert table.status_code == figure.status_code == narrative.status_code == 200
            assert prereg.status_code == 200
            assert "LLM-written summary (model qr-llm, prompt hash" in narrative.text
            assert "immutable" in table.headers["cache-control"]

            index = await client.get(
                f"/api/studies/{run.study_id}/analysis/{analysis_id}/index",
                params={"run_id": run.run_id},
            )
            assert index.status_code == 200, index.text
            assert table_name in index.json()["tables"]
            assert figure_name in index.json()["figures"]
            assert "missing controls preclude" in index.json()["plan"]["claim_rule"]
            assert index.json()["manifest"]["narrative"]["published"] is True
            for artifact in ("plan.json", "manifest.json"):
                response = await client.get(
                    f"/api/studies/{run.study_id}/analysis/{analysis_id}/{artifact}",
                    params={"run_id": run.run_id},
                )
                assert response.status_code == 200
            licence = await client.get(f"/api/studies/{run.study_id}/LICENSE-data")
            assert licence.status_code == 200
            assert "4.0" in licence.text
            assert "LICENSE-data" in licence.headers["content-disposition"]

            # A provider metadata canary must be stripped from the streamed archive.
            (output / "provider-canary.json").write_text(
                '{"provider_headers":{"Authorization":"secret"},"kept":true}\n',
                encoding="utf-8",
            )
            bundle = await client.get(f"/api/studies/{run.study_id}/bundle.tar.zst")
            assert bundle.status_code == 200
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(bundle.content)) as reader:
                tar_bytes = reader.read()
            with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
                names = archive.getnames()
                assert any(name.endswith("/LICENSE-data") for name in names)
                canary = next(name for name in names if name.endswith("provider-canary.json"))
                extracted = archive.extractfile(canary)
                assert extracted is not None
                assert b"provider_headers" not in extracted.read()

            withdrawn = await client.post(
                f"/api/studies/{run.study_id}/withdraw",
                json={"reason": "Host requested withdrawal."},
                headers={"X-Host-Token": HOST_TOKEN},
            )
            assert withdrawn.status_code == 200, withdrawn.text
            after = store.run(run.study_id, run.run_id)
            assert after.read_run().withdrawn is True
            assert after.run_dir.withdrawn_md.is_file()
            assert after.run_dir.arms_dir.is_dir()  # data retained, never deleted


async def test_analysis_selection_and_actual_provenance(tmp_path: Path) -> None:
    store, run, app = _setup(tmp_path)
    directory = store.run(run.study_id, run.run_id).run_dir
    output = directory.analysis_dir("a_same_plan")
    (output / "tables").mkdir(parents=True)
    (output / "plan.json").write_text('{"claim_rule":"Frozen rule"}', encoding="utf-8")
    (output / "manifest.json").write_text('{"plan_hash":"hash"}', encoding="utf-8")
    (output / "tables" / "endpoint.json").write_text('{"run":"first"}', encoding="utf-8")
    second = run.model_copy(update={"run_id": "run_second", "kind": "full"})
    second_dir = store.create_run(second).run_dir.analysis_dir("a_same_plan")
    shutil.copytree(output, second_dir)
    (second_dir / "tables" / "endpoint.json").write_text('{"run":"second"}', encoding="utf-8")
    samples = directory.arm_card("qrng_supervised", "unpredictabench").generations_jsonl
    samples.write_text(
        "\n".join(
            json.dumps({"finish_reason": "stop", "provenance": provenance})
            for provenance in (
                {"source_label": "quantum", "quantum": True},
                {"source_label": "quantum", "quantum": True, "engine_fallback_delta": 1},
                {"source_label": "seeded_prng", "quantum": True},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = f"/api/studies/{run.study_id}/analysis/a_same_plan"
        assert (await client.get(f"{base}/index")).status_code == 404
        for record, expected in ((run, "first"), (second, "second")):
            response = await client.get(f"{base}/tables/endpoint", params={"run_id": record.run_id})
            assert response.json() == {"run": expected}
        for invalid in ("../run_second", "run_absent"):
            response = await client.get(f"{base}/index", params={"run_id": invalid})
            assert response.status_code == 404
        index = (await client.get(f"{base}/index", params={"run_id": run.run_id})).json()
        rows = {
            row["source_label"]: row
            for row in index["provenance"]
            if row["arm_id"] == "qrng_supervised"
        }
        assert rows["quantum"]["samples"] == 1
        assert rows["quantum"]["quantum"] is True
        assert rows["unverified"]["tainted_samples"] == 1
        assert rows["unverified"]["quantum"] is False
        assert rows["seeded_prng"]["quantum"] is False
