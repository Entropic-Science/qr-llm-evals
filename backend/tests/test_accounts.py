"""User accounts: password hashing, the accounts store, the auth routes, the CLI.

Key-shaped fixtures are built by concatenation (see ``test_hygiene.py``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from qr_llm_evals.accounts import (
    AccountDisabled,
    Accounts,
    BadCredentials,
    InvalidPassword,
    InvalidUsername,
    KeyAlreadyLinked,
    UnknownUser,
    UsernameTaken,
)
from qr_llm_evals.accounts import cli as accounts_cli
from qr_llm_evals.accounts.passwords import hash_password, needs_rehash, verify_password
from qr_llm_evals.api import create_app
from qr_llm_evals.api.deps import AppState, recheck_linked_key
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.cards import load_cards
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.keys.verify import FileKeyVerifier, ProbeKeyVerifier, sha256_hex
from qr_llm_evals.logging_utils import SecretFilter
from test_api import API, FIXTURES, _study_dict

KEY_A = "qrk_" + "alice_key_0123456789abcdef"
KEY_B = "qrk_" + "bob_key_0123456789abcdefgh"
KEY_UNKNOWN = "qrk_" + "nobody_key_0123456789abcd"
PASSWORD = "correct horse battery"
FAST_N = 2**4  # scrypt cost for tests; the default takes ~50 ms per hash


# ── passwords ─────────────────────────────────────────────────────────────────


def test_password_hash_round_trip_and_salt() -> None:
    one = hash_password(PASSWORD, n=FAST_N)
    two = hash_password(PASSWORD, n=FAST_N)
    assert one != two, "each hash gets its own salt"
    assert PASSWORD not in one
    assert verify_password(PASSWORD, one) and verify_password(PASSWORD, two)
    assert not verify_password(PASSWORD + "x", one)
    assert not needs_rehash(one, n=FAST_N) and needs_rehash(one)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$16$8$1$!!$!!", "md5$1$1$1$YQ==$YQ=="])
def test_malformed_stored_hash_never_matches(stored: str) -> None:
    assert verify_password(PASSWORD, stored) is False


# ── accounts store ────────────────────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def accounts(tmp_path: Path, clock: _Clock) -> AsyncIterator[Accounts]:
    acc = Accounts(
        tmp_path / "data" / "accounts.sqlite",
        session_ttl=timedelta(hours=2),
        clock=clock,
        scrypt_n=FAST_N,
    )
    yield acc
    acc.close()


def _create(acc: Accounts, username: str = "alice", key: str = KEY_A) -> Any:
    return acc.create_user(
        username,
        PASSWORD,
        key_sha256=sha256_hex(key),
        key_name=username,
        key_fingerprint=sha256_hex(key)[:8],
    )


def test_create_user_normalises_and_validates(accounts: Accounts) -> None:
    user = _create(accounts, "  Alice ")
    assert user.username == "alice" and user.user_id.startswith("usr_")
    assert user.quota_id == user.key_name
    assert not user.is_admin and not user.disabled
    for bad in ("ab", "has space", "-leading", "x" * 33, "ünï"):
        with pytest.raises(InvalidUsername):
            _create(accounts, bad, KEY_B)
    with pytest.raises(InvalidPassword):
        accounts.create_user(
            "bob", "short", key_sha256=sha256_hex(KEY_B), key_name=None, key_fingerprint="0" * 8
        )


def test_one_account_per_username_and_per_key(accounts: Accounts) -> None:
    _create(accounts)
    with pytest.raises(UsernameTaken):
        _create(accounts, "ALICE", KEY_B)
    with pytest.raises(KeyAlreadyLinked):
        _create(accounts, "bob", KEY_A)
    bob = _create(accounts, "bob", KEY_B)
    with pytest.raises(KeyAlreadyLinked):
        accounts.link_key(
            bob.user_id, key_sha256=sha256_hex(KEY_A), key_name="alice", key_fingerprint="0" * 8
        )
    # re-linking your own key is allowed
    again = accounts.link_key(
        bob.user_id, key_sha256=sha256_hex(KEY_B), key_name="bob2", key_fingerprint="1" * 8
    )
    assert again.key_name == "bob2"


def test_authenticate_does_not_distinguish_unknown_from_wrong(accounts: Accounts) -> None:
    user = _create(accounts)
    assert accounts.authenticate("Alice", PASSWORD).user_id == user.user_id
    with pytest.raises(BadCredentials) as wrong:
        accounts.authenticate("alice", "not the password")
    with pytest.raises(BadCredentials) as unknown:
        accounts.authenticate("nobody", PASSWORD)
    assert str(wrong.value) == str(unknown.value)
    accounts.set_disabled(user.user_id, True)
    with pytest.raises(AccountDisabled):
        accounts.authenticate("alice", PASSWORD)
    with pytest.raises(UnknownUser):
        accounts.get_user("usr_missing")


def test_sessions_expire_and_can_be_revoked(accounts: Accounts, clock: _Clock) -> None:
    user = _create(accounts)
    first = accounts.create_session(user.user_id)
    second = accounts.create_session(user.user_id)
    assert first.token != second.token and first.token.startswith("qrs_")
    assert accounts.user_for_session(first.token) == user
    assert accounts.user_for_session("qrs_" + "x" * 40) is None
    assert accounts.user_for_session(KEY_A) is None, "a qrk_ key is never a session"

    accounts.revoke_session(first.token)
    assert accounts.user_for_session(first.token) is None
    assert accounts.user_for_session(second.token) == user

    clock.now += timedelta(hours=2, seconds=1)
    assert accounts.user_for_session(second.token) is None
    assert accounts.session_count(user.user_id) == 0


def test_disable_and_password_reset_sign_the_user_out(accounts: Accounts) -> None:
    user = _create(accounts)
    token = accounts.create_session(user.user_id).token
    accounts.set_password(user.user_id, "a brand new password")
    assert accounts.user_for_session(token) is None
    with pytest.raises(BadCredentials):
        accounts.authenticate("alice", PASSWORD)
    accounts.authenticate("alice", "a brand new password")

    token = accounts.create_session(user.user_id).token
    accounts.set_disabled(user.user_id, True)
    assert accounts.user_for_session(token) is None
    accounts.set_disabled(user.user_id, False)
    assert accounts.user_for_session(token) is None, "re-enabling does not restore sessions"


def test_login_upgrades_a_hash_below_the_current_cost(tmp_path: Path) -> None:
    path = tmp_path / "accounts.sqlite"
    weak = Accounts(path, scrypt_n=2**3)
    user = _create(weak)
    weak.close()
    strong = Accounts(path, scrypt_n=FAST_N)
    strong.authenticate("alice", PASSWORD)
    row = strong._one("SELECT password_hash FROM users WHERE user_id = ?", (user.user_id,))
    assert row is not None and not needs_rehash(row["password_hash"], n=FAST_N)
    strong.authenticate("alice", PASSWORD)
    strong.close()


def test_database_holds_no_plaintext_secret(accounts: Accounts) -> None:
    user = _create(accounts)
    token = accounts.create_session(user.user_id).token
    with accounts._lock:
        accounts._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = b"".join(p.read_bytes() for p in accounts.path.parent.glob("accounts.sqlite*"))
    for secret in (PASSWORD, KEY_A, token):
        assert secret.encode() not in blob
    assert sha256_hex(KEY_A).encode() in blob and sha256_hex(token).encode() in blob


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _write_keys(path: Path, *, a_disabled: bool = False, with_a: bool = True) -> None:
    rows: list[dict[str, Any]] = [{"name": "bob", "key_sha256": sha256_hex(KEY_B)}]
    if with_a:
        row: dict[str, Any] = {"name": "alice", "key_sha256": sha256_hex(KEY_A)}
        if a_disabled:
            row["disabled"] = True
        rows.append(row)
    path.write_text(json.dumps(rows), encoding="utf-8")


def _settings(tmp_path: Path, **extra: str) -> Settings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    keys_file = tmp_path / "api-keys.json"
    _write_keys(keys_file)
    return load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys_file),
            "QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY": "1",
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
            **extra,
        }
    )


def _app(settings: Settings, *, limiter: TokenBucketLimiter | None = None) -> FastAPI:
    return create_app(
        settings,
        verifier=FileKeyVerifier(settings.keys_file),
        accounts=Accounts(Path(settings.data_dir) / "accounts.sqlite", scrypt_n=FAST_N),
        cards=load_cards(FIXTURES / "evals"),
        limiter=limiter
        if limiter is not None
        else TokenBucketLimiter(rate_per_s=1000.0, burst=1000),
        repo_root=FIXTURES,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return _settings(tmp_path)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = _app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _signup(
    client: httpx.AsyncClient, username: str = "alice", key: str = KEY_A
) -> dict[str, Any]:
    resp = await client.post(
        f"{API}/auth/signup", json={"username": username, "password": PASSWORD, "api_key": key}
    )
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


def _error(resp: httpx.Response, status: int) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    err: dict[str, Any] = resp.json()["error"]
    return err


async def test_signup_creates_an_account_and_a_session(client: httpx.AsyncClient) -> None:
    data = await _signup(client)
    assert set(data) == {"user", "session_token", "expires_at"}
    assert data["session_token"].startswith("qrs_")
    user = data["user"]
    assert user["username"] == "alice" and user["key_name"] == "alice"
    assert user["key_fingerprint"] == sha256_hex(KEY_A)[:8] and user["is_admin"] is False
    text = json.dumps(data)
    assert KEY_A not in text and PASSWORD not in text and sha256_hex(KEY_A) not in text

    me = await client.get(f"{API}/me", headers=_bearer(data["session_token"]))
    assert me.status_code == 200 and me.json() == user


async def test_signup_needs_a_working_key(client: httpx.AsyncClient) -> None:
    body = {"username": "mallory", "password": PASSWORD, "api_key": KEY_UNKNOWN}
    err = _error(await client.post(f"{API}/auth/signup", json=body), 403)
    assert err["param"] == "api_key" and KEY_UNKNOWN not in err["message"]
    # nothing was created: the name is still free
    await _signup(client, "mallory", KEY_B)


async def test_signup_conflicts_and_validation(client: httpx.AsyncClient) -> None:
    await _signup(client)
    taken = {"username": "Alice", "password": PASSWORD, "api_key": KEY_B}
    assert _error(await client.post(f"{API}/auth/signup", json=taken), 409)["param"] == "username"
    same_key = {"username": "carol", "password": PASSWORD, "api_key": KEY_A}
    assert _error(await client.post(f"{API}/auth/signup", json=same_key), 409)["param"] == "api_key"
    weak = {"username": "carol", "password": "short", "api_key": KEY_B}
    err = _error(await client.post(f"{API}/auth/signup", json=weak), 400)
    assert err["param"] == "password" and "short" not in err["message"]
    bad_name = {"username": "has space", "password": PASSWORD, "api_key": KEY_B}
    assert (
        _error(await client.post(f"{API}/auth/signup", json=bad_name), 400)["param"] == "username"
    )


async def test_signup_can_be_closed(tmp_path: Path) -> None:
    app = _app(_settings(tmp_path, QR_EVALS_SIGNUP_ENABLED="0"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        body = {"username": "alice", "password": PASSWORD, "api_key": KEY_A}
        _error(await c.post(f"{API}/auth/signup", json=body), 403)


async def test_login_logout_cycle(client: httpx.AsyncClient) -> None:
    first = (await _signup(client))["session_token"]
    resp = await client.post(f"{API}/auth/login", json={"username": "ALICE", "password": PASSWORD})
    assert resp.status_code == 200
    second = resp.json()["session_token"]
    assert second != first

    wrong = await client.post(f"{API}/auth/login", json={"username": "alice", "password": "nope"})
    unknown = await client.post(f"{API}/auth/login", json={"username": "ghost", "password": "nope"})
    assert _error(wrong, 401)["message"] == _error(unknown, 401)["message"]

    assert (await client.post(f"{API}/auth/logout", headers=_bearer(second))).json() == {"ok": True}
    _error(await client.get(f"{API}/me", headers=_bearer(second)), 401)
    assert (await client.get(f"{API}/me", headers=_bearer(first))).status_code == 200


async def test_account_routes_need_a_session_not_a_key(client: httpx.AsyncClient) -> None:
    await _signup(client)
    missing = await client.get(f"{API}/me")
    _error(missing, 401)
    assert missing.headers["WWW-Authenticate"].startswith("Bearer")
    _error(await client.get(f"{API}/me", headers=_bearer(KEY_A)), 401)
    _error(await client.get(f"{API}/me", headers=_bearer("qrs_" + "x" * 43)), 401)
    _error(await client.post(f"{API}/auth/logout"), 401)


async def test_link_a_different_key(client: httpx.AsyncClient) -> None:
    alice = (await _signup(client))["session_token"]
    refused = await client.post(
        f"{API}/me/key", json={"api_key": KEY_UNKNOWN}, headers=_bearer(alice)
    )
    _error(refused, 403)
    linked = await client.post(f"{API}/me/key", json={"api_key": KEY_B}, headers=_bearer(alice))
    assert linked.status_code == 200
    assert linked.json()["key_name"] == "bob"
    assert linked.json()["key_fingerprint"] == sha256_hex(KEY_B)[:8]
    # KEY_A is free again, KEY_B is now taken
    carol = (await _signup(client, "carol", KEY_A))["session_token"]
    taken = await client.post(f"{API}/me/key", json={"api_key": KEY_B}, headers=_bearer(carol))
    _error(taken, 409)


async def test_session_launch_records_the_owner(client: httpx.AsyncClient) -> None:
    data = await _signup(client)
    body = {"config": _study_dict(), "kind": "pilot"}
    resp = await client.post(f"{API}/runs", json=body, headers=_bearer(data["session_token"]))
    assert resp.status_code == 201, resp.text
    run = (await client.get(f"{API}/runs/{resp.json()['run_id']}")).json()["run"]
    assert run["owner_id"] == data["user"]["user_id"] and run["key_name"] == "alice"
    # the per-key cap is the per-account cap: a second active run is refused
    again = await client.post(f"{API}/runs", json=body, headers=_bearer(data["session_token"]))
    _error(again, 429)


async def test_bare_key_launch_is_refused_unless_the_operator_allows_it(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    body = {"config": _study_dict(), "kind": "pilot"}
    refused = await client.post(f"{API}/runs", json=body, headers=_bearer(KEY_B))
    assert "sign in" in _error(refused, 401)["message"]
    _error(await client.post(f"{API}/readiness", json=body, headers=_bearer(KEY_B)), 401)
    _error(await client.post(f"{API}/runs", json=body), 401)

    app = _app(_settings(tmp_path / "operator", QR_EVALS_ALLOW_KEY_LAUNCH="1"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        resp = await c.post(f"{API}/runs", json=body, headers=_bearer(KEY_B))
        assert resp.status_code == 201, resp.text
        run = (await c.get(f"{API}/runs/{resp.json()['run_id']}")).json()["run"]
        assert run["owner_id"] is None and run["key_name"] == "bob"


@pytest.mark.parametrize(("change", "status"), [("disabled", 403), ("removed", 403)])
async def test_launch_is_refused_once_the_linked_key_stops_working(
    client: httpx.AsyncClient, settings: Settings, change: str, status: int
) -> None:
    token = (await _signup(client))["session_token"]
    _write_keys(
        Path(settings.keys_file), a_disabled=change == "disabled", with_a=change != "removed"
    )
    body = {"config": _study_dict(), "kind": "pilot"}
    err = _error(await client.post(f"{API}/runs", json=body, headers=_bearer(token)), status)
    assert "link a working key" in err["message"]
    # the account still works: the user can sign in and link another key
    assert (await client.get(f"{API}/me", headers=_bearer(token))).status_code == 200
    linked = await client.post(f"{API}/me/key", json={"api_key": KEY_B}, headers=_bearer(token))
    assert linked.status_code == 200
    assert (await client.post(f"{API}/runs", json=body, headers=_bearer(token))).status_code == 201


async def test_expired_session_cannot_launch(client: httpx.AsyncClient) -> None:
    body = {"config": _study_dict(), "kind": "pilot"}
    resp = await client.post(f"{API}/runs", json=body, headers=_bearer("qrs_" + "y" * 43))
    _error(resp, 401)


def test_probe_mode_trusts_the_key_verified_at_link_time(
    accounts: Accounts, settings: Settings
) -> None:
    user = _create(accounts)
    state = AppState.__new__(AppState)
    state.verifier = ProbeKeyVerifier("http://127.0.0.1:1")
    assert recheck_linked_key(state, user) == user.quota_id


async def test_login_is_rate_limited_per_address(tmp_path: Path) -> None:
    app = _app(_settings(tmp_path), limiter=TokenBucketLimiter(rate_per_s=0.001, burst=2))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        body = {"username": "ghost", "password": "whatever"}
        for _ in range(2):
            _error(await c.post(f"{API}/auth/login", json=body), 401)
        limited = await c.post(f"{API}/auth/login", json=body)
        _error(limited, 429)
        assert "Retry-After" in limited.headers


async def test_auth_logs_carry_no_secret(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.handler.addFilter(SecretFilter())
    with caplog.at_level(logging.INFO):
        token = (await _signup(client))["session_token"]
        await client.post(f"{API}/auth/login", json={"username": "alice", "password": PASSWORD})
        await client.post(f"{API}/auth/logout", headers=_bearer(token))
    assert "auth.signup" in {getattr(r, "event", None) for r in caplog.records}
    dump = "\n".join(str(r.__dict__) for r in caplog.records) + caplog.text
    for secret in (PASSWORD, KEY_A, token, sha256_hex(KEY_A)):
        assert secret not in dump


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_manages_accounts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "accounts.sqlite"
    acc = Accounts(db, scrypt_n=FAST_N)
    user = _create(acc)
    token = acc.create_session(user.user_id).token
    acc.close()

    def cli(*argv: str, answers: tuple[str, ...] = ()) -> int:
        replies = iter(answers)
        return accounts_cli.run(["--db", str(db), *argv], prompt=lambda _: next(replies))

    assert cli("list") == 0
    out = capsys.readouterr().out
    assert "alice" in out and "1 account(s)" in out and sha256_hex(KEY_A) not in out

    assert cli("make-admin", "alice") == 0
    assert cli("disable", "alice") == 0
    assert cli("list") == 0
    assert "admin,disabled" in capsys.readouterr().out

    check = Accounts(db, scrypt_n=FAST_N)
    assert check.user_for_session(token) is None
    check.close()

    assert cli("enable", "alice") == 0 and cli("revoke-admin", "alice") == 0
    assert cli("reset-password", "alice", answers=("one password here", "another one here")) == 1
    assert "do not match" in capsys.readouterr().err
    assert cli("reset-password", "alice", answers=("a new long password",) * 2) == 0
    assert cli("disable", "ghost") == 1

    check = Accounts(db)
    assert check.authenticate("alice", "a new long password").is_admin is False
    check.close()
