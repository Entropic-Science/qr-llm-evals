"""Token usage ledger, quotas, and the usage / admin routes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from qr_llm_evals.accounts import Accounts, Prices, UnknownUser, UsageEvent, UsageLedger
from qr_llm_evals.accounts import cli as accounts_cli
from qr_llm_evals.accounts import service as accounts_service
from qr_llm_evals.accounts.usage import MICRO
from qr_llm_evals.api.usage import UsageRecorder
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.keys.verify import sha256_hex
from qr_llm_evals.store import Store
from qr_llm_evals.store.schema import JudgeRow, Usage
from qr_llm_evals.store.store import UsageNote
from stub_engine import StubEngine
from test_api import API, Harness, _study_dict, _wait_state

KEY_A = "qrk_" + "alice_key_0123456789abcdef"
KEY_B = "qrk_" + "bob_key_0123456789abcdefgh"
ANTHROPIC = "sk-ant-" + "api03-usage-secret-0123456789"
PASSWORD = "correct horse battery"
FAST_N = 2**4
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SAMPLES = 2 * 2 * 2 * 3  # 2 arms × 2 items × k 2 × 3 told cells (the stub_free pilot)


# ── ledger ────────────────────────────────────────────────────────────────────


@pytest.fixture
def accounts(tmp_path: Path) -> Iterator[Accounts]:
    acc = Accounts(tmp_path / "accounts.sqlite", scrypt_n=FAST_N)
    yield acc
    acc.close()


def _user(acc: Accounts, name: str = "alice", key: str = KEY_A) -> Any:
    return acc.create_user(
        name,
        PASSWORD,
        key_sha256=sha256_hex(key),
        key_name=name,
        key_fingerprint=sha256_hex(key)[:8],
    )


def _event(user_id: str, **over: Any) -> UsageEvent:
    base: dict[str, Any] = {
        "user_id": user_id,
        "run_id": "run_1",
        "kind": "generation",
        "model": "qr-llm",
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "reasoning_tokens": 5,
        "arm_id": "qrng",
        "card_id": "stub_free",
        "source_label": "quantum",
        "charged": True,
        "at": T0,
    }
    base.update(over)
    return UsageEvent(**base)


def test_ledger_totals_and_groupings(accounts: Accounts) -> None:
    alice, bob = _user(accounts), _user(accounts, "bob", KEY_B)
    ledger = UsageLedger(accounts, default_quota=1000)
    assert ledger.totals(alice.user_id).calls == 0
    assert ledger.summary(alice.user_id).by_day == []

    ledger.record(_event(alice.user_id))
    ledger.record(_event(alice.user_id, source_label="seeded_prng", arm_id="prng"))
    ledger.record(
        _event(alice.user_id, kind="judge", model="claude", charged=False, source_label=None)
    )
    ledger.record(
        _event(
            alice.user_id,
            run_id="run_2",
            kind="narrative",
            charged=False,
            source_label=None,
            at=T0 + timedelta(days=1),
        )
    )
    ledger.record(_event(bob.user_id, prompt_tokens=7, completion_tokens=3))

    totals = ledger.totals(alice.user_id)
    assert totals.calls == 4
    assert (totals.qrng_input, totals.qrng_output) == (100, 40), "the quantum arm"
    assert (totals.prng_input, totals.prng_output) == (100, 40), "the seeded arm"
    assert (totals.own_key_input, totals.own_key_output) == (200, 80), "judge and narrative"
    assert totals.charged_tokens == 280, "only the two qr-server generations are charged"
    # 100 x $20 + 40 x $80 + 100 x $5 + 40 x $20 per million, in micro-dollars
    assert totals.cost_microusd(ledger.prices) == 2000 + 3200 + 500 + 800

    summary = ledger.summary(alice.user_id)
    assert {b.key: b.totals.calls for b in summary.by_kind} == {
        "generation": 2,
        "judge": 1,
        "narrative": 1,
    }
    assert {b.key: b.totals.calls for b in summary.by_source} == {"quantum": 1, "seeded_prng": 1}
    assert [b.key for b in summary.by_day] == ["2026-11-19", "2026-11-18"]
    assert [b.key for b in summary.by_run] == ["run_2", "run_1"]

    by_user = ledger.totals_by_user()
    assert by_user[bob.user_id].charged_tokens == 10 and by_user[alice.user_id].calls == 4


def test_quota_default_override_and_unlimited(accounts: Accounts) -> None:
    alice = _user(accounts)
    ledger = UsageLedger(accounts, default_quota=200)
    ledger.record(_event(alice.user_id))  # 140 charged
    quota = ledger.quota(alice)
    assert (quota.limit, quota.used, quota.remaining, quota.exhausted) == (200, 140, 60, False)

    ledger.record(_event(alice.user_id))  # 280 charged
    quota = ledger.quota(alice)
    assert quota.exhausted and quota.remaining == 0, "remaining never goes negative"

    raised = accounts.set_quota(alice.user_id, 1000)
    assert ledger.quota(raised).remaining == 720
    unlimited = accounts.set_quota(alice.user_id, 0)
    quota = ledger.quota(unlimited)
    assert quota.unlimited and quota.remaining is None and not quota.exhausted
    back = accounts.set_quota(alice.user_id, None)
    assert back.token_quota is None and ledger.quota(back).limit == 200
    with pytest.raises(accounts_service.AccountError):
        accounts.set_quota(alice.user_id, -1)


def test_ledger_never_stores_negative_counts(accounts: Accounts) -> None:
    alice = _user(accounts)
    ledger = UsageLedger(accounts)
    ledger.record(_event(alice.user_id, prompt_tokens=-5, completion_tokens=-1))
    totals = ledger.totals(alice.user_id)
    assert totals.charged_tokens == 0 and totals.cost_microusd(ledger.prices) == 0


def test_engine_calls_without_a_quantum_label_are_priced_as_prng(accounts: Accounts) -> None:
    alice = _user(accounts)
    ledger = UsageLedger(accounts)
    # an on-engine judge call carries no source label; it is not QRNG-sampled
    ledger.record(_event(alice.user_id, kind="judge", source_label=None))
    ledger.record(_event(alice.user_id, source_label="seeded_prng_biased"))
    totals = ledger.totals(alice.user_id)
    assert (totals.qrng_input, totals.qrng_output) == (0, 0)
    assert (totals.prng_input, totals.prng_output) == (200, 80)


def test_settling_zeroes_the_balance_and_keeps_the_history(accounts: Accounts) -> None:
    alice, bob = _user(accounts), _user(accounts, "bob", KEY_B)
    ledger = UsageLedger(accounts, prices=Prices(qrng_input=20, qrng_output=80))
    ledger.record(_event(alice.user_id, prompt_tokens=1_000_000, completion_tokens=500_000))
    assert ledger.outstanding(alice.user_id) == 60 * MICRO  # $20 + $40

    paid = ledger.settle(alice.user_id, settled_by=bob.user_id, at=T0, note="invoice 7")
    assert paid.amount_microusd == 60 * MICRO
    assert ledger.outstanding(alice.user_id) == 0
    assert ledger.totals(alice.user_id).calls == 1, "usage rows are untouched"

    # later usage is owed again; settling twice in a row records a zero
    ledger.record(_event(alice.user_id, prompt_tokens=1_000_000, completion_tokens=0))
    assert ledger.outstanding(alice.user_id) == 20 * MICRO
    ledger.settle(alice.user_id, settled_by=bob.user_id, at=T0 + timedelta(days=1))
    again = ledger.settle(alice.user_id, settled_by=bob.user_id, at=T0 + timedelta(days=2))
    assert again.amount_microusd == 0 and ledger.outstanding(alice.user_id) == 0

    history = ledger.settlements(alice.user_id)
    assert [h.amount_microusd for h in history] == [0, 20 * MICRO, 60 * MICRO]
    assert history[-1].note == "invoice 7" and history[-1].settled_by == bob.user_id
    assert ledger.settled_by_user() == {alice.user_id: 80 * MICRO}
    assert ledger.outstanding(bob.user_id) == 0
    with pytest.raises(UnknownUser):
        ledger.settle("usr_nope", settled_by=bob.user_id, at=T0)


def test_version_1_database_migrates_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "accounts.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(accounts_service, "MIGRATIONS", accounts_service.MIGRATIONS[:1])
        old = Accounts(path, scrypt_n=FAST_N)
        # a row as version 1 wrote it (the current code reads the version 2 shape)
        old._conn.execute(
            "INSERT INTO users (user_id, username, password_hash, key_sha256, "
            "key_fingerprint, created_at, key_linked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("usr_old", "alice", "x", sha256_hex(KEY_A), "0" * 8, T0.isoformat(), T0.isoformat()),
        )
        old.close()
    new = Accounts(path, scrypt_n=FAST_N)
    kept = new.get_user("usr_old")
    assert kept.username == "alice" and kept.token_quota is None
    UsageLedger(new).record(_event("usr_old"))
    assert UsageLedger(new).totals("usr_old").calls == 1
    new.close()
    Accounts(path, scrypt_n=FAST_N).close()  # re-opening applies nothing twice


def test_cli_set_quota(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "accounts.sqlite"
    acc = Accounts(db, scrypt_n=FAST_N)
    alice = _user(acc)
    acc.close()
    assert accounts_cli.run(["--db", str(db), "set-quota", "alice", "5000"]) == 0
    assert accounts_cli.run(["--db", str(db), "set-quota", "alice", "lots"]) == 1
    check = Accounts(db)
    assert check.get_user(alice.user_id).token_quota == 5000
    check.close()
    assert accounts_cli.run(["--db", str(db), "set-quota", "alice", "default"]) == 0
    check = Accounts(db)
    assert check.get_user(alice.user_id).token_quota is None
    check.close()
    capsys.readouterr()


def test_cli_balance_and_settle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "accounts.sqlite"
    acc = Accounts(db, scrypt_n=FAST_N)
    alice = _user(acc)
    UsageLedger(acc).record(_event(alice.user_id, prompt_tokens=1_000_000, completion_tokens=0))
    acc.close()
    assert accounts_cli.run(["--db", str(db), "balance", "alice"]) == 0
    assert "alice: $20.00" in capsys.readouterr().out
    assert accounts_cli.run(["--db", str(db), "settle", "alice", "--note", "cash"]) == 0
    assert "paid $20.00" in capsys.readouterr().out
    assert accounts_cli.run(["--db", str(db), "balance", "alice"]) == 0
    assert "alice: $0.00" in capsys.readouterr().out
    assert accounts_cli.run(["--db", str(db), "settle", "nobody"]) == 1
    capsys.readouterr()


# ── store hook ────────────────────────────────────────────────────────────────


def test_a_failing_usage_hook_never_loses_the_row(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    seen: list[UsageNote] = []

    def hook(note: UsageNote) -> None:
        seen.append(note)
        raise RuntimeError("ledger is down")

    store.usage_hook = hook
    note = UsageNote(
        run_id="run_x",
        kind="judge",
        arm_id="a",
        card_id="c",
        model="m",
        source_label=None,
        pipeline_pinned=False,
        usage=Usage(prompt_tokens=1, completion_tokens=2),
        at=T0,
    )
    store.notify_usage(note)  # must not raise
    assert seen == [note]
    store.close()


def test_judge_row_usage_defaults_for_rows_written_before_it_existed() -> None:
    old = {
        "sample_ref": "s",
        "judge_id": "study_judge",
        "model": "m",
        "rubric_hash": "0" * 64,
        "score": 1.0,
        "raw": "1",
        "at": T0.isoformat(),
    }
    assert JudgeRow.model_validate(old).usage == Usage()


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _settings(tmp_path: Path, **extra: str) -> Settings:
    import json

    keys_file = tmp_path / "api-keys.json"
    keys_file.write_text(
        json.dumps(
            [
                {"name": "alice", "key_sha256": sha256_hex(KEY_A)},
                {"name": "bob", "key_sha256": sha256_hex(KEY_B)},
            ]
        ),
        encoding="utf-8",
    )
    return load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys_file),
            "QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY": "5",
            "QR_EVALS_MAX_INFLIGHT": "2",
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
            **extra,
        }
    )


class Site:
    """A running app, a client, and helpers for signed-in users."""

    def __init__(self, harness: Harness, client: httpx.AsyncClient) -> None:
        self.h = harness
        self.c = client

    async def signup(self, name: str = "alice", key: str = KEY_A) -> tuple[dict[str, str], str]:
        resp = await self.c.post(
            f"{API}/auth/signup", json={"username": name, "password": PASSWORD, "api_key": key}
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        return {"Authorization": f"Bearer {data['session_token']}"}, data["user"]["user_id"]

    async def launch(self, auth: dict[str, str], **body: Any) -> httpx.Response:
        payload = {
            "config": _study_dict(),
            "kind": "pilot",
            "provider_keys": {"anthropic": ANTHROPIC},
            **body,
        }
        return await self.c.post(f"{API}/runs", json=payload, headers=auth)

    async def control(self, launched: dict[str, Any], action: str) -> httpx.Response:
        return await self.c.post(
            f"{API}/runs/{launched['run_id']}/control",
            json={"action": action},
            headers={"X-Host-Token": launched["host_token"]},
        )

    async def run_to(self, launched: dict[str, Any], states: set[str]) -> dict[str, Any]:
        started = await self.control(launched, "start")
        assert started.status_code == 200, started.text
        return await _wait_state(self.c, launched["run_id"], states)

    async def usage(self, auth: dict[str, str]) -> dict[str, Any]:
        resp = await self.c.get(f"{API}/me/usage", headers=auth)
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data


@asynccontextmanager
async def _site(settings: Settings, engine: StubEngine | None = None) -> AsyncIterator[Site]:
    h = Harness(settings, engine=engine)
    async with (
        h.app.router.lifespan_context(h.app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url="http://test") as c,
    ):
        yield Site(h, c)


@pytest.fixture
async def site(tmp_path: Path) -> AsyncIterator[Site]:
    async with _site(_settings(tmp_path)) as s:
        yield s


def _kinds(usage: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {b["key"]: b["totals"] for b in usage["by_kind"]}


async def test_a_run_is_metered_to_its_owner(site: Site) -> None:
    alice, alice_id = await site.signup()
    bob, _ = await site.signup("bob", KEY_B)
    empty = await site.usage(alice)
    assert empty["totals"]["calls"] == 0 and empty["quota"]["used"] == 0
    assert empty["quota"]["limit"] == 2_000_000 and empty["quota"]["exhausted"] is False

    launched = (await site.launch(alice)).json()
    await site.run_to(launched, {"complete"})

    usage = await site.usage(alice)
    gen = _kinds(usage)["generation"]
    assert gen["calls"] == SAMPLES
    assert all(gen[k] > 0 for k in ("qrng_input", "qrng_output", "prng_input", "prng_output"))
    assert gen["own_key_tokens"] == 0
    charged = gen["qrng_input"] + gen["qrng_output"] + gen["prng_input"] + gen["prng_output"]
    assert usage["quota"]["used"] == charged
    assert usage["quota"]["remaining"] == 2_000_000 - charged
    expected_cost = (
        gen["qrng_input"] * 20 + gen["qrng_output"] * 80 + gen["prng_input"] * 5
    ) + gen["prng_output"] * 20
    assert gen["cost_microusd"] == expected_cost
    billing = usage["billing"]
    assert billing["outstanding_microusd"] == usage["totals"]["cost_microusd"] >= expected_cost
    assert billing["paid_microusd"] == 0 and billing["settlements"] == []
    assert billing["prices"] == {
        "qrng_input": 20.0,
        "qrng_output": 80.0,
        "prng_input": 5.0,
        "prng_output": 20.0,
    }

    # both arms run on the qr-server engine, one quantum and one seeded
    sources = {b["key"]: b["totals"]["calls"] for b in usage["by_source"]}
    assert sum(sources.values()) == SAMPLES and "quantum" in sources and len(sources) == 2
    assert [b["key"] for b in usage["by_run"]] == [launched["run_id"]]
    assert len(usage["by_day"]) == 1

    # the disk rows and the ledger agree
    rows = [
        row
        for arm in ("qrng", "prng_seed_7")
        for row in site.h.store.find_run(launched["run_id"]).generations(arm, "stub_free")  # type: ignore[union-attr]
    ]
    assert sum(r.usage.prompt_tokens for r in rows) == gen["qrng_input"] + gen["prng_input"]
    assert sum(r.usage.completion_tokens for r in rows) == gen["qrng_output"] + gen["prng_output"]

    # the run's files are attributed to its owner
    storage = (await site.c.get(f"{API}/me/storage", headers=alice)).json()
    assert [r["run_id"] for r in storage["runs"]] == [launched["run_id"]]
    assert storage["total_bytes"] == storage["runs"][0]["size_bytes"] > 0
    assert (await site.c.get(f"{API}/me/storage", headers=bob)).json() == {
        "total_bytes": 0,
        "runs": [],
    }
    assert (await site.c.get(f"{API}/me/storage")).status_code == 401

    # nobody else is charged for it
    assert (await site.usage(bob))["totals"]["calls"] == 0
    assert site.h.state.usage.owner_of(launched["run_id"]) == alice_id


async def test_judge_tokens_are_recorded_but_not_charged(site: Site) -> None:
    alice, _ = await site.signup()
    launched = (await site.launch(alice)).json()
    await site.run_to(launched, {"complete"})
    kinds = _kinds(await site.usage(alice))
    if "judge" not in kinds:
        pytest.skip("the stub_free pilot has no LLM-judged card")
    judge = kinds["judge"]
    assert judge["calls"] > 0 and judge["cost_microusd"] == 0
    assert judge["own_key_tokens"] == 15 * judge["calls"]


async def test_operator_key_runs_are_not_metered(tmp_path: Path) -> None:
    async with _site(_settings(tmp_path, QR_EVALS_ALLOW_KEY_LAUNCH="1")) as site:
        alice, _ = await site.signup()
        resp = await site.launch({"Authorization": f"Bearer {KEY_B}"})
        assert resp.status_code == 201, resp.text
        await site.run_to(resp.json(), {"complete"})
        assert site.h.state.ledger.totals_by_user() == {}
        assert (await site.usage(alice))["totals"]["calls"] == 0


async def test_quota_pauses_the_run_and_blocks_resume_until_raised(tmp_path: Path) -> None:
    settings = _settings(tmp_path, QR_EVALS_DEFAULT_TOKEN_QUOTA="1")
    async with _site(settings, StubEngine(chunk_delay_s=0.005)) as site:
        alice, alice_id = await site.signup()
        launched = (await site.launch(alice)).json()  # 0 of 1 used: allowed
        view = await site.run_to(launched, {"paused", "complete"})
        assert view["run"]["state"] == "paused", "the first charged sample spends the budget"

        usage = await site.usage(alice)
        assert usage["quota"]["exhausted"] is True and usage["quota"]["remaining"] == 0
        done_at_pause = _kinds(usage)["generation"]["calls"]
        assert 0 < done_at_pause < SAMPLES

        refused = await site.control(launched, "resume")
        assert refused.status_code == 403 and "quota" in refused.json()["error"]["message"]
        again = await site.launch(alice)
        assert again.status_code == 403 and "quota" in again.json()["error"]["message"]

        site.h.state.accounts.set_quota(alice_id, 0)  # unlimited
        resumed = await site.control(launched, "resume")
        assert resumed.status_code == 200, resumed.text
        await _wait_state(site.c, launched["run_id"], {"complete"})
        assert _kinds(await site.usage(alice))["generation"]["calls"] == SAMPLES


async def test_full_run_is_refused_when_the_estimate_exceeds_the_budget(tmp_path: Path) -> None:
    async with _site(_settings(tmp_path, QR_EVALS_DEFAULT_TOKEN_QUOTA="50")) as site:
        alice, alice_id = await site.signup()
        refused = await site.launch(alice, kind="full")
        assert refused.status_code == 403
        message = refused.json()["error"]["message"]
        assert "estimated" in message and "50" in message
        # a pilot only needs a budget that is not already spent
        assert (await site.launch(alice, kind="pilot")).status_code == 201
        site.h.state.accounts.set_quota(alice_id, 0)
        other = _study_dict(title="Another study")
        assert (await site.launch(alice, kind="full", config=other)).status_code == 201


async def test_admin_routes(site: Site) -> None:
    alice, alice_id = await site.signup()
    bob, bob_id = await site.signup("bob", KEY_B)
    assert (await site.c.get(f"{API}/admin/users")).status_code == 401
    assert (await site.c.get(f"{API}/admin/users", headers=bob)).status_code == 403
    site.h.state.accounts.set_admin(alice_id, True)

    launched = (await site.launch(bob)).json()
    await site.run_to(launched, {"complete"})

    listing = (await site.c.get(f"{API}/admin/users", headers=alice)).json()
    assert listing["default_token_quota"] == 2_000_000
    by_name = {u["user"]["username"]: u for u in listing["users"]}
    assert set(by_name) == {"alice", "bob"}
    assert by_name["alice"]["user"]["is_admin"] is True and by_name["alice"]["totals"]["calls"] == 0
    assert by_name["bob"]["totals"]["calls"] >= SAMPLES
    bob_totals = by_name["bob"]["totals"]
    assert by_name["bob"]["quota"]["used"] == sum(
        bob_totals[k] for k in ("qrng_input", "qrng_output", "prng_input", "prng_output")
    )
    assert by_name["bob"]["outstanding_microusd"] == bob_totals["cost_microusd"] > 0
    assert by_name["bob"]["storage_bytes"] > 0 and by_name["alice"]["storage_bytes"] == 0
    assert by_name["alice"]["outstanding_microusd"] == 0
    assert listing["prices"]["qrng_output"] == 80.0
    assert 0 < listing["disk_free_bytes"] <= listing["disk_total_bytes"]

    # marking bob as paid zeroes what he owes and leaves his usage alone
    settle = f"{API}/admin/users/{bob_id}/settle"
    assert (await site.c.post(settle, json={}, headers=bob)).status_code == 403
    paid = await site.c.post(settle, json={"note": "bank transfer"}, headers=alice)
    assert paid.status_code == 200, paid.text
    assert paid.json()["outstanding_microusd"] == 0
    assert paid.json()["totals"] == bob_totals
    bob_billing = (await site.usage(bob))["billing"]
    assert bob_billing["outstanding_microusd"] == 0
    assert bob_billing["paid_microusd"] == bob_totals["cost_microusd"]
    assert [s["note"] for s in bob_billing["settlements"]] == ["bank transfer"]
    gone = await site.c.post(f"{API}/admin/users/usr_nope/settle", json={}, headers=alice)
    assert gone.status_code == 404
    assert by_name["bob"]["token_quota"] is None and by_name["bob"]["disabled"] is False

    url = f"{API}/admin/users/{bob_id}"
    capped = await site.c.post(url, json={"token_quota": 10}, headers=alice)
    assert capped.status_code == 200
    assert capped.json()["token_quota"] == 10 and capped.json()["quota"]["exhausted"] is True
    assert (await site.launch(bob)).status_code == 403

    # an empty body changes nothing; null puts the quota back on the default
    assert (await site.c.post(url, json={}, headers=alice)).json()["token_quota"] == 10
    reset = await site.c.post(url, json={"token_quota": None}, headers=alice)
    assert reset.json()["token_quota"] is None and reset.json()["quota"]["limit"] == 2_000_000

    disabled = await site.c.post(url, json={"disabled": True}, headers=alice)
    assert disabled.json()["disabled"] is True
    assert (await site.c.get(f"{API}/me", headers=bob)).status_code == 401
    promoted = await site.c.post(url, json={"disabled": False, "is_admin": True}, headers=alice)
    assert promoted.json()["user"]["is_admin"] is True and promoted.json()["disabled"] is False

    me = f"{API}/admin/users/{alice_id}"
    assert (await site.c.post(me, json={"is_admin": False}, headers=alice)).status_code == 409
    assert (await site.c.post(me, json={"disabled": True}, headers=alice)).status_code == 409
    assert (await site.c.post(me, json={"token_quota": 0}, headers=alice)).status_code == 200
    missing = await site.c.post(f"{API}/admin/users/usr_nope", json={}, headers=alice)
    assert missing.status_code == 404
    negative = await site.c.post(url, json={"token_quota": -1}, headers=alice)
    assert negative.status_code == 400


async def test_narrative_tokens_are_recorded_uncharged(site: Site) -> None:
    alice, _ = await site.signup()
    launched = (await site.launch(alice)).json()
    recorder: UsageRecorder = site.h.state.usage
    recorder.record_narrative(
        launched["run_id"], "claude-x", {"prompt_tokens": 900, "completion_tokens": 300}
    )
    recorder.record_narrative(launched["run_id"], "claude-x", None)
    recorder.record_narrative("run_unknown", "claude-x", {"prompt_tokens": 1})
    # an owner the accounts database does not know must not raise into the analysis
    recorder._owners["run_orphan"] = "usr_gone"
    recorder.record_narrative("run_orphan", "claude-x", {"prompt_tokens": 1})
    narrative = _kinds(await site.usage(alice))["narrative"]
    assert narrative["calls"] == 2 and narrative["cost_microusd"] == 0
    assert narrative["own_key_tokens"] == 1200


# ── review fixes ──────────────────────────────────────────────────────────────

QR_MODEL = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}


def _custom(base_url: str) -> dict[str, str]:
    return {
        "id": f"custom:{base_url}#some-model",
        "family": "external",
        "provider": "custom",
        "served_name": "some-model",
    }


def _external_study(model: dict[str, str], **over: Any) -> dict[str, Any]:
    """A study whose only arm runs on an external provider (nothing charged)."""
    arm = {"id": "ext", "model": model["id"], "sampling": "t1_minp", "source": "provider"}
    estimates = {"tps_by_model": {"qr-llm": 400.0, model["id"]: 50.0}}
    return _study_dict(
        models=[QR_MODEL, model], arms=[arm], title="External only", estimates=estimates, **over
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://stub",  # the engine origin in these tests
        "http://127.0.0.1:8000/v1",
        "https://127.0.0.1:8000/v1",
        "https://localhost/v1",
        "https://engine.internal/v1",
        "https://10.0.0.5/v1",
        "https://[::1]/v1",
        "http://llm.example.org/v1",  # public, but not https
    ],
)
async def test_custom_models_cannot_point_at_the_engine(site: Site, base_url: str) -> None:
    alice, _ = await site.signup()
    refused = await site.launch(alice, config=_external_study(_custom(base_url)))
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["param"] == "config.models"
    as_narrative = await site.launch(alice, config=_study_dict(narrative_model=_custom(base_url)))
    assert as_narrative.status_code == 400, as_narrative.text


async def test_spent_quota_does_not_block_a_study_that_costs_nothing(tmp_path: Path) -> None:
    async with _site(_settings(tmp_path, QR_EVALS_DEFAULT_TOKEN_QUOTA="10")) as site:
        alice, alice_id = await site.signup()
        site.h.state.ledger.record(_event(alice_id))  # 140 charged tokens: over the quota
        assert (await site.usage(alice))["quota"]["exhausted"] is True

        charged = await site.launch(alice)
        assert charged.status_code == 403 and "quota" in charged.json()["error"]["message"]
        readiness = await site.c.post(
            f"{API}/readiness", json={"config": _study_dict()}, headers=alice
        )
        assert readiness.status_code == 403 and "quota" in readiness.json()["error"]["message"]

        external = _external_study(_custom("https://llm.example.org/v1"))
        free = await site.launch(alice, config=external)
        assert free.status_code == 201, free.text
        view = (await site.c.get(f"{API}/runs/{free.json()['run_id']}")).json()
        assert view["owner_quota_exhausted"] is True and view["run"]["owner_id"] is None


async def test_disabled_account_cannot_start_and_its_running_run_is_paused(
    tmp_path: Path,
) -> None:
    async with _site(_settings(tmp_path), StubEngine(chunk_delay_s=0.005)) as site:
        alice, alice_id = await site.signup()
        accounts = site.h.state.accounts

        first = (await site.launch(alice)).json()
        accounts.set_disabled(alice_id, True)
        refused = await site.control(first, "start")
        assert refused.status_code == 403 and "disabled" in refused.json()["error"]["message"]
        assert site.h.engine.requests == []

        accounts.set_disabled(alice_id, False)
        started = await site.control(first, "start")
        assert started.status_code == 200, started.text
        await _wait_state(site.c, first["run_id"], {"running"})
        accounts.set_disabled(alice_id, True)
        view = await _wait_state(site.c, first["run_id"], {"paused", "complete"})
        assert view["run"]["state"] == "paused", "the next persisted sample pauses the run"
        assert (await site.control(first, "resume")).status_code == 403

        accounts.set_disabled(alice_id, False)
        assert (await site.control(first, "resume")).status_code == 200
        await _wait_state(site.c, first["run_id"], {"complete"})


async def test_a_ledger_failure_pauses_the_run_instead_of_running_unmetered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _site(_settings(tmp_path), StubEngine(chunk_delay_s=0.005)) as site:
        alice, _ = await site.signup()
        launched = (await site.launch(alice)).json()
        ledger = site.h.state.ledger
        with monkeypatch.context() as patch:

            def down(event: UsageEvent) -> None:
                raise RuntimeError("database is locked")

            patch.setattr(ledger, "record", down)
            view = await site.run_to(launched, {"paused", "complete"})
            assert view["run"]["state"] == "paused"
        # every generated row is still on disk even though none was recorded
        run_store = site.h.store.find_run(launched["run_id"])
        assert run_store is not None
        on_disk = sum(
            len(list(run_store.generations(arm, "stub_free"))) for arm in ("qrng", "prng_seed_7")
        )
        assert 0 < on_disk < SAMPLES
        assert ledger.totals_by_user() == {}

        assert (await site.control(launched, "resume")).status_code == 200
        await _wait_state(site.c, launched["run_id"], {"complete"})
        assert _kinds(await site.usage(alice))["generation"]["calls"] == SAMPLES - on_disk


async def test_engine_narrative_and_judge_tokens_are_charged(site: Site) -> None:
    alice, _ = await site.signup()
    launched = (await site.launch(alice)).json()
    recorder: UsageRecorder = site.h.state.usage
    usage = {"prompt_tokens": 900, "completion_tokens": 300}
    recorder.record_narrative(launched["run_id"], "claude-x", usage)
    recorder.record_narrative(launched["run_id"], "qr-llm", usage, on_engine=True)
    narrative = _kinds(await site.usage(alice))["narrative"]
    assert (narrative["prng_input"], narrative["prng_output"]) == (900, 300)
    assert narrative["own_key_tokens"] == 1200 and narrative["qrng_input"] == 0

    note = UsageNote(
        run_id=launched["run_id"],
        kind="judge",
        arm_id="qrng",
        card_id="stub_free",
        model="qr-llm",
        source_label=None,
        pipeline_pinned=True,
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        at=T0,
    )
    recorder.on_row(note)
    judge = _kinds(await site.usage(alice))["judge"]
    assert (judge["prng_input"], judge["prng_output"]) == (10, 5)


def test_a_failed_migration_rolls_back_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "accounts.sqlite"
    good = accounts_service.MIGRATIONS
    broken = (good[0], (2, "CREATE TABLE half_done (id INTEGER); CREATE TABLE users (x);"))
    with monkeypatch.context() as patch:
        patch.setattr(accounts_service, "MIGRATIONS", broken)
        with pytest.raises(Exception, match="already exists"):
            Accounts(path, scrypt_n=FAST_N)
    # nothing of the failed migration stuck, so the real one applies cleanly
    acc = Accounts(path, scrypt_n=FAST_N)
    tables = {r["name"] for r in acc._conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in tables and "usage_events" in tables
    versions = [r["version"] for r in acc._conn.execute("SELECT version FROM schema_migrations")]
    assert sorted(versions) == [1, 2, 3]
    acc.close()


# ── POST /judge/check ─────────────────────────────────────────────────────────


async def test_judge_check_tries_the_settings_with_the_users_key(
    site: Site, caplog: pytest.LogCaptureFixture
) -> None:
    alice, _ = await site.signup()
    body = {"judge": {"model": "claude-haiku-5-5", "max_tokens": 200}, "api_key": ANTHROPIC}
    assert (await site.c.post(f"{API}/judge/check", json=body)).status_code == 401

    with caplog.at_level("DEBUG"):
        ok = await site.c.post(f"{API}/judge/check", json=body, headers=alice)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {
        "ok": True,
        "status": 200,
        "model": "claude-haiku-5-5",
        "detail": "The judge answered.",
    }
    sent = site.h.judge.calls[-1]
    assert sent["model"] == "claude-haiku-5-5" and sent["max_tokens"] == 200
    assert sent["thinking"] == {"type": "disabled"} and "temperature" not in sent
    # the key was used once and appears nowhere else
    assert ANTHROPIC not in ok.text and ANTHROPIC not in caplog.text

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "temperature is not supported on this model"}}
        )

    site.h.state.judge_transport = httpx.MockTransport(refuse)
    hot = {"judge": {"model": "claude-haiku-5-5", "temperature": 0.5}, "api_key": ANTHROPIC}
    bad = await site.c.post(f"{API}/judge/check", json=hot, headers=alice)
    assert bad.status_code == 200
    assert bad.json()["ok"] is False and bad.json()["status"] == 400
    assert "temperature is not supported" in bad.json()["detail"]

    out_of_range = {"judge": {"temperature": 3}, "api_key": ANTHROPIC}
    assert (
        await site.c.post(f"{API}/judge/check", json=out_of_range, headers=alice)
    ).status_code == 400
