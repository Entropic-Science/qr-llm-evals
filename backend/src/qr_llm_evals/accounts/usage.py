"""Per-account token usage ledger and the token quota it feeds.

One row per model call made on behalf of an account: a generation, a judge
call or a narrative call. Rows are append-only and live in ``accounts.sqlite``
next to the users they belong to.

Every call falls in one of three lanes:

``qrng``
    Ran on the shared qr-server engine for a quantum arm (source label
    ``quantum``).
``prng``
    Any other call on the shared engine: the pseudo-random arms, and judge or
    narrative calls served by the engine.
``own_key``
    Ran on the user's own provider key (the Anthropic judge, external
    providers). Recorded for the dashboard, never billed here.

The first two lanes are **charged**: their input + output tokens count against
the account's quota and are priced by :class:`Prices`. The quota is a lifetime
budget, not a rolling window; ``0`` means unlimited.

**Outstanding** is the priced lifetime usage minus the settlements an admin
has recorded. Settling inserts a row; usage rows are never changed or removed.
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


MICRO = 1_000_000
QUANTUM_LABEL = "quantum"


@dataclass(frozen=True, slots=True)
class Prices:
    """US dollars per million tokens, per lane and direction."""

    qrng_input: float = 20.0
    qrng_output: float = 80.0
    prng_input: float = 5.0
    prng_output: float = 20.0


@dataclass(frozen=True, slots=True)
class UsageTotals:
    calls: int = 0
    qrng_input: int = 0
    qrng_output: int = 0
    prng_input: int = 0
    prng_output: int = 0
    own_key_input: int = 0
    own_key_output: int = 0

    @property
    def charged_tokens(self) -> int:
        """Tokens that ran on the shared engine; what the quota counts."""
        return self.qrng_input + self.qrng_output + self.prng_input + self.prng_output

    @property
    def own_key_tokens(self) -> int:
        return self.own_key_input + self.own_key_output

    def cost_microusd(self, prices: Prices) -> int:
        """Price of the charged tokens in millionths of a dollar.

        A price is dollars per million tokens, which is also micro-dollars per
        token, so the sum needs no scaling.
        """
        return round(
            self.qrng_input * prices.qrng_input
            + self.qrng_output * prices.qrng_output
            + self.prng_input * prices.prng_input
            + self.prng_output * prices.prng_output
        )


@dataclass(frozen=True, slots=True)
class Settlement:
    """An admin's record that an account paid ``amount_microusd``."""

    user_id: str
    amount_microusd: int
    settled_at: datetime
    settled_by: str
    note: str | None = None


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


_QRNG = f"charged = 1 AND source_label = '{QUANTUM_LABEL}'"
_PRNG = f"charged = 1 AND (source_label IS NULL OR source_label != '{QUANTUM_LABEL}')"


def _sum(condition: str, column: str, name: str) -> str:
    return f"COALESCE(SUM(CASE WHEN {condition} THEN {column} ELSE 0 END), 0) AS {name}"


_SUMS = ", ".join(
    (
        "COUNT(*) AS calls",
        _sum(_QRNG, "prompt_tokens", "qrng_input"),
        _sum(_QRNG, "completion_tokens", "qrng_output"),
        _sum(_PRNG, "prompt_tokens", "prng_input"),
        _sum(_PRNG, "completion_tokens", "prng_output"),
        _sum("charged = 0", "prompt_tokens", "own_key_input"),
        _sum("charged = 0", "completion_tokens", "own_key_output"),
    )
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
_SETTLED_SQL = "SELECT COALESCE(SUM(amount_microusd), 0) AS paid FROM settlements WHERE user_id = ?"


def _totals(row: sqlite3.Row | None) -> UsageTotals:
    if row is None:
        return UsageTotals()
    return UsageTotals(
        calls=int(row["calls"]),
        qrng_input=int(row["qrng_input"]),
        qrng_output=int(row["qrng_output"]),
        prng_input=int(row["prng_input"]),
        prng_output=int(row["prng_output"]),
        own_key_input=int(row["own_key_input"]),
        own_key_output=int(row["own_key_output"]),
    )


class UsageLedger:
    """Reads and writes ``usage_events`` through the :class:`Accounts` connection."""

    def __init__(
        self,
        accounts: Accounts,
        *,
        default_quota: int = UNLIMITED,
        prices: Prices | None = None,
    ) -> None:
        self._accounts = accounts
        self.default_quota = max(0, default_quota)
        self.prices = prices or Prices()

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

    # -- money -------------------------------------------------------------

    def settled_by_user(self) -> dict[str, int]:
        rows = self._rows(
            "SELECT user_id, COALESCE(SUM(amount_microusd), 0) AS paid "
            "FROM settlements GROUP BY user_id",
            (),
        )
        return {str(r["user_id"]): int(r["paid"]) for r in rows}

    def settled(self, user_id: str) -> int:
        rows = self._rows(_SETTLED_SQL, (user_id,))
        return int(rows[0]["paid"]) if rows else 0

    def outstanding(self, user_id: str) -> int:
        """Priced lifetime usage minus recorded settlements, in micro-dollars."""
        return self.totals(user_id).cost_microusd(self.prices) - self.settled(user_id)

    def settle(
        self, user_id: str, *, settled_by: str, at: datetime, note: str | None = None
    ) -> Settlement:
        """Record that the account paid what it owes right now.

        The amount is read and written in one transaction, so tokens used
        while an admin clicks stay outstanding instead of being forgiven.
        """
        self._accounts.get_user(user_id)
        with self._accounts._tx() as conn:
            row = conn.execute(_TOTALS_SQL, (user_id,)).fetchone()
            paid = conn.execute(_SETTLED_SQL, (user_id,)).fetchone()
            amount = _totals(row).cost_microusd(self.prices) - int(paid["paid"])
            conn.execute(
                "INSERT INTO settlements (user_id, amount_microusd, settled_at, settled_by, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, amount, at.astimezone(UTC).isoformat(), settled_by, note),
            )
        return Settlement(
            user_id=user_id, amount_microusd=amount, settled_at=at, settled_by=settled_by, note=note
        )

    def settlements(self, user_id: str, *, limit: int = 20) -> list[Settlement]:
        rows = self._rows(
            "SELECT user_id, amount_microusd, settled_at, settled_by, note FROM settlements "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return [
            Settlement(
                user_id=str(r["user_id"]),
                amount_microusd=int(r["amount_microusd"]),
                settled_at=datetime.fromisoformat(r["settled_at"]),
                settled_by=str(r["settled_by"]),
                note=r["note"],
            )
            for r in rows
        ]


__all__ = [
    "MICRO",
    "UNLIMITED",
    "Prices",
    "Quota",
    "Settlement",
    "UsageBucket",
    "UsageEvent",
    "UsageKind",
    "UsageLedger",
    "UsageSummary",
    "UsageTotals",
]
