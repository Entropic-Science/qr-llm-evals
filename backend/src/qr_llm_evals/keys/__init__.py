"""Launch-gate key verification and the per-run BYO secret bag (spec §2.4, D-14).

Layer 0: imports nothing internal except :mod:`qr_llm_evals.config` and the
root :mod:`qr_llm_evals.logging_utils`.
"""

from __future__ import annotations

from qr_llm_evals.keys.secrets import KNOWN_PROVIDERS, SecretBag, SecretFilter
from qr_llm_evals.keys.verify import (
    FileKeyVerifier,
    KeyIdentity,
    KeyRefused,
    KeyVerifier,
    KeyVerifyUnavailable,
    ProbeKeyVerifier,
    build_verifier,
    fingerprint,
    parse_bearer,
    verify,
)

__all__ = [
    "KNOWN_PROVIDERS",
    "FileKeyVerifier",
    "KeyIdentity",
    "KeyRefused",
    "KeyVerifier",
    "KeyVerifyUnavailable",
    "ProbeKeyVerifier",
    "SecretBag",
    "SecretFilter",
    "build_verifier",
    "fingerprint",
    "parse_bearer",
    "verify",
]
