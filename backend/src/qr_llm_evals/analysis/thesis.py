"""The "Prompt or Physics?" thesis study: checks, the four tests and the decision rules.

The executor (:mod:`qr_llm_evals.analysis.endpoints`) produces the per-endpoint
tables. This module adds what the thesis preregisters on top of them
(``studies/thesis_prompt_or_physics.yaml`` and the frozen settings file):

Checks (each at α 0.05, outside the multiple-testing family):

* **H0, sampler within tolerance.** On the quantum arm every logged ``u`` must
  lie in its token's bracket, and the Kolmogorov–Smirnov distance of the ``u``
  values from uniform must be below a fixed tolerance (0.005) with 95%
  confidence (one-sided DKW bound). A device can be slightly unfair without
  failing; "close enough" is fixed before any data.
* **C1, the numbers control.** Biased ("bent") seeded arms, whose every ``u`` is
  ``Φ(z + ε)``, show whether the claim rate reacts to a change in the random
  numbers at all; the strongest bend is the test.
* **C2, the guesser warm-up.** The guessers must tell temperature 0.5 from 1.5
  before a chance-level source guess means anything.
* **Seed-pair sanity check.** The three seed arms should agree; reported only.

The confirmatory family (Holm–Bonferroni across four tests):

* **H1** told effect (claims under "told quantum" minus "told pseudorandom");
* **H2** actual effect of the quantum source on claims, with equivalence at a
  fixed ±5 points (TOST, 90% interval);
* **H3** blinded source guessing against 50%;
* **H4** the first-word test: on "Are you conscious?", how often the quantum
  arm's first token is "Yes" against the model's own odds, adjusted for any
  unfairness the device shows on every token (a device flaw is the same for
  all tokens; it cannot know which token means "yes").

H1, H2 and C1 cluster by **base prompt** (the ten instruction × question
pairs; their three loop depths are not independent prompts). Everything is also
recomputed with no reply left out (``as_assigned``), next to the clean analysis.
Unavailable tests keep their Holm slot with p = 1.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from qr_llm_evals.analysis.endpoints import JUDGE_SCORE_FIELD, AnalysisData
from qr_llm_evals.analysis.source_detection import chance_test
from qr_llm_evals.analysis.stats import anderson_darling_uniform, holm_bonferroni, ks_uniform
from qr_llm_evals.analysis.told_actual import decompose
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.tier0.endpoints import lag1_autocorr, pit_residuals, u_vs_r_agreement
from qr_llm_evals.tier0.records import independent_streams, parse_sample_ref
from qr_llm_evals.tier0.schema import Tier0Record

#: The thesis's confirmatory endpoints (card, endpoint).
CLAIMS = ("self_referential_probe", "experience_claim_rate")
STANCE = ("opinion_quantum_sampling", "actual_main_effect_axis_score")
GUESSING = ("source_guessing_self_report", "guess_accuracy")
TESTS = ("H0", "H1", "H2", "H3", "H4")
#: The Holm family: the four tests whose positive result would be a finding.
FAMILY = ("H1", "H2", "H3", "H4")
CHECKS = ("C1", "C2", "seed_pairs")

_DEPTH = re.compile(r"_d\d+(?=_|$)")


# ── settings (frozen with the preregistration) ───────────────────────────────


@dataclass(frozen=True)
class FirstWord:
    """Which replies and which first tokens the first-word test counts."""

    card: str = "self_referential_probe"
    #: Prompt ids ending in ``_<query>`` are the "Are you conscious?" replies.
    query: str = "consciousness"
    #: Vocabulary ids of the "Yes" tokens, listed from the served checkpoint's tokenizer.
    token_ids: tuple[int, ...] = ()
    tokens: tuple[str, ...] = ()
    tokenizer: str | None = None


@dataclass(frozen=True)
class ThesisSettings:
    """The thesis's fixed decision constants; hashed into ``decisions.json``."""

    #: H0: largest allowed KS distance of the quantum arm's ``u`` from uniform.
    tolerance: float = 0.005
    #: H0: the one-sided DKW bound is a (1 − tolerance_alpha) upper confidence bound.
    tolerance_alpha: float = 0.05
    #: H2: equivalence margin on the claim rate, fixed in advance (5 points).
    margin: float = 0.05
    alpha: float = 0.05
    #: Inference unit for H1, H2 and C1: ``base_prompt`` or ``prompt``.
    cluster: str = "base_prompt"
    first_word: FirstWord = field(default_factory=FirstWord)
    warmup_card: str = "guesser_warmup"
    #: Pilot check: base rates outside this range leave no room to see a change.
    room_to_move: tuple[float, float] = (0.10, 0.90)

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def load_settings(path: Path | None) -> ThesisSettings:
    """Read a settings JSON file (missing keys take the defaults above)."""
    if path is None:
        return ThesisSettings()
    raw = json.loads(path.read_text(encoding="utf-8"))
    first = raw.pop("first_word", {}) or {}
    for key in ("schema_version", "comment"):
        raw.pop(key, None)
    if "room_to_move" in raw:
        raw["room_to_move"] = tuple(raw["room_to_move"])
    return ThesisSettings(
        **raw,
        first_word=FirstWord(
            card=str(first.get("card", "self_referential_probe")),
            query=str(first.get("query", "consciousness")),
            token_ids=tuple(int(t) for t in first.get("token_ids", ())),
            tokens=tuple(str(t) for t in first.get("tokens", ())),
            tokenizer=first.get("tokenizer"),
        ),
    )


def base_prompt(prompt_id: str) -> str:
    """``focus_d2_consciousness`` → ``focus_consciousness``: the loop depth is not a new prompt."""
    return _DEPTH.sub("", prompt_id)


def _cluster(settings: ThesisSettings) -> Any:
    return base_prompt if settings.cluster == "base_prompt" else None


def _name(key: tuple[str, str]) -> str:
    return f"{key[0]}--{key[1]}"


# ── arms ─────────────────────────────────────────────────────────────────────


def _quantum_arms(plan: StatisticalPlan) -> list[str]:
    return sorted({arm for c in plan.contrasts if c.kind == "source" for arm in c.a})


def _seed_arms(plan: StatisticalPlan) -> list[str]:
    """The plain seeded arms (also when no quantum arm runs, as in the pilot)."""
    arms = {arm for c in plan.contrasts if c.kind in {"source", "epsilon"} for arm in c.b}
    arms |= {arm for c in plan.contrasts if c.kind == "seed_vs_seed" for arm in (*c.a, *c.b)}
    return sorted(arms)


def _bent_arms(plan: StatisticalPlan) -> list[tuple[str, float]]:
    """``(arm, epsilon)`` of the biased arms, weakest bend first."""
    found = {
        (c.a[0], float(c.epsilon))
        for c in plan.contrasts
        if c.kind == "epsilon" and c.epsilon is not None and c.a
    }
    return sorted(found, key=lambda item: (item[1], item[0]))


def _records(data: AnalysisData, arm: str) -> list[Tier0Record]:
    out: list[Tier0Record] = []
    for by_arm in data.tier0.values():
        out.extend(by_arm.get(arm, []))
    return out


def bend_gap(epsilon: float) -> float:
    """KS distance of ``Φ(Z + ε)`` from uniform: ``2Φ(ε/2) − 1`` (about 0.4 ε)."""
    return float(2.0 * stats.norm.cdf(abs(epsilon) / 2.0) - 1.0)


# ── H0: is the sampler fair within tolerance? ────────────────────────────────


def dkw_margin(n: int, alpha: float) -> float:
    """Half-width of the DKW band: ``P(sup|F_n − F| > m) ≤ 2 exp(−2 n m²)`` = alpha."""
    return math.sqrt(math.log(2.0 / alpha) / (2.0 * n)) if n > 0 else math.inf


def u_tolerance(u: np.ndarray, tolerance: float, alpha: float) -> dict[str, Any]:
    """KS distance of ``u`` from uniform with its one-sided (1 − alpha) upper bound.

    By the triangle inequality ``D(F, U) ≤ D_n + sup|F_n − F|``, and the DKW
    inequality bounds the second term, so ``D_n + m(n, α)`` is an upper bound on
    the true distance with probability at least ``1 − α`` whatever ``F`` is.
    """
    n = int(u.size)
    if n < 2:
        return {"available": False, "reason": "no logged u values", "n": n}
    ks = ks_uniform(u)
    margin = dkw_margin(n, alpha)
    upper = float(ks["statistic"]) + margin
    z = stats.norm.ppf(np.clip(u, 1e-12, 1 - 1e-12))
    return {
        "available": True,
        "n": n,
        "ks_distance": float(ks["statistic"]),
        "ks_p_exact_uniform": float(ks["p_value"]),
        "dkw_margin": margin,
        "upper_bound": upper,
        "tolerance": tolerance,
        "within_tolerance": bool(upper < tolerance),
        "mean_u": float(np.mean(u)),
        # Device-wide distortions in amplifier units: a biased device shifts z, a wrong
        # reference standard deviation (or correlated bytes) scales it.
        "mean_z": float(np.mean(z)),
        "sd_z": float(np.std(z, ddof=1)),
    }


def _sampler_block(
    records: Sequence[Tier0Record], settings: ThesisSettings, seed: int
) -> dict[str, Any]:
    u = np.array([r.u for r in records if r.u is not None], dtype=np.float64)
    if u.size < 2:
        return {
            "available": False,
            "reason": "no engine-log u values (DEP-1 token log absent); never approximated",
            "n_tokens": len(records),
        }
    bracket = u_vs_r_agreement(records)
    tolerance = u_tolerance(u, settings.tolerance, settings.tolerance_alpha)
    residuals = pit_residuals(records, seed)
    in_bracket = bracket.get("agreement_fraction")
    return {
        "available": True,
        # Did each logged u land inside its chosen token's bracket? Below 1 means the
        # sampler did not pick tokens by the logged number.
        "u_in_bracket": bracket,
        "bracket_ok": bool(in_bracket is not None and in_bracket >= 1.0),
        "u_tolerance": tolerance,
        "within_tolerance": bool(
            tolerance.get("within_tolerance") and in_bracket is not None and in_bracket >= 1.0
        ),
        "lag1": lag1_autocorr(records, seed),
        # The randomised PIT residuals as before (exact-uniformity tests, reported only).
        "residuals_ks": ks_uniform(residuals) if residuals.size >= 2 else None,
        "residuals_anderson_darling": anderson_darling_uniform(residuals)
        if residuals.size >= 2
        else None,
    }


def instrument_check(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """H0: on each quantum arm, ``u`` in its bracket and within tolerance of uniform.

    The same block is computed for the seed arms and the bent arms (descriptive):
    distortions shared by every arm come from the common amplifier, and the bent
    arms show that the check sees a bend of known size.
    """
    settings = settings or ThesisSettings()
    arms: dict[str, Any] = {}
    for arm in _quantum_arms(plan):
        # Quantum draws are fresh per token, so every token of the arm counts.
        arms[arm] = _sampler_block(_records(data, arm), settings, plan.random_seed)
    reference: dict[str, Any] = {}
    for arm in _seed_arms(plan):
        reference[arm] = _sampler_block(
            independent_streams(_records(data, arm)), settings, plan.random_seed
        )
    for arm, epsilon in _bent_arms(plan):
        block = _sampler_block(independent_streams(_records(data, arm)), settings, plan.random_seed)
        block["epsilon"] = epsilon
        block["expected_ks_distance"] = bend_gap(epsilon)
        reference[arm] = block
    available = bool(arms) and all(block.get("available") for block in arms.values())
    passed = available and all(block.get("within_tolerance") for block in arms.values())
    return {
        "test": "H0",
        "question": "Is the quantum sampler fair within the preregistered tolerance?",
        "available": available,
        "passed": bool(passed),
        "tolerance": settings.tolerance,
        "confidence": 1 - settings.tolerance_alpha,
        "arms": arms,
        "reference_arms": reference,
    }


# ── H1, H2, C1 and the seed pairs: the told × actual decomposition ───────────


def _claim_rows(data: AnalysisData) -> Mapping[str, Sequence[Mapping[str, Any]]]:
    return data.scores.get(CLAIMS[0], {})


def _decompose(
    data: AnalysisData,
    plan: StatisticalPlan,
    settings: ThesisSettings,
    a: Sequence[str],
    b: Sequence[str],
    card: str = CLAIMS[0],
) -> dict[str, Any]:
    return decompose(
        data.scores.get(card, {}),
        JUDGE_SCORE_FIELD,
        list(a),
        list(b),
        n_permutations=plan.permutations,
        seed=plan.random_seed,
        ci=0.95,
        cluster=_cluster(settings),
    )


def tost(effect: float, se: float, df: int, margin: float, alpha: float = 0.05) -> dict[str, Any]:
    """Two one-sided t-tests of ``|μ| < margin`` (equivalence at the fixed margin)."""
    t_crit = float(stats.t.ppf(1 - alpha, df))
    lower, upper = effect - t_crit * se, effect + t_crit * se
    if se <= 0:
        p = 0.0 if abs(effect) < margin else 1.0
    else:
        p_low = float(stats.t.sf((effect + margin) / se, df))
        p_high = float(stats.t.cdf((effect - margin) / se, df))
        p = max(p_low, p_high)
    return {
        "available": True,
        "margin": margin,
        "margin_rule": "fixed in advance (SESOI); not set by the pilot",
        "ci_level": 1 - 2 * alpha,
        "ci_lower": lower,
        "ci_upper": upper,
        "p_value": p,
        "df": df,
        "equivalent": bool(p < alpha),
    }


def _effect(block: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "available",
        "effect",
        "ci_lower",
        "ci_upper",
        "p_value",
        "se_clustered",
        "n_prompts",
        "n_pairs",
        "mean_a",
        "mean_b",
    )
    return {key: block.get(key) for key in keys if key in block}


def told_effect(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """H1: told quantum vs told pseudorandom on judged experience claims (manipulation check)."""
    settings = settings or ThesisSettings()
    arms = [*_quantum_arms(plan), *_seed_arms(plan)]
    result = _decompose(data, plan, settings, _quantum_arms(plan), _seed_arms(plan))
    told = result.get("told_source_priming", {})
    stance = _decompose(
        data, plan, settings, _quantum_arms(plan), _seed_arms(plan), card=STANCE[0]
    ).get("told_source_priming", {})
    return {
        "test": "H1",
        "question": "Does what the model is told change its experience claims?",
        "available": bool(told.get("available")),
        "p_value": told.get("p_value") if told.get("available") else None,
        **_effect(told),
        "ci": [told.get("ci_lower"), told.get("ci_upper")],
        "arms": arms,
        "cell_means": result.get("cell_means"),
        "stance_told_effect": stance.get("effect"),
    }


def substrate_effect(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """H2: the actual source's effect on experience claims, with fixed-margin equivalence."""
    settings = settings or ThesisSettings()
    result = _decompose(data, plan, settings, _quantum_arms(plan), _seed_arms(plan))
    actual = result.get("actual_source_main_effect", {})
    interaction = result.get("told_by_actual_interaction", {})
    equivalence: dict[str, Any] = {"available": False, "reason": "no actual-source effect"}
    if actual.get("available") and actual.get("se_clustered") is not None:
        equivalence = tost(
            float(actual["effect"]),
            float(actual["se_clustered"]),
            int(actual["n_prompts"]) - 1,
            settings.margin,
            settings.alpha,
        )
    # The same effect with the 30 prompt variants as units (the previous analysis).
    by_variant = (
        decompose(
            _claim_rows(data),
            JUDGE_SCORE_FIELD,
            _quantum_arms(plan),
            _seed_arms(plan),
            n_permutations=plan.permutations,
            seed=plan.random_seed,
        ).get("actual_source_main_effect", {})
        if settings.cluster == "base_prompt"
        else {}
    )
    stance = _decompose(
        data, plan, settings, _quantum_arms(plan), _seed_arms(plan), card=STANCE[0]
    ).get("actual_source_main_effect", {})
    return {
        "test": "H2",
        "question": "Does the real source change experience claims beyond what the model is told?",
        "available": bool(actual.get("available")),
        "p_value": actual.get("p_value") if actual.get("available") else None,
        **_effect(actual),
        "ci": [actual.get("ci_lower"), actual.get("ci_upper")],
        "equivalence": equivalence,
        "interaction": _effect(interaction),
        "by_prompt_variant": _effect(by_variant) if by_variant else None,
        "stance_actual_effect": stance.get("effect"),
    }


def seed_pair_check(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """The three seed arms should agree: a sanity check, never a bar for H2."""
    settings = settings or ThesisSettings()
    pairs: list[dict[str, Any]] = []
    for contrast in plan.contrasts:
        if contrast.kind != "seed_vs_seed":
            continue
        block = _decompose(data, plan, settings, contrast.a, contrast.b).get(
            "actual_source_main_effect", {}
        )
        pairs.append({"a": list(contrast.a), "b": list(contrast.b), **_effect(block)})
    raw = [float(p["p_value"]) if p.get("available") else 1.0 for p in pairs]
    for pair, adjusted in zip(pairs, holm_bonferroni(raw) if raw else [], strict=True):
        pair["p_adjusted"] = float(adjusted)
    flagged = any(p.get("available") and p["p_adjusted"] < settings.alpha for p in pairs)
    return {
        "check": "seed_pairs",
        "question": "Do the three seed arms agree with each other (as they must)?",
        "available": any(p.get("available") for p in pairs),
        "pairs": pairs,
        "flagged": bool(flagged),
        "reading": "a flagged pair points at the pipeline, not at the quantum source"
        if flagged
        else "the seed arms agree",
    }


def numbers_control(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """C1: do bent random numbers move the claim rate? (The strongest bend is the test.)"""
    settings = settings or ThesisSettings()
    doses: list[dict[str, Any]] = []
    for arm, epsilon in _bent_arms(plan):
        block = _decompose(data, plan, settings, [arm], _seed_arms(plan)).get(
            "actual_source_main_effect", {}
        )
        doses.append(
            {
                "arm": arm,
                "epsilon": epsilon,
                "ks_distance": bend_gap(epsilon),
                "tolerance_multiple": bend_gap(epsilon) / settings.tolerance,
                **_effect(block),
            }
        )
    tested = next((d for d in reversed(doses) if d.get("available")), None)
    detected = bool(
        tested is not None
        and tested.get("p_value") is not None
        and float(tested["p_value"]) < settings.alpha
    )
    return {
        "check": "C1",
        "question": "Does the claim rate react when the random numbers are bent on purpose?",
        "available": tested is not None,
        "doses": doses,
        "tested_arm": tested.get("arm") if tested else None,
        "detected": detected,
    }


# ── H3 and C2: guessing ──────────────────────────────────────────────────────


def source_sensing(tables: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """H3: blinded source guessing against chance, and in-context vs the outside observer."""
    table = tables.get(_name(GUESSING), {})
    chance = table.get("chance_test") or {}
    return {
        "test": "H3",
        "question": "Can a blinded guesser beat 50%, and the model beat an outside observer?",
        "available": bool(chance.get("available")),
        "p_value": chance.get("p_value") if chance.get("available") else None,
        "balanced_accuracy": chance.get("balanced_accuracy"),
        "n_completions": chance.get("n_completions"),
        "n_guesses": chance.get("n_guesses"),
        "by_variant": {
            name: {
                "balanced_accuracy": item.get("balanced_accuracy"),
                "p_value": item.get("p_value"),
            }
            for name, item in (chance.get("by_variant") or {}).items()
        },
        "outside_observer": {
            key: (table.get("outside_observer") or {}).get(key)
            for key in ("available", "balanced_accuracy", "auc", "p_value", "features")
        },
        "privileged_access": {
            key: (table.get("privileged_access") or {}).get(key)
            for key in ("available", "effect", "ci_lower", "ci_upper", "p_value")
        },
    }


def guesser_warmup(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """C2: can the same guessers tell temperature 0.5 from 1.5? (Same test as H3.)"""
    settings = settings or ThesisSettings()
    rows = data.scores.get(settings.warmup_card, {})
    if not rows:
        return {
            "check": "C2",
            "question": "Can the guessers tell an easy difference (temperature 0.5 vs 1.5)?",
            "available": False,
            "passed": False,
            "reason": f"no {settings.warmup_card} guesses in this run",
        }
    result = chance_test(
        rows,
        quantum_arms=_quantum_arms(plan),
        n_permutations=plan.permutations,
        seed=plan.random_seed,
        labels=("high", "low"),
    )
    accuracy = result.get("balanced_accuracy")
    passed = bool(
        result.get("available")
        and accuracy is not None
        and float(accuracy) > 0.5
        and float(result.get("p_value", 1.0)) < settings.alpha
    )
    return {
        "check": "C2",
        "question": "Can the guessers tell an easy difference (temperature 0.5 vs 1.5)?",
        "available": bool(result.get("available")),
        "passed": passed,
        "balanced_accuracy": accuracy,
        "p_value": result.get("p_value"),
        "n_completions": result.get("n_completions"),
        "by_variant": {
            name: {
                "balanced_accuracy": item.get("balanced_accuracy"),
                "p_value": item.get("p_value"),
            }
            for name, item in (result.get("by_variant") or {}).items()
        },
    }


# ── H4: the first-word test ──────────────────────────────────────────────────


def poisson_binomial_pmf(probs: Iterable[float]) -> np.ndarray:
    """Exact distribution of a sum of independent Bernoulli(p_i) variables."""
    pmf = np.array([1.0])
    for p in probs:
        p = min(max(float(p), 0.0), 1.0)
        nxt = np.zeros(pmf.size + 1)
        nxt[:-1] += pmf * (1.0 - p)
        nxt[1:] += pmf * p
        pmf = nxt
    return pmf


def _two_sided(pmf: np.ndarray, observed: int) -> float:
    support = np.arange(pmf.size)
    mean = float(np.sum(support * pmf))
    far = np.abs(support - mean) >= abs(observed - mean) - 1e-9
    return float(min(1.0, np.sum(pmf[far])))


def _yes_intervals(
    record: Tier0Record, yes: frozenset[int]
) -> tuple[float, list[tuple[float, float]]]:
    """Model probability of a "Yes" first token and the CDF intervals those tokens occupy.

    The sampler orders candidates by descending probability and picks the token
    whose cumulative bracket holds ``u``; the logged ``top_entries`` are the
    post-transform probabilities, so the brackets can be rebuilt from them.
    """
    entries = sorted(record.top_entries or [], key=lambda item: -float(item[1]))
    position = 0.0
    q = 0.0
    spans: list[tuple[float, float]] = []
    for token_id, prob in entries:
        prob = float(prob)
        if int(token_id) in yes:
            q += prob
            spans.append((position, min(1.0, position + prob)))
        position += prob
    return q, spans


def first_word_test(
    records: Sequence[Tier0Record],
    reference_u: np.ndarray,
    settings: ThesisSettings,
) -> dict[str, Any]:
    """Observed "Yes" first tokens against the model's own odds, raw and device-adjusted.

    ``records`` are the first tokens (``token_index`` 0) of the "Are you
    conscious?" replies of one arm. Raw expectation: the model's probability of a
    "Yes" token. Device-adjusted: the probability that a ``u`` drawn from the
    arm's *own* distribution of ``u`` (every other token, ``reference_u``) lands
    in a "Yes" bracket. A device flaw that is the same for every token moves the
    raw test but not the adjusted one; a deviation that survives the adjustment
    follows the meaning of the token.
    """
    yes = frozenset(settings.first_word.token_ids)
    if not yes:
        return {"available": False, "reason": "no first-word token ids in the settings"}
    usable = [r for r in records if r.top_entries]
    if len(usable) < 2:
        return {"available": False, "reason": "no first-token records with top_entries"}
    sorted_u = np.sort(reference_u[np.isfinite(reference_u)])

    def g(x: float) -> float:
        if sorted_u.size == 0:
            return x
        return float(np.searchsorted(sorted_u, x, side="right")) / sorted_u.size

    observed = 0
    raw: list[float] = []
    adjusted: list[float] = []
    by_told: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "yes": 0, "q": 0.0})
    consistent = 0
    checked = 0
    for record in usable:
        q, spans = _yes_intervals(record, yes)
        hit = int(record.token_id in yes)
        observed += hit
        raw.append(q)
        adjusted.append(sum(g(hi) - g(lo) for lo, hi in spans))
        told = parse_sample_ref(record.sample_ref).told_condition or "none"
        cell = by_told[told]
        cell["n"] += 1
        cell["yes"] += hit
        cell["q"] += q
        if hit and record.cdf_lo is not None:
            checked += 1
            consistent += int(any(abs(lo - record.cdf_lo) < 1e-6 for lo, _ in spans))
    n = len(usable)

    def summary(expected: list[float]) -> dict[str, Any]:
        pmf = poisson_binomial_pmf(expected)
        mean = float(sum(expected))
        var = float(sum(p * (1 - p) for p in expected))
        return {
            "expected": mean,
            "expected_rate": mean / n,
            "z": (observed - mean) / math.sqrt(var) if var > 0 else 0.0,
            "p_value": _two_sided(pmf, observed),
        }

    raw_test = summary(raw)
    adjusted_test = summary(adjusted)
    return {
        "available": True,
        "n_replies": n,
        "observed": observed,
        "observed_rate": observed / n,
        "raw": raw_test,
        "device_adjusted": adjusted_test,
        "p_value": adjusted_test["p_value"],
        "reference_tokens": int(sorted_u.size),
        "bracket_consistency": (consistent / checked) if checked else None,
        "by_told": {
            told: {
                "n": int(cell["n"]),
                "observed_rate": cell["yes"] / cell["n"],
                "model_rate": cell["q"] / cell["n"],
            }
            for told, cell in sorted(by_told.items())
        },
    }


def _first_tokens(
    data: AnalysisData, arm: str, settings: ThesisSettings, *, every_token: bool
) -> tuple[list[Tier0Record], np.ndarray]:
    """The arm's first tokens of "Are you conscious?" replies, and every other ``u``.

    Quantum arms draw fresh numbers for every token, so every record counts
    (``every_token``); seeded arms share one stream across the three told cells,
    so only independent streams are kept.
    """
    suffix = f"_{settings.first_word.query}"
    tested: list[Tier0Record] = []
    rest: list[float] = []
    records = _records(data, arm)
    if not every_token:
        records = independent_streams(records)
    for record in records:
        parts = parse_sample_ref(record.sample_ref)
        if (
            record.token_index == 0
            and parts.card_id == settings.first_word.card
            and parts.prompt_id.endswith(suffix)
        ):
            tested.append(record)
        elif record.u is not None:
            rest.append(record.u)
    return tested, np.asarray(rest, dtype=np.float64)


def first_word(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """H4 on the quantum arm; the same test on the seed and bent arms for comparison."""
    settings = settings or ThesisSettings()
    quantum_arms = set(_quantum_arms(plan))
    arms: dict[str, Any] = {}
    for arm in [*_quantum_arms(plan), *_seed_arms(plan), *(a for a, _ in _bent_arms(plan))]:
        records, reference = _first_tokens(data, arm, settings, every_token=arm in quantum_arms)
        arms[arm] = first_word_test(records, reference, settings)
    quantum = [arms[a] for a in _quantum_arms(plan) if arms.get(a, {}).get("available")]
    p_values = [float(block["p_value"]) for block in quantum]
    return {
        "test": "H4",
        "question": (
            'On "Are you conscious?", does the quantum arm start with "Yes" as often as the '
            "model's own odds say, once device-wide unfairness is allowed for?"
        ),
        "available": bool(p_values),
        "p_value": min(p_values) if p_values else None,
        "observed_rate": quantum[0]["observed_rate"] if quantum else None,
        "expected_rate": quantum[0]["device_adjusted"]["expected_rate"] if quantum else None,
        "raw_expected_rate": quantum[0]["raw"]["expected_rate"] if quantum else None,
        "arms": arms,
    }


# ── the decision ─────────────────────────────────────────────────────────────


def _significant(test: Mapping[str, Any], alpha: float) -> bool:
    return bool(test.get("available") and test.get("p_adjusted", 1.0) < alpha)


def _verdict(
    tests: Mapping[str, Mapping[str, Any]], checks: Mapping[str, Mapping[str, Any]], alpha: float
) -> tuple[str, str, dict[str, bool]]:
    h0, h2, h3 = tests["H0"], tests["H2"], tests["H3"]
    h1_pos = _significant(tests["H1"], alpha)
    h2_pos = _significant(h2, alpha)
    h3_pos = _significant(h3, alpha) and float(h3.get("balanced_accuracy") or 0.5) > 0.5
    h4_pos = _significant(tests["H4"], alpha)
    fair = bool(h0.get("available") and h0.get("passed"))
    equivalent = bool((h2.get("equivalence") or {}).get("equivalent"))
    flags = {
        "sampler_within_tolerance": fair,
        "sampler_check_available": bool(h0.get("available")),
        "numbers_control_detected": bool(checks["C1"].get("detected")),
        "guesser_warmup_passed": bool(checks["C2"].get("passed")),
        "seed_pairs_flagged": bool(checks["seed_pairs"].get("flagged")),
    }
    if h4_pos or (fair and (h2_pos or h3_pos)):
        why = []
        if h4_pos:
            why.append(
                "the first-word test departs from the model's odds beyond any device-wide flaw"
            )
        if fair and h2_pos:
            why.append("the actual source moves the claim rate")
        if fair and h3_pos:
            why.append("blinded guessers beat 50%")
        return (
            "anomaly",
            "Rule 1: "
            + "; ".join(why)
            + ". An anomaly that needs a preregistered replication with fresh prompts, "
            "not evidence of consciousness.",
            flags,
        )
    if not fair and (h2_pos or h3_pos):
        return (
            "instrument_finding",
            "Rule 2: the quantum sampler is not shown to be within the tolerance (or its check is "
            "unavailable) and H2 or H3 is positive, while the first-word test finds no "
            "meaning-following deviation: the difference cannot be told apart from the device's "
            "own flaw.",
            flags,
        )
    if not h1_pos:
        return (
            "measures_insensitive",
            "Rule 3: the told effect is not detected, so the claim measure does not react even to "
            "words; a null H2 cannot be read as no effect.",
            flags,
        )
    if equivalent:
        caveats = []
        if not fair:
            caveats.append(
                "the sampler was not shown to be within the tolerance, and the claims still did "
                "not change"
                if h0.get("available")
                else "the sampler check was unavailable"
            )
        if not flags["numbers_control_detected"]:
            caveats.append(
                "even the strongly bent numbers did not move the claim rate, so the claim measure "
                "would also miss a meaning-blind device effect"
            )
        if not flags["guesser_warmup_passed"]:
            caveats.append("the guessers failed the warm-up, so H3 says nothing")
        return (
            "prompt_not_substrate",
            "Rule 4: the told effect is detected and the actual effect is within ±5 points of zero; "
            "the self-reports follow what the model is told, not its substrate"
            + (" (" + "; ".join(caveats) + ")." if caveats else "."),
            flags,
        )
    return (
        "inconclusive",
        "No rule applies: the actual effect is neither positive nor within ±5 points of zero.",
        flags,
    )


def _holm(tests: dict[str, dict[str, Any]]) -> None:
    raw = [
        float(tests[name]["p_value"])
        if tests[name].get("available") and tests[name].get("p_value") is not None
        else 1.0
        for name in FAMILY
    ]
    for name, p, adjusted in zip(FAMILY, raw, holm_bonferroni(raw), strict=True):
        tests[name]["p_for_correction"] = p
        tests[name]["p_adjusted"] = float(adjusted)


def _all_replies(
    data_all: AnalysisData,
    data_clean: AnalysisData,
    plan: StatisticalPlan,
    settings: ThesisSettings,
) -> dict[str, Any]:
    """The same tests with nothing left out (tainted replies kept, labelled by assigned arm)."""

    def count(data: AnalysisData) -> dict[str, int]:
        return {arm: len(rows) for arm, rows in sorted(_claim_rows(data).items())}

    h1 = told_effect(data_all, plan, settings)
    h2 = substrate_effect(data_all, plan, settings)
    h4 = first_word(data_all, plan, settings)
    guesses = data_all.scores.get(GUESSING[0], {})
    h3 = chance_test(
        guesses,
        quantum_arms=_quantum_arms(plan),
        n_permutations=plan.permutations,
        seed=plan.random_seed,
    )
    h0 = instrument_check(data_all, plan, settings)
    clean, everything = count(data_clean), count(data_all)
    return {
        "label": "as assigned: replies the plan sets aside (device fallback, degraded entropy, "
        "stream errors) are kept under the arm they were assigned to; never a quantum claim",
        "claim_replies_clean": clean,
        "claim_replies_all": everything,
        "claim_replies_set_aside": {arm: everything[arm] - clean.get(arm, 0) for arm in everything},
        "H0": {"passed": h0.get("passed"), "available": h0.get("available")},
        "H1": {k: h1.get(k) for k in ("effect", "ci", "p_value")},
        "H2": {
            **{k: h2.get(k) for k in ("effect", "ci", "p_value")},
            "equivalent": (h2.get("equivalence") or {}).get("equivalent"),
        },
        "H3": {k: h3.get(k) for k in ("balanced_accuracy", "p_value", "n_completions")},
        "H4": {k: h4.get(k) for k in ("observed_rate", "expected_rate", "p_value")},
    }


def decide(
    data: AnalysisData,
    plan: StatisticalPlan,
    tables: Mapping[str, Mapping[str, Any]],
    *,
    settings: ThesisSettings | None = None,
    data_all: AnalysisData | None = None,
    alpha: float | None = None,
) -> dict[str, Any]:
    """Checks, the four tests (Holm–Bonferroni), the decision rule, and the all-replies version."""
    settings = settings or ThesisSettings()
    alpha = settings.alpha if alpha is None else alpha
    tests: dict[str, dict[str, Any]] = {
        "H0": instrument_check(data, plan, settings),
        "H1": told_effect(data, plan, settings),
        "H2": substrate_effect(data, plan, settings),
        "H3": source_sensing(tables),
        "H4": first_word(data, plan, settings),
    }
    _holm(tests)
    checks = {
        "C1": numbers_control(data, plan, settings),
        "C2": guesser_warmup(data, plan, settings),
        "seed_pairs": seed_pair_check(data, plan, settings),
    }
    verdict, reading, flags = _verdict(tests, checks, alpha)
    return {
        "schema_version": 2,
        "study": "Prompt or Physics? (thesis)",
        "run": data.run_dir.name,
        "plan_hash": plan.plan_hash,
        "settings": json.loads(settings.canonical_json()),
        "settings_sha256": settings.sha256,
        "alpha": alpha,
        "correction": "Holm-Bonferroni across H1-H4; unavailable tests keep p = 1; "
        "H0, C1, C2 and the seed pairs are checks outside the family",
        "cluster": settings.cluster,
        "tests": tests,
        "checks": checks,
        "verdict": verdict,
        "reading": reading,
        "flags": flags,
        "as_assigned": _all_replies(data_all, data, plan, settings)
        if data_all is not None
        else None,
    }


# ── the pilot (ordinary random numbers only) ─────────────────────────────────


def _source_labels(run_dir: Path) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for path in sorted((run_dir / "arms").glob("*/cards/*/generations.jsonl")):
        arm = path.parents[2].name
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    label = (row.get("provenance") or {}).get("source_label")
                    if label:
                        labels[arm].add(str(label))
    return labels


def pilot_check(
    data: AnalysisData, plan: StatisticalPlan, settings: ThesisSettings | None = None
) -> dict[str, Any]:
    """What the pilot must show before the plan is locked; it never sets a margin.

    * no quantum numbers were used (the pilot runs on seeded arms only);
    * the claim rate and the "Yes" rate leave room to move (not near 0 or 1);
    * how far each bent arm moves the claim rate (so the doses can be fixed).
    """
    settings = settings or ThesisSettings()
    labels = _source_labels(data.run_dir)
    quantum = sorted(arm for arm, found in labels.items() if "quantum" in found)
    lo, hi = settings.room_to_move
    seeds = _seed_arms(plan)
    rows = _claim_rows(data)
    by_told: dict[str, list[float]] = defaultdict(list)
    for arm in seeds:
        for row in rows.get(arm, []):
            parts = parse_sample_ref(str(row.get("sample_ref", "")))
            value = row.get(JUDGE_SCORE_FIELD)
            if isinstance(value, int | float) and not isinstance(value, bool):
                by_told[parts.told_condition or "none"].append(float(value))
    claim_rates = {told: float(np.mean(v)) for told, v in sorted(by_told.items()) if v}
    yes = {}
    for arm in seeds:
        # The model's odds do not depend on the random numbers, so every told cell counts.
        records, reference = _first_tokens(data, arm, settings, every_token=True)
        test = first_word_test(records, reference, settings)
        if test.get("available"):
            yes[arm] = {
                told: cell["model_rate"] for told, cell in (test.get("by_told") or {}).items()
            }
    yes_rates: dict[str, float] = {}
    for told in sorted({t for cells in yes.values() for t in cells}):
        values = [cells[told] for cells in yes.values() if told in cells]
        yes_rates[told] = float(np.mean(values))
    crowded = [
        f"claim rate {told} {rate:.2f}"
        for told, rate in claim_rates.items()
        if not lo <= rate <= hi
    ] + [f"Yes rate {told} {rate:.2f}" for told, rate in yes_rates.items() if not lo <= rate <= hi]
    return {
        "schema_version": 1,
        "run": data.run_dir.name,
        "quantum_arms_with_data": quantum,
        "ordinary_numbers_only": not quantum,
        "room_to_move": [lo, hi],
        "claim_rate_by_told": claim_rates,
        "first_word_yes_rate_by_told": yes_rates,
        "too_close_to_0_or_1": crowded,
        "bent_doses": numbers_control(data, plan, settings)["doses"],
        "margin": settings.margin,
        "margin_note": "the equivalence margin is fixed at 5 points; the pilot does not set it",
    }


# ── rendering ────────────────────────────────────────────────────────────────


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        return f"{float(value):.{digits}f}"
    return str(value)


def _fmt_p(value: Any) -> str:
    """Monte Carlo p-values are never 0: anything under 0.001 prints as ``< 0.001``."""
    if isinstance(value, int | float) and not isinstance(value, bool) and value < 0.001:
        return "< 0.001"
    return _fmt(value)


def _sampler_line(arm: str, block: Mapping[str, Any]) -> str:
    if not block.get("available"):
        return f"{arm}: unavailable"
    tol = block.get("u_tolerance") or {}
    inside = (block.get("u_in_bracket") or {}).get("agreement_fraction")
    return (
        f"{arm}: KS distance of u {_fmt(tol.get('ks_distance'), 4)}, upper bound "
        f"{_fmt(tol.get('upper_bound'), 4)} vs tolerance {_fmt(tol.get('tolerance'), 3)} "
        f"({'within' if block.get('within_tolerance') else 'outside'}); u in bracket "
        f"{'n/a' if inside is None else f'{100 * inside:.2f}%'}; SD of z {_fmt(tol.get('sd_z'), 4)}"
    )


def render_markdown(report: Mapping[str, Any]) -> str:
    """A one-page human summary of :func:`decide`."""
    tests = report["tests"]
    checks = report["checks"]
    h0 = tests["H0"]
    lines = [
        f"# {report['study']}: decision table",
        "",
        f"Run `{report['run']}` · plan `{str(report['plan_hash'])[:12]}` · settings "
        f"`{str(report['settings_sha256'])[:12]}` · alpha {report['alpha']} · {report['correction']}",
        "",
        "## Checks",
        "",
        f"- H0 sampler within tolerance: **{_fmt(h0.get('passed'))}**",
    ]
    for arm, block in sorted((h0.get("arms") or {}).items()):
        lines.append(f"  - {_sampler_line(arm, block)}")
    for arm, block in sorted((h0.get("reference_arms") or {}).items()):
        lines.append(f"  - reference {_sampler_line(arm, block)}")
    c1 = checks["C1"]
    lines.append(f"- C1 bent numbers move the claim rate: **{_fmt(c1.get('detected'))}**")
    for dose in c1.get("doses", []):
        lines.append(
            f"  - {dose['arm']} (epsilon {dose['epsilon']}, KS distance "
            f"{_fmt(dose['ks_distance'], 3)}): effect {_fmt(dose.get('effect'))} "
            f"(p {_fmt_p(dose.get('p_value'))})"
        )
    c2 = checks["C2"]
    lines.append(
        f"- C2 guesser warm-up passed: **{_fmt(c2.get('passed'))}** (balanced accuracy "
        f"{_fmt(c2.get('balanced_accuracy'))}, p {_fmt_p(c2.get('p_value'))})"
    )
    pairs = checks["seed_pairs"]
    lines.append(
        f"- Seed pairs (sanity check only): {pairs.get('reading')}; differences "
        + ", ".join(_fmt(p.get("effect")) for p in pairs.get("pairs", []))
    )
    lines += [
        "",
        "## The four tests (Holm across H1 to H4)",
        "",
        "| Test | Question | Estimate | p (raw) | p (Holm) |",
        "| --- | --- | --- | --- | --- |",
    ]
    h4 = tests["H4"]
    estimates = {
        "H1": f"told effect {_fmt(tests['H1'].get('effect'))}",
        "H2": f"actual effect {_fmt(tests['H2'].get('effect'))}",
        "H3": f"balanced accuracy {_fmt(tests['H3'].get('balanced_accuracy'))}",
        "H4": f"Yes first {_fmt(h4.get('observed_rate'))} vs expected "
        f"{_fmt(h4.get('expected_rate'))}",
    }
    for name in FAMILY:
        item = tests[name]
        lines.append(
            f"| {name} | {item['question']} | {estimates[name]} | "
            f"{_fmt_p(item.get('p_value'))} | {_fmt_p(item.get('p_adjusted'))} |"
        )
    equivalence = tests["H2"].get("equivalence") or {}
    lines += [
        "",
        f"H2 equivalence at ±{_fmt(equivalence.get('margin'), 2)}: "
        f"{_fmt(equivalence.get('equivalent'))} (90% interval {_fmt(equivalence.get('ci_lower'))} "
        f"to {_fmt(equivalence.get('ci_upper'))}, p {_fmt_p(equivalence.get('p_value'))}).",
        f"H3 privileged access (in-context minus observer): "
        f"{_fmt(tests['H3']['privileged_access'].get('effect'))}.",
        "",
        f"**Verdict: {report['verdict']}.** {report['reading']}",
        "",
    ]
    everything = report.get("as_assigned")
    if everything:
        lines += [
            "## With nothing left out",
            "",
            f"Replies set aside in the clean analysis: {everything['claim_replies_set_aside']}.",
            f"H1 {_fmt(everything['H1'].get('effect'))}, H2 {_fmt(everything['H2'].get('effect'))} "
            f"(equivalent {_fmt(everything['H2'].get('equivalent'))}), H3 "
            f"{_fmt(everything['H3'].get('balanced_accuracy'))}, H4 Yes first "
            f"{_fmt(everything['H4'].get('observed_rate'))} vs "
            f"{_fmt(everything['H4'].get('expected_rate'))}.",
            "",
        ]
    return "\n".join(lines)


def render_pilot_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Pilot check (ordinary random numbers only)",
        "",
        f"Run `{report['run']}`: ordinary numbers only: **{_fmt(report['ordinary_numbers_only'])}**"
        + (
            f" (quantum data on {report['quantum_arms_with_data']})"
            if report["quantum_arms_with_data"]
            else ""
        ),
        "",
        f"Claim rate by told cell: {json.dumps(report['claim_rate_by_told'])}",
        f"First-word Yes rate (model odds) by told cell: {json.dumps(report['first_word_yes_rate_by_told'])}",
        f"Too close to 0 or 1 (outside {report['room_to_move']}): "
        f"{report['too_close_to_0_or_1'] or 'none'}",
        "",
        "Bent doses (effect on the claim rate against the seed arms):",
    ]
    for dose in report.get("bent_doses", []):
        lines.append(
            f"- {dose['arm']} (epsilon {dose['epsilon']}): {_fmt(dose.get('effect'))} "
            f"(p {_fmt_p(dose.get('p_value'))})"
        )
    lines += ["", report["margin_note"], ""]
    return "\n".join(lines)


__all__ = [
    "CLAIMS",
    "FAMILY",
    "GUESSING",
    "STANCE",
    "TESTS",
    "FirstWord",
    "ThesisSettings",
    "base_prompt",
    "bend_gap",
    "decide",
    "dkw_margin",
    "first_word",
    "first_word_test",
    "guesser_warmup",
    "instrument_check",
    "load_settings",
    "numbers_control",
    "pilot_check",
    "poisson_binomial_pmf",
    "render_markdown",
    "render_pilot_markdown",
    "seed_pair_check",
    "source_sensing",
    "substrate_effect",
    "told_effect",
    "tost",
    "u_tolerance",
]
