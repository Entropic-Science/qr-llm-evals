"""Can anyone tell the entropy source from the text? (T4-f source-guessing, H3).

The executor compares guess accuracy *between arms*; the card's own question is
whether accuracy beats chance at all, and whether the model's in-context guess
beats an outside observer. This module answers both from archived rows:

* :func:`chance_test` — balanced accuracy of the guesses against 0.5. The unit
  is the **judged completion** (many guesses of one completion are not
  independent), and the null is built by permuting the true source labels over
  completions, so a guesser that always answers "QRNG" scores exactly 0.5 and a
  response bias can never pass for detection. Raw accuracy, d′ and per-variant
  / per-guesser-source splits are descriptive.
* :func:`outside_observer` — the best outside observer: an L2 logistic
  regression on per-sample Tier-0 features of the judged completions
  (:func:`~qr_llm_evals.tier0.endpoints.sample_features`), scored by stratified
  k-fold cross-validation with a label-permutation p-value.
* :func:`privileged_access` — in-context guess accuracy minus the observer's
  accuracy on the same completions, with a paired bootstrap interval.

Everything is seeded and pure numpy/scipy; nothing reads files or the registry.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from statistics import NormalDist
from typing import Any

import numpy as np
from numpy.typing import NDArray

from qr_llm_evals.analysis.stats import binomial_ci, bootstrap_ci
from qr_llm_evals.tier0.endpoints import sample_features
from qr_llm_evals.tier0.schema import Tier0Record

FloatArray = NDArray[np.float64]

SOURCES: tuple[str, str] = ("qrng", "prng")
#: Candidate observer features (dropped when absent or constant in the data).
OBSERVER_FEATURES: tuple[str, ...] = (
    "mean_logprob",
    "mean_rank",
    "argmax_fraction",
    "sequence_length",
    "repetition_rate",
    "mean_entropy_raw_top20",
    "mean_entropy",
    "tail_fraction_p001",
)
DEFAULT_FOLDS = 10
DEFAULT_L2 = 1.0


def _mc_pvalue(count_extreme: int, n_resamples: int) -> float:
    return min(1.0, (count_extreme + 1) / (n_resamples + 1))


def _guesser_source(sample_ref: str, quantum_arms: Iterable[str]) -> str:
    arm = sample_ref.split("/", 1)[0]
    return "qrng" if arm in set(quantum_arms) else "prng"


def guess_rows(
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    labels: tuple[str, str] = SOURCES,
) -> list[dict[str, Any]]:
    """Parsed guesses with a known true label, one dict per guess sample.

    ``labels`` are the two answers, the first being the "positive" one (``qrng``
    for source guessing; ``high`` for the thesis's temperature warm-up).
    """
    out: list[dict[str, Any]] = []
    for arm, rows in rows_by_arm.items():
        for row in rows:
            guess = row.get("guess")
            source = row.get("source")
            if guess not in labels or source not in labels:
                continue
            ref = str(row.get("sample_ref", ""))
            parts = ref.split("/")
            fallback = parts[2] if len(parts) > 2 else ref
            completion = str(row.get("source_sample_ref") or fallback)
            out.append(
                {
                    "arm_id": arm,
                    "sample_ref": ref,
                    "completion": completion,
                    "variant": str(row.get("variant") or "unknown"),
                    "guess": guess,
                    "source": source,
                }
            )
    return out


def _d_prime(rows: Sequence[Mapping[str, Any]], positive: str = "qrng") -> float | None:
    qr = [r for r in rows if r["source"] == positive]
    pr = [r for r in rows if r["source"] != positive]
    if not qr or not pr:
        return None
    hit = (sum(r["guess"] == positive for r in qr) + 0.5) / (len(qr) + 1)
    false_alarm = (sum(r["guess"] == positive for r in pr) + 0.5) / (len(pr) + 1)
    return NormalDist().inv_cdf(hit) - NormalDist().inv_cdf(false_alarm)


def _summary(
    rows: Sequence[Mapping[str, Any]], *, n_permutations: int, seed: int, positive: str = "qrng"
) -> dict[str, Any]:
    by_completion: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_completion[str(row["completion"])].append(row)
    completions = sorted(by_completion)
    labels = np.array([by_completion[c][0]["source"] == positive for c in completions], dtype=bool)
    says_qrng = np.array(
        [np.mean([r["guess"] == positive for r in by_completion[c]]) for c in completions],
        dtype=np.float64,
    )
    accuracy_c = np.array(
        [np.mean([r["guess"] == r["source"] for r in by_completion[c]]) for c in completions],
        dtype=np.float64,
    )
    n_q, n_p = int(labels.sum()), int((~labels).sum())
    hits = sum(r["guess"] == r["source"] for r in rows)
    base: dict[str, Any] = {
        "n_guesses": len(rows),
        "n_completions": len(completions),
        "n_qrng_completions": n_q,
        "n_prng_completions": n_p,
        "says_qrng_rate": float(np.mean([r["guess"] == positive for r in rows])) if rows else None,
        "positive_label": positive,
        "raw_accuracy": hits / len(rows) if rows else None,
        "raw_accuracy_ci": list(binomial_ci(hits, len(rows))) if rows else None,
        "d_prime": _d_prime(rows, positive),
    }
    if n_q == 0 or n_p == 0:
        return {
            "available": False,
            "reason": "guesses must cover completions of both labels",
            **base,
        }

    def balanced(lab: NDArray[np.bool_]) -> float:
        return 0.5 + 0.5 * float(says_qrng[lab].mean() - says_qrng[~lab].mean())

    observed = balanced(labels)
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(n_permutations):
        if abs(balanced(rng.permutation(labels)) - 0.5) >= abs(observed - 0.5) - 1e-12:
            extreme += 1
    completion_ci = list(bootstrap_ci(accuracy_c, seed=seed)) if accuracy_c.size > 1 else None
    return {
        "available": True,
        "balanced_accuracy": observed,
        "effect": observed - 0.5,
        "p_value": _mc_pvalue(extreme, n_permutations),
        "n_permutations": int(n_permutations),
        "completion_accuracy": float(accuracy_c.mean()),
        "completion_accuracy_ci": completion_ci,
        **base,
    }


def chance_test(
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    quantum_arms: Sequence[str] = (),
    n_permutations: int = 2000,
    seed: int = 42,
    labels: tuple[str, str] = SOURCES,
) -> dict[str, Any]:
    """Balanced guess accuracy vs 0.5 with a completion-level label-permutation null."""
    rows = guess_rows(rows_by_arm, labels)
    if not rows:
        return {
            "available": False,
            "reason": "no parsed guesses with known labels (score rows need guess and source)",
        }
    positive = labels[0]
    overall = _summary(rows, n_permutations=n_permutations, seed=seed, positive=positive)
    variants = sorted({str(r["variant"]) for r in rows})
    by_variant = {
        variant: _summary(
            [r for r in rows if r["variant"] == variant],
            n_permutations=n_permutations,
            seed=seed,
            positive=positive,
        )
        for variant in variants
    }
    by_guesser: dict[str, Any] = {}
    if quantum_arms:
        for label in SOURCES:
            subset = [r for r in rows if _guesser_source(r["sample_ref"], quantum_arms) == label]
            if subset:
                by_guesser[f"guesses_sampled_by_{label}"] = _summary(
                    subset, n_permutations=n_permutations, seed=seed, positive=positive
                )
    return {
        **overall,
        "unit": "judged completion; null permutes true labels over completions",
        "labels": list(labels),
        "by_variant": by_variant,
        "by_guesser_source": by_guesser,
    }


# ── the outside observer ─────────────────────────────────────────────────────


def _fit_logistic(x: FloatArray, y: FloatArray, l2: float, max_iter: int = 50) -> FloatArray:
    """Newton–Raphson for L2-penalised logistic regression (intercept unpenalised)."""
    design = np.hstack([np.ones((x.shape[0], 1)), x])
    beta = np.zeros(design.shape[1])
    penalty = np.full(design.shape[1], l2)
    penalty[0] = 0.0
    for _ in range(max_iter):
        z = np.clip(design @ beta, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-z))
        gradient = design.T @ (p - y) + penalty * beta
        weights = p * (1.0 - p)
        hessian = (
            design.T @ (design * weights[:, None])
            + np.diag(penalty)
            + 1e-9 * np.eye(design.shape[1])
        )
        step = np.linalg.solve(hessian, gradient)
        beta = beta - step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return beta


def _predict(beta: FloatArray, x: FloatArray) -> FloatArray:
    z = np.clip(beta[0] + x @ beta[1:], -30.0, 30.0)
    return np.asarray(1.0 / (1.0 + np.exp(-z)), dtype=np.float64)


def _stratified_folds(labels: NDArray[np.bool_], n_folds: int, seed: int) -> NDArray[np.int64]:
    rng = np.random.default_rng(seed)
    folds = np.empty(labels.size, dtype=np.int64)
    for value in (True, False):
        idx = np.flatnonzero(labels == value)
        idx = rng.permutation(idx)
        folds[idx] = np.arange(idx.size) % n_folds
    return folds


def _cross_validated(
    x: FloatArray, labels: NDArray[np.bool_], folds: NDArray[np.int64], l2: float
) -> FloatArray:
    y = labels.astype(np.float64)
    out = np.empty(labels.size, dtype=np.float64)
    for fold in np.unique(folds):
        test = folds == fold
        train = ~test
        mean = x[train].mean(axis=0)
        sd = x[train].std(axis=0)
        sd[sd == 0] = 1.0
        beta = _fit_logistic((x[train] - mean) / sd, y[train], l2)
        out[test] = _predict(beta, (x[test] - mean) / sd)
    return out


def _balanced_accuracy(pred: NDArray[np.bool_], labels: NDArray[np.bool_]) -> float:
    return 0.5 * (float(pred[labels].mean()) + float((~pred[~labels]).mean()))


def _auc(scores: FloatArray, labels: NDArray[np.bool_]) -> float:
    pos, neg = scores[labels], scores[~labels]
    # Average ranks for ties keep the Mann–Whitney statistic exact.
    combined = np.concatenate([pos, neg])
    order = np.argsort(combined, kind="mergesort")
    ranks = np.empty(combined.size, dtype=np.float64)
    sorted_vals = combined[order]
    i = 0
    while i < combined.size:
        j = i
        while j + 1 < combined.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_sum = float(ranks[: pos.size].sum())
    return (rank_sum - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size)


def observer_features(
    records_by_completion: Mapping[str, Sequence[Tier0Record]],
    completions: Sequence[str],
) -> tuple[list[str], list[str], FloatArray]:
    """Feature matrix over the completions that have Tier-0 records.

    Returns ``(kept_completions, feature_names, matrix)``; a feature is kept only
    when every kept completion has it and it is not constant.
    """
    rows: list[dict[str, float | int | None]] = []
    kept: list[str] = []
    for ref in completions:
        seq = sorted(records_by_completion.get(ref, []), key=lambda r: r.token_index)
        if seq:
            kept.append(ref)
            rows.append(sample_features(seq))
    names: list[str] = []
    columns: list[list[float]] = []
    for name in OBSERVER_FEATURES:
        values = [row.get(name) for row in rows]
        if not values or any(v is None for v in values):
            continue
        column = [float(v) for v in values if v is not None]
        if len(set(column)) < 2:
            continue
        names.append(name)
        columns.append(column)
    matrix = np.asarray(columns, dtype=np.float64).T if columns else np.empty((len(kept), 0))
    return kept, names, matrix


def outside_observer(
    records_by_completion: Mapping[str, Sequence[Tier0Record]],
    labels_by_completion: Mapping[str, str],
    *,
    n_folds: int = DEFAULT_FOLDS,
    l2: float = DEFAULT_L2,
    n_permutations: int = 1000,
    seed: int = 42,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Cross-validated logistic-regression source classifier on Tier-0 features.

    Returns the summary table and, separately, per-completion out-of-fold
    correctness (0/1) for :func:`privileged_access`.
    """
    completions = sorted(c for c, s in labels_by_completion.items() if s in SOURCES)
    kept, names, x = observer_features(records_by_completion, completions)
    labels = np.array([labels_by_completion[c] == "qrng" for c in kept], dtype=bool)
    n_q, n_p = int(labels.sum()), int((~labels).sum())
    base = {
        "model": f"L2 logistic regression (lambda={l2}), standardised per training fold",
        "validation": f"stratified {n_folds}-fold cross-validation, seed {seed}",
        "features": names,
        "n_completions": len(kept),
        "n_qrng_completions": n_q,
        "n_prng_completions": n_p,
    }
    if not names or min(n_q, n_p) < n_folds:
        reason = (
            "no usable Tier-0 features for the judged completions"
            if not names
            else f"each source needs at least {n_folds} completions with Tier-0 records"
        )
        return {"available": False, "reason": reason, **base}, {}
    folds = _stratified_folds(labels, n_folds, seed)
    scores = _cross_validated(x, labels, folds, l2)
    pred = scores >= 0.5
    observed = _balanced_accuracy(pred, labels)
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(n_permutations):
        shuffled = rng.permutation(labels)
        null_scores = _cross_validated(x, shuffled, _stratified_folds(shuffled, n_folds, seed), l2)
        null_ba = _balanced_accuracy(null_scores >= 0.5, shuffled)
        if abs(null_ba - 0.5) >= abs(observed - 0.5) - 1e-12:
            extreme += 1
    correct = (pred == labels).astype(int)
    table = {
        "available": True,
        "accuracy": float(correct.mean()),
        "accuracy_ci": list(binomial_ci(int(correct.sum()), int(correct.size))),
        "balanced_accuracy": observed,
        "effect": observed - 0.5,
        "auc": _auc(scores, labels),
        "p_value": _mc_pvalue(extreme, n_permutations),
        "n_permutations": int(n_permutations),
        **base,
    }
    return table, {ref: int(value) for ref, value in zip(kept, correct, strict=True)}


def privileged_access(
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    observer_correct: Mapping[str, int],
    *,
    variant: str = "in_context",
    n_resamples: int = 10_000,
    seed: int = 42,
) -> dict[str, Any]:
    """In-context minus observer **balanced** accuracy on the same completions.

    Balanced accuracy (mean of the per-source accuracies) keeps a majority-class
    observer from looking better than chance when the judged set is unbalanced.
    The interval is a paired bootstrap that resamples completions within source.
    """
    per_completion: dict[str, list[bool]] = defaultdict(list)
    sources: dict[str, str] = {}
    for row in guess_rows(rows_by_arm):
        if row["variant"] == variant:
            completion = str(row["completion"])
            per_completion[completion].append(row["guess"] == row["source"])
            sources[completion] = str(row["source"])
    common = sorted(set(per_completion) & set(observer_correct))
    labels = np.array([sources[c] == "qrng" for c in common], dtype=bool)
    if len(common) < 2 or labels.all() or not labels.any():
        return {
            "available": False,
            "reason": (
                f"needs {variant} guesses and an observer call on completions of both sources"
            ),
            "n_completions": len(common),
        }
    guessed = np.array([np.mean(per_completion[c]) for c in common], dtype=np.float64)
    observed = np.array([observer_correct[c] for c in common], dtype=np.float64)

    def balanced(values: FloatArray, lab: NDArray[np.bool_]) -> float:
        return 0.5 * (float(values[lab].mean()) + float(values[~lab].mean()))

    effect = balanced(guessed, labels) - balanced(observed, labels)
    rng = np.random.default_rng(seed)
    q_idx, p_idx = np.flatnonzero(labels), np.flatnonzero(~labels)
    boot = np.empty(n_resamples, dtype=np.float64)
    for b in range(n_resamples):
        idx = np.concatenate(
            [rng.choice(q_idx, size=q_idx.size), rng.choice(p_idx, size=p_idx.size)]
        )
        boot[b] = balanced(guessed[idx], labels[idx]) - balanced(observed[idx], labels[idx])
    lower, upper = np.percentile(boot, [2.5, 97.5])
    if effect > 0:
        p_value = min(1.0, 2.0 * _mc_pvalue(int(np.sum(boot <= 0)), n_resamples))
    elif effect < 0:
        p_value = min(1.0, 2.0 * _mc_pvalue(int(np.sum(boot >= 0)), n_resamples))
    else:
        p_value = 1.0
    return {
        "available": True,
        "variant": variant,
        "comparison": (
            f"{variant} guess balanced accuracy minus outside-observer balanced accuracy, "
            "same completions"
        ),
        "effect": float(effect),
        "guess_balanced_accuracy": balanced(guessed, labels),
        "observer_balanced_accuracy": balanced(observed, labels),
        "ci_lower": float(lower),
        "ci_upper": float(upper),
        "ci_level": 0.95,
        "p_value": float(p_value),
        "n_completions": len(common),
        "n_resamples": int(n_resamples),
    }


def records_by_completion(
    records_by_arm: Mapping[str, Sequence[Tier0Record]],
) -> dict[str, list[Tier0Record]]:
    """Group Tier-0 records of the judged card by ``sample_ref`` (= the completion id)."""
    out: dict[str, list[Tier0Record]] = defaultdict(list)
    for records in records_by_arm.values():
        for record in records:
            out[record.sample_ref].append(record)
    return dict(out)


def labels_by_completion(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """True source per judged completion, from parsed guess rows."""
    return {str(r["completion"]): str(r["source"]) for r in rows}


__all__ = [
    "OBSERVER_FEATURES",
    "chance_test",
    "guess_rows",
    "labels_by_completion",
    "observer_features",
    "outside_observer",
    "privileged_access",
    "records_by_completion",
]
