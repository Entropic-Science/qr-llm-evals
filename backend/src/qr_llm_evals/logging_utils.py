"""Root logger setup and the secret-redaction filter (spec §2.4, D-14, §5.2 "Secrets").

JSON-per-line to stderr (journald picks it up), one line per event, in the
shape qr-llm-api uses. Message CONTENT is never logged — only metadata.

:class:`SecretFilter` is installed on the root **handler** (a filter on a
``Logger`` only sees records that logger itself created, not propagated
ones). It redacts two classes of secret from every record's message, args
and formatted exception text:

* values registered at runtime through :func:`register_secret` — every BYO
  provider/judge key placed in a run's ``SecretBag`` registers itself here;
* well-known token *shapes* (``qrk_…`` launch keys, ``sk-ant-…`` / ``sk-or-…``
  / ``sk-…`` provider keys, ``Bearer …`` headers) so a key that was never
  registered (a typo'd launch attempt, an upstream error echo) still never
  reaches the journal.

This module is a plain root module so that both ``keys/`` (layer 0) and the
entrypoint can import it without crossing a layer boundary.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any

SERVICE = "qr-llm-evals"
REDACTED = "[REDACTED]"

_RESERVED: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
    }
)

#: Token shapes redacted even when the value was never registered.
_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bqrk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bqrs_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-or-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
)

#: Shortest value we will register; anything shorter would redact noise.
MIN_SECRET_LENGTH = 8


class _SecretRegistry:
    """Process-wide set of secret values to scrub from log output."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        if len(value) >= MIN_SECRET_LENGTH:
            with self._lock:
                self._values.add(value)

    def discard(self, value: str) -> None:
        with self._lock:
            self._values.discard(value)

    def snapshot(self) -> tuple[str, ...]:
        with self._lock:
            # Longest first so a secret that contains another is scrubbed whole.
            return tuple(sorted(self._values, key=len, reverse=True))

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


_REGISTRY = _SecretRegistry()


def register_secret(value: str) -> None:
    """Make ``value`` disappear from every log record from now on."""
    _REGISTRY.add(value)


def unregister_secret(value: str) -> None:
    """Stop scrubbing ``value`` (a run has ended and its bag was cleared)."""
    _REGISTRY.discard(value)


def _clear_registry_for_tests() -> None:
    _REGISTRY.clear()


def redact(text: str) -> str:
    """Return ``text`` with every registered secret and known token shape replaced."""
    for value in _REGISTRY.snapshot():
        if value in text:
            text = text.replace(value, REDACTED)
    for pattern in _SHAPE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact_value(v) for v in value)
    return value


class SecretFilter(logging.Filter):
    """Scrub secrets from ``msg``, ``args``, extra fields and exception text.

    Never drops a record (always returns ``True``); it rewrites it in place so
    every handler downstream sees the redacted version.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Format eagerly so a secret split across ``msg`` and ``args`` ("Bearer %s")
        # is scrubbed as one string, then redact the rendered message.
        if record.args:
            try:
                record.msg = record.getMessage()
            except (TypeError, ValueError, KeyError):
                record.msg = redact(str(record.msg)) + " " + redact(repr(record.args))
            record.args = None
        record.msg = redact(str(record.msg)) if record.msg is not None else record.msg
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED or key.startswith("_"):
                continue
            record.__dict__[key] = _redact_value(value)
        if record.exc_info:
            # Formatter.format() appends a pre-set exc_text and only renders exc_info
            # when exc_text is empty — so render + redact once, then drop the live
            # exception object (its args still hold the raw secret).
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ``ts``, ``level``, ``logger``, ``service``, ``message`` + extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": SERVICE,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        elif record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def install_secret_filter(target: logging.Handler | logging.Logger) -> SecretFilter:
    """Attach one :class:`SecretFilter` to ``target`` (idempotent)."""
    for existing in target.filters:
        if isinstance(existing, SecretFilter):
            return existing
    flt = SecretFilter()
    target.addFilter(flt)
    return flt


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON stderr handler with :class:`SecretFilter` on the root logger."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    install_secret_filter(handler)
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs one INFO line per upstream call (URL only, but noisy); ours is enough.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


__all__ = [
    "REDACTED",
    "SERVICE",
    "JsonFormatter",
    "SecretFilter",
    "configure_logging",
    "get_logger",
    "install_secret_filter",
    "redact",
    "register_secret",
    "unregister_secret",
]
