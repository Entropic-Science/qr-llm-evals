"""The token limit of an eval: the card's own, the recommended safe one and a study's bounds.

Every generating eval sends ``max_tokens`` with each request, and the engine
ends a reply that reaches it. The card's own ``max_tokens`` is the *standard*
limit: enough for a reply of the usual length, with room to spare. A study may
set another limit for an eval (``cards[].overrides.max_tokens``), and the setup
offers a *safe* one beside the standard: several times the standard, for a
study that would rather pay for long replies than analyse cut ones.

The safe limit is a rule and not a measurement. Nothing here has checked that
no reply of an eval reaches it, and a model that thinks before it answers can
run to any limit. A reply that reaches the limit is still flagged as cut
(``cards/result_checks.py``), whatever the limit was.

These facts are kept beside the cards and not on them, for the reason
``cards/endpoint_meta.py`` gives: a card snapshot is hashed into every plan, so
a new card field would move the ``cards_hash`` of every registered study.

Layer 0: imports the card schema only.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from qr_llm_evals.cards.schema import EvalCard

#: The most a study may set, and the most a card may ask for. The engine on the
#: box serves a context of 131,072 tokens that the prompt and the reply share;
#: this leaves the prompt at least half of it. ``models/schema.py`` repeats the
#: number (layer 0 modules do not import each other) and a test compares them.
TOKEN_LIMIT_MAX = 64_000
#: The safe limit is this many times the card's own.
SAFE_FACTOR = 4
#: A safe limit is never below this, so that an eval with a one-word answer
#: (a card limit of 8 or 16 tokens) still gets room for a sentence.
SAFE_FLOOR = 256


class TokenLimit(BaseModel):
    """What a study may set as the token limit of one generating eval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    standard: int = Field(
        ge=1, description="The card's own max_tokens: the limit of a study that sets none."
    )
    safe: int = Field(
        ge=1,
        description=(
            f"The recommended generous limit: {SAFE_FACTOR} times the standard, at least "
            f"{SAFE_FLOOR} and at most {TOKEN_LIMIT_MAX}. A rule, not a measured bound."
        ),
    )
    min: int = Field(
        ge=1, description="The lowest limit a study may set: the usual length of a reply."
    )
    max: int = Field(ge=1, description="The highest limit a study may set.")


def token_limit(card: EvalCard) -> TokenLimit | None:
    """The limits of ``card``, or ``None`` for a card that generates nothing."""
    if not card.generates or card.max_tokens <= 0:
        return None
    standard = card.max_tokens
    return TokenLimit(
        standard=standard,
        safe=min(TOKEN_LIMIT_MAX, max(SAFE_FACTOR * standard, SAFE_FLOOR)),
        min=min(card.est_tokens_per_sample, standard),
        max=max(TOKEN_LIMIT_MAX, standard),
    )


def effective_max_tokens(card: EvalCard, override: int | None = None) -> int:
    """The ``max_tokens`` a request of ``card`` carries after the study's override.

    Raises ``ValueError`` for an override on a card that generates nothing and
    for one outside the card's bounds.
    """
    if override is None:
        return card.max_tokens
    limit = token_limit(card)
    if limit is None:
        raise ValueError(
            f"{card.id}: max_tokens cannot be overridden on a card that generates nothing"
        )
    if not limit.min <= override <= limit.max:
        raise ValueError(f"{card.id}: max_tokens={override} outside [{limit.min}, {limit.max}]")
    return override


def token_limits_export(cards: dict[str, EvalCard]) -> dict[str, dict[str, int]]:
    """The limits of every generating card, keyed by card id, for the contracts package."""
    out: dict[str, dict[str, int]] = {}
    for card_id in sorted(cards):
        limit = token_limit(cards[card_id])
        if limit is not None:
            out[card_id] = limit.model_dump(mode="json")
    return out


__all__ = [
    "SAFE_FACTOR",
    "SAFE_FLOOR",
    "TOKEN_LIMIT_MAX",
    "TokenLimit",
    "effective_max_tokens",
    "token_limit",
    "token_limits_export",
]
