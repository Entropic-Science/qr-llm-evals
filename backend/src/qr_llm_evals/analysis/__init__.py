"""Analysis phase: statistics, endpoints, figures, tables, narrative (spec §2.10).

Layer 2: may import ``store`` / ``cards`` / ``tier0`` only. ``analysis.stats``
and ``analysis.mmd`` are pure numpy/scipy leaves (no internal imports).
"""

from qr_llm_evals.analysis.run import run

__all__ = ["run"]
