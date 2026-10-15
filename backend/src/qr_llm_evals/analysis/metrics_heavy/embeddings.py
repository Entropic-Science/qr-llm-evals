"""Frozen sentence embeddings, loaded lazily and forced onto CPU."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import numpy as np
from numpy.typing import NDArray

from . import MetricsUnavailable

EMBEDDING_MODEL_ID = "sentence-transformers/all-mpnet-base-v2"
EMBEDDING_MODEL_REVISION = "e8c3b32edf5434bc2275fc9bab85f82640a19130"


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise MetricsUnavailable(
            "sentence-transformers is unavailable; install qr-llm-evals[metrics]"
        ) from exc
    try:
        return SentenceTransformer(
            EMBEDDING_MODEL_ID,
            revision=EMBEDDING_MODEL_REVISION,
            device="cpu",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise MetricsUnavailable(f"cannot load frozen embedding checkpoint: {exc}") from exc


def embed_texts(texts: Sequence[str]) -> NDArray[np.float64]:
    """Return L2-normalised embeddings in input order."""
    if not texts:
        return np.empty((0, 0), dtype=np.float64)
    values = _model().encode(
        list(texts),
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return np.asarray(values, dtype=np.float64)


__all__ = ["EMBEDDING_MODEL_ID", "EMBEDDING_MODEL_REVISION", "embed_texts"]
