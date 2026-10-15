"""Load ``evals/*.yaml`` into :class:`EvalCard` objects.

Loading is pure parsing (YAML → pydantic). Admission rules are applied by
:mod:`qr_llm_evals.cards.validate`; :func:`load_cards` only enforces the two
structural facts a registry cannot function without: the file name equals the
card ``id`` and ids are unique.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from qr_llm_evals.cards.schema import EvalCard

#: ``backend/src/qr_llm_evals/cards/loader.py`` → repository root (editable install).
REPO_ROOT: Path = Path(__file__).resolve().parents[4]
DEFAULT_REGISTRY_DIR: Path = REPO_ROOT / "evals"


class CardLoadError(ValueError):
    """A card file could not be parsed into the schema."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(f"{path.name}: " + "; ".join(problems))


def format_validation_error(err: ValidationError) -> list[str]:
    """``field.path: message (got <input>)`` per error, stable for tests and PR output."""
    out: list[str] = []
    for e in err.errors(include_url=False):
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        got_s = "" if e["type"] == "missing" else f" (got {e.get('input')!r})"
        out.append(f"{loc}: {e['msg']}{got_s}")
    return out


def load_card(path: Path) -> EvalCard:
    """Parse one card file; raises :class:`CardLoadError` with readable problems."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise CardLoadError(path, [f"invalid YAML: {err}"]) from err
    if not isinstance(raw, dict):
        raise CardLoadError(path, ["top level must be a mapping"])
    try:
        card = EvalCard.model_validate(raw)
    except ValidationError as err:
        raise CardLoadError(path, format_validation_error(err)) from err
    if path.stem != card.id:
        raise CardLoadError(path, [f"file name '{path.stem}' must equal id '{card.id}'"])
    return card


def iter_card_paths(registry_dir: Path | None = None) -> list[Path]:
    """Card files in the registry, sorted; ``_pools/`` and ``_shared/`` are not cards."""
    root = registry_dir if registry_dir is not None else DEFAULT_REGISTRY_DIR
    return sorted(p for p in root.glob("*.yaml") if not p.name.startswith("_"))


def load_cards(registry_dir: Path | None = None) -> dict[str, EvalCard]:
    """Every card in ``registry_dir`` keyed by id, in sorted-file order."""
    cards: dict[str, EvalCard] = {}
    for path in iter_card_paths(registry_dir):
        card = load_card(path)
        if card.id in cards:
            raise CardLoadError(path, [f"duplicate card id '{card.id}'"])
        cards[card.id] = card
    return cards


__all__ = [
    "DEFAULT_REGISTRY_DIR",
    "REPO_ROOT",
    "CardLoadError",
    "format_validation_error",
    "iter_card_paths",
    "load_card",
    "load_cards",
]
