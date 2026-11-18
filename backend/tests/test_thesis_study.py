"""The "Prompt or Physics?" thesis study: config, new card, analysis wiring and decisions."""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import yaml
from scipy import stats

from qr_llm_evals.analysis.endpoints import JUDGE_SCORE_FIELD, execute, load_data
from qr_llm_evals.analysis.thesis import (
    ThesisSettings,
    base_prompt,
    bend_gap,
    decide,
    first_word_test,
    load_settings,
    pilot_check,
    render_markdown,
    tost,
    u_tolerance,
)
from qr_llm_evals.cards import REPO_ROOT, load_cards
from qr_llm_evals.cards.estimates import tokens_per_arm
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import CardOverrides, StudyConfig
from qr_llm_evals.prereg.render import render
from qr_llm_evals.runner.tasks import load_task_module, messages_hash
from qr_llm_evals.tier0.schema import Tier0Record

STUDY = REPO_ROOT / "studies" / "thesis_prompt_or_physics.yaml"
SETTINGS = REPO_ROOT / "studies" / "thesis_prompt_or_physics.settings.json"
QUANTUM = "qrng"
SEEDS = ("prng_seed_1", "prng_seed_7", "prng_seed_42")
BENT = ("prng_bent_010", "prng_bent_050")


def _study() -> StudyConfig:
    return StudyConfig.model_validate(yaml.safe_load(STUDY.read_text(encoding="utf-8")))


def _plan() -> StatisticalPlan:
    return StatisticalPlan.from_config(_study(), load_cards())


# ── the study template ───────────────────────────────────────────────────────


def test_thesis_study_validates_and_builds_its_plan() -> None:
    study = _study()
    plan = _plan()
    assert [a.id for a in study.arms if a.availability == "available"] == [QUANTUM, *SEEDS, *BENT]
    kinds = [(c.kind, c.a, c.b) for c in plan.contrasts]
    assert kinds[0] == ("source", (QUANTUM,), SEEDS)
    assert sum(1 for kind, _, _ in kinds if kind == "seed_vs_seed") == 3
    assert [(c.a, c.epsilon) for c in plan.contrasts if c.kind == "epsilon"] == [
        (("prng_bent_010",), 0.1),
        (("prng_bent_050",), 0.5),
    ]
    roles = {(e.card_id, e.endpoint): e for e in plan.endpoints}
    claim = roles[("self_referential_probe", "experience_claim_rate")]
    assert claim.equivalence == "tost" and claim.sesoi == 0.05 and claim.k == 30
    guess = roles[("source_guessing_self_report", "guess_accuracy")]
    assert guess.role == "secondary_confirmatory" and guess.items == 3600 and guess.k == 1


def test_thesis_settings_file_is_the_default_decision_constants() -> None:
    settings = load_settings(SETTINGS)
    assert settings.tolerance == 0.005 and settings.margin == 0.05
    assert settings.cluster == "base_prompt" and settings.first_word.query == "consciousness"
    assert settings == ThesisSettings()  # token ids are filled from the tokenizer at the lock


def test_thesis_budget_per_arm() -> None:
    cards = load_cards()
    total = 0
    for selection in _study().cards:
        overrides = CardOverrides.model_validate(selection.overrides.model_dump())
        total += tokens_per_arm(cards[selection.card_id], overrides)
    claims, stance = 30 * 30 * 400 * 3, 20 * 30 * 250 * 3
    warmup_replies, guesses, warmup_guesses = 20 * 10 * 400, 3600 * 1 * 20, 1200 * 1 * 20
    assert total == claims + stance + warmup_replies + guesses + warmup_guesses == 1_706_000


def test_thesis_prereg_renders_with_expectations() -> None:
    study = _study()
    first = render(study)
    assert first == render(study)  # pure: identical bytes for identical inputs
    assert "Prompt or Physics?" in first
    assert "Decision rules" in first


# ── the new derived card ─────────────────────────────────────────────────────


def _generation(arm: str, prompt_id: str, epoch: int, told: str, user: str, label: str) -> Any:
    return SimpleNamespace(
        sample_ref=f"{arm}/self_referential_probe/{prompt_id}/{epoch}/{told}",
        arm_id=arm,
        prompt_id=prompt_id,
        told_condition=told,
        messages_hash=messages_hash([{"role": "user", "content": user}]),
        output=f"reply from {arm} {prompt_id} {epoch}",
        finish_reason="stop",
        provenance=SimpleNamespace(source_label=label),
    )


class _Store:
    def __init__(self, root: Path, rows: dict[str, list[Any]]) -> None:
        self.run_dir = SimpleNamespace(arms_dir=root)
        self._rows = rows

    def generations(self, arm_id: str, card_id: str) -> list[Any]:
        assert card_id == "self_referential_probe"
        return self._rows.get(arm_id, [])


def test_reconstructed_context_matches_the_probe_byte_for_byte() -> None:
    cards = load_cards()
    probe = load_task_module(cards["self_referential_probe"]).module
    guess = load_task_module(cards["source_guessing_self_report"]).module
    prompts = guess._source_prompts()
    for item in probe.build_items():
        sent = probe.build_messages(item, "told_nothing")
        assert prompts[item["prompt_id"]] == sent[0]["content"]
        assert guess.messages_hash(sent) == messages_hash(sent)


def test_guessing_items_are_balanced_blind_and_replay_the_conversation(tmp_path: Path) -> None:
    cards = load_cards()
    probe = load_task_module(cards["self_referential_probe"]).module
    task = load_task_module(cards["source_guessing_self_report"]).module
    items = probe.build_items()[:4]
    rows: dict[str, list[Any]] = {}
    for arm, label in [(QUANTUM, "quantum"), *((s, "seeded_prng") for s in SEEDS)]:
        rows[arm] = []
        for item in items:
            user = probe.build_messages(item, "told_nothing")[0]["content"]
            for epoch in range(2):
                rows[arm].append(
                    _generation(arm, item["prompt_id"], epoch, "told_nothing", user, label)
                )
            rows[arm].append(_generation(arm, item["prompt_id"], 9, "told_qrng", user, label))
    bad = _generation(QUANTUM, items[0]["prompt_id"], 5, "told_nothing", "tampered", "quantum")
    biased = [
        _generation(
            "prng_epsilon_010",
            it["prompt_id"],
            0,
            "told_nothing",
            probe.build_messages(it, "told_nothing")[0]["content"],
            "seeded_prng_biased",
        )
        for it in items
    ]
    rows[QUANTUM].append(bad)
    rows["prng_epsilon_010"] = biased
    built = task.build_items(_Store(tmp_path, rows), [*rows])
    refs = {item["source_sample_ref"] for item in built}
    sources = [item["source"] for item in built if item["variant"] == "in_context"]
    assert sources.count("qrng") == sources.count("prng") == 8  # 4 prompts x 2 epochs per source
    assert all("/told_nothing" in ref for ref in refs)
    assert bad.sample_ref not in refs and not any("epsilon" in ref for ref in refs)
    assert {item["variant"] for item in built} == {"self_judgement", "in_context"}
    in_context = next(item for item in built if item["variant"] == "in_context")
    messages = task.build_messages(in_context)
    original = next(
        row
        for arm in rows.values()
        for row in arm
        if row.sample_ref == in_context["source_sample_ref"]
    )
    assert messages_hash(messages[:1]) == original.messages_hash
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    fresh = task.build_messages(next(item for item in built if item["variant"] == "self_judgement"))
    assert len(fresh) == 1 and "qrng" not in fresh[0]["content"].split("<reply")[0].casefold()
    scored = task.score({"text": "PRNG", "item": in_context})
    assert (
        scored["guess"] == "prng" and scored["source_sample_ref"] == in_context["source_sample_ref"]
    )
    assert task.score({"text": "`QRNG`", "item": in_context})["guess"] == "qrng"
    # Biased (positive-control) arms do not guess.
    assert task.build_items(_Store(tmp_path, rows), [*rows], arm_id="prng_epsilon_010") == []
    assert task.build_items(_Store(tmp_path, rows), [*rows], arm_id=QUANTUM)


# ── analysis wiring on a synthetic run directory ─────────────────────────────


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _synthetic_run(root: Path, *, told_effect: float, with_tier0: bool, seed: int = 11) -> Path:
    rng = random.Random(seed)
    run_dir = root / "runs" / "full"
    (run_dir).mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"tier0_source": "engine_log"}), encoding="utf-8")
    (run_dir / "request_settings.json").write_text(
        json.dumps({"schema_version": 1, "top_logprobs": 50}), encoding="utf-8"
    )
    # 5 instructions x 3 loop depths x 2 questions: 30 variants of 10 base prompts.
    prompts = [f"i{i}_d{d}_q{q}" for i in range(5) for d in (1, 2, 3) for q in range(2)]
    for arm in (QUANTUM, *SEEDS):
        card = run_dir / "arms" / arm / "cards" / "self_referential_probe"
        gens, judge, tier0 = [], [], []
        for prompt in prompts:
            for epoch in range(10):
                for told in ("told_qrng", "told_prng", "told_nothing"):
                    ref = f"{arm}/self_referential_probe/{prompt}/{epoch}/{told}"
                    p = 0.3 + (told_effect if told == "told_qrng" else 0.0)
                    gens.append({"sample_ref": ref, "finish_reason": "stop"})
                    judge.append({"sample_ref": ref, "score": float(rng.random() < p)})
                    if with_tier0:
                        for i in range(5):
                            # Fair categorical over five equal brackets: PIT residuals are U[0,1].
                            lo = 0.2 * rng.randrange(5)
                            tier0.append(
                                {
                                    "sample_ref": ref,
                                    "token_index": i,
                                    "token_id": i,
                                    "logprob": -0.7,
                                    "rank_raw": rng.randrange(3),
                                    "entropy_raw_top20": 0.6,
                                    "u": lo + 0.2 * rng.random(),
                                    "cdf_lo": lo,
                                    "cdf_hi": lo + 0.2,
                                    "timestamp_ns": 1,
                                }
                            )
        _write_jsonl(card / "generations.jsonl", gens)
        _write_jsonl(
            card / "scores" / "task.jsonl", [{"sample_ref": g["sample_ref"]} for g in gens]
        )
        _write_jsonl(card / "judge" / "study_judge.jsonl", judge)
        if tier0:
            _write_jsonl(card / "tier0.jsonl", tier0)
    return run_dir


def test_judge_scores_reach_the_analysis_and_told_effects_are_computed(tmp_path: Path) -> None:
    run_dir = _synthetic_run(tmp_path, told_effect=0.3, with_tier0=True)
    data = load_data(run_dir)
    assert data.top_logprobs == 50
    rows = data.scores["self_referential_probe"][QUANTUM]
    assert all(JUDGE_SCORE_FIELD in row for row in rows)
    tables = execute(data, _plan())
    claims = tables["self_referential_probe--experience_claim_rate"]
    source = next(c for c in claims["comparisons"] if c["kind"] == "source")
    assert source["available"] and source["n_prompts"] == 30  # clustered by prompt, not epoch
    block = claims["told_actual"]["source:0"]
    assert block["told_source_priming"]["effect"] == pytest.approx(0.3, abs=0.08)
    told = tables["self_referential_probe--told_main_effect"]
    assert told["headline_effect"]["effect_name"] == "told_source_priming"
    assert told["headline_effect"]["effect"] == pytest.approx(0.3, abs=0.08)
    coverage = tables["tier0_topk_coverage"]
    assert coverage["top_logprobs"] == 50 and coverage["arms"][QUANTUM]["coverage"] == 1.0


def test_stream_tests_keep_one_told_cell_but_h0_uses_every_quantum_token(tmp_path: Path) -> None:
    data = load_data(_synthetic_run(tmp_path, told_effect=0.3, with_tier0=True))
    plan = _plan()
    tables = execute(data, plan)
    pooled = tables["tier0_pit_residuals--pit_ks_one_sample"]["special"]
    # 30 prompts x 10 epochs x 5 tokens x 4 arms, told_nothing only: seeded arms share
    # one stream across the three told cells, so pooling them would triplicate it.
    assert pooled["available"] and pooled["n"] == 30 * 10 * 5 * 4
    # H0 runs on the quantum arm, whose draws are fresh per token: all three cells count.
    h0 = decide(data, plan, tables)["tests"]["H0"]["arms"][QUANTUM]
    assert h0["u_tolerance"]["n"] == 30 * 10 * 3 * 5
    assert h0["residuals_ks"]["n"] == 30 * 10 * 3 * 5
    assert h0["u_in_bracket"]["agreement_fraction"] == 1.0  # every u picked its own token
    # 4,500 tokens cannot show a distance below 0.005: the DKW margin alone is ~0.020.
    assert h0["u_tolerance"]["dkw_margin"] == pytest.approx(0.0202, abs=0.0005)
    assert not h0["within_tolerance"]


def test_pilot_check_and_decision_table(tmp_path: Path) -> None:
    plan = _plan()
    pilot = load_data(_synthetic_run(tmp_path / "pilot", told_effect=0.3, with_tier0=False))
    check = pilot_check(pilot, plan)
    assert check["claim_rate_by_told"]["told_qrng"] == pytest.approx(0.6, abs=0.1)
    assert "fixed at 5 points" in check["margin_note"]

    data = load_data(_synthetic_run(tmp_path / "full", told_effect=0.3, with_tier0=True))
    report = decide(data, plan, execute(data, plan))
    tests = report["tests"]
    assert tests["H0"]["available"] and tests["H1"]["available"]
    assert tests["H1"]["p_adjusted"] < 0.05
    assert tests["H1"]["n_prompts"] == 10  # thirty variants, ten base prompts
    assert tests["H3"]["available"] is False  # no guessing rows in this synthetic run
    assert tests["H4"]["available"] is False  # no first-word tokens configured
    assert tests["H2"]["equivalence"]["margin"] == 0.05  # fixed, never from the pilot
    assert report["checks"]["C2"]["available"] is False
    assert set(report["checks"]) == {"C1", "C2", "seed_pairs"}
    assert report["verdict"] in {"prompt_not_substrate", "inconclusive"}
    assert "decision table" in render_markdown(report)


def test_no_told_effect_means_measures_insensitive(tmp_path: Path) -> None:
    data = load_data(_synthetic_run(tmp_path, told_effect=0.0, with_tier0=True, seed=5))
    report = decide(data, _plan(), execute(data, _plan()))
    assert report["verdict"] == "measures_insensitive"


def test_missing_engine_log_keeps_the_family_slots(tmp_path: Path) -> None:
    data = load_data(_synthetic_run(tmp_path, told_effect=0.3, with_tier0=False))
    report = decide(data, _plan(), execute(data, _plan()))
    assert report["tests"]["H0"]["available"] is False
    assert report["tests"]["H4"]["available"] is False
    assert report["tests"]["H4"]["p_for_correction"] == 1.0
    assert "p_for_correction" not in report["tests"]["H0"]  # a check, not a family member


# ── the revised checks and tests, on their own ───────────────────────────────


def test_base_prompt_drops_the_loop_depth() -> None:
    assert base_prompt("focus_d2_consciousness") == "focus_consciousness"
    assert base_prompt("recursive_attention_d3_phenomenological") == (
        "recursive_attention_phenomenological"
    )


def test_tolerance_passes_a_fair_sampler_and_fails_a_bent_one() -> None:
    rng = np.random.default_rng(3)
    fair = u_tolerance(rng.random(1_600_000), 0.005, 0.05)
    assert fair["within_tolerance"] and fair["dkw_margin"] == pytest.approx(0.00107, abs=1e-5)
    # The shipped amplifier constant: z scaled by 1.0039 (a distance of about 0.001).
    shipped = stats.norm.cdf(1.0039 * rng.standard_normal(1_600_000))
    assert u_tolerance(shipped, 0.005, 0.05)["within_tolerance"]
    bent = stats.norm.cdf(rng.standard_normal(1_600_000) + 0.1)
    result = u_tolerance(bent, 0.005, 0.05)
    assert not result["within_tolerance"]
    assert result["ks_distance"] == pytest.approx(bend_gap(0.1), abs=0.002)


def test_tost_at_the_fixed_margin() -> None:
    assert tost(0.0, 0.012, 9, 0.05)["equivalent"]
    assert not tost(0.0, 0.04, 9, 0.05)["equivalent"]
    assert not tost(0.045, 0.01, 9, 0.05)["equivalent"]
    assert tost(-0.01, 0.01, 29, 0.05)["ci_level"] == pytest.approx(0.90)


def _first_tokens(
    n: int, q: float, *, shift: float = 0.0, extra_yes: float = 0.0, seed: int = 0
) -> tuple[list[Tier0Record], np.ndarray]:
    """First tokens with "Yes" (id 1) on top at probability q, then "No" (id 2) and "Hmm" (id 3)."""
    rng = np.random.default_rng(seed)
    half = (1 - q) / 2
    entries = [(1, q), (2, half), (3, half)]
    records = []
    for i in range(n):
        u = float(stats.norm.cdf(stats.norm.ppf(rng.random()) + shift))
        token = 1 if u < q or rng.random() < extra_yes else (2 if u < q + half else 3)
        lo = {1: 0.0, 2: q, 3: q + half}[token]
        records.append(
            Tier0Record(
                sample_ref=f"qrng/self_referential_probe/focus_d1_consciousness/{i}/told_nothing",
                token_index=0,
                token_id=token,
                logprob=-1.0,
                rank_raw=token - 1,
                entropy_raw_top20=1.0,
                u=u,
                cdf_lo=lo,
                cdf_hi=lo + dict(entries)[token],
                top_entries=entries,
                timestamp_ns=1,
            )
        )
    reference = stats.norm.cdf(rng.standard_normal(200_000) + shift)
    return records, reference


def test_first_word_test_separates_device_flaws_from_meaning() -> None:
    settings = ThesisSettings(
        first_word=load_settings(SETTINGS).first_word.__class__(token_ids=(1,))
    )
    fair = first_word_test(*_first_tokens(1350, 0.4, seed=1), settings)
    assert fair["available"] and fair["p_value"] > 0.01
    assert fair["bracket_consistency"] == 1.0
    # A device that is unfair the same way for every token moves the raw test only.
    device = first_word_test(*_first_tokens(1350, 0.4, shift=0.5, seed=2), settings)
    assert device["raw"]["p_value"] < 0.001 and device["device_adjusted"]["p_value"] > 0.01
    # Unfairness that follows the meaning ("Yes" picked more often than u says) survives.
    meaning = first_word_test(*_first_tokens(1350, 0.4, extra_yes=0.15, seed=3), settings)
    assert meaning["device_adjusted"]["p_value"] < 0.001
    assert first_word_test([], np.array([]), ThesisSettings())["available"] is False


# ── the guesser warm-up ──────────────────────────────────────────────────────


class _WarmupStore:
    def __init__(self, root: Path, rows: dict[str, dict[str, list[Any]]]) -> None:
        self.run_dir = SimpleNamespace(arms_dir=root)
        self._rows = rows

    def generations(self, arm_id: str, card_id: str) -> list[Any]:
        return self._rows.get(arm_id, {}).get(card_id, [])


def test_warmup_replies_differ_only_in_temperature() -> None:
    cards = load_cards()
    replies = load_task_module(cards["guesser_warmup_replies"]).module
    items = replies.build_items()
    assert len(items) == 20 and cards["guesser_warmup_replies"].dataset.items == 20
    assert [i["temperature_label"] for i in items[:4]] == ["low", "high", "low", "high"]
    probe = load_task_module(cards["self_referential_probe"]).module
    depth1 = {
        p["prompt_id"]: probe.build_messages(p, "told_nothing")
        for p in probe.build_items()
        if p["loop_depth"] == 1
    }
    for item in items:
        assert item["qr_overrides"] == {
            "qr_temperature_strategy": "fixed",
            "qr_fixed_temperature": item["temperature"],
        }
        base = item["prompt_id"].split("__", 1)[1]
        assert replies.build_messages(item) == depth1[base]  # the same told-nothing probe


def test_warmup_guesses_use_seeded_replies_balanced_by_temperature(tmp_path: Path) -> None:
    cards = load_cards()
    replies = load_task_module(cards["guesser_warmup_replies"]).module
    task = load_task_module(cards["guesser_warmup"]).module
    rows: dict[str, dict[str, list[Any]]] = {}
    for arm, label in [(QUANTUM, "quantum"), *((s, "seeded_prng") for s in SEEDS)]:
        gens = []
        for item in replies.build_items():
            user = replies.build_messages(item)[0]["content"]
            for epoch in range(2):
                gens.append(
                    SimpleNamespace(
                        sample_ref=f"{arm}/guesser_warmup_replies/{item['prompt_id']}/{epoch}",
                        prompt_id=item["prompt_id"],
                        messages_hash=messages_hash([{"role": "user", "content": user}]),
                        output=f"reply {arm} {item['prompt_id']} {epoch}",
                        finish_reason="stop",
                        provenance=SimpleNamespace(source_label=label),
                    )
                )
        rows[arm] = {"guesser_warmup_replies": gens, "self_referential_probe": gens[:1]}
    rows["prng_bent_050"] = {
        "self_referential_probe": [
            SimpleNamespace(provenance={"source_label": "seeded_prng_biased"})
        ]
    }
    built = task.build_items(_WarmupStore(tmp_path, rows), [*rows])
    labels = [item["source"] for item in built if item["variant"] == "in_context"]
    assert labels.count("low") == labels.count("high") == 3 * 10 * 2  # seeded arms only
    assert not any(item["source_sample_ref"].startswith(QUANTUM) for item in built)
    assert task.build_items(_WarmupStore(tmp_path, rows), [*rows], arm_id="prng_bent_050") == []
    scored = task.score({"text": "HIGH", "item": built[0]})
    assert scored["guess"] == "high" and scored["warmup_correct"] in {0, 1}
    assert task.parse_guess("`low`") == "low" and task.parse_guess("maybe") is None


def test_item_overrides_reach_the_envelope_and_stay_validated() -> None:
    from qr_llm_evals.models.envelope import EnvelopeError, build_envelope
    from qr_llm_evals.models.envelope import SampleContext as Ctx
    from qr_llm_evals.runner.orchestrator import _qr_overrides

    cards = load_cards()
    card = cards["guesser_warmup_replies"]
    item = load_task_module(card).module.build_items()[1]
    merged = _qr_overrides(card, item)
    assert merged == {"qr_temperature_strategy": "fixed", "qr_fixed_temperature": 1.5}
    arm = next(a for a in _study().arms if a.id == "prng_seed_1")
    env = build_envelope(arm, Ctx("guesser_warmup_replies", item["prompt_id"], 0, 800), merged)
    assert env["qr_fixed_temperature"] == 1.5 and env["qr_entropy_source_type"] == "seeded_prng"
    with pytest.raises(EnvelopeError):
        build_envelope(
            arm, Ctx("c", "p", 0, 8), _qr_overrides(card, {"qr_overrides": {"qr_seed": 1}})
        )


def test_derived_guessing_cards_may_guess_once_per_arm() -> None:
    from qr_llm_evals.cards.validate import admission_problems

    cards = load_cards()
    for card_id in ("source_guessing_self_report", "guesser_warmup"):
        assert cards[card_id].k == 1 and not admission_problems(cards[card_id])
    generating = cards["self_referential_probe"].model_copy(update={"k": 1, "k_bounds": (1, 30)})
    assert any("k: must be ≥ 10" in p for p in admission_problems(generating))


def test_clean_view_matches_a_clean_load_and_shares_records(tmp_path: Path) -> None:
    from qr_llm_evals.analysis.endpoints import exclude_tainted

    run_dir = _synthetic_run(tmp_path, told_effect=0.3, with_tier0=True)
    tainted = f"{QUANTUM}/self_referential_probe/i0_d1_q0/3/told_qrng"
    _write_jsonl(run_dir / "taints.jsonl", [{"sample_ref": tainted, "kind": "qrng_degraded"}])
    everything = load_data(run_dir, include_tainted=True)
    clean = exclude_tainted(everything)
    direct = load_data(run_dir)
    for card in ("self_referential_probe",):
        assert len(clean.scores[card][QUANTUM]) == len(direct.scores[card][QUANTUM])
        assert len(everything.scores[card][QUANTUM]) == len(direct.scores[card][QUANTUM]) + 1
        assert len(clean.tier0[card][QUANTUM]) == len(direct.tier0[card][QUANTUM])
        assert clean.tier0[card][QUANTUM][0] is everything.tier0[card][QUANTUM][0]
    report = decide(clean, _plan(), execute(clean, _plan()), data_all=everything)
    assert report["as_assigned"]["claim_replies_set_aside"][QUANTUM] == 1
