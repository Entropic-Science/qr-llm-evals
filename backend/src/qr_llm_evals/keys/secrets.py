"""In-memory BYO secrets scoped to one run (spec D-14, §2.4, §5.2 "Secrets").

A :class:`SecretBag` holds the provider/judge keys a host typed into the launch
form. Guarantees (AGENTS.md invariant 3):

* **Never persisted.** The bag is not JSON-serialisable (``json.dumps`` raises
  ``TypeError``), has no pydantic schema, and ``run.json`` only ever stores
  :meth:`SecretBag.keys_present` booleans.
* **Never logged.** ``repr``/``str`` show only the *names* present; every value
  is registered with :mod:`qr_llm_evals.logging_utils` so :class:`SecretFilter`
  scrubs it from any log line or traceback that mentions it by accident.
* **Scoped to a run.** :meth:`SecretBag.clear` unregisters the values; a backend
  restart loses the bag, which is exactly why a restarted run is ``needs_keys``.

The provider names are the launch-body keys (``provider_keys: {openrouter?,
openai?, anthropic?, custom?}``); the bag is otherwise an opaque mapping.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from qr_llm_evals.logging_utils import (
    REDACTED,
    SecretFilter,
    redact,
    register_secret,
    unregister_secret,
)

#: The launch-body provider slots (spec §2.4). Others are accepted but reported too.
KNOWN_PROVIDERS: tuple[str, ...] = ("openrouter", "openai", "anthropic", "custom")


class SecretBag:
    """Mutable, non-serialisable mapping ``provider -> secret`` for one run."""

    __slots__ = ("_values",)

    def __init__(self, secrets: Mapping[str, str | None] | None = None) -> None:
        self._values: dict[str, str] = {}
        if secrets:
            for name, value in secrets.items():
                if value:
                    self.set(name, value)

    # ── mutation ──────────────────────────────────────────────────────────

    def set(self, name: str, value: str) -> None:
        """Store ``value`` under ``name`` and register it for log redaction."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"secret {name!r} must be a non-empty string")
        old = self._values.get(name)
        if old is not None and old != value:
            unregister_secret(old)
        self._values[name] = value
        register_secret(value)

    def remove(self, name: str) -> None:
        value = self._values.pop(name, None)
        if value is not None:
            unregister_secret(value)

    def clear(self) -> None:
        """Forget every secret (run ended); values are unregistered from the filter."""
        for value in self._values.values():
            unregister_secret(value)
        self._values.clear()

    # ── read ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def require(self, name: str) -> str:
        value = self._values.get(name)
        if value is None:
            raise KeyError(f"no {name!r} key in this run's secret bag")
        return value

    def __contains__(self, name: object) -> bool:
        return name in self._values

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[str]:
        """Iterates *names* only; values are never enumerable."""
        return iter(sorted(self._values))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))

    def keys_present(self) -> dict[str, bool]:
        """``{provider: bool}`` — the only form of the bag that may leave the process."""
        present = {name: name in self._values for name in KNOWN_PROVIDERS}
        for name in self._values:
            present.setdefault(name, True)
        return present

    # ── never leak ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"SecretBag(names={self.names()!r}, values={REDACTED})"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)

    def __reduce__(self) -> str:
        raise TypeError("SecretBag is never pickled or serialised")

    def __getstate__(self) -> None:
        raise TypeError("SecretBag is never pickled or serialised")


__all__ = ["KNOWN_PROVIDERS", "SecretBag", "SecretFilter", "redact"]
