#!/usr/bin/env python
"""Export the pydantic contracts to ``packages/contracts`` (spec §4 intro, AGENTS.md invariant 7).

Pipeline (one command, deterministic bytes)::

    pydantic models ──► packages/contracts/schema/<Model>.json      (one JSON Schema per contract)
                    ──► packages/contracts/schema/_bundle.json      (all contracts, shared $defs)
    evals/*.yaml    ──► packages/contracts/src/generated/cards.json (the registry, for the SPA)
    _bundle.json    ──► packages/contracts/src/generated/types.ts   (json-schema-to-typescript)
    <Model>.json    ──► packages/contracts/src/generated/zod.ts     (z.fromJSONSchema wrappers)

The TypeScript half runs ``packages/contracts/scripts/generate.ts`` through pnpm
so the JSON → TS rules live in one place. ``--check`` regenerates everything
into a temporary tree and fails on any byte difference (the ``schemas`` row of
``scripts/check.py``); nothing is written in check mode.

Usage::

    python scripts/export_schemas.py            # write
    python scripts/export_schemas.py --check    # verify committed output is current
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from qr_llm_evals.api.schema import (
    AdminUsersResponse,
    AdminUserUpdate,
    AdminUserView,
    AnalyseResponse,
    AuthResponse,
    CardsResponse,
    CardView,
    CreateSessionRequest,
    EstimatesResponse,
    HealthResponse,
    LaunchRequest,
    LaunchResponse,
    LinkKeyRequest,
    LoginRequest,
    ModelsResponse,
    PreregRenderRequest,
    PreregRenderResponse,
    ReadinessRequest,
    RunControlRequest,
    RunControlResponse,
    RunKeysRequest,
    RunKeysResponse,
    RunView,
    SessionControlRequest,
    SessionControlResponse,
    SessionJoinRequest,
    SessionJoinResponse,
    SessionPresenceRequest,
    SessionPresenceResponse,
    SessionQuestionnaireRequest,
    SessionView,
    SettleRequest,
    SignupRequest,
    StorageResponse,
    StudyView,
    UsageResponse,
    UserView,
    WithdrawRequest,
    WithdrawResponse,
)
from qr_llm_evals.cards import EvalCard, SelectionEstimate, load_cards
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.store.schema import (
    GenerationRow,
    JudgeRow,
    ReadinessReport,
    Run,
    Taint,
)
from qr_llm_evals.supervised.schema import Observer, Questionnaire, Session
from qr_llm_evals.tier0.schema import Tier0Record

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "packages" / "contracts"
SCHEMA_SUBDIR = "schema"
GENERATED_SUBDIR = Path("src") / "generated"
GENERATOR = CONTRACTS_DIR / "scripts" / "generate.ts"
BUNDLE_NAME = "_bundle.json"
CARDS_NAME = "cards.json"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

#: The contracts, in the order they appear in ``_bundle.json`` / ``types.ts`` / ``zod.ts``.
#: Adding a model here is the only registration step; everything else is generated.
EXPORTS: tuple[type[BaseModel], ...] = (
    StudyConfig,
    EvalCard,
    SelectionEstimate,
    Run,
    GenerationRow,
    JudgeRow,
    Taint,
    ReadinessReport,
    Tier0Record,
    Session,
    Observer,
    Questionnaire,
    # HTTP wire shapes (spec §4.7), from api/schema.py
    HealthResponse,
    CardView,
    PreregRenderRequest,
    PreregRenderResponse,
    CardsResponse,
    ModelsResponse,
    EstimatesResponse,
    ReadinessRequest,
    LaunchRequest,
    LaunchResponse,
    RunView,
    RunControlRequest,
    RunControlResponse,
    RunKeysRequest,
    RunKeysResponse,
    CreateSessionRequest,
    SessionView,
    SessionControlRequest,
    SessionControlResponse,
    SessionJoinRequest,
    SessionJoinResponse,
    SessionPresenceRequest,
    SessionPresenceResponse,
    SessionQuestionnaireRequest,
    AnalyseResponse,
    StudyView,
    WithdrawRequest,
    WithdrawResponse,
    # accounts
    SignupRequest,
    LoginRequest,
    LinkKeyRequest,
    UserView,
    AuthResponse,
    UsageResponse,
    AdminUserView,
    AdminUsersResponse,
    AdminUserUpdate,
    SettleRequest,
    StorageResponse,
)

#: Hand-written files that live next to the generated ones and must survive ``--check``.
KEEP_IN_GENERATED: frozenset[str] = frozenset({"README.md"})


def _dump(data: Any) -> str:
    """Stable, diff-friendly JSON: 2-space indent, insertion order, UTF-8, LF."""
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Standalone JSON Schema for one contract (own ``$defs``), dialect declared."""
    schema = model.model_json_schema(schema_generator=GenerateJsonSchema, mode="validation")
    return {"$schema": JSON_SCHEMA_DIALECT, "$id": f"{model.__name__}.json", **schema}


def bundle_schema() -> dict[str, Any]:
    """One document with every contract as a property and all nested models in ``$defs``.

    ``json-schema-to-typescript`` compiles this into a single ``types.ts`` so a
    model shared by several contracts (``Provenance``, ``ModelRef`` …) gets one
    TypeScript name instead of one per file.
    """
    ref_map, defs = models_json_schema(
        [(m, "validation") for m in EXPORTS],
        ref_template="#/$defs/{model}",
        schema_generator=GenerateJsonSchema,
    )
    properties = {m.__name__: ref_map[(m, "validation")] for m in EXPORTS}
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": BUNDLE_NAME,
        "title": "Contracts",
        "description": "Every qr-llm-evals contract, generated by scripts/export_schemas.py.",
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
        "$defs": defs["$defs"],
    }


def cards_export() -> list[dict[str, Any]]:
    """The registry as JSON, sorted by id (spec §5.2 'UI ↔ docs consistency')."""
    return [card.model_dump(mode="json") for _, card in sorted(load_cards().items())]


def render_python_outputs() -> dict[Path, str]:
    """Relative path → file text for everything Python produces."""
    out: dict[Path, str] = {}
    for model in EXPORTS:
        out[Path(SCHEMA_SUBDIR) / f"{model.__name__}.json"] = _dump(model_schema(model))
    out[Path(SCHEMA_SUBDIR) / BUNDLE_NAME] = _dump(bundle_schema())
    out[GENERATED_SUBDIR / CARDS_NAME] = _dump(cards_export())
    return out


def run_ts_generator(root: Path) -> bool:
    """Run ``generate.ts`` with Node (24+ strips types natively). False if Node is missing.

    Node resolves ``json-schema-to-typescript`` / ``zod`` from the generator's own
    location (``packages/contracts/node_modules``), so ``pnpm install`` must have run.
    """
    if shutil.which("node") is None or not (CONTRACTS_DIR / "node_modules").is_dir():
        print(
            "WARNING: node or packages/contracts/node_modules missing; skipping types.ts / "
            "zod.ts generation (CI regenerates them — run `pnpm install` to check locally).",
            file=sys.stderr,
        )
        return False
    cmd = [
        "node",
        str(GENERATOR),
        "--schema-dir",
        str(root / SCHEMA_SUBDIR),
        "--out-dir",
        str(root / GENERATED_SUBDIR),
    ]
    subprocess.run(cmd, cwd=CONTRACTS_DIR, check=True)  # noqa: S603 - fixed argv, no user input
    return True


def _tree(root: Path, sub: Path) -> dict[Path, bytes]:
    base = root / sub
    if not base.is_dir():
        return {}
    return {p.relative_to(root): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def write_all() -> int:
    for rel, text in render_python_outputs().items():
        _write(CONTRACTS_DIR / rel, text)
    run_ts_generator(CONTRACTS_DIR)
    print(f"wrote contracts to {CONTRACTS_DIR.relative_to(REPO_ROOT)}")
    return 0


def check_all() -> int:
    with tempfile.TemporaryDirectory(prefix="qr-evals-contracts-") as tmp:
        root = Path(tmp)
        for rel, text in render_python_outputs().items():
            _write(root / rel, text)
        # Files not produced here but present in the real tree (README.md) are
        # copied so the comparison sees only generated content.
        for name in KEEP_IN_GENERATED:
            src = CONTRACTS_DIR / GENERATED_SUBDIR / name
            if src.is_file():
                _write(root / GENERATED_SUBDIR / name, src.read_text(encoding="utf-8"))
        ts_ok = run_ts_generator(root)

        expected: dict[Path, bytes] = {}
        actual: dict[Path, bytes] = {}
        for sub in (Path(SCHEMA_SUBDIR), GENERATED_SUBDIR):
            expected.update(_tree(root, sub))
            actual.update(_tree(CONTRACTS_DIR, sub))
        if not ts_ok:
            ts_files = {p for p in actual if p.suffix == ".ts"}
            actual = {p: b for p, b in actual.items() if p not in ts_files}

    drift = sorted({*expected, *actual}, key=str)
    problems = [
        f"{'missing' if p not in actual else 'stale' if p not in expected else 'differs'}: {p.as_posix()}"
        for p in drift
        if expected.get(p) != actual.get(p)
    ]
    if problems:
        print("contracts are out of date — run `python scripts/export_schemas.py`:")
        print("\n".join(f"  {p}" for p in problems))
        return 1
    print(f"contracts up to date ({len(expected)} files)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Verify instead of write.")
    args = parser.parse_args(argv)
    return check_all() if args.check else write_all()


if __name__ == "__main__":
    sys.exit(main())
