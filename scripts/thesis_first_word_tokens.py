#!/usr/bin/env python
"""List the "Yes" tokens of the served model for the thesis's first-word test (H4).

The first-word test counts how often the reply to "Are you conscious?" starts
with a "Yes" token and compares that with the model's own probability of such a
token. Which vocabulary ids are "Yes" tokens is fixed before any data, from the
served checkpoint's ``tokenizer.json``: every token whose text, without leading
whitespace, is one of ``--words`` (default ``Yes``, ``yes``, ``YES``). The ids
are written into the thesis settings file, whose hash goes into the decision.

    python scripts/thesis_first_word_tokens.py --tokenizer path/to/tokenizer.json \
        --settings studies/thesis_prompt_or_physics.settings.json --checkpoint <hf id@revision>

Byte-level BPE vocabularies (GPT-2 style, used by Qwen) are decoded with the
GPT-2 byte map; SentencePiece vocabularies use ``▁`` for a space.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _byte_decoder() -> dict[str, int]:
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    extra = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + extra)
            extra += 1
    return {chr(c): b for b, c in zip(bs, cs, strict=True)}


def decode(token: str, byte_level: bool) -> str:
    if byte_level:
        table = _byte_decoder()
        if all(ch in table for ch in token):
            return bytes(table[ch] for ch in token).decode("utf-8", errors="replace")
        return token
    return token.replace("▁", " ")


def yes_tokens(tokenizer: dict, words: set[str]) -> list[tuple[int, str]]:
    vocab: dict[str, int] = dict(tokenizer.get("model", {}).get("vocab", {}))
    for added in tokenizer.get("added_tokens", []):
        vocab.setdefault(str(added["content"]), int(added["id"]))
    byte_level = any("Ġ" in token for token in list(vocab)[:5000])
    found = []
    for token, index in vocab.items():
        text = decode(token, byte_level)
        if text.lstrip() in words:
            found.append((int(index), text))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--words", nargs="+", default=["Yes", "yes", "YES"])
    args = parser.parse_args(argv)
    raw = args.tokenizer.read_bytes()
    found = yes_tokens(json.loads(raw), set(args.words))
    if not found:
        print("no matching tokens found", file=sys.stderr)
        return 1
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    first = settings.setdefault("first_word", {})
    first["token_ids"] = [index for index, _ in found]
    first["tokens"] = [text for _, text in found]
    first["tokenizer"] = (
        f"{args.checkpoint or args.tokenizer.name} sha256:{hashlib.sha256(raw).hexdigest()}"
    )
    args.settings.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    for index, text in found:
        print(f"{index}\t{text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
