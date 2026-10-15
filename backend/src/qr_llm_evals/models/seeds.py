"""Per-request seed derivation for the seeded-PRNG arms (spec §2.2, D-6).

qr-sampler restarts its counter-based Philox stream per request, so one raw
``qr_seed`` for every prompt would make the control arm's u-stream identical
across prompts (``docs/determinism.md`` audit note 5b). Instead every request
gets ``qr_seed = H(study_seed, card_id, prompt_id, epoch)``: a pure function
of the study seed and the pairing key, so the PRNG arm is replayable from
``(study_seed, prompt, epoch)`` (FR-4.1) while distinct prompts and epochs get
distinct streams.

The function is byte-stable across platforms (SHA-256 over a fixed UTF-8
string; fixed vectors in ``backend/tests/test_seeds.py``). Do not change the
separator or the truncation without bumping the prereg schema version: it
would silently change every seeded arm's outputs.
"""

from __future__ import annotations

import hashlib

from qr_llm_evals.models.schema import SEED_MAX_EXCLUSIVE

#: Field separator in the hashed pairing string. ``|`` cannot occur in a card
#: id (``^[a-z0-9_]+$``); ``prompt_id`` is checked for it explicitly.
SEPARATOR = "|"


def pairing_key(study_seed: int, card_id: str, prompt_id: str, epoch: int) -> str:
    """The exact string hashed by :func:`derive_seed` (kept for audit tables)."""
    if not 0 <= study_seed < SEED_MAX_EXCLUSIVE:
        raise ValueError(f"study_seed {study_seed} outside [0, 2**63)")
    if epoch < 0:
        raise ValueError(f"epoch must be >= 0, got {epoch}")
    for name, value in (("card_id", card_id), ("prompt_id", prompt_id)):
        if not value or SEPARATOR in value:
            raise ValueError(f"{name} must be non-empty and free of {SEPARATOR!r}: {value!r}")
    return SEPARATOR.join((str(study_seed), card_id, prompt_id, str(epoch)))


def derive_seed(study_seed: int, card_id: str, prompt_id: str, epoch: int) -> int:
    """Per-request ``qr_seed`` in ``[0, 2**63)``: a pure function of the study seed and the pairing key."""
    digest = hashlib.sha256(
        pairing_key(study_seed, card_id, prompt_id, epoch).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") >> 1


__all__ = ["SEPARATOR", "derive_seed", "pairing_key"]
