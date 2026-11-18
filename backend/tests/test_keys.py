"""Launch gate + secrets (spec §2.4, D-14, §5.2 "Secrets"): file-mode verify
(valid / unknown / disabled, hot reload on mtime, constant-time digest), probe
mode (401/403 refused, 5xx unavailable, 2xx/429 verified), ``build_verifier``,
and ``SecretBag`` never appearing in repr / json / logs (``caplog``).
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from qr_llm_evals import logging_utils
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys import (
    FileKeyVerifier,
    KeyIdentity,
    KeyRefused,
    KeyVerifyUnavailable,
    ProbeKeyVerifier,
    SecretBag,
    SecretFilter,
    build_verifier,
    fingerprint,
    parse_bearer,
    verify,
)
from qr_llm_evals.keys.verify import PROBE_LANE, probe_body, sha256_hex
from qr_llm_evals.logging_utils import REDACTED, JsonFormatter, redact

GOOD = "qrk_" + "a" * 40
DISABLED = "qrk_" + "d" * 40
UNKNOWN = "qrk_" + "u" * 40


def _write_keys(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(entries), encoding="utf-8")


@pytest.fixture
def keys_file(tmp_path: Path) -> Path:
    path = tmp_path / "api-keys.json"
    _write_keys(
        path,
        [
            {"name": "alice", "key_sha256": sha256_hex(GOOD), "rpm": 60},
            {"name": "bob", "key_sha256": sha256_hex(DISABLED), "disabled": True},
        ],
    )
    return path


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    logging_utils._clear_registry_for_tests()
    yield
    logging_utils._clear_registry_for_tests()


# ── file mode ─────────────────────────────────────────────────────────────────


def test_file_verifier_valid_unknown_disabled(keys_file: Path) -> None:
    v = FileKeyVerifier(keys_file)
    ident = v.verify(GOOD)
    assert ident == KeyIdentity(key_name="alice", key_fingerprint=sha256_hex(GOOD)[:8])
    assert ident.quota_id == "alice"
    assert GOOD not in repr(ident)

    with pytest.raises(KeyRefused, match="unknown"):
        v.verify(UNKNOWN)
    with pytest.raises(KeyRefused, match="disabled"):
        v.verify(DISABLED)
    with pytest.raises(KeyRefused, match="missing"):
        v.verify("")
    assert v.names == ["alice", "bob"]
    assert v.mode == "file"


async def test_file_verifier_async_and_module_verify(keys_file: Path) -> None:
    v = FileKeyVerifier(keys_file)
    assert (await v.averify(GOOD)).key_name == "alice"
    assert verify(GOOD, v).key_name == "alice"


def test_file_verifier_hot_reloads_on_mtime_change(keys_file: Path) -> None:
    v = FileKeyVerifier(keys_file)
    assert v.verify(GOOD).key_name == "alice"
    with pytest.raises(KeyRefused):
        v.verify(UNKNOWN)

    # Add the unknown key and bump mtime explicitly (FS mtime resolution can be coarse).
    _write_keys(
        keys_file,
        [
            {"name": "alice", "key_sha256": sha256_hex(GOOD)},
            {"name": "carol", "key_sha256": sha256_hex(UNKNOWN)},
        ],
    )
    later = time.time() + 5
    os.utime(keys_file, (later, later))
    assert v.verify(UNKNOWN).key_name == "carol"
    assert v.reload() == 2


def test_file_verifier_accepts_keys_wrapper_and_plaintext_rows(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": [{"name": "p", "key": GOOD}]}), encoding="utf-8")
    assert FileKeyVerifier(path).verify(GOOD).key_name == "p"


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        "[1]",
        '[{"key_sha256": "abc"}]',
        '[{"name": "x"}]',
        '[{"name": "x", "key_sha256": "zz"}]',
    ],
)
def test_file_verifier_malformed_is_unavailable_not_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "keys.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(KeyVerifyUnavailable):
        FileKeyVerifier(path).verify(GOOD)


def test_file_verifier_missing_file_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(KeyVerifyUnavailable):
        FileKeyVerifier(tmp_path / "nope.json").verify(GOOD)


def test_fingerprint_and_bearer_parsing() -> None:
    assert fingerprint(GOOD) == sha256_hex(GOOD)[:8]
    assert parse_bearer(f"Bearer {GOOD}") == GOOD
    assert parse_bearer(f"bearer   {GOOD} ") == GOOD
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("Bearer ") is None
    assert parse_bearer(None) is None


# ── probe mode ────────────────────────────────────────────────────────────────


class _Edge:
    def __init__(self, status: int, *, error: bool = False) -> None:
        self.status = status
        self.error = error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise httpx.ConnectError("refused")
        return httpx.Response(self.status, json={"ok": self.status < 400})

    def verifier(self) -> ProbeKeyVerifier:
        t = httpx.MockTransport(self)
        return ProbeKeyVerifier("http://127.0.0.1:8820/", transport=t, async_transport=t)


def test_probe_sends_one_token_request_on_prng_lane() -> None:
    edge = _Edge(200)
    ident = edge.verifier().verify(GOOD)
    assert ident == KeyIdentity(key_name=None, key_fingerprint=fingerprint(GOOD))
    assert ident.quota_id == fingerprint(GOOD)
    req = edge.requests[0]
    assert str(req.url) == "http://127.0.0.1:8820/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {GOOD}"
    sent = json.loads(req.content)
    assert sent == probe_body()
    assert sent["model"] == PROBE_LANE == "qr-llm-prng"
    assert sent["max_tokens"] == 1 and sent["stream"] is False


@pytest.mark.parametrize("status", [401, 403])
def test_probe_refuses_on_auth_failure(status: int) -> None:
    with pytest.raises(KeyRefused):
        _Edge(status).verifier().verify(GOOD)


@pytest.mark.parametrize("status", [429, 400])
def test_probe_treats_post_auth_4xx_as_verified(status: int) -> None:
    assert _Edge(status).verifier().verify(GOOD).key_fingerprint == fingerprint(GOOD)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_probe_5xx_is_unavailable(status: int) -> None:
    with pytest.raises(KeyVerifyUnavailable):
        _Edge(status).verifier().verify(GOOD)


def test_probe_network_error_is_unavailable_and_empty_key_refused() -> None:
    with pytest.raises(KeyVerifyUnavailable):
        _Edge(200, error=True).verifier().verify(GOOD)
    with pytest.raises(KeyRefused):
        _Edge(200).verifier().verify("")


async def test_probe_async_paths() -> None:
    assert (await _Edge(200).verifier().averify(GOOD)).key_name is None
    with pytest.raises(KeyRefused):
        await _Edge(403).verifier().averify(GOOD)
    with pytest.raises(KeyVerifyUnavailable):
        await _Edge(200, error=True).verifier().averify(GOOD)
    with pytest.raises(KeyRefused):
        await _Edge(200).verifier().averify("")


def test_build_verifier_follows_settings(keys_file: Path) -> None:
    file_v = build_verifier(load_settings({"QR_EVALS_KEYS_FILE": str(keys_file)}))
    assert isinstance(file_v, FileKeyVerifier) and file_v.path == keys_file
    probe_v = build_verifier(
        load_settings(
            {"QR_EVALS_KEY_VERIFY": "probe", "QR_EVALS_EDGE_ORIGIN": "http://127.0.0.1:8820/v1"}
        )
    )
    assert isinstance(probe_v, ProbeKeyVerifier)
    assert probe_v.url == "http://127.0.0.1:8820/v1/chat/completions"


# ── SecretBag ─────────────────────────────────────────────────────────────────

ANTHROPIC = "sk-ant-" + "api03-bagsecret-ABCDEFGHIJKLMNOP"
SK_OR_SHAPED = "sk-or-v1-bagsecret-0123456789abcdef"


def test_secret_bag_basic_semantics() -> None:
    bag = SecretBag({"anthropic": ANTHROPIC, "openai": None, "custom": ""})
    assert bag.get("anthropic") == ANTHROPIC
    assert bag.require("anthropic") == ANTHROPIC
    assert bag.get("openai") is None
    assert "anthropic" in bag and "openai" not in bag
    assert len(bag) == 1 and list(bag) == ["anthropic"] and bag.names() == ("anthropic",)
    assert bag.keys_present() == {
        "openai": False,
        "anthropic": True,
        "custom": False,
    }
    with pytest.raises(KeyError):
        bag.require("openai")
    with pytest.raises(ValueError):
        bag.set("openai", "  ")
    bag.set("mycloud", SK_OR_SHAPED)
    assert bag.keys_present()["mycloud"] is True
    bag.remove("mycloud")
    assert "mycloud" not in bag
    bag.clear()
    assert len(bag) == 0 and bag.keys_present()["anthropic"] is False


def test_secret_bag_never_in_repr_str_json_or_pickle() -> None:
    bag = SecretBag({"anthropic": ANTHROPIC})
    for rendered in (repr(bag), str(bag), f"{bag}"):
        assert ANTHROPIC not in rendered
        assert REDACTED in rendered and "anthropic" in rendered
    with pytest.raises(TypeError):
        json.dumps(bag)
    with pytest.raises(TypeError):
        json.dumps({"run": {"secrets": bag}})
    with pytest.raises(TypeError):
        pickle.dumps(bag)
    # run.json only ever gets the booleans
    assert json.loads(json.dumps(bag.keys_present())) == bag.keys_present()


def test_secret_bag_identity_equality() -> None:
    a, b = (
        SecretBag({"openai": "sk-1234567890abcdefghij"}),
        SecretBag({"openai": "sk-1234567890abcdefghij"}),
    )
    assert a != b and a == a and len({a, b}) == 2


# ── SecretFilter / logging ───────────────────────────────────────────────────


def test_secret_filter_scrubs_registered_values_from_caplog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.handler.addFilter(SecretFilter())
    log = logging.getLogger("qr_llm_evals.test.secrets")
    bag = SecretBag({"anthropic": ANTHROPIC})
    with caplog.at_level(logging.DEBUG, logger="qr_llm_evals.test.secrets"):
        log.info("judge key is %s", bag.get("anthropic"))
        log.warning("bag=%r", bag)
        log.error(
            "header", extra={"authorization": f"Bearer {ANTHROPIC}", "nested": {"k": [ANTHROPIC]}}
        )
        try:
            raise RuntimeError(f"upstream said {ANTHROPIC} is invalid")
        except RuntimeError:
            log.exception("failed")
    text = caplog.text
    assert ANTHROPIC not in text
    assert text.count(REDACTED) >= 3
    formatted = [JsonFormatter().format(r) for r in caplog.records]
    assert all(ANTHROPIC not in line for line in formatted)
    exc_line = next(line for line in formatted if '"failed"' in line)
    assert "RuntimeError" in exc_line and REDACTED in exc_line
    for record in caplog.records:
        assert ANTHROPIC not in record.getMessage()
        assert ANTHROPIC not in str(record.__dict__)


def test_secret_filter_scrubs_known_token_shapes_even_when_unregistered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.handler.addFilter(SecretFilter())
    log = logging.getLogger("qr_llm_evals.test.shapes")
    with caplog.at_level(logging.INFO, logger="qr_llm_evals.test.shapes"):
        log.info(
            "launch with %s and %s and Authorization: Bearer %s",
            GOOD,
            SK_OR_SHAPED,
            "tok_" + "x" * 20,
        )
    assert GOOD not in caplog.text and SK_OR_SHAPED not in caplog.text
    assert "tok_xxxx" not in caplog.text
    assert caplog.text.count(REDACTED) == 3


def test_clear_unregisters_and_redact_handles_overlaps() -> None:
    bag = SecretBag({"openai": "sk-proj-QWERTYUIOPASDFGHJKL"})
    assert redact("k=sk-proj-QWERTYUIOPASDFGHJKL") == f"k={REDACTED}"
    bag.clear()
    # The value shape is still scrubbed by pattern; a shapeless value is not once cleared.
    plain = SecretBag({"custom": "plain-value-with-no-prefix-123"})
    assert redact("v=plain-value-with-no-prefix-123") == f"v={REDACTED}"
    plain.clear()
    assert redact("v=plain-value-with-no-prefix-123") == "v=plain-value-with-no-prefix-123"


def test_configure_logging_installs_filter_on_root_handler() -> None:
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    try:
        logging_utils.configure_logging("debug")
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert any(isinstance(f, SecretFilter) for f in handler.filters)
        assert isinstance(handler.formatter, JsonFormatter)
        assert logging_utils.install_secret_filter(handler) in handler.filters
        assert sum(isinstance(f, SecretFilter) for f in handler.filters) == 1
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
