"""Helpers around :class:`Tier0Record`: the join key, grouping, JSONL I/O (spec §2.6).

Everything here is pure data plumbing shared by :mod:`~qr_llm_evals.tier0.acquire`
(which *builds* records) and :mod:`~qr_llm_evals.tier0.endpoints` (which
*consumes* them). Layer 1: imports nothing above ``models``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qr_llm_evals.tier0.schema import Tier0Record

#: vLLM ``--return-tokens-as-token-ids`` renders every token as ``token_id:<int>``.
TOKEN_ID_PREFIX = "token_id:"  # nosec B105 - a wire-format prefix, not a credential
#: Synthetic ids derived from token *text* live in ``[2**30, 2**31)`` so they can never
#: collide with a real vocabulary index (vocabularies are far below 2**30).
_SYNTHETIC_ID_BASE = 1 << 30
_SYNTHETIC_ID_SPAN = 1 << 30


@dataclass(frozen=True, slots=True)
class SampleRefParts:
    """``<arm_id>/<card_id>/<prompt_id>/<epoch>[/<told>]`` split into its parts."""

    arm_id: str
    card_id: str
    prompt_id: str
    epoch: int
    told_condition: str | None = None

    @property
    def prompt_key(self) -> str:
        """The arm-independent pairing key (``card/prompt[/told]``) used within-prompt."""
        key = f"{self.card_id}/{self.prompt_id}"
        return f"{key}/{self.told_condition}" if self.told_condition else key


def parse_sample_ref(sample_ref: str) -> SampleRefParts:
    """Split a join key; raises ``ValueError`` for anything that is not one."""
    parts = sample_ref.split("/")
    if len(parts) not in (4, 5) or not all(parts):
        raise ValueError(
            f"sample_ref {sample_ref!r} must be '<arm>/<card>/<prompt>/<epoch>[/<told>]'"
        )
    try:
        epoch = int(parts[3])
    except ValueError as err:
        raise ValueError(f"sample_ref {sample_ref!r}: epoch {parts[3]!r} is not an int") from err
    if epoch < 0:
        raise ValueError(f"sample_ref {sample_ref!r}: epoch must be >= 0")
    return SampleRefParts(
        parts[0], parts[1], parts[2], epoch, parts[4] if len(parts) == 5 else None
    )


def prompt_key(sample_ref: str) -> str:
    return parse_sample_ref(sample_ref).prompt_key


def token_id_from_api_token(token: str) -> int:
    """A stable non-negative id for a token the API returned as text.

    With vLLM's ``--return-tokens-as-token-ids`` the text is ``token_id:<int>``
    and the real vocabulary index is recovered. Otherwise the id is a
    deterministic hash of the UTF-8 text placed above any real vocabulary
    index; only *equality* of ids is ever used on the degraded path (Hamming
    kernel, repetition rate), so a collision-resistant hash is sufficient.
    """
    if token.startswith(TOKEN_ID_PREFIX):
        suffix = token[len(TOKEN_ID_PREFIX) :]
        if suffix.isdigit():
            return int(suffix)
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return _SYNTHETIC_ID_BASE + int.from_bytes(digest[:4], "big") % _SYNTHETIC_ID_SPAN


def entropy_nats(probs: Sequence[float]) -> float:
    """Shannon entropy (nats) of a probability vector, renormalised to sum to 1."""
    total = float(sum(p for p in probs if p > 0))
    if total <= 0:
        return 0.0
    h = 0.0
    for p in probs:
        if p > 0:
            q = p / total
            h -= q * math.log(q)
    return max(h, 0.0)


def group_by_sample(records: Iterable[Tier0Record]) -> dict[str, list[Tier0Record]]:
    """Records per ``sample_ref``, each list sorted by ``token_index``; insertion-ordered keys."""
    out: dict[str, list[Tier0Record]] = {}
    for rec in records:
        out.setdefault(rec.sample_ref, []).append(rec)
    for seq in out.values():
        seq.sort(key=lambda r: r.token_index)
    return out


#: The told cell kept by :func:`independent_streams` (present on every told/actual card).
INDEPENDENT_TOLD_CELL = "told_nothing"


def independent_streams(records: Iterable[Tier0Record]) -> list[Tier0Record]:
    """Drop the told/actual duplicates that share one seeded entropy stream.

    The three told cells of one ``(card, prompt, epoch)`` share a ``qr_seed``:
    the told note is not in the pairing key (``models/seeds.py``), by design, so
    the told contrast runs on common random numbers. On a seeded arm their
    ``u``-streams are therefore identical, and their tokens and PIT residuals are
    dependent, which makes stream-level tests that treat tokens or samples as
    independent anti-conservative. Those tests keep records without a told cell
    plus the ``told_nothing`` cell, on every arm alike so arms stay comparable.
    Input order is preserved.
    """
    keep: dict[str, bool] = {}
    out: list[Tier0Record] = []
    for rec in records:
        ok = keep.get(rec.sample_ref)
        if ok is None:
            told = parse_sample_ref(rec.sample_ref).told_condition
            ok = keep[rec.sample_ref] = told is None or told == INDEPENDENT_TOLD_CELL
        if ok:
            out.append(rec)
    return out


def token_sequences(records: Iterable[Tier0Record]) -> dict[str, list[int]]:
    """``sample_ref -> [token_id, …]`` in generation order."""
    return {ref: [r.token_id for r in seq] for ref, seq in group_by_sample(records).items()}


def records_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[Tier0Record]:
    return [Tier0Record.model_validate(dict(row)) for row in rows]


def read_tier0_jsonl(path: Path) -> list[Tier0Record]:
    """Parse a ``tier0.jsonl`` file (blank lines ignored) into validated records."""
    out: list[Tier0Record] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as err:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {err}") from err
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{lineno}: each line must be an object")
            out.append(Tier0Record.model_validate(obj))
    return out


def records_to_rows(records: Iterable[Tier0Record]) -> list[dict[str, Any]]:
    """Plain-JSON rows (``exclude_none``) for ``store.append_tier0``."""
    return [r.model_dump(mode="json", exclude_none=True) for r in records]


FieldsSetPool = dict[frozenset[str], set[str]]


def share_fields_set(record: Tier0Record, pool: FieldsSetPool) -> Tier0Record:
    """Make ``record`` share its "fields that were set" bookkeeping with its look-alikes.

    Pydantic keeps one ``set`` of field names per model instance. For a record
    with twenty-odd fields that set is about four fifths of the instance's
    memory (roughly 2.9 KB against 0.55 KB without it, measured), and a full
    study loads millions of records whose sets are identical. Pointing them at
    one shared set per distinct shape is what lets the analysis of a full run
    fit in a few gigabytes. The records are only read after loading; nothing
    may add to a record's fields set in place.
    """
    key = frozenset(record.model_fields_set)
    shared = pool.get(key)
    if shared is None:
        shared = pool[key] = set(key)
    object.__setattr__(record, "__pydantic_fields_set__", shared)
    return record


__all__ = [
    "INDEPENDENT_TOLD_CELL",
    "TOKEN_ID_PREFIX",
    "FieldsSetPool",
    "SampleRefParts",
    "entropy_nats",
    "group_by_sample",
    "independent_streams",
    "parse_sample_ref",
    "prompt_key",
    "read_tier0_jsonl",
    "records_from_rows",
    "records_to_rows",
    "share_fields_set",
    "token_id_from_api_token",
    "token_sequences",
]
