"""Ledger dataclasses → the wire models of ``/me/usage`` and ``/admin/users``."""

from __future__ import annotations

from qr_llm_evals.accounts import Prices, Quota, UsageBucket, UsageLedger, UsageTotals, User
from qr_llm_evals.api.schema import (
    BillingView,
    PricesView,
    QuotaView,
    SettlementView,
    UsageBucketView,
    UsageResponse,
    UsageTotalsView,
)


def totals_view(totals: UsageTotals, prices: Prices) -> UsageTotalsView:
    return UsageTotalsView(
        calls=totals.calls,
        qrng_input=totals.qrng_input,
        qrng_output=totals.qrng_output,
        prng_input=totals.prng_input,
        prng_output=totals.prng_output,
        own_key_tokens=totals.own_key_tokens,
        cost_microusd=totals.cost_microusd(prices),
    )


def prices_view(prices: Prices) -> PricesView:
    return PricesView(
        qrng_input=prices.qrng_input,
        qrng_output=prices.qrng_output,
        prng_input=prices.prng_input,
        prng_output=prices.prng_output,
    )


def quota_view(quota: Quota) -> QuotaView:
    return QuotaView(
        limit=quota.limit, used=quota.used, remaining=quota.remaining, exhausted=quota.exhausted
    )


def usage_response(ledger: UsageLedger, user: User) -> UsageResponse:
    summary = ledger.summary(user.user_id)
    prices = ledger.prices

    def buckets(items: list[UsageBucket]) -> list[UsageBucketView]:
        return [UsageBucketView(key=b.key, totals=totals_view(b.totals, prices)) for b in items]

    paid = ledger.settled(user.user_id)
    return UsageResponse(
        quota=quota_view(Quota(limit=ledger.limit_for(user), used=summary.totals.charged_tokens)),
        billing=BillingView(
            outstanding_microusd=summary.totals.cost_microusd(prices) - paid,
            paid_microusd=paid,
            prices=prices_view(prices),
            settlements=[
                SettlementView(
                    amount_microusd=s.amount_microusd, settled_at=s.settled_at, note=s.note
                )
                for s in ledger.settlements(user.user_id)
            ],
        ),
        totals=totals_view(summary.totals, prices),
        by_kind=buckets(summary.by_kind),
        by_source=buckets(summary.by_source),
        by_day=buckets(summary.by_day),
        by_run=buckets(summary.by_run),
    )


__all__ = ["prices_view", "quota_view", "totals_view", "usage_response"]
