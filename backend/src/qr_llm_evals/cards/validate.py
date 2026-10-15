"""Admission validator for eval cards (registry B.1, spec §4.3).

Run by ``backend/tests/test_cards.py`` on every registry card and by
contributors before opening a PR (``docs/add-an-eval.md``). Each rule appends
one plain-language problem; :func:`admission_problems` returns them all so a
reviewer sees every violation at once instead of one per round-trip.

Rules:

1. ``generation ∈ {free, short}`` — enforced by the schema (other values do not parse).
2. ``k ≥ 10`` (a per-prompt statistic needs k samples); ``k`` may be null only
   when the card generates nothing from the target (Tier 0 / derived / judge-only,
   i.e. ``est_tokens_per_sample == 0``). ``k_bounds`` must bracket ``k`` and
   its floor must be ≥ 10.
3. An LLM judge is fixed at temperature 0 and is a different model from the
   target; every non-``none`` judge names a model.
4. ``dataset.revision`` is pinned for ``hf:``/``git:`` sources once the card is
   ``implemented`` (``repo:`` prompts are hashed into the prereg; ``derived:``
   cards have no dataset of their own).
5. Estimate arithmetic: ``est_gen_tokens_per_arm == items × k × est_tokens_per_sample × told_cells``
   and ``est_tokens_per_sample ≤ max_tokens``.
6. Engine ↔ implementation pointer consistency; ``implemented`` cards must
   point at a task module (and rubric) that exists, or a well-formed Inspect task.
7. ``planned`` cards are never ``selectable``.
8. ``sesoi`` keys are declared endpoints; Tier 0 cards declare a Tier 0 source;
   ``told_actual`` is a Tier 4 device.
"""

from __future__ import annotations

import re
from pathlib import Path

from qr_llm_evals.cards.estimates import tokens_per_arm
from qr_llm_evals.cards.loader import REPO_ROOT, CardLoadError, load_card, load_cards
from qr_llm_evals.cards.schema import REVISION_REQUIRED_SCHEMES, EvalCard

MIN_K = 10
INSPECT_TASK_RE = re.compile(r"^[a-z0-9_]+/[a-z0-9_]+$")


class CardAdmissionError(ValueError):
    def __init__(self, card_id: str, problems: list[str]) -> None:
        self.card_id = card_id
        self.problems = problems
        super().__init__(f"{card_id}: " + "; ".join(problems))


def _check_k(card: EvalCard, out: list[str]) -> None:
    if card.k is None:
        if card.generates:
            out.append(
                "k: must be an integer ≥ 10 on a card that generates text "
                "(k may be null only when est_tokens_per_sample == 0)"
            )
        if card.k_bounds is not None:
            out.append("k_bounds: must be null when k is null")
        return
    if card.k < MIN_K:
        out.append(f"k: must be ≥ {MIN_K} for a per-prompt statistic (got {card.k})")
    if card.k_bounds is not None:
        lo, hi = card.k_bounds
        if lo < MIN_K:
            out.append(f"k_bounds: floor must be ≥ {MIN_K} (got {lo})")
        if lo > hi:
            out.append(f"k_bounds: lo must be ≤ hi (got [{lo}, {hi}])")
        if not lo <= card.k <= hi:
            out.append(f"k_bounds: k={card.k} must lie within [{lo}, {hi}]")


def _check_judge(card: EvalCard, out: list[str]) -> None:
    j = card.judge
    if j.kind == "none":
        if j.model is not None:
            out.append("judge.model: must be null when judge.kind is 'none'")
        return
    if not j.model:
        out.append(f"judge.model: required when judge.kind is '{j.kind}'")
    if j.kind == "llm":
        if j.temperature != 0:
            out.append(f"judge.temperature: an LLM judge must run at 0 (got {j.temperature})")
        if j.different_from_target is not True:
            out.append("judge.different_from_target: an LLM judge must be a different model (true)")


def _check_dataset(card: EvalCard, out: list[str]) -> None:
    ds = card.dataset
    if (
        card.status == "implemented"
        and ds.scheme in REVISION_REQUIRED_SCHEMES
        and not (ds.revision or "").strip()
    ):
        out.append(
            f"dataset.revision: required for '{ds.scheme}:' sources once implemented "
            "(pin a commit / dataset sha)"
        )
    if ds.subsample is not None and ds.subsample.n != ds.items:
        out.append(
            "dataset.subsample.n: must equal dataset.items when subsampling "
            f"(got {ds.subsample.n} vs {ds.items})"
        )
    if card.generates and ds.items < 1:
        out.append("dataset.items: must be ≥ 1 on a card that generates text")


def _check_estimates(card: EvalCard, out: list[str]) -> None:
    expected = tokens_per_arm(card)
    if card.est_gen_tokens_per_arm != expected:
        out.append(
            "est_gen_tokens_per_arm: must equal items × k × est_tokens_per_sample × told_cells = "
            f"{card.dataset.items} × {card.k if card.k is not None else 1} × "
            f"{card.est_tokens_per_sample} × {card.told_cells} = {expected} "
            f"(got {card.est_gen_tokens_per_arm})"
        )
    if card.est_tokens_per_sample > card.max_tokens:
        out.append(
            "est_tokens_per_sample: must not exceed max_tokens "
            f"({card.est_tokens_per_sample} > {card.max_tokens})"
        )


def _check_implementation(card: EvalCard, out: list[str], repo_root: Path) -> None:
    if card.engine == "thin":
        if card.inspect_task is not None:
            out.append("inspect_task: must be null when engine is 'thin'")
        if card.task_module is None:
            out.append("task_module: required when engine is 'thin' (evals/<id>/task.py)")
        elif card.task_module != f"evals/{card.id}/task.py":
            out.append(f"task_module: must be 'evals/{card.id}/task.py' (got '{card.task_module}')")
    else:
        if card.task_module is not None:
            out.append("task_module: must be null when engine is 'inspect'")
        if card.inspect_task is None:
            out.append("inspect_task: required when engine is 'inspect'")
        elif not INSPECT_TASK_RE.match(card.inspect_task):
            out.append(
                f"inspect_task: must look like '<package>/<task>' (got '{card.inspect_task}')"
            )
    if card.status == "implemented":
        if (
            card.engine == "thin"
            and card.task_module
            and not (repo_root / card.task_module).is_file()
        ):
            out.append(
                f"task_module: '{card.task_module}' does not exist but status is implemented"
            )
        if card.judge.rubric and not (repo_root / card.judge.rubric).is_file():
            out.append(
                f"judge.rubric: '{card.judge.rubric}' does not exist but status is implemented"
            )


def _check_status(card: EvalCard, out: list[str]) -> None:
    if card.status == "planned" and card.selectable:
        out.append("selectable: a 'planned' card must not be selectable")


def _check_endpoints(card: EvalCard, out: list[str]) -> None:
    declared = set(card.endpoints.all)
    dup = set(card.endpoints.primary) & set(card.endpoints.secondary)
    if dup:
        out.append(f"endpoints: {sorted(dup)} listed as both primary and secondary")
    for key in card.sesoi:
        if key not in declared:
            out.append(f"sesoi.{key}: not a declared endpoint")
    if card.tier == 0 and card.endpoints.requires_tier0 == "none":
        out.append(
            "endpoints.requires_tier0: a Tier 0 card must declare engine_log or api_logprobs"
        )
    if card.told_actual and card.tier != 4:
        out.append("told_actual: only Tier 4 self-report/opinion cards use the told/actual design")


def admission_problems(card: EvalCard, repo_root: Path | None = None) -> list[str]:
    """All admission-rule violations for ``card`` (empty ⇒ admitted)."""
    root = repo_root if repo_root is not None else REPO_ROOT
    out: list[str] = []
    _check_k(card, out)
    _check_judge(card, out)
    _check_dataset(card, out)
    _check_estimates(card, out)
    _check_implementation(card, out, root)
    _check_status(card, out)
    _check_endpoints(card, out)
    return out


def validate_card(card: EvalCard, repo_root: Path | None = None) -> None:
    """Raise :class:`CardAdmissionError` unless the card is admitted."""
    problems = admission_problems(card, repo_root)
    if problems:
        raise CardAdmissionError(card.id, problems)


def check_card_file(path: Path, repo_root: Path | None = None) -> list[str]:
    """Parse + admit one file; returns every problem (schema or admission), empty if clean."""
    try:
        card = load_card(path)
    except CardLoadError as err:
        return list(err.problems)
    return admission_problems(card, repo_root)


def validate_registry(
    registry_dir: Path | None = None, repo_root: Path | None = None
) -> dict[str, EvalCard]:
    """Load and admit every card; raises on the first offending card."""
    cards = load_cards(registry_dir)
    for card in cards.values():
        validate_card(card, repo_root)
    return cards


__all__ = [
    "MIN_K",
    "CardAdmissionError",
    "admission_problems",
    "check_card_file",
    "validate_card",
    "validate_registry",
]
