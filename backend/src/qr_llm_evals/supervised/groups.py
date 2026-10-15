"""Join codes and heartbeat-backed pseudonymous group presence."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass

from qr_llm_evals.supervised.schema import ObserverPresence

JOIN_CODE_LENGTH = 8
JOIN_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
HEARTBEAT_INTERVAL_S = 15.0
PRESENCE_TTL_S = HEARTBEAT_INTERVAL_S * 2


def new_join_code() -> str:
    return "".join(secrets.choice(JOIN_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


@dataclass(slots=True)
class _Heartbeat:
    name: str
    at: float
    left: bool = False


class PresenceTracker:
    def __init__(self, *, ttl_s: float = PRESENCE_TTL_S) -> None:
        if ttl_s <= 0:
            raise ValueError("presence TTL must be positive")
        self.ttl_s = ttl_s
        self._items: dict[str, _Heartbeat] = {}
        self._lock = threading.Lock()

    def heartbeat(self, observer_id: str, name: str, now: float) -> bool:
        """Record presence and return whether the visible set changed."""
        with self._lock:
            before = self._present_ids(now)
            self._items[observer_id] = _Heartbeat(name=name, at=now)
            return before != self._present_ids(now)

    def mark_left(self, observer_id: str, now: float) -> bool:
        with self._lock:
            before = self._present_ids(now)
            item = self._items.get(observer_id)
            if item is not None:
                item.left = True
            return before != self._present_ids(now)

    def present(self, now: float) -> list[ObserverPresence]:
        with self._lock:
            return [
                ObserverPresence(observer_id=observer_id, name=item.name)
                for observer_id, item in sorted(self._items.items())
                if not item.left and now - item.at <= self.ttl_s
            ]

    def _present_ids(self, now: float) -> tuple[str, ...]:
        return tuple(
            observer_id
            for observer_id, item in sorted(self._items.items())
            if not item.left and now - item.at <= self.ttl_s
        )


__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "JOIN_ALPHABET",
    "JOIN_CODE_LENGTH",
    "PRESENCE_TTL_S",
    "PresenceTracker",
    "new_join_code",
]
