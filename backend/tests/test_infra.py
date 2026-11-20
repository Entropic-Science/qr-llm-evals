"""Deployment contracts; no live host, token, or paid generation required."""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def bash_binary() -> str:
    # Git Bash, not the Windows WSL launcher without an installed distro.
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable; static safety assertions still run")
    return bash


def test_unit_parses_and_is_hardened() -> None:
    name = "qr-llm-evals"
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.optionxform = str
    unit.read_string(read(f"infra/{name}.service"))
    assert set(unit.sections()) == {"Unit", "Service", "Install"}
    service = unit["Service"]
    assert service["User"] == service["Group"] == "qr-llm-evals"
    assert service["ProtectSystem"] == "strict"
    for option in ("NoNewPrivileges", "ProtectHome", "PrivateTmp", "PrivateDevices"):
        assert service[option] == "true"
    assert service["CapabilityBoundingSet"] == ""
    assert service["UMask"] == "0077"
    assert service["LimitCORE"] == "0"
    assert unit["Install"]["WantedBy"] == "multi-user.target"
    assert service["ExecStartPre"].endswith(" -m qr_llm_evals.preflight")
    assert service["ExecStart"].endswith(" -m qr_llm_evals.entrypoint")
    assert service["WorkingDirectory"] == "/opt/qr-llm-evals/current"
    assert service["StateDirectory"] == "qr-llm-evals"
    assert "EnvironmentFile=/etc/qr-llm-evals/qr-llm-evals.env" in read(f"infra/{name}.service")


def test_no_tunnel_connector_is_shipped() -> None:
    # The API hostname is a public hostname on qr-llm-api's tunnel; a second
    # connector here would need its own token and compete for the hostname.
    names = sorted(p.name for p in (ROOT / "infra").iterdir())
    assert not [n for n in names if "cloudflared" in n], names
    assert "cloudflared" not in read("scripts/deploy.sh")


def test_example_variables_documented_and_secrets_separated() -> None:
    reference = read("infra/ENV_REFERENCE.md")
    backend = read("infra/qr-llm-evals.env.example")
    for name in re.findall(r"^([A-Z][A-Z0-9_]*)=", backend, re.M):
        assert f"| `{name}` |" in reference, name
    assert "QR_EVALS_BIND_HOST=127.0.0.1" in backend
    assert "QR_EVALS_BIND_PORT=8830" in backend
    assert "QR_EVALS_CORS_ORIGINS=https://evals.entropic.science" in backend
    assert "TUNNEL_TOKEN=" not in backend
    assert not re.search(r"^(OPENAI|ANTHROPIC|OPENROUTER)_API_KEY=", backend, re.M)


@pytest.mark.parametrize("name", ["deploy", "rollback"])
def test_shell_syntax(name: str) -> None:
    subprocess.run([bash_binary(), "-n", f"scripts/{name}.sh"], cwd=ROOT, check=True)
    assert b"\r" not in (ROOT / "scripts" / f"{name}.sh").read_bytes()


def test_deploy_static_safety_contract() -> None:
    script = read("scripts/deploy.sh")
    assert "git status --porcelain --untracked-files=all" in script
    assert script.index("dirty tree; commit first") < script.index("git archive")
    assert 'git archive --format=tar "$COMMIT"' in script
    assert "--require-hashes" in script
    assert 'activate "$RELEASE" "$OLD"' in script
    rollback = read("scripts/rollback.sh")
    assert "flock -n 9" in rollback
    assert rollback.index('preflight "$target" || return 1') < rollback.index(
        'flip "$target" "$ROOT/current"'
    )
    assert 'd.get("commit")==sys.argv[1]' in rollback
    assert not re.search(r"\brm\s", script + rollback)


def test_dirty_checkout_refused_before_privileged_work(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("deploy", "rollback"):
        shutil.copyfile(ROOT / "scripts" / f"{name}.sh", scripts / f"{name}.sh")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    result = subprocess.run(
        [bash_binary(), "scripts/deploy.sh"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "dirty tree; commit first" in result.stderr
    assert not (tmp_path / "releases").exists()


@pytest.mark.parametrize(
    ("preflight_ok", "candidate_ok", "expected"),
    [
        (False, True, ["preflight candidate", "status 1"]),
        (
            True,
            True,
            [
                "preflight candidate",
                "flip candidate /opt/qr-llm-evals/current",
                "systemctl daemon-reload",
                "systemctl restart qr-llm-evals.service",
                "health candidate",
                "flip old /opt/qr-llm-evals/previous",
                "status 0",
            ],
        ),
        (
            True,
            False,
            [
                "preflight candidate",
                "flip candidate /opt/qr-llm-evals/current",
                "systemctl daemon-reload",
                "systemctl restart qr-llm-evals.service",
                "health candidate",
                "flip old /opt/qr-llm-evals/current",
                "systemctl daemon-reload",
                "systemctl restart qr-llm-evals.service",
                "health old",
                "status 1",
            ],
        ),
    ],
)
def test_activation_order_and_recovery(
    preflight_ok: bool, candidate_ok: bool, expected: list[str]
) -> None:
    # Mock every host-changing helper; execute real activation control flow only.
    code = f"""source scripts/rollback.sh
preflight() {{ echo "preflight $1"; return {0 if preflight_ok else 1}; }}
flip() {{ echo "flip $1 $2"; }}
systemctl() {{ echo "systemctl $*"; }}
health() {{ echo "health $1"; [[ $1 == old ]] || return {0 if candidate_ok else 1}; }}
status=0
activate candidate old || status=$?
echo "status $status"
"""
    result = subprocess.run(
        [bash_binary(), "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines() == expected


def _wrangler_config() -> dict[str, Any]:
    # JSONC: whole-line comments only, which is all the file uses.
    lines = read("wrangler.jsonc").splitlines()
    return json.loads("\n".join(line for line in lines if not line.lstrip().startswith("//")))


def test_web_worker_config_and_spa_fallback() -> None:
    config = _wrangler_config()
    assert config["name"] == "qr-llm-evals"
    assert config["assets"] == {
        "directory": "./apps/web/dist",
        "not_found_handling": "single-page-application",
    }
    # Assets only: no Worker script, bindings, routes or variables.
    assert set(config) == {"$schema", "name", "compatibility_date", "assets"}
    # The Pages-style catch-all rule is replaced by not_found_handling above.
    assert not (ROOT / "apps/web/public/_redirects").exists()


def test_web_security_headers() -> None:
    headers = read("apps/web/public/_headers")
    policy = next(line for line in headers.splitlines() if "Content-Security-Policy:" in line)
    directives = dict(
        part.strip().split(" ", 1)
        for part in policy.split(": ", 1)[1].split(";")
        if " " in part.strip()
    )
    assert directives["connect-src"] == "https://evals-api.entropic.science"
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == directives["frame-ancestors"] == "'none'"
    assert "'unsafe-eval'" not in policy
    assert "X-Content-Type-Options: nosniff" in headers


@pytest.mark.parametrize("flip_ok", [True, False])
def test_first_install_failure_and_failed_flip(flip_ok: bool) -> None:
    code = f"""source scripts/rollback.sh
preflight() {{ return 0; }}
flip() {{ echo flip; return {0 if flip_ok else 1}; }}
systemctl() {{ echo "systemctl $*"; }}
health() {{ return 1; }}
status=0
activate candidate '' || status=$?
echo "status $status"
"""
    result = subprocess.run(
        [bash_binary(), "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    if flip_ok:
        assert result.stdout.splitlines() == [
            "flip",
            "systemctl daemon-reload",
            "systemctl restart qr-llm-evals.service",
            "systemctl stop qr-llm-evals.service",
            "status 1",
        ]
        assert "First deployment failed" in result.stderr
    else:
        assert result.stdout.splitlines() == ["flip", "status 1"]
