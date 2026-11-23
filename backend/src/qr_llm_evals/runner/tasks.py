"""Thin-runner task-module protocol and loader (spec §2.5, ``docs/add-an-eval.md`` §4).

A thin card ships ``evals/<id>/task.py`` exposing three pure functions —
``build_items()``, ``build_messages(item, told)``, ``score(sample)`` — and
optionally ``judge_prompt(sample)`` and ``judge_fields(sample, raw)`` for
judged cards. The module must not
import the engine client, read the environment or do network I/O at import
time; :func:`load_task_module` imports it from its file path (not
``sys.path``) so the registry is not a Python package and cards cannot import
each other by accident.

``evals/_shared/`` holds the told/actual preambles every told-actual card
prepends (:func:`told_preamble`) so the wording is identical across cards and
the *actual* source is never mentioned.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, runtime_checkable

from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.models.schema import CardOverrides
from qr_llm_evals.store.schema import ToldCondition

#: ``None`` for cards without the told factor.
Told = ToldCondition | None
#: The told cells a told/actual card runs per arm (spec §4.3).
TOLD_CONDITIONS: tuple[Told, ...] = ("told_qrng", "told_prng", "told_nothing")
SHARED_DIR_NAME = "_shared"
_MODULE_PREFIX = "qr_llm_evals_task_"

Item = dict[str, Any]
Messages = list[dict[str, str]]


class TaskModuleError(ValueError):
    """The task module is missing, malformed or violates the protocol."""


@runtime_checkable
class TaskModule(Protocol):
    """What ``evals/<id>/task.py`` must provide (``judge_prompt`` is optional)."""

    def build_items(self) -> list[Item]: ...

    def build_messages(self, item: Item, told: Told = None) -> Messages: ...

    def score(self, sample: Mapping[str, Any]) -> dict[str, float | int | str | None]: ...


@dataclass(frozen=True, slots=True)
class LoadedTask:
    """A validated task module bound to its card."""

    card: EvalCard
    module: ModuleType
    path: Path

    def build_items(self, **context: Any) -> list[Item]:
        """Build items, injecting only context parameters explicitly accepted by a derived card."""
        fn = self.module.build_items
        signature = inspect.signature(fn)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        supplied = (
            context
            if accepts_kwargs
            else {name: value for name, value in context.items() if name in signature.parameters}
        )
        items = fn(**supplied)
        problems = item_problems(items)
        if problems:
            raise TaskModuleError(f"{self.card.id}: build_items(): " + "; ".join(problems))
        return list(items)

    def build_messages(self, item: Item, told: Told = None) -> Messages:
        messages = self.module.build_messages(item, told)
        problems = message_problems(messages)
        if problems:
            raise TaskModuleError(f"{self.card.id}: build_messages(): " + "; ".join(problems))
        return [dict(m) for m in messages]

    def score(self, sample: Mapping[str, Any]) -> dict[str, float | int | str | None]:
        result = self.module.score(dict(sample))
        if not isinstance(result, Mapping):
            raise TaskModuleError(f"{self.card.id}: score() must return a mapping")
        return dict(result)

    @property
    def has_judge_prompt(self) -> bool:
        return callable(getattr(self.module, "judge_prompt", None))

    def judge_prompt(self, sample: Mapping[str, Any]) -> str:
        """The judge's user turn; falls back to :func:`default_judge_prompt`."""
        fn: Callable[[Mapping[str, Any]], str] | None = getattr(self.module, "judge_prompt", None)
        text = fn(dict(sample)) if callable(fn) else default_judge_prompt(sample)
        if not isinstance(text, str) or not text.strip():
            raise TaskModuleError(f"{self.card.id}: judge_prompt() must return non-empty text")
        return text

    @property
    def has_judge_fields(self) -> bool:
        return callable(getattr(self.module, "judge_fields", None))

    def judge_fields(self, sample: Mapping[str, Any], raw: str) -> dict[str, float | None] | None:
        """Named values read from the judge's reply, or ``None`` when it cannot be read.

        For a card whose rubric asks the judge for more than one number. The
        ``score`` key is the sample's judge score; every other key is stored
        beside it (``JudgeRow.fields``) and reaches the analysis under its own
        name. A value is a number, or ``None`` where it does not apply to this
        sample. ``None`` for the whole reply, or no numeric ``score``, records
        a reply that could not be read: it is kept without a score and the
        judge is not asked again. Raising here records a failed judgement,
        which a resumed run tries again.
        """
        fn = getattr(self.module, "judge_fields", None)
        if not callable(fn):
            raise TaskModuleError(f"{self.card.id}: task module has no judge_fields()")
        result = fn(dict(sample), raw)
        if result is None:
            return None
        if not isinstance(result, Mapping):
            raise TaskModuleError(f"{self.card.id}: judge_fields() must return a mapping or None")
        fields: dict[str, float | None] = {}
        for key, value in result.items():
            number = isinstance(value, int | float) and not isinstance(value, bool)
            if not isinstance(key, str) or not (value is None or number):
                raise TaskModuleError(
                    f"{self.card.id}: judge_fields() must map str names to numbers or None "
                    f"(got {key!r}: {value!r})"
                )
            fields[key] = None if value is None else float(value)
        return fields


# ── loading ──────────────────────────────────────────────────────────────────


def task_module_path(card: EvalCard, repo_root: Path | None = None) -> Path:
    if card.engine != "thin" or not card.task_module:
        raise TaskModuleError(f"card {card.id!r} is not a thin card with a task_module")
    root = repo_root if repo_root is not None else REPO_ROOT
    return root / card.task_module


def load_task_module(card: EvalCard, repo_root: Path | None = None) -> LoadedTask:
    """Import ``card.task_module`` from its file and check the protocol surface."""
    path = task_module_path(card, repo_root)
    if not path.is_file():
        raise TaskModuleError(f"card {card.id!r}: task module {path} does not exist")
    name = f"{_MODULE_PREFIX}{card.id}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TaskModuleError(f"card {card.id!r}: cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Registering under a unique name lets dataclasses / pickling inside the
    # module resolve; it never collides with an installed package.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as err:
        sys.modules.pop(name, None)
        raise TaskModuleError(f"card {card.id!r}: importing {path} failed: {err}") from err
    missing = [
        fn
        for fn in ("build_items", "build_messages", "score")
        if not callable(getattr(module, fn, None))
    ]
    if missing:
        raise TaskModuleError(f"card {card.id!r}: task module lacks {missing}")
    return LoadedTask(card=card, module=module, path=path)


def item_problems(items: Any) -> list[str]:
    if not isinstance(items, Sequence) or isinstance(items, str | bytes):
        return ["must return a list of dicts"]
    problems: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            problems.append(f"item {i} is not a dict")
            continue
        pid = item.get("prompt_id")
        if not isinstance(pid, str) or not pid or "/" in pid or "|" in pid:
            problems.append(f"item {i}: prompt_id must be a non-empty str without '/' or '|'")
        elif pid in seen:
            problems.append(f"duplicate prompt_id {pid!r}")
        else:
            seen.add(pid)
    return problems


def message_problems(messages: Any) -> list[str]:
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes) or not messages:
        return ["must return a non-empty list of {role, content} dicts"]
    problems: list[str] = []
    for i, m in enumerate(messages):
        if not isinstance(m, Mapping) or not isinstance(m.get("role"), str):
            problems.append(f"message {i} needs a str role")
        elif not isinstance(m.get("content"), str):
            problems.append(f"message {i} needs str content")
    return problems


# ── item selection ───────────────────────────────────────────────────────────


def effective_k(card: EvalCard, overrides: CardOverrides | None = None) -> int:
    """Samples per prompt after the study's override, clamped to ``k_bounds``."""
    k = card.k if card.k is not None else 1
    if overrides is not None and overrides.k is not None:
        k = overrides.k
        if card.k_bounds is not None:
            lo, hi = card.k_bounds
            if not lo <= k <= hi:
                raise TaskModuleError(
                    f"card {card.id!r}: k override {k} outside k_bounds [{lo}, {hi}]"
                )
    return max(1, k)


def select_items(
    items: Sequence[Item], card: EvalCard, overrides: CardOverrides | None = None
) -> list[Item]:
    """The first ``n`` items in module order (``n`` = override or the card's ``dataset.items``)."""
    n = len(items)
    if card.dataset.items:
        n = min(n, card.dataset.items)
    if overrides is not None and overrides.items is not None:
        # A derived card has as many items as the run's records allow, so an
        # override above that is a cap, not an error.
        if overrides.items > len(items) and card.dataset.scheme != "derived":
            raise TaskModuleError(
                f"card {card.id!r}: items override {overrides.items} exceeds {len(items)} available"
            )
        n = min(n, overrides.items)
    return list(items[:n])


def told_conditions(card: EvalCard) -> tuple[Told, ...]:
    return TOLD_CONDITIONS if card.told_actual else (None,)


# ── shared preambles ─────────────────────────────────────────────────────────


def shared_dir(repo_root: Path | None = None) -> Path:
    root = repo_root if repo_root is not None else REPO_ROOT
    return root / "evals" / SHARED_DIR_NAME


@lru_cache(maxsize=64)
def _read_shared(path: Path) -> str:
    if not path.is_file():
        raise TaskModuleError(f"shared file {path} does not exist")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()


def load_shared(name: str, repo_root: Path | None = None) -> str:
    """Text of ``evals/_shared/<name>`` (LF-normalised, stripped)."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise TaskModuleError(f"shared name {name!r} must be a bare file name")
    return _read_shared(shared_dir(repo_root) / name)


def told_preamble(told: Told, repo_root: Path | None = None) -> str:
    """The frozen preamble for a told condition (``""`` when ``told`` is ``None``)."""
    if told is None:
        return ""
    return load_shared(f"{told}.md", repo_root)


def shared_hash(repo_root: Path | None = None) -> str:
    """sha256 over every ``evals/_shared/*`` file (name + LF bytes) — frozen in the prereg."""
    digest = hashlib.sha256()
    root = shared_dir(repo_root)
    for path in sorted(root.glob("*")) if root.is_dir() else []:
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
            digest.update(b"\0")
    return digest.hexdigest()


# ── helpers shared with the orchestrator / judge phase ───────────────────────


def messages_hash(messages: Sequence[Mapping[str, Any]]) -> str:
    """``sha256`` of the canonical JSON of the messages (``GenerationRow.messages_hash``)."""
    payload = json.dumps(
        [dict(m) for m in messages], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prompt_preview(messages: Sequence[Mapping[str, Any]], limit: int = 120) -> str:
    """First ``limit`` chars of the last user turn (``sample.started`` event)."""
    for m in reversed(messages):
        if m.get("role") == "user":
            text = str(m.get("content") or "")
            return text if len(text) <= limit else text[: limit - 1] + "…"
    return ""


def default_judge_prompt(sample: Mapping[str, Any]) -> str:
    """Generic judge user turn: the prompt's last user message and the model's answer."""
    item = sample.get("item") or {}
    prompt = str(item.get("prompt") or item.get("question") or item.get("prompt_id") or "")
    return f"<prompt>\n{prompt}\n</prompt>\n\n<response>\n{sample.get('text', '')}\n</response>"


__all__ = [
    "TOLD_CONDITIONS",
    "Item",
    "LoadedTask",
    "Messages",
    "TaskModule",
    "TaskModuleError",
    "Told",
    "default_judge_prompt",
    "effective_k",
    "item_problems",
    "load_shared",
    "load_task_module",
    "message_problems",
    "messages_hash",
    "prompt_preview",
    "select_items",
    "shared_dir",
    "shared_hash",
    "task_module_path",
    "told_conditions",
    "told_preamble",
]
