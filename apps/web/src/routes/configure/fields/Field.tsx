import type { EvalCard, ModelRef } from "@qr-evals/contracts";
import { createContext, useContext, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { RecChip } from "../../../components/RecChip";
import { useRecommended } from "../../../lib/config-edits";
import type { WizardStudyConfig } from "../../../lib/config-store";
import type { Rec } from "../../../lib/recommendations";
import type { Roster } from "../../../lib/roster";

/**
 * What every field and every settings panel is given. Nothing here belongs to the wizard, so the same
 * component renders in a step, on the settings page and in a map popover.
 */
export interface FieldProps {
  config: WizardStudyConfig;
  updateConfig: (config: WizardStudyConfig) => void;
  cards: readonly EvalCard[];
  modelOptions: readonly ModelRef[];
  /** `rate(config, cards)`, computed once by whoever renders the fields. */
  recs: readonly Rec[];
  /**
   * The roles the study's comparisons last had (control seeds, positive controls, the observed arm,
   * determinism). A study whose last comparison was unticked has no arms to read them from, so the page
   * remembers them: the next comparison ticked gets the same roles. Left out, such a study starts from none.
   */
  lastRoster?: Roster | null;
}

/**
 * A prefix for the element ids of the fields below it. The same field can be on the page twice (in a step and
 * in the settings dialog over it), and an id must name one element.
 */
export const FieldScope = createContext("");

/** The element id of the field that edits a study key path, for links from a problem to its field. */
export function fieldId(path: string, scope = ""): string {
  return `${scope}field-${path.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
}

/** `fieldId` within the scope the field is rendered in. */
export function useFieldId(path: string): string {
  return fieldId(path, useContext(FieldScope));
}

/** The chip of a setting and, where the value differs from the recommended one, the button that restores it. */
export function FieldState({ rec, config, cards, updateConfig }: Pick<FieldProps, "config" | "cards" | "updateConfig"> & { rec: Rec | undefined }) {
  if (rec === undefined) return null;
  // A rated setting (prompts, repetitions) can be short of its recommended value in two degrees; both get the button.
  const differs = rec.recommended !== null && (rec.state === "custom" || rec.state === "invalid" || rec.state === "insufficient" || rec.state === "marginal");
  const restored = differs ? useRecommended(rec.path)(config, cards) : config;
  return <span className="field__state">
    <RecChip rec={rec} />
    {differs && restored !== config ? <button
      type="button"
      className="text-button"
      onClick={(event) => {
        // The button goes away once the value is the recommended one; the focus moves to the chip beside it,
        // which now says so, instead of being dropped on the page.
        const chip = event.currentTarget.parentElement?.querySelector<HTMLElement>("button.rec");
        updateConfig(restored);
        chip?.focus();
      }}
    >Use recommended</button> : null}
  </span>;
}

interface FieldFrameProps {
  /** The study key path the field edits, as the panel registry lists it. */
  path: string;
  label: ReactNode;
  note?: ReactNode;
  state?: ReactNode;
  children: ReactNode;
}

/** A labelled control with its note. The label wraps the control, as everywhere in the setup pages. */
export function Field({ path, label, note, state, children }: FieldFrameProps) {
  return <div className="field" id={useFieldId(path)} data-path={path}>
    <label className="field__label">{label}{children}</label>
    {state}
    {note ? <p className="field-note">{note}</p> : null}
  </div>;
}

/** A group of controls under one legend: a set of choices, or a list. */
export function FieldGroup({ path, label, note, state, children, className }: FieldFrameProps & { className?: string }) {
  return <fieldset className={`field field--group${className ? ` ${className}` : ""}`} id={useFieldId(path)} data-path={path}>
    <legend>{label}</legend>
    {children}
    {state}
    {note ? <p className="field-note">{note}</p> : null}
  </fieldset>;
}

/** A value the study records and the page does not let anyone change. */
export function ReadOnlyField({ path, label, value, note }: { path: string; label: string; value: ReactNode; note?: ReactNode }) {
  return <div className="field field--readonly" id={useFieldId(path)} data-path={path}>
    <span className="field__label">{label}</span>
    <span className="field__value">{value}</span>
    {note ? <p className="field-note">{note}</p> : null}
  </div>;
}

interface NumberInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> {
  value: number | null | undefined;
  /** Called with every number typed, in or out of range: a draft may be wrong while it is edited. */
  onCommit: (value: number | null) => void;
  /** Whether fractions are accepted. Counts are whole. */
  fractional?: boolean;
  /** Whether an empty box means "no value". Otherwise an empty box is only a number half typed. */
  nullable?: boolean;
}

/**
 * A number box that does not fight the keyboard.
 *
 * While it has the focus it shows what was typed, so clearing the box to type a new number does not snap back
 * to the old one. Each number typed is passed on at once; the box shows the study's own value again on blur.
 */
export function NumberInput({ value, onCommit, fractional = false, nullable = false, ...rest }: NumberInputProps) {
  const [draft, setDraft] = useState<string | null>(null);
  return <input
    {...rest}
    type="number"
    inputMode={fractional ? "decimal" : "numeric"}
    step={rest.step ?? (fractional ? "any" : 1)}
    value={draft ?? (value === null || value === undefined ? "" : String(value))}
    onChange={(event) => {
      const text = event.target.value;
      setDraft(text);
      if (text.trim() === "") { if (nullable) onCommit(null); return; }
      const number = Number(text);
      if (Number.isFinite(number) && (fractional || Number.isInteger(number))) onCommit(number);
    }}
    onBlur={(event) => { setDraft(null); rest.onBlur?.(event); }}
  />;
}

interface ListInputProps<T> extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> {
  values: readonly T[];
  /** One entry from its text, or `undefined` for text that is not an entry yet. */
  parse: (text: string) => T | undefined;
  onCommit: (values: T[]) => void;
}

/** A list typed as comma-separated entries. It is passed on whenever every entry typed so far reads as one. */
export function ListInput<T extends string | number>({ values, parse, onCommit, ...rest }: ListInputProps<T>) {
  const [draft, setDraft] = useState<string | null>(null);
  return <input
    {...rest}
    value={draft ?? values.join(", ")}
    onChange={(event) => {
      const text = event.target.value;
      setDraft(text);
      const entries = text.split(",").map((entry) => entry.trim()).filter((entry) => entry !== "").map(parse);
      if (entries.every((entry) => entry !== undefined)) onCommit(entries as T[]);
    }}
    onBlur={() => setDraft(null)}
  />;
}
