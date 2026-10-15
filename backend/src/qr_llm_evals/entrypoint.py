"""Process entry point: ``python -m qr_llm_evals.entrypoint`` or the uvicorn factory.

Two ways in, both used by ``infra/qr-llm-evals.service``:

* ``uvicorn qr_llm_evals.entrypoint:build_asgi_app --factory`` — uvicorn owns
  the socket; the factory reads the environment, runs the boot checks and
  returns the app.
* ``python -m qr_llm_evals.entrypoint`` — :func:`main` does the same and then
  calls ``uvicorn.run`` on ``QR_EVALS_BIND_HOST:QR_EVALS_BIND_PORT``
  (default ``127.0.0.1:8830``, loopback only; cloudflared fronts it, spec D-3).

Boot fails fast (exit 2, one JSON line + one plain stderr line naming the
reason) on a malformed environment or an unusable keys file / data dir, so a
``systemctl restart`` after a typo shows the cause in ``journalctl`` instead
of serving 500s. ``python -m qr_llm_evals.preflight`` runs the same checks
without binding a socket.
"""

from __future__ import annotations

import sys

from fastapi import FastAPI

from qr_llm_evals.api.app import create_app
from qr_llm_evals.config import Settings, load_settings
from qr_llm_evals.logging_utils import configure_logging, get_logger
from qr_llm_evals.preflight import BootRefused, run_checks

EXIT_REFUSED = 2


def load_boot_inputs() -> Settings:
    """Settings + preflight checks; raises :class:`BootRefused` with the reason."""
    try:
        settings = load_settings()
    except SystemExit as err:
        raise BootRefused(str(err)) from err
    problems = [c for c in run_checks(settings) if not c.ok and c.fatal]
    if problems:
        raise BootRefused("; ".join(f"{c.name}: {c.detail}" for c in problems))
    return settings


def build_asgi_app() -> FastAPI:
    """uvicorn ``--factory`` target."""
    # INFO until the settings are known good, so a bad LOG_LEVEL still logs the refusal.
    configure_logging("INFO")
    log = get_logger("qr_llm_evals.entrypoint")
    try:
        settings = load_boot_inputs()
    except BootRefused as err:
        log.error("boot refused", extra={"event": "api.boot.refused", "reason": str(err)})
        print(f"qr-llm-evals: {err}", file=sys.stderr)
        raise SystemExit(EXIT_REFUSED) from err
    configure_logging(settings.log_level)
    return create_app(settings)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    app = build_asgi_app()
    settings: Settings = app.state.qr.settings
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=None,  # our JSON root handler already captures uvicorn's loggers
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())


__all__ = ["EXIT_REFUSED", "build_asgi_app", "load_boot_inputs", "main"]
