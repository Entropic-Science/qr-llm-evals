"""Stream-level Tier-0 tests keep one told cell: seeded arms share a stream across cells."""

from __future__ import annotations

from qr_llm_evals.tier0 import endpoints
from qr_llm_evals.tier0.records import INDEPENDENT_TOLD_CELL, independent_streams
from qr_llm_evals.tier0.schema import Tier0Record


def _record(sample_ref: str, index: int = 0) -> Tier0Record:
    return Tier0Record(
        sample_ref=sample_ref,
        token_index=index,
        token_id=3,
        logprob=-0.5,
        rank_raw=0,
        entropy_raw_top20=0.4,
        u=0.25,
        cdf_lo=0.0,
        cdf_hi=0.6,
        timestamp_ns=1,
    )


def test_keeps_untold_records_and_the_told_nothing_cell_in_order() -> None:
    base = "prng_seed_7/self_referential_probe/focus_d1_consciousness/0"
    records = [
        _record(f"{base}/told_qrng"),
        _record("prng_seed_7/source_guessing_self_report/item_0003/1"),
        _record(f"{base}/told_nothing", 0),
        _record(f"{base}/told_prng"),
        _record(f"{base}/told_nothing", 1),
    ]
    kept = independent_streams(records)
    assert [r.sample_ref for r in kept] == [
        "prng_seed_7/source_guessing_self_report/item_0003/1",
        f"{base}/{INDEPENDENT_TOLD_CELL}",
        f"{base}/{INDEPENDENT_TOLD_CELL}",
    ]
    assert [r.token_index for r in kept] == [0, 0, 1]


def test_t0a_table_counts_one_told_cell_per_prompt_and_epoch() -> None:
    records = [
        _record(f"qrng/self_referential_probe/p{p}/{e}/{told}", i)
        for p in range(3)
        for e in range(2)
        for told in ("told_qrng", "told_prng", "told_nothing")
        for i in range(4)
    ]
    table = endpoints.t0a_table({"qrng": records}, None, seed=42, tier0_source="engine_log")
    arm = table["arms"]["qrng"]
    assert arm["n_tokens"] == arm["n_bracketed"] == 3 * 2 * 4  # told_nothing only
