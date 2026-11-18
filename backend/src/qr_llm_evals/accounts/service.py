"""User accounts and login sessions, in their own SQLite file.

``accounts.sqlite`` is **not** the run index: ``index.sqlite`` can be rebuilt
from the artifacts on disk, accounts cannot, so they live in a separate file
with their own forward-only :data:`MIGRATIONS`. Never delete it.

What is stored about a user's qr-llm-api key: its SHA-256, its name in the
keys file (when known) and an 8-hex fingerprint. Never the key. What is stored
about a session token: its SHA-256. Never the token. Both secrets are
high-entropy random strings, so an unsalted digest is enough; passwords are
not, and go through scrypt (:mod:`qr_llm_evals.accounts.passwords`).

Concurrency follows ``store/db.py``: one connection, ``check_same_thread=False``
behind an ``RLock``, WAL mode, static SQL with bound parameters only.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qr_llm_evals.accounts.passwords import (
    PASSWORD_MAX,
    PASSWORD_MIN,
    dummy_hash,
    hash_password,
    needs_rehash,
    verify_password,
)

ACCOUNTS_DB_NAME = "accounts.sqlite"
SESSION_PREFIX = "qrs_"
SESSION_TOKEN_BYTES = 32
USER_ID_BYTES = 8
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")

Clock = Callable[[], datetime]

#: Forward-only migrations ``(version, sql)``; each applied inside one transaction.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE users (
            user_id          TEXT PRIMARY KEY,
            username         TEXT NOT NULL UNIQUE,
            password_hash    TEXT NOT NULL,
            key_sha256       TEXT NOT NULL UNIQUE,
            key_name         TEXT,
            key_fingerprint  TEXT NOT NULL,
            is_admin         INTEGER NOT NULL DEFAULT 0,
            disabled         INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL,
            key_linked_at    TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_sha256  TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES users(user_id),
            created_at    TEXT NOT NULL,
            expires_at    TEXT NOT NULL
        );
        CREATE INDEX sessions_user_id ON sessions(user_id);
        """,
    ),
    (
        2,
        """
        ALTER TABLE users ADD COLUMN token_quota INTEGER;
        CREATE TABLE usage_events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id            TEXT NOT NULL REFERENCES users(user_id),
            run_id             TEXT NOT NULL,
            kind               TEXT NOT NULL,
            arm_id             TEXT,
            card_id            TEXT,
            source_label       TEXT,
            model              TEXT NOT NULL,
            prompt_tokens      INTEGER NOT NULL,
            completion_tokens  INTEGER NOT NULL,
            reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
            charged            INTEGER NOT NULL DEFAULT 0,
            at                 TEXT NOT NULL
        );
        CREATE INDEX usage_events_user_at ON usage_events(user_id, at);
        CREATE INDEX usage_events_run ON usage_events(run_id);
        """,
    ),
)


class AccountError(Exception):
    """Base for the refusals a route turns into a 4xx."""


class InvalidUsername(AccountError):
    pass


class InvalidPassword(AccountError):
    pass


class UsernameTaken(AccountError):
    pass


class KeyAlreadyLinked(AccountError):
    """The key's digest is already linked to another account (one account per key)."""


class BadCredentials(AccountError):
    """Unknown username or wrong password; deliberately not distinguished."""


class AccountDisabled(AccountError):
    pass


class UnknownUser(AccountError):
    pass


@dataclass(frozen=True, slots=True)
class User:
    user_id: str
    username: str
    key_sha256: str
    key_name: str | None
    key_fingerprint: str
    is_admin: bool
    disabled: bool
    created_at: datetime
    key_linked_at: datetime
    #: Lifetime token budget; ``None`` = the configured default, ``0`` = unlimited.
    token_quota: int | None = None

    @property
    def quota_id(self) -> str:
        """Same bucket the raw launch key would use: the key's name, else its fingerprint."""
        return self.key_name or self.key_fingerprint


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Returned once at login/signup; ``token`` is never stored or logged."""

    token: str
    expires_at: datetime


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalise_username(raw: str) -> str:
    name = raw.strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise InvalidUsername(
            "username must be 3-32 characters: lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return name


def check_password(password: str) -> None:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise InvalidPassword(f"password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters")


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        key_sha256=row["key_sha256"],
        key_name=row["key_name"],
        key_fingerprint=row["key_fingerprint"],
        is_admin=bool(row["is_admin"]),
        disabled=bool(row["disabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        key_linked_at=datetime.fromisoformat(row["key_linked_at"]),
        token_quota=row["token_quota"],
    )


class Accounts:
    """Users, their linked key digest, and their login sessions."""

    def __init__(
        self,
        path: str | Path,
        *,
        session_ttl: timedelta = timedelta(hours=168),
        clock: Clock = lambda: datetime.now(UTC),
        scrypt_n: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.session_ttl = session_ttl
        self._clock = clock
        self._scrypt_n = scrypt_n
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

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
                self._conn.executescript("BEGIN;" + sql + "COMMIT;")
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _iso(self._clock())),
                )

    def _hash(self, password: str) -> str:
        if self._scrypt_n is None:
            return hash_password(password)
        return hash_password(password, n=self._scrypt_n)

    def _one(self, sql: str, params: tuple[object, ...]) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
            return row

    # -- users -------------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        *,
        key_sha256: str,
        key_name: str | None,
        key_fingerprint: str,
    ) -> User:
        """Create an account for an already-verified key (the caller did the verifying)."""
        name = normalise_username(username)
        check_password(password)
        now = _iso(self._clock())
        user_id = "usr_" + secrets.token_hex(USER_ID_BYTES)
        password_hash = self._hash(password)
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
                raise UsernameTaken(f"username {name!r} is taken")
            if conn.execute("SELECT 1 FROM users WHERE key_sha256 = ?", (key_sha256,)).fetchone():
                raise KeyAlreadyLinked("this API key is already linked to an account")
            conn.execute(
                "INSERT INTO users (user_id, username, password_hash, key_sha256, key_name, "
                "key_fingerprint, created_at, key_linked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, password_hash, key_sha256, key_name, key_fingerprint, now, now),
            )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> User:
        row = self._one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if row is None:
            raise UnknownUser(f"no user {user_id!r}")
        return _user(row)

    def find_user(self, username: str) -> User | None:
        row = self._one("SELECT * FROM users WHERE username = ?", (username.strip().lower(),))
        return _user(row) if row is not None else None

    def list_users(self) -> list[User]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY created_at, user_id").fetchall()
        return [_user(r) for r in rows]

    def authenticate(self, username: str, password: str) -> User:
        """The user for a correct username + password; one scrypt on every path."""
        row = self._one("SELECT * FROM users WHERE username = ?", (username.strip().lower(),))
        stored = row["password_hash"] if row is not None else dummy_hash()
        ok = verify_password(password, stored)
        if row is None or not ok:
            raise BadCredentials("wrong username or password")
        user = _user(row)
        if user.disabled:
            raise AccountDisabled("this account is disabled")
        wanted_n = self._scrypt_n
        stale = needs_rehash(stored) if wanted_n is None else needs_rehash(stored, n=wanted_n)
        if stale:
            with self._tx() as conn:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE user_id = ?",
                    (self._hash(password), user.user_id),
                )
        return user

    def set_password(self, user_id: str, password: str) -> None:
        """Replace the password and sign the user out everywhere."""
        check_password(password)
        self.get_user(user_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (self._hash(password), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def link_key(
        self, user_id: str, *, key_sha256: str, key_name: str | None, key_fingerprint: str
    ) -> User:
        """Point the account at a different (already-verified) key."""
        self.get_user(user_id)
        with self._tx() as conn:
            other = conn.execute(
                "SELECT user_id FROM users WHERE key_sha256 = ?", (key_sha256,)
            ).fetchone()
            if other is not None and other["user_id"] != user_id:
                raise KeyAlreadyLinked("this API key is already linked to another account")
            conn.execute(
                "UPDATE users SET key_sha256 = ?, key_name = ?, key_fingerprint = ?, "
                "key_linked_at = ? WHERE user_id = ?",
                (key_sha256, key_name, key_fingerprint, _iso(self._clock()), user_id),
            )
        return self.get_user(user_id)

    def set_disabled(self, user_id: str, disabled: bool) -> User:
        """Disable (and sign out) or re-enable an account."""
        self.get_user(user_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE users SET disabled = ? WHERE user_id = ?", (int(disabled), user_id)
            )
            if disabled:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return self.get_user(user_id)

    def set_admin(self, user_id: str, is_admin: bool) -> User:
        self.get_user(user_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE users SET is_admin = ? WHERE user_id = ?", (int(is_admin), user_id)
            )
        return self.get_user(user_id)

    def set_quota(self, user_id: str, token_quota: int | None) -> User:
        """Set the lifetime token budget: ``None`` = configured default, ``0`` = unlimited."""
        if token_quota is not None and token_quota < 0:
            raise AccountError("token quota cannot be negative")
        self.get_user(user_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE users SET token_quota = ? WHERE user_id = ?", (token_quota, user_id)
            )
        return self.get_user(user_id)

    # -- sessions ----------------------------------------------------------

    def create_session(self, user_id: str) -> IssuedSession:
        token = SESSION_PREFIX + secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = self._clock()
        expires = now + self.session_ttl
        with self._tx() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(now),))
            conn.execute(
                "INSERT INTO sessions (token_sha256, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (sha256_hex(token), user_id, _iso(now), _iso(expires)),
            )
        return IssuedSession(token=token, expires_at=expires)

    def user_for_session(self, token: str) -> User | None:
        """The signed-in, enabled user behind ``token``; ``None`` for anything else."""
        if not token.startswith(SESSION_PREFIX):
            return None
        row = self._one(
            "SELECT u.* FROM sessions s JOIN users u ON u.user_id = s.user_id "
            "WHERE s.token_sha256 = ? AND s.expires_at > ? AND u.disabled = 0",
            (sha256_hex(token), _iso(self._clock())),
        )
        return _user(row) if row is not None else None

    def revoke_session(self, token: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_sha256 = ?", (sha256_hex(token),))

    def session_count(self, user_id: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ? AND expires_at > ?",
            (user_id, _iso(self._clock())),
        )
        return int(row["n"]) if row is not None else 0


__all__ = [
    "ACCOUNTS_DB_NAME",
    "MIGRATIONS",
    "SESSION_PREFIX",
    "AccountDisabled",
    "AccountError",
    "Accounts",
    "BadCredentials",
    "InvalidPassword",
    "InvalidUsername",
    "IssuedSession",
    "KeyAlreadyLinked",
    "UnknownUser",
    "User",
    "UsernameTaken",
    "check_password",
    "normalise_username",
    "sha256_hex",
]
