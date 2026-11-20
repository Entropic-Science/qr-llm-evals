"""PIT residuals draw V per token, so separate calls never share a V sequence."""

from __future__ import annotations

import numpy as np

from qr_llm_evals.tier0 import endpoints
from qr_llm_evals.tier0.schema import Tier0Record


def _records(arm: str, n_samples: int, length: int, lo: float = 0.0, hi: float = 1.0) -> list:
    """Certain tokens (bracket [lo, hi]) so the residual is lo + V·(hi − lo): V shows through."""
    return [
        Tier0Record(
            sample_ref=f"{arm}/self_referential_probe/p{j}/0",
            token_index=i,
            token_id=1,
            logprob=0.0,
            rank_raw=0,
            entropy_raw_top20=0.0,
            u=0.5,
            cdf_lo=lo,
            cdf_hi=hi,
            timestamp_ns=1,
        )
        for j in range(n_samples)
        for i in range(length)
    ]


def test_v_is_keyed_to_the_token_not_the_position() -> None:
    records = _records("qrng", 3, 8)
    pooled = endpoints.pit_residuals(records, seed=42)
    first, second = records[:8], records[8:16]
    alone = endpoints.pit_residuals(first, seed=42)
    np.testing.assert_array_equal(alone, pooled[:8])  # same token, same V, however sliced
    np.testing.assert_array_equal(endpoints.pit_residuals(list(reversed(first)), 42), alone[::-1])
    # Before the fix every call restarted one seeded stream, so two samples got identical V.
    assert not np.array_equal(alone, endpoints.pit_residuals(second, seed=42))
    assert not np.array_equal(alone, endpoints.pit_residuals(first, seed=7))


def test_two_arms_do_not_share_v_index_by_index() -> None:
    a = endpoints.pit_residuals(_records("qrng", 4, 25), seed=42)
    b = endpoints.pit_residuals(_records("prng_seed_7", 4, 25), seed=42)
    assert a.size == b.size == 100
    assert np.mean(a == b) == 0.0
    assert abs(float(np.corrcoef(a, b)[0, 1])) < 0.3


def test_lag1_is_calibrated_across_many_samples() -> None:
    # 300 samples of 20 certain tokens: residuals are pure V. A V stream shared across
    # samples repeated one sequence 300 times, so its chance lag-1 r looked significant.
    result = endpoints.lag1_autocorr(_records("qrng", 300, 20), seed=42)
    assert result["n_pairs"] == 300 * 19
    assert abs(result["r"]) < 4 / np.sqrt(result["n_pairs"])
    assert result["p_value"] > 0.001


def test_token_uniforms_are_uniform_and_deterministic() -> None:
    records = _records("qrng", 50, 40)
    v = endpoints.token_uniforms(records, seed=42)
    assert v.min() > 0.0 and v.max() < 1.0
    assert abs(float(v.mean()) - 0.5) < 0.03 and abs(float(v.var()) - 1 / 12) < 0.01
    np.testing.assert_array_equal(v, endpoints.token_uniforms(records, seed=42))
    counts = np.histogram(v, bins=10, range=(0.0, 1.0))[0]
    assert counts.min() > 150  # 2,000 draws, about 200 per decile
