import { panelId } from "../../components/study/panels/Panel";
import { panelFor } from "../../lib/panels";
import { TOKEN_LIMIT_PATH } from "../../lib/token-limits";

// Finding the field of a setting on the page and putting the focus on it, for a link from a problem, a chip
// or (later) a map node. Fields are found by the `data-path` every field carries, not by element id: an id is
// scoped to where the field is drawn, and a few fields have none.

/** What can be typed into or chosen from, and failing that what can be pressed. A field's own control comes before its chip. */
const EDITABLE = "input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled])";
const FOCUSABLE = `${EDITABLE}, button:not([disabled]), a[href], [tabindex]`;
const CARD_SETTING = /^cards\.([^.]+)\.(k|items|max_tokens)$/;

/**
 * The `data-path` of the field that edits the setting a `Rec` path names: the prompts table for an eval's
 * prompts and repetitions, the token limits table for its token limit. Study key paths pass through.
 */
function fieldPath(path: string): string {
  const setting = CARD_SETTING.exec(path)?.[2];
  if (setting === undefined) return path;
  return setting === "max_tokens" ? TOKEN_LIMIT_PATH : "cards[].overrides";
}

/** The row of one eval in the prompts table or the token limits table, which name each eval by its id in a `<code>`. */
function cardRow(field: HTMLElement, path: string): HTMLElement | null {
  const cardId = CARD_SETTING.exec(path)?.[1];
  if (cardId === undefined) return null;
  return [...field.querySelectorAll<HTMLElement>("tr")].find((row) => [...row.querySelectorAll("code")].some((code) => code.textContent === cardId)) ?? null;
}

const within = (path: string, parent: string) => path === parent || path.startsWith(`${parent}.`) || path.startsWith(`${parent}[`);

/**
 * The element of a setting under `root`: the field with that path, else the field of the block it sits in,
 * else the panel that edits it. `null` when the setting is not drawn there.
 */
export function locateField(root: ParentNode, path: string): HTMLElement | null {
  const fields = [...root.querySelectorAll<HTMLElement>("[data-path]")];
  const wanted = fieldPath(path);
  for (const candidate of [path, wanted]) {
    const exact = fields.find((field) => field.dataset.path === candidate);
    if (exact) return cardRow(exact, path) ?? exact;
  }
  const parents = fields
    .filter((field) => within(wanted, field.dataset.path ?? ""))
    .sort((a, b) => (b.dataset.path ?? "").length - (a.dataset.path ?? "").length);
  if (parents[0]) return parents[0];
  const panel = panelFor(path);
  return panel ? root.querySelector<HTMLElement>(`[id="${panelId(panel.id)}"]`) : null;
}

function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Puts the focus on the first control of a field (or on the element itself) and brings it into view, in the
 * middle of the window unless `block` says otherwise. A field folded away in a `<details>` is unfolded first.
 */
export function focusField(element: HTMLElement, block: ScrollLogicalPosition = "center"): void {
  for (let node = element.parentElement; node; node = node.parentElement) {
    if (node instanceof HTMLDetailsElement) node.open = true;
  }
  const control = element.matches(FOCUSABLE) ? element : element.querySelector<HTMLElement>(EDITABLE) ?? element.querySelector<HTMLElement>(FOCUSABLE);
  if (control === null) element.setAttribute("tabindex", "-1");
  (control ?? element).focus({ preventScroll: true });
  if (typeof element.scrollIntoView === "function") element.scrollIntoView({ block, behavior: reducedMotion() ? "auto" : "smooth" });
}
