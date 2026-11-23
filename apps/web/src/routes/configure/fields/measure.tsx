import type { CardSelection, EvalCard } from "@qr-evals/contracts";
import { Badge } from "../../../components/Badge";
import { setCardItems, setCardK, toggleCard } from "../../../lib/config-edits";
import type { WizardStudyConfig } from "../../../lib/config-store";
import { eligible, metaFor } from "../../../lib/design";
import { tokensPerArm } from "../../../lib/estimates";
import { recFor } from "../../../lib/recommendations";
import { FieldGroup, FieldState, NumberInput, useFieldId, type FieldProps } from "./Field";

// The fields of "what is measured": which evals run, and on how many prompts with how many repetitions.

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 });

export const cardTitle = (card: EvalCard) => (card as EvalCard & { title?: string }).title ?? card.id;
const selectionOf = (props: FieldProps, cardId: string): CardSelection | undefined => props.config.cards.find((selection) => selection.card_id === cardId);
const headline = (card: EvalCard) => card.endpoints.primary[0] ?? "";
/** What an outcome is called: its registered label, or the eval's title for an outcome that has no label of its own. */
function outcomeLabel(card: EvalCard, endpoint: string = headline(card)): string {
  const { label } = metaFor(card.id, endpoint);
  // An outcome without a registered row is labelled by its raw id, which is a key and not a name.
  return label === endpoint ? cardTitle(card) : label;
}

/** A value the study carries for a setting the eval does not take, with the way back to the eval's own. */
function Stray({ value, what, onRemove }: { value: number; what: string; onRemove: () => void }) {
  return <span className="field__value">
    {value} <small>set in this study, but {what}</small>{" "}
    <button type="button" className="text-button" onClick={onRemove}>Use the card&apos;s value</button>
  </span>;
}

const setItems = (config: WizardStudyConfig, cardId: string) => setCardItems(config, cardId, null);
const setK = (config: WizardStudyConfig, cardId: string) => setCardK(config, cardId, null);

/** Whether the eval's headline outcome can decide a study, and what to call it when it cannot. */
function standing(card: EvalCard): { text: string; decides: boolean } {
  const endpoint = headline(card);
  if (eligible(card, endpoint)) return { text: "Can decide a study", decides: true };
  if (metaFor(card.id, endpoint).level === "prompt_group") return { text: "Not computable yet: scored per prompt group", decides: false };
  return { text: "Descriptive only", decides: false };
}

/**
 * What an eval adds to each arm. An eval that reads what the study generates anyway has no prompts of its own:
 * "0 tokens" would read as if it measured nothing, so it says what is the case.
 */
function perArmText(card: EvalCard, selection: CardSelection | undefined): string {
  if (card.dataset.items === 0) return "no tokens of its own";
  return `${compact.format(tokensPerArm(card, selection?.overrides))} tokens / arm`;
}

/**
 * What stands where an eval's prompts would: the token audits read the tokens the study generates, and the
 * session measures read what the sessions produce. Neither measures every token, so neither is said to.
 */
const NO_PROMPTS = "no prompts of its own";

function sizeText(card: EvalCard, selection: CardSelection | undefined): string {
  if (card.dataset.items === 0) return NO_PROMPTS;
  const items = selection?.overrides?.items ?? card.dataset.items;
  const k = selection?.overrides?.k ?? card.k;
  return k === null || k === undefined ? `${items} prompts` : `${items} prompts × ${k}`;
}

/** The evals of the study: the token-level measures that always run, and the checklist of the rest, by tier. */
export function EvalChecklistField(props: FieldProps) {
  const { config, updateConfig, cards } = props;
  const selected = new Set(config.cards.map((selection) => selection.card_id));
  const tokenAudits = cards.filter((card) => card.tier === 0 && card.status === "implemented");
  const rest = cards.filter((card) => !tokenAudits.includes(card));
  const tiers = [...new Set(rest.map((card) => card.tier))].sort((a, b) => a - b);
  // An eval the study names and this registry does not have can still be taken out.
  const unknown = config.cards.filter((selection) => !cards.some((card) => card.id === selection.card_id));

  return <FieldGroup path="cards[].card_id" className="eval-checklist" label="Selected evals">
    <section className="eval-checklist__always" aria-label="Measured on the tokens">
      <h4>Measured on the tokens</h4>
      <p className="field-note">These audits read the tokens the study generates; they have no prompts of their own.</p>
      <ul>
        {tokenAudits.map((card) => <li key={card.id}>
          <span><strong>{cardTitle(card)}</strong> <small>{outcomeLabel(card)}</small></span>
          {selected.has(card.id)
            ? <Badge tone={standing(card).decides ? "positive" : "muted"}>{standing(card).text}</Badge>
            : <button type="button" className="text-button" onClick={() => updateConfig(toggleCard(config, card.id))}>Add {card.id}</button>}
        </li>)}
      </ul>
    </section>
    {tiers.map((tier) => <section key={tier} aria-label={`Tier ${tier}`}>
      <h4>Tier {tier}</h4>
      <div className="eval-selector">
        {rest.filter((card) => card.tier === tier).map((card) => {
          const selection = selectionOf(props, card.id);
          const on = selection !== undefined;
          const status = standing(card);
          return <label className={`eval-check${on ? " is-selected" : ""}`} key={card.id}>
            <input type="checkbox" checked={on} disabled={!card.selectable && !on} onChange={() => updateConfig(toggleCard(config, card.id))} />
            <span className="eval-check__body">
              <span><strong>{card.id}</strong><em>{cardTitle(card)}</em></span>
              <small>{outcomeLabel(card)} · {sizeText(card, selection)} · {perArmText(card, selection)}</small>
              <span className="eval-check__meta">
                {card.status === "implemented" ? <Badge tone={status.decides ? "positive" : "muted"}>{status.text}</Badge> : <Badge tone="muted">{card.status}</Badge>}
              </span>
            </span>
          </label>;
        })}
      </div>
    </section>)}
    {unknown.length > 0 ? <section aria-label="Evals this server does not have">
      <h4>Not in this registry</h4>
      <ul>{unknown.map((selection) => <li key={selection.card_id}>
        <code>{selection.card_id}</code>
        <button type="button" className="text-button" onClick={() => updateConfig(toggleCard(config, selection.card_id))}>Remove {selection.card_id}</button>
      </li>)}</ul>
    </section> : null}
  </FieldGroup>;
}

/** Prompts for one eval. The runner takes the first N in file order. */
export function CardPromptsField(props: FieldProps & { cardId: string }) {
  const { config, updateConfig, cards, cardId } = props;
  const card = cards.find((candidate) => candidate.id === cardId);
  const selection = selectionOf(props, cardId);
  if (card === undefined || selection === undefined) return null;
  if (card.dataset.items === 0) {
    const stray = selection.overrides?.items ?? null;
    return stray === null ? <span className="field__value">{NO_PROMPTS}</span>
      : <Stray value={stray} what="this eval has no prompts of its own and takes no prompt count" onRemove={() => updateConfig(setItems(config, cardId))} />;
  }
  const items = selection.overrides?.items ?? card.dataset.items;
  return <label className="field__label" data-path="cards[].overrides.items">
    <span className="sr-only">Prompts for {card.id}</span>
    <NumberInput
      value={items}
      min={1}
      max={card.dataset.items}
      // All of the eval's prompts is the card's own value, so it is stored as no override.
      onCommit={(value) => { if (value !== null) updateConfig(setCardItems(config, cardId, value === card.dataset.items ? null : value)); }}
    />
    {items < card.dataset.items ? <small>Uses the first {items} prompts in file order.</small> : null}
  </label>;
}

/** Repetitions per prompt for one eval, within the card's bounds. */
export function CardKField(props: FieldProps & { cardId: string }) {
  const { config, updateConfig, cards, recs, cardId } = props;
  const card = cards.find((candidate) => candidate.id === cardId);
  const selection = selectionOf(props, cardId);
  if (card === undefined || selection === undefined) return null;
  if (card.k === null || card.k === undefined) {
    const stray = selection.overrides?.k ?? null;
    return stray === null ? <span className="field__value">none</span>
      : <Stray value={stray} what="this eval takes no repetitions" onRemove={() => updateConfig(setK(config, cardId))} />;
  }
  const [lo, hi] = card.k_bounds ?? [card.k, card.k];
  const locked = recFor(recs, `cards.${cardId}.k`)?.state === "locked";
  return <label className="field__label" data-path="cards[].overrides.k">
    <span className="sr-only">Repetitions (k) for {card.id}</span>
    <NumberInput
      value={selection.overrides?.k ?? card.k}
      min={lo}
      max={hi}
      disabled={locked}
      onCommit={(value) => { if (value !== null) updateConfig(setCardK(config, cardId, value === card.k ? null : value)); }}
    />
  </label>;
}

function PromptsRows(props: FieldProps & { ids: readonly string[] }) {
  const { config, cards, recs, ids } = props;
  return <>{ids.map((id) => {
    const card = cards.find((candidate) => candidate.id === id);
    if (card === undefined) return null;
    const selection = selectionOf(props, id);
    const outcomes = (config.design?.confirmatory ?? []).filter((ref) => ref.card_id === id).map((ref) => metaFor(id, ref.endpoint).label);
    return <tr key={id}>
      <th scope="row">{outcomes.length > 0 ? outcomes.join(", ") : outcomeLabel(card)}<small><code>{id}</code></small></th>
      <td data-label="Prompts (N)"><CardPromptsField {...props} cardId={id} /></td>
      <td data-label="k"><CardKField {...props} cardId={id} /></td>
      <td data-label="Per arm" className="design-table__readout">{perArmText(card, selection)}</td>
      <td data-label="Evidence · preview">
        <FieldState rec={recFor(recs, `cards.${id}.items`)} {...props} />
        <FieldState rec={recFor(recs, `cards.${id}.k`)} {...props} />
      </td>
    </tr>;
  })}</>;
}

/**
 * Prompts and repetitions of every selected eval that has any: the outcomes that decide the study first, with
 * their sample-size rating, then the rest, folded away.
 */
export function PromptsTableField(props: FieldProps) {
  const { config, cards } = props;
  const id = useFieldId("cards[].overrides");
  const known = config.cards.map((selection) => selection.card_id).filter((cardId) => cards.some((card) => card.id === cardId));
  const deciding = new Set((config.design?.confirmatory ?? []).map((ref) => ref.card_id));
  // An eval that has prompts of its own, one that decides the study, and one the study gives a k or a prompt
  // count although it takes none: every value the study holds for an eval has a row to see and change it in.
  const carries = (cardId: string) => { const overrides = config.cards.find((selection) => selection.card_id === cardId)?.overrides; return (overrides?.k ?? null) !== null || (overrides?.items ?? null) !== null; };
  const sized = (cardId: string) => { const card = cards.find((candidate) => candidate.id === cardId); return card !== undefined && (card.dataset.items > 0 || deciding.has(cardId) || carries(cardId)); };
  const first = known.filter((cardId) => deciding.has(cardId));
  const others = known.filter((cardId) => !deciding.has(cardId) && sized(cardId));
  const head = <thead><tr><th scope="col">Outcome</th><th scope="col">Prompts (N)</th><th scope="col">k</th><th scope="col">Per arm</th><th scope="col">Evidence · preview</th></tr></thead>;

  return <div className="field" id={id} data-path="cards[].overrides">
    <p className="field-note">Prompts are the unit of analysis. Repetitions sharpen each prompt's value, so fewer repetitions call for more prompts; they are never counted as prompts themselves.</p>
    {first.length > 0 ? <div className="design-table-frame"><table className="design-table">
      <caption className="sr-only">Prompts and repetitions of the outcomes that decide the study</caption>
      {head}
      <tbody><PromptsRows {...props} ids={first} /></tbody>
    </table></div> : <p className="field-note">No outcome decides this study yet, so no sample size is rated.</p>}
    {others.length > 0 ? <details className="settings-details">
      <summary>Evals that do not decide the study ({others.length})</summary>
      <div className="design-table-frame"><table className="design-table">
        <caption className="sr-only">Prompts and repetitions of the other evals</caption>
        {head}
        <tbody><PromptsRows {...props} ids={others} /></tbody>
      </table></div>
    </details> : null}
  </div>;
}
