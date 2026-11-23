"""Load archived rows and execute the frozen endpoint/contrast contract."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from qr_llm_evals.analysis import decisions
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
from qr_llm_evals.cards.endpoint_meta import in_range
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
    ("darkbench", "dark_pattern_rate"): (JUDGE_SCORE_FIELD, None),
    ("deception_bench", "deception_rate"): (JUDGE_SCORE_FIELD, None),
    ("eval_awareness", "eval_belief_rate"): (JUDGE_SCORE_FIELD, None),
    ("mask", "lie_rate"): (JUDGE_SCORE_FIELD, None),
    ("mask_beliefs", "belief_held"): (JUDGE_SCORE_FIELD, None),
    ("strong_reject", "harmfulness_score"): (JUDGE_SCORE_FIELD, None),
}


def _field(card_id: str, endpoint: str) -> str:
    """The per-sample score-row field an endpoint is computed from."""
    return JUDGED_ENDPOINTS.get((card_id, endpoint), (endpoint, None))[0]


#: Observer permutations are full cross-validation refits; cap them for runtime.
MAX_OBSERVER_PERMUTATIONS = 1000
#: Finish reasons of a generation row that holds a completed reply.
COMPLETED_FINISH_REASONS = frozenset({"stop", "length"})
#: The taint of a generation that ended in an error before the reply was complete.
STREAM_ERROR = "stream_error"


@dataclass(frozen=True)
class AnalysisData:
    run_dir: Path
    tier0_source: str | None
    scores: dict[str, dict[str, list[dict[str, Any]]]]
    tier0: dict[str, dict[str, list[Tier0Record]]]
    questionnaires: list[dict[str, Any]]
    #: The run's recorded per-token ``top_logprobs`` depth (``request_settings.json``).
    top_logprobs: int | None = None
    #: Sample refs that have a completed generation row. ``exclude_tainted`` has no
    #: generation rows of its own, so the load carries what the exclusion rule needs.
    #: A data set built by hand has none, and every taint then excludes.
    completed: frozenset[str] = frozenset()


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def excluded_refs(run_dir: Path, completed: AbstractSet[str]) -> set[str]:
    """Sample refs the exclusion rule leaves out of the clean analysis.

    A tainted sample is left out, with one exception that holds for every plan:
    a ``stream_error`` taint does not exclude a sample that has a completed row.
    That taint was written for an attempt that ended in an error. The sample was
    generated again after a resume, and the taint of the failed attempt says
    nothing about the reply that completed. A sample that also carries a taint
    of another kind stays out, and so does one whose every attempt failed.

    ``completed`` is the set of refs that have a completed generation row.
    ``load_data`` and ``exclude_tainted`` both apply this one rule, so the two
    ways of loading the clean data cannot disagree.
    """
    excluded: set[str] = set()
    for row in iter_jsonl(run_dir / "taints.jsonl"):
        if not row.get("sample_ref"):
            continue
        ref = str(row["sample_ref"])
        if row.get("kind") == STREAM_ERROR and ref in completed:
            continue
        excluded.add(ref)
    return excluded


def load_data(run_dir: Path, *, include_tainted: bool = False) -> AnalysisData:
    """Load only archived inputs; scorer modules and the live registry are never consulted.

    The clean primary analysis drops tainted samples (engine fallback, degraded
    entropy, stream errors: the plan's exclusion rule, which ``excluded_refs``
    applies). An excluded row is dropped whatever else the arm holds: an arm
    whose every sample on a card is excluded has no clean rows there, and the
    endpoints of that card are then unavailable for it. ``include_tainted=True``
    keeps them, labelled by the arm they were *assigned* to, for the "nothing
    left out" sensitivity analysis; such a load never feeds a quantum claim.
    """
    run = _read_object(run_dir / "run.json")
    scores: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    tier0: dict[str, dict[str, list[Tier0Record]]] = defaultdict(lambda: defaultdict(list))
    # One shared fields-set per record shape: see share_fields_set (memory).
    shapes: FieldsSetPool = {}
    # The exclusion rule needs to know which samples completed, so the generation
    # rows of every card are read before the taints are applied.
    completed_in: dict[Path, set[str]] = {}
    for arm_dir in sorted((run_dir / "arms").glob("*")):
        cards_dir = arm_dir / "cards"
        for card_dir in sorted(cards_dir.glob("*")) if cards_dir.is_dir() else []:
            completed_in[card_dir] = {
                str(row["sample_ref"])
                for row in iter_jsonl(card_dir / "generations.jsonl")
                if row.get("finish_reason") in COMPLETED_FINISH_REASONS
            }
    completed: frozenset[str] = frozenset().union(*completed_in.values())
    tainted = set() if include_tainted else excluded_refs(run_dir, completed)
    for arm_dir in sorted((run_dir / "arms").glob("*")):
        cards_dir = arm_dir / "cards"
        for card_dir in sorted(cards_dir.glob("*")) if cards_dir.is_dir() else []:
            clean_refs = completed_in[card_dir] - tainted
            merged: dict[str, dict[str, Any]] = {}
            for score_path in sorted((card_dir / "scores").glob("*.jsonl")):
                for row in iter_jsonl(score_path):
                    ref = str(row.get("sample_ref", ""))
                    # ``clean_refs`` is empty for a card directory without generation
                    # rows, and every row is then kept. A tainted row never is.
                    if ref in tainted or (clean_refs and ref not in clean_refs):
                        continue
                    merged.setdefault(ref, {"sample_ref": ref}).update(row)
            # Judged cards: the study judge's per-sample score (judge/<judge_id>.jsonl)
            # joins the score row so judged endpoints have a value to analyse.
            for judge_path in sorted((card_dir / "judge").glob("*.jsonl")):
                for row in iter_jsonl(judge_path):
                    ref = str(row.get("sample_ref", ""))
                    if ref in tainted or (clean_refs and ref not in clean_refs):
                        continue
                    value = row.get("score")
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        merged.setdefault(ref, {"sample_ref": ref})[JUDGE_SCORE_FIELD] = float(
                            value
                        )
                        # Named values the card read from the same reply (JudgeRow.fields).
                        extra = row.get("fields")
                        for name, number in extra.items() if isinstance(extra, dict) else ():
                            if isinstance(number, int | float) and not isinstance(number, bool):
                                merged[ref][str(name)] = float(number)
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
        completed=completed,
    )


def tainted_refs(run_dir: Path) -> set[str]:
    """Sample refs the run marked as tainted (``taints.jsonl``), whatever the kind.

    This is not the set the clean analysis leaves out: that is ``excluded_refs``.
    """
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
    The rule is ``excluded_refs``, applied to the completed samples the load carries.
    """
    tainted = excluded_refs(data.run_dir, data.completed)
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
    data: AnalysisData, card: str, endpoint: str, arm: str, *, bounded: bool = False
) -> dict[str, tuple[float, str]]:
    """One arm's values of an endpoint, keyed by pairing suffix.

    ``bounded`` leaves out a value outside the endpoint's registered range
    (``cards.endpoint_meta``). A designed study rates its sample size on that
    range, and a judged score is only bounded by a rubric the judge may not
    follow: a reply that is not in the rubric's format can parse to any number.
    Such a value is not a measurement. Left out, its pair is missing and counts
    against the completeness of the hypothesis.
    """
    out: dict[str, tuple[float, str]] = {}
    field = _field(card, endpoint)
    for row in data.scores.get(card, {}).get(arm, []):
        value = _value(row, field)
        if value is not None and bounded and not in_range(card, endpoint, value):
            continue
        if value is not None:
            suffix, prompt = _key(row)
            out[suffix] = (value, prompt)
    return out


def _paired(
    data: AnalysisData, endpoint: EndpointPlan, contrast: Contrast, *, bounded: bool = False
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    left_by_arm = [
        _arm_values(data, endpoint.card_id, endpoint.endpoint, arm, bounded=bounded)
        for arm in contrast.a
    ]
    right_by_arm = [
        _arm_values(data, endpoint.card_id, endpoint.endpoint, arm, bounded=bounded)
        for arm in contrast.b
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


def _registered_margin(plan: StatisticalPlan, endpoint: EndpointPlan) -> float | None:
    """The equivalence margin a designed plan registered for ``endpoint``, if any.

    A margin belongs to the endpoint, so every (model, sampling) cell's
    hypothesis on it carries the same one.
    """
    if plan.design is None:
        return None
    return next(
        (
            hypothesis.margin
            for hypothesis in plan.design.hypotheses
            if (hypothesis.card_id, hypothesis.endpoint) == (endpoint.card_id, endpoint.endpoint)
            and hypothesis.margin is not None
        ),
        None,
    )


#: Why a paired comparison whose complete pairs all lie on one prompt is unavailable.
ONE_PROMPT_REASON = "fewer than two prompts with complete pairs"


def _score_contrast(
    data: AnalysisData, endpoint: EndpointPlan, contrast: Contrast, plan: StatisticalPlan
) -> dict[str, Any]:
    # Only a designed study registers the range; a legacy study keeps every value it always had.
    left, right, prompts = _paired(data, endpoint, contrast, bounded=plan.design is not None)
    if left.size < 2:
        return {"available": False, "reason": "fewer than two complete paired scores"}
    # The permutation test and the equivalence test take the prompt as the unit
    # that varies. Pairs that all lie on one prompt give them nothing to estimate
    # a spread from, so the comparison is unavailable, as it is without pairs.
    one_prompt = np.unique(prompts).size < 2
    if one_prompt and endpoint.test != "paired_binomial":
        return {"available": False, "reason": ONE_PROMPT_REASON}
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
            # The legacy plan states 90% and always computed 95%; that mismatch is
            # frozen with its tables. A designed plan computes the level it registers.
            ci=LEGACY_CI if plan.design is None else plan.ci_level,
        )
        effect = float(result["mean_diff"])
    out: dict[str, Any] = {
        "available": True,
        "effect": effect,
        "mean_a": float(left.mean()),
        "mean_b": float(right.mean()),
        **result,
    }
    if plan.design is not None:
        # The margin was fixed at registration, so no pilot file is consulted and
        # the test runs at the plan's own level.
        margin = _registered_margin(plan, endpoint)
        if margin is not None:
            out["equivalence"] = (
                {"available": False, "reason": ONE_PROMPT_REASON}
                if one_prompt
                else tost_paired(left - right, margin, alpha=plan.alpha, prompt_ids=prompts)
            )
        return out
    pilot_path = data.run_dir.parent / "pilot" / f"{endpoint.card_id}--{endpoint.endpoint}.json"
    if endpoint.equivalence == "tost" and endpoint.sesoi is not None and pilot_path.is_file():
        pilot = _read_object(pilot_path).get("seed_pair_differences")
        if isinstance(pilot, list) and pilot and one_prompt:
            out["equivalence"] = {"available": False, "reason": ONE_PROMPT_REASON}
        elif isinstance(pilot, list) and pilot:
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


#: What every interval of a plan without a design was computed at. That plan
#: states 90%; the mismatch is frozen with its tables.
LEGACY_CI = 0.95


def _interval_level(plan: StatisticalPlan) -> float | None:
    """The level of every interval of a designed analysis; ``None`` without a design.

    A designed study computes all its intervals at the level it registered.
    ``None`` leaves the primitives at the 95% they always computed, so the
    archived tables keep their bytes.
    """
    return None if plan.design is None else plan.ci_level


def _told_actual_block(
    data: AnalysisData, endpoint: EndpointPlan, plan: StatisticalPlan
) -> dict[str, Any]:
    """Told priming, actual effect and interaction per source contrast (ANALYSIS_PLAN)."""
    rows = data.scores.get(endpoint.card_id, {})
    level = _interval_level(plan)
    return {
        f"source:{index}": decompose(
            rows,
            _field(endpoint.card_id, endpoint.endpoint),
            contrast.a,
            contrast.b,
            n_permutations=plan.permutations,
            seed=plan.random_seed,
            ci=LEGACY_CI if level is None else level,
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
    level = _interval_level(plan)
    chance = chance_test(
        rows,
        quantum_arms=quantum,
        n_permutations=plan.permutations,
        seed=plan.random_seed,
        ci=level,
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
            ci=level,
        )
        observer["source_card"] = source_card
    privileged = (
        privileged_access(
            rows, correct, n_resamples=plan.bootstrap_replicates, seed=plan.random_seed, ci=level
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


#: The primitives' raw-p flag at a fixed 0.05. A designed study tests at the level
#: it registered and after correction, so its tables do not carry this second signal.
RAW_SIGNIFICANCE_FLAG = "significant_at_05"


def _without_raw_flag(value: Any) -> Any:
    """``value`` with every ``significant_at_05`` key removed, at any depth."""
    if isinstance(value, dict):
        return {
            key: _without_raw_flag(item)
            for key, item in value.items()
            if key != RAW_SIGNIFICANCE_FLAG
        }
    if isinstance(value, list | tuple):
        return [_without_raw_flag(item) for item in value]
    return value


def _generations_present(data: AnalysisData) -> dict[str, dict[str, int]]:
    """Per eval and arm, the generations of the run that left token records.

    This is what a token-stream test can draw on. ``analysis.decisions`` takes
    the numerator of its completeness from it, on the evals the plan counted.
    The count is taken before the told cells are thinned
    (``INDEPENDENT_STREAM_CARDS``): thinning is part of the test, not missing data.
    """
    return {
        card: {arm: len({record.sample_ref for record in records}) for arm, records in arms.items()}
        for card, arms in data.tier0.items()
    }


def _generations_bracketed(data: AnalysisData) -> dict[str, dict[str, int]]:
    """Per eval and arm, the generations with a token record the KS test can read.

    The KS test is run on PIT residuals, and a residual needs the token's CDF
    bracket (``tier0.endpoints.pit_residuals`` skips a record without one). A
    generation whose engine token log could not be read keeps records without
    brackets: it left token records and gives the KS test nothing. So the
    completeness of a KS hypothesis is counted here, on the generations the
    test can use, and not on :func:`_generations_present`.
    """
    return {
        card: {
            arm: len(
                {
                    record.sample_ref
                    for record in records
                    if record.cdf_lo is not None and record.cdf_hi is not None
                }
            )
            for arm, records in arms.items()
        }
        for card, arms in data.tier0.items()
    }


def execute(data: AnalysisData, plan: StatisticalPlan) -> dict[str, Table]:
    """Execute every frozen endpoint; missing input is an explicit unavailable table."""
    tables: dict[str, Table] = {}
    snapshots = _card_snapshots(plan)
    designed = plan.design is not None
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
                    # A designed study may compare several (model, sampling) cells,
                    # so each of its rows says which cell it belongs to.
                    **(
                        {"model": contrast.model, "sampling": contrast.sampling} if designed else {}
                    ),
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
                envelope_level=_interval_level(plan),
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

    if plan.design is None:
        _legacy_corrections(tables, plan)
    else:
        tables = {name: _without_raw_flag(table) for name, table in tables.items()}
        decisions.apply(
            tables,
            plan,
            run_dir=data.run_dir,
            generations_present=_generations_present(data),
            generations_bracketed=_generations_bracketed(data),
        )

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


def _legacy_corrections(tables: dict[str, Table], plan: StatisticalPlan) -> None:
    """Holm, the three-seed claim rule and BH of a plan without a design block.

    These are the corrections every study registered before the design block
    was analysed with; their tables are pinned byte for byte, so the body is
    kept as it was. A designed study is resolved by ``analysis.decisions``.
    """
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


__all__ = [
    "JUDGED_ENDPOINTS",
    "AnalysisData",
    "exclude_tainted",
    "excluded_refs",
    "execute",
    "load_data",
    "paired_values",
    "tainted_refs",
]
