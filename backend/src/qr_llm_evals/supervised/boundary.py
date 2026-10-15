"""Generation-boundary scheduling for supervised windows (spec §2.9)."""

from __future__ import annotations

from dataclasses import dataclass, field

EMA_ALPHA = 0.2


def should_dispatch(
    now: float, session_start: float, target_s: float, expected_gen_s: float
) -> bool:
    """Dispatch iff stopping after one more generation lands closer to the target."""
    remaining = session_start + target_s - now
    if remaining <= 0:
        return False
    return expected_gen_s < 2 * remaining


@dataclass(slots=True)
class ExponentialMovingAverage:
    """An α=0.2 EMA, seeded before it is used for a scheduling decision."""

    value: float
    alpha: float = EMA_ALPHA

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError("EMA seed must be positive")
        if not 0 < self.alpha <= 1:
            raise ValueError("EMA alpha must be in (0, 1]")

    def update(self, observation: float) -> float:
        if observation <= 0:
            raise ValueError("EMA observation must be positive")
        self.value = self.alpha * observation + (1 - self.alpha) * self.value
        return self.value


@dataclass(slots=True)
class BoundaryEstimator:
    """Per-card token EMAs plus a run throughput EMA.

    Tiled consoles call :meth:`slot_should_dispatch` independently whenever a
    slot becomes idle.  Thus one long stream never grants another slot an
    extra generation after the boundary decision has flipped.
    """

    readiness_tps: float
    card_token_estimates: dict[str, float]
    alpha: float = EMA_ALPHA
    _tps: ExponentialMovingAverage = field(init=False)
    _tokens: dict[str, ExponentialMovingAverage] = field(init=False)

    def __post_init__(self) -> None:
        self._tps = ExponentialMovingAverage(self.readiness_tps, self.alpha)
        self._tokens = {
            card_id: ExponentialMovingAverage(tokens, self.alpha)
            for card_id, tokens in self.card_token_estimates.items()
        }

    def expected_gen_s(self, card_id: str) -> float:
        try:
            return self._tokens[card_id].value / self._tps.value
        except KeyError as err:
            raise ValueError(f"card {card_id!r} has no seeded token estimate") from err

    def observe(self, card_id: str, *, tokens: int, elapsed_s: float) -> None:
        if tokens <= 0 or elapsed_s <= 0:
            return
        try:
            self._tokens[card_id].update(float(tokens))
        except KeyError as err:
            raise ValueError(f"card {card_id!r} has no seeded token estimate") from err
        self._tps.update(tokens / elapsed_s)

    def slot_should_dispatch(
        self, card_id: str, *, now: float, session_start: float, target_s: float
    ) -> bool:
        return should_dispatch(now, session_start, target_s, self.expected_gen_s(card_id))


__all__ = ["EMA_ALPHA", "BoundaryEstimator", "ExponentialMovingAverage", "should_dispatch"]
