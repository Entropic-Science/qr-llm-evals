"""Tier 0 endpoint measurements from ``Tier0Record`` streams (registry B.2, C.4; spec §2.6, §2.10).

Layer 1 — this module may use numpy/scipy but **not** ``qr_llm_evals.analysis``
(the layering rule; ``analysis.stats`` is a leaf importable only from
``analysis``). So the split is:

* here: the measurement itself — PIT residuals from ``u``/CDF brackets, token-id
  sequences padded for the Hamming kernel, per-sample logprob features,
  per-second bit fractions per supervised window — plus the elementary
  statistics that are one scipy call (KS one-/two-sample, Stouffer-Z, the
  window-label permutation) so each function returns a JSON-able **table**;
* in ``analysis`` (which imports both): Anderson–Darling on the residuals,
  the MMD-Hamming permutation test on :func:`mmd_inputs`, paired per-prompt
  permutation on :func:`prompt_feature_rows`, Holm/BH across endpoints.

Every table carries ``endpoint``/``card``, ``available`` and — when the run's
``tier0_source`` cannot honestly compute it — a ``reason`` instead of numbers
(PRD A-3: *unavailable*, never approximated).
"""

from __future__ import annotations

import functools
import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import stats as sp

from qr_llm_evals.models.schema import Tier0Source
from qr_llm_evals.tier0.acquire import ENTROPY_RAW_TOP20_LABEL
from qr_llm_evals.tier0.availability import CARD_ENDPOINTS, is_available, requirement_for
from qr_llm_evals.tier0.records import group_by_sample, independent_streams, parse_sample_ref
from qr_llm_evals.tier0.schema import Tier0Record

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Table = dict[str, Any]
RecordsByArm = Mapping[str, Sequence[Tier0Record]]

#: Pad value for :func:`mmd_inputs`; negative so it never collides with a token id
#: (same convention as ``analysis.mmd.PAD_TOKEN``).
PAD_TOKEN = -1
#: ``p_post`` below this counts as the min-p tail (registry B.2 T0-c).
TAIL_P = 0.01
#: Rank histogram bins ``0 … RANK_HIST_MAX-1`` plus one overflow bin ``>= RANK_HIST_MAX``.
RANK_HIST_MAX = 20
#: One second in ``timestamp_ns`` units (the GCP/PEAR bin width, registry C.4).
NS_PER_SECOND = 1_000_000_000

__all__ = [
    "PAD_TOKEN",
    "RANK_HIST_MAX",
    "TAIL_P",
    "MmdInputs",
    "SecondBin",
    "first_token_residuals",
    "lag1_autocorr",
    "mmd_inputs",
    "pit_inputs",
    "pit_residuals",
    "prompt_feature_rows",
    "rank_histogram",
    "sample_feature_rows",
    "sample_features",
    "second_bins",
    "stream_deviation_table",
    "t0a_table",
    "t0b_table",
    "t0c_table",
    "token_uniforms",
    "topk_coverage",
    "u_vs_r_agreement",
    "unavailable",
]


# --------------------------------------------------------------------------- #
# Shared table plumbing
# --------------------------------------------------------------------------- #


def unavailable(
    endpoint: str, tier0_source: Tier0Source | None, reason: str | None = None
) -> Table:
    """A table stating that ``endpoint`` cannot be computed for this run."""
    need = requirement_for(endpoint)
    have = "no Tier 0 records" if tier0_source is None else f"tier0_source={tier0_source}"
    return {
        "endpoint": endpoint,
        "requires_tier0": need,
        "available": False,
        "reason": reason or f"needs {need}; this run has {have}",
    }


def _check_source(endpoint: str, tier0_source: Tier0Source | None) -> Table | None:
    """``None`` when the endpoint may be computed, else the unavailable table."""
    if tier0_source is None:
        return None
    return None if is_available(endpoint, tier0_source) else unavailable(endpoint, tier0_source)


def _select(records_by_arm: RecordsByArm, arms: str | Sequence[str]) -> list[Tier0Record]:
    names = [arms] if isinstance(arms, str) else list(arms)
    out: list[Tier0Record] = []
    for name in names:
        if name not in records_by_arm:
            raise KeyError(f"unknown arm {name!r}; have {sorted(records_by_arm)}")
        out.extend(records_by_arm[name])
    return out


def _label(arms: str | Sequence[str]) -> str:
    return arms if isinstance(arms, str) else "+".join(arms)


# --------------------------------------------------------------------------- #
# T0-a  Probability-integral-transform residuals
# --------------------------------------------------------------------------- #


def _bracketed(records: Iterable[Tier0Record]) -> list[Tier0Record]:
    return [r for r in records if r.cdf_lo is not None and r.cdf_hi is not None]


_SPLITMIX_GAMMA = np.uint64(0x9E3779B97F4A7C15)
_SPLITMIX_M1 = np.uint64(0xBF58476D1CE4E5B9)
_SPLITMIX_M2 = np.uint64(0x94D049BB133111EB)


@functools.lru_cache(maxsize=1 << 17)
def _sample_key(seed: int, sample_ref: str) -> int:
    digest = hashlib.sha256(f"pit-v\x1f{seed}\x1f{sample_ref}".encode()).digest()
    return int.from_bytes(digest[:8], "little")


def token_uniforms(records: Sequence[Tier0Record], seed: int = 42) -> FloatArray:
    """One ``V ~ U(0,1)`` per record, keyed by ``(seed, sample_ref, token_index)``.

    Keyed, not drawn in list order: ``V`` is independent across tokens, samples
    and arms, and a token keeps its ``V`` however callers slice or pool the
    records. (Drawing ``V`` by position gave every call the same sequence, so
    per-sample calls shared it across samples and two arms shared it index by
    index — which inflated the lag-1 and pooled uniformity tests under H0.)
    SplitMix64 over a per-sample SHA-256 key; deterministic across processes.
    """
    keys = np.fromiter(
        (_sample_key(seed, r.sample_ref) for r in records), dtype=np.uint64, count=len(records)
    )
    index = np.fromiter((r.token_index for r in records), dtype=np.uint64, count=len(records))
    x = keys + (index + np.uint64(1)) * _SPLITMIX_GAMMA  # uint64 arithmetic wraps mod 2**64
    x = (x ^ (x >> np.uint64(30))) * _SPLITMIX_M1
    x = (x ^ (x >> np.uint64(27))) * _SPLITMIX_M2
    x = x ^ (x >> np.uint64(31))
    result: FloatArray = ((x >> np.uint64(11)).astype(np.float64) + 0.5) / float(1 << 53)
    return result


def pit_residuals(records: Iterable[Tier0Record], seed: int = 42) -> FloatArray:
    """Randomised quantile residuals ``r = F(t−1) + V·p(t)``, ``V ~ U[0,1]`` fresh and seeded.

    With ``cdf_lo = F(t−1)`` and ``cdf_hi = F(t)`` this is ``cdf_lo + V·(cdf_hi − cdf_lo)``
    (2506.06975): U[0,1] i.i.d. under H0 whatever RNG chose ``t``. ``V`` comes from
    :func:`token_uniforms`, so it is independent per token even across separate
    calls. Records without a bracket (degraded path) are skipped, never imputed.
    Order follows the input order so callers can align residuals with their records.
    """
    recs = _bracketed(records)
    if not recs:
        return np.empty(0, dtype=np.float64)
    lo = np.array([r.cdf_lo for r in recs], dtype=np.float64)
    hi = np.array([r.cdf_hi for r in recs], dtype=np.float64)
    v = token_uniforms(recs, seed)
    result: FloatArray = np.clip(lo + v * (hi - lo), 0.0, 1.0)
    return result


def first_token_residuals(records: Iterable[Tier0Record], seed: int = 42) -> FloatArray:
    """Residuals of ``token_index == 0`` only — the widest distributions, most sensitive."""
    return pit_residuals([r for r in records if r.token_index == 0], seed=seed)


def lag1_autocorr(records: Iterable[Tier0Record], seed: int = 42) -> dict[str, Any]:
    """Lag-1 Pearson autocorrelation of residuals, pairs formed **within** a sequence only."""
    left: list[float] = []
    right: list[float] = []
    for seq in group_by_sample(records).values():
        res = pit_residuals(seq, seed=seed)
        if res.size >= 2:
            left.extend(res[:-1].tolist())
            right.extend(res[1:].tolist())
    n = len(left)
    if n < 3:
        return {"r": None, "n_pairs": n, "p_value": None}
    a = np.asarray(left)
    b = np.asarray(right)
    if a.std() == 0.0 or b.std() == 0.0:
        return {"r": 0.0, "n_pairs": n, "p_value": 1.0}
    res = sp.pearsonr(a, b)
    return {"r": float(res.statistic), "n_pairs": n, "p_value": float(res.pvalue)}


def u_vs_r_agreement(records: Iterable[Tier0Record]) -> dict[str, Any]:
    """Fraction of tokens whose consumed ``u`` lies in the chosen token's CDF bracket.

    An inverse-CDF sampler satisfies ``cdf_lo ≤ u < cdf_hi`` for every token; a
    fraction below 1 means the sampler did not select by the logged ``u`` and
    the T0-a residuals cannot be trusted. Reported with the mean absolute gap
    between ``u`` and the nearest bracket edge for the misses.
    """
    hits = 0
    n = 0
    gaps: list[float] = []
    for r in _bracketed(records):
        if r.u is None or r.cdf_lo is None or r.cdf_hi is None:
            continue
        n += 1
        if r.cdf_lo <= r.u <= r.cdf_hi:
            hits += 1
        else:
            gaps.append(min(abs(r.u - r.cdf_lo), abs(r.u - r.cdf_hi)))
    return {
        "agreement_fraction": (hits / n) if n else None,
        "n": n,
        "n_misses": n - hits,
        "mean_miss_gap": (float(np.mean(gaps)) if gaps else 0.0) if n else None,
    }


def _ks_uniform(x: FloatArray) -> dict[str, Any]:
    if x.size == 0:
        return {"statistic": None, "p_value": None, "n": 0}
    res = sp.kstest(x, "uniform", method="exact" if x.size <= 10_000 else "asymp")
    return {"statistic": float(res.statistic), "p_value": float(res.pvalue), "n": int(x.size)}


def _ks_two_sample(x: FloatArray, y: FloatArray) -> dict[str, Any]:
    if x.size == 0 or y.size == 0:
        return {"statistic": None, "p_value": None, "n_x": int(x.size), "n_y": int(y.size)}
    res = sp.ks_2samp(x, y, alternative="two-sided", method="auto")
    return {
        "statistic": float(res.statistic),
        "p_value": float(res.pvalue),
        "n_x": int(x.size),
        "n_y": int(y.size),
    }


def pit_inputs(records_by_arm: RecordsByArm, seed: int = 42) -> dict[str, FloatArray]:
    """Residual arrays per arm — what ``analysis`` feeds to AD / two-sample tests."""
    return {arm: pit_residuals(recs, seed=seed) for arm, recs in records_by_arm.items()}


def t0a_table(
    records_by_arm: RecordsByArm,
    contrasts: Mapping[str, tuple[str | Sequence[str], str | Sequence[str]]] | None = None,
    *,
    seed: int = 42,
    tier0_source: Tier0Source | None = None,
) -> Table:
    """T0-a per-arm uniformity checks and two-sample KS for each named contrast.

    ``contrasts`` maps a label to ``(arms_a, arms_b)``; either side may be one
    arm or a list of arms pooled together (``"qrng"`` vs ``["prng_1", "prng_7",
    "prng_42"]`` is the Study 1 primary). The Anderson–Darling endpoints are
    computed by ``analysis`` from :func:`pit_inputs` and are not in this table.
    """
    blocked = _check_source("pit_ks_one_sample", tier0_source)
    if blocked is not None:
        return {**blocked, "card": "tier0_pit_residuals"}
    # One told cell per (card, prompt, epoch): seeded arms share the stream across cells.
    records_by_arm = {arm: independent_streams(recs) for arm, recs in records_by_arm.items()}
    arms: dict[str, Any] = {}
    for arm, recs in records_by_arm.items():
        bracketed = _bracketed(recs)
        residuals = pit_residuals(bracketed, seed=seed)
        u = np.array([r.u for r in bracketed if r.u is not None], dtype=np.float64)
        arms[arm] = {
            "n_tokens": len(recs),
            "n_bracketed": len(bracketed),
            "residual_mean": float(residuals.mean()) if residuals.size else None,
            "residual_var": float(residuals.var()) if residuals.size else None,
            "pit_ks_one_sample": _ks_uniform(residuals),
            "pit_first_token_ks": _ks_uniform(first_token_residuals(bracketed, seed=seed)),
            "pit_lag1_autocorr": lag1_autocorr(bracketed, seed=seed),
            "u_uniformity": _ks_uniform(u),
            "u_vs_r_agreement": u_vs_r_agreement(bracketed),
        }
    any_bracketed = any(a["n_bracketed"] > 0 for a in arms.values())
    out: Table = {
        "card": "tier0_pit_residuals",
        "endpoint": "pit_ks_two_sample",
        "requires_tier0": requirement_for("pit_ks_two_sample"),
        "available": any_bracketed,
        "seed": seed,
        "expected_under_h0": {"residual_mean": 0.5, "residual_var": 1 / 12, "lag1_r": 0.0},
        "arms": arms,
        "contrasts": {},
    }
    if not any_bracketed:
        out["reason"] = "no record carries u / cdf_lo / cdf_hi (engine token log absent)"
    for label, (a, b) in (contrasts or {}).items():
        ra = pit_residuals(_select(records_by_arm, a), seed=seed)
        rb = pit_residuals(_select(records_by_arm, b), seed=seed)
        out["contrasts"][label] = {
            "a": _label(a),
            "b": _label(b),
            "pit_ks_two_sample": _ks_two_sample(ra, rb),
        }
    return out


# --------------------------------------------------------------------------- #
# T0-b  MMD-Hamming inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MmdInputs:
    """Padded token-id matrices and prompt keys for ``analysis.mmd.mmd_hamming_permutation_test``."""

    seqs_a: IntArray
    seqs_b: IntArray
    prompts_a: list[str]
    prompts_b: list[str]
    length: int
    sample_refs_a: list[str]
    sample_refs_b: list[str]


def _sequences(records: Iterable[Tier0Record]) -> tuple[list[str], list[str], list[list[int]]]:
    refs: list[str] = []
    prompts: list[str] = []
    seqs: list[list[int]] = []
    for ref, seq in group_by_sample(records).items():
        refs.append(ref)
        prompts.append(parse_sample_ref(ref).prompt_key)
        seqs.append([r.token_id for r in seq])
    return refs, prompts, seqs


def _pad(seqs: Sequence[Sequence[int]], length: int) -> IntArray:
    out = np.full((len(seqs), length), PAD_TOKEN, dtype=np.int64)
    for i, seq in enumerate(seqs):
        row = np.asarray(seq, dtype=np.int64)[:length]
        out[i, : row.size] = row
    return out


def mmd_inputs(
    records_by_arm: RecordsByArm,
    arms_a: str | Sequence[str],
    arms_b: str | Sequence[str],
    length: int | None = None,
) -> MmdInputs:
    """Both arms' sequences right-padded to one shared ``L`` (pads compare equal, as upstream).

    Prompts are keyed arm-independently (``card/prompt[/told]``) so the
    within-prompt permutation pairs the same prompt across arms. ``length``
    defaults to the longest sequence in either arm.
    """
    refs_a, prompts_a, seqs_a = _sequences(_select(records_by_arm, arms_a))
    refs_b, prompts_b, seqs_b = _sequences(_select(records_by_arm, arms_b))
    if not seqs_a or not seqs_b:
        raise ValueError("both arms need at least one sample for MMD inputs")
    longest = max(len(s) for s in [*seqs_a, *seqs_b])
    length = longest if length is None else length
    if length <= 0:
        raise ValueError("sequences must contain at least one token")
    return MmdInputs(
        seqs_a=_pad(seqs_a, length),
        seqs_b=_pad(seqs_b, length),
        prompts_a=prompts_a,
        prompts_b=prompts_b,
        length=length,
        sample_refs_a=refs_a,
        sample_refs_b=refs_b,
    )


def t0b_table(records_by_arm: RecordsByArm, *, tier0_source: Tier0Source | None = None) -> Table:
    """Descriptive side of T0-b (per-arm sample/prompt counts and lengths).

    The permutation statistic itself lives in ``analysis.mmd`` (heavy, seeded,
    ``b ≥ 2000``) and is appended to this table by the analysis step.
    """
    blocked = _check_source("mmd_hamming", tier0_source)
    if blocked is not None:
        return {**blocked, "card": "tier0_mmd_hamming"}
    arms: dict[str, Any] = {}
    for arm, recs in records_by_arm.items():
        _refs, prompts, seqs = _sequences(recs)
        lengths = [len(s) for s in seqs]
        arms[arm] = {
            "n_samples": len(seqs),
            "n_prompts": len(set(prompts)),
            "mean_length": float(np.mean(lengths)) if lengths else None,
            "max_length": max(lengths) if lengths else 0,
        }
    return {
        "card": "tier0_mmd_hamming",
        "endpoint": "mmd_hamming",
        "requires_tier0": requirement_for("mmd_hamming"),
        "available": any(a["n_samples"] > 0 for a in arms.values()),
        "kernel": "hamming on right-padded token ids, pad compares equal",
        "arms": arms,
    }


# --------------------------------------------------------------------------- #
# T0-c  Free logprob endpoints
# --------------------------------------------------------------------------- #

T0C_ENDPOINTS: tuple[str, ...] = CARD_ENDPOINTS["tier0_logprob_endpoints"]


def sample_features(seq: Sequence[Tier0Record]) -> dict[str, float | int | None]:
    """The Blind-PRNG-Hijacking detector set for one sample (``None`` where the field is absent)."""
    if not seq:
        raise ValueError("a sample needs at least one token")
    n = len(seq)
    logprobs = np.array([r.logprob for r in seq], dtype=np.float64)
    ranks = np.array([r.rank_raw for r in seq], dtype=np.float64)
    ent_post = [r.entropy_post for r in seq if r.entropy_post is not None]
    p_post = [r.p_post for r in seq if r.p_post is not None]
    ids = [r.token_id for r in seq]
    return {
        "mean_logprob": float(logprobs.mean()),
        "mean_entropy": float(np.mean(ent_post)) if len(ent_post) == n else None,
        "mean_entropy_raw_top20": float(np.mean([r.entropy_raw_top20 for r in seq])),
        "mean_rank": float(ranks.mean()),
        "argmax_fraction": float(np.mean(ranks == 0)),
        "tail_fraction_p001": (
            float(np.mean([p < TAIL_P for p in p_post])) if len(p_post) == n else None
        ),
        "sequence_length": n,
        "repetition_rate": 1.0 - len(set(ids)) / n,
    }


def sample_feature_rows(records_by_arm: RecordsByArm) -> list[dict[str, Any]]:
    """One row per sample: ``arm_id, sample_ref, prompt_key, epoch`` + :func:`sample_features`."""
    rows: list[dict[str, Any]] = []
    for arm, recs in records_by_arm.items():
        for ref, seq in group_by_sample(recs).items():
            parts = parse_sample_ref(ref)
            rows.append(
                {
                    "arm_id": arm,
                    "sample_ref": ref,
                    "prompt_key": parts.prompt_key,
                    "epoch": parts.epoch,
                    **sample_features(seq),
                }
            )
    return rows


def prompt_feature_rows(records_by_arm: RecordsByArm) -> list[dict[str, Any]]:
    """Per-``(arm, prompt)`` means over epochs — the unit of the paired permutation test."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in sample_feature_rows(records_by_arm):
        groups.setdefault((row["arm_id"], row["prompt_key"]), []).append(row)
    out: list[dict[str, Any]] = []
    for (arm, key), rows in groups.items():
        agg: dict[str, Any] = {"arm_id": arm, "prompt_key": key, "n_samples": len(rows)}
        for ep in (*T0C_ENDPOINTS, "mean_entropy_raw_top20"):
            values = [r[ep] for r in rows if r[ep] is not None]
            agg[ep] = float(np.mean(values)) if len(values) == len(rows) else None
        out.append(agg)
    return out


def rank_histogram(records: Iterable[Tier0Record], max_rank: int = RANK_HIST_MAX) -> list[int]:
    """Counts of ``rank_raw`` in ``0 … max_rank-1`` plus an overflow bin for ``>= max_rank``."""
    counts = [0] * (max_rank + 1)
    for r in records:
        counts[min(r.rank_raw, max_rank)] += 1
    return counts


def topk_coverage(records: Iterable[Tier0Record], top_k: int) -> float | None:
    """Share of sampled tokens that fall inside the returned top-``top_k`` (``rank_raw < top_k``).

    On the ``api_logprobs`` path ``rank_raw`` equals ``top_k`` exactly when the
    sampled token was absent from the returned alternatives, so this is the
    fraction of tokens the rank-based detectors see in full. It tells an
    operator whether the configured depth (``QR_EVALS_TOP_LOGPROBS``) is deep
    enough; a value below 1 means raising the depth, not discarding tokens.
    ``None`` for no records or ``top_k <= 0``.
    """
    if top_k <= 0:
        return None
    ranks = [r.rank_raw for r in records]
    if not ranks:
        return None
    return float(np.mean([rank < top_k for rank in ranks]))


def t0c_table(
    records_by_arm: RecordsByArm,
    *,
    tier0_source: Tier0Source | None = None,
    top_k: int | None = None,
) -> Table:
    """T0-c per-arm means of every detector, with per-endpoint availability.

    ``top_k`` is the run's recorded ``top_logprobs`` depth; when given, each arm
    also reports :func:`topk_coverage` and the labels name the depth.
    """
    prompt_rows = prompt_feature_rows(records_by_arm)
    arms: dict[str, Any] = {}
    for arm, recs in records_by_arm.items():
        mine = [r for r in prompt_rows if r["arm_id"] == arm]
        summary: dict[str, Any] = {
            "n_samples": sum(r["n_samples"] for r in mine),
            "n_prompts": len(mine),
            "n_tokens": len(recs),
            "rank_histogram": rank_histogram(recs),
            "mean_entropy_raw_top20": (
                float(np.mean([r["mean_entropy_raw_top20"] for r in mine])) if mine else None
            ),
        }
        if top_k is not None:
            summary["topk_coverage"] = topk_coverage(recs, top_k)
        for ep in T0C_ENDPOINTS:
            blocked = _check_source(ep, tier0_source)
            values = [r[ep] for r in mine if r[ep] is not None]
            if blocked is not None:
                summary[ep] = {"available": False, "reason": blocked["reason"]}
            elif not values or len(values) != len(mine):
                summary[ep] = {
                    "available": False,
                    "reason": "field absent from the records (engine token log needed)",
                }
            else:
                summary[ep] = {"available": True, "mean": float(np.mean(values))}
        arms[arm] = summary
    return {
        "card": "tier0_logprob_endpoints",
        "endpoint": "mean_logprob",
        "requires_tier0": requirement_for("mean_logprob"),
        "available": any(a["n_samples"] > 0 for a in arms.values()),
        "labels": {
            "mean_entropy_raw_top20": ENTROPY_RAW_TOP20_LABEL,
            "rank_histogram": f"rank_raw counts 0..{RANK_HIST_MAX - 1}, last bin >= {RANK_HIST_MAX}",
            "tail_fraction_p001": f"fraction of tokens with post-truncation p < {TAIL_P}",
            **(
                {
                    "top_logprobs": top_k,
                    "topk_coverage": (
                        f"share of sampled tokens inside the returned top-{top_k} "
                        f"(rank_raw < {top_k}); below 1 means the depth should be raised"
                    ),
                }
                if top_k is not None
                else {}
            ),
        },
        "arms": arms,
        "prompt_rows": prompt_rows,
    }


# --------------------------------------------------------------------------- #
# T4-l  Stream deviation during observed windows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SecondBin:
    """Per-second bit fraction: ``successes`` = tokens with ``u >= 0.5`` out of ``trials``."""

    session_id: str | None
    window: str
    second: int
    successes: int
    trials: int

    @property
    def z(self) -> float:
        return (self.successes - 0.5 * self.trials) / math.sqrt(0.25 * self.trials)


def second_bins(records: Iterable[Tier0Record]) -> list[SecondBin]:
    """Bin ``u`` values by ``(session_id, window, floor(timestamp_ns / 1e9))``, ordered by time.

    The bit is ``u >= 0.5`` — one honest bit per consumed uniform — so under H0
    the per-second count is Binomial(n, ½) and its z-score is
    ``(k − n/2) / sqrt(n/4)`` (the PHASE_K bit-fraction statistic).
    """
    acc: dict[tuple[str | None, str, int], list[int]] = {}
    for r in records:
        if r.u is None:
            continue
        key = (r.session_id, r.window, r.timestamp_ns // NS_PER_SECOND)
        cell = acc.setdefault(key, [0, 0])
        cell[0] += int(r.u >= 0.5)
        cell[1] += 1
    return [
        SecondBin(sid, win, sec, k, n)
        for (sid, win, sec), (k, n) in sorted(acc.items(), key=lambda kv: (kv[0][2], str(kv[0])))
    ]


def _stouffer(z: FloatArray) -> dict[str, Any]:
    if z.size == 0:
        return {"z": None, "p_value": None, "n_seconds": 0}
    combined = float(z.sum() / math.sqrt(z.size))
    return {
        "z": combined,
        "p_value": float(2 * sp.norm.sf(abs(combined))),
        "n_seconds": int(z.size),
    }


def _contrast_z(z_sup: FloatArray, z_uns: FloatArray) -> float | None:
    if z_sup.size == 0 or z_uns.size == 0:
        return None
    return float(
        (z_sup.sum() / math.sqrt(z_sup.size) - z_uns.sum() / math.sqrt(z_uns.size)) / math.sqrt(2)
    )


def _window_permutation(
    z: FloatArray, is_supervised: NDArray[np.bool_], n_permutations: int, rng: np.random.Generator
) -> dict[str, Any]:
    """Shuffle window labels across seconds (within the session); statistic = Stouffer contrast."""
    observed = _contrast_z(z[is_supervised], z[~is_supervised])
    if observed is None or n_permutations < 1:
        return {"statistic": observed, "p_value": None, "n_permutations": 0}
    extreme = 0
    for _ in range(n_permutations):
        perm = rng.permutation(is_supervised)
        null = _contrast_z(z[perm], z[~perm])
        if null is not None and abs(null) >= abs(observed) - 1e-12:
            extreme += 1
    return {
        "statistic": observed,
        "p_value": min(1.0, (extreme + 1) / (n_permutations + 1)),
        "n_permutations": int(n_permutations),
    }


def stream_deviation_table(
    records: Iterable[Tier0Record],
    *,
    n_permutations: int = 2000,
    seed: int = 42,
    tier0_source: Tier0Source | None = None,
    envelope_level: float | None = None,
) -> Table:
    """T4-l per session: Stouffer-Z of supervised vs unsupervised seconds, cumulative deviation, permutation p.

    ``break`` seconds are reported but excluded from the contrast (they are
    neither observed nor matched control). The cumulative-deviation series is
    per second, ``Σ (k_s − n_s/2)``, with the ``±1.96·sqrt(Σ n_s/4)`` envelope
    (GCP/PEAR plot). Records without ``u`` contribute nothing.

    ``envelope_level`` is for a study that registered its own interval level:
    the envelope is then the two-sided band at that level, written as
    ``envelope`` beside ``envelope_level``. Without it the table carries the
    ``envelope_95`` it always did.
    """
    half_width = (
        1.96 if envelope_level is None else float(sp.norm.ppf(1 - (1 - envelope_level) / 2))
    )
    blocked = _check_source("stouffer_z_supervised_vs_unsupervised", tier0_source)
    if blocked is not None:
        return {**blocked, "card": "stream_deviation_windows"}
    bins = second_bins(records)
    rng = np.random.default_rng(seed)
    sessions: dict[str, Any] = {}
    for sid in sorted({b.session_id for b in bins}, key=str):
        mine = [b for b in bins if b.session_id == sid]
        z = np.array([b.z for b in mine], dtype=np.float64)
        windows = np.array([b.window for b in mine])
        sup = windows == "supervised"
        uns = windows == "unsupervised"
        contrast_mask = sup | uns
        excess = np.array([b.successes - 0.5 * b.trials for b in mine], dtype=np.float64)
        trials = np.array([b.trials for b in mine], dtype=np.float64)
        cumdev = np.cumsum(excess)
        envelope = half_width * np.sqrt(np.cumsum(trials) * 0.25)
        contrast = _contrast_z(z[sup], z[uns])
        sessions[str(sid)] = {
            "n_seconds": len(mine),
            "n_tokens": int(trials.sum()),
            "by_window": {
                w: _stouffer(z[windows == w]) for w in ("supervised", "break", "unsupervised")
            },
            "stouffer_z_supervised_vs_unsupervised": {
                "z": contrast,
                "p_value": None if contrast is None else float(2 * sp.norm.sf(abs(contrast))),
            },
            "window_permutation_p": _window_permutation(
                z[contrast_mask], sup[contrast_mask], n_permutations, rng
            ),
            "cumulative_deviation": {
                "seconds": [b.second for b in mine],
                "window": [b.window for b in mine],
                "cumdev": cumdev.tolist(),
                **(
                    {"envelope_95": envelope.tolist()}
                    if envelope_level is None
                    else {"envelope": envelope.tolist(), "envelope_level": envelope_level}
                ),
                "terminal_z": (
                    float(cumdev[-1] / math.sqrt(trials.sum() * 0.25)) if len(mine) else None
                ),
            },
        }
    return {
        "card": "stream_deviation_windows",
        "endpoint": "stouffer_z_supervised_vs_unsupervised",
        "requires_tier0": requirement_for("stouffer_z_supervised_vs_unsupervised"),
        "available": bool(bins),
        **({} if bins else {"reason": "no record carries u with an engine timestamp"}),
        "bit": "u >= 0.5",
        "bin": "1 s of engine timestamp_ns",
        "seed": seed,
        "sessions": sessions,
    }
