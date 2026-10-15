"""Advisory countdowns; expiry never starts or resumes generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TimerKind = Literal["intention", "session", "break", "refocus"]


@dataclass(frozen=True, slots=True)
class AdvisoryTimer:
    kind: TimerKind
    started_at: float
    duration_s: float

    def remaining(self, now: float) -> float:
        """Return a signed countdown; negative values are intentional."""
        return self.duration_s - (now - self.started_at)


__all__ = ["AdvisoryTimer", "TimerKind"]
