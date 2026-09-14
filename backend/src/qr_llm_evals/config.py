"""Process configuration, read once from the environment.

This is the ONLY module in the package that touches ``os.environ`` (spec §3).
Every variable read here has a row in ``infra/ENV_REFERENCE.md``; the
``backend/tests/test_env_reference.py`` test scans this file for ``_env_*(e, "NAME")``
calls and fails when a row is missing, so a variable cannot enter the code
without entering the operator catalogue.

Settings are a frozen pydantic model built by :func:`load_settings`. Tests pass
an explicit mapping instead of mutating the process environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KeyVerifyMode = Literal["file", "probe"]

_Env = Mapping[str, str]

DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("https://evals.entropic.science", "http://localhost:5173")


def _env_str(env: _Env, name: str, default: str) -> str:
    return env.get(name, default).strip() or default


def _env_opt(env: _Env, name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _env_int(env: _Env, name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as err:
        raise SystemExit(f"{name}={raw!r} is not an integer") from err


def _env_list(env: _Env, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def normalise_origin(raw: str) -> str:
    """``http://127.0.0.1:8000/v1/`` → ``http://127.0.0.1:8000`` (origin only, no ``/v1``)."""
    url = "".join(raw.split()).rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url.rstrip("/")


class Settings(BaseModel):
    """Immutable process settings (see ``infra/ENV_REFERENCE.md`` for each field)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── serving stack the harness consumes (spec §1.3) ────────────────────
    vllm_origin: str = Field(description="Shared vLLM + qr-sampler origin on loopback.")
    edge_origin: str = Field(description="qr-llm-api edge origin, used only by probe key checks.")

    # ── launch gate (spec §2.4) ───────────────────────────────────────────
    key_verify: KeyVerifyMode
    keys_file: str
    max_active_runs_per_key: int = Field(ge=1)
    max_inflight: int = Field(ge=1)

    # ── generation (spec §2.2 common body) ────────────────────────────────
    priority: int = Field(description="vLLM request priority sent with every generation.")

    # ── storage and Tier 0 (spec D-4, §2.6) ───────────────────────────────
    data_dir: str
    token_log_dir: str | None = Field(
        default=None,
        description="Engine-side per-request token log directory (DEP-1); None = not available.",
    )

    # ── HTTP server ───────────────────────────────────────────────────────
    bind_host: str
    bind_port: int = Field(ge=1, le=65535)
    cors_origins: tuple[str, ...]
    log_level: str


def load_settings(env: _Env | None = None) -> Settings:
    """Build :class:`Settings` from ``env`` (defaults to the process environment).

    Invalid values raise ``SystemExit`` with the offending variable named, so a
    misconfigured unit fails at boot rather than mid-run.
    """
    e: _Env = os.environ if env is None else env
    raw_verify = _env_str(e, "QR_EVALS_KEY_VERIFY", "file")
    if raw_verify not in ("file", "probe"):
        raise SystemExit(f"QR_EVALS_KEY_VERIFY={raw_verify!r} must be 'file' or 'probe'")
    key_verify: KeyVerifyMode = "file" if raw_verify == "file" else "probe"
    return Settings(
        vllm_origin=normalise_origin(_env_str(e, "QR_EVALS_VLLM_ORIGIN", "http://127.0.0.1:8000")),
        edge_origin=normalise_origin(_env_str(e, "QR_EVALS_EDGE_ORIGIN", "http://127.0.0.1:8820")),
        key_verify=key_verify,
        keys_file=_env_str(e, "QR_EVALS_KEYS_FILE", "/etc/qr-llm-api/api-keys.json"),
        max_active_runs_per_key=_env_int(e, "QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY", 1),
        max_inflight=_env_int(e, "QR_EVALS_MAX_INFLIGHT", 3),
        priority=_env_int(e, "QR_EVALS_PRIORITY", 0),
        data_dir=_env_str(e, "QR_EVALS_DATA_DIR", "/var/lib/qr-llm-evals"),
        token_log_dir=_env_opt(e, "QR_EVALS_TOKEN_LOG_DIR"),
        bind_host=_env_str(e, "QR_EVALS_BIND_HOST", "127.0.0.1"),
        bind_port=_env_int(e, "QR_EVALS_BIND_PORT", 8830),
        cors_origins=_env_list(e, "QR_EVALS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS),
        log_level=_env_str(e, "QR_EVALS_LOG_LEVEL", "INFO").upper(),
    )


__all__ = ["KeyVerifyMode", "Settings", "load_settings", "normalise_origin"]
