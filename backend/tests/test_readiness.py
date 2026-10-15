"""Readiness-check contract tests (spec §2.7 six steps, §2.2 determinism toggle,
§2.6 DEP-1/DEP-2, §4.4 ReadinessReport, §5.3 live end-to-end).

Every test drives ``readiness.run_readiness`` against ``stub_engine.StubEngine``
(a deterministic fake vLLM + qr-sampler) so all six steps, the refusal path and
the persistence path are exercised without the box.
"""

from __future__ import annotations

import os
import random
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from qr_llm_evals.config import load_settings
from qr_llm_evals.models.envelope import EQUIVALENCE_KEYS
from qr_llm_evals.models.schema import Arm, StudyConfig, config_hash
from qr_llm_evals.readiness import (
    EngineProbe,
    EquivalenceError,
    ReadinessConfig,
    fetch_entropy_health,
    measure_determinism,
    measure_throughput,
    probe_context,
    replays_identical,
    request_id,
    resolve_tier,
    run_readiness,
)
from qr_llm_evals.readiness.check import PROBE_ARM_PREFIX, PROVIDER_ARM_REASON
from qr_llm_evals.readiness.probe import REQUEST_ID_PREFIX, ProbeResult
from qr_llm_evals.store import Run, RunStore, Store
from qr_llm_evals.tier0.acquire import engine_log_path
from stub_engine import ALLOWED_QR_KEYS, StubEngine

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
H = "b" * 64
MODEL = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
EXTERNAL = {
    "id": "openrouter:anthropic/claude",
    "family": "external",
    "provider": "openrouter",
    "served_name": "anthropic/claude",
}


def _study(arms: list[dict[str, Any]], models: list[dict[str, Any]] | None = None) -> StudyConfig:
    return StudyConfig.model_validate(
        {
            "title": "Readiness test study",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": models or [MODEL],
            "arms": arms,
            "cards": [{"card_id": "stub_free"}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": MODEL,
        }
    )


QRNG = {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"}
SEED7 = {
    "id": "prng_seed_7",
    "model": "qr-llm",
    "sampling": "creative",
    "source": "prng_seeded",
    "study_seed": 7,
}
EPS = {
    "id": "prng_eps",
    "model": "qr-llm",
    "sampling": "creative",
    "source": "prng_epsilon",
    "study_seed": 7,
    "epsilon": 0.01,
}


def _study1() -> StudyConfig:
    return _study([QRNG, SEED7])


def _config(**overrides: Any) -> ReadinessConfig:
    base: dict[str, Any] = {
        "vllm_origin": "http://stub",
        "replays": 5,
        "replay_tokens": 8,
        "load_streams": 2,
        "load_tokens": 8,
        "token_log_wait_s": 0.0,
    }
    base.update(overrides)
    return ReadinessConfig(**base)


def _run(
    study: StudyConfig, engine: StubEngine, config: ReadinessConfig | None = None, **kw: Any
) -> Any:
    return run_readiness(
        study, config or _config(), transport=engine.transport, clock=lambda: NOW, **kw
    )


def _refs(engine: StubEngine) -> list[str]:
    return [rid.rsplit("/", 1)[0] for rid in engine.request_ids]


# ── step 1: entropy health ───────────────────────────────────────────────────


def test_entropy_health_parsed() -> None:
    engine = StubEngine()
    health = fetch_entropy_health("http://stub", transport=engine.transport)
    assert health.rpc_ok and not health.currently_degraded
    assert health.fallback_count == 0 and health.source_type == "quantum_grpc"
    assert engine.health_reads == 1


def test_entropy_health_unreachable_is_degraded() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    health = fetch_entropy_health("http://stub", transport=httpx.MockTransport(boom))
    assert not health.rpc_ok and health.currently_degraded
    assert "ConnectError" in str(health.detail["error"])

    health = fetch_entropy_health(
        "http://stub", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    assert not health.rpc_ok and health.currently_degraded


# ── happy path: the Study 1 arms on a fully capable engine ───────────────────


def test_study1_report_all_green(tmp_path: Path) -> None:
    engine = StubEngine(n_tokens=8)
    log_dir = tmp_path / "token-log"
    log_dir.mkdir()

    def write_log(body: dict[str, Any]) -> None:
        if body["vllm_xargs"].get("qr_token_log"):
            engine_log_path(log_dir, body["request_id"]).write_text("{}\n", encoding="utf-8")

    engine.on_request = write_log

    report = _run(_study1(), engine, _config(token_log_dir=str(log_dir)))

    assert report.at == NOW
    assert report.entropy.rpc_ok
    assert set(report.arms) == {"qrng", "prng_seed_7"}
    assert all(a.available for a in report.arms.values())
    assert report.arms["qrng"].resolved_qr_args["qr_entropy_source_type"] == "quantum_grpc"
    assert report.arms["prng_seed_7"].resolved_qr_args["qr_entropy_source_type"] == "seeded_prng"
    assert "qr_seed" in report.arms["prng_seed_7"].resolved_qr_args
    assert report.equivalence_ok
    # the stub is a pure function of qr_seed ⇒ identical solo and under-load replays
    assert report.determinism_tier == "load_invariant"
    assert report.tier0_source == "engine_log"
    assert report.epsilon_supported  # stub allowlists qr_bias_epsilon
    assert report.measured_tps is None  # throughput probe is opt-in

    refs = _refs(engine)
    assert all(r.startswith(REQUEST_ID_PREFIX + "/") for r in refs)
    assert refs[:2] == [request_id("qrng", "dry_run"), request_id("prng_seed_7", "dry_run")]
    solo = [r for r in refs if "/replay/solo/" in r]
    under = [r for r in refs if "/replay/load/" in r]
    load = [r for r in refs if r.startswith(request_id("qrng", "load"))]
    assert len(solo) == 5 and len(under) == 5 and len(load) == 2
    # dry-runs are max_tokens: 1; the harness never forwards client qr_* keys
    dry = [b for b in engine.requests if b["request_id"].rsplit("/", 1)[0].endswith("/dry_run")]
    assert all(b["max_tokens"] == 1 for b in dry)
    assert all(set(b["vllm_xargs"]) <= ALLOWED_QR_KEYS for b in engine.requests)


def test_probe_bodies_are_harness_owned() -> None:
    """Probe bodies come from build_request_body: logprobs on, thinking off, envelope pinned."""
    engine = StubEngine()
    probe = EngineProbe(OpenAIClientFor(engine), {"qr-llm": _study1().models[0]}, priority=3)
    arm = _study1().arms[1]
    body = probe.body(arm, probe_context("x"), request_id="readiness/x")
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["priority"] == 3 and body["request_id"] == "readiness/x"
    assert body["vllm_xargs"]["qr_signal_amplifier_type"] == "zscore_mean"
    with_extra = probe.body(
        arm, probe_context("x"), request_id="r", extra_xargs={"qr_token_log": True}
    )
    assert with_extra["vllm_xargs"]["qr_token_log"] is True
    assert "qr_token_log" not in body["vllm_xargs"]


def OpenAIClientFor(engine: StubEngine) -> Any:  # noqa: N802 - test helper reads as a factory
    from qr_llm_evals.client import OpenAIClient

    return OpenAIClient("http://stub", transport=engine.transport)


# ── step 2: envelope dry-run ⇒ unavailable(reason) on 400 ────────────────────


def test_rejected_envelope_marks_arm_unavailable() -> None:
    """An engine without qr_top_p support answers 400 to the creative arm only."""
    allowed = frozenset(ALLOWED_QR_KEYS - {"qr_top_p"})
    engine = StubEngine(allowed_qr_keys=allowed)
    study = _study(
        [
            QRNG,
            SEED7,
            {
                "id": "t1",
                "model": "qr-llm",
                "sampling": "t1_minp",
                "source": "prng_seeded",
                "study_seed": 7,
            },
        ]
    )
    with pytest.raises(EquivalenceError):
        _run(study, engine)  # creative vs t1_minp is also a pipeline mismatch (step 3)

    engine = StubEngine(allowed_qr_keys=allowed)
    report = _run(_study1(), engine)
    for arm_id in ("qrng", "prng_seed_7"):
        entry = report.arms[arm_id]
        assert not entry.available
        assert entry.reason is not None and "400" in entry.reason and "qr_top_p" in entry.reason
        assert entry.resolved_qr_args["qr_top_p"] == 0.95  # what was *sent* is still recorded
    assert report.equivalence_ok  # envelopes were equivalent; the engine just refused them
    assert report.determinism_tier == "none"
    assert report.tier0_source == "api_logprobs"
    assert not report.epsilon_supported
    assert len(engine.requests) == 2  # nothing beyond the two dry-runs


def test_unreachable_engine_is_unavailable_not_400() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    report = run_readiness(
        _study1(), _config(), transport=httpx.MockTransport(boom), clock=lambda: NOW
    )
    assert not report.entropy.rpc_ok
    for entry in report.arms.values():
        assert not entry.available
        assert entry.reason is not None and "unreachable" in entry.reason
    assert report.determinism_tier == "none"


def test_external_provider_arms_are_listed_not_probed() -> None:
    engine = StubEngine()
    ext_arm = {
        "id": "ext",
        "model": EXTERNAL["id"],
        "sampling": "creative",
        "source": "provider",
    }
    report = _run(_study([QRNG, SEED7, ext_arm], models=[MODEL, EXTERNAL]), engine)
    assert report.arms["ext"].available
    assert report.arms["ext"].reason == PROVIDER_ARM_REASON
    assert report.arms["ext"].resolved_qr_args == {}
    assert not any(r.startswith("readiness/ext/") for r in _refs(engine))
    assert report.equivalence_ok


# ── step 3: amplifier equivalence — refuse on non-equivalence ────────────────


def test_non_equivalent_arms_refused_with_clear_message(tmp_path: Path) -> None:
    engine = StubEngine()
    study = _study(
        [
            QRNG,
            {
                "id": "prng_t1",
                "model": "qr-llm",
                "sampling": "t1_minp",
                "source": "prng_seeded",
                "study_seed": 7,
            },
        ]
    )
    run_store = _run_store(tmp_path, study)
    with pytest.raises(EquivalenceError) as info:
        _run(study, engine, run_store=run_store)
    err = info.value
    assert "refusing" in str(err) and "not randomness" in str(err)
    assert err.violations and "prng_t1 differs from qrng" in err.violations[0]
    assert "qr_preset" in err.violations[0] and "qr_min_p_base" in err.violations[0]
    assert not err.report.equivalence_ok
    # no measurement was spent on a study that will not run
    assert err.report.determinism_tier == "none"
    assert len(engine.requests) == 2
    # the refused report is still persisted (a finding worth keeping)
    persisted = run_store.read_readiness()
    assert persisted is not None and not persisted.equivalence_ok
    assert run_store.read_run().readiness is not None


def test_equivalence_keys_are_exactly_the_entropy_keys() -> None:
    assert {"qr_entropy_source_type", "qr_seed", "qr_bias_epsilon"} == EQUIVALENCE_KEYS


def test_greedy_sanity_arm_does_not_break_equivalence() -> None:
    engine = StubEngine()
    greedy = {
        "id": "greedy",
        "model": "qr-llm",
        "sampling": "greedy",
        "source": "prng_system",
    }
    report = _run(_study([QRNG, SEED7, greedy]), engine)
    assert report.equivalence_ok
    assert report.arms["greedy"].available
    assert report.arms["greedy"].resolved_qr_args == {"qr_bypass": True}


# ── step 4: determinism tier ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("solo", "load", "flag", "tier"),
    [
        (False, None, False, "none"),
        (None, None, True, "none"),
        (True, None, False, "sampling_deterministic"),
        (True, False, False, "sampling_deterministic"),
        (True, True, False, "load_invariant"),
        (True, None, True, "serialized_replay"),
        (True, True, True, "serialized_replay"),
    ],
)
def test_resolve_tier(solo: bool | None, load: bool | None, flag: bool, tier: str) -> None:
    assert resolve_tier(solo, load, tier2_replay_active=flag) == tier


def test_replays_identical() -> None:
    def ok(tokens: tuple[str, ...]) -> ProbeResult:
        from qr_llm_evals.client import Completion
        from qr_llm_evals.client.sse import TokenLogprob

        c = Completion(logprobs=[TokenLogprob(t, -0.1) for t in tokens])
        return ProbeResult("r", True, 200, completion=c)

    assert replays_identical([ok(("a", "b")), ok(("a", "b"))])
    assert not replays_identical([ok(("a", "b")), ok(("a", "c"))])
    assert not replays_identical([ok(()), ok(())])
    assert not replays_identical([ok(("a",)), ProbeResult("r", False, 400, reason="no")])
    assert not replays_identical([])


class _NondeterministicEngine(StubEngine):
    """Ignores qr_seed: every replay differs (an engine with no sampling determinism)."""

    def _frames(self, body: dict[str, Any]) -> list[bytes]:
        body = dict(body)
        xargs = dict(body.get("vllm_xargs") or {})
        xargs.pop("qr_seed", None)
        body["vllm_xargs"] = xargs
        body["request_id"] = f"{body.get('request_id')}#{random.random()}"  # noqa: S311
        return super()._frames(body)


class _LoadSensitiveEngine(StubEngine):
    """Deterministic solo, but any concurrent stream perturbs the replayed tokens."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._active = 0
        self._peak_lock = threading.Lock()
        self._gate = threading.Barrier(1)

    def _frames(self, body: dict[str, Any]) -> list[bytes]:
        rid = str(body.get("request_id"))
        if "/replay/load/" in rid:
            body = dict(body)
            body["request_id"] = rid  # already unique per replay ⇒ differs
            xargs = dict(body.get("vllm_xargs") or {})
            xargs.pop("qr_seed", None)
            body["vllm_xargs"] = xargs
        return super()._frames(body)


def test_tier_none_when_solo_replay_differs() -> None:
    engine = _NondeterministicEngine(n_tokens=8)
    report = _run(_study1(), engine)
    assert report.determinism_tier == "none"
    assert report.arms["prng_seed_7"].available  # the arm still *runs*; only the tier is honest
    assert not any("/replay/load/" in r for r in _refs(engine))  # no load phase after a solo fail


def test_tier_sampling_deterministic_when_load_perturbs() -> None:
    engine = _LoadSensitiveEngine(n_tokens=8)
    report = _run(_study1(), engine)
    assert report.determinism_tier == "sampling_deterministic"


def test_tier_serialized_replay_from_operator_flag() -> None:
    engine = StubEngine(n_tokens=8)
    report = _run(_study1(), engine, _config(tier2_replay_active=True))
    assert report.determinism_tier == "serialized_replay"
    assert not any("/replay/load/" in r for r in _refs(engine))  # under-load phase skipped
    assert not any(r.startswith(request_id("qrng", "load")) for r in _refs(engine))


def test_operator_flag_never_overrides_a_failed_replay() -> None:
    """QR_EVALS_TIER2_REPLAY_ACTIVE is a declaration, not evidence."""
    engine = _NondeterministicEngine(n_tokens=8)
    report = _run(_study1(), engine, _config(tier2_replay_active=True))
    assert report.determinism_tier == "none"


def test_deterministic_arm_gets_reason_when_tier2_not_active() -> None:
    engine = StubEngine(n_tokens=8)
    det = dict(SEED7, id="det", deterministic=True)
    report = _run(_study([QRNG, det]), engine)
    assert report.arms["det"].available
    assert report.arms["det"].reason is not None
    assert "determinism_tier=load_invariant" in report.arms["det"].reason
    report = _run(_study([QRNG, det]), StubEngine(n_tokens=8), _config(tier2_replay_active=True))
    assert report.arms["det"].reason is None


def test_synthetic_probe_arms_when_study_lacks_seeded_or_qrng_arm() -> None:
    engine = StubEngine(n_tokens=8)
    report = _run(_study([QRNG]), engine)  # no seeded arm ⇒ derive one for the replay
    assert report.determinism_tier == "load_invariant"
    assert set(report.arms) == {"qrng"}  # synthetic arms never enter the report
    assert any(r.startswith(f"readiness/{PROBE_ARM_PREFIX}_prng_seeded/") for r in _refs(engine))

    engine = StubEngine(n_tokens=8)
    report = _run(_study([SEED7]), engine)  # no QRNG arm ⇒ derive one for the load
    assert report.determinism_tier == "load_invariant"
    assert any(r.startswith(f"readiness/{PROBE_ARM_PREFIX}_qrng/load") for r in _refs(engine))


def test_measure_determinism_without_seeded_arm() -> None:
    engine = StubEngine()
    probe = EngineProbe(OpenAIClientFor(engine), {"qr-llm": _study1().models[0]})
    outcome = measure_determinism(probe, None, None)
    assert outcome.tier == "none" and outcome.solo_identical is None
    assert engine.requests == []


# ── step 5: DEP-1 / DEP-2 detection ──────────────────────────────────────────


def test_tier0_source_degraded_when_token_log_refused() -> None:
    engine = StubEngine(n_tokens=8, allowed_qr_keys=frozenset(ALLOWED_QR_KEYS - {"qr_token_log"}))
    report = _run(_study1(), engine, _config(token_log_dir="/nonexistent"))
    assert report.tier0_source == "api_logprobs"
    assert report.arms["qrng"].available  # the refusal only affects the capability probe


def test_tier0_source_degraded_when_log_dir_unset_or_file_missing(tmp_path: Path) -> None:
    report = _run(_study1(), StubEngine(n_tokens=8), _config(token_log_dir=None))
    assert report.tier0_source == "api_logprobs"
    report = _run(_study1(), StubEngine(n_tokens=8), _config(token_log_dir=str(tmp_path)))
    assert report.tier0_source == "api_logprobs"


def test_tier0_source_engine_log_when_file_appears_late(tmp_path: Path) -> None:
    """The engine writes the log asynchronously; the check waits up to token_log_wait_s."""
    engine = StubEngine(n_tokens=8)
    ticks = {"n": 0}

    def perf() -> float:
        ticks["n"] += 1
        return ticks["n"] * 0.1

    def sleep(_: float) -> None:
        if ticks["n"] >= 3:
            rid = next(r for r in engine.request_ids if "/token_log/" in r)
            engine_log_path(tmp_path, rid).write_text("{}\n", encoding="utf-8")

    report = _run(
        _study1(),
        engine,
        _config(token_log_dir=str(tmp_path), token_log_wait_s=5.0),
        sleep=sleep,
        perf=perf,
    )
    assert report.tier0_source == "engine_log"


def test_epsilon_arm_unavailable_when_dep2_missing() -> None:
    """Study with an ε arm on an engine without qr_bias_epsilon: the arm is listed, unavailable."""
    engine = StubEngine(
        n_tokens=8, allowed_qr_keys=frozenset(ALLOWED_QR_KEYS - {"qr_bias_epsilon"})
    )
    report = _run(_study([QRNG, SEED7, EPS]), engine)
    assert not report.epsilon_supported
    eps = report.arms["prng_eps"]
    assert not eps.available
    assert eps.reason is not None and "DEP-2" in eps.reason
    assert eps.resolved_qr_args["qr_bias_epsilon"] == 0.01
    assert report.equivalence_ok  # ε is an allowed difference
    assert report.arms["qrng"].available and report.arms["prng_seed_7"].available


def test_epsilon_arm_available_when_dep2_present() -> None:
    report = _run(_study([QRNG, SEED7, EPS]), StubEngine(n_tokens=8))
    assert report.epsilon_supported
    assert report.arms["prng_eps"].available and report.arms["prng_eps"].reason is None


# ── step 6: throughput probe ─────────────────────────────────────────────────


def test_throughput_probe_opt_in() -> None:
    engine = StubEngine(n_tokens=64)
    clock = iter([0.0, 2.0])
    report = run_readiness(
        _study1(),
        _config(throughput_probe=True, throughput_streams=4, throughput_tokens=64),
        transport=engine.transport,
        clock=lambda: NOW,
        perf=lambda: next(clock, 2.0),
    )
    assert report.measured_tps is not None
    tp = [r for r in _refs(engine) if r.startswith(request_id("qrng", "throughput"))]
    assert len(tp) == 4
    assert report.measured_tps == pytest.approx(4 * 64 / 2.0)
    assert all(b["max_tokens"] == 64 for b in engine.requests if "/throughput/" in b["request_id"])


def test_measure_throughput_none_when_nothing_generated() -> None:
    engine = StubEngine()
    engine.on_request = lambda body: engine.fail_refs.add(body["request_id"])
    probe = EngineProbe(OpenAIClientFor(engine), {"qr-llm": _study1().models[0]})
    result = measure_throughput(probe, _study1().arms[0], streams=2, max_tokens=4)
    assert result.accepted == 0 and result.tps is None


# ── persistence via the store ────────────────────────────────────────────────


def _run_store(root: Path, study: StudyConfig, run_id: str = "run1") -> RunStore:
    store = Store(root)
    study_dir = store.freeze_study(study, "# prereg\n")
    return store.create_run(
        Run(
            run_id=run_id,
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash=H,
            config_hash=config_hash(study),
        )
    )


def test_report_persisted_into_run(tmp_path: Path) -> None:
    study = _study1()
    run_store = _run_store(tmp_path / "data", study)
    report = _run(study, StubEngine(n_tokens=8), run_store=run_store)
    assert run_store.read_readiness() == report
    run = run_store.read_run()
    assert run.readiness == report
    assert run.tier0_source == "api_logprobs"
    assert run.determinism_tier == "load_invariant"
    # the report survives a JSON round trip byte-for-byte relevant fields
    assert run_store.run_dir.readiness_json.is_file()


def test_report_never_contains_secrets(tmp_path: Path) -> None:
    study = _study1()
    run_store = _run_store(tmp_path / "data", study)
    _run(study, StubEngine(n_tokens=8), run_store=run_store)
    text = run_store.run_dir.readiness_json.read_text(encoding="utf-8")
    assert "Authorization" not in text and "Bearer" not in text and "sk-" not in text


# ── config plumbing ──────────────────────────────────────────────────────────


def test_readiness_config_from_settings() -> None:
    settings = load_settings(
        {
            "QR_EVALS_VLLM_ORIGIN": "http://127.0.0.1:8000/v1",
            "QR_EVALS_PRIORITY": "2",
            "QR_EVALS_TOKEN_LOG_DIR": "/var/lib/qr-sampler/token-log",
            "QR_EVALS_TIER2_REPLAY_ACTIVE": "yes",
        }
    )
    cfg = ReadinessConfig.from_settings(settings, throughput_probe=True)
    assert cfg.vllm_origin == "http://127.0.0.1:8000"
    assert cfg.priority == 2 and cfg.token_log_dir == "/var/lib/qr-sampler/token-log"
    assert cfg.tier2_replay_active and cfg.throughput_probe
    assert not load_settings({}).tier2_replay_active
    assert not load_settings({"QR_EVALS_TIER2_REPLAY_ACTIVE": "off"}).tier2_replay_active
    with pytest.raises(SystemExit):
        load_settings({"QR_EVALS_TIER2_REPLAY_ACTIVE": "maybe"})


def test_probe_context_shares_seed_across_replays() -> None:
    from qr_llm_evals.models.envelope import request_seed

    arm = Arm.model_validate(SEED7)
    ctx = probe_context("replay", max_tokens=8)
    assert request_seed(arm, ctx) == request_seed(arm, probe_context("replay", max_tokens=8))
    assert request_seed(arm, ctx) != request_seed(arm, probe_context("other"))


# ── live: end-to-end on the box (spec §5.3) ──────────────────────────────────


@pytest.mark.live
def test_live_readiness_end_to_end(tmp_path: Path) -> None:
    """Study 1 arms on the real engine: health, envelopes accepted, equivalence,
    5× solo replay identical for the seeded arm, tier0_source detected."""
    settings = load_settings(dict(os.environ))
    study = _study1()
    run_store = _run_store(tmp_path / "data", study)
    report = run_readiness(study, ReadinessConfig.from_settings(settings), run_store=run_store)
    assert report.entropy.rpc_ok and not report.entropy.currently_degraded
    assert report.entropy.source_type in (None, "quantum_grpc")
    for arm_id, entry in report.arms.items():
        assert entry.available, f"{arm_id}: {entry.reason}"
    assert report.equivalence_ok
    assert report.determinism_tier != "none", "seeded replay must be reproducible on the box"
    assert report.tier0_source in ("engine_log", "api_logprobs")
    assert run_store.read_run().determinism_tier == report.determinism_tier


def test_probe_ids_are_unique_without_changing_sampling_context() -> None:
    engine = StubEngine()
    study = _study1()
    for _ in range(2):
        probe = EngineProbe(OpenAIClientFor(engine), {"qr-llm": study.models[0]})
        for _ in range(2):
            result = probe.generate(
                study.arms[1], probe_context("replay"), request_id="readiness/repeated"
            )
            assert result.accepted and result.request_id == engine.request_ids[-1]
    assert len(set(engine.request_ids)) == 4
    bodies = [{k: v for k, v in body.items() if k != "request_id"} for body in engine.requests]
    assert all(body == bodies[0] for body in bodies)


@pytest.mark.parametrize("finish_reason", [None, "error", "cancelled", "content_filter"])
def test_incomplete_probe_is_not_replay_evidence(finish_reason: str | None) -> None:
    from qr_llm_evals.client import Completion

    class IncompleteClient:
        def generate(self, body: Any) -> Completion:
            return Completion(text="partial", finish_reason=finish_reason)

    probe = EngineProbe(IncompleteClient(), {"qr-llm": _study1().models[0]})  # type: ignore[arg-type]
    result = probe.generate(_study1().arms[1], probe_context("x"), request_id="readiness/x")
    assert not result.accepted
    assert not replays_identical([result] * 5)


def test_stale_token_log_cannot_pass_new_probe(tmp_path: Path) -> None:
    engine_log_path(tmp_path, request_id("qrng", "token_log")).write_text("{}\n", encoding="utf-8")
    report = _run(_study1(), StubEngine(), _config(token_log_dir=str(tmp_path)))
    assert report.tier0_source == "api_logprobs"
