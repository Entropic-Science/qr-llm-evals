#!/usr/bin/env python
"""Dump raw bytes from the entropy server for the device check ("Prompt or Physics?" thesis).

``thesis_device_check.py --bytes`` characterises the quantum device from a file
of its raw output. This script writes that file. It asks the entropy server
(Qbert0G) for bytes with the same ``GetEntropy`` call qr-sampler makes for
every token, in requests of the same size (10,000 bytes), so the dump is what
the sampler would have been given.

Run it on the box, with an interpreter that has ``grpcio`` (the engine's
environment does), while no study is generating: the device serves one request
at a time, so a dump and a run would slow each other down.

    sudo /opt/vllm-venv/bin/python studies/thesis_prompt_or_physics/thesis_device_dump.py \\
        --out /var/lib/qr-llm-evals/device/device.bin --bytes 2000000000
    python studies/thesis_prompt_or_physics/thesis_device_check.py \\
        --bytes /var/lib/qr-llm-evals/device/device.bin --out device_check.json

The server address and the API key are read from the engine's environment
file (``--env-file``, default ``/etc/qr-server/qr-server.env``:
``QR_GRPC_SERVER_ADDRESS`` and ``QR_GRPC_API_KEY``). The key is never printed
and never written to the output. Reading that file is why the example uses
``sudo``.

The dump can be interrupted and continued: bytes are appended, and a second
run with the same ``--out`` carries on to ``--bytes``. When the target is
reached the script writes ``<out>.json`` with the SHA-256 of the file, the
request size, the number of requests and the device ids the server named.
Two gigabytes are 200,000 requests; the server's default daily allowance is
100 GiB.

This is the one place where the study talks to the entropy server directly.
The harness itself never does (``infra/ENV_REFERENCE.md``); this is an
operator tool outside the ``qr_llm_evals`` package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ENV_FILE = Path("/etc/qr-server/qr-server.env")
DEFAULT_ADDRESS = "unix:///run/qbert0g/qbert0g.sock"
METHOD = "/qr_entropy.EntropyService/GetEntropy"
KEY_HEADER = "api-key"
#: What qr-sampler draws per token (thesis_device_check.SAMPLE_COUNT).
REQUEST_BYTES = 10_000
DEFAULT_TOTAL = 2_000_000_000
MAX_RETRIES = 5

#: ``fetch(n) -> (payload, device_id)``
Fetch = Callable[[int], tuple[bytes, str]]


# ── the two protobuf messages, by hand (no generated code needed) ────────────


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_request(n: int) -> bytes:
    """``EntropyRequest{bytes_needed = n}``: field 1, varint."""
    return b"\x08" + _varint(n)


def _read_varint(raw: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = raw[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def decode_reply(raw: bytes) -> tuple[bytes, str]:
    """``EntropyResponse`` → (``data`` field 1, ``device_id`` field 4)."""
    data = b""
    device = ""
    pos = 0
    while pos < len(raw):
        tag, pos = _read_varint(raw, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            _, pos = _read_varint(raw, pos)
        elif wire == 2:
            length, pos = _read_varint(raw, pos)
            chunk = raw[pos : pos + length]
            pos += length
            if field == 1:
                data = chunk
            elif field == 4:
                device = chunk.decode("utf-8", "replace")
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError(f"unexpected wire type {wire} in the server's reply")
    return data, device


# ── configuration ────────────────────────────────────────────────────────────


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines of a systemd environment file (comments and blanks skipped)."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def grpc_fetch(address: str, api_key: str, timeout_s: float) -> Fetch:
    try:
        import grpc
    except ImportError as err:  # pragma: no cover - depends on the interpreter used
        raise SystemExit(
            "error: this interpreter has no grpcio; run the script with the engine's "
            "environment, for example /opt/vllm-venv/bin/python"
        ) from err
    channel = grpc.insecure_channel(
        address,
        options=[("grpc.max_receive_message_length", 16 * 1024 * 1024)],
    )
    call = channel.unary_unary(METHOD, request_serializer=None, response_deserializer=None)
    metadata = ((KEY_HEADER, api_key),)

    def fetch(n: int) -> tuple[bytes, str]:
        reply: bytes = call(encode_request(n), metadata=metadata, timeout=timeout_s)
        return decode_reply(reply)

    return fetch


# ── the dump ─────────────────────────────────────────────────────────────────


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dump(
    fetch: Fetch,
    out: Path,
    total: int,
    *,
    request_bytes: int = REQUEST_BYTES,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = print,
) -> dict[str, object]:
    """Append whole requests to ``out`` until it holds ``total`` bytes; returns the summary."""
    if total % request_bytes:
        raise SystemExit(f"error: --bytes must be a multiple of the request size ({request_bytes})")
    out.parent.mkdir(parents=True, exist_ok=True)
    have = out.stat().st_size if out.exists() else 0
    if have % request_bytes:
        raise SystemExit(
            f"error: {out} holds {have} bytes, which is not a whole number of {request_bytes}-byte "
            "requests; it was not written by this script with this request size"
        )
    if have > total:
        raise SystemExit(f"error: {out} already holds {have} bytes, more than --bytes {total}")
    if have:
        progress(f"continuing {out} at {have:,} of {total:,} bytes")
    started = datetime.now(UTC)
    devices: set[str] = set()
    requests = 0
    last_report = time.monotonic()
    with out.open("ab") as fh:
        while have < total:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    data, device = fetch(request_bytes)
                    break
                except Exception as err:  # grpc.RpcError or a transport error
                    if attempt == MAX_RETRIES:
                        fh.flush()
                        raise SystemExit(
                            f"error: the entropy server failed {MAX_RETRIES + 1} times in a row "
                            f"({type(err).__name__}); {have:,} bytes are saved, run again to continue"
                        ) from err
                    sleep(min(30.0, 2.0**attempt))
            if len(data) != request_bytes:
                raise SystemExit(
                    f"error: asked for {request_bytes} bytes, the server sent {len(data)}; "
                    "nothing from this reply was written"
                )
            fh.write(data)
            have += request_bytes
            requests += 1
            if device:
                devices.add(device)
            now = time.monotonic()
            if now - last_report >= 30.0:
                progress(f"  {have:,} of {total:,} bytes ({100.0 * have / total:.1f}%)")
                last_report = now
    summary: dict[str, object] = {
        "file": out.name,
        "bytes": have,
        "sha256": file_sha256(out),
        "request_bytes": request_bytes,
        "requests_this_run": requests,
        "device_ids_this_run": sorted(devices),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "method": METHOD,
    }
    out.with_name(out.name + ".json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    progress(f"done: {have:,} bytes, sha256 {summary['sha256']}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="file to append the bytes to")
    parser.add_argument("--bytes", type=int, default=DEFAULT_TOTAL, help="total size to reach")
    parser.add_argument(
        "--request-bytes",
        type=int,
        default=REQUEST_BYTES,
        help="bytes per request; keep the default, it is what the sampler draws per token",
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--address", help="server address (default: from the environment file)")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    args = parser.parse_args(argv)
    if args.bytes <= 0 or args.request_bytes <= 0:
        raise SystemExit("error: --bytes and --request-bytes must be positive")
    try:
        env = read_env_file(args.env_file)
    except OSError as err:
        raise SystemExit(f"error: cannot read {args.env_file}: {err.strerror}") from err
    api_key = env.get("QR_GRPC_API_KEY", "")
    if not api_key or api_key.startswith("CHANGEME"):
        raise SystemExit(f"error: {args.env_file} has no QR_GRPC_API_KEY")
    address = args.address or env.get("QR_GRPC_SERVER_ADDRESS") or DEFAULT_ADDRESS
    print(f"entropy server: {address}; request size {args.request_bytes:,} bytes")
    dump(
        grpc_fetch(address, api_key, args.timeout),
        args.out,
        args.bytes,
        request_bytes=args.request_bytes,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
