"""Load archived rows and execute the frozen endpoint/contrast contract."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from qr_llm_evals.analysis.mmd import mmd_hamming_permutation_test
from qr_llm_evals.analysis.stats import (
    anderson_darling_uniform,
    bh_fdr,
    holm_bonferroni,
    ks_two_sample,
    ks_uniform,
    mcnemar_test,
    paired_permutation_test,
    tost_margin,
    tost_paired,
)
from qr_llm_evals.cards.statistical_plan import Contrast, EndpointPlan, StatisticalPlan
from qr_llm_evals.store.artifacts import iter_jsonl
from qr_llm_evals.tier0.endpoints import mmd_inputs, pit_residuals, stream_deviation_table
from qr_llm_evals.tier0.schema import Tier0Record

Table = dict[str, Any]


@dataclass(frozen=True)
class AnalysisData:
    run_dir: Path
    tier0_source: str | None
    scores: dict[str, dict[str, list[dict[str, Any]]]]
    tier0: dict[str, dict[str, list[Tier0Record]]]
    questionnaires: list[dict[str, Any]]


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def load_data(run_dir: Path) -> AnalysisData:
    """Load only archived inputs; scorer modules and the live registry are never consulted."""
    run = _read_object(run_dir / "run.json")
    scores: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    tier0: dict[str, dict[str, list[Tier0Record]]] = defaultdict(lambda: defaultdict(list))
    tainted = {
        str(row["sample_ref"])
        for row in iter_jsonl(run_dir / "taints.jsonl")
        if row.get("sample_ref")
    }
    for arm_dir in sorted((run_dir / "arms").glob("*")):
        cards_dir = arm_dir / "cards"
        for card_dir in sorted(cards_dir.glob("*")) if cards_dir.is_dir() else []:
            clean_refs = {
                str(row["sample_ref"])
                for row in iter_jsonl(card_dir / "generations.jsonl")
                if row.get("finish_reason") in {"stop", "length"}
                and row.get("sample_ref") not in tainted
            }
            merged: dict[str, dict[str, Any]] = {}
            for score_path in sorted((card_dir / "scores").glob("*.jsonl")):
                for row in iter_jsonl(score_path):
                    ref = str(row.get("sample_ref", ""))
                    if clean_refs and ref not in clean_refs:
                        continue
                    merged.setdefault(ref, {"sample_ref": ref}).update(row)
            scores[card_dir.name][arm_dir.name].extend(merged.values())
            for row in iter_jsonl(card_dir / "tier0.jsonl"):
                if row.get("sample_ref") in tainted:
                    continue
                try:
                    tier0[card_dir.name][arm_dir.name].append(Tier0Record.model_validate(row))
                except ValueError:
                    continue
    readiness = _read_object(run_dir / "readiness.json")
    source = run.get("tier0_source") or readiness.get("tier0_source")
    return AnalysisData(
        run_dir=run_dir,
        tier0_source=str(source) if source else None,
        scores={card: dict(arms) for card, arms in scores.items()},
        tier0={card: dict(arms) for card, arms in tier0.items()},
        questionnaires=list(iter_jsonl(run_dir / "questionnaires.jsonl")),
    )


def _key(row: dict[str, Any]) -> tuple[str, str]:
    ref = str(row.get("sample_ref", ""))
    parts = ref.split("/")
    suffix = "/".join(parts[2:]) if len(parts) >= 4 else ref
    prompt = str(row.get("prompt_id") or (parts[-2] if len(parts) >= 2 else suffix))
    return suffix, prompt


def _value(row: dict[str, Any], endpoint: str) -> float | None:
    value = row.get(endpoint)
    if value is None and endpoint == "rank1_hit_rate":
        value = row.get("rank1_hit")
    if value is None and endpoint == "mean_rank":
        value = row.get("target_rank")
    if isinstance(value, bool | int | float) and not isinstance(value, bool):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, bool):
        return float(value)
    return None


def _arm_values(
    data: AnalysisData, card: str, endpoint: str, arm: str
) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for row in data.scores.get(card, {}).get(arm, []):
        value = _value(row, endpoint)
        if value is not None:
            suffix, prompt = _key(row)
            out[suffix] = (value, prompt)
    return out


def _paired(
    data: AnalysisData, endpoint: EndpointPlan, contrast: Contrast
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    left_by_arm = [
        _arm_values(data, endpoint.card_id, endpoint.endpoint, arm) for arm in contrast.a
    ]
    right_by_arm = [
        _arm_values(data, endpoint.card_id, endpoint.endpoint, arm) for arm in contrast.b
    ]
    keys = (
        set.intersection(*(set(values) for values in [*left_by_arm, *right_by_arm]))
        if left_by_arm and right_by_arm
        else set()
    )
    left: list[float] = []
    right: list[float] = []
    prompts: list[str] = []
    for key in sorted(keys):
        left.append(float(np.mean([values[key][0] for values in left_by_arm])))
        right.append(float(np.mean([values[key][0] for values in right_by_arm])))
        prompts.append(left_by_arm[0][key][1])
    return np.asarray(left), np.asarray(right), np.asarray(prompts)


def _tier0_for(data: AnalysisData, card_id: str) -> dict[str, list[Tier0Record]]:
    direct = data.tier0.get(card_id)
    if direct:
        return direct
    # Zero-generation Tier-0 cards consume the complete run stream.
    pooled: dict[str, list[Tier0Record]] = defaultdict(list)
    for by_arm in data.tier0.values():
        for arm, records in by_arm.items():
            pooled[arm].extend(records)
    return dict(pooled)


def _tier0_contrast(
    data: AnalysisData, endpoint: EndpointPlan, contrast: Contrast, plan: StatisticalPlan
) -> dict[str, Any]:
    records = _tier0_for(data, endpoint.card_id)
    if endpoint.endpoint == "pit_ks_two_sample":
        left = pit_residuals(
            [record for arm in contrast.a for record in records.get(arm, [])], plan.random_seed
        )
        right = pit_residuals(
            [record for arm in contrast.b for record in records.get(arm, [])], plan.random_seed
        )
        if left.size == 0 or right.size == 0:
            return {"available": False, "reason": "no engine-log PIT residuals for both sides"}
        result = ks_two_sample(left, right)
        return {"available": True, **result, "effect": float(result["statistic"])}
    if endpoint.endpoint == "mmd_hamming":
        try:
            inputs = mmd_inputs(records, contrast.a, contrast.b, length=endpoint.k)
            result = mmd_hamming_permutation_test(
                inputs.seqs_a,
                inputs.seqs_b,
                inputs.prompts_a,
                inputs.prompts_b,
                n_permutations=plan.permutations,
                seed=plan.random_seed,
                implementation="vendored",
            )
        except (KeyError, ValueError) as error:
            return {"available": False, "reason": str(error)}
        return {"available": True, **result, "effect": float(result["statistic"])}
    return {"available": False, "reason": "not a Tier-0 primary"}


def _score_contrast(
    data: AnalysisData, endpoint: EndpointPlan, contrast: Contrast, plan: StatisticalPlan
) -> dict[str, Any]:
    left, right, prompts = _paired(data, endpoint, contrast)
    if left.size < 2:
        return {"available": False, "reason": "fewer than two complete paired scores"}
    if endpoint.test == "paired_binomial":
        result = mcnemar_test(left.astype(bool), right.astype(bool))
        effect = float(left.mean() - right.mean())
    else:
        result = paired_permutation_test(
            left,
            right,
            prompts,
            n_permutations=plan.permutations,
            seed=plan.random_seed,
            ci=0.95,
        )
        effect = float(result["mean_diff"])
    out: dict[str, Any] = {
        "available": True,
        "effect": effect,
        "mean_a": float(left.mean()),
        "mean_b": float(right.mean()),
        **result,
    }
    pilot_path = data.run_dir.parent / "pilot" / f"{endpoint.card_id}--{endpoint.endpoint}.json"
    if endpoint.equivalence == "tost" and endpoint.sesoi is not None and pilot_path.is_file():
        pilot = _read_object(pilot_path).get("seed_pair_differences")
        if isinstance(pilot, list) and pilot:
            margin = tost_margin(endpoint.sesoi, pilot)
            out["equivalence"] = tost_paired(left - right, margin, prompt_ids=prompts)
        else:
            out["equivalence"] = {"available": False, "reason": "pilot differences unavailable"}
    elif endpoint.equivalence != "unavailable":
        out["equivalence"] = {
            "available": False,
            "reason": "frozen SESOI and independent pilot differences are required",
        }
    return out


def _questionnaire_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    belief: dict[str, float] = {}
    posts: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        raw_answers = row.get("answers")
        answers: dict[str, Any] = raw_answers if isinstance(raw_answers, dict) else {}
        key = f"{row.get('observer_id')}|{row.get('session_id')}"
        if row.get("phase") == "pre" and isinstance(answers.get("influence_belief"), int | float):
            belief[key] = float(answers["influence_belief"])
        elif row.get("phase") == "post":
            posts.append((key, answers))
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for key, answers in posts:
        if key not in belief:
            continue
        bucket = str(int(belief[key])) if belief[key].is_integer() else str(belief[key])
        for item, value in answers.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                groups[bucket][item].append(float(value))
    return {
        bucket: {
            item: {
                "n": len(values),
                "mean": float(np.mean(values)),
                "sd": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            }
            for item, values in sorted(items.items())
        }
        for bucket, items in sorted(groups.items())
    }


def execute(data: AnalysisData, plan: StatisticalPlan) -> dict[str, Table]:
    """Execute every frozen endpoint; missing input is an explicit unavailable table."""
    tables: dict[str, Table] = {}
    for endpoint in plan.endpoints:
        name = f"{endpoint.card_id}--{endpoint.endpoint}"
        comparisons: list[dict[str, Any]] = []
        for index, contrast in enumerate(plan.contrasts):
            if endpoint.card_id.startswith("tier0_"):
                result = _tier0_contrast(data, endpoint, contrast, plan)
            else:
                result = _score_contrast(data, endpoint, contrast, plan)
            comparisons.append(
                {
                    "id": f"{contrast.kind}:{index}",
                    "kind": contrast.kind,
                    "arms_a": list(contrast.a),
                    "arms_b": list(contrast.b),
                    "epsilon": contrast.epsilon,
                    **result,
                }
            )
        if endpoint.card_id == "stream_deviation_windows":
            records = [
                record
                for values in _tier0_for(data, endpoint.card_id).values()
                for record in values
            ]
            special = stream_deviation_table(
                records,
                n_permutations=plan.permutations,
                seed=plan.random_seed,
                tier0_source=data.tier0_source,  # type: ignore[arg-type]
            )
        elif endpoint.test in {"ks_uniform", "anderson_darling_uniform"}:
            residuals = [
                value
                for records in _tier0_for(data, endpoint.card_id).values()
                for value in pit_residuals(records, plan.random_seed)
            ]
            if residuals:
                special = {
                    "available": True,
                    **(
                        ks_uniform(residuals)
                        if endpoint.test == "ks_uniform"
                        else anderson_darling_uniform(residuals)
                    ),
                }
            else:
                special = {"available": False, "reason": "no engine-log PIT residuals"}
        else:
            special = None
        available = any(item.get("available") for item in comparisons) or bool(
            special and special.get("available")
        )
        tables[name] = {
            "schema_version": 1,
            "card_id": endpoint.card_id,
            "endpoint": endpoint.endpoint,
            "tier": endpoint.tier,
            "role": endpoint.role,
            "test": endpoint.test,
            "correction": endpoint.correction,
            "family": endpoint.family,
            "precision": 6,
            "available": available,
            **({} if available else {"reason": "required archived endpoint rows are unavailable"}),
            "comparisons": comparisons,
            "controls": [item for item in comparisons if item["kind"] == "seed_vs_seed"],
            "epsilon_curve": [item for item in comparisons if item["kind"] == "epsilon"],
            "special": special,
            **(
                {
                    "effect_order": [
                        "told_source_priming",
                        "actual_source_main_effect",
                        "told_by_actual_interaction",
                    ]
                }
                if endpoint.told_effect_first
                else {}
            ),
        }

    # Corrections are applied after all raw p-values exist. Unavailable primaries retain p=1 slots.
    for kind, family_name in (
        ("source", "source_primaries"),
        ("supervised", "supervised_primaries"),
    ):
        slots: list[tuple[dict[str, Any], float]] = []
        for table in tables.values():
            if table["role"] != "primary":
                continue
            matches = [item for item in table["comparisons"] if item["kind"] == kind]
            if matches:
                item = matches[0]
                slots.append(
                    (item, float(item.get("p_value", 1.0)) if item.get("available") else 1.0)
                )
            else:
                table.setdefault("correction_slots", {})[family_name] = {
                    "available": False,
                    "p_for_correction": 1.0,
                }
        if slots:
            for (item, _), adjusted in zip(
                slots, holm_bonferroni([p for _, p in slots]), strict=True
            ):
                item["p_adjusted"] = float(adjusted)
                item["significant"] = bool(item.get("available") and adjusted < plan.alpha)

    # The preregistered claim rule also requires a treatment magnitude larger than
    # every seed-pair control. Missing controls always preclude a claim.
    for table in tables.values():
        if table.get("role") != "primary":
            continue
        treatment = next((item for item in table["comparisons"] if item["kind"] == "source"), None)
        controls = table["controls"]
        if treatment is not None:
            controls_complete = len(controls) == 3 and all(
                item.get("available") and item.get("effect") is not None for item in controls
            )
            exceeds = bool(
                controls_complete
                and treatment.get("effect") is not None
                and abs(float(treatment["effect"]))
                > max(abs(float(item["effect"])) for item in controls)
            )
            treatment["exceeds_all_seed_controls"] = exceeds
            treatment["claim_supported"] = bool(treatment.get("significant") and exceeds)

    by_tier: dict[int, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for table in tables.values():
        if table["correction"] != "bh":
            continue
        source = next((item for item in table["comparisons"] if item["kind"] == "source"), None)
        if source is not None:
            by_tier[int(table["tier"])].append((source, float(source.get("p_value", 1.0))))
    for members in by_tier.values():
        for (item, _), adjusted in zip(members, bh_fdr([p for _, p in members]), strict=True):
            item["p_adjusted"] = float(adjusted)

    if data.questionnaires:
        tables["questionnaire_descriptives_by_belief"] = {
            "schema_version": 1,
            "endpoint": "questionnaire_descriptives_by_belief",
            "precision": 3,
            "available": True,
            "groups": _questionnaire_table(data.questionnaires),
        }
    return tables


__all__ = ["AnalysisData", "execute", "load_data"]
