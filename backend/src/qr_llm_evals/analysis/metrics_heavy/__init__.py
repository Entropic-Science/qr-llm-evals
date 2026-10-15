"""Optional CPU-only text metrics.

This package deliberately imports no heavyweight dependency at module import time.
Callers may import it on a core installation and receive :class:`MetricsUnavailable`
only when a metric requiring the ``metrics`` extra is invoked.
"""

from __future__ import annotations


class MetricsUnavailable(RuntimeError):
    """The optional ``metrics`` dependencies or a frozen checkpoint are unavailable."""


__all__ = ["MetricsUnavailable"]
