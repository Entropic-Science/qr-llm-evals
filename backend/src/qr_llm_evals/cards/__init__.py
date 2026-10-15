"""Eval-card registry: schema, loader, admission validator and estimates (spec §4.2–4.3).

Layer 0: imports nothing internal except :mod:`qr_llm_evals.config`.
"""

from __future__ import annotations

from qr_llm_evals.cards.estimates import (
    TOLD_CELLS,
    CardEstimate,
    CardOverrides,
    EstimateAssumptions,
    SelectionEstimate,
    estimate_selection,
    tokens_per_arm,
)
from qr_llm_evals.cards.loader import (
    DEFAULT_REGISTRY_DIR,
    REPO_ROOT,
    CardLoadError,
    load_card,
    load_cards,
)
from qr_llm_evals.cards.schema import (
    Dataset,
    Endpoints,
    EvalCard,
    Judge,
    Subsample,
)
from qr_llm_evals.cards.validate import (
    CardAdmissionError,
    admission_problems,
    check_card_file,
    validate_card,
    validate_registry,
)

__all__ = [
    "DEFAULT_REGISTRY_DIR",
    "REPO_ROOT",
    "TOLD_CELLS",
    "CardAdmissionError",
    "CardEstimate",
    "CardLoadError",
    "CardOverrides",
    "Dataset",
    "Endpoints",
    "EstimateAssumptions",
    "EvalCard",
    "Judge",
    "SelectionEstimate",
    "Subsample",
    "admission_problems",
    "check_card_file",
    "estimate_selection",
    "load_card",
    "load_cards",
    "tokens_per_arm",
    "validate_card",
    "validate_registry",
]
