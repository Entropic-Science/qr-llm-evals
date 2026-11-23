"""The log filter keeps scrubbing a key while any run still holds it."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.logging_utils import REDACTED, _clear_registry_for_tests, redact

KEY = "shared-key-of-no-known-shape-41"
LINE = f"upstream said: {KEY}"


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    _clear_registry_for_tests()
    yield
    _clear_registry_for_tests()


def test_a_key_two_runs_hold_is_scrubbed_until_both_have_ended() -> None:
    first = SecretBag({"anthropic": KEY})
    second = SecretBag({"anthropic": KEY})
    first.clear()
    assert redact(LINE) == f"upstream said: {REDACTED}"
    second.clear()
    assert redact(LINE) == LINE


def test_setting_the_same_key_again_does_not_hold_it_twice() -> None:
    bag = SecretBag({"anthropic": KEY})
    bag.set("anthropic", KEY)
    bag.clear()
    assert redact(LINE) == LINE


def test_one_bag_with_the_key_in_two_slots_lets_go_slot_by_slot() -> None:
    bag = SecretBag({"anthropic": KEY, "custom": KEY})
    bag.remove("custom")
    assert redact(LINE) == f"upstream said: {REDACTED}"
    bag.remove("anthropic")
    assert redact(LINE) == LINE


def test_a_replaced_key_is_let_go_and_the_new_one_held() -> None:
    bag = SecretBag({"anthropic": KEY})
    bag.set("anthropic", KEY + "-new")
    assert redact(LINE) == LINE
    assert redact(f"x {KEY}-new") == f"x {REDACTED}"
