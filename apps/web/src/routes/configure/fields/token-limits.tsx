import type { CardSelection, EvalCard, TokenLimit } from "@qr-evals/contracts";
import { setCardMaxTokens } from "../../../lib/config-edits";
import type { WizardStudyConfig } from "../../../lib/config-store";
import { TOKEN_LIMIT_PATH, effectiveLimit, tokenLimitFor } from "../../../lib/token-limits";
import { NumberInput, useFieldId, type FieldProps } from "./Field";
import { Stray, cardTitle } from "./measure";

// The token limit of each selected eval that generates replies: the card's own, or the one the study sets.

const whole = new Intl.NumberFormat("en");
const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 });

/** A card with `told_actual` runs every prompt in three cells: told quantum, told pseudo-random, told nothing. */
const TOLD_CELLS = 3;

export const TOKEN_LIMITS_NOTE = "Each reply ends when it reaches its eval's token limit, and a reply that does is flagged as cut. Standard is the eval's own limit. Safe is four times that, at least 256 and at most 64,000: a reply should rarely reach it, but nothing guarantees that, and a model that thinks before it answers can use all of it. The limit caps a reply. It does not change the estimate, which rests on the usual length of a reply, so the last column is the most an arm can generate on that eval.";

/** What the study text says about an override on an eval that takes none. */
const NOT_TAKEN = "this eval generates nothing and takes no token limit";

const selectionOf = (config: WizardStudyConfig, cardId: string): CardSelection | undefined => config.cards.find((selection) => selection.card_id === cardId);

/** The most one arm can generate on an eval: every reply of every prompt, repetition and told cell at its limit. */
export function mostTokensPerArm(card: EvalCard, selection: CardSelection | undefined): number {
  const items = selection?.overrides?.items ?? card.dataset.items;
  const k = selection?.overrides?.k ?? card.k ?? 1;
  return items * k * (card.told_actual ? TOLD_CELLS : 1) * effectiveLimit(card, selection);
}

/** The study with one eval's limit set; the eval's own (standard) limit is stored as no override. */
function withLimit(config: WizardStudyConfig, cardId: string, limit: TokenLimit, value: number): WizardStudyConfig {
  return setCardMaxTokens(config, cardId, value === limit.standard ? null : value);
}

/** The token limit of one eval, as a number box within the eval's bounds. Nothing for an eval that generates nothing. */
export function CardTokenLimitField(props: FieldProps & { cardId: string }) {
  const { config, updateConfig, cards, cardId } = props;
  const card = cards.find((candidate) => candidate.id === cardId);
  const selection = selectionOf(config, cardId);
  if (card === undefined || selection === undefined) return null;
  const limit = tokenLimitFor(cardId);
  if (limit === undefined) {
    const stray = selection.overrides?.max_tokens ?? null;
    return stray === null ? null : <Stray value={stray} what={NOT_TAKEN} onRemove={() => updateConfig(setCardMaxTokens(config, cardId, null))} />;
  }
  return <label className="field__label">
    <span className="sr-only">Token limit for {card.id}</span>
    <NumberInput
      value={effectiveLimit(card, selection)}
      min={limit.min}
      max={limit.max}
      onCommit={(value) => { if (value !== null) updateConfig(withLimit(config, cardId, limit, value)); }}
    />
  </label>;
}

/** The two limits offered for one eval. The one the eval runs with is shown pressed. */
export function CardTokenPresets(props: FieldProps & { cardId: string }) {
  const { config, updateConfig, cards, cardId } = props;
  const card = cards.find((candidate) => candidate.id === cardId);
  const limit = tokenLimitFor(cardId);
  if (card === undefined || limit === undefined) return null;
  const current = effectiveLimit(card, selectionOf(config, cardId));
  const presets = [["Standard", limit.standard], ["Safe", limit.safe]] as const;
  return <span className="chip-row" role="group" aria-label={`Token limit presets for ${card.id}`}>
    {presets.map(([name, value]) => <button
      key={name}
      type="button"
      className={`chip-toggle${current === value ? " is-on" : ""}`}
      aria-pressed={current === value}
      onClick={() => updateConfig(withLimit(config, cardId, limit, value))}
    >{name} · {whole.format(value)}</button>)}
  </span>;
}

/**
 * The token limits of the selected evals that generate replies, one row each, and a row for an eval the study
 * gives a limit although it generates nothing, so that every limit the study holds can be seen and taken back.
 */
export function TokenLimitsField(props: FieldProps) {
  const { config, updateConfig, cards } = props;
  const id = useFieldId(TOKEN_LIMIT_PATH);
  const rows = config.cards.flatMap((selection) => {
    const card = cards.find((candidate) => candidate.id === selection.card_id);
    if (card === undefined) return [];
    const limit = tokenLimitFor(card.id);
    return limit !== undefined || (selection.overrides?.max_tokens ?? null) !== null ? [{ card, selection, limit }] : [];
  });
  const limited = rows.flatMap(({ card, limit }) => (limit === undefined ? [] : [{ card, limit }]));
  const setAll = (pick: (limit: TokenLimit) => number) => updateConfig(
    limited.reduce((study, { card, limit }) => withLimit(study, card.id, limit, pick(limit)), config),
  );

  return <div className="field" id={id} data-path={TOKEN_LIMIT_PATH}>
    <p className="field-note">{TOKEN_LIMITS_NOTE}</p>
    {rows.length === 0 ? <p className="field-note">No selected eval generates replies of its own, so there is no token limit to set.</p> : <>
      {limited.length > 0 ? <div className="chip-row">
        <button type="button" className="text-button" onClick={() => setAll((limit) => limit.standard)}>Set all to standard</button>
        <button type="button" className="text-button" onClick={() => setAll((limit) => limit.safe)}>Set all to safe</button>
      </div> : null}
      <div className="design-table-frame"><table className="design-table">
        <caption className="sr-only">Token limits of the selected evals</caption>
        <thead><tr><th scope="col">Eval</th><th scope="col">Token limit</th><th scope="col">Presets</th><th scope="col">At most, per arm</th></tr></thead>
        <tbody>{rows.map(({ card, selection, limit }) => <tr key={card.id}>
          <th scope="row">{cardTitle(card)}<small><code>{card.id}</code></small></th>
          {limit === undefined
            ? <td data-label="Token limit" colSpan={3}><CardTokenLimitField {...props} cardId={card.id} /></td>
            : <>
              <td data-label="Token limit"><CardTokenLimitField {...props} cardId={card.id} /></td>
              <td data-label="Presets"><CardTokenPresets {...props} cardId={card.id} /></td>
              <td data-label="At most, per arm" className="design-table__readout">{compact.format(mostTokensPerArm(card, selection))} tokens</td>
            </>}
        </tr>)}</tbody>
      </table></div>
    </>}
  </div>;
}
