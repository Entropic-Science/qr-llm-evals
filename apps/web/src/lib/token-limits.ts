import { tokenLimits, type CardSelection, type EvalCard, type TokenLimit } from "@qr-evals/contracts";

// The token limit of an eval, as a study may set it (`cards[].overrides.max_tokens`).
//
// The numbers are the backend's (`cards/token_limits.py`, exported to the contracts package as
// `token-limits.json`): the standard, the safe value and the bounds are read here, never computed.

/** The study key path of the setting, with list items as `[]`. */
export const TOKEN_LIMIT_PATH = "cards[].overrides.max_tokens";

/** What a study may set as the token limit of an eval, or `undefined` for an eval that generates nothing. */
export function tokenLimitFor(cardId: string): TokenLimit | undefined {
  return Object.hasOwn(tokenLimits, cardId) ? tokenLimits[cardId] : undefined;
}

/** The token limit a reply of this eval runs with: the study's, or the card's own. Unchecked. */
export function effectiveLimit(card: EvalCard, selection: CardSelection | undefined): number {
  return selection?.overrides?.max_tokens ?? card.max_tokens;
}

/** Whether a study's token limit for an eval is one the eval can run with: a whole number within its bounds. */
export function limitStands(cardId: string, value: number): boolean {
  const limit = tokenLimitFor(cardId);
  return limit !== undefined && Number.isInteger(value) && limit.min <= value && value <= limit.max;
}
