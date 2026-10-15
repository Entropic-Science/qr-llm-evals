"""Public study results/downloads and host-authorised withdrawal."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse

from qr_llm_evals.api.deps import HOST_TOKEN_HEADER, State, host_token_matches
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.schema import StudyView, WithdrawRequest, WithdrawResponse
from qr_llm_evals.runner.events import RunEvent
from qr_llm_evals.store.artifacts import iter_jsonl, read_json
from qr_llm_evals.store.paths import StudyDir, safe_segment
from qr_llm_evals.store.schema import Run

router = APIRouter(tags=["studies"])

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "public, max-age=0, must-revalidate"


def _safe(value: str, what: str) -> str:
    try:
        return safe_segment(value, what=what)
    except ValueError as err:
        raise ApiError(404, str(err)) from err


def _study(state: State, study_id: str) -> StudyDir:
    try:
        return state.store.study(_safe(study_id, "study_id"))
    except FileNotFoundError as err:
        raise ApiError(404, f"study {study_id!r} not found", param="study_id") from err


def _file(
    path: Path,
    media_type: str,
    *,
    immutable: bool = True,
    filename: str | None = None,
) -> FileResponse:
    if not path.is_file():
        raise ApiError(404, f"result artifact {path.name!r} not found")
    headers = {"Cache-Control": IMMUTABLE_CACHE if immutable else REVALIDATE_CACHE}
    return FileResponse(path, media_type=media_type, headers=headers, filename=filename)


def _public_run(run: Run) -> dict[str, object]:
    data = run.model_dump(mode="json")
    for field in ("host_token_hash", "key_name", "keys_present"):
        data.pop(field, None)
    return data


def _analysis(study_dir: StudyDir, analysis_id: str, run_id: str | None = None) -> Path:
    aid = _safe(analysis_id, "analysis_id")
    run_ids = [_safe(run_id, "run_id")] if run_id is not None else study_dir.run_ids()
    matches = [study_dir.run(rid).analysis_dir(aid) for rid in run_ids]
    existing = [path for path in matches if path.is_dir()]
    if len(existing) != 1:
        raise ApiError(404, f"analysis {analysis_id!r} not found uniquely")
    return existing[0]


def _provenance(run_dir: Path) -> list[dict[str, object]]:
    """Expose counts and source labels only, never prompts or provider metadata."""
    counts: dict[tuple[str, str], list[int]] = {}
    labels = {"quantum", "seeded_prng", "system_prng", "seeded_prng_biased", "provider_prng"}
    for path in sorted((run_dir / "arms").glob("*/cards/*/generations.jsonl")):
        arm_id = path.parents[2].name
        for row in iter_jsonl(path):
            provenance = row.get("provenance") or {}
            label = provenance.get("source_label", "unavailable")
            if label not in labels:
                label = "unavailable"
            tainted = bool(
                provenance.get("engine_fallback_delta", 0)
                or provenance.get("engine_degraded", False)
                or row.get("finish_reason") in {"error", "aborted"}
            )
            if label == "quantum" and (tainted or provenance.get("quantum") is not True):
                label = "unverified"
            values = counts.setdefault((arm_id, label), [0, 0])
            values[0] += 1
            values[1] += int(tainted)
    return [
        {
            "arm_id": arm_id,
            "source_label": label,
            "quantum": label == "quantum",
            "samples": values[0],
            "tainted_samples": values[1],
        }
        for (arm_id, label), values in sorted(counts.items())
    ]


@router.get("/studies/{study_id}/analysis/{analysis_id}/index")
def get_analysis_index(
    study_id: str, analysis_id: str, state: State, run_id: str | None = None
) -> dict[str, object]:
    output = _analysis(_study(state, study_id), analysis_id, run_id)
    if not (output / "manifest.json").is_file() or not (output / "plan.json").is_file():
        raise ApiError(404, "analysis artifacts are not ready")
    return {
        "plan": read_json(output / "plan.json"),
        "manifest": read_json(output / "manifest.json"),
        "tables": sorted(path.stem for path in (output / "tables").glob("*.json")),
        "figures": sorted(path.stem for path in (output / "figures").glob("*.svg")),
        "provenance": _provenance(output.parent.parent),
    }


@router.get("/studies/{study_id}/analysis/{analysis_id}/plan.json")
def get_analysis_plan(
    study_id: str, analysis_id: str, state: State, run_id: str | None = None
) -> FileResponse:
    return _file(
        _analysis(_study(state, study_id), analysis_id, run_id) / "plan.json", "application/json"
    )


@router.get("/studies/{study_id}/analysis/{analysis_id}/manifest.json")
def get_analysis_manifest(
    study_id: str, analysis_id: str, state: State, run_id: str | None = None
) -> FileResponse:
    return _file(
        _analysis(_study(state, study_id), analysis_id, run_id) / "manifest.json",
        "application/json",
        immutable=False,
    )


@router.get("/studies/{study_id}/LICENSE-data")
def get_data_licence(study_id: str, state: State) -> FileResponse:
    return _file(_study(state, study_id).licence_data, "text/plain", filename="LICENSE-data")


@router.get("/studies/{study_id}", response_model=StudyView)
async def get_study(study_id: str, state: State) -> StudyView:
    study_dir = _study(state, study_id)
    runs = state.store.list_runs(study_id=study_id)
    analyses = {
        run.run_id: (
            sorted(
                path.name
                for path in study_dir.run(run.run_id).analysis_root.iterdir()
                if path.is_dir()
            )
            if study_dir.run(run.run_id).analysis_root.is_dir()
            else []
        )
        for run in runs
    }
    return StudyView(
        study_id=study_id,
        study=read_json(study_dir.study_json),
        runs=[_public_run(run) for run in runs],
        analyses=analyses,
        withdrawn=bool(runs) and all(run.withdrawn for run in runs),
    )


@router.get("/studies/{study_id}/analysis/{analysis_id}/tables/{name}")
def get_table(
    study_id: str, analysis_id: str, name: str, state: State, run_id: str | None = None
) -> FileResponse:
    output = _analysis(_study(state, study_id), analysis_id, run_id)
    table = _safe(name.removesuffix(".json"), "table name") + ".json"
    return _file(output / "tables" / table, "application/json")


@router.get("/studies/{study_id}/analysis/{analysis_id}/figures/{name}.svg")
def get_figure(
    study_id: str, analysis_id: str, name: str, state: State, run_id: str | None = None
) -> FileResponse:
    output = _analysis(_study(state, study_id), analysis_id, run_id)
    figure = _safe(name, "figure name") + ".svg"
    return _file(output / "figures" / figure, "image/svg+xml")


@router.get("/studies/{study_id}/analysis/{analysis_id}/narrative.md")
def get_narrative(
    study_id: str, analysis_id: str, state: State, run_id: str | None = None
) -> FileResponse:
    output = _analysis(_study(state, study_id), analysis_id, run_id)
    return _file(output / "narrative.md", "text/markdown; charset=utf-8")


@router.get("/studies/{study_id}/prereg.md")
async def get_prereg(study_id: str, state: State) -> FileResponse:
    study_dir = _study(state, study_id)
    return _file(
        study_dir.prereg_md,
        "text/markdown; charset=utf-8",
        filename=f"{study_id}-prereg.md",
    )


@router.get("/studies/{study_id}/bundle.tar.zst", response_class=StreamingResponse)
async def get_bundle(study_id: str, state: State) -> StreamingResponse:
    _study(state, study_id)
    return StreamingResponse(
        state.store.export_bundle(study_id),
        media_type="application/zstd",
        headers={
            "Cache-Control": REVALIDATE_CACHE,
            "Content-Disposition": f'attachment; filename="{study_id}.tar.zst"',
        },
    )


@router.post("/studies/{study_id}/withdraw", response_model=WithdrawResponse)
async def withdraw_study_run(
    study_id: str,
    body: WithdrawRequest,
    state: State,
    x_host_token: Annotated[str | None, Header()] = None,
) -> WithdrawResponse:
    _study(state, study_id)
    if not x_host_token:
        raise ApiError(401, f"{HOST_TOKEN_HEADER} header is required for host actions")
    runs = state.store.list_runs(study_id=study_id)
    if body.run_id is not None:
        runs = [run for run in runs if run.run_id == body.run_id]
        if not runs:
            raise ApiError(404, f"run {body.run_id!r} is not part of study {study_id!r}")
    authorised = [
        run for run in runs if host_token_matches(x_host_token.strip(), run.host_token_hash)
    ]
    if not authorised:
        raise ApiError(403, "host token does not match a run in this study")
    if len(authorised) > 1 and body.run_id is None:
        raise ApiError(409, "run_id is required because this host token matches multiple runs")
    run = state.store.withdraw(authorised[0].run_id, body.reason, now=state.clock())
    live = state.registry.get(run.run_id)
    if live is not None:
        live.bus.emit(
            RunEvent(
                "run.state",
                {"run_id": run.run_id, "state": run.state, "withdrawn": True},
            )
        )
    return WithdrawResponse(study_id=study_id, run_id=run.run_id)


__all__ = ["router"]
