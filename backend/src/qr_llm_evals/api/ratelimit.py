"""Per-IP token buckets for the open ``POST`` routes (spec §4.7 "Rate limits").

``/estimates``, ``/prereg/render`` and ``/sessions/{id}/join`` need no key, so
an anonymous client could otherwise spin the estimator or the prereg renderer
for free. Each caller IP gets a bucket of :data:`DEFAULT_BURST` tokens refilled
at :data:`DEFAULT_RATE_PER_S`; an empty bucket answers ``429`` with a
``Retry-After`` computed from the refill rate (never a silent queue, spec §2.4).

The launch route (``POST /runs``) is *not* rate-limited here; it is gated by
the key and the active-run quota.

Client IP: ``CF-Connecting-IP`` when present, else the **last** hop of
``X-Forwarded-For``, else the socket peer. On the box every request arrives
through cloudflared on loopback, so a header is what distinguishes clients.
Cloudflare sets ``CF-Connecting-IP`` itself and appends the real address to
``X-Forwarded-For``; the first hop of that header is whatever the client sent
and must not be trusted, or one client could mint a fresh bucket per request.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from qr_llm_evals.api.errors import ApiError

DEFAULT_RATE_PER_S = 0.5  # 30 requests / minute sustained
DEFAULT_BURST = 10
#: Idle buckets are forgotten after this long so the table cannot grow forever.
IDLE_TTL_S = 600.0


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class RateLimited(Exception):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"rate limited; retry in {retry_after_s:.1f}s")
        self.retry_after_s = retry_after_s


class TokenBucketLimiter:
    """Thread-safe ``key -> bucket`` token-bucket limiter."""

    def __init__(
        self,
        rate_per_s: float = DEFAULT_RATE_PER_S,
        burst: int = DEFAULT_BURST,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_s <= 0 or burst < 1:
            raise ValueError("rate_per_s must be > 0 and burst >= 1")
        self.rate = rate_per_s
        self.burst = burst
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._last_sweep = clock()

    def take(self, key: str, cost: float = 1.0) -> None:
        """Consume ``cost`` tokens for ``key`` or raise :class:`RateLimited`."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.burst), updated=now)
                self._buckets[key] = bucket
            else:
                refill = (now - bucket.updated) * self.rate
                bucket.tokens = min(float(self.burst), bucket.tokens + refill)
                bucket.updated = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return
            deficit = cost - bucket.tokens
        raise RateLimited(deficit / self.rate)

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < IDLE_TTL_S:
            return
        self._last_sweep = now
        stale = [k for k, b in self._buckets.items() if now - b.updated > IDLE_TTL_S]
        for k in stale:
            del self._buckets[k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)


def client_ip(request: Request) -> str:
    connecting = request.headers.get("cf-connecting-ip", "").strip()
    if connecting:
        return connecting
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def enforce_subject(limiter: TokenBucketLimiter, scope: str, subject: str) -> None:
    """Like :func:`enforce`, keyed by something other than the caller's address."""
    try:
        limiter.take(f"{scope}:{subject}")
    except RateLimited as err:
        retry = max(1, int(err.retry_after_s + 0.999))
        raise ApiError(
            429,
            f"too many attempts; retry in {retry}s",
            headers={"Retry-After": str(retry)},
        ) from err


def enforce(limiter: TokenBucketLimiter, request: Request, *, scope: str = "ip") -> None:
    """Take one token for the caller or raise the ``429`` :class:`ApiError`."""
    try:
        limiter.take(f"{scope}:{client_ip(request)}")
    except RateLimited as err:
        retry = max(1, int(err.retry_after_s + 0.999))
        raise ApiError(
            429,
            f"too many requests from this address; retry in {retry}s",
            headers={"Retry-After": str(retry)},
        ) from err


__all__ = [
    "DEFAULT_BURST",
    "DEFAULT_RATE_PER_S",
    "IDLE_TTL_S",
    "RateLimited",
    "TokenBucketLimiter",
    "client_ip",
    "enforce",
    "enforce_subject",
]
