"""Tier 0 contract tests (spec §5.2 'Tier 0'): record builders from both fixtures,
the availability matrix, PIT uniformity on a synthetic sampler, MMD planted
shift / seed-vs-seed null, and the T4-l window join."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from qr_llm_evals.analysis import mmd, stats
from qr_llm_evals.cards import load_cards
from qr_llm_evals.tier0 import acquire, availability, endpoints, records
from qr_llm_evals.tier0.schema import Tier0Record

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tier0"
ENGINE_LOG = FIXTURES / "engine_log.jsonl"
API_LOGPROBS = json.loads((FIXTURES / "api_logprobs.json").read_text(encoding="utf-8"))
SAMPLE_REF = API_LOGPROBS["sample_ref"]
CHUNKS = API_LOGPROBS["chunks"]

TIER0_CARDS = (
    "tier0_pit_residuals",
    "tier0_mmd_hamming",
    "tier0_logprob_endpoints",
    "stream_deviation_windows",
)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


synth = _load_module(FIXTURES / "synthetic_sampler.py", "synthetic_sampler")


# ── records helpers ─────────────────────────────────────────────────────────


def test_parse_sample_ref_and_prompt_key() -> None:
    parts = records.parse_sample_ref("qrng/creative_diversity/p001/3/told_qrng")
    assert (parts.arm_id, parts.card_id, parts.prompt_id, parts.epoch) == (
        "qrng",
        "creative_diversity",
        "p001",
        3,
    )
    assert parts.prompt_key == "creative_diversity/p001/told_qrng"
    assert records.prompt_key("prng_1/creative_diversity/p001/0") == "creative_diversity/p001"
    for bad in ("a/b/c", "a/b/c/x", "a//c/0", "a/b/c/-1"):
        with pytest.raises(ValueError):
            records.parse_sample_ref(bad)


def test_token_id_from_api_token() -> None:
    assert records.token_id_from_api_token("token_id:1234") == 1234
    hashed = records.token_id_from_api_token(" river")
    assert hashed == records.token_id_from_api_token(" river")
    assert hashed >= 1 << 30
    assert hashed != records.token_id_from_api_token(" rivers")


# ── acquisition: degraded (api_logprobs) path ───────────────────────────────


def test_build_records_degraded_path_from_api_fixture() -> None:
    recs = acquire.build_records(SAMPLE_REF, CHUNKS, None, timestamp_ns=7, session_id="s1")
    assert [r.token_index for r in recs] == [0, 1, 2, 3, 4]
    assert all(r.sample_ref == SAMPLE_REF and r.timestamp_ns == 7 for r in recs)
    assert [r.rank_raw for r in recs] == [0, 2, 0, 1, 5]  # last token is below every top-5 alt
    assert all(r.u is None and r.cdf_lo is None and r.p_post is None for r in recs)
    assert all(r.entropy_post is None and r.top_entries is None for r in recs)
    assert all(r.entropy_raw_top20 > 0 for r in recs)
    assert all(r.session_id == "s1" for r in recs)
    assert recs[0].logprob == pytest.approx(-0.223143551)
    assert recs[0].token_id == records.token_id_from_api_token("The")


def test_build_records_accepts_token_logprob_objects() -> None:
    from qr_llm_evals.client.sse import TokenLogprob, TopLogprob

    objs = [
        TokenLogprob(
            token=c["token"],
            logprob=c["logprob"],
            top_logprobs=tuple(TopLogprob(t, lp) for t, lp in c["top_logprobs"]),
        )
        for c in CHUNKS
    ]
    from_objs = acquire.build_records(SAMPLE_REF, objs)
    from_dicts = acquire.build_records(SAMPLE_REF, CHUNKS)
    assert from_objs == from_dicts


def test_positive_logprob_is_rejected() -> None:
    with pytest.raises(acquire.AcquisitionError, match="positive"):
        acquire.build_records(SAMPLE_REF, [{"token": "x", "logprob": 0.5}])


# ── acquisition: engine_log path ────────────────────────────────────────────


def test_build_records_engine_log_path_from_fixture() -> None:
    recs = acquire.build_records(SAMPLE_REF, CHUNKS, ENGINE_LOG, window="supervised")
    assert len(recs) == 5
    first, last = recs[0], recs[-1]
    assert first.token_id == 791 and last.token_id == 4400  # engine ids, not text hashes
    assert first.logprob == pytest.approx(-0.223143551)  # raw logprob from the API
    assert first.rank_raw == 0 and last.rank_raw == 5  # raw rank from the API
    assert (first.u, first.cdf_lo, first.cdf_hi) == (0.2677, 0.0, 0.80)
    assert first.p_post == 0.80 and first.rank_post == 0 and first.n_candidates == 3
    assert first.entropy_post == 0.74 and first.temperature == 1.0 and first.min_p == 0.1
    assert first.top_entries == [(791, 0.80), (32, 0.15), (644, 0.05)]
    assert first.timestamp_ns == 1757800000000000000
    assert first.entropy_source_used == "qrng" and first.is_fallback is False
    assert last.entropy_source_used == "urandom" and last.is_fallback is True
    assert all(r.window == "supervised" for r in recs)
    assert endpoints.u_vs_r_agreement(recs)["agreement_fraction"] == 1.0
    # Round-trips through the JSONL form the store writes.
    rows = records.records_to_rows(recs)
    assert records.records_from_rows(rows) == recs


def test_engine_log_token_count_mismatch_is_an_error() -> None:
    with pytest.raises(acquire.AcquisitionError, match="engine log has 5 tokens"):
        acquire.build_records(SAMPLE_REF, CHUNKS[:4], ENGINE_LOG)


def test_engine_log_without_api_needs_raw_logprob(tmp_path: Path) -> None:
    with pytest.raises(acquire.AcquisitionError, match="no raw logprob"):
        acquire.build_records(SAMPLE_REF, [], ENGINE_LOG)
    rows = acquire.parse_engine_log(ENGINE_LOG)
    path = tmp_path / "with_logprob.jsonl"
    path.write_text(
        "\n".join(json.dumps({**r, "logprob": -1.0, "rank_raw": 0}) for r in rows) + "\n",
        encoding="utf-8",
    )
    recs = acquire.build_records(SAMPLE_REF, [], path)
    assert [r.logprob for r in recs] == [-1.0] * 5


def test_parse_engine_log_rejects_gaps_and_non_dep1_lines(tmp_path: Path) -> None:
    rows = acquire.parse_engine_log(ENGINE_LOG)
    gap = tmp_path / "gap.jsonl"
    gap.write_text(
        "\n".join(json.dumps(r) for r in rows if r["token_index"] != 2) + "\n", encoding="utf-8"
    )
    with pytest.raises(acquire.AcquisitionError, match="gap or duplicate"):
        acquire.parse_engine_log(gap)
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"token_id": 1}\n', encoding="utf-8")
    with pytest.raises(acquire.AcquisitionError, match="not a DEP-1 record"):
        acquire.parse_engine_log(bad)
    assert acquire.engine_log_path(tmp_path, SAMPLE_REF).name == (
        "qrng__creative_diversity__p001__0.jsonl"
    )


# ── availability matrix ─────────────────────────────────────────────────────


def test_availability_matrix_matches_cards() -> None:
    cards = load_cards()
    for card_id in TIER0_CARDS:
        card = cards[card_id]
        assert tuple(card.endpoints.all) == availability.CARD_ENDPOINTS[card_id]
        assert card.status == "implemented"
        # The card's declared floor is the weakest requirement among its endpoints.
        needs = {availability.requirement_for(ep) for ep in card.endpoints.all}
        assert card.endpoints.requires_tier0 == (
            "api_logprobs" if "api_logprobs" in needs else "engine_log"
        )
    assert set(availability.ENDPOINT_REQUIREMENTS) == {
        ep for eps in availability.CARD_ENDPOINTS.values() for ep in eps
    }


def test_endpoints_for_each_source() -> None:
    engine = availability.endpoints_for("engine_log")
    assert set(engine.values()) == {"available"}
    degraded = availability.endpoints_for("api_logprobs")
    assert availability.available_endpoints("api_logprobs") == {
        "mmd_hamming",
        "mmd_hamming_seed_vs_seed",
        "mean_logprob",
        "mean_rank",
        "argmax_fraction",
        "sequence_length",
        "repetition_rate",
    }
    assert degraded["pit_ks_two_sample"] == "unavailable"
    assert degraded["stouffer_z_supervised_vs_unsupervised"] == "unavailable"
    assert set(availability.endpoints_for(None).values()) == {"unavailable"}
    assert availability.card_status("tier0_pit_residuals", "api_logprobs") == "unavailable"
    assert availability.card_status("tier0_logprob_endpoints", "api_logprobs") == "partial"
    assert availability.card_status("tier0_mmd_hamming", "api_logprobs") == "available"
    with pytest.raises(KeyError, match="unknown Tier 0 endpoint"):
        availability.is_available("nope", "engine_log")


# ── T0-a: PIT residuals on the synthetic sampler ────────────────────────────


@pytest.fixture(scope="module")
def arms() -> dict[str, list[Tier0Record]]:
    return {
        "qrng": synth.synthesize("qrng", seed=1),
        "prng_7": synth.synthesize("prng_7", seed=7),
        "prng_42": synth.synthesize("prng_42", seed=42),
        "biased": synth.synthesize("biased", seed=3, epsilon=1.5),
    }


def test_pit_residuals_uniform_for_unbiased_and_not_for_biased(
    arms: dict[str, list[Tier0Record]],
) -> None:
    inputs = endpoints.pit_inputs(arms)
    assert inputs["qrng"].size == 5 * 4 * 24
    assert stats.ks_uniform(inputs["qrng"])["p_value"] > 0.01
    assert stats.anderson_darling_uniform(inputs["qrng"])["p_value"] > 0.01
    assert stats.ks_uniform(inputs["biased"])["p_value"] < 1e-6
    assert stats.ks_two_sample(inputs["qrng"], inputs["prng_7"])["p_value"] > 0.01
    assert stats.ks_two_sample(inputs["qrng"], inputs["biased"])["p_value"] < 1e-6
    # Deterministic under the seed; different V under another seed.
    np.testing.assert_array_equal(endpoints.pit_residuals(arms["qrng"]), inputs["qrng"])
    assert not np.array_equal(endpoints.pit_residuals(arms["qrng"], seed=9), inputs["qrng"])


def test_t0a_table_arms_and_contrasts(arms: dict[str, list[Tier0Record]]) -> None:
    table = endpoints.t0a_table(
        arms,
        {"qrng_vs_prng": ("qrng", ["prng_7", "prng_42"]), "qrng_vs_biased": ("qrng", "biased")},
        tier0_source="engine_log",
    )
    assert table["available"] and table["card"] == "tier0_pit_residuals"
    q = table["arms"]["qrng"]
    assert q["n_bracketed"] == q["n_tokens"]
    assert q["residual_mean"] == pytest.approx(0.5, abs=0.03)
    assert q["u_vs_r_agreement"]["agreement_fraction"] == 1.0
    assert q["u_uniformity"]["p_value"] > 0.01
    assert q["pit_first_token_ks"]["n"] == 5 * 4
    assert abs(q["pit_lag1_autocorr"]["r"]) < 0.1
    assert table["contrasts"]["qrng_vs_prng"]["b"] == "prng_7+prng_42"
    assert table["contrasts"]["qrng_vs_prng"]["pit_ks_two_sample"]["p_value"] > 0.01
    assert table["contrasts"]["qrng_vs_biased"]["pit_ks_two_sample"]["p_value"] < 1e-6
    json.dumps(table)  # tables are JSON-able


def test_t0a_unavailable_on_degraded_path() -> None:
    degraded = {"qrng": acquire.build_records(SAMPLE_REF, CHUNKS)}
    table = endpoints.t0a_table(degraded, tier0_source="api_logprobs")
    assert table["available"] is False and "engine_log" in table["reason"]
    # Even without a declared source, records lacking brackets yield no numbers.
    table = endpoints.t0a_table(degraded)
    assert table["available"] is False and table["arms"]["qrng"]["n_bracketed"] == 0
    assert table["arms"]["qrng"]["pit_ks_one_sample"]["statistic"] is None


# ── T0-b: MMD-Hamming on the synthetic sampler ──────────────────────────────


def test_mmd_inputs_pairs_prompts_across_arms(arms: dict[str, list[Tier0Record]]) -> None:
    inputs = endpoints.mmd_inputs(arms, "qrng", ["prng_7", "prng_42"])
    assert inputs.seqs_a.shape == (20, 24) and inputs.seqs_b.shape == (40, 24)
    assert set(inputs.prompts_a) == set(inputs.prompts_b)
    assert inputs.prompts_a[0] == "creative_diversity/p1"
    assert not (inputs.seqs_a == endpoints.PAD_TOKEN).any()
    with pytest.raises(ValueError, match="at least one sample"):
        endpoints.mmd_inputs({"a": [], "b": arms["qrng"]}, "a", "b")


def test_mmd_detects_planted_shift_and_is_null_seed_vs_seed(
    arms: dict[str, list[Tier0Record]],
) -> None:
    shifted = endpoints.mmd_inputs(arms, "qrng", "biased")
    res = mmd.mmd_hamming_permutation_test(
        shifted.seqs_a, shifted.seqs_b, shifted.prompts_a, shifted.prompts_b, n_permutations=300
    )
    assert res["p_value"] < 0.05 and res["n_prompts"] == 5
    null = endpoints.mmd_inputs(arms, "prng_7", "prng_42")
    res_null = mmd.mmd_hamming_permutation_test(
        null.seqs_a, null.seqs_b, null.prompts_a, null.prompts_b, n_permutations=300
    )
    assert res_null["p_value"] > 0.05


def test_t0b_table_descriptives(arms: dict[str, list[Tier0Record]]) -> None:
    table = endpoints.t0b_table(arms, tier0_source="api_logprobs")
    assert table["available"] and table["arms"]["qrng"] == {
        "n_samples": 20,
        "n_prompts": 5,
        "mean_length": 24.0,
        "max_length": 24,
    }


# ── T0-c: logprob endpoints ─────────────────────────────────────────────────


def test_sample_features_known_answer() -> None:
    recs = acquire.build_records(SAMPLE_REF, CHUNKS, ENGINE_LOG)
    feats = endpoints.sample_features(recs)
    assert feats["sequence_length"] == 5
    assert feats["mean_rank"] == pytest.approx((0 + 2 + 0 + 1 + 5) / 5)
    assert feats["argmax_fraction"] == pytest.approx(2 / 5)
    assert feats["mean_logprob"] == pytest.approx(np.mean([c["logprob"] for c in CHUNKS]))
    assert feats["mean_entropy"] == pytest.approx(np.mean([0.74, 1.21, 0.62, 1.02, 1.35]))
    assert feats["tail_fraction_p001"] == 0.0
    assert feats["repetition_rate"] == 0.0
    degraded = endpoints.sample_features(acquire.build_records(SAMPLE_REF, CHUNKS))
    assert degraded["mean_entropy"] is None and degraded["tail_fraction_p001"] is None
    assert endpoints.rank_histogram(recs)[:6] == [2, 1, 1, 0, 0, 1]


def test_t0c_table_availability_per_endpoint(arms: dict[str, list[Tier0Record]]) -> None:
    full = endpoints.t0c_table({"qrng": arms["qrng"]}, tier0_source="engine_log")
    q = full["arms"]["qrng"]
    assert q["n_samples"] == 20 and q["n_prompts"] == 5 and q["n_tokens"] == 480
    assert all(q[ep]["available"] for ep in endpoints.T0C_ENDPOINTS)
    assert len(q["rank_histogram"]) == endpoints.RANK_HIST_MAX + 1
    assert "truncated" in full["labels"]["mean_entropy_raw_top20"]
    rows = full["prompt_rows"]
    assert len(rows) == 5 and all(r["n_samples"] == 4 for r in rows)

    degraded = endpoints.t0c_table(
        {"qrng": acquire.build_records(SAMPLE_REF, CHUNKS)}, tier0_source="api_logprobs"
    )
    d = degraded["arms"]["qrng"]
    assert d["mean_logprob"]["available"] and d["sequence_length"]["available"]
    assert not d["mean_entropy"]["available"] and "engine_log" in d["mean_entropy"]["reason"]
    assert not d["tail_fraction_p001"]["available"]


# ── T4-l: stream deviation per window ───────────────────────────────────────


def test_stream_deviation_window_join() -> None:
    recs = synth.synthesize(
        "qrng_sup",
        seed=11,
        epochs=6,
        tokens=30,
        session_id="sess1",
        window_by_epoch=("supervised", "unsupervised", "break"),
    )
    bins = endpoints.second_bins(recs)
    assert {b.window for b in bins} == {"supervised", "unsupervised", "break"}
    assert sum(b.trials for b in bins) == len(recs)
    assert all(b.session_id == "sess1" for b in bins)
    assert bins == sorted(bins, key=lambda b: b.second)

    table = endpoints.stream_deviation_table(recs, n_permutations=200, tier0_source="engine_log")
    assert table["available"] and list(table["sessions"]) == ["sess1"]
    s = table["sessions"]["sess1"]
    assert s["n_tokens"] == len(recs)
    assert s["by_window"]["supervised"]["n_seconds"] > 0
    assert s["by_window"]["unsupervised"]["n_seconds"] > 0
    z = s["stouffer_z_supervised_vs_unsupervised"]["z"]
    assert abs(z) < 3.5 and 0 < s["stouffer_z_supervised_vs_unsupervised"]["p_value"] <= 1
    perm = s["window_permutation_p"]
    assert perm["statistic"] == pytest.approx(z) and 0 < perm["p_value"] <= 1
    cd = s["cumulative_deviation"]
    assert len(cd["cumdev"]) == len(cd["envelope_95"]) == s["n_seconds"]
    assert abs(cd["terminal_z"]) < 3.5
    json.dumps(table)


def test_stream_deviation_unavailable_without_u() -> None:
    degraded = acquire.build_records(SAMPLE_REF, CHUNKS, session_id="s")
    assert endpoints.stream_deviation_table(degraded, tier0_source="api_logprobs")["available"] is (
        False
    )
    table = endpoints.stream_deviation_table(degraded)
    assert table["available"] is False and table["sessions"] == {}


# ── the four zero-generation task modules ───────────────────────────────────


@pytest.mark.parametrize("card_id", TIER0_CARDS)
def test_task_modules_are_zero_generation(card_id: str) -> None:
    module = _load_module(REPO_ROOT / "evals" / card_id / "task.py", f"task_{card_id}")
    assert card_id == module.CARD_ID
    assert module.build_items() == []
    assert module.score({"item": {}, "text": ""}) == {}
    with pytest.raises(RuntimeError, match="generates nothing"):
        module.build_messages({})


def test_task_modules_score_from_tier0(arms: dict[str, list[Tier0Record]]) -> None:
    load = lambda cid: _load_module(REPO_ROOT / "evals" / cid / "task.py", f"t_{cid}")  # noqa: E731
    pit = load("tier0_pit_residuals").score_from_tier0(
        arms, contrasts={"q_vs_p": ("qrng", ["prng_7", "prng_42"])}, tier0_source="engine_log"
    )
    assert pit["card"] == "tier0_pit_residuals" and "q_vs_p" in pit["contrasts"]
    mmd_task = load("tier0_mmd_hamming")
    assert mmd_task.score_from_tier0(arms, permutations=2000)["n_permutations"] == 2000
    assert mmd_task.mmd_inputs(arms, "qrng", "prng_7").length == 24
    logprob_task = load("tier0_logprob_endpoints")
    assert logprob_task.score_from_tier0(arms)["card"] == "tier0_logprob_endpoints"
    sample = {"tier0": records.records_to_rows(arms["qrng"][:24])}
    assert logprob_task.score(sample)["sequence_length"] == 24
    stream = load("stream_deviation_windows").score_from_tier0(arms["qrng"], permutations=10)
    assert stream["card"] == "stream_deviation_windows"
