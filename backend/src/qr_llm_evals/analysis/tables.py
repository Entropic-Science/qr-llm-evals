"""Deterministic JSON table rendering for the analysis phase."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

DEFAULT_PRECISION = 6


def rounded(value: Any, precision: int = DEFAULT_PRECISION) -> Any:
    """Recursively round finite floats while preserving integers and unavailable values."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        result = round(value, precision)
        return 0.0 if result == 0 else result
    if isinstance(value, dict):
        return {str(key): rounded(item, precision) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [rounded(item, precision) for item in value]
    return value


def table_bytes(table: dict[str, Any]) -> bytes:
    """Canonical, human-readable bytes used both by production and re-analysis."""
    precision = int(table.get("precision", DEFAULT_PRECISION))
    payload = rounded(table, precision)
    return (
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_tables(directory: Path, tables: dict[str, dict[str, Any]]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, table in sorted(tables.items()):
        path = directory / f"{name}.json"
        path.write_bytes(table_bytes(table))
        written.append(path)
    return written


__all__ = ["DEFAULT_PRECISION", "rounded", "table_bytes", "write_tables"]
