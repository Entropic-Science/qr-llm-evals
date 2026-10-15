import type { EvalCard } from "@qr-evals/contracts";
import { tokensPerArm } from "../../lib/estimates";
import type { WizardStepProps } from "./index";

const number = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 });

export function Evals({ config, updateConfig, cards }: WizardStepProps) {
  const selected = new Set(config.cards.map(({ card_id }) => card_id));
  const toggle = (card: EvalCard) => updateConfig({ ...config, cards: selected.has(card.id) ? config.cards.filter(({ card_id }) => card_id !== card.id) : [...config.cards, { card_id: card.id }] });
  return <section aria-labelledby="step-evals"><p className="eyebrow">Protocol 03</p><h2 id="step-evals">Evaluation registry</h2><p className="step-intro">Select free-generation measures. Costs are per arm and update the readout immediately.</p>
    <div className="eval-selector">{cards.map((card) => <label className={`eval-check${selected.has(card.id) ? " is-selected" : ""}`} key={card.id}>
      <input type="checkbox" checked={selected.has(card.id)} disabled={!card.selectable && !selected.has(card.id)} onChange={() => toggle(card)} />
      <span className="eval-check__body"><span><strong>{card.id}</strong><em>T{card.tier} · {card.family.replaceAll("_", " ")}</em></span><small>{card.purpose}</small><span className="eval-check__meta">k {card.k ?? "derived"} · {number.format(tokensPerArm(card))} tokens / arm · thinking {card.thinking ? "on" : "off"} · {card.sensitivity}</span>{!card.selectable && <span className="badge badge--muted">{card.status}</span>}</span>
    </label>)}</div>
    {config.cards.length === 0 && <p className="validation-note" role="alert">Select at least one evaluation.</p>}
  </section>;
}
