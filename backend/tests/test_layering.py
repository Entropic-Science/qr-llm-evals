"""Import-linter-style enforcement of the AGENTS.md layering rule (spec §3, §1.2).

Rule (imports point down only)::

    config, client, keys, cards, models   layer 0: no internal imports except config
    tier0, store, prereg                  layer 1
    runner, inspect_bridge, supervised,   layer 2
    readiness
    analysis                              layer 2: store/cards/tier0 only; analysis.stats is a leaf
    api                                   top: nothing imports api
    entrypoint.py, preflight.py           process entry points: may import anything

Plus the forbidden-dependency rule: no ``openai``/``vllm``/``modal`` anywhere;
``torch``/``transformers``/``sentence_transformers`` only under
``analysis/metrics_heavy``. The test walks every module with ``ast`` (no imports
executed) so it also covers modules whose optional dependencies are absent.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

PACKAGE = "qr_llm_evals"
SRC = Path(__file__).resolve().parents[1] / "src" / PACKAGE

LAYER0: frozenset[str] = frozenset({"client", "keys", "cards", "models", "accounts"})
LAYER1: frozenset[str] = frozenset({"tier0", "store", "prereg"})

#: subpackage -> internal subpackages it may import (``config`` is always allowed).
ALLOWED: dict[str, frozenset[str]] = {
    **{name: frozenset() for name in LAYER0},
    **{name: LAYER0 for name in LAYER1},
    "runner": LAYER0 | LAYER1,
    "inspect_bridge": LAYER0 | LAYER1,
    "supervised": LAYER0 | LAYER1,
    "readiness": LAYER0 | LAYER1,
    "analysis": frozenset({"store", "cards", "tier0"}),
    "api": LAYER0 | LAYER1 | {"runner", "inspect_bridge", "supervised", "readiness", "analysis"},
}

#: Root modules that sit *above* ``api``: the process entry points. They may
#: import any subpackage; every other root module (``config``, ``logging_utils``)
#: must import none.
ENTRY_POINTS: frozenset[str] = frozenset({"entrypoint.py", "preflight.py"})

#: Third-party roots that must never be imported anywhere in the backend.
FORBIDDEN_EVERYWHERE: frozenset[str] = frozenset({"openai", "vllm", "modal"})
#: Heavy roots allowed only under analysis/metrics_heavy (imported lazily).
HEAVY: frozenset[str] = frozenset({"torch", "transformers", "sentence_transformers"})
HEAVY_HOME = ("analysis", "metrics_heavy")


def _import_targets(node: ast.stmt, pkg_parts: list[str]) -> list[str]:
    targets: list[list[str]] = []
    if isinstance(node, ast.Import):
        targets.extend(alias.name.split(".") for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.level == 0:
            if node.module:
                targets.append(node.module.split("."))
        else:
            base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
            mod = node.module.split(".") if node.module else []
            resolved = base + mod
            targets.append(resolved)
            targets.extend(resolved + [alias.name] for alias in node.names)
    return [".".join(t) for t in targets]


def _iter_module_imports() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        pkg_parts = [PACKAGE, *rel.parts[:-1]]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                found.extend((rel, tgt) for tgt in _import_targets(node, pkg_parts))
    return found


def _subpackage_of(rel: Path) -> str | None:
    return rel.parts[0] if len(rel.parts) > 1 else None


def test_layering_rule() -> None:
    violations: list[str] = []
    for rel, target in _iter_module_imports():
        if not target.startswith(PACKAGE):
            continue
        parts = target.split(".")
        target_sub = parts[1] if len(parts) > 1 else None
        source_sub = _subpackage_of(rel)
        if target_sub is None or target_sub == "config" or target_sub not in ALLOWED:
            continue  # root package, config, or a plain module: always fine
        if source_sub is None:
            if rel.name in ENTRY_POINTS:
                continue
            violations.append(f"{rel}: root package must not import subpackage '{target_sub}'")
        elif target_sub == source_sub:
            continue
        elif target_sub == "api":
            violations.append(f"{rel}: nothing may import 'api'")
        elif target_sub == "analysis" and parts[2:3] == ["stats"]:
            violations.append(f"{rel}: 'analysis.stats' is a leaf; import it only from analysis")
        elif target_sub not in ALLOWED[source_sub]:
            violations.append(
                f"{rel}: '{source_sub}' may not import '{target_sub}' "
                f"(allowed: {sorted(ALLOWED[source_sub]) or 'nothing internal'})"
            )
    assert not violations, "layering violations:\n" + "\n".join(violations)


def test_analysis_stats_is_a_leaf() -> None:
    for rel, target in _iter_module_imports():
        if rel.parts[:2] in {("analysis", "stats.py"), ("analysis", "stats")}:
            assert not target.startswith(PACKAGE), (
                f"{rel}: analysis.stats imports internal {target}"
            )


def test_forbidden_dependencies() -> None:
    offenders: list[str] = []
    for rel, target in _iter_module_imports():
        root = target.split(".")[0]
        if root in FORBIDDEN_EVERYWHERE:
            offenders.append(f"{rel}: imports forbidden '{root}'")
        elif root in HEAVY and rel.parts[:2] != HEAVY_HOME:
            offenders.append(f"{rel}: '{root}' is allowed only under analysis/metrics_heavy")
    assert not offenders, "forbidden dependencies:\n" + "\n".join(offenders)


def test_subpackages_on_disk_are_declared() -> None:
    """Every subpackage present must be in the layer table (the table may lead the code)."""
    on_disk = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    unknown = on_disk - set(ALLOWED)
    assert not unknown, f"undeclared subpackages {sorted(unknown)}; add them to ALLOWED"


def test_package_imports_side_effect_free() -> None:
    """The root package and every present subpackage import without side effects."""
    importlib.import_module(PACKAGE)
    importlib.import_module(f"{PACKAGE}.config")
    for sub in sorted(ALLOWED):
        if (SRC / sub / "__init__.py").exists():
            importlib.import_module(f"{PACKAGE}.{sub}")
