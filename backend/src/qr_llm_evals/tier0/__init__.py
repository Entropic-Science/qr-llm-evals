"""Tier 0: token-level records from the engine log or API logprobs (spec §2.6, D-8).

* :mod:`~qr_llm_evals.tier0.schema` — ``Tier0Record``.
* :mod:`~qr_llm_evals.tier0.acquire` — ``build_records`` from DEP-1 engine logs
  and/or vLLM logprobs.
* :mod:`~qr_llm_evals.tier0.records` — join-key parsing, grouping, JSONL I/O.
* :mod:`~qr_llm_evals.tier0.availability` — which endpoints a ``tier0_source`` supports.
* :mod:`~qr_llm_evals.tier0.endpoints` — T0-a/b/c and T4-l measurement tables.

Layer 1: may import ``cards`` / ``models`` (layer 0); never ``analysis``.
"""

from __future__ import annotations

from qr_llm_evals.tier0.acquire import AcquisitionError, build_records
from qr_llm_evals.tier0.availability import endpoints_for, is_available
from qr_llm_evals.tier0.schema import Tier0Record

__all__ = ["AcquisitionError", "Tier0Record", "build_records", "endpoints_for", "is_available"]
