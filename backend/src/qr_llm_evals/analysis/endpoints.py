"""Load archived rows and execute the frozen endpoint/contrast contract."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from qr_llm_evals.analysis.mmd import mmd_hamming_permutation_test
from qr_llm_evals.analysis.source_detection import (
    chance_test,
    guess_rows,
    labels_by_completion,
    outside_observer,
    privileged_access,
    records_by_completion,
)
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
from qr_llm_evals.analysis.told_actual import decompose
from qr_llm_evals.cards.statistical_plan import Contrast, EndpointPlan, StatisticalPlan
from qr_llm_evals.store.artifacts import iter_jsonl
from qr_llm_evals.tier0.endpoints import (
    mmd_inputs,
    pit_residuals,
    stream_deviation_table,
    topk_coverage,
)
from qr_llm_evals.tier0.records import FieldsSetPool, independent_streams, share_fields_set
from qr_llm_evals.tier0.schema import Tier0Record

Table = dict[str, Any]

#: Cards analysed as source-guessing tasks (accuracy vs chance, outside observer).
SOURCE_GUESSING_PREFIX = "source_guessing"
#: Score-row field that carries the study judge's parsed per-sample score.
JUDGE_SCORE_FIELD = "judge_score"
#: Judged endpoints → (per-sample field, told/actual effect the endpoint names).
#: ``None`` = the endpoint is the per-sample value itself (its per-prompt mean is
#: the rate); an effect name = the headline is that effect of the told/actual
#: decomposition, while the arm comparisons stay on the per-sample field.
JUDGED_ENDPOINTS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("self_referential_probe", "experience_claim_rate"): (JUDGE_SCORE_FIELD, None),
    ("self_referential_probe", "told_main_effect"): (JUDGE_SCORE_FIELD, "told_source_priming"),
    ("opinion_quantum_sampling", "actual_main_effect_axis_score"): (
        JUDGE_SCORE_FIELD,
        "actual_source_main_effect",
    ),
    ("opinion_quantum_sampling", "told_main_effect"): (JUDGE_SCORE_FIELD, "told_source_priming"),
    ("opinion_quantum_sampling", "told_x_actual_interaction"): (
        JUDGE_SCORE_FIELD,
        "told_by_actual_interaction",
    ),
}


def _field(card_id: str, endpoint: str) -> str:
    """The per-sample score-row field an endpoint is computed from."""
    return JUDGED_ENDPOINTS.get((card_id, endpoint), (endpoint, None))[0]


#: Observer permutations are full cross-validation refits; cap them for runtime.
MAX_OBSERVER_PERMUTATIONS = 1000


@dataclass(frozen=True)
class AnalysisData:
    run_dir: Path
    tier0_source: str | None
    scores: dict[str, dict[str, list[dict[str, Any]]]]
    tier0: dict[str, dict[str, list[Tier0Record]]]
    questionnaires: list[dict[str, Any]]
    #: The run's recorded per-token ``top_logprobs`` depth (``request_settings.json``).
    top_logprobs: int | None = None


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def load_data(run_dir: Path, *, include_tainted: bool = False) -> AnalysisData:
    """Load only archived inputs; scorer modules and the live registry are never consulted.

    The clean primary analysis drops tainted samples (engine fallback, degraded
    entropy, stream errors: the plan's exclusion rule). ``include_tainted=True``
    keeps them, labelled by the arm they were *assigned* to, for the "nothing
    left out" sensitivity analysis; such a load never feeds a quantum claim.
    """
    run = _read_object(run_dir / "run.json")
    scores: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    tier0: dict[str, dict[str, list[Tier0Record]]] = defaultdict(lambda: defaultdict(list))
    # One shared fields-set per record shape: see share_fields_set (memory).
    shapes: FieldsSetPool = {}
    tainted = (
        set()
        if include_tainted
        else {
            str(row["sample_ref"])
            for row in iter_jsonl(run_dir / "taints.jsonl")
            if row.get("sample_ref")
        }
    )
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
            # Judged cards: the study judge's per-sample score (judge/<judge_id>.jsonl)
            # joins the score row so judged endpoints have a value to analyse.
            for judge_path in sorted((card_dir / "judge").glob("*.jsonl")):
                for row in iter_jsonl(judge_path):
                    ref = str(row.get("sample_ref", ""))
                    if clean_refs and ref not in clean_refs:
                        continue
                    value = row.get("score")
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        merged.setdefault(ref, {"sample_ref": ref})[JUDGE_SCORE_FIELD] = float(
                            value
                        )
            scores[card_dir.name][arm_dir.name].extend(merged.values())
            refs: dict[str, str] = {}
            for row in iter_jsonl(card_dir / "tier0.jsonl"):
                tier0_ref = row.get("sample_ref")
                if tier0_ref in tainted:
                    continue
                if isinstance(tier0_ref, str):
                    # One string per sample, shared by its tokens (millions of records).
                    row["sample_ref"] = refs.setdefault(tier0_ref, tier0_ref)
                try:
                    record = Tier0Record.model_validate(row)
                except ValueError:
                    continue
                tier0[card_dir.name][arm_dir.name].append(share_fields_set(record, shapes))
    readiness = _read_object(run_dir / "readiness.json")
    source = run.get("tier0_source") or readiness.get("tier0_source")
    depth = _read_object(run_dir / "request_settings.json").get("top_logprobs")
    return AnalysisData(
        run_dir=run_dir,
        tier0_source=str(source) if source else None,
        scores={card: dict(arms) for card, arms in scores.items()},
        tier0={card: dict(arms) for card, arms in tier0.items()},
        questionnaires=list(iter_jsonl(run_dir / "questionnaires.jsonl")),
        top_logprobs=depth if isinstance(depth, int) and not isinstance(depth, bool) else None,
    )


def tainted_refs(run_dir: Path) -> set[str]:
    """Sample refs the run marked as tainted (``taints.jsonl``)."""
    return {
        str(row["sample_ref"])
        for row in iter_jsonl(run_dir / "taints.jsonl")
        if row.get("sample_ref")
    }


def exclude_tainted(data: AnalysisData) -> AnalysisData:
    """The clean view of a ``load_data(..., include_tainted=True)`` load.

    Same result as ``load_data(run_dir)`` without a second pass over the files: the
    records are shared, only the lists are filtered, so the two versions of an
    analysis (clean and nothing-left-out) cost one load of the token records.
    """
    tainted = tainted_refs(data.run_dir)
    if not tainted:
        return data
    return replace(
        data,
        scores={
            card: {
                arm: [row for row in rows if row.get("sample_ref") not in tainted]
                for arm, rows in arms.items()
            }
            for card, arms in data.scores.items()
        },
        tier0={
            card: {
                arm: [rec for rec in records if rec.sample_ref not in tainted]
                for arm, records in arms.items()
            }
            for card, arms in data.tier0.items()
        },
    )


def _key(row: dict[str, Any]) -> tuple[str, str]:
    """``(pairing suffix, prompt cluster)`` from ``arm/card/prompt/epoch[/told]``.

    The cluster is the prompt (``parts[2]``). Taking ``parts[-2]`` instead would
    cluster told/actual refs by *epoch*, because their last segment is the told cell.
    """
    ref = str(row.get("sample_ref", ""))
    parts = ref.split("/")
    suffix = "/".join(parts[2:]) if len(parts) >= 4 else ref
    fallback = parts[2] if len(parts) >= 4 else (parts[-2] if len(parts) >= 2 else suffix)
    prompt = str(row.get("prompt_id") or fallback)
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
    field = _field(card, endpoint)
    for row in data.scores.get(card, {}).get(arm, []):
        value = _value(row, field)
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


def paired_values(
    data: AnalysisData, endpoint: EndpointPlan, contrast: Contrast
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The frozen pairing (card/prompt/epoch/told) every score contrast uses: ``(a, b, prompts)``."""
    return _paired(data, endpoint, contrast)


#: Zero-generation cards that test the pooled stream as independent tokens or samples.
#: They keep one told cell per (card, prompt, epoch): seeded arms share one stream across
#: the three told cells (``tier0.records.independent_streams``).
INDEPENDENT_STREAM_CARDS = frozenset(
    {"tier0_pit_residuals", "tier0_mmd_hamming", "tier0_logprob_endpoints"}
)


def _tier0_for(data: AnalysisData, card_id: str) -> dict[str, list[Tier0Record]]:
    direct = data.tier0.get(card_id)
    if direct:
        return direct
    # Zero-generation Tier-0 cards consume the complete run stream.
    pooled: dict[str, list[Tier0Record]] = defaultdict(list)
    for by_arm in data.tier0.values():
        for arm, records in by_arm.items():
            pooled[arm].extend(
                independent_streams(records) if card_id in INDEPENDENT_STREAM_CARDS else records
            )
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


def _card_snapshots(plan: StatisticalPlan) -> dict[str, dict[str, Any]]:
    """The frozen card snapshots archived in the plan, keyed by card id."""
    try:
        snapshots = json.loads(plan.cards_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(snapshots, list):
        return {}
    return {str(card.get("id")): card for card in snapshots if isinstance(card, dict)}


def _derived_source(snapshot: dict[str, Any] | None) -> str | None:
    """``dataset.source == "derived:<card_id>"`` → ``<card_id>``; ``None`` otherwise."""
    dataset = snapshot.get("dataset") if snapshot else None
    source = str(dataset.get("source", "")) if isinstance(dataset, dict) else ""
    if not source.startswith("derived:"):
        return None
    card_id = source[len("derived:") :]
    return card_id if card_id and card_id.replace("_", "").isalnum() else None


def _told_actual_block(
    data: AnalysisData, endpoint: EndpointPlan, plan: StatisticalPlan
) -> dict[str, Any]:
    """Told priming, actual effect and interaction per source contrast (ANALYSIS_PLAN)."""
    rows = data.scores.get(endpoint.card_id, {})
    return {
        f"source:{index}": decompose(
            rows,
            _field(endpoint.card_id, endpoint.endpoint),
            contrast.a,
            contrast.b,
            n_permutations=plan.permutations,
            seed=plan.random_seed,
        )
        for index, contrast in enumerate(plan.contrasts)
        if contrast.kind == "source"
    }


def _source_guessing_block(
    data: AnalysisData,
    endpoint: EndpointPlan,
    plan: StatisticalPlan,
    snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Accuracy vs chance, the outside observer and privileged access for a guessing card."""
    rows = data.scores.get(endpoint.card_id, {})
    quantum = sorted({arm for c in plan.contrasts if c.kind == "source" for arm in c.a})
    chance = chance_test(
        rows, quantum_arms=quantum, n_permutations=plan.permutations, seed=plan.random_seed
    )
    source_card = _derived_source(snapshots.get(endpoint.card_id))
    correct: dict[str, int] = {}
    observer: dict[str, Any]
    if source_card is None:
        observer = {"available": False, "reason": "card is not derived from a generating card"}
    else:
        observer, correct = outside_observer(
            records_by_completion(data.tier0.get(source_card, {})),
            labels_by_completion(guess_rows(rows)),
            n_permutations=min(plan.permutations, MAX_OBSERVER_PERMUTATIONS),
            seed=plan.random_seed,
        )
        observer["source_card"] = source_card
    privileged = (
        privileged_access(
            rows, correct, n_resamples=plan.bootstrap_replicates, seed=plan.random_seed
        )
        if correct
        else {"available": False, "reason": "outside observer unavailable"}
    )
    return {"chance_test": chance, "outside_observer": observer, "privileged_access": privileged}


def _topk_coverage_table(data: AnalysisData) -> Table | None:
    """Per-arm share of sampled tokens inside the run's returned top-k (no fixed 20)."""
    if data.top_logprobs is None or not data.tier0:
        return None
    pooled: dict[str, list[Tier0Record]] = defaultdict(list)
    for by_arm in data.tier0.values():
        for arm, records in by_arm.items():
            pooled[arm].extend(records)
    depth = data.top_logprobs
    return {
        "schema_version": 1,
        "endpoint": "tier0_topk_coverage",
        "precision": 6,
        "available": any(pooled.values()),
        "top_logprobs": depth,
        "tier0_source": data.tier0_source,
        "label": (
            f"share of sampled tokens inside the returned top-{depth} (rank_raw < {depth}); "
            "below 1 means QR_EVALS_TOP_LOGPROBS should be raised, not that tokens are dropped"
        ),
        "arms": {
            arm: {"n_tokens": len(records), "coverage": topk_coverage(records, depth)}
            for arm, records in sorted(pooled.items())
        },
    }


def execute(data: AnalysisData, plan: StatisticalPlan) -> dict[str, Table]:
    """Execute every frozen endpoint; missing input is an explicit unavailable table."""
    tables: dict[str, Table] = {}
    snapshots = _card_snapshots(plan)
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
        extras: dict[str, Any] = {}
        if endpoint.told_effect_first and not endpoint.card_id.startswith("tier0_"):
            block = _told_actual_block(data, endpoint, plan)
            extras["told_actual"] = block
            effect = JUDGED_ENDPOINTS.get((endpoint.card_id, endpoint.endpoint), ("", None))[1]
            if effect is not None and block:
                first = next(iter(block.values()))
                extras["headline_effect"] = {
                    "effect_name": effect,
                    "field": _field(endpoint.card_id, endpoint.endpoint),
                    **first.get(effect, {"available": False}),
                }
        if (
            endpoint.card_id.startswith(SOURCE_GUESSING_PREFIX)
            and endpoint.endpoint == "guess_accuracy"
        ):
            extras.update(_source_guessing_block(data, endpoint, plan, snapshots))
        available = (
            any(item.get("available") for item in comparisons)
            or bool(special and special.get("available"))
            or bool(extras.get("chance_test", {}).get("available"))
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
            **extras,
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

    # Descriptive tables carry no role/correction, so they join after the corrections.
    coverage = _topk_coverage_table(data)
    if coverage is not None:
        tables["tier0_topk_coverage"] = coverage

    if data.questionnaires:
        tables["questionnaire_descriptives_by_belief"] = {
            "schema_version": 1,
            "endpoint": "questionnaire_descriptives_by_belief",
            "precision": 3,
            "available": True,
            "groups": _questionnaire_table(data.questionnaires),
        }
    return tables


__all__ = [
    "JUDGED_ENDPOINTS",
    "AnalysisData",
    "exclude_tainted",
    "execute",
    "load_data",
    "paired_values",
    "tainted_refs",
]
