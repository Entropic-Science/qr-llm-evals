"""The readiness check (spec §2.7, PRD §7.4 step 1): ``run_readiness`` → ``ReadinessReport``.

Six steps, in order, against the shared vLLM + qr-sampler on loopback:

1. ``GET /health/entropy`` — ``rpc_ok``, ``currently_degraded``, the
   ``fallback_count`` baseline (:class:`~qr_llm_evals.store.schema.EntropyHealth`).
2. Envelope dry-run — one ``max_tokens: 1`` request per arm; a ``400`` from
   qr-sampler's per-request validation makes the arm ``unavailable(reason)``.
   External-provider arms are not probed here (their BYO key arrives at launch).
3. Amplifier equivalence — the resolved ``qr_*`` dicts of all qr-llm arms may
   differ **only** in ``qr_entropy_source_type`` / ``qr_seed`` /
   ``qr_bias_epsilon``; otherwise the study would compare pipelines, not
   entropy, and the check **refuses** (:class:`EquivalenceError`).
4. Determinism tier — :mod:`~qr_llm_evals.readiness.determinism`.
5. ``qr_token_log`` accepted **and** the log file appears under
   ``QR_EVALS_TOKEN_LOG_DIR`` ⇒ ``tier0_source=engine_log`` (DEP-1), else the
   degraded ``api_logprobs``; ``qr_bias_epsilon`` accepted ⇒ ε arms available (DEP-2).
6. Throughput probe (opt-in) — :mod:`~qr_llm_evals.readiness.throughput`.

The report is persisted through the store when a ``RunStore`` is given
(``readiness.json`` plus the ``readiness`` / ``tier0_source`` /
``determinism_tier`` fields of ``run.json``). A refused report is persisted
too — ``equivalence_ok=false`` is a finding worth keeping — and then raised.

Layer 2: imports ``config``, ``client``, ``models`` (layer 0), ``store`` and
``tier0`` (layer 1). Nothing here touches the process environment; build a
:class:`ReadinessConfig` via :meth:`ReadinessConfig.from_settings`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from qr_llm_evals.client.openai_client import OpenAIClient
from qr_llm_evals.config import Settings
from qr_llm_evals.models.envelope import EnvelopeError, build_envelope, equivalence_violations
from qr_llm_evals.models.schema import (
    EPSILON_LEVELS,
    Arm,
    DeterminismTier,
    ModelRef,
    StudyConfig,
    Tier0Source,
)
from qr_llm_evals.readiness.determinism import (
    DEFAULT_LOAD_STREAMS,
    DEFAULT_LOAD_TOKENS,
    DEFAULT_REPLAY_TOKENS,
    DEFAULT_REPLAYS,
    ReplayOutcome,
    measure_determinism,
)
from qr_llm_evals.readiness.probe import (
    REQUEST_ID_PREFIX,
    EngineProbe,
    probe_context,
    request_id,
)
from qr_llm_evals.readiness.throughput import DEFAULT_STREAMS, DEFAULT_TOKENS, measure_throughput
from qr_llm_evals.store.schema import ArmReadiness, EntropyHealth, ReadinessReport
from qr_llm_evals.store.store import RunStore
from qr_llm_evals.tier0.acquire import engine_log_path

logger = logging.getLogger("qr_llm_evals.readiness")

ENTROPY_HEALTH_PATH: Final[str] = "/health/entropy"
#: Id of the synthetic probe arms the check derives when the study lacks a seeded / QRNG arm.
PROBE_ARM_PREFIX: Final[str] = "readiness_probe"
#: Reason recorded on external-provider arms (never probed at readiness).
PROVIDER_ARM_REASON: Final[str] = (
    "external provider arm: not probed at readiness (BYO key arrives at launch)"
)

Clock = Callable[[], datetime]
Sleep = Callable[[float], None]


class ReadinessError(RuntimeError):
    """The readiness check could not produce an honest report."""


class EquivalenceError(ReadinessError):
    """Spec §2.7 step 3: the qr-llm arms do not share one pipeline; refuse to proceed."""

    def __init__(self, violations: list[str], report: ReadinessReport) -> None:
        self.violations = violations
        self.report = report
        super().__init__(
            "refusing: qr-llm arms differ in more than the entropy source / seed / epsilon, "
            "so the study would compare sampling pipelines, not randomness. "
            "Give every arm the same sampling (and the pinned amplifier); "
            "violations: " + "; ".join(violations)
        )


@dataclass(frozen=True, slots=True)
class ReadinessConfig:
    """Process-level knobs for the check (``from_settings`` maps them from the env)."""

    vllm_origin: str
    priority: int = 0
    token_log_dir: str | None = None
    tier2_replay_active: bool = False
    replays: int = DEFAULT_REPLAYS
    replay_tokens: int = DEFAULT_REPLAY_TOKENS
    load_streams: int = DEFAULT_LOAD_STREAMS
    load_tokens: int = DEFAULT_LOAD_TOKENS
    throughput_probe: bool = False
    throughput_streams: int = DEFAULT_STREAMS
    throughput_tokens: int = DEFAULT_TOKENS
    request_timeout_s: float = 120.0
    health_timeout_s: float = 5.0
    token_log_wait_s: float = 2.0

    @classmethod
    def from_settings(
        cls, settings: Settings, *, throughput_probe: bool = False
    ) -> ReadinessConfig:
        return cls(
            vllm_origin=settings.vllm_origin,
            priority=settings.priority,
            token_log_dir=settings.token_log_dir,
            tier2_replay_active=settings.tier2_replay_active,
            throughput_probe=throughput_probe,
        )


# ── step 1: entropy health ───────────────────────────────────────────────────


def fetch_entropy_health(
    origin: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 5.0
) -> EntropyHealth:
    """``GET /health/entropy``; an unreachable engine is reported as not ok, degraded."""
    url = origin.rstrip("/") + ENTROPY_HEALTH_PATH
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            return EntropyHealth(
                rpc_ok=False,
                currently_degraded=True,
                fallback_count=0,
                detail={"error": f"HTTP {resp.status_code} from {url}"},
            )
        data: Any = resp.json()
    except (httpx.HTTPError, ValueError) as err:
        return EntropyHealth(
            rpc_ok=False,
            currently_degraded=True,
            fallback_count=0,
            detail={"error": f"{type(err).__name__}: {err}"},
        )
    if not isinstance(data, dict):
        return EntropyHealth(
            rpc_ok=False,
            currently_degraded=True,
            fallback_count=0,
            detail={"error": "non-object health body"},
        )
    sampler = data.get("sampler")
    sampler = sampler if isinstance(sampler, dict) else {}
    source = data.get("last_source_used") or data.get("source_type")
    known = {"rpc_ok", "currently_degraded", "fallback_count", "source_type", "last_source_used"}
    return EntropyHealth(
        rpc_ok=bool(data.get("rpc_ok", False)),
        currently_degraded=bool(
            data.get("currently_degraded", False) or sampler.get("currently_degraded", False)
        ),
        fallback_count=max(0, int(data.get("fallback_count", 0) or 0)),
        source_type=None if source is None else str(source),
        detail={k: v for k, v in data.items() if k not in known},
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _models(study: StudyConfig) -> dict[str, ModelRef]:
    return {m.id: m for m in study.models}


def _qr_arms(study: StudyConfig) -> list[Arm]:
    models = _models(study)
    return [a for a in study.arms if models[a.model].provider == "qr_server"]


def _probe_arm(study: StudyConfig, template: Arm, source: str) -> Arm:
    """A synthetic seeded / QRNG arm sharing ``template``'s model and sampling."""
    sampling = template.sampling
    if sampling == "greedy":
        sampling = "creative"  # greedy consults no randomness; cannot be a QRNG / replay arm
    seed = study.seeds[0] if source == "prng_seeded" else None
    return Arm(
        id=f"{PROBE_ARM_PREFIX}_{source}",
        model=template.model,
        sampling=sampling,
        source=source,  # type: ignore[arg-type]
        study_seed=seed,
        deterministic=template.deterministic,
    )


def _pick(arms: list[Arm], accepted: dict[str, bool], source: str) -> Arm | None:
    for arm in arms:
        if arm.source == source and accepted.get(arm.id):
            return arm
    return None


def _wait_for(path: Path, *, timeout_s: float, sleep: Sleep, perf: Callable[[], float]) -> bool:
    deadline = perf() + timeout_s
    while True:
        if path.is_file():
            return True
        if perf() >= deadline:
            return False
        sleep(0.05)


# ── the check ────────────────────────────────────────────────────────────────


def run_readiness(
    study: StudyConfig,
    config: ReadinessConfig,
    client: OpenAIClient | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    run_store: RunStore | None = None,
    clock: Clock | None = None,
    sleep: Sleep = time.sleep,
    perf: Callable[[], float] = time.perf_counter,
) -> ReadinessReport:
    """Run the six steps and return (and persist, when ``run_store`` is given) the report.

    Args:
        study: The frozen study whose arms are checked.
        config: Process knobs (see :class:`ReadinessConfig`).
        client: Optional prepared generation client; built from ``config`` if absent.
        transport: Injectable ``httpx`` transport for both the health probe and the
            default client (tests pass the stub engine's).
        run_store: When given, ``readiness.json`` and ``run.json`` are updated.
        clock / sleep / perf: Injectable time sources.

    Raises:
        EquivalenceError: step 3 failed; the report (persisted if possible) is attached.
    """
    now = clock or (lambda: datetime.now(UTC))
    gen_client = client or OpenAIClient(
        config.vllm_origin, timeout=config.request_timeout_s, transport=transport
    )
    models = _models(study)
    probe = EngineProbe(gen_client, models, priority=config.priority)

    # 1. entropy health
    entropy = fetch_entropy_health(
        config.vllm_origin, transport=transport, timeout=config.health_timeout_s
    )
    logger.info(
        "readiness: entropy rpc_ok=%s degraded=%s fallbacks=%d source=%s",
        entropy.rpc_ok,
        entropy.currently_degraded,
        entropy.fallback_count,
        entropy.source_type,
    )

    # 2. envelope dry-run per arm
    arms: dict[str, ArmReadiness] = {}
    envelopes: dict[str, dict[str, Any]] = {}
    accepted: dict[str, bool] = {}
    dry_ctx = probe_context("dry_run", max_tokens=1)
    for arm in study.arms:
        if models[arm.model].provider != "qr_server":
            arms[arm.id] = ArmReadiness(available=True, reason=PROVIDER_ARM_REASON)
            continue
        try:
            envelope = build_envelope(arm, dry_ctx)
        except EnvelopeError as err:
            arms[arm.id] = ArmReadiness(available=False, reason=str(err))
            accepted[arm.id] = False
            continue
        envelopes[arm.id] = envelope
        result = probe.generate(arm, dry_ctx, request_id=request_id(arm.id, "dry_run"))
        accepted[arm.id] = result.accepted
        arms[arm.id] = ArmReadiness(
            available=result.accepted,
            reason=None if result.accepted else result.reason,
            resolved_qr_args=envelope,
        )

    # 3. amplifier equivalence over the resolved qr_* dicts
    violations = equivalence_violations(envelopes)
    equivalence_ok = not violations

    qr_arms = _qr_arms(study)
    template = next((a for a in qr_arms if accepted.get(a.id)), None)

    # 4. determinism tier (seeded replay; QRNG load) — only on an equivalent, accepted pipeline
    outcome = ReplayOutcome("none", None, None, "not measured")
    epsilon_supported = False
    tier0_source: Tier0Source = "api_logprobs"
    tier0_detail = "not measured"
    measured_tps: float | None = None
    if equivalence_ok and template is not None:
        seeded = _pick(qr_arms, accepted, "prng_seeded") or _accepted_probe_arm(
            probe, study, template, "prng_seeded", accepted
        )
        load = _pick(qr_arms, accepted, "qrng") or _accepted_probe_arm(
            probe, study, template, "qrng", accepted
        )
        outcome = measure_determinism(
            probe,
            seeded,
            load,
            replays=config.replays,
            replay_tokens=config.replay_tokens,
            load_streams=config.load_streams,
            load_tokens=config.load_tokens,
            tier2_replay_active=config.tier2_replay_active,
        )
        logger.info("readiness: determinism_tier=%s (%s)", outcome.tier, outcome.detail)

        # 5. DEP-1 / DEP-2 capability probes
        tier0_source, tier0_detail = detect_tier0_source(
            probe, template, config, sleep=sleep, perf=perf
        )
        logger.info("readiness: tier0_source=%s (%s)", tier0_source, tier0_detail)
        if seeded is not None:
            epsilon_supported, eps_detail = detect_epsilon_support(probe, seeded)
            logger.info("readiness: epsilon_supported=%s (%s)", epsilon_supported, eps_detail)

        # 6. optional throughput probe (prefers the QRNG arm: the slowest pipeline)
        if config.throughput_probe:
            tp = measure_throughput(
                probe,
                load or template,
                streams=config.throughput_streams,
                max_tokens=config.throughput_tokens,
                perf=perf,
            )
            measured_tps = tp.tps
            logger.info(
                "readiness: throughput %d/%d streams, %d tokens in %.2fs → %s tok/s",
                tp.accepted,
                tp.streams,
                tp.tokens_generated,
                tp.elapsed_s,
                None if measured_tps is None else f"{measured_tps:.1f}",
            )

    arms = _annotate(arms, study, outcome.tier, epsilon_supported)
    report = ReadinessReport(
        at=now(),
        entropy=entropy,
        arms=arms,
        equivalence_ok=equivalence_ok,
        determinism_tier=outcome.tier,
        tier0_source=tier0_source,
        epsilon_supported=epsilon_supported,
        measured_tps=measured_tps,
    )
    if run_store is not None:
        persist(report, run_store)
    if violations:
        raise EquivalenceError(violations, report)
    return report


def _accepted_probe_arm(
    probe: EngineProbe, study: StudyConfig, template: Arm, source: str, accepted: dict[str, bool]
) -> Arm | None:
    """Derive a synthetic arm and dry-run it; ``None`` when the engine refuses it."""
    arm = _probe_arm(study, template, source)
    result = probe.generate(arm, probe_context("dry_run"), request_id=request_id(arm.id, "dry_run"))
    accepted[arm.id] = result.accepted
    if not result.accepted:
        logger.info("readiness: synthetic %s arm refused: %s", source, result.reason)
        return None
    return arm


def _annotate(
    arms: dict[str, ArmReadiness],
    study: StudyConfig,
    tier: DeterminismTier,
    epsilon_supported: bool,
) -> dict[str, ArmReadiness]:
    """Fold the tier / ε findings into per-arm availability and reasons."""
    out = dict(arms)
    for arm in study.arms:
        entry = out[arm.id]
        if arm.source == "prng_epsilon" and not epsilon_supported:
            # Either the dry-run already got the engine's 400 (keep its text) or the
            # capability probe failed later; both mean DEP-2 is absent.
            reason = "qr_bias_epsilon (DEP-2) not accepted by the engine"
            if entry.reason:
                reason = f"{reason}: {entry.reason}"
            out[arm.id] = entry.model_copy(update={"available": False, "reason": reason})
        elif arm.deterministic and entry.available and tier != "serialized_replay":
            out[arm.id] = entry.model_copy(
                update={
                    "reason": (
                        f"deterministic requested; measured determinism_tier={tier} "
                        "(Tier-2 serialized replay is not active on the engine)"
                    )
                }
            )
    return out


# ── step 5 probes ────────────────────────────────────────────────────────────


def detect_tier0_source(
    probe: EngineProbe,
    arm: Arm,
    config: ReadinessConfig,
    *,
    sleep: Sleep = time.sleep,
    perf: Callable[[], float] = time.perf_counter,
) -> tuple[Tier0Source, str]:
    """DEP-1: ``qr_token_log`` accepted **and** its file lands in ``token_log_dir`` ⇒ ``engine_log``."""
    rid = request_id(arm.id, "token_log")
    result = probe.generate(
        arm,
        probe_context("token_log"),
        request_id=rid,
        extra_xargs={"qr_token_log": True},  # nosec B105 - a per-request flag, not a credential
    )
    if not result.accepted:
        return "api_logprobs", f"qr_token_log refused: {result.reason}"
    if config.token_log_dir is None:
        return "api_logprobs", "qr_token_log accepted but QR_EVALS_TOKEN_LOG_DIR is unset"
    path = engine_log_path(Path(config.token_log_dir), result.request_id)
    if _wait_for(path, timeout_s=config.token_log_wait_s, sleep=sleep, perf=perf):
        return "engine_log", f"qr_token_log accepted; log file present at {path}"
    return "api_logprobs", f"qr_token_log accepted but no log file appeared at {path}"


def detect_epsilon_support(probe: EngineProbe, seeded_arm: Arm) -> tuple[bool, str]:
    """DEP-2: is ``qr_bias_epsilon`` accepted on the seeded source?"""
    eps = EPSILON_LEVELS[0]
    result = probe.generate(
        seeded_arm,
        probe_context("epsilon"),
        request_id=request_id(seeded_arm.id, "epsilon"),
        extra_xargs={"qr_bias_epsilon": eps},
    )
    if result.accepted:
        return True, f"qr_bias_epsilon={eps} accepted"
    return False, f"qr_bias_epsilon refused: {result.reason}"


# ── persistence ──────────────────────────────────────────────────────────────


def persist(report: ReadinessReport, run_store: RunStore) -> None:
    """Write ``readiness.json`` and mirror the summary fields into ``run.json``."""
    run_store.write_readiness(report)
    run = run_store.read_run()
    run_store.write_run(
        run.model_copy(
            update={
                "readiness": report,
                "tier0_source": report.tier0_source,
                "determinism_tier": report.determinism_tier,
            }
        )
    )


__all__ = [
    "ENTROPY_HEALTH_PATH",
    "PROBE_ARM_PREFIX",
    "PROVIDER_ARM_REASON",
    "REQUEST_ID_PREFIX",
    "EquivalenceError",
    "ReadinessConfig",
    "ReadinessError",
    "detect_epsilon_support",
    "detect_tier0_source",
    "fetch_entropy_health",
    "persist",
    "run_readiness",
]
