"""Frozen UnpredictaBench adapter and KS@100 scorer.

The runner calls :func:`score` once per generation, in which case it records the
parsed scalar as ``answer_value``.  The analysis phase calls the same function
with ``texts`` (or ``samples``) containing the k responses for one prompt; that
form computes the per-prompt distributional endpoints.

The bundled data are a deterministic, seven-stratum subset of the upstream
dataset.  They include the upstream 10,000-value reference track so scoring is
offline and cannot drift when a remote dataset changes.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.distance import jensenshannon
from scipy.stats import gaussian_kde, ks_2samp, wasserstein_distance

KS_N = 100
KS_ALPHA = 0.0001
DISCRETE_KS_PERMUTATIONS = 1_000_000
DISTANCE_RESAMPLES = 999
RANDOM_SEED = 20_240_521

_DATA_PATH = Path(__file__).with_name("prompts") / "items.json.gz"
_MANIFEST_PATH = Path(__file__).with_name("prompts") / "manifest.json"
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


@lru_cache(maxsize=1)
def _frozen_items() -> tuple[dict[str, Any], ...]:
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    digest = hashlib.sha256(_DATA_PATH.read_bytes()).hexdigest()
    if digest != manifest["items_json_gz_sha256"]:
        raise ValueError("UnpredictaBench frozen data hash does not match manifest")
    with gzip.open(_DATA_PATH, mode="rt", encoding="utf-8", newline="\n") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or len(payload) != 150:
        raise ValueError("UnpredictaBench frozen data must contain exactly 150 items")
    return tuple(payload)


def build_items() -> list[dict[str, Any]]:
    """Return the fixed 150-item subset, stratified across all seven task types."""
    return [dict(item) for item in _frozen_items()]


def build_messages(item: Mapping[str, Any], told: str | None = None) -> list[dict[str, str]]:
    """Build the upstream prompt verbatim; this card has no told/actual factor."""
    del told
    return [{"role": "user", "content": str(item["prompt"])}]


def _normalise(text: Any) -> str:
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", "", value).lower()
    return value.strip("\n")


def _sequence(text: str) -> list[str] | None:
    stripped = text.strip()
    try:
        value = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        value = None
    if isinstance(value, list):
        return [_normalise(part) for part in value]
    lines = [part for part in _normalise(stripped).split("\n") if part]
    return lines or None


def _lehmer_first(values: Sequence[str], elements: Sequence[str]) -> float | None:
    if len(values) != len(elements) or len(set(values)) != len(values):
        return None
    try:
        indexes = [elements.index(value) for value in values]
    except ValueError:
        return None
    return float(sum(value < indexes[0] for value in indexes[1:]))


def _extract(text: Any, extractor: Mapping[str, Any]) -> float | None:
    kind = extractor.get("kind")
    if kind == "number":
        match = _NUMBER_RE.search(str(text).replace(",", ""))
        if match is None:
            return None
        value = float(match.group(0))
        return value if math.isfinite(value) else None
    if kind == "mapping":
        mapped = extractor.get("mapping", {}).get(_normalise(text))
        return float(mapped) if mapped is not None else None
    if kind in {"permutation", "line_permutation"}:
        values = _sequence(str(text))
        if values is None:
            return None
        for word in extractor.get("remove_words", []):
            values = [re.sub(rf"\b{re.escape(str(word))}\b", "", value) for value in values]
        values = [value for value in values if value]
        return _lehmer_first(values, list(extractor["elements"]))
    raise ValueError(f"unknown UnpredictaBench extractor kind: {kind!r}")


def _ks_statistic_from_counts(
    counts_a: np.ndarray, counts_b: np.ndarray, n_a: int, n_b: int
) -> np.ndarray:
    cdf_a = np.cumsum(counts_a, axis=-1) / n_a
    cdf_b = np.cumsum(counts_b, axis=-1) / n_b
    return np.max(np.abs(cdf_a - cdf_b), axis=-1)


def _discrete_ks(
    reference: np.ndarray, predictions: np.ndarray, permutations: int
) -> tuple[float, float]:
    combined = np.concatenate([reference, predictions])
    support, inverse = np.unique(combined, return_inverse=True)
    pooled = np.bincount(inverse, minlength=support.size).astype(np.int64)
    n_ref = reference.size
    n_pred = predictions.size
    ref_counts = np.bincount(inverse[:n_ref], minlength=support.size).astype(np.int64)
    pred_counts = pooled - ref_counts
    observed = float(_ks_statistic_from_counts(ref_counts, pred_counts, n_ref, n_pred))

    rng = np.random.default_rng(RANDOM_SEED)
    greater_or_equal = 0
    remaining = max(1, int(permutations))
    while remaining:
        batch = min(20_000, remaining)
        perm_ref = rng.multivariate_hypergeometric(pooled, n_ref, size=batch)
        perm_pred = pooled[None, :] - perm_ref
        perm_stats = _ks_statistic_from_counts(perm_ref, perm_pred, n_ref, n_pred)
        greater_or_equal += int(np.sum(perm_stats >= observed))
        remaining -= batch
    p_value = (greater_or_equal + 1) / (max(1, int(permutations)) + 1)
    return observed, float(p_value)


def _distance_scores(
    reference: np.ndarray, predictions: np.ndarray, resamples: int
) -> tuple[float | None, float | None]:
    if reference.size < 2 or predictions.size < 2:
        return None, None
    observed = float(wasserstein_distance(reference, predictions))
    # Keep the original unequal group sizes in every permutation, exactly as
    # the upstream metric notebook does.
    pooled = np.concatenate([reference, predictions])
    rng = np.random.default_rng(RANDOM_SEED)
    null = np.empty(max(1, resamples), dtype=float)
    n_reference = reference.size
    for index in range(null.size):
        order = rng.permutation(pooled.size)
        null[index] = float(
            wasserstein_distance(pooled[order[:n_reference]], pooled[order[n_reference:]])
        )
    std = float(null.std())
    return float(observed - null.mean()), None if std < 1e-12 else float(
        (observed - null.mean()) / std
    )


def _js_divergence(reference: np.ndarray, predictions: np.ndarray) -> float | None:
    if reference.size < 2 or predictions.size < 2:
        return None
    if np.allclose(reference.min(), reference.max()) and np.allclose(
        predictions.min(), predictions.max()
    ):
        return 0.0 if np.isclose(reference[0], predictions[0]) else None
    low = min(float(reference.min()), float(predictions.min()))
    high = max(float(reference.max()), float(predictions.max()))
    span = high - low
    grid = np.linspace(low - 0.1 * span, high + 0.1 * span, 512)
    try:
        p = gaussian_kde(reference)(grid)
        q = gaussian_kde(predictions)(grid)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if p.sum() == 0 or q.sum() == 0:
        return None
    distance = jensenshannon(p / p.sum(), q / q.sum())
    return float(distance**2)


def _texts(sample: Mapping[str, Any]) -> list[Any] | None:
    if isinstance(sample.get("texts"), Sequence) and not isinstance(sample["texts"], str | bytes):
        return list(sample["texts"])
    samples = sample.get("samples")
    if isinstance(samples, Sequence) and not isinstance(samples, str | bytes):
        return [part.get("text", "") if isinstance(part, Mapping) else part for part in samples]
    return None


def score(sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
    """Parse one answer or score a complete k-response distribution against its reference."""
    item = sample.get("item")
    if not isinstance(item, Mapping):
        raise ValueError("sample.item is required")
    extractor = item.get("extractor")
    if not isinstance(extractor, Mapping):
        raise ValueError("sample.item.extractor is required")

    texts = _texts(sample)
    if texts is None:
        value = _extract(sample.get("text", ""), extractor)
        return {"answer_value": value, "answer_valid": int(value is not None)}

    values = [_extract(text, extractor) for text in texts[:KS_N]]
    predictions = np.asarray([value for value in values if value is not None], dtype=float)
    reference = np.asarray(item.get("reference_values", []), dtype=float)
    if reference.size < 2:
        raise ValueError("sample.item.reference_values must contain the frozen reference track")
    if predictions.size < 2:
        return {
            "answer_count": len(values),
            "valid_answer_count": int(predictions.size),
            "parse_rate": predictions.size / max(1, len(values)),
            "ks_statistic": None,
            "ks_p_value": None,
            "ks100_pass_rate": 0,
            "wasserstein_z": None,
            "jsd_vs_ground_truth": None,
        }

    if bool(item.get("discrete")):
        permutations = int(sample.get("discrete_ks_permutations", DISCRETE_KS_PERMUTATIONS))
        ks_statistic, ks_p_value = _discrete_ks(reference, predictions, permutations)
    else:
        result = ks_2samp(reference, predictions, method="exact")
        ks_statistic, ks_p_value = float(result.statistic), float(result.pvalue)
    _, wasserstein_z = _distance_scores(
        reference, predictions, int(sample.get("distance_resamples", DISTANCE_RESAMPLES))
    )
    return {
        "answer_count": len(values),
        "valid_answer_count": int(predictions.size),
        "parse_rate": predictions.size / max(1, len(values)),
        "ks_statistic": ks_statistic,
        "ks_p_value": ks_p_value,
        "ks100_pass_rate": int(predictions.size == KS_N and ks_p_value > KS_ALPHA),
        "wasserstein_z": wasserstein_z,
        "jsd_vs_ground_truth": _js_divergence(reference, predictions),
    }
