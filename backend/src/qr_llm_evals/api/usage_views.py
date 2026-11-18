"""Ledger dataclasses → the wire models of ``/me/usage`` and ``/admin/users``."""

from __future__ import annotations

from qr_llm_evals.accounts import Quota, UsageBucket, UsageLedger, UsageTotals, User
from qr_llm_evals.api.schema import (
    QuotaView,
    UsageBucketView,
    UsageResponse,
    UsageTotalsView,
)


def totals_view(totals: UsageTotals) -> UsageTotalsView:
    return UsageTotalsView(
        calls=totals.calls,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        reasoning_tokens=totals.reasoning_tokens,
        charged_tokens=totals.charged_tokens,
    )


def quota_view(quota: Quota) -> QuotaView:
    return QuotaView(
        limit=quota.limit, used=quota.used, remaining=quota.remaining, exhausted=quota.exhausted
    )


def _buckets(buckets: list[UsageBucket]) -> list[UsageBucketView]:
    return [UsageBucketView(key=b.key, totals=totals_view(b.totals)) for b in buckets]


def usage_response(ledger: UsageLedger, user: User) -> UsageResponse:
    summary = ledger.summary(user.user_id)
    return UsageResponse(
        quota=quota_view(Quota(limit=ledger.limit_for(user), used=summary.totals.charged_tokens)),
        totals=totals_view(summary.totals),
        by_kind=_buckets(summary.by_kind),
        by_source=_buckets(summary.by_source),
        by_day=_buckets(summary.by_day),
        by_run=_buckets(summary.by_run),
    )


__all__ = ["quota_view", "totals_view", "usage_response"]
