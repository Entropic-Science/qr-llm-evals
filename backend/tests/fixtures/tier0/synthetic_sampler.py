"""A synthetic inverse-CDF sampler that emits engine-path ``Tier0Record`` streams.

Used by ``backend/tests/test_tier0.py`` to check the endpoints against known
truth: with an unbiased uniform the PIT residuals are U[0,1] and two arms are
exchangeable; with ``epsilon > 0`` the consumed uniform is warped as
``u' = u ** (1 + ε)`` (the DEP-2 positive-control shape), which shifts token
choice towards the head of the CDF and must be detected.

The "model" is context-free: the logits at ``(card, prompt, token_index)`` are
a deterministic function of that triple, identical across arms and epochs, so
any difference between arms comes only from the uniforms consumed.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

import numpy as np

from qr_llm_evals.tier0.schema import Tier0Record, Window

VOCAB = 64
TOP_K = 20
NS_PER_TOKEN = 40_000_000  # 25 tokens/s


def _logits(card_id: str, prompt_id: str, token_index: int, vocab: int = VOCAB) -> np.ndarray:
    key = f"{card_id}/{prompt_id}/{token_index}".encode()
    seed = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    logits: np.ndarray = rng.normal(scale=1.8, size=vocab)
    return logits


def synthesize(
    arm_id: str,
    *,
    card_id: str = "creative_diversity",
    prompt_ids: Sequence[str] = ("p1", "p2", "p3", "p4", "p5"),
    epochs: int = 4,
    tokens: int = 24,
    seed: int = 0,
    epsilon: float = 0.0,
    session_id: str | None = None,
    window: Window = "unsupervised",
    window_by_epoch: Sequence[Window] | None = None,
    start_ns: int = 1_757_800_000_000_000_000,
    entropy_source_used: str = "qrng",
) -> list[Tier0Record]:
    """Records for one arm; ``window_by_epoch`` tags each epoch's samples with its own window."""
    rng = np.random.default_rng(seed)
    out: list[Tier0Record] = []
    clock = start_ns
    for epoch in range(epochs):
        win = window if window_by_epoch is None else window_by_epoch[epoch % len(window_by_epoch)]
        for prompt_id in prompt_ids:
            ref = f"{arm_id}/{card_id}/{prompt_id}/{epoch}"
            for t in range(tokens):
                logits = _logits(card_id, prompt_id, t)
                probs = np.exp(logits - logits.max())
                probs /= probs.sum()
                order = np.argsort(-probs, kind="stable")  # CDF in rank order
                sorted_p = probs[order]
                cdf = np.cumsum(sorted_p)
                u = float(rng.uniform())
                if epsilon:
                    u = u ** (1.0 + epsilon)
                rank = int(np.searchsorted(cdf, u, side="right"))
                rank = min(rank, VOCAB - 1)
                token_id = int(order[rank])
                p = float(sorted_p[rank])
                cdf_lo = float(cdf[rank - 1]) if rank > 0 else 0.0
                cdf_hi = float(cdf[rank])
                top = sorted_p[:TOP_K] / sorted_p[:TOP_K].sum()
                out.append(
                    Tier0Record(
                        sample_ref=ref,
                        token_index=t,
                        token_id=token_id,
                        logprob=math.log(p),
                        rank_raw=rank,
                        p_post=p,
                        rank_post=rank,
                        n_candidates=VOCAB,
                        entropy_post=float(-(probs * np.log(probs)).sum()),
                        entropy_raw_top20=float(-(top * np.log(top)).sum()),
                        temperature=1.0,
                        min_p=0.0,
                        u=u,
                        cdf_lo=cdf_lo,
                        cdf_hi=min(cdf_hi, 1.0),
                        top_entries=[(int(order[i]), float(sorted_p[i])) for i in range(TOP_K)],
                        timestamp_ns=clock,
                        session_id=session_id,
                        window=win,
                        entropy_source_used=entropy_source_used,
                        is_fallback=False,
                    )
                )
                clock += NS_PER_TOKEN
    return out
