import type { EndpointRef, StudyDesign } from "@qr-evals/contracts";
import { useId } from "react";
import {
  declareDesign, removeDesign, setAlpha, setClaim, setExpectations, setMargin, setSensitivityEpsilon, toggleConfirmatory,
} from "../../../lib/config-edits";
import {
  MAX_CONFIRMATORY, cardMargin, compared, draftHypotheses, eligible, epsilonArms, formatG, ineligibleReason,
  metaFor, noMarginReason, recommendDesign, sourceCells,
} from "../../../lib/design";
import { MARGIN_CAP_FRACTION, MARGIN_FLOOR_FRACTION } from "../../../lib/power";
import { recFor } from "../../../lib/recommendations";
import { EPSILON_LEVELS } from "../../../lib/roster";
import { Field, FieldGroup, FieldState, NumberInput, ReadOnlyField, type FieldProps } from "./Field";

// The fields of the design block: what would count as a finding, which outcomes decide the study, at which
// false-finding rate, against which positive control and within which margins. A study without a design
// block has none of them to set; `DeclareDesignField` gives it the recommended block.

type Alpha = NonNullable<StudyDesign["alpha"]>;
type Epsilon = NonNullable<StudyDesign["sensitivity_epsilon"]>;

const ALPHA_LEVELS: readonly Alpha[] = [0.05, 0.01, 0.005];
const CLAIMS: readonly { value: StudyDesign["claim"]; label: string }[] = [
  { value: "difference", label: "A difference only" },
  { value: "difference_or_equivalence", label: "A difference, or its absence within a margin" },
];
const refKey = (ref: EndpointRef) => `${ref.card_id}/${ref.endpoint}`;

/** For a study on the built-in plan: what that means, and the one button that changes it. */
export function DeclareDesignField({ config, updateConfig, cards }: FieldProps) {
  if (config.design !== null && config.design !== undefined) return null;
  const possible = recommendDesign(config, cards) !== null;
  // That the study is on the built-in plan is said once, by the problem listed above the panel. Here is the way out of it.
  return <div className="field" data-path="design">
    <p className="field-note">
      A design says, before any data exist, what would count as a finding: the outcomes that decide the study, the false-finding rate,
      the positive control and the margins. Declaring one makes it this study&apos;s analysis plan in place of the built-in one, so the
      study&apos;s hash changes. It starts every choice of the design at its recommended value and changes nothing else: prompts,
      repetitions and arms stay as they are. Where an outcome that then decides the study needs more repetitions, the table of prompts
      and repetitions on this step says so and offers the number that is enough. Removing the design block again, on this step, gives
      back the study as it was before it was declared.
    </p>
    <button type="button" className="button button--secondary" disabled={!possible} onClick={() => updateConfig(declareDesign(config, cards))}>Declare a design</button>
    {possible ? null : <p className="field-note">None of the selected evals has an outcome that can decide a study, so there is no design to declare yet.</p>}
  </div>;
}

/** For a study with a design block: the way back to the built-in plan, folded away because it discards the design. */
export function BuiltInPlanField({ config, updateConfig }: FieldProps) {
  if (config.design === null || config.design === undefined) return null;
  return <details className="settings-details" data-path="design">
    <summary>Return to the built-in plan</summary>
    <p className="field-note">
      This takes the design block out of the study: its claim, the outcomes that decide it, the false-finding rate, the positive control
      and the margins. The study is then analysed on the built-in Study 1 plan, which resolves no hypothesis to a verdict, and its hash
      changes. Prompts, repetitions and arms stay as they are. A design declared again starts from the recommended values.
    </p>
    <button type="button" className="button button--secondary" onClick={() => updateConfig(removeDesign(config))}>Remove the design block</button>
  </details>;
}

/** What a design field shows on a study that has no design block. */
export function NoDesignNote() {
  return <p className="field-note">Set once a design is declared.</p>;
}

export function ClaimField(props: FieldProps) {
  const { config, updateConfig, cards, recs } = props;
  // The same field can be on the page twice; each copy is its own radio group.
  const group = useId();
  const design = config.design;
  if (design === null || design === undefined) return null;
  // The absence of an effect can only be claimed against a positive control every comparison runs.
  const control = recommendDesign(config, cards)?.sensitivity_epsilon ?? null;
  const choose = (claim: StudyDesign["claim"]) => {
    let next = setClaim(config, claim);
    if (claim === "difference_or_equivalence" && (design.sensitivity_epsilon ?? null) === null && control !== null) next = setSensitivityEpsilon(next, control);
    updateConfig(next);
  };
  return <FieldGroup path="design.claim" label="What would count as a finding?" state={<FieldState rec={recFor(recs, "design.claim")} {...props} />}>
    {CLAIMS.map(({ value, label }) => {
      const blocked = value === "difference_or_equivalence" && control === null && design.claim !== value;
      return <label className="toggle-line" key={value}>
        <input type="radio" name={group} checked={design.claim === value} disabled={blocked} onChange={() => choose(value)} />
        <span>{label}{blocked ? <small> · needs a positive control that is switched on in every comparison</small> : null}</span>
      </label>;
    })}
  </FieldGroup>;
}

export function OutcomesField(props: FieldProps) {
  const { config, updateConfig, cards, recs } = props;
  const design = config.design;
  if (design === null || design === undefined) return null;
  const chosen = new Set(design.confirmatory.map(refKey));
  const offered: EndpointRef[] = config.cards.flatMap((selection) => {
    const card = cards.find((candidate) => candidate.id === selection.card_id);
    return card === undefined ? [] : card.endpoints.primary.filter((endpoint) => eligible(card, endpoint)).map((endpoint) => ({ card_id: card.id, endpoint }));
  });
  // An outcome that was chosen and can no longer decide a study stays listed, so it can be taken out.
  const stale = design.confirmatory.filter((ref) => !offered.some((candidate) => refKey(candidate) === refKey(ref)));
  const m = draftHypotheses(config, cards).length;
  const full = design.confirmatory.length >= MAX_CONFIRMATORY;
  return <FieldGroup
    path="design.confirmatory"
    label={`Outcomes that decide the study (m = ${m})`}
    note={`Each is tested at α/${Math.max(1, m)}. Every outcome you add makes all of them harder to pass.`}
    state={<FieldState rec={recFor(recs, "design.confirmatory")} {...props} />}
  >
    {offered.length + stale.length === 0 ? <p className="field-note">None of the selected evals has an outcome that can decide a study.</p> : null}
    {[...stale, ...offered].map((ref) => {
      const on = chosen.has(refKey(ref));
      const why = stale.includes(ref)
        ? (config.cards.some((selection) => selection.card_id === ref.card_id) ? ineligibleReason(cards.find((card) => card.id === ref.card_id), ref.endpoint) : "the eval is not selected")
        : null;
      return <label className="toggle-line" key={refKey(ref)}>
        <input type="checkbox" checked={on} disabled={!on && full} onChange={() => updateConfig(toggleConfirmatory(config, ref))} />
        <span>{metaFor(ref.card_id, ref.endpoint).label} <small><code>{ref.card_id}</code>{why ? ` · cannot decide a study: ${why}` : ""}</small></span>
      </label>;
    })}
    {full ? <p className="field-note">A study registers at most {MAX_CONFIRMATORY} outcomes.</p> : null}
  </FieldGroup>;
}

export function AlphaField(props: FieldProps) {
  const { config, updateConfig, recs } = props;
  const design = config.design;
  if (design === null || design === undefined) return null;
  return <Field
    path="design.alpha"
    label="False-finding rate (α)"
    note="Across all confirmatory tests together. It can only be made stricter."
    state={<FieldState rec={recFor(recs, "design.alpha")} {...props} />}
  >
    <select value={String(design.alpha ?? 0.05)} onChange={(event) => updateConfig(setAlpha(config, Number(event.target.value) as Alpha))}>
      {ALPHA_LEVELS.map((level) => <option key={level} value={String(level)}>{formatG(level)}</option>)}
    </select>
  </Field>;
}

export function SensitivityEpsilonField(props: FieldProps) {
  const { config, updateConfig, recs } = props;
  const design = config.design;
  if (design === null || design === undefined) return null;
  const cells = sourceCells(config);
  const current = design.sensitivity_epsilon ?? null;
  const levels = EPSILON_LEVELS.map((level) => {
    const present = cells.length > 0 && cells.every((cell) => epsilonArms(config, cell, level).length > 0);
    const running = present && cells.every((cell) => epsilonArms(config, cell, level).some((arm) => compared(arm, "prng_epsilon")));
    return { level, present, running };
    // Only a control that runs in every comparison can be chosen. The level the study names stays listed
    // whatever became of its arm, so that it is shown for what it is and can be changed.
  }).filter(({ level, running }) => running || level === current);
  return <Field
    path="design.sensitivity_epsilon"
    label="Positive control that must be detected"
    note="'Equivalent' is only returned if this arm is detected on the same outcome. Whether it will be is not rated: how far a biased sampler moves an outcome is not known before a run. With 'None', a null result is inconclusive. A level is offered once its arm is switched on in every comparison, under Comparisons."
    state={<FieldState rec={recFor(recs, "design.sensitivity_epsilon")} {...props} />}
  >
    <select value={current === null ? "" : String(current)} onChange={(event) => updateConfig(setSensitivityEpsilon(config, event.target.value === "" ? null : Number(event.target.value) as Epsilon))}>
      <option value="">None</option>
      {levels.map(({ level, present, running }) => <option key={level} value={String(level)}>
        ε {level.toFixed(2)}{!present ? " (not in every comparison)" : !running ? " (switched off)" : ""}
      </option>)}
    </select>
  </Field>;
}

/** One outcome's margin: the card's, a declared one where the card states none and the outcome has a range, or none. */
export function MarginField(props: FieldProps & { cardId: string; endpoint: string }) {
  const { config, updateConfig, cards, recs, cardId, endpoint } = props;
  const design = config.design;
  const card = cards.find((candidate) => candidate.id === cardId);
  if (design === null || design === undefined || card === undefined) return null;
  const ref = { card_id: cardId, endpoint };
  const path = `design.margins.${cardId}/${endpoint}`;
  const label = metaFor(cardId, endpoint).label;
  const state = <FieldState rec={recFor(recs, path)} {...props} />;
  const sesoi = cardMargin(card, endpoint);
  if (sesoi !== null) return <ReadOnlyField path={path} label={label} value={<>±{formatG(sesoi)} · from the eval card {state}</>} />;

  const declared = (design.margins ?? []).filter((margin) => margin.card_id === cardId && margin.endpoint === endpoint).at(-1);
  // Whether the outcome can take a margin of any size. A test without a paired difference is named as such,
  // whatever its card says about a SESOI. (The smallest margin no longer answers this: one below a
  // thousandth of the range is itself refused.)
  const refusal = noMarginReason(card, endpoint);
  if (refusal !== null && declared === undefined) return <ReadOnlyField path={path} label={label} value={<>none {state}</>} note={`${label} ${refusal}.`} />;
  const bounds = metaFor(cardId, endpoint).range;
  const cap = bounds === null ? undefined : MARGIN_CAP_FRACTION * (bounds[1] - bounds[0]);
  const least = bounds === null ? undefined : MARGIN_FLOOR_FRACTION * (bounds[1] - bounds[0]);
  const limits = cap === undefined || least === undefined ? undefined
    : `Between ${formatG(least)} and ${formatG(cap)}: a thousandth to a tenth of the outcome's range. Leave empty for no margin.`;
  return <Field path={path} label={`${label}: smallest difference that matters (±)`} state={state} note={limits}>
    <NumberInput
      value={declared?.margin ?? null}
      fractional
      nullable
      min={least ?? 0}
      max={cap}
      onCommit={(value) => { if (value === null || value > 0) updateConfig(setMargin(config, ref, value)); }}
    />
  </Field>;
}

export function MarginsField(props: FieldProps) {
  const design = props.config.design;
  if (design === null || design === undefined) return null;
  return <FieldGroup path="design.margins" label="Smallest difference that matters" note="Fixed now. Never derived from a pilot.">
    {design.confirmatory.length === 0 ? <p className="field-note">Choose an outcome first.</p> : null}
    {design.confirmatory.map((ref) => <MarginField key={refKey(ref)} {...props} cardId={ref.card_id} endpoint={ref.endpoint} />)}
  </FieldGroup>;
}

export function ExpectationsField({ config, updateConfig }: FieldProps) {
  return <Field
    path="expectations"
    label="What do you expect to see?"
    note="Written before any result is seen. It is frozen into the pre-registration as context; it is not treated as evidence."
  >
    <textarea rows={6} value={config.expectations ?? ""} placeholder="Before seeing the results, we expect…" onChange={(event) => updateConfig(setExpectations(config, event.target.value))} />
  </Field>;
}
