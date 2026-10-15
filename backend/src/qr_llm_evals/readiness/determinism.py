"""Determinism-tier measurement (spec §2.2 toggle, §2.7 step 4).

The harness never flips engine settings; it *measures* what replaying one
seeded request gives back and records the strongest tier the evidence
supports (``models.schema.DeterminismTier``):

* ``none`` — the seeded replay differed even with the engine otherwise idle
  (or could not be measured);
* ``sampling_deterministic`` — ``replays`` solo replays of one ``qr_seed``
  returned identical tokens;
* ``load_invariant`` — they were also identical while ``load_streams``
  concurrent QRNG generations ran (batch composition did not change the
  sampled tokens);
* ``serialized_replay`` — the operator declared qr-sampler's
  ``tier2-replay.conf`` active (``QR_EVALS_TIER2_REPLAY_ACTIVE=1``:
  ``max_num_seqs=1``, prefix caching off) **and** the solo replay passed.
  Under a serialized engine the under-load replay is trivially identical
  (there is no concurrency), so it is skipped rather than reported.

Token identity is judged on the streamed token strings (vLLM's ``logprobs``
tokens are the tokenizer pieces, one per sampled id), which is what the
Tier 0 records compare too.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final

from qr_llm_evals.models.envelope import SampleContext
from qr_llm_evals.models.schema import Arm, DeterminismTier
from qr_llm_evals.readiness.probe import EngineProbe, ProbeResult, probe_context, request_id

logger = logging.getLogger("qr_llm_evals.readiness")

DEFAULT_REPLAYS: Final[int] = 5
DEFAULT_REPLAY_TOKENS: Final[int] = 32
DEFAULT_LOAD_STREAMS: Final[int] = 4
DEFAULT_LOAD_TOKENS: Final[int] = 128
REPLAY_PROMPT_ID: Final[str] = "replay"


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    """Evidence behind a tier. ``None`` = that phase was not measured."""

    tier: DeterminismTier
    solo_identical: bool | None
    load_identical: bool | None
    detail: str


def replays_identical(results: list[ProbeResult]) -> bool:
    """All accepted, all non-empty, all the same token sequence."""
    if not results or any(not r.accepted for r in results):
        return False
    signatures = {r.token_signature for r in results}
    return len(signatures) == 1 and signatures.pop() != ()


def resolve_tier(
    solo_identical: bool | None, load_identical: bool | None, *, tier2_replay_active: bool
) -> DeterminismTier:
    """Pure tier decision — pinned by tests so the wording above stays true."""
    if not solo_identical:
        return "none"
    if tier2_replay_active:
        return "serialized_replay"
    if load_identical:
        return "load_invariant"
    return "sampling_deterministic"


def _replay(
    probe: EngineProbe, arm: Arm, ctx: SampleContext, phase: str, index: int
) -> ProbeResult:
    return probe.generate(arm, ctx, request_id=request_id(arm.id, REPLAY_PROMPT_ID, phase, index))


def replay_solo(
    probe: EngineProbe, arm: Arm, *, replays: int = DEFAULT_REPLAYS, max_tokens: int
) -> list[ProbeResult]:
    """``replays`` sequential generations of one seeded context."""
    ctx = probe_context(REPLAY_PROMPT_ID, max_tokens=max_tokens)
    return [_replay(probe, arm, ctx, "solo", i) for i in range(replays)]


def replay_under_load(
    probe: EngineProbe,
    arm: Arm,
    load_arm: Arm,
    *,
    replays: int = DEFAULT_REPLAYS,
    max_tokens: int,
    load_streams: int = DEFAULT_LOAD_STREAMS,
    load_tokens: int = DEFAULT_LOAD_TOKENS,
) -> tuple[list[ProbeResult], list[ProbeResult]]:
    """``replays`` generations of the seeded context while ``load_streams`` QRNG streams run.

    Returns ``(replays, load_results)``; load failures are logged, not fatal.
    """
    ctx = probe_context(REPLAY_PROMPT_ID, max_tokens=max_tokens)
    load_ctx = probe_context("load", max_tokens=load_tokens)
    with ThreadPoolExecutor(max_workers=load_streams + replays) as pool:
        load_futures = [
            pool.submit(
                probe.generate, load_arm, load_ctx, request_id=request_id(load_arm.id, "load", i)
            )
            for i in range(load_streams)
        ]
        replay_futures = [pool.submit(_replay, probe, arm, ctx, "load", i) for i in range(replays)]
        replay_results = [f.result() for f in replay_futures]
        load_results = [f.result() for f in load_futures]
    for r in load_results:
        if not r.accepted:
            logger.warning("synthetic load stream %s failed: %s", r.request_id, r.reason)
    return replay_results, load_results


def measure_determinism(
    probe: EngineProbe,
    seeded_arm: Arm | None,
    load_arm: Arm | None,
    *,
    replays: int = DEFAULT_REPLAYS,
    replay_tokens: int = DEFAULT_REPLAY_TOKENS,
    load_streams: int = DEFAULT_LOAD_STREAMS,
    load_tokens: int = DEFAULT_LOAD_TOKENS,
    tier2_replay_active: bool = False,
) -> ReplayOutcome:
    """Spec §2.7 step 4. ``seeded_arm`` None ⇒ nothing to replay ⇒ ``none``.

    ``load_arm`` None ⇒ the QRNG envelope was not accepted (or absent), so the
    under-load phase is skipped rather than run against an engine that would
    answer 400 to every load stream and make the replay trivially "invariant".
    """
    if seeded_arm is None:
        return ReplayOutcome("none", None, None, "no seeded qr-llm arm accepted; nothing to replay")
    solo = replay_solo(probe, seeded_arm, replays=replays, max_tokens=replay_tokens)
    solo_ok = replays_identical(solo)
    if not solo_ok:
        failed = [r.reason for r in solo if not r.accepted]
        detail = (
            f"solo replay failed: {failed[0]}"
            if failed
            else f"{len(solo)} solo replays of one qr_seed returned different tokens"
        )
        return ReplayOutcome("none", False, None, detail)
    if tier2_replay_active:
        return ReplayOutcome(
            "serialized_replay",
            True,
            None,
            f"{len(solo)} solo replays identical; operator declares tier2-replay.conf active "
            "(max_num_seqs=1, prefix caching off), under-load replay not applicable",
        )
    if load_arm is None:
        return ReplayOutcome(
            "sampling_deterministic",
            True,
            None,
            f"{len(solo)} solo replays identical; no accepted QRNG arm to generate load",
        )
    under, _load = replay_under_load(
        probe,
        seeded_arm,
        load_arm,
        replays=replays,
        max_tokens=replay_tokens,
        load_streams=load_streams,
        load_tokens=load_tokens,
    )
    load_ok = replays_identical(under)
    tier = resolve_tier(True, load_ok, tier2_replay_active=False)
    verdict = "identical" if load_ok else "differed"
    detail = (
        f"{len(solo)} solo replays identical; {len(under)} replays under {load_streams} "
        f"concurrent QRNG streams {verdict}"
    )
    return ReplayOutcome(tier, True, load_ok, detail)


__all__ = [
    "DEFAULT_LOAD_STREAMS",
    "DEFAULT_LOAD_TOKENS",
    "DEFAULT_REPLAYS",
    "DEFAULT_REPLAY_TOKENS",
    "REPLAY_PROMPT_ID",
    "ReplayOutcome",
    "measure_determinism",
    "replay_solo",
    "replay_under_load",
    "replays_identical",
    "resolve_tier",
]
