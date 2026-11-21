"""Launching the real study files: Study 1 and the thesis study through
``POST /runs``, and the thesis launch script (lock, pilot, launch) against the
app with the real eval registry and a stub engine.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from qr_llm_evals.api.app import create_app
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.cards import REPO_ROOT
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys.verify import sha256_hex
from qr_llm_evals.prereg import prereg_hash
from stub_engine import StubEngine

STUDY_DIR = REPO_ROOT / "studies" / "thesis_prompt_or_physics"
sys.path.insert(0, str(STUDY_DIR))

import thesis_launch  # noqa: E402

API_KEY = "qrk_" + "thesis_launch_0123456789abcd"
PASSWORD = "correct horse battery"
JUDGE_KEY = "sk-ant-" + "api03-thesis-launch-0123456789"
SETTINGS_FILE = STUDY_DIR / "settings.json"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    keys_file = tmp_path / "api-keys.json"
    keys_file.write_text(
        json.dumps([{"name": "alice", "key_sha256": sha256_hex(API_KEY)}]), encoding="utf-8"
    )
    settings = load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys_file),
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
            "QR_EVALS_DEFAULT_TOKEN_QUOTA": "0",
        }
    )
    engine = StubEngine(n_tokens=8)
    app = create_app(
        settings,  # the real eval registry and model catalogue
        limiter=TokenBucketLimiter(rate_per_s=1000.0, burst=1000),
        engine_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"})),
        readiness_transport=engine.transport,
    )
    with TestClient(app) as test_client:
        signup = test_client.post(
            "/api/auth/signup",
            json={"username": "alice", "password": PASSWORD, "api_key": API_KEY},
        )
        assert signup.status_code == 201, signup.text
        test_client.headers["X-Test-Session"] = signup.json()["session_token"]
        yield test_client


def _auth(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {client.headers['X-Test-Session']}"}


def _thesis_study(tmp_path: Path) -> Path:
    """The thesis template with its placeholders filled in, as the runbook asks."""
    config = yaml.safe_load((STUDY_DIR / "study.yaml").read_text(encoding="utf-8"))
    config["authors"] = ["Test Author", "Entropic Science"]
    config["contact"] = "study@example.invalid"
    config["expectations"] = (
        str(config["expectations"])
        .replace("FILL-IN-BEFORE-REGISTRATION", thesis_launch.settings_sha256(SETTINGS_FILE))
        .replace("REPLACE_WITH_THE_THEORY_AUTHORS_PREDICTIONS", "no difference between sources")
    )
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _files(tmp_path: Path) -> list[str]:
    password = tmp_path / "password"
    password.write_text(PASSWORD, encoding="utf-8")
    return [
        "--api", "http://testserver",
        "--username", "alice",
        "--password-file", str(password),
        "--settings", str(SETTINGS_FILE),
    ]  # fmt: skip


def _judge_key_file(tmp_path: Path) -> str:
    path = tmp_path / "judge-key"
    path.write_text(JUDGE_KEY + "\n", encoding="utf-8")
    return str(path)


def _run_record(client: TestClient, run_id: str) -> dict[str, Any]:
    record: dict[str, Any] = client.get(f"/api/runs/{run_id}").json()["run"]
    return record


# ── the study files launch as written ────────────────────────────────────────


@pytest.mark.parametrize(
    "study", ["studies/study1.yaml", "studies/thesis_prompt_or_physics/study.yaml"]
)
def test_a_study_naming_the_derived_cards_can_be_launched(client: TestClient, study: str) -> None:
    config = yaml.safe_load((REPO_ROOT / study).read_text(encoding="utf-8"))
    assert any(c["card_id"].startswith("tier0_") for c in config["cards"])
    launched = client.post(
        "/api/runs",
        json={"config": config, "kind": "pilot", "provider_keys": {"anthropic": JUDGE_KEY}},
        headers=_auth(client),
    )
    assert launched.status_code == 201, launched.text
    assert _run_record(client, launched.json()["run_id"])["state"] == "created"


def test_a_planned_card_still_cannot_be_launched(client: TestClient) -> None:
    config = yaml.safe_load((REPO_ROOT / "studies/study1.yaml").read_text(encoding="utf-8"))
    config["cards"] = [{"card_id": "darkbench"}]
    refused = client.post(
        "/api/runs",
        json={"config": config, "kind": "pilot", "provider_keys": {"anthropic": JUDGE_KEY}},
        headers=_auth(client),
    )
    assert refused.status_code == 400 and "darkbench" in refused.json()["error"]["message"]


# ── the launch script ────────────────────────────────────────────────────────


def test_lock_then_launch_freezes_the_registered_preregistration(
    client: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study = _thesis_study(tmp_path)
    lock = tmp_path / "registration"
    base = [*_files(tmp_path), "--study", str(study)]

    # The stub engine writes no token log, which the thesis needs: no lock without --force.
    with pytest.raises(SystemExit, match="engine token log is not available"):
        thesis_launch.run([*base, "lock", "--out", str(lock)], client=client)
    assert not lock.exists()

    assert thesis_launch.run([*base, "lock", "--out", str(lock), "--force"], client=client) == 0
    hashes = json.loads((lock / "hashes.json").read_text(encoding="utf-8"))
    assert set(hashes) == set(thesis_launch.HASH_KEYS)
    assert all(len(value) == 64 for value in hashes.values())
    assert prereg_hash((lock / "prereg.md").read_text(encoding="utf-8")) == hashes["prereg_hash"]
    assert hashes["settings_sha256"] == thesis_launch.settings_sha256(SETTINGS_FILE)
    assert json.loads((lock / "plan.json").read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    assert all(hashes[key] in out for key in thesis_launch.HASH_KEYS)

    launch = [*base, "launch", "--lock", str(lock), "--judge-key-file", _judge_key_file(tmp_path)]
    assert thesis_launch.run(launch, client=client) == 0
    out = capsys.readouterr().out
    (token_file,) = lock.glob("*.host-token")
    run_id = token_file.name.removesuffix(".host-token")
    record = _run_record(client, run_id)
    assert record["kind"] == "full" and record["state"] == "created"
    assert record["prereg_hash"] == hashes["prereg_hash"] and record["prereg_stale"] is False
    assert record["config_hash"] == hashes["config_hash"]
    assert record["keys_present"]["anthropic"] is True
    # secrets stay out of the terminal; the host token is only in its file
    host_token = token_file.read_text(encoding="utf-8").strip()
    assert host_token and host_token not in out
    assert JUDGE_KEY not in out and PASSWORD not in out
    assert "Not started" in out


def test_launch_refuses_a_study_that_changed_after_the_lock(
    client: TestClient, tmp_path: Path
) -> None:
    study = _thesis_study(tmp_path)
    lock = tmp_path / "registration"
    base = [*_files(tmp_path), "--study", str(study)]
    thesis_launch.run([*base, "lock", "--out", str(lock), "--force"], client=client)
    launch = [*base, "launch", "--lock", str(lock), "--judge-key-file", _judge_key_file(tmp_path)]

    changed = yaml.safe_load(study.read_text(encoding="utf-8"))
    changed["seeds"] = [1, 7, 43]
    for arm in changed["arms"]:
        if arm.get("study_seed") == 42:
            arm["study_seed"] = 43
    study.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="the study changed after it was locked"):
        thesis_launch.run(launch, client=client)

    # the study file restored, but the decision constants edited
    study.write_text(
        yaml.safe_dump(json.loads((lock / "config.json").read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    edited = tmp_path / "settings.json"
    settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    settings["tolerance"] = 0.02
    edited.write_text(json.dumps(settings), encoding="utf-8")
    other = [a if a != str(SETTINGS_FILE) else str(edited) for a in launch]
    with pytest.raises(SystemExit, match="settings_sha256"):
        thesis_launch.run(other, client=client)
    assert list(lock.glob("*.host-token")) == [], "nothing was launched"


def test_lock_refuses_a_template_with_placeholders(client: TestClient, tmp_path: Path) -> None:
    base = [*_files(tmp_path), "--study", str(STUDY_DIR / "study.yaml")]
    with pytest.raises(SystemExit, match="template placeholders"):
        thesis_launch.run(
            [*base, "lock", "--out", str(tmp_path / "lock"), "--force"], client=client
        )


def test_the_pilot_switches_the_quantum_arms_off(client: TestClient, tmp_path: Path) -> None:
    study = _thesis_study(tmp_path)
    out = tmp_path / "pilot"
    args = [
        *_files(tmp_path), "--study", str(study),
        "pilot", "--out", str(out), "--judge-key-file", _judge_key_file(tmp_path),
    ]  # fmt: skip
    assert thesis_launch.run(args, client=client) == 0
    (token_file,) = out.glob("*.host-token")
    record = _run_record(client, token_file.name.removesuffix(".host-token"))
    assert record["kind"] == "pilot"
    frozen = client.app.state.qr.store.load_study(record["study_id"])  # type: ignore[attr-defined]
    quantum = [arm for arm in frozen.arms if arm.source == "qrng"]
    assert quantum and all(arm.availability == "unavailable" for arm in quantum)
    assert any(arm.availability == "available" for arm in frozen.arms)


def test_a_wrong_password_is_reported_plainly(client: TestClient, tmp_path: Path) -> None:
    wrong = tmp_path / "wrong"
    wrong.write_text("not the password", encoding="utf-8")
    args = [
        "--api", "http://testserver", "--username", "alice", "--password-file", str(wrong),
        "lock", "--out", str(tmp_path / "lock"),
    ]  # fmt: skip
    with pytest.raises(SystemExit, match="401"):
        thesis_launch.run(args, client=client)


# ── POST /config/parse (the wizard's "Load study file") ──────────────────────


def test_a_study_file_parses_into_a_full_configuration(client: TestClient) -> None:
    text = (STUDY_DIR / "study.yaml").read_text(encoding="utf-8")
    parsed = client.post("/api/config/parse", json={"text": text})
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["unknown_cards"] == [] and len(body["config_hash"]) == 64
    config = body["config"]
    # defaults the file leaves out are present, which is what the wizard needs
    assert config["judge"]["provider"] == "anthropic" and config["judge"]["max_tokens"] == 1024
    assert all("overrides" in card for card in config["cards"])
    assert {arm["source"] for arm in config["arms"]} >= {"qrng", "prng_seeded", "prng_epsilon"}
    # the full form is itself a valid file and hashes the same
    again = client.post("/api/config/parse", json={"text": json.dumps(config)}).json()
    assert again["config_hash"] == body["config_hash"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("arms: [unclosed", "not valid YAML or JSON"),
        ("- just\n- a list\n", "does not hold a study configuration"),
        ("title: only a title\n", "not a valid study configuration"),
    ],
)
def test_a_bad_study_file_is_refused_with_the_reason(
    client: TestClient, text: str, message: str
) -> None:
    refused = client.post("/api/config/parse", json={"text": text})
    assert refused.status_code == 400
    assert message in refused.json()["error"]["message"]


def test_a_study_file_with_an_unknown_card_says_which(client: TestClient) -> None:
    config = yaml.safe_load((REPO_ROOT / "studies/study1.yaml").read_text(encoding="utf-8"))
    config["cards"] = [{"card_id": "creative_diversity"}, {"card_id": "no_such_eval"}]
    parsed = client.post("/api/config/parse", json={"text": yaml.safe_dump(config)})
    assert parsed.status_code == 200 and parsed.json()["unknown_cards"] == ["no_such_eval"]
