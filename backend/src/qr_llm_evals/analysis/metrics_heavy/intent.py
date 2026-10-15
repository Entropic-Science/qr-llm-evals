"""Exploratory intention-alignment endpoint (spec section 2.10)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .embeddings import EMBEDDING_MODEL_ID, EMBEDDING_MODEL_REVISION, embed_texts


def intent_cosine(intention_text: str, outputs: Sequence[str]) -> list[float]:
    """Cosine similarity of each output to an intention using the frozen encoder."""
    if not intention_text.strip():
        raise ValueError("intention_text must not be blank")
    if not outputs:
        return []
    vectors = embed_texts([intention_text, *outputs])
    return [float(value) for value in np.clip(vectors[1:] @ vectors[0], -1.0, 1.0)]


__all__ = ["EMBEDDING_MODEL_ID", "EMBEDDING_MODEL_REVISION", "intent_cosine"]
