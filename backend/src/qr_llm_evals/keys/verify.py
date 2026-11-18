"""Launch-gate key verification (spec §2.4, D-1).

``POST /api/runs`` requires ``Authorization: Bearer qrk_…`` — an
api.entropic.science key. The key is used **only to gate**: it is never stored;
the run record keeps :attr:`KeyIdentity.key_name` (from the keys file) or
:attr:`KeyIdentity.key_fingerprint` (first 8 hex of the SHA-256) for quota
accounting.

Two verifiers, selected by ``QR_EVALS_KEY_VERIFY``:

``file`` (default on the box)
    :class:`FileKeyVerifier` reads qr-llm-api's ``/etc/qr-llm-api/api-keys.json``
    (a list of ``{"name", "key_sha256", "disabled"?}`` entries, or ``{"keys":
    [...]}``), hot-reloads on mtime/size change, compares ``sha256(key)`` against
    **every** entry in constant time (``hmac.compare_digest``, no early exit so
    timing does not reveal which row matched) and honours ``disabled``.
``probe``
    :class:`ProbeKeyVerifier` sends one ``max_tokens: 1`` request to the edge on
    the ``qr-llm-prng`` lane; ``401``/``403`` ⇒ refused. Any other response means
    the edge authenticated the key (a ``429`` is still an authenticated key);
    ``5xx``/network ⇒ :class:`KeyVerifyUnavailable`, never a silent refusal.

Both expose ``verify`` (sync) and ``averify`` (async, for the FastAPI dependency).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from qr_llm_evals.config import Settings

log = logging.getLogger(__name__)

KEY_PREFIX = "qrk_"
FINGERPRINT_HEX = 8
PROBE_LANE = "qr-llm-prng"
PROBE_PATH = "/v1/chat/completions"


class KeyRefused(Exception):
    """The presented key is unknown, disabled, or rejected by the edge."""

    def __init__(self, reason: str = "invalid launch key", *, status_code: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class KeyVerifyUnavailable(Exception):
    """The verifier itself cannot answer (keys file missing/malformed, edge down)."""


@dataclass(frozen=True, slots=True)
class KeyIdentity:
    """What a run record may keep about the launch key (never the key itself)."""

    key_name: str | None
    key_fingerprint: str

    @property
    def quota_id(self) -> str:
        """The per-key quota bucket: the name when known, else the fingerprint."""
        return self.key_name or self.key_fingerprint


def sha256_hex(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def fingerprint(secret: str) -> str:
    """First :data:`FINGERPRINT_HEX` hex chars of ``sha256(secret)``."""
    return sha256_hex(secret)[:FINGERPRINT_HEX]


def parse_bearer(header_value: str | None) -> str | None:
    """``"Bearer qrk_x"`` → ``"qrk_x"``; anything else → ``None``."""
    if not header_value:
        return None
    scheme, _, token = header_value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


class KeyVerifier(Protocol):
    """The launch gate's dependency surface."""

    mode: str

    def verify(self, key: str) -> KeyIdentity: ...

    async def averify(self, key: str) -> KeyIdentity: ...


# ── file mode ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Entry:
    name: str
    digest: str
    disabled: bool


def _read_entries(path: Path) -> list[_Entry]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise KeyVerifyUnavailable(f"keys file not found: {path}") from err
    except (OSError, ValueError) as err:
        raise KeyVerifyUnavailable(f"keys file unreadable or not JSON: {path}: {err}") from err
    if isinstance(raw, dict) and isinstance(raw.get("keys"), list):
        raw = raw["keys"]
    if not isinstance(raw, list):
        raise KeyVerifyUnavailable(f"{path}: expected a JSON list of key entries")
    entries: list[_Entry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise KeyVerifyUnavailable(f"{path}: entry {i} is not an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise KeyVerifyUnavailable(f"{path}: entry {i} has no name")
        if item.get("key_sha256"):
            digest = str(item["key_sha256"]).strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise KeyVerifyUnavailable(f"{path}: entry {name!r}: key_sha256 must be 64 hex")
        elif item.get("key"):
            # qr-llm-api may be configured to allow plaintext rows; hash, never keep.
            digest = sha256_hex(str(item["key"]))
        else:
            raise KeyVerifyUnavailable(f"{path}: entry {name!r} needs 'key_sha256'")
        entries.append(_Entry(name=name, digest=digest, disabled=bool(item.get("disabled"))))
    return entries


class FileKeyVerifier:
    """Constant-time lookup against qr-llm-api's keys file, hot-reloaded on change."""

    mode = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: list[_Entry] | None = None
        self._stamp: tuple[int, int] | None = None

    def _current_stamp(self) -> tuple[int, int]:
        try:
            st = os.stat(self.path)
        except OSError as err:
            raise KeyVerifyUnavailable(f"keys file not found: {self.path}") from err
        return (st.st_mtime_ns, st.st_size)

    def _load(self) -> list[_Entry]:
        stamp = self._current_stamp()
        if self._entries is None or stamp != self._stamp:
            self._entries = _read_entries(self.path)
            self._stamp = stamp
            log.info(
                "keys file loaded", extra={"event": "keys.reload", "count": len(self._entries)}
            )
        return self._entries

    def reload(self) -> int:
        """Force a reload; returns the number of entries."""
        self._entries = None
        return len(self._load())

    @property
    def names(self) -> list[str]:
        return [e.name for e in self._load()]

    def verify(self, key: str) -> KeyIdentity:
        if not key:
            raise KeyRefused("missing launch key")
        presented = sha256_hex(key)
        found: _Entry | None = None
        # Compare against EVERY entry so timing does not reveal which one matched.
        for entry in self._load():
            if hmac.compare_digest(presented, entry.digest):
                found = entry
        if found is None:
            raise KeyRefused("unknown launch key")
        if found.disabled:
            raise KeyRefused("launch key is disabled", status_code=403)
        return KeyIdentity(key_name=found.name, key_fingerprint=presented[:FINGERPRINT_HEX])

    async def averify(self, key: str) -> KeyIdentity:
        return self.verify(key)

    def verify_digest(self, digest: str) -> KeyIdentity:
        """Re-check a key by its stored SHA-256 (a linked account's key; no plaintext).

        Same answers as :meth:`verify`: a key removed from the file is refused
        with 401, a disabled one with 403.
        """
        presented = digest.strip().lower()
        found: _Entry | None = None
        for entry in self._load():
            if hmac.compare_digest(presented, entry.digest):
                found = entry
        if found is None:
            raise KeyRefused("unknown launch key")
        if found.disabled:
            raise KeyRefused("launch key is disabled", status_code=403)
        return KeyIdentity(key_name=found.name, key_fingerprint=presented[:FINGERPRINT_HEX])


@runtime_checkable
class DigestVerifier(Protocol):
    """A verifier that can also re-check a key from its digest (file mode only).

    The probe verifier cannot: the edge accepts keys, not digests.
    """

    def verify_digest(self, digest: str) -> KeyIdentity: ...


# ── probe mode ────────────────────────────────────────────────────────────────


def probe_body(lane: str = PROBE_LANE) -> dict[str, Any]:
    """The one-token request the probe sends (cheap, on the PRNG lane, never quantum)."""
    return {
        "model": lane,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }


class ProbeKeyVerifier:
    """Verify by asking the qr-llm-api edge on loopback (``QR_EVALS_KEY_VERIFY=probe``)."""

    mode = "probe"

    def __init__(
        self,
        edge_origin: str,
        *,
        lane: str = PROBE_LANE,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.edge_origin = edge_origin.rstrip("/")
        self.lane = lane
        self.timeout = timeout
        self._transport = transport
        self._async_transport = async_transport

    @property
    def url(self) -> str:
        return self.edge_origin + PROBE_PATH

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _interpret(status: int, key: str) -> KeyIdentity:
        if status in (401, 403):
            raise KeyRefused("launch key rejected by the edge", status_code=status)
        if status >= 500:
            raise KeyVerifyUnavailable(f"edge probe returned {status}")
        # 2xx, or a 4xx past authentication (400 body nit, 429 rate limit): key is real.
        return KeyIdentity(key_name=None, key_fingerprint=fingerprint(key))

    def verify(self, key: str) -> KeyIdentity:
        if not key:
            raise KeyRefused("missing launch key")
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                resp = client.post(self.url, headers=self._headers(key), json=probe_body(self.lane))
        except httpx.HTTPError as err:
            raise KeyVerifyUnavailable(f"edge probe failed: {type(err).__name__}") from err
        return self._interpret(resp.status_code, key)

    async def averify(self, key: str) -> KeyIdentity:
        if not key:
            raise KeyRefused("missing launch key")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._async_transport
            ) as client:
                resp = await client.post(
                    self.url, headers=self._headers(key), json=probe_body(self.lane)
                )
        except httpx.HTTPError as err:
            raise KeyVerifyUnavailable(f"edge probe failed: {type(err).__name__}") from err
        return self._interpret(resp.status_code, key)


def build_verifier(settings: Settings) -> KeyVerifier:
    """The verifier ``QR_EVALS_KEY_VERIFY`` selects."""
    if settings.key_verify == "probe":
        return ProbeKeyVerifier(settings.edge_origin)
    return FileKeyVerifier(settings.keys_file)


def verify(key: str, verifier: KeyVerifier) -> KeyIdentity:
    """Module-level convenience: ``verify(key)`` through the configured verifier."""
    return verifier.verify(key)


__all__ = [
    "FINGERPRINT_HEX",
    "KEY_PREFIX",
    "PROBE_LANE",
    "DigestVerifier",
    "FileKeyVerifier",
    "KeyIdentity",
    "KeyRefused",
    "KeyVerifier",
    "KeyVerifyUnavailable",
    "ProbeKeyVerifier",
    "build_verifier",
    "fingerprint",
    "parse_bearer",
    "probe_body",
    "sha256_hex",
    "verify",
]
