"""Cooperative cancellation for streaming generations (spec §2.3).

A :class:`CancelToken` is checked by the client *between* SSE chunks. When it
trips, the client raises :class:`GenerationCancelled` from inside the
``httpx`` streaming context, which closes the response — and closing the
response is what makes vLLM abort the sequence server-side (the supervised
"stop" control and run abort both rely on this; no separate cancel endpoint).

The token is thread-safe (``threading.Event``) so a sync generation running in
a worker thread can be cancelled from the async API loop.
"""

from __future__ import annotations

import threading


class GenerationCancelled(Exception):
    """Raised inside the stream when its :class:`CancelToken` was tripped."""

    def __init__(self, reason: str = "cancelled") -> None:
        super().__init__(reason)
        self.reason = reason


class CancelToken:
    """One-shot, thread-safe cancellation flag with an optional reason."""

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        """Trip the token; the next chunk boundary raises :class:`GenerationCancelled`."""
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise GenerationCancelled(self._reason)

    def __repr__(self) -> str:
        state = f"cancelled={self._reason!r}" if self.cancelled else "active"
        return f"CancelToken({state})"


__all__ = ["CancelToken", "GenerationCancelled"]
