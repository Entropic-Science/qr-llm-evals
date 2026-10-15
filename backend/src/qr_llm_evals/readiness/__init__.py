"""Readiness check (spec §2.7): can the shared engine honestly run this study?

``run_readiness(study, config)`` performs the six pre-registration steps —
entropy health, per-arm envelope dry-run, amplifier equivalence, determinism
tier, DEP-1 / DEP-2 capability detection, optional throughput probe — and
returns (and persists) a ``ReadinessReport``. Nothing here flips engine
settings; the check *measures* and *records*.

Layer 2: imports ``config``, ``client``, ``models``, ``store``, ``tier0``.
"""

from __future__ import annotations

from qr_llm_evals.readiness.check import (
    EquivalenceError,
    ReadinessConfig,
    ReadinessError,
    detect_epsilon_support,
    detect_tier0_source,
    fetch_entropy_health,
    persist,
    run_readiness,
)
from qr_llm_evals.readiness.determinism import (
    ReplayOutcome,
    measure_determinism,
    replays_identical,
    resolve_tier,
)
from qr_llm_evals.readiness.probe import EngineProbe, ProbeResult, probe_context, request_id
from qr_llm_evals.readiness.throughput import ThroughputResult, measure_throughput

__all__ = [
    "EngineProbe",
    "EquivalenceError",
    "ProbeResult",
    "ReadinessConfig",
    "ReadinessError",
    "ReplayOutcome",
    "ThroughputResult",
    "detect_epsilon_support",
    "detect_tier0_source",
    "fetch_entropy_health",
    "measure_determinism",
    "measure_throughput",
    "persist",
    "probe_context",
    "replays_identical",
    "request_id",
    "resolve_tier",
    "run_readiness",
]
