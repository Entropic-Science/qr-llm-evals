"""Read-only preview of a card's prompts for the setup pages (``GET /cards/{id}/items``).

A researcher choosing evals wants to read what a model will be asked. Only
some cards can show that before a run: the ones whose task module builds its
items from files in this repository. The rest get their prompts at run time
(from another card's generations, from a pinned remote dataset, from inside an
Inspect task) or have none, and :func:`classify` says which.

Nothing here may reach the network, download or write. That is why the cards
whose module is imported are an explicit list, :data:`PREVIEW_SAFE`, and not
"every thin card": a module such as ``semantic_entropy_sqa`` fetches its
dataset inside ``build_items()``. A card joins the list only after its
``build_items()`` and ``build_messages()`` have been read and found to touch
nothing but files under ``evals/``.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.runner.tasks import Told, load_task_module, told_conditions

#: Where a card's prompts come from. Only ``task_module`` can be listed.
ItemSource = Literal[
    "task_module", "derived_at_run", "remote_dataset", "inspect_task", "no_prompts", "error"
]

#: Cards whose task module is imported for a preview (see the module docstring).
PREVIEW_SAFE: frozenset[str] = frozenset(
    {
        "altered_state_meq",
        "creative_diversity",
        "guesser_warmup_replies",
        "introspection_scales",
        "moralchoice_consistency",
        "opinion_quantum_sampling",
        "self_referential_probe",
        "sentience_statements",
        "thurstonian_self_outcomes",
        "unpredictabench",
    }
)

#: Characters of one message kept in a preview; longer content is cut and flagged.
#: The cap is there to bound a response, not to shorten prompts: it stands well
#: above the longest prompt of the cards on :data:`PREVIEW_SAFE` (2 382
#: characters, ``altered_state_meq``), and a test fails when a card outgrows it.
#: At the largest page (100 items) a response is then at most about 0.6 MB per
#: message of an item; the real registry's largest page is about 125 KiB.
CONTENT_LIMIT = 6000
#: The told cell shown when a told/actual card is asked for no particular one.
DEFAULT_TOLD: Told = "told_nothing"
#: ``dataset.source`` of the cards computed from the Tier 0 token log.
TIER0_LOG_SOURCE = "derived:tier0_log"
_REMOTE_SCHEMES: frozenset[str] = frozenset({"hf", "git"})
_REASON_LIMIT = 300


@dataclass(frozen=True, slots=True)
class PreviewItem:
    """One prompt as the target model receives it, cut to :data:`CONTENT_LIMIT` per message."""

    index: int
    prompt_id: str
    messages: tuple[dict[str, str], ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class CardPreview:
    """Everything a card can show before a run; built once and then only sliced."""

    card_id: str
    source: ItemSource
    reason: str | None
    told_actual: bool
    items_by_told: Mapping[Told, tuple[PreviewItem, ...]]

    @property
    def listable(self) -> bool:
        return self.source == "task_module"

    def told_for(self, told: Told) -> Told:
        """The told cell a request resolves to (``None`` on a card without the factor)."""
        if not self.told_actual:
            return None
        return told if told is not None else DEFAULT_TOLD

    def items(self, told: Told) -> tuple[PreviewItem, ...]:
        return self.items_by_told.get(self.told_for(told), ())


def classify(card: EvalCard) -> ItemSource:
    """Where ``card`` gets its prompts, decided from the card alone (nothing is imported).

    ``error`` here means a card that ships its prompts in this repository but
    is not on :data:`PREVIEW_SAFE`: its module has not been cleared, so it is
    not imported and the preview says so instead of guessing.
    """
    if card.engine == "inspect":
        return "inspect_task"
    if card.id in PREVIEW_SAFE and card.task_module:
        return "task_module"
    if card.dataset.scheme in _REMOTE_SCHEMES:
        return "remote_dataset"
    if card.dataset.source == TIER0_LOG_SOURCE:
        return "no_prompts"
    # Every other derived card, and a card that sends nothing to the target
    # (the ranking judge reads session transcripts), is assembled from the
    # run's own records.
    if card.dataset.scheme == "derived" or not card.generates:
        return "derived_at_run"
    return "error"


def _unlisted_reason(card: EvalCard, source: ItemSource) -> str:
    if source == "inspect_task":
        return f"the prompts are defined inside the Inspect task {card.inspect_task}"
    if source == "remote_dataset":
        return f"the prompts are downloaded at run time from {card.dataset.source}"
    if source == "no_prompts":
        return "computed from the token log of the other evals; it has no prompts of its own"
    if source == "derived_at_run":
        return "the prompts are built from this study's own generations at run time"
    return (
        f"{card.task_module or card.id} is not cleared for preview "
        "(PREVIEW_SAFE in runner/preview.py)"
    )


def _clip(text: str) -> tuple[str, bool]:
    return (text, False) if len(text) <= CONTENT_LIMIT else (text[:CONTENT_LIMIT], True)


def _preview_item(
    index: int, item: Mapping[str, Any], messages: list[dict[str, str]]
) -> PreviewItem:
    shown: list[dict[str, str]] = []
    truncated = False
    for message in messages:
        content, cut = _clip(message["content"])
        truncated = truncated or cut
        shown.append({"role": message["role"], "content": content})
    return PreviewItem(
        index=index, prompt_id=str(item["prompt_id"]), messages=tuple(shown), truncated=truncated
    )


def _error_reason(err: Exception, root: Path) -> str:
    # The route is open, so the server's directory layout stays out of the
    # message: paths are shown relative to the repository.
    text = str(err) or type(err).__name__
    for prefix in (str(root), root.as_posix()):
        text = text.replace(prefix, "<repo>")
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 1] + "…"


def build_preview(card: EvalCard, repo_root: Path | None = None) -> CardPreview:
    """Build the whole preview of ``card``. Never raises: a broken module is ``source="error"``."""
    source = classify(card)
    if source != "task_module":
        return CardPreview(card.id, source, _unlisted_reason(card, source), card.told_actual, {})
    root = repo_root if repo_root is not None else REPO_ROOT
    try:
        task = load_task_module(card, root)
        items = task.build_items()
        by_told: dict[Told, tuple[PreviewItem, ...]] = {
            told: tuple(
                _preview_item(index, item, task.build_messages(item, told))
                for index, item in enumerate(items)
            )
            for told in told_conditions(card)
        }
    except Exception as err:  # noqa: BLE001 - a card's module is arbitrary code; a preview never fails the request
        return CardPreview(card.id, "error", _error_reason(err, root), card.told_actual, {})
    return CardPreview(card.id, "task_module", None, card.told_actual, by_told)


class PreviewCache:
    """Each card's preview, built on first use and kept for the life of the process.

    The registry is loaded once at boot and cards are frozen, so a preview
    cannot go stale. The lock makes two first requests for the same card build
    it once; building is file reads and string formatting.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previews: dict[str, CardPreview] = {}

    def get(self, card: EvalCard, repo_root: Path | None = None) -> CardPreview:
        with self._lock:
            preview = self._previews.get(card.id)
            if preview is None:
                preview = build_preview(card, repo_root)
                self._previews[card.id] = preview
            return preview

    def __len__(self) -> int:
        with self._lock:
            return len(self._previews)


__all__ = [
    "CONTENT_LIMIT",
    "DEFAULT_TOLD",
    "PREVIEW_SAFE",
    "TIER0_LOG_SOURCE",
    "CardPreview",
    "ItemSource",
    "PreviewCache",
    "PreviewItem",
    "build_preview",
    "classify",
]
