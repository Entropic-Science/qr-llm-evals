"""Model catalogue, sampling envelopes, seeds and the frozen study configuration (spec §2.2, §4.1).

Layer 0: imports nothing internal except :mod:`qr_llm_evals.config`.
"""

from __future__ import annotations

from qr_llm_evals.models.catalogue import ModelCatalogue, load_catalogue, parse_model_id
from qr_llm_evals.models.envelope import (
    SampleContext,
    build_envelope,
    build_request_body,
    envelope_hash,
    equivalence_violations,
)
from qr_llm_evals.models.provenance import label_for_source, provenance_fields
from qr_llm_evals.models.providers import ProviderProfile, load_provider_profiles, profile_for
from qr_llm_evals.models.schema import (
    DEFAULT_SEEDS,
    EPSILON_LEVELS,
    Arm,
    CardSelection,
    DisplaySettings,
    EstimateAssumptions,
    JudgeSpec,
    ModelRef,
    PilotConfig,
    SettingsByDisplay,
    StudyConfig,
    SupervisedConfig,
    canonical_json,
    config_hash,
)
from qr_llm_evals.models.seeds import derive_seed

__all__ = [
    "DEFAULT_SEEDS",
    "EPSILON_LEVELS",
    "Arm",
    "CardSelection",
    "DisplaySettings",
    "EstimateAssumptions",
    "JudgeSpec",
    "ModelRef",
    "PilotConfig",
    "SettingsByDisplay",
    "StudyConfig",
    "SupervisedConfig",
    "ModelCatalogue",
    "ProviderProfile",
    "SampleContext",
    "build_envelope",
    "build_request_body",
    "canonical_json",
    "config_hash",
    "derive_seed",
    "envelope_hash",
    "equivalence_violations",
    "label_for_source",
    "load_catalogue",
    "load_provider_profiles",
    "parse_model_id",
    "profile_for",
    "provenance_fields",
]
