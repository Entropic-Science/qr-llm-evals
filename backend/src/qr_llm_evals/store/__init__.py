"""Storage: SQLite index + JSONL artifacts, bundle layout, ``quarantine()`` (spec D-4, §4.5).

Entry points:

* :class:`Store` / :class:`RunStore` - the façade (``store.store``).
* ``store.schema`` - the run-time records (``Run``, ``GenerationRow``, ...).
* ``store.paths`` - the bundle layout as typed path helpers.
* ``store.artifacts`` - append-only JSONL writers and ``completed_refs()``.
* ``store.bundle`` - ``write_frozen_study()`` and the ``tar.zst`` exporter.
* ``store.quarantine`` - ``quarantine()`` / ``withdraw()``; nothing is ever deleted.

Layer 1: may import ``cards`` / ``models`` (layer 0).
"""

from __future__ import annotations

from qr_llm_evals.store.artifacts import (
    JsonlWriter,
    append_jsonl,
    completed_judge_refs,
    completed_refs,
    iter_jsonl,
    read_jsonl,
)
from qr_llm_evals.store.bundle import (
    derive_study_id,
    extract_bundle,
    iter_bundle,
    prereg_sha256,
    read_bundle,
    strip_provider_metadata,
    write_bundle,
    write_frozen_study,
)
from qr_llm_evals.store.db import SCHEMA_VERSION, Index
from qr_llm_evals.store.paths import ArmCardDir, DataRoot, RunDir, StudyDir, safe_segment
from qr_llm_evals.store.quarantine import quarantine, withdraw
from qr_llm_evals.store.schema import (
    GenerationRow,
    JudgeRow,
    Progress,
    Provenance,
    ReadinessReport,
    Run,
    RunState,
    Taint,
)
from qr_llm_evals.store.store import RunStore, Store

__all__ = [
    "SCHEMA_VERSION",
    "ArmCardDir",
    "DataRoot",
    "GenerationRow",
    "Index",
    "JsonlWriter",
    "JudgeRow",
    "Progress",
    "Provenance",
    "ReadinessReport",
    "Run",
    "RunDir",
    "RunState",
    "RunStore",
    "Store",
    "StudyDir",
    "Taint",
    "append_jsonl",
    "completed_judge_refs",
    "completed_refs",
    "derive_study_id",
    "extract_bundle",
    "iter_bundle",
    "iter_jsonl",
    "prereg_sha256",
    "quarantine",
    "read_bundle",
    "read_jsonl",
    "safe_segment",
    "strip_provider_metadata",
    "withdraw",
    "write_bundle",
    "write_frozen_study",
]
