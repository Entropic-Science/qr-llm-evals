"""Glue between persisted rows and the per-account usage ledger and quota.

The store calls :meth:`UsageRecorder.on_row` after every generation or judge
row lands on disk (see ``Store.usage_hook``). The recorder looks up the run's
owner, writes a ledger row, and pauses the run when the owner may no longer
generate: the token quota is spent, the account was disabled, or the row could
not be recorded at all. The pause takes effect at the next item boundary, like
a host pause; nothing in flight is cancelled and nothing is deleted.

Failing closed on a ledger error matters: the row is already on disk and is
never lost, but a run that kept going while accounting was down would generate
unmetered for as long as the fault lasted.

An operator's bare-key run has no owner and is not recorded.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from qr_llm_evals.accounts import Accounts, Quota, UnknownUser, UsageEvent, UsageLedger
from qr_llm_evals.api.registry import RunRegistry
from qr_llm_evals.runner.control import RunControlError
from qr_llm_evals.runner.events import RunEvent
from qr_llm_evals.store.store import Store, UsageNote

log = logging.getLogger("qr_llm_evals.api.usage")

QUOTA_EVENT = "quota.exhausted"


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class UsageRecorder:
    def __init__(
        self,
        store: Store,
        accounts: Accounts,
        ledger: UsageLedger,
        registry: RunRegistry,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._accounts = accounts
        self.ledger = ledger
        self._registry = registry
        self._clock = clock
        self._owners: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def owner_of(self, run_id: str) -> str | None:
        """The run's ``owner_id`` (cached: it never changes after launch)."""
        with self._lock:
            if run_id in self._owners:
                return self._owners[run_id]
        run_store = self._store.find_run(run_id)
        owner = run_store.read_run().owner_id if run_store is not None else None
        with self._lock:
            self._owners[run_id] = owner
        return owner

    def quota_for(self, user_id: str) -> Quota | None:
        try:
            return self.ledger.quota(self._accounts.get_user(user_id))
        except UnknownUser:
            return None

    def on_row(self, note: UsageNote) -> None:
        owner = self.owner_of(note.run_id)
        if owner is None:
            return
        # Charged: anything generated on the shared engine. Generation rows say
        # so through their provenance; judge rows through the judge's target.
        charged = note.pipeline_pinned
        try:
            self.ledger.record(
                UsageEvent(
                    user_id=owner,
                    run_id=note.run_id,
                    kind=note.kind,
                    arm_id=note.arm_id,
                    card_id=note.card_id,
                    source_label=note.source_label,
                    model=note.model,
                    prompt_tokens=note.usage.prompt_tokens,
                    completion_tokens=note.usage.completion_tokens,
                    reasoning_tokens=note.usage.reasoning_tokens or 0,
                    charged=charged,
                    at=note.at,
                )
            )
        except Exception:
            log.exception(
                "usage not recorded; pausing the run",
                extra={"event": "usage.record_failed", "run_id": note.run_id, "user_id": owner},
            )
            self._pause(note.run_id)
            return
        self._pause_if_blocked(note.run_id, owner)

    def record_narrative(
        self,
        run_id: str,
        model: str,
        raw_usage: Mapping[str, Any] | None,
        *,
        on_engine: bool = False,
    ) -> None:
        """Record a narrative call; never raises (accounting must not fail an analysis).

        ``on_engine`` marks a narrative written by the shared engine, which is
        charged like any other generation there.
        """
        try:
            owner = self.owner_of(run_id)
            if owner is None:
                return
            raw = raw_usage or {}
            self.ledger.record(
                UsageEvent(
                    user_id=owner,
                    run_id=run_id,
                    kind="narrative",
                    model=model,
                    prompt_tokens=_count(raw.get("prompt_tokens")),
                    completion_tokens=_count(raw.get("completion_tokens")),
                    charged=on_engine,
                    at=self._clock(),
                )
            )
        except Exception:
            log.exception(
                "narrative usage not recorded",
                extra={"event": "usage.narrative_failed", "run_id": run_id},
            )

    def _pause(self, run_id: str) -> bool:
        """Ask the run to pause at its next item boundary; True if this call did it.

        Goes to the control directly: ``LiveRun.pause`` consults the state in
        ``run.json``, which lags a host resume and would refuse here.
        """
        live = self._registry.get(run_id)
        if live is None or live.control.paused:
            return False
        try:
            live.control.pause()
        except RunControlError:
            return False
        return True

    def _pause_if_blocked(self, run_id: str, owner: str) -> None:
        try:
            user = self._accounts.get_user(owner)
        except UnknownUser:
            return
        if user.disabled:
            if self._pause(run_id):
                log.warning(
                    "run paused: owner account is disabled",
                    extra={"event": "usage.owner_disabled", "run_id": run_id, "user_id": owner},
                )
            return
        if self.ledger.limit_for(user) == 0:
            return  # unlimited: no need to total the ledger on every row
        quota = self.ledger.quota(user)
        if not quota.exhausted or not self._pause(run_id):
            return
        live = self._registry.get(run_id)
        if live is not None:
            live.bus.emit(
                RunEvent(QUOTA_EVENT, {"run_id": run_id, "limit": quota.limit, "used": quota.used})
            )
        log.warning(
            "run paused: token quota exhausted",
            extra={
                "event": "usage.quota_exhausted",
                "run_id": run_id,
                "user_id": owner,
                "limit": quota.limit,
                "used": quota.used,
            },
        )


__all__ = ["QUOTA_EVENT", "UsageRecorder"]
