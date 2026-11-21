#!/usr/bin/env python
"""Lock and launch the thesis study from a terminal.

The web wizard can load this study too (the "Load study file" button). This
script does the same steps in a way that is easy to repeat and to check, and
keeps what was registered in one folder:

    # 1. readiness, then the pre-registration and its four hashes, saved to a folder
    python studies/thesis_prompt_or_physics/thesis_launch.py lock --out registration/

    # 2. the pilot: ordinary numbers only (quantum arms switched off), needs no lock
    python studies/thesis_prompt_or_physics/thesis_launch.py pilot

    # 3. the full run: refuses unless the study still matches the locked hashes
    python studies/thesis_prompt_or_physics/thesis_launch.py launch --lock registration/ --start

``lock`` writes ``config.json``, ``readiness.json``, ``prereg.md``,
``plan.json`` and ``hashes.json`` (``config_hash``, ``plan_hash``,
``prereg_hash``, ``settings_sha256``). Those four hashes are what gets
registered; ``thesis_report.py register`` turns the folder into the text to
file. ``launch`` sends the locked configuration and the locked readiness
report, so the server freezes the same pre-registration, and it stops if the
study file, the settings file or the registry changed since the lock.

Secrets are never arguments. The account password and the Anthropic judge key
are asked for at the prompt, or read from the files named by
``--password-file`` and ``--judge-key-file``. The run's host token, which is
needed to start, pause or abort it, is written to ``<run_id>.host-token`` in
the output folder with owner-only permissions and is not printed.

By default the script talks to https://evals-api.entropic.science; on the box
use ``--api http://127.0.0.1:8830``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_API = "https://evals-api.entropic.science"
DEFAULT_STUDY = HERE / "study.yaml"
DEFAULT_SETTINGS = HERE / "settings.json"
PLACEHOLDERS = ("REPLACE_WITH_", "FILL-IN-BEFORE-REGISTRATION", "PIN_")
HASH_KEYS = ("config_hash", "plan_hash", "prereg_hash", "settings_sha256")
QUANTUM_SOURCE = "qrng"


class LaunchError(SystemExit):
    """A refusal with a message for the operator (exit status 1)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"error: {message}")


# ── inputs ───────────────────────────────────────────────────────────────────


def load_study(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise LaunchError(f"{path} is not a study file")
    return config


def placeholders_in(config: dict[str, Any]) -> list[str]:
    """Template placeholders still in the study (author, contact, settings hash)."""
    text = json.dumps(config)
    return [marker for marker in PLACEHOLDERS if marker in text]


def settings_sha256(path: Path) -> str:
    sys.path.insert(0, str(HERE))
    from thesis import load_settings  # the study's own module; not part of the package

    return str(load_settings(path).sha256)


def pilot_config(config: dict[str, Any]) -> dict[str, Any]:
    """The study with every quantum arm switched off: the pilot uses ordinary numbers only."""
    pilot = json.loads(json.dumps(config))
    for arm in pilot["arms"]:
        if arm.get("source") == QUANTUM_SOURCE:
            arm["availability"] = "unavailable"
            arm["unavailable_reason"] = "pilot: ordinary numbers only (thesis runbook, step 5)"
    pilot["pilot"] = {**(pilot.get("pilot") or {}), "enabled": True}
    return pilot


def read_secret(file: Path | None, prompt: str) -> str:
    if file is not None:
        value = file.read_text(encoding="utf-8").strip()
    elif sys.stdin.isatty():
        value = getpass.getpass(prompt)
    else:
        raise LaunchError(
            f"no terminal to ask for it: {prompt.rstrip(': ')} needs a --…-file option"
        )
    if not value:
        raise LaunchError(f"empty value for: {prompt.rstrip(': ')}")
    return value


# ── the API ──────────────────────────────────────────────────────────────────


class Api:
    """The handful of calls the launch needs, on one signed-in session."""

    def __init__(self, client: httpx.Client, base: str) -> None:
        self.client = client
        self.base = base.rstrip("/") + ("" if base.rstrip("/").endswith("/api") else "/api")
        self.headers: dict[str, str] = {}

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, self.base + path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = response.text[:300]
            raise LaunchError(f"{method} {path} answered {response.status_code}: {detail}")
        return response.json()

    def login(self, username: str, password: str) -> None:
        data = self._send("POST", "/auth/login", json={"username": username, "password": password})
        self.headers = {"Authorization": f"Bearer {data['session_token']}"}

    def readiness(self, config: dict[str, Any]) -> dict[str, Any]:
        body = {"config": config, "throughput_probe": False}
        report: dict[str, Any] = self._send("POST", "/readiness", json=body, headers=self.headers)
        return report

    def prereg(self, config: dict[str, Any], readiness: dict[str, Any] | None) -> dict[str, Any]:
        body = {"config": config, "readiness": readiness}
        rendered: dict[str, Any] = self._send("POST", "/prereg/render", json=body)
        return rendered

    def launch(
        self,
        config: dict[str, Any],
        readiness: dict[str, Any] | None,
        *,
        kind: str,
        prereg_hash: str | None,
        judge_key: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "config": config,
            "readiness": readiness,
            "kind": kind,
            "provider_keys": {"anthropic": judge_key},
        }
        if prereg_hash is not None:
            body["prereg_hash"] = prereg_hash
        launched: dict[str, Any] = self._send("POST", "/runs", json=body, headers=self.headers)
        return launched

    def start(self, run_id: str, host_token: str) -> dict[str, Any]:
        started: dict[str, Any] = self._send(
            "POST",
            f"/runs/{run_id}/control",
            json={"action": "start"},
            headers={"X-Host-Token": host_token},
        )
        return started


# ── steps ────────────────────────────────────────────────────────────────────


def describe_readiness(report: dict[str, Any]) -> list[str]:
    arms = report.get("arms") or {}
    lines = [
        f"  token log: {report.get('tier0_source')}",
        f"  arms differ only in the entropy source: {report.get('equivalence_ok')}",
        f"  determinism: {report.get('determinism_tier')}",
        f"  biased control arms supported: {report.get('epsilon_supported')}",
    ]
    for arm_id, state in sorted(arms.items()):
        note = "" if state.get("available") else f" ({state.get('reason') or 'unavailable'})"
        lines.append(f"  arm {arm_id}: {'ready' if state.get('available') else 'not ready'}{note}")
    return lines


def readiness_problems(report: dict[str, Any], *, need_token_log: bool) -> list[str]:
    problems = []
    if not report.get("equivalence_ok"):
        problems.append("the arms differ in more than the entropy source")
    if need_token_log and report.get("tier0_source") != "engine_log":
        problems.append(
            "the engine token log is not available, so the sampler check (H0) and the "
            "first-word test (H4) could not be run"
        )
    return problems


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def save_host_token(out: Path, launched: dict[str, Any]) -> Path:
    path = out / f"{launched['run_id']}.host-token"
    path.write_text(launched["host_token"] + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def cmd_lock(api: Api, args: argparse.Namespace) -> int:
    config = load_study(args.study)
    left = placeholders_in(config)
    if left and not args.allow_placeholders:
        raise LaunchError(
            f"{args.study.name} still has template placeholders ({', '.join(left)}); fill in the "
            "author, the contact and the settings hash first (runbook, step 3)"
        )
    settings_hash = settings_sha256(args.settings)
    if settings_hash not in json.dumps(config) and not args.allow_placeholders:
        raise LaunchError(
            f"the settings hash {settings_hash} is not written into the study's expectations; "
            "the decision constants would not be part of the registration (runbook, step 3)"
        )
    print("Checking readiness…")
    readiness = api.readiness(config)
    print("\n".join(describe_readiness(readiness)))
    problems = readiness_problems(readiness, need_token_log=True)
    if problems and not args.force:
        raise LaunchError("not ready to lock: " + "; ".join(problems) + " (--force overrides)")

    rendered = api.prereg(config, readiness)
    hashes = {
        "config_hash": rendered["config_hash"],
        "plan_hash": rendered["plan_hash"],
        "prereg_hash": rendered["prereg_hash"],
        "settings_sha256": settings_hash,
    }
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "config.json", config)
    write_json(out / "readiness.json", readiness)
    write_json(out / "plan.json", rendered["plan"])
    write_json(out / "hashes.json", hashes)
    (out / "prereg.md").write_text(rendered["markdown"], encoding="utf-8", newline="\n")
    print(f"\nLocked in {out}:")
    for key in HASH_KEYS:
        print(f"  {key}: {hashes[key]}")
    print("\nRegister these four hashes before the full run (runbook, step 6).")
    return 0


def cmd_pilot(api: Api, args: argparse.Namespace) -> int:
    config = pilot_config(load_study(args.study))
    print("Checking readiness for the pilot (quantum arms switched off)…")
    readiness = api.readiness(config)
    print("\n".join(describe_readiness(readiness)))
    problems = readiness_problems(readiness, need_token_log=False)
    if problems and not args.force:
        raise LaunchError("not ready: " + "; ".join(problems) + " (--force overrides)")
    judge_key = read_secret(args.judge_key_file, "Anthropic judge key: ")
    launched = api.launch(config, readiness, kind="pilot", prereg_hash=None, judge_key=judge_key)
    return _after_launch(api, args, launched, args.out)


def cmd_launch(api: Api, args: argparse.Namespace) -> int:
    lock: Path = args.lock
    try:
        locked = json.loads((lock / "hashes.json").read_text(encoding="utf-8"))
        config = json.loads((lock / "config.json").read_text(encoding="utf-8"))
        readiness = json.loads((lock / "readiness.json").read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise LaunchError(f"{lock} is not a lock folder ({err.filename} is missing)") from err

    current = load_study(args.study)
    if current != config:
        raise LaunchError(
            f"{args.study} differs from the locked {lock / 'config.json'}: the study changed "
            "after it was locked. Lock (and register) again, or restore the study file."
        )
    now = {**api.prereg(config, readiness), "settings_sha256": settings_sha256(args.settings)}
    changed = [key for key in HASH_KEYS if now[key] != locked.get(key)]
    if changed:
        detail = "; ".join(f"{key} is {now[key]}, locked {locked.get(key)}" for key in changed)
        raise LaunchError(
            "the study no longer matches what was locked (the settings file, the eval registry "
            f"or the server's pre-registration template changed): {detail}"
        )
    print("The study matches the locked hashes:")
    for key in HASH_KEYS:
        print(f"  {key}: {locked[key]}")

    judge_key = read_secret(args.judge_key_file, "Anthropic judge key: ")
    launched = api.launch(
        config, readiness, kind="full", prereg_hash=locked["prereg_hash"], judge_key=judge_key
    )
    if launched.get("prereg_stale"):
        # The run exists but was not started; say so loudly and leave it idle.
        token_file = save_host_token(lock, launched)
        raise LaunchError(
            f"run {launched['run_id']} was created but the server froze a different "
            f"pre-registration than the locked one. It was NOT started. Abort it from the web "
            f"page (host token in {token_file}) and find out what changed."
        )
    return _after_launch(api, args, launched, lock)


def _after_launch(api: Api, args: argparse.Namespace, launched: dict[str, Any], out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    token_file = save_host_token(out, launched)
    print(f"\nRun {launched['run_id']} created in study {launched['study_id']}.")
    print(f"Host token saved to {token_file} (needed to start, pause or abort the run).")
    if args.start:
        state = api.start(launched["run_id"], launched["host_token"])
        print(f"Started: {state.get('state', 'running')}.")
    else:
        print("Not started. Start it from the run page, or run this command again with --start.")
    return 0


# ── command line ─────────────────────────────────────────────────────────────


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    top.add_argument("--api", default=DEFAULT_API, help=f"API origin (default {DEFAULT_API})")
    top.add_argument("--study", type=Path, default=DEFAULT_STUDY, help="study file (YAML)")
    top.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="thesis settings file")
    top.add_argument("--username", help="account name (asked for when omitted)")
    top.add_argument("--password-file", type=Path, help="file holding the account password")
    sub = top.add_subparsers(dest="command", required=True)

    lock = sub.add_parser("lock", help="readiness, pre-registration and the four hashes")
    lock.add_argument("--out", type=Path, required=True, help="folder to write the lock into")
    lock.add_argument("--force", action="store_true", help="lock although readiness has problems")
    lock.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="lock a template that still has placeholders (rehearsals only)",
    )

    pilot = sub.add_parser("pilot", help="launch the pilot with the quantum arms switched off")
    launch = sub.add_parser("launch", help="launch the full run from a lock folder")
    launch.add_argument("--lock", type=Path, required=True, help="folder written by 'lock'")
    for command in (pilot, launch):
        command.add_argument("--judge-key-file", type=Path, help="file holding the Anthropic key")
        command.add_argument("--start", action="store_true", help="start generation right away")
    pilot.add_argument("--out", type=Path, default=Path("."), help="folder for the host token file")
    pilot.add_argument(
        "--force", action="store_true", help="launch although readiness has problems"
    )
    return top


def run(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    args = parser().parse_args(argv)
    own = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(600, connect=15))
    try:
        api = Api(http, args.api)
        username = args.username or (input("Account name: ") if sys.stdin.isatty() else "")
        if not username:
            raise LaunchError("give the account name with --username")
        api.login(username, read_secret(args.password_file, "Account password: "))
        commands = {"lock": cmd_lock, "pilot": cmd_pilot, "launch": cmd_launch}
        return commands[args.command](api, args)
    finally:
        if own:
            http.close()


if __name__ == "__main__":
    raise SystemExit(run())
