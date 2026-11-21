"""The thesis study's operator tools: the device byte dump, the registration
text and the decision figures. No entropy server, no engine, no network.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from qr_llm_evals.cards import REPO_ROOT

STUDY_DIR = REPO_ROOT / "studies" / "thesis_prompt_or_physics"
sys.path.insert(0, str(STUDY_DIR))

import thesis_device_check  # noqa: E402
import thesis_device_dump as dump  # noqa: E402
import thesis_figures  # noqa: E402
import thesis_report  # noqa: E402

BLOCK = dump.REQUEST_BYTES


def _philox_fetch(seed: int = 42) -> tuple[dump.Fetch, list[int]]:
    """A fair "device": block k is Philox(seed, k), the seeded lane's generator."""
    calls: list[int] = []

    def fetch(n: int) -> tuple[bytes, str]:
        k = len(calls)
        calls.append(n)
        generator = np.random.Generator(np.random.Philox(key=seed, counter=[0, 0, 0, k]))
        return generator.bytes(n), "dragonfly-0"

    return fetch, calls


# ── the wire format ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("n", "wire"),
    [(1, b"\x08\x01"), (127, b"\x08\x7f"), (128, b"\x08\x80\x01"), (10_000, b"\x08\x90\x4e")],
)
def test_request_is_field_one_as_a_varint(n: int, wire: bytes) -> None:
    assert dump.encode_request(n) == wire


def test_reply_decoding_takes_the_bytes_and_the_device_and_skips_the_rest() -> None:
    payload = bytes(range(256)) * 2
    reply = (
        b"\x0a" + dump._varint(len(payload)) + payload  # field 1: data
        + b"\x10\x07"  # field 2: sequence id (varint), skipped
        + b"\x18\xd2\x85\xd8\xcc\x04"  # field 3: timestamp (varint), skipped
        + b"\x22\x0bdragonfly-0"  # field 4: device id
        + b"\x2d\x01\x02\x03\x04"  # an unknown fixed32 field, skipped
    )  # fmt: skip
    assert dump.decode_reply(reply) == (payload, "dragonfly-0")
    assert dump.decode_reply(b"") == (b"", "")


def test_env_file_is_read_without_printing_the_key(tmp_path: Path) -> None:
    env = tmp_path / "qr-server.env"
    env.write_text(
        "# comment\n\nQR_GRPC_SERVER_ADDRESS=unix:///run/qbert0g/qbert0g.sock\n"
        'QR_GRPC_API_KEY="k-0123"\nNOT_A_PAIR\n',
        encoding="utf-8",
    )
    values = dump.read_env_file(env)
    assert values["QR_GRPC_SERVER_ADDRESS"] == "unix:///run/qbert0g/qbert0g.sock"
    assert values["QR_GRPC_API_KEY"] == "k-0123" and "NOT_A_PAIR" not in values


# ── the dump ─────────────────────────────────────────────────────────────────


def test_dump_writes_whole_requests_and_can_be_continued(tmp_path: Path) -> None:
    out = tmp_path / "device" / "device.bin"
    fetch, calls = _philox_fetch()
    lines: list[str] = []
    dump.dump(fetch, out, 3 * BLOCK, progress=lines.append)
    assert out.stat().st_size == 3 * BLOCK and calls == [BLOCK] * 3

    # a later run with a larger target carries on from where the file ends
    summary = dump.dump(fetch, out, 5 * BLOCK, progress=lines.append)
    assert out.stat().st_size == 5 * BLOCK and len(calls) == 5
    assert any("continuing" in line for line in lines)
    assert summary["bytes"] == 5 * BLOCK and summary["requests_this_run"] == 2
    assert summary["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert summary["device_ids_this_run"] == ["dragonfly-0"]
    saved = json.loads((tmp_path / "device" / "device.bin.json").read_text(encoding="utf-8"))
    assert saved == summary
    # the file is exactly the blocks, in order
    expected, _ = _philox_fetch()
    assert out.read_bytes() == b"".join(expected(BLOCK)[0] for _ in range(5))


def test_dump_refuses_a_file_it_did_not_write_and_bad_sizes(tmp_path: Path) -> None:
    fetch, _calls = _philox_fetch()
    odd = tmp_path / "odd.bin"
    odd.write_bytes(b"x" * 123)
    with pytest.raises(SystemExit, match="not a whole number"):
        dump.dump(fetch, odd, BLOCK, progress=lambda _line: None)
    assert odd.read_bytes() == b"x" * 123, "the file is left as it was"
    with pytest.raises(SystemExit, match="multiple of the request size"):
        dump.dump(fetch, tmp_path / "new.bin", BLOCK + 1, progress=lambda _line: None)
    full = tmp_path / "full.bin"
    dump.dump(fetch, full, 2 * BLOCK, progress=lambda _line: None)
    with pytest.raises(SystemExit, match="more than --bytes"):
        dump.dump(fetch, full, BLOCK, progress=lambda _line: None)


def test_dump_retries_then_stops_with_what_it_has(tmp_path: Path) -> None:
    good, _calls = _philox_fetch()
    attempts = 0

    def flaky(n: int) -> tuple[bytes, str]:
        nonlocal attempts
        attempts += 1
        if attempts in (2, 3):  # the second block fails twice, then works
            raise ConnectionError("server busy")
        return good(n)

    out = tmp_path / "flaky.bin"
    waits: list[float] = []
    dump.dump(flaky, out, 2 * BLOCK, sleep=waits.append, progress=lambda _line: None)
    assert out.stat().st_size == 2 * BLOCK and waits == [1.0, 2.0]

    def dead(n: int) -> tuple[bytes, str]:
        raise ConnectionError("gone")

    with pytest.raises(SystemExit, match="run again to continue"):
        dump.dump(dead, out, 3 * BLOCK, sleep=lambda _s: None, progress=lambda _line: None)
    assert out.stat().st_size == 2 * BLOCK, "nothing partial was appended"

    def short(n: int) -> tuple[bytes, str]:
        return b"\x00" * (n - 1), "dragonfly-0"

    with pytest.raises(SystemExit, match="the server sent"):
        dump.dump(short, out, 3 * BLOCK, progress=lambda _line: None)
    assert out.stat().st_size == 2 * BLOCK


def test_a_dump_feeds_the_device_check(tmp_path: Path) -> None:
    out = tmp_path / "device.bin"
    fetch, _calls = _philox_fetch()
    dump.dump(fetch, out, 400 * BLOCK, progress=lambda _line: None)
    report = thesis_device_check.characterise(
        thesis_device_check.blocks_from_file(out, BLOCK, None)
    )
    # a fair generator: byte mean near 127.5, as the check reports it
    text = json.dumps(report, default=str)
    assert "127." in text and 400 in _numbers(report)


def _numbers(value: Any) -> set[float]:
    found: set[float] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _numbers(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= _numbers(item)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        found.add(float(value))
    return found


# ── the registration text ────────────────────────────────────────────────────


def _lock(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    lock = tmp_path / "registration"
    lock.mkdir()
    hashes = {
        key: hashlib.sha256(key.encode()).hexdigest() for key in thesis_report.REGISTRATION_KEYS
    }
    (lock / "hashes.json").write_text(json.dumps(hashes), encoding="utf-8")
    (lock / "config.json").write_text(
        json.dumps(
            {
                "title": "Prompt or Physics?",
                "authors": ["A. Author"],
                "contact": "a@example.invalid",
            }
        ),
        encoding="utf-8",
    )
    return lock, hashes


def test_register_writes_the_four_hashes_and_how_to_check_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock, hashes = _lock(tmp_path)
    assert thesis_report.main(["register", "--lock", str(lock), "--commit", "abc1234"]) == 0
    text = (lock / "REGISTRATION.md").read_text(encoding="utf-8")
    assert text.startswith("# Registration: Prompt or Physics?")
    assert all(value in text for value in hashes.values())
    assert "A. Author" in text and "commit abc1234" in text
    assert "## How to check a hash" in text and "\r" not in text
    assert capsys.readouterr().out.strip().startswith("# Registration")


def test_register_refuses_an_incomplete_lock(tmp_path: Path) -> None:
    lock, hashes = _lock(tmp_path)
    (lock / "hashes.json").write_text(
        json.dumps({**hashes, "plan_hash": "short"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="plan_hash"):
        thesis_report.main(["register", "--lock", str(lock)])
    with pytest.raises(SystemExit, match="not a lock folder"):
        thesis_report.main(["register", "--lock", str(tmp_path / "nowhere")])


# ── the figures ──────────────────────────────────────────────────────────────


def _report() -> dict[str, Any]:
    def sampler(distance: float, epsilon: float | None = None) -> dict[str, Any]:
        block: dict[str, Any] = {
            "u_tolerance": {"ks_distance": distance, "upper_bound": distance + 0.01}
        }
        if epsilon is not None:
            block["epsilon"] = epsilon
        return block

    def first(observed: float, expected: float) -> dict[str, Any]:
        return {
            "available": True,
            "observed_rate": observed,
            "device_adjusted": {"expected_rate": expected},
        }

    return {
        "tests": {
            "H0": {
                "tolerance": 0.005,
                "arms": {"qrng": sampler(0.004)},
                "reference_arms": {
                    "prng_seed_1": sampler(0.006),
                    "prng_bent_050": sampler(0.2, epsilon=0.5),
                },
            },
            "H1": {"available": True, "effect": 0.29, "ci_lower": 0.2, "ci_upper": 0.39},
            "H2": {
                "available": True,
                "effect": -0.01,
                "ci_lower": -0.11,
                "ci_upper": 0.09,
                "equivalence": {"margin": 0.05},
            },
            "H4": {
                "arms": {
                    "qrng": first(0.46, 0.40),
                    "prng_seed_1": first(0.37, 0.30),
                    "prng_bent_050": first(0.15, 0.19),
                }
            },
        }
    }


def test_arm_kinds_follow_the_report_then_the_name() -> None:
    assert thesis_figures.arm_kind("qrng") == "quantum"
    assert thesis_figures.arm_kind("prng_seed_7") == "seeded"
    assert thesis_figures.arm_kind("prng_bent_010") == "bent"
    assert thesis_figures.arm_kind("control_a", {"epsilon": 0.1}) == "bent"
    assert set(thesis_figures.KINDS) == {"quantum", "seeded", "bent"}
    assert len({marker for _colour, marker, _label in thesis_figures.KINDS.values()}) == 3


def test_figures_are_drawn_from_the_decisions_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("matplotlib")
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps(_report()), encoding="utf-8")
    assert thesis_report.main(["figures", "--decisions", str(decisions)]) == 0
    out = tmp_path / "figures"
    names = sorted(path.name for path in out.iterdir())
    assert names == [
        "effects.png", "effects.svg", "first_word.png", "first_word.svg",
        "sampler.png", "sampler.svg",
    ]  # fmt: skip
    assert all((out / name).stat().st_size > 2000 for name in names)
    svg = (out / "sampler.svg").read_text(encoding="utf-8")
    assert "<svg" in svg and "Date" not in svg.split("</metadata>")[0].split("<dc:date>")[0][-200:]
    assert len(capsys.readouterr().out.splitlines()) == 6

    # the same report draws the same SVG again: no timestamps, fixed ids
    first = (out / "effects.svg").read_bytes()
    thesis_figures.effects_figure(_report(), out)
    assert (out / "effects.svg").read_bytes() == first


def test_figures_skip_what_the_report_cannot_support(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    report = _report()
    report["tests"]["H4"] = {"arms": {}}
    report["tests"]["H1"]["available"] = False
    written = thesis_figures.write_figures(report, tmp_path)
    assert {path.stem for path in written} == {"effects", "sampler"}  # H2 alone still draws

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"tests": {}}), encoding="utf-8")
    assert thesis_report.main(["figures", "--decisions", str(empty)]) == 1
