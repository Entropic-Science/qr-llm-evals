"""Start the frozen analysis plan and produce the checked LLM narrative."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from qr_llm_evals.analysis.narrative import (
    build_prompt,
    load_tables,
    prompt_hash,
    render_narrative,
    verify_numbers,
)
from qr_llm_evals.analysis.run import load_plan
from qr_llm_evals.analysis.run import run as run_analysis
from qr_llm_evals.api.deps import AppState, HostRun, State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.schema import AnalyseResponse
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.client.openai_client import OpenAIClient
from qr_llm_evals.client.profiles import target_for
from qr_llm_evals.models.schema import ModelRef
from qr_llm_evals.runner.events import RunEvent, RunEventBus
from qr_llm_evals.store.artifacts import read_json, write_json, write_text

log = logging.getLogger("qr_llm_evals.api.analysis")

router = APIRouter(tags=["analysis"])

NARRATIVE_MAX_TOKENS = 2048


def _emit(live: HostRun, analysis_id: str, stage: str, **extra: Any) -> None:
    live.bus.emit(
        RunEvent(
            "analysis.progress",
            {"analysis_id": analysis_id, "stage": stage, **extra},
        )
    )


def _plan_for(state: AppState, study_id: str) -> StatisticalPlan:
    study_dir = state.store.study(study_id)
    path = study_dir.path / "prereg-plan.json"
    if path.is_file():
        return load_plan(path)
    # The study config is immutable after freeze. Persisting this deterministic
    # projection makes the downloaded bundle independently re-analysable.
    plan = StatisticalPlan.from_config(state.store.load_study(study_id), state.cards)
    path.write_text(plan.canonical_json() + "\n", encoding="utf-8", newline="\n")
    return plan


def _custom_base_url(model: ModelRef) -> str | None:
    if model.provider == "custom" and model.id.startswith("custom:"):
        return model.id.removeprefix("custom:").split("#", 1)[0]
    return None


def _generate_narrative(
    state: AppState,
    live: HostRun,
    model: ModelRef,
    prompt: str,
) -> str:
    target = target_for(
        model.provider,
        _custom_base_url(model),
        vllm_origin=state.settings.vllm_origin,
    )
    api_key = live.secrets.get(target.secret_name or "") if target.needs_key else None
    if target.needs_key and not api_key:
        raise RuntimeError(f"narrative model needs the {target.secret_name!r} provider key")
    client = OpenAIClient(
        target.base_url,
        api_key=api_key,
        extra_headers=target.extra_headers,
        transport=state.analysis_transport,
    )
    completion = client.complete(
        {
            "model": model.served_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": NARRATIVE_MAX_TOKENS,
        }
    )
    state.usage.record_narrative(live.run_id, model.served_name, completion.usage)
    if not completion.text.strip():
        raise RuntimeError("narrative model returned an empty response")
    return completion.text


def _record_narrative_manifest(
    output: Path,
    *,
    model: str,
    prompt_sha256: str,
    published: bool,
    offending_numbers: list[str],
) -> None:
    path = output / "manifest.json"
    manifest = read_json(path)
    manifest["narrative"] = {
        "model": model,
        "prompt_sha256": prompt_sha256,
        "published": published,
        "offending_numbers": offending_numbers,
    }
    write_json(path, manifest)


def _worker(state: AppState, live: HostRun, plan: StatisticalPlan) -> None:
    run_record = live.read()
    analysis_id = f"a_{plan.plan_hash[:16]}"
    try:
        _emit(live, analysis_id, "statistics", status="running")
        actual_id = run_analysis(
            live.run_store.study_dir.path,
            plan,
            run_id=run_record.run_id,
        )
        output = live.run_store.run_dir.analysis_dir(actual_id)
        _emit(live, actual_id, "statistics", status="complete")

        tables = load_tables(output / "tables")
        prereg_md = live.run_store.study_dir.prereg_md.read_text(encoding="utf-8")
        prompt = build_prompt(tables, prereg_md)
        digest = prompt_hash(prompt)
        _emit(live, actual_id, "narrative", status="running")
        try:
            draft = _generate_narrative(
                state,
                live,
                state.store.load_study(run_record.study_id).narrative_model,
                prompt,
            )
            offending = verify_numbers(draft, tables)
            rendered = render_narrative(
                draft,
                model=state.store.load_study(run_record.study_id).narrative_model.served_name,
                prompt_sha256=digest,
                offending_numbers=offending,
            )
            published = not offending
        except Exception as err:  # noqa: BLE001 - tables remain public when prose generation fails
            log.exception(
                "narrative generation failed for %s",
                run_record.run_id,
                extra={"run_id": run_record.run_id, "analysis_id": actual_id},
            )
            offending = []
            published = False
            model_name = state.store.load_study(run_record.study_id).narrative_model.served_name
            rendered = (
                f"*LLM-written summary (model {model_name}, prompt hash {digest})*\n\n"
                "# Narrative withheld: generation failed\n\n"
                f"The statistical tables remain available. Reason: {type(err).__name__}.\n"
            )
        write_text(output / "narrative.md", rendered)
        model_name = state.store.load_study(run_record.study_id).narrative_model.served_name
        _record_narrative_manifest(
            output,
            model=model_name,
            prompt_sha256=digest,
            published=published,
            offending_numbers=offending,
        )
        _emit(
            live,
            actual_id,
            "narrative",
            status="complete" if published else "withheld",
            offending_numbers=offending,
        )
        live.set_state("complete")
        _emit(live, actual_id, "complete", status="complete")
    except Exception as err:  # noqa: BLE001 - background boundary must restore durable state
        log.exception(
            "analysis failed for %s",
            run_record.run_id,
            extra={"run_id": run_record.run_id, "analysis_id": analysis_id},
        )
        live.set_state("complete")
        _emit(
            live,
            analysis_id,
            "failed",
            status="failed",
            error=type(err).__name__,
        )


@router.post("/runs/{run_id}/analyse", response_model=AnalyseResponse, status_code=202)
async def analyse_run(run_id: str, live: HostRun, state: State) -> AnalyseResponse:
    run_record = live.read()
    if run_record.withdrawn:
        raise ApiError(409, "a withdrawn run cannot be analysed")
    if run_record.state == "analysing":
        raise ApiError(409, "analysis is already running")
    if run_record.state != "complete":
        raise ApiError(409, f"run must be complete before analysis (currently {run_record.state})")
    try:
        plan = _plan_for(state, run_record.study_id)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as err:
        raise ApiError(409, f"frozen analysis plan is unavailable: {err}") from err
    analysis_id = f"a_{plan.plan_hash[:16]}"
    # Generation closes its event bus. Analysis is a second producer phase and
    # therefore opens a fresh bus before exposing the analysing state.
    if live.bus.closed:
        live.bus = RunEventBus()
    live.set_state("analysing")
    _emit(live, analysis_id, "queued", status="queued")
    threading.Thread(
        target=_worker,
        args=(state, live, plan),
        name=f"analysis-{run_id}",
        daemon=True,
    ).start()
    return AnalyseResponse(
        run_id=run_id,
        study_id=run_record.study_id,
        analysis_id=analysis_id,
    )


__all__ = ["router"]
