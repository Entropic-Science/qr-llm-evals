"""Append-only JSONL artifacts and the resume set (spec §2.5, D-4).

Writers only ever *append*; a file is flushed after every line and
``fsync``-ed on close (and on :meth:`JsonlWriter.sync`), so a killed process
loses at most the line it was writing. Readers are tolerant of exactly that:
a truncated final line is skipped, never fatal, so a crashed run can resume.

Resume follows the ``item_key`` pattern of qr-llm-research: the runner reads
``generations.jsonl`` back and skips every ``sample_ref`` that is already
complete (:func:`completed_refs`). Aborted / errored rows are re-runnable.

A record may name fields that are left out of the file while they are empty
(``OMIT_WHEN_EMPTY`` on the record class, read by :func:`dump_row`). That is
how a field added later leaves the files of records that do not use it as
they were.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import IO, Any, TypeVar

from pydantic import BaseModel

from qr_llm_evals.cards.result_checks import FINAL_JUDGE_STATUSES

T = TypeVar("T")

Row = dict[str, Any]
RowLike = BaseModel | Mapping[str, Any]

#: ``finish_reason`` values that make a generation row count as complete for resume.
COMPLETE_FINISH_REASONS: frozenset[str] = frozenset({"stop", "length"})


def dump_row(row: RowLike) -> Row:
    """The JSON form of a record as it is written to a file.

    A field the record class lists in ``OMIT_WHEN_EMPTY`` is dropped when it is
    ``None`` or an empty list or mapping, so the file of a record that does not
    use the field has no key for it.
    """
    if not isinstance(row, BaseModel):
        return dict(row)
    data = row.model_dump(mode="json")
    for key in getattr(type(row), "OMIT_WHEN_EMPTY", ()):
        if key in data and (data[key] is None or data[key] == [] or data[key] == {}):
            del data[key]
    return data


def encode_line(row: RowLike) -> str:
    """One JSONL line: UTF-8, NaN forbidden, trailing newline."""
    return json.dumps(dump_row(row), ensure_ascii=False, allow_nan=False) + "\n"


class JsonlWriter:
    """Append-only line writer; ``with JsonlWriter(path) as w: w.append(row)``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None
        self.lines_written = 0

    def open(self) -> JsonlWriter:
        self._handle()
        return self

    def _handle(self) -> IO[str]:
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8", newline="\n")  # noqa: SIM115
        return self._fh

    def append(self, row: RowLike) -> None:
        line = encode_line(row)  # encode first: a bad row must not leave a partial line
        fh = self._handle()
        fh.write(line)
        fh.flush()
        self.lines_written += 1

    def extend(self, rows: Iterable[RowLike]) -> int:
        n = 0
        for row in rows:
            self.append(row)
            n += 1
        return n

    def sync(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        if self._fh is not None:
            self.sync()
            self._fh.close()
            self._fh = None

    def __enter__(self) -> JsonlWriter:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()


def append_jsonl(path: Path, rows: RowLike | Iterable[RowLike]) -> int:
    """Append one row or many, fsync, close. Returns the number of lines written."""
    items: Iterable[RowLike] = [rows] if isinstance(rows, BaseModel | Mapping) else rows
    with JsonlWriter(path) as writer:
        return writer.extend(items)


def iter_jsonl(path: Path, *, strict: bool = False) -> Iterator[Row]:
    """Yield decoded rows; skip blank and truncated lines unless ``strict``."""
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as err:
                if strict:
                    raise ValueError(f"{path}:{lineno}: malformed JSONL line") from err
                continue
            if isinstance(row, dict):
                yield row


def read_jsonl(path: Path, *, strict: bool = False) -> list[Row]:
    return list(iter_jsonl(path, strict=strict))


def completed_refs(generations_jsonl: Path) -> set[str]:
    """``sample_ref``s whose generation finished (``stop`` / ``length``): the resume set."""
    refs: set[str] = set()
    for row in iter_jsonl(generations_jsonl):
        ref = row.get("sample_ref")
        if isinstance(ref, str) and row.get("finish_reason") in COMPLETE_FINISH_REASONS:
            refs.add(ref)
    return refs


def completed_judge_refs(judge_jsonl: Path) -> set[str]:
    """``sample_ref``s the judge is not asked about again.

    A row with a score, and a row whose ``status`` says a reply was received
    (also one no score could be read from, or whose value the rubric does not
    allow): a reply that was received is final. A row without a reply, and a
    row from before rows had a status that has no score, is asked again.
    """
    refs: set[str] = set()
    for row in iter_jsonl(judge_jsonl):
        ref = row.get("sample_ref")
        if not isinstance(ref, str):
            continue
        if row.get("score") is not None or row.get("status") in FINAL_JUDGE_STATUSES:
            refs.add(ref)
    return refs


def write_json(path: Path, data: RowLike, *, indent: int | None = 2) -> None:
    """Write a JSON *summary* file atomically (temp file + ``os.replace``).

    Summaries (``run.json``, ``readiness.json``, session files) are snapshots,
    not append logs, so they are rewritten in place; temp+replace means a
    crash mid-write leaves the previous snapshot intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        dump_row(data), ensure_ascii=False, allow_nan=False, indent=indent, sort_keys=True
    )
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    _retry_windows_sharing(lambda: os.replace(tmp, path))


#: On Windows a reader holding ``run.json`` open makes ``os.replace`` (and a
#: read that races the replace) fail with ``PermissionError`` for a few
#: milliseconds. The API polls snapshots while worker threads rewrite them,
#: so both sides retry briefly; POSIX never takes this path.
_SHARING_RETRIES = 40
_SHARING_SLEEP_S = 0.005


def _retry_windows_sharing(op: Callable[[], T]) -> T:
    for attempt in range(_SHARING_RETRIES):
        try:
            return op()
        except PermissionError:
            if attempt == _SHARING_RETRIES - 1:
                raise
            time.sleep(_SHARING_SLEEP_S)
    raise AssertionError("unreachable")


def read_json(path: Path) -> Row:
    data = json.loads(_retry_windows_sharing(lambda: path.read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings (prereg hashes are over LF bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


__all__ = [
    "COMPLETE_FINISH_REASONS",
    "JsonlWriter",
    "Row",
    "RowLike",
    "append_jsonl",
    "completed_judge_refs",
    "completed_refs",
    "dump_row",
    "encode_line",
    "iter_jsonl",
    "read_json",
    "read_jsonl",
    "write_json",
    "write_text",
]
