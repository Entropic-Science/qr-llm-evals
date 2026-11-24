"""Envelope correctness (spec §5.2): byte-pinned Study 1 envelopes, the
equivalence rule, greedy refusal, refused override keys, the full request body
and the provider path. The copied sibling-repo constants are compared live
against ``GET /v1/models`` (``--run-live``).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from pydantic import ValidationError

from qr_llm_evals.models.envelope import (
    AMPLIFIER_PINS,
    CREATIVE_GUARD,
    DETERMINISTIC_ENVELOPE,
    EQUIVALENCE_KEYS,
    GREEDY_QRNG_REASON,
    REFUSED_QR_KEYS,
    SAMPLE_COUNT,
    SAMPLE_MEAN_SE,
    SAMPLING_TABLE,
    SOURCE_TYPES,
    TOP_LOGPROBS,
    EnvelopeError,
    SampleContext,
    bent_population_mean,
    build_envelope,
    build_request_body,
    envelope_hash,
    envelope_json,
    equivalence_violations,
    token_log_xargs,
    validate_overrides,
)
from qr_llm_evals.models.providers import load_provider_profiles
from qr_llm_evals.models.schema import Arm, Conditioning, ModelRef

QR_LLM = ModelRef(id="qr-llm", family="qr_llm", provider="qr_server", served_name="qr-llm")
CTX = SampleContext(
    card_id="creative_diversity", prompt_id="p0001", epoch=0, max_tokens=512, thinking=False
)
MESSAGES = [{"role": "user", "content": "hi"}]


def _arm(id: str, **kw: Any) -> Arm:
    return Arm.model_validate(
        {"id": id, "model": "qr-llm", "sampling": "creative", "source": "qrng", **kw}
    )


#: Study 1 arms (PRD §7.1): supervised/unsupervised QRNG vs seeded PRNG at 1/7/42.
STUDY1_ARMS: dict[str, Arm] = {
    "qrng_supervised": _arm("qrng_supervised", observer_mode="supervised"),
    "qrng_unsupervised": _arm("qrng_unsupervised"),
    "prng_seed_1": _arm("prng_seed_1", source="prng_seeded", study_seed=1, deterministic=True),
    "prng_seed_7": _arm("prng_seed_7", source="prng_seeded", study_seed=7, deterministic=True),
    "prng_seed_42": _arm("prng_seed_42", source="prng_seeded", study_seed=42, deterministic=True),
}

_PINS = (
    '"qr_signal_amplifier_type":"zscore_mean","qr_entropy_prefetch":false,'
    '"qr_zscore_calibration_samples":0,"qr_sample_count":10000'
)
_CREATIVE = '"qr_preset":"creative_sampling","qr_top_p":0.95'

#: Byte-pinned wire form (insertion order preserved, like qr-llm-api's lanes tests).
#: Changing any byte here changes the envelope lines of every pre-registration.
PINNED: dict[str, str] = {
    "qrng_supervised": (
        '{"qr_entropy_source_type":"quantum_grpc",' + _CREATIVE + "," + _PINS + "}"
    ),
    "qrng_unsupervised": (
        '{"qr_entropy_source_type":"quantum_grpc",' + _CREATIVE + "," + _PINS + "}"
    ),
    "prng_seed_1": (
        '{"qr_entropy_source_type":"seeded_prng",'
        + _CREATIVE
        + ',"qr_seed":2558847153261501190,'
        + _PINS
        + "}"
    ),
    "prng_seed_7": (
        '{"qr_entropy_source_type":"seeded_prng",'
        + _CREATIVE
        + ',"qr_seed":4798978718871851284,'
        + _PINS
        + "}"
    ),
    "prng_seed_42": (
        '{"qr_entropy_source_type":"seeded_prng",'
        + _CREATIVE
        + ',"qr_seed":422598750703451419,'
        + _PINS
        + "}"
    ),
}


def _wire(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, separators=(",", ":"))


# ── copied constants ─────────────────────────────────────────────────────────


def test_copied_constants_match_qr_llm_api_lanes() -> None:
    # SOURCE: qr-llm-api src/qr_llm_api/lanes.py (see the comments in envelope.py).
    assert CREATIVE_GUARD == {"qr_top_p": 0.95}
    assert DETERMINISTIC_ENVELOPE == {
        "qr_signal_amplifier_type": "zscore_mean",
        "qr_entropy_prefetch": False,
        "qr_zscore_calibration_samples": 0,
    }
    # The harness adds the draw size, so no engine default decides it.
    assert AMPLIFIER_PINS == {**DETERMINISTIC_ENVELOPE, "qr_sample_count": SAMPLE_COUNT}
    assert SAMPLE_COUNT == 10_000


def test_refused_keys_mirror_qr_llm_research_and_owned_keys() -> None:
    # SOURCE: qr-llm-research client/qr_args.py REFUSED_QR_KEYS ⊂ ours.
    assert {
        "qr_fallback_mode",
        "qr_entropy_source_instances",
        "qr_entropy_source_type",
        "qr_preset",
    } <= REFUSED_QR_KEYS
    assert {
        "qr_seed",
        "qr_bypass",
        "qr_bias_epsilon",
        "qr_population_mean",
        "qr_population_std",
        "qr_diagnostic_mode",
        "qr_sample_count",
    } <= REFUSED_QR_KEYS


# ── byte-pinned Study 1 envelopes ────────────────────────────────────────────


@pytest.mark.parametrize("arm_id", sorted(STUDY1_ARMS))
def test_study1_envelope_bytes(arm_id: str) -> None:
    assert _wire(build_envelope(STUDY1_ARMS[arm_id], CTX)) == PINNED[arm_id]


def test_study1_arms_differ_only_in_entropy_keys() -> None:
    envelopes = {k: build_envelope(a, CTX) for k, a in STUDY1_ARMS.items()}
    assert equivalence_violations(envelopes) == []
    ref = envelopes["qrng_supervised"]
    for arm_id, env in envelopes.items():
        diff = {k for k in set(ref) | set(env) if ref.get(k) != env.get(k)}
        assert diff <= EQUIVALENCE_KEYS, f"{arm_id}: {diff}"
    # the two QRNG arms are byte-identical: observer mode never touches the wire
    assert envelopes["qrng_supervised"] == envelopes["qrng_unsupervised"]


def test_all_arms_pin_the_amplifier_including_qrng() -> None:
    """D-7: the QRNG arm does NOT inherit the public lane's process-default amplifier."""
    for arm in STUDY1_ARMS.values():
        env = build_envelope(arm, CTX)
        for key, value in AMPLIFIER_PINS.items():
            assert env[key] == value, (arm.id, key)


def test_seeded_envelope_is_deterministic_envelope_plus_seed() -> None:
    env = build_envelope(STUDY1_ARMS["prng_seed_1"], CTX)
    assert env["qr_entropy_source_type"] == "seeded_prng"
    assert {k: env[k] for k in DETERMINISTIC_ENVELOPE} == DETERMINISTIC_ENVELOPE
    assert 0 <= env["qr_seed"] < 2**63


def test_seed_varies_with_prompt_and_epoch_but_not_observer_mode() -> None:
    a = build_envelope(STUDY1_ARMS["prng_seed_1"], CTX)
    b = build_envelope(
        STUDY1_ARMS["prng_seed_1"],
        SampleContext(card_id="creative_diversity", prompt_id="p0002", epoch=0, max_tokens=512),
    )
    c = build_envelope(
        STUDY1_ARMS["prng_seed_1"],
        SampleContext(card_id="creative_diversity", prompt_id="p0001", epoch=1, max_tokens=512),
    )
    assert len({a["qr_seed"], b["qr_seed"], c["qr_seed"]}) == 3


# ── other sampling / source rows of the §2.2 tables ──────────────────────────


def test_every_sampling_arm_sends_its_draw_size_and_no_card_can_change_it() -> None:
    """The engine's process default never decides how many bytes a token draws."""
    arms = [
        *STUDY1_ARMS.values(),
        _arm("sys", source="prng_system"),
        _arm("eps", source="prng_epsilon", study_seed=1, epsilon=0.03),
        _arm("t1", sampling="t1_minp"),
    ]
    for arm in arms:
        assert build_envelope(arm, CTX)["qr_sample_count"] == SAMPLE_COUNT
    greedy = _arm("greedy", sampling="greedy", source="prng_system")
    assert "qr_sample_count" not in build_envelope(greedy, CTX)
    with pytest.raises(EnvelopeError):
        validate_overrides({"qr_sample_count": 1})


def test_source_type_table() -> None:
    assert SOURCE_TYPES == {
        "qrng": "quantum_grpc",
        "prng_seeded": "seeded_prng",
        "prng_system": "system",
        "prng_epsilon": "seeded_prng",
    }


def test_t1_minp_row() -> None:
    env = build_envelope(_arm("t1", sampling="t1_minp"), CTX)
    assert env["qr_preset"] == "normal_t1"
    assert env["qr_min_p_base"] == 0.10
    assert "qr_top_p" not in env


def test_system_prng_row() -> None:
    env = build_envelope(_arm("sys", source="prng_system"), CTX)
    assert env["qr_entropy_source_type"] == "system"
    assert "qr_seed" not in env


# ── entropy conditioning ─────────────────────────────────────────────────────

HASHED = Conditioning()
_HASH_KEYS = {
    "qr_conditioning_mode": "hash_truncate",
    "qr_conditioning_hash": "sha256",
    "qr_conditioning_in_bytes": 128,
    "qr_conditioning_keep_bytes": 16,
}


def test_a_new_conditioning_is_hashed_with_the_standard_parameters() -> None:
    assert HASHED.model_dump() == {
        "mode": "hash_truncate",
        "hash": "sha256",
        "in_bytes": 128,
        "keep_bytes": 16,
    }
    # 8 raw device bytes are read for every byte served.
    assert HASHED.read_ratio() == "8"
    assert Conditioning(in_bytes=40).read_ratio() == "2.5"
    assert Conditioning(mode="raw").read_ratio() == "1"


@pytest.mark.parametrize(
    "bad",
    [
        {"keep_bytes": 17},  # more than half a sha256 digest
        {"in_bytes": 31},  # less than twice keep_bytes
        {"hash": "sha512", "keep_bytes": 33},
        {"hash": "md5"},
        {"mode": "whiten"},
        {"in_bytes": 8192},
    ],
)
def test_conditioning_rules_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Conditioning.model_validate(bad)


@pytest.mark.parametrize(
    ("hash_name", "half"), [("sha256", 16), ("sha512", 32), ("sha3_256", 16), ("blake2b", 32)]
)
def test_hashing_always_discards_at_least_half_of_every_digest(hash_name: str, half: int) -> None:
    block = {"hash": hash_name, "in_bytes": 256}
    assert Conditioning.model_validate({**block, "keep_bytes": half}).keep_bytes == half
    with pytest.raises(ValidationError) as refused:
        Conditioning.model_validate({**block, "keep_bytes": half + 1})
    assert (
        "Serving more than half of each digest may compromise the integrity of the signal; "
        f"keep at most {half} bytes of a {hash_name} digest"
    ) in str(refused.value)


def test_a_hashed_block_holds_at_least_twice_the_bytes_kept() -> None:
    assert Conditioning.model_validate({"in_bytes": 32, "keep_bytes": 16}).in_bytes == 32
    with pytest.raises(ValidationError, match="at least twice the bytes kept"):
        Conditioning.model_validate({"in_bytes": 31, "keep_bytes": 16})


def test_hashed_conditioning_rides_only_entropy_server_arms() -> None:
    quantum = build_envelope(STUDY1_ARMS["qrng_unsupervised"], CTX, conditioning=HASHED)
    raw = build_envelope(STUDY1_ARMS["qrng_unsupervised"], CTX)
    assert quantum == {**raw, **_HASH_KEYS}
    for arm in (STUDY1_ARMS["prng_seed_1"], _arm("sys", source="prng_system")):
        assert build_envelope(arm, CTX, conditioning=HASHED) == build_envelope(arm, CTX)


def test_raw_conditioning_leaves_the_envelope_of_an_older_study() -> None:
    for arm in STUDY1_ARMS.values():
        raw = build_envelope(arm, CTX, conditioning=Conditioning(mode="raw"))
        assert envelope_json(raw) == envelope_json(build_envelope(arm, CTX))


def test_a_hashed_study_is_still_one_pipeline() -> None:
    envelopes = {k: build_envelope(a, CTX, conditioning=HASHED) for k, a in STUDY1_ARMS.items()}
    assert equivalence_violations(envelopes) == []
    assert set(_HASH_KEYS) <= EQUIVALENCE_KEYS


@pytest.mark.parametrize("key", sorted(_HASH_KEYS))
def test_a_card_cannot_set_the_conditioning(key: str) -> None:
    with pytest.raises(EnvelopeError):
        validate_overrides({key: _HASH_KEYS[key]})


def test_the_request_body_carries_the_study_conditioning() -> None:
    body = build_request_body(
        STUDY1_ARMS["qrng_unsupervised"], QR_LLM, CTX, MESSAGES, conditioning=HASHED
    )
    assert {k: body["vllm_xargs"][k] for k in _HASH_KEYS} == _HASH_KEYS


def test_epsilon_row_bends_the_reference_mean_only() -> None:
    eps = _arm("eps", source="prng_epsilon", study_seed=1, epsilon=0.03)
    seeded = STUDY1_ARMS["prng_seed_1"]
    e_env, s_env = build_envelope(eps, CTX), build_envelope(seeded, CTX)
    # u' = Φ(z + ε): the reference mean drops by ε standard errors of the byte mean
    assert e_env.pop("qr_population_mean") == pytest.approx(127.5 - 0.03 * 73.61215932167728 / 100)
    assert e_env == s_env
    assert bent_population_mean(0.5) == pytest.approx(127.5 - 0.5 * SAMPLE_MEAN_SE)


def test_greedy_is_bypass_not_fixed_temperature() -> None:
    env = build_envelope(_arm("g", sampling="greedy", source="prng_system"), CTX)
    assert env == {"qr_bypass": True}
    assert "qr_seed" not in build_envelope(
        _arm("gs", sampling="greedy", source="prng_seeded", study_seed=1), CTX
    ), "qr-sampler refuses seed + bypass"
    assert SAMPLING_TABLE["greedy"][0] is None


def test_greedy_refused_on_qrng_with_reason() -> None:
    with pytest.raises(ValueError, match=GREEDY_QRNG_REASON):
        Arm(id="g", model="qr-llm", sampling="greedy", source="qrng")
    # and the builder refuses it even if a non-validated arm reaches it
    arm = Arm.model_construct(
        id="g", model="qr-llm", sampling="greedy", source="qrng", study_seed=None, epsilon=None
    )
    with pytest.raises(EnvelopeError, match=GREEDY_QRNG_REASON):
        build_envelope(arm, CTX)


def test_greedy_arms_excluded_from_equivalence_check() -> None:
    envelopes = {k: build_envelope(a, CTX) for k, a in STUDY1_ARMS.items()}
    envelopes["greedy"] = build_envelope(_arm("g", sampling="greedy", source="prng_system"), CTX)
    assert equivalence_violations(envelopes) == []


def test_equivalence_violation_is_readable() -> None:
    envelopes = {
        "a": build_envelope(STUDY1_ARMS["qrng_supervised"], CTX),
        "t1": build_envelope(_arm("t1", sampling="t1_minp"), CTX),
    }
    (problem,) = equivalence_violations(envelopes)
    assert problem.startswith("t1 differs from a on ")
    assert "qr_preset" in problem


def test_provider_source_has_no_envelope() -> None:
    arm = Arm(id="ext", model="openai:gpt-4o", sampling="creative", source="provider")
    with pytest.raises(EnvelopeError, match="no qr_\\* envelope"):
        build_envelope(arm, CTX)


# ── overrides ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "qr_fallback_mode",
        "qr_entropy_source_type",
        "qr_seed",
        "qr_bypass",
        "qr_grpc_timeout",
        "qr_cb_x",
        "temperature",
    ],
)
def test_refused_override_keys(key: str) -> None:
    with pytest.raises(EnvelopeError, match="refused"):
        validate_overrides({key: 1})


def test_allowed_override_applies_last() -> None:
    env = build_envelope(STUDY1_ARMS["qrng_supervised"], CTX, {"qr_hvh_t_base": 1.2})
    assert env["qr_hvh_t_base"] == 1.2
    assert list(env)[-1] == "qr_hvh_t_base"


# ── hash / canonical bytes ───────────────────────────────────────────────────


def test_envelope_hash_is_order_independent_and_pinned() -> None:
    env = build_envelope(STUDY1_ARMS["qrng_supervised"], CTX)
    reordered = dict(reversed(list(env.items())))
    assert envelope_hash(env) == envelope_hash(reordered)
    assert envelope_json(env) == (
        '{"qr_entropy_prefetch":false,"qr_entropy_source_type":"quantum_grpc",'
        '"qr_preset":"creative_sampling","qr_sample_count":10000,'
        '"qr_signal_amplifier_type":"zscore_mean","qr_top_p":0.95,'
        '"qr_zscore_calibration_samples":0}'
    )
    assert envelope_hash(env) == "777c2922a9fdb2187dfb46ef1ce53e230823e6c033d4e54b8d5590ccbdd6f3df"


# ── full request body ────────────────────────────────────────────────────────


def test_request_body_common_pins() -> None:
    ctx = SampleContext(
        card_id="c",
        prompt_id="p",
        epoch=2,
        max_tokens=64,
        thinking=True,
        told_condition="told_qrng",
    )
    body = build_request_body(STUDY1_ARMS["prng_seed_7"], QR_LLM, ctx, MESSAGES, priority=5)
    assert body["model"] == "qr-llm"
    assert body["messages"] == MESSAGES
    assert body["max_tokens"] == 64
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["logprobs"] is True
    assert body["top_logprobs"] == TOP_LOGPROBS == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["request_id"] == "prng_seed_7/c/p/2/told_qrng"
    assert body["priority"] == 5
    assert body["vllm_xargs"] == build_envelope(STUDY1_ARMS["prng_seed_7"], ctx)
    assert "temperature" not in body
    assert not any(k.startswith("qr_") for k in body), "qr_* keys only inside vllm_xargs"


def test_request_body_greedy_sets_temperature_zero() -> None:
    body = build_request_body(
        _arm("g", sampling="greedy", source="prng_system"), QR_LLM, CTX, MESSAGES
    )
    assert body["temperature"] == 0.0
    assert body["vllm_xargs"] == {"qr_bypass": True}


def test_request_body_non_streaming_has_no_stream_options() -> None:
    body = build_request_body(STUDY1_ARMS["qrng_supervised"], QR_LLM, CTX, MESSAGES, stream=False)
    assert body["stream"] is False
    assert "stream_options" not in body


def test_request_body_rejects_model_mismatch() -> None:
    other = ModelRef(id="other", family="qr_llm", provider="qr_server", served_name="x")
    with pytest.raises(EnvelopeError, match="targets"):
        build_request_body(STUDY1_ARMS["qrng_supervised"], other, CTX, MESSAGES)


def test_qr_source_on_external_provider_refused() -> None:
    ext = ModelRef(id="openai:gpt-4o", family="external", provider="openai", served_name="gpt-4o")
    arm = Arm(id="q", model="openai:gpt-4o", sampling="creative", source="qrng")
    with pytest.raises(EnvelopeError, match="pinned qr-llm target"):
        build_request_body(arm, ext, CTX, MESSAGES)


def test_provider_body_uses_profile() -> None:
    profiles = load_provider_profiles()
    ext = ModelRef(id="openai:gpt-4o", family="external", provider="openai", served_name="gpt-4o")
    arm = Arm(id="ext", model="openai:gpt-4o", sampling="t1_minp", source="provider")
    body = build_request_body(arm, ext, CTX, MESSAGES, profile=profiles["openai"])
    assert body["model"] == "gpt-4o"
    assert body["temperature"] == 1.0
    assert "min_p" not in body, "openai does not accept min_p"
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    for absent in ("vllm_xargs", "request_id", "priority", "chat_template_kwargs", "seed"):
        assert absent not in body
    # unknown profile: plain surface, no logprobs
    plain = build_request_body(arm, ext, CTX, MESSAGES)
    assert "logprobs" not in plain and plain["temperature"] == 1.0


def test_provider_top_logprobs_capped() -> None:
    from qr_llm_evals.models.providers import ProviderProfile

    prof = ProviderProfile(
        id="x",
        accepted_params=("temperature", "logprobs", "top_logprobs"),
        logprobs_honoured=True,
        top_logprobs_cap=5,
    )
    ext = ModelRef(id="custom:http://h#m", family="external", provider="custom", served_name="m")
    arm = Arm(id="ext", model="custom:http://h#m", sampling="creative", source="provider")
    body = build_request_body(arm, ext, CTX, MESSAGES, profile=prof)
    assert body["top_logprobs"] == 5 and "top_p" not in body


# ── live: copied constants vs the edge's catalogue ───────────────────────────


@pytest.mark.live
def test_live_lane_descriptions_agree_with_copied_constants() -> None:
    """``GET /v1/models`` on the qr-llm-api edge describes the public lanes; the
    creative guard and the seeded control it names must match what we copied."""
    import httpx

    origin = os.environ.get("QR_EVALS_EDGE_ORIGIN", "http://127.0.0.1:8820").rstrip("/")
    docs = httpx.get(f"{origin}/v1/models", timeout=10).raise_for_status().json()["data"]
    by_id = {d["id"].rsplit("/", 1)[-1]: d for d in docs}
    creative = by_id["qr-llm"]["description"]
    assert f"top_p {CREATIVE_GUARD['qr_top_p']}" in creative
    assert "creative_sampling" in creative
    det = by_id["qr-llm-deterministic"]["description"].lower()
    assert "seed" in det and "no quantum entropy" in det


# ── per-token log request ────────────────────────────────────────────────────


def test_token_log_keys_ride_the_request_not_the_hashed_envelope() -> None:
    ctx = SampleContext(card_id="c", prompt_id="p", epoch=2, max_tokens=64)
    arm = STUDY1_ARMS["prng_seed_7"]
    envelope = build_envelope(arm, ctx)
    plain = build_request_body(arm, QR_LLM, ctx, MESSAGES)
    logged = build_request_body(arm, QR_LLM, ctx, MESSAGES, token_log=True)

    assert plain["vllm_xargs"] == envelope
    assert logged["vllm_xargs"] == {
        **envelope,
        "qr_token_log": True,
        "qr_request_id": "prng_seed_7/c/p/2",
    }
    # the engine names the log after the id the harness reads it back by
    assert logged["vllm_xargs"]["qr_request_id"] == logged["request_id"]
    assert token_log_xargs("a/b") == {"qr_token_log": True, "qr_request_id": "a/b"}
    # what the pre-registration and the provenance pin is unchanged
    assert not {"qr_token_log", "qr_request_id"} & set(envelope)
    assert {k: v for k, v in logged.items() if k != "vllm_xargs"} == {
        k: v for k, v in plain.items() if k != "vllm_xargs"
    }


def test_a_greedy_arm_asks_for_no_token_log() -> None:
    ctx = SampleContext(card_id="c", prompt_id="p", epoch=0, max_tokens=8)
    greedy = _arm("greedy_prng", sampling="greedy", source="prng_system")
    body = build_request_body(greedy, QR_LLM, ctx, MESSAGES, token_log=True)
    assert body["vllm_xargs"] == {"qr_bypass": True}
