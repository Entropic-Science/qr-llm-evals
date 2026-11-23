"""The sentence encoder SaGE scores with, frozen, loaded lazily and forced onto CPU.

SaGE (Bonagiri et al., arXiv 2402.13709) measures the similarity of two answers
with ``sentence-transformers/stsb-distilroberta-base-v2``. The checkpoint is a
different one from :mod:`.embeddings`, so it has its own loader and pin.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import numpy as np
from numpy.typing import NDArray

from . import MetricsUnavailable

SAGE_MODEL_ID = "sentence-transformers/stsb-distilroberta-base-v2"
SAGE_MODEL_REVISION = "c5319c5ec2eb7cb173d9756028b87c90c8f2c411"


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise MetricsUnavailable(
            "sentence-transformers is unavailable; install qr-llm-evals[metrics]"
        ) from exc
    try:
        return SentenceTransformer(SAGE_MODEL_ID, revision=SAGE_MODEL_REVISION, device="cpu")
    except (OSError, RuntimeError, ValueError) as exc:
        raise MetricsUnavailable(f"cannot load frozen SaGE encoder: {exc}") from exc


def cosine_matrix(texts: Sequence[str]) -> NDArray[np.float64]:
    """Pairwise cosine similarity of ``texts``, in input order."""
    if not texts:
        return np.empty((0, 0), dtype=np.float64)
    values = _model().encode(
        list(texts),
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    vectors = np.asarray(values, dtype=np.float64)
    return np.asarray(vectors @ vectors.T, dtype=np.float64)


__all__ = ["SAGE_MODEL_ID", "SAGE_MODEL_REVISION", "cosine_matrix"]
