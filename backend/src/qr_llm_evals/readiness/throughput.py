"""Optional throughput probe (spec §2.7 step 6).

``streams`` concurrent ``max_tokens``-token generations on one qr-llm arm;
aggregate tokens/s = generated tokens ÷ wall-clock of the whole batch. The
number refreshes ``EstimateAssumptions.tps_by_model`` in the UI, nothing else.
It is off by default because it spends real QRNG time (4 × 256 tokens).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final

from qr_llm_evals.models.schema import Arm
from qr_llm_evals.readiness.probe import EngineProbe, ProbeResult, probe_context, request_id

DEFAULT_STREAMS: Final[int] = 4
DEFAULT_TOKENS: Final[int] = 256

Perf = Callable[[], float]


@dataclass(frozen=True, slots=True)
class ThroughputResult:
    streams: int
    accepted: int
    tokens_generated: int
    elapsed_s: float

    @property
    def tps(self) -> float | None:
        """Aggregate tokens/s, ``None`` when nothing was generated or no time elapsed."""
        if self.tokens_generated <= 0 or self.elapsed_s <= 0:
            return None
        return self.tokens_generated / self.elapsed_s


def measure_throughput(
    probe: EngineProbe,
    arm: Arm,
    *,
    streams: int = DEFAULT_STREAMS,
    max_tokens: int = DEFAULT_TOKENS,
    perf: Perf = time.perf_counter,
) -> ThroughputResult:
    ctx = probe_context("throughput", max_tokens=max_tokens)
    started = perf()
    with ThreadPoolExecutor(max_workers=streams) as pool:
        futures = [
            pool.submit(probe.generate, arm, ctx, request_id=request_id(arm.id, "throughput", i))
            for i in range(streams)
        ]
        results: list[ProbeResult] = [f.result() for f in futures]
    elapsed = perf() - started
    accepted = [r for r in results if r.accepted]
    return ThroughputResult(
        streams=streams,
        accepted=len(accepted),
        tokens_generated=sum(r.completion_tokens for r in accepted),
        elapsed_s=elapsed,
    )


__all__ = ["DEFAULT_STREAMS", "DEFAULT_TOKENS", "ThroughputResult", "measure_throughput"]
