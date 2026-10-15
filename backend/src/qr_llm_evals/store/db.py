"""SQLite index over the on-disk artifacts (spec D-4, §4.4 table list).

The database is an **index, never the source of truth**: ``run.json``, the
session files and the JSONL rows on disk are canonical and are what a bundle
exports. Every table here can be rebuilt from them, which is why the file is
excluded from bundles and why a schema change is a plain forward migration in
:data:`MIGRATIONS` rather than a data migration.

Concurrency: one connection per :class:`Index`, ``check_same_thread=False``
behind an ``RLock`` (FastAPI runs sync handlers in a thread pool); WAL mode so
readers never block the runner's writes. All SQL is static text with bound
parameters - no string-built statements.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

Row = dict[str, Any]

#: Forward-only migrations ``(version, sql)``; each applied inside one transaction.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE studies (
            study_id     TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            config_hash  TEXT NOT NULL,
            prereg_hash  TEXT,
            created_at   TEXT NOT NULL,
            frozen_at    TEXT NOT NULL
        );
        CREATE TABLE runs (
            run_id       TEXT PRIMARY KEY,
            study_id     TEXT NOT NULL REFERENCES studies(study_id),
            kind         TEXT NOT NULL,
            state        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            key_name     TEXT NOT NULL,
            withdrawn    INTEGER NOT NULL DEFAULT 0,
            needs_keys   INTEGER NOT NULL DEFAULT 0,
            updated_at   TEXT NOT NULL,
            json         TEXT NOT NULL
        );
        CREATE INDEX runs_study_id ON runs(study_id);
        CREATE INDEX runs_state ON runs(state);
        CREATE TABLE sessions (
            session_id   TEXT PRIMARY KEY,
            run_id       TEXT NOT NULL REFERENCES runs(run_id),
            idx          INTEGER NOT NULL,
            started_at   TEXT,
            ended_at     TEXT,
            json         TEXT NOT NULL
        );
        CREATE INDEX sessions_run_id ON sessions(run_id);
        CREATE TABLE windows (
            window_id    TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL REFERENCES sessions(session_id),
            run_id       TEXT NOT NULL REFERENCES runs(run_id),
            kind         TEXT NOT NULL,
            start        TEXT NOT NULL,
            end_at       TEXT
        );
        CREATE INDEX windows_run_id ON windows(run_id);
        CREATE INDEX windows_session_id ON windows(session_id);
        CREATE TABLE observers (
            observer_id  TEXT NOT NULL,
            run_id       TEXT NOT NULL REFERENCES runs(run_id),
            display_name TEXT NOT NULL,
            joined_at    TEXT NOT NULL,
            consent_at   TEXT,
            PRIMARY KEY (observer_id, run_id)
        );
        CREATE INDEX observers_run_id ON observers(run_id);
        CREATE TABLE questionnaires (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT NOT NULL REFERENCES runs(run_id),
            observer_id  TEXT NOT NULL,
            session_id   TEXT NOT NULL,
            phase        TEXT NOT NULL,
            at           TEXT NOT NULL,
            json         TEXT NOT NULL
        );
        CREATE INDEX questionnaires_run_id ON questionnaires(run_id);
        CREATE TABLE taints (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT NOT NULL REFERENCES runs(run_id),
            sample_ref   TEXT,
            window_id    TEXT,
            kind         TEXT NOT NULL,
            detail       TEXT NOT NULL DEFAULT '',
            at           TEXT NOT NULL
        );
        CREATE INDEX taints_run_id ON taints(run_id);
        CREATE TABLE progress (
            run_id       TEXT NOT NULL REFERENCES runs(run_id),
            arm_id       TEXT NOT NULL,
            card_id      TEXT NOT NULL,
            done         INTEGER NOT NULL DEFAULT 0,
            total        INTEGER NOT NULL DEFAULT 0,
            aborted      INTEGER NOT NULL DEFAULT 0,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (run_id, arm_id, card_id)
        );
        CREATE INDEX progress_run_id ON progress(run_id);
        """,
    ),
)

SCHEMA_VERSION: int = MIGRATIONS[-1][0]

#: Static statements run by :meth:`Index.clear_run` (index rows only, never artifacts).
_CLEAR_RUN_SQL: tuple[str, ...] = (
    "DELETE FROM windows WHERE run_id = ?",
    "DELETE FROM sessions WHERE run_id = ?",
    "DELETE FROM observers WHERE run_id = ?",
    "DELETE FROM questionnaires WHERE run_id = ?",
    "DELETE FROM taints WHERE run_id = ?",
    "DELETE FROM progress WHERE run_id = ?",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dump(obj: BaseModel | Mapping[str, Any]) -> Row:
    return obj.model_dump(mode="json") if isinstance(obj, BaseModel) else dict(obj)


def _json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _loads(row: Row) -> Row:
    return dict(json.loads(row["json"]))


class Index:
    """The SQLite index; constructing it creates the file and applies pending migrations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                int(r["version"])
                for r in self._conn.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                # executescript() commits any open transaction first, so the
                # DDL and its bookkeeping row are wrapped explicitly here.
                self._conn.executescript("BEGIN;" + sql + "COMMIT;")
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )

    @property
    def schema_version(self) -> int:
        row = self._one("SELECT MAX(version) AS v FROM schema_migrations")
        return int(row["v"] or 0) if row else 0

    # -- studies -----------------------------------------------------------

    def upsert_study(
        self,
        study_id: str,
        *,
        title: str,
        config_hash: str,
        prereg_hash: str | None,
        created_at: str,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO studies (study_id, title, config_hash, prereg_hash, created_at, frozen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(study_id) DO UPDATE SET
                    title = excluded.title, config_hash = excluded.config_hash,
                    prereg_hash = excluded.prereg_hash
                """,
                (study_id, title, config_hash, prereg_hash, created_at, _now()),
            )

    def get_study(self, study_id: str) -> Row | None:
        return self._one("SELECT * FROM studies WHERE study_id = ?", (study_id,))

    def list_studies(self) -> list[Row]:
        return self._all("SELECT * FROM studies ORDER BY created_at, study_id")

    # -- runs --------------------------------------------------------------

    def upsert_run(self, run: BaseModel | Mapping[str, Any]) -> None:
        """Index a ``Run`` record (the full record is kept as JSON for cheap reads)."""
        data = _dump(run)
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, study_id, kind, state, created_at, key_name,
                                  withdrawn, needs_keys, updated_at, json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state = excluded.state, withdrawn = excluded.withdrawn,
                    needs_keys = excluded.needs_keys, updated_at = excluded.updated_at,
                    json = excluded.json
                """,
                (
                    data["run_id"],
                    data["study_id"],
                    data["kind"],
                    data["state"],
                    data["created_at"],
                    data["key_name"],
                    int(bool(data.get("withdrawn", False))),
                    int(bool(data.get("needs_keys", False))),
                    _now(),
                    _json(data),
                ),
            )

    def get_run(self, run_id: str) -> Row | None:
        row = self._one("SELECT json FROM runs WHERE run_id = ?", (run_id,))
        return None if row is None else _loads(row)

    def list_runs(self, *, study_id: str | None = None, state: str | None = None) -> list[Row]:
        if study_id is not None and state is not None:
            rows = self._all(
                "SELECT json FROM runs WHERE study_id = ? AND state = ? ORDER BY created_at, run_id",
                (study_id, state),
            )
        elif study_id is not None:
            rows = self._all(
                "SELECT json FROM runs WHERE study_id = ? ORDER BY created_at, run_id",
                (study_id,),
            )
        elif state is not None:
            rows = self._all(
                "SELECT json FROM runs WHERE state = ? ORDER BY created_at, run_id", (state,)
            )
        else:
            rows = self._all("SELECT json FROM runs ORDER BY created_at, run_id")
        return [_loads(r) for r in rows]

    def count_active_runs(self, key_name: str) -> int:
        """Runs gated by ``key_name`` that are not finished (launch-gate quota, spec §2.4)."""
        row = self._one(
            "SELECT COUNT(*) AS n FROM runs WHERE key_name = ? "
            "AND state NOT IN ('complete', 'aborted')",
            (key_name,),
        )
        return int(row["n"]) if row else 0

    # -- sessions / windows / observers / questionnaires -------------------

    def upsert_session(self, session: BaseModel | Mapping[str, Any]) -> None:
        """Index a supervised ``Session`` and replace its ``windows`` rows."""
        data = _dump(session)
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, run_id, idx, started_at, ended_at, json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    started_at = excluded.started_at, ended_at = excluded.ended_at,
                    json = excluded.json
                """,
                (
                    data["session_id"],
                    data["run_id"],
                    int(data.get("index", 0)),
                    data.get("started_at"),
                    data.get("ended_at"),
                    _json(data),
                ),
            )
            conn.execute("DELETE FROM windows WHERE session_id = ?", (data["session_id"],))
            for window in data.get("windows", []):
                conn.execute(
                    "INSERT INTO windows (window_id, session_id, run_id, kind, start, end_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        window["window_id"],
                        data["session_id"],
                        data["run_id"],
                        window["kind"],
                        window["start"],
                        window.get("end"),
                    ),
                )

    def get_session(self, session_id: str) -> Row | None:
        row = self._one("SELECT json FROM sessions WHERE session_id = ?", (session_id,))
        return None if row is None else _loads(row)

    def list_sessions(self, run_id: str) -> list[Row]:
        rows = self._all("SELECT json FROM sessions WHERE run_id = ? ORDER BY idx", (run_id,))
        return [_loads(r) for r in rows]

    def list_windows(self, run_id: str) -> list[Row]:
        return self._all(
            "SELECT window_id, session_id, run_id, kind, start, end_at AS end "
            "FROM windows WHERE run_id = ? ORDER BY start",
            (run_id,),
        )

    def upsert_observer(self, observer: BaseModel | Mapping[str, Any]) -> None:
        data = _dump(observer)
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO observers (observer_id, run_id, display_name, joined_at, consent_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(observer_id, run_id) DO UPDATE SET
                    display_name = excluded.display_name, consent_at = excluded.consent_at
                """,
                (
                    data["observer_id"],
                    data["run_id"],
                    data["display_name"],
                    data["joined_at"],
                    data.get("consent_at"),
                ),
            )

    def list_observers(self, run_id: str) -> list[Row]:
        return self._all(
            "SELECT * FROM observers WHERE run_id = ? ORDER BY joined_at, observer_id", (run_id,)
        )

    def insert_questionnaire(self, run_id: str, q: BaseModel | Mapping[str, Any]) -> None:
        data = _dump(q)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO questionnaires (run_id, observer_id, session_id, phase, at, json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    data["observer_id"],
                    data["session_id"],
                    data["phase"],
                    data["at"],
                    _json(data),
                ),
            )

    def list_questionnaires(self, run_id: str) -> list[Row]:
        rows = self._all("SELECT json FROM questionnaires WHERE run_id = ? ORDER BY id", (run_id,))
        return [_loads(r) for r in rows]

    # -- taints / progress -------------------------------------------------

    def insert_taint(self, run_id: str, taint: BaseModel | Mapping[str, Any]) -> None:
        data = _dump(taint)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO taints (run_id, sample_ref, window_id, kind, detail, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    data.get("sample_ref"),
                    data.get("window_id"),
                    data["kind"],
                    data.get("detail", ""),
                    data["at"],
                ),
            )

    def list_taints(self, run_id: str) -> list[Row]:
        return self._all(
            "SELECT sample_ref, window_id, kind, detail, at FROM taints "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        )

    def upsert_progress(
        self, run_id: str, arm_id: str, card_id: str, *, done: int, total: int, aborted: int = 0
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO progress (run_id, arm_id, card_id, done, total, aborted, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, arm_id, card_id) DO UPDATE SET
                    done = excluded.done, total = excluded.total, aborted = excluded.aborted,
                    updated_at = excluded.updated_at
                """,
                (run_id, arm_id, card_id, done, total, aborted, _now()),
            )

    def progress(self, run_id: str) -> dict[str, dict[str, Row]]:
        """``{arm_id: {card_id: {done, total, aborted}}}`` (the shape of ``Run.progress.by_arm``)."""
        out: dict[str, dict[str, Row]] = {}
        for r in self._all(
            "SELECT arm_id, card_id, done, total, aborted FROM progress WHERE run_id = ? "
            "ORDER BY arm_id, card_id",
            (run_id,),
        ):
            out.setdefault(r["arm_id"], {})[r["card_id"]] = {
                "done": r["done"],
                "total": r["total"],
                "aborted": r["aborted"],
            }
        return out

    # -- rebuild -----------------------------------------------------------

    def clear_run(self, run_id: str) -> None:
        """Drop the per-run *index* rows before re-indexing them from disk.

        Only the rebuildable SQLite index is touched; artifacts on disk are
        never removed (AGENTS.md invariant 4). The ``runs`` row itself stays.
        """
        with self._tx() as conn:
            for sql in _CLEAR_RUN_SQL:
                conn.execute(sql, (run_id,))

    # -- helpers -----------------------------------------------------------

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> Row | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return None if row is None else dict(row)

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]


__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "Index", "Row"]
