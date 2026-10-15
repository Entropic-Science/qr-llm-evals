"""Prompt construction and the publication gate for the LLM-written narrative.

The statistical tables are the sole numeric source of truth.  A narrative is
publishable only when every numeric token it contains occurs verbatim in the
canonical rendered tables.  This intentionally conservative check catches the
most common and most damaging narrative failure: an otherwise fluent summary
inventing or silently rounding a result.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from qr_llm_evals.analysis.tables import table_bytes

NARRATIVE_SYSTEM_PROMPT = """You write a concise scientific results summary.
Use only the supplied preregistration and machine-generated tables. Never
calculate, round, reformat, or invent a number: copy every number exactly as it
appears in a table. State unavailable results as unavailable. Separate
pre-registered primary findings from exploratory findings and mention the
seed-vs-seed controls beside treatment claims. Return Markdown only."""

# A numeric token can be signed, decimal, scientific notation, or a percentage.
# Word/dot boundaries avoid treating fragments of identifiers and hashes as data.
_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?%?(?![\w.])"
)

Table = Mapping[str, Any] | str | bytes


def _rendered_table(value: Table) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return table_bytes(dict(value)).decode("utf-8")


def build_prompt(tables: Mapping[str, Table], prereg_md: str) -> str:
    """Return the frozen narrative prompt, with canonical table renderings."""
    sections = []
    for name, table in sorted(tables.items()):
        sections.append(f"### {name}\n\n```json\n{_rendered_table(table).rstrip()}\n```")
    rendered = "\n\n".join(sections)
    return (
        f"{NARRATIVE_SYSTEM_PROMPT}\n\n"
        "## Pre-registration\n\n"
        f"{prereg_md.rstrip()}\n\n"
        "## Machine-generated result tables\n\n"
        f"{rendered}\n"
    )


def prompt_hash(prompt: str) -> str:
    """SHA-256 of the exact UTF-8 prompt sent to the narrative model."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def numeric_tokens(text: str) -> list[str]:
    """Numeric tokens in order of first occurrence, without duplicates."""
    return list(dict.fromkeys(match.group(0) for match in _NUMBER.finditer(text)))


def verify_numbers(narrative: str, tables: Mapping[str, Table]) -> list[str]:
    """Return numeric tokens absent from every canonical rendered table.

    An empty list means the narrative passes the publication gate.  Matching is
    deliberately textual: changing ``0.050000`` to ``0.05`` is not allowed,
    because even harmless-looking reformats can conceal a changed interpretation.
    """
    rendered = "\n".join(_rendered_table(table) for table in tables.values())
    allowed = set(numeric_tokens(rendered))
    return [token for token in numeric_tokens(narrative) if token not in allowed]


def render_narrative(
    narrative: str,
    *,
    model: str,
    prompt_sha256: str,
    offending_numbers: list[str] | None = None,
) -> str:
    """Add the mandatory provenance label or render the withheld notice."""
    label = f"LLM-written summary (model {model}, prompt hash {prompt_sha256})"
    offending = offending_numbers or []
    if offending:
        listed = ", ".join(f"`{item}`" for item in offending)
        return (
            f"*{label}*\n\n"
            "# Narrative withheld: unverified numbers\n\n"
            "The generated summary was not published because these numeric tokens "
            f"do not occur in the machine-generated tables: {listed}.\n"
        )
    return f"*{label}*\n\n{narrative.strip()}\n"


def load_tables(directory: Path) -> dict[str, str]:
    """Load rendered JSON tables from an analysis directory.

    Keeping the rendered strings also makes the verifier inspect exactly the
    bytes exposed by the table download route.
    """
    return {
        path.stem: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.json"))
    }


__all__ = [
    "NARRATIVE_SYSTEM_PROMPT",
    "build_prompt",
    "load_tables",
    "numeric_tokens",
    "prompt_hash",
    "render_narrative",
    "verify_numbers",
]
