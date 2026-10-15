/**
 * @qr-evals/contracts — the typed surface shared by apps/web and the backend.
 *
 * Everything under ./generated is produced by `python scripts/export_schemas.py`
 * from the pydantic schemas in backend/src/qr_llm_evals; never hand-edit it.
 * Hand-written helpers (zod refinements, fixtures) live beside this file.
 *
 * - `types.ts`  — TypeScript types compiled from `schema/_bundle.json`.
 * - `zod.ts`    — `<Name>Schema` zod validators built from `schema/<Name>.json`.
 * - `cards.json` — the eval registry (`evals/*.yaml`) as the SPA consumes it.
 */

import cardsJson from "./generated/cards.json";
import type { EvalCard } from "./generated/types";
import { EvalCardSchema } from "./generated/zod";

export const CONTRACTS_VERSION = "0.1.0";

export type * from "./generated/types";
export * from "./generated/zod";

/** The eval registry, validated against the EvalCard contract at load. */
export const cards: readonly EvalCard[] = EvalCardSchema.array().parse(cardsJson);

/** Cards a study may select today (`status: implemented` and `selectable: true`). */
export const selectableCards: readonly EvalCard[] = cards.filter(
  (c) => c.status === "implemented" && c.selectable,
);

export function cardById(id: string): EvalCard | undefined {
  return cards.find((c) => c.id === id);
}
