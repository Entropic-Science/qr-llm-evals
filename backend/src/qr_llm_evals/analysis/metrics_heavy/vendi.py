"""Embedding-kernel diversity metrics."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .embeddings import embed_texts


def embedding_diversity(texts: Sequence[str]) -> dict[str, float]:
    """Compute Vendi score and one-minus-mean-cosine on frozen embeddings."""
    if not texts:
        raise ValueError("at least one text is required")
    vectors = embed_texts(texts)
    if len(vectors) == 1:
        return {"vendi_embed": 1.0, "one_minus_mean_cosine": 0.0}
    kernel = np.clip(vectors @ vectors.T, -1.0, 1.0)
    upper = kernel[np.triu_indices(len(vectors), k=1)]
    lexical_direction = float(1.0 - np.mean(upper))

    # The Vendi score is exp(von Neumann entropy(K / trace(K))).
    eigenvalues = np.linalg.eigvalsh(kernel / float(np.trace(kernel)))
    probabilities = np.clip(eigenvalues, 0.0, None)
    probabilities = probabilities[probabilities > 1e-12]
    probabilities /= probabilities.sum()
    vendi = float(np.exp(-np.sum(probabilities * np.log(probabilities))))
    return {
        "vendi_embed": vendi,
        "one_minus_mean_cosine": lexical_direction,
    }


__all__ = ["embedding_diversity"]
