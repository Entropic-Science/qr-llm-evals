"""Per-account token usage ledger and the token quota it feeds.

One row per model call made on behalf of an account: a generation, a judge
call or a narrative call. Rows are append-only and live in ``accounts.sqlite``
next to the users they belong to.

**Charged** tokens are the ones that count against the account's quota:
prompt + completion tokens of generations on the shared qr-server engine.
Judge, narrative and external-provider calls are recorded for the dashboard
but not charged, because the user's own provider key pays for those.

The quota is a lifetime budget, not a rolling window. ``0`` means unlimited.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from qr_llm_evals.accounts.service import Accounts, User

UsageKind = Literal["generation", "judge", "narrative"]
UNLIMITED = 0


@dataclass(frozen=True, slots=True)
class UsageEvent:
    user_id: str
    run_id: str
    kind: UsageKind
    model: str
    prompt_tokens: int
    completion_tokens: int
    at: datetime
    reasoning_tokens: int = 0
    arm_id: str | None = None
    card_id: str | None = None
    source_label: str | None = None
    charged: bool = False


@dataclass(frozen=True, slots=True)
class UsageTotals:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    charged_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class UsageBucket:
    key: str
    totals: UsageTotals


@dataclass(frozen=True, slots=True)
class Quota:
    limit: int
    used: int

    @property
    def unlimited(self) -> bool:
        return self.limit == UNLIMITED

    @property
    def remaining(self) -> int | None:
        """Tokens left; ``None`` when unlimited. Never negative."""
        return None if self.unlimited else max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.used >= self.limit


@dataclass(frozen=True, slots=True)
class UsageSummary:
    totals: UsageTotals
    by_kind: list[UsageBucket]
    by_source: list[UsageBucket]
    by_day: list[UsageBucket]
    by_run: list[UsageBucket]


_SUMS = (
    "COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
    "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
    "COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens, "
    "COALESCE(SUM(CASE WHEN charged = 1 THEN prompt_tokens + completion_tokens ELSE 0 END), 0) "
    "AS charged_tokens"
)

#: Static statements, one per grouping (no string-built SQL at call time).
_TOTALS_SQL = f"SELECT {_SUMS} FROM usage_events WHERE user_id = ?"  # noqa: S608  # nosec B608
_BY_KIND_SQL = (
    f"SELECT kind AS key, {_SUMS} FROM usage_events WHERE user_id = ? "  # noqa: S608  # nosec B608
    "GROUP BY kind ORDER BY kind"
)
_BY_SOURCE_SQL = (
    f"SELECT COALESCE(source_label, 'unlabelled') AS key, {_SUMS} "  # noqa: S608  # nosec B608
    "FROM usage_events WHERE user_id = ? AND kind = 'generation' GROUP BY key ORDER BY key"
)
_BY_DAY_SQL = (
    f"SELECT substr(at, 1, 10) AS key, {_SUMS} FROM usage_events "  # noqa: S608  # nosec B608
    "WHERE user_id = ? GROUP BY key ORDER BY key DESC LIMIT ?"
)
_BY_RUN_SQL = (
    f"SELECT run_id AS key, {_SUMS} FROM usage_events WHERE user_id = ? "  # noqa: S608  # nosec B608
    "GROUP BY run_id ORDER BY MAX(at) DESC LIMIT ?"
)
_ALL_USERS_SQL = f"SELECT user_id AS key, {_SUMS} FROM usage_events GROUP BY user_id"  # noqa: S608  # nosec B608


def _totals(row: sqlite3.Row | None) -> UsageTotals:
    if row is None:
        return UsageTotals()
    return UsageTotals(
        calls=int(row["calls"]),
        prompt_tokens=int(row["prompt_tokens"]),
        completion_tokens=int(row["completion_tokens"]),
        reasoning_tokens=int(row["reasoning_tokens"]),
        charged_tokens=int(row["charged_tokens"]),
    )


class UsageLedger:
    """Reads and writes ``usage_events`` through the :class:`Accounts` connection."""

    def __init__(self, accounts: Accounts, *, default_quota: int = UNLIMITED) -> None:
        self._accounts = accounts
        self.default_quota = max(0, default_quota)

    def record(self, event: UsageEvent) -> None:
        with self._accounts._tx() as conn:
            conn.execute(
                "INSERT INTO usage_events (user_id, run_id, kind, arm_id, card_id, source_label, "
                "model, prompt_tokens, completion_tokens, reasoning_tokens, charged, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.user_id,
                    event.run_id,
                    event.kind,
                    event.arm_id,
                    event.card_id,
                    event.source_label,
                    event.model,
                    max(0, event.prompt_tokens),
                    max(0, event.completion_tokens),
                    max(0, event.reasoning_tokens),
                    int(event.charged),
                    event.at.astimezone(UTC).isoformat(),
                ),
            )

    def _rows(self, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
        with self._accounts._lock:
            rows: list[sqlite3.Row] = self._accounts._conn.execute(sql, params).fetchall()
            return rows

    def _buckets(self, sql: str, params: tuple[object, ...]) -> list[UsageBucket]:
        return [UsageBucket(key=str(r["key"]), totals=_totals(r)) for r in self._rows(sql, params)]

    def totals(self, user_id: str) -> UsageTotals:
        rows = self._rows(_TOTALS_SQL, (user_id,))
        return _totals(rows[0] if rows else None)

    def totals_by_user(self) -> dict[str, UsageTotals]:
        return {b.key: b.totals for b in self._buckets(_ALL_USERS_SQL, ())}

    def summary(self, user_id: str, *, days: int = 30, runs: int = 20) -> UsageSummary:
        return UsageSummary(
            totals=self.totals(user_id),
            by_kind=self._buckets(_BY_KIND_SQL, (user_id,)),
            by_source=self._buckets(_BY_SOURCE_SQL, (user_id,)),
            by_day=self._buckets(_BY_DAY_SQL, (user_id, days)),
            by_run=self._buckets(_BY_RUN_SQL, (user_id, runs)),
        )

    def limit_for(self, user: User) -> int:
        return self.default_quota if user.token_quota is None else user.token_quota

    def quota(self, user: User) -> Quota:
        return Quota(limit=self.limit_for(user), used=self.totals(user.user_id).charged_tokens)


__all__ = [
    "UNLIMITED",
    "Quota",
    "UsageBucket",
    "UsageEvent",
    "UsageKind",
    "UsageLedger",
    "UsageSummary",
    "UsageTotals",
]
