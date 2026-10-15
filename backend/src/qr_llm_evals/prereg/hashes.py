"""Document hashes are SHA-256 of UTF-8 LF bytes (D-11)."""

import hashlib

from qr_llm_evals.models.schema import config_hash as config_hash


def normalize_lf(markdown: str) -> str:
    return markdown.replace("\r\n", "\n").replace("\r", "\n")


def prereg_hash(markdown: str) -> str:
    return hashlib.sha256(normalize_lf(markdown).encode("utf-8")).hexdigest()
