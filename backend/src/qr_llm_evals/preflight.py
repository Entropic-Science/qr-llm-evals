"""``python -m qr_llm_evals.preflight`` — rehearse the boot without binding a socket.

Run it under the unit's environment before ``systemctl restart qr-llm-evals``::

    sudo systemd-run --quiet --wait --pipe --collect -p User=qr-llm-evals \\
        -p EnvironmentFile=/etc/qr-llm-evals/qr-llm-evals.env \\
        /opt/qr-llm-evals/.venv/bin/python -m qr_llm_evals.preflight

Checks (each prints one plain line):

``config``      the environment parses (``config.load_settings``).
``data_dir``    ``QR_EVALS_DATA_DIR`` exists (or can be created) and is writable.
``keys``        ``file`` mode: the keys file is readable and parses, with ≥ 1
                entry; ``probe`` mode: the edge origin answers.
``engine``      the shared vLLM answers ``GET /health`` (non-fatal: the API
                still serves the registry while the engine restarts; runs
                will fail readiness instead).
``registry``    ``evals/*.yaml`` and ``config/*.yaml`` load.

Exit ``0`` when everything passes, ``1`` when only non-fatal checks failed,
``2`` when a fatal check failed (the same refusal the service would log).
The entrypoint reuses :func:`run_checks` so the two can never disagree.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from qr_llm_evals.cards.loader import CardLoadError, load_cards
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.keys.verify import FileKeyVerifier, KeyVerifyUnavailable
from qr_llm_evals.models.catalogue import CatalogueError, load_catalogue
from qr_llm_evals.models.providers import ProviderProfileError, load_provider_profiles

EXIT_OK = 0
EXIT_WARN = 1
EXIT_REFUSED = 2
ENGINE_TIMEOUT_S = 3.0


class BootRefused(Exception):
    """The environment cannot start the service (the message names why)."""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = True

    @property
    def line(self) -> str:
        mark = "OK " if self.ok else ("FAIL" if self.fatal else "WARN")
        return f"[{mark}] {self.name}: {self.detail}"


def check_data_dir(settings: Settings) -> Check:
    root = Path(settings.data_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".preflight-", delete=True):
            pass
    except OSError as err:
        return Check("data_dir", False, f"{root} is not writable: {err}")
    return Check("data_dir", True, f"{root} writable")


def check_keys(settings: Settings) -> Check:
    if settings.key_verify == "probe":
        try:
            with httpx.Client(timeout=ENGINE_TIMEOUT_S) as client:
                resp = client.get(settings.edge_origin + "/health")
        except httpx.HTTPError as err:
            return Check(
                "keys", False, f"edge {settings.edge_origin} unreachable: {type(err).__name__}"
            )
        return Check(
            "keys", True, f"probe mode; edge {settings.edge_origin} answered {resp.status_code}"
        )
    path = Path(settings.keys_file)
    if not path.exists():
        return Check("keys", False, f"{path} not found (QR_EVALS_KEYS_FILE)")
    if not os.access(path, os.R_OK):
        return Check(
            "keys",
            False,
            f"{path} is not readable by this user "
            "(chgrp qr-llm-evals + chmod 0640 on the qr-llm-api side; docs/SETUP_GUIDE.md)",
        )
    try:
        count = FileKeyVerifier(path).reload()
    except KeyVerifyUnavailable as err:
        return Check("keys", False, str(err))
    if count == 0:
        return Check("keys", False, f"{path} has no key entries; nobody could launch a run")
    return Check("keys", True, f"{path}: {count} entr{'y' if count == 1 else 'ies'}")


def check_engine(settings: Settings) -> Check:
    url = settings.vllm_origin + "/health"
    try:
        with httpx.Client(timeout=ENGINE_TIMEOUT_S) as client:
            resp = client.get(url)
    except httpx.HTTPError as err:
        return Check("engine", False, f"{url} unreachable: {type(err).__name__}", fatal=False)
    ok = resp.status_code < 400
    return Check("engine", ok, f"{url} answered {resp.status_code}", fatal=False)


def check_registry() -> Check:
    try:
        cards = load_cards()
        catalogue = load_catalogue()
        providers = load_provider_profiles()
    except (CardLoadError, CatalogueError, ProviderProfileError, OSError, ValueError) as err:
        return Check("registry", False, f"{type(err).__name__}: {err}")
    return Check(
        "registry",
        True,
        f"{len(cards)} cards, {len(catalogue.entries)} models, {len(providers)} provider profiles",
    )


def run_checks(settings: Settings, *, engine: bool = True) -> Iterator[Check]:
    yield check_data_dir(settings)
    yield check_keys(settings)
    yield check_registry()
    if engine:
        yield check_engine(settings)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in args
    try:
        settings = load_settings()
    except SystemExit as err:
        print(f"[FAIL] config: {err}", file=sys.stderr)
        return EXIT_REFUSED
    if not quiet:
        print(
            f"[OK ] config: bind {settings.bind_host}:{settings.bind_port}, "
            f"engine {settings.vllm_origin}, key_verify={settings.key_verify}, "
            f"data_dir={settings.data_dir}",
            file=sys.stderr,
        )
    worst = EXIT_OK
    for check in run_checks(settings):
        if not quiet or not check.ok:
            print(check.line, file=sys.stderr)
        if not check.ok:
            worst = max(worst, EXIT_REFUSED if check.fatal else EXIT_WARN)
    verdict = {EXIT_OK: "OK", EXIT_WARN: "OK with warnings", EXIT_REFUSED: "REFUSED"}[worst]
    print(f"qr-llm-evals preflight: {verdict}", file=sys.stderr)
    return worst


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())


__all__ = [
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_WARN",
    "BootRefused",
    "Check",
    "check_data_dir",
    "check_engine",
    "check_keys",
    "check_registry",
    "main",
    "run_checks",
]
