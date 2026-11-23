import type { DisplaySettings } from "@qr-evals/contracts";
import { useState } from "react";
import { setDisplaySettings, setJudge, setNarrativeModel, setSupervised } from "../../../lib/config-edits";
import { DEFAULT_JUDGE, JUDGE_MODELS } from "../../../lib/judge";
import { DEFAULT_DISPLAY_SETTINGS, type DisplayMode } from "../../../lib/supervised-settings";
import { Field, FieldGroup, NumberInput, ReadOnlyField, useFieldId, type FieldProps } from "./Field";

// The fields of the instruments: the judge that scores some evals, the model that writes the narrative, and
// the settings of supervised sessions.

const CUSTOM = "__custom__";
const judgeOf = ({ config }: FieldProps) => ({ ...DEFAULT_JUDGE, ...config.judge });

// ── judge ────────────────────────────────────────────────────────────────────

export function JudgeProviderField() {
  return <ReadOnlyField path="judge.provider" label="Judge provider" value="Anthropic" note="The judge is a Claude model called with your own Anthropic key, with the same settings for every arm." />;
}

export function JudgeModelField(props: FieldProps) {
  const { config, updateConfig } = props;
  const judge = judgeOf(props);
  const listed = JUDGE_MODELS.find(({ id }) => id === judge.model);
  // Whether the id is typed is remembered, not read off the id: a dated snapshot (claude-haiku-5-5-20261001)
  // begins with a listed id, and the box must not close under the hands when the text typed so far equals one.
  const [typing, setTyping] = useState(false);
  const known = typing ? undefined : listed;
  return <>
    <Field path="judge.model" label="Judge model" note={known?.note}>
      <select
        value={known ? judge.model : CUSTOM}
        onChange={(event) => {
          setTyping(event.target.value === CUSTOM);
          updateConfig(setJudge(config, { model: event.target.value === CUSTOM ? "" : event.target.value }));
        }}
      >
        {JUDGE_MODELS.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
        <option value={CUSTOM}>Another model (type its exact id)</option>
      </select>
    </Field>
    {known ? null : <div className="field"><label className="field__label">Exact model id
      <input value={judge.model ?? ""} placeholder="claude-…" onChange={(event) => updateConfig(setJudge(config, { model: event.target.value.trim() }))} />
    </label></div>}
  </>;
}

export function JudgeMaxTokensField(props: FieldProps) {
  const { config, updateConfig } = props;
  return <Field path="judge.max_tokens" label="Longest answer, in tokens" note="The judge's reply is cut off at this length. 1024 is enough for a score with a short reason.">
    <NumberInput value={judgeOf(props).max_tokens ?? 1024} min={64} max={16000} step={64} onCommit={(value) => { if (value !== null) updateConfig(setJudge(config, { max_tokens: value })); }} />
  </Field>;
}

export function JudgeThinkingField(props: FieldProps) {
  const { config, updateConfig } = props;
  return <Field
    path="judge.thinking"
    label="Extended thinking"
    note={"Off is cheaper and keeps each judgement a single pass. Sonnet 5.5 and Opus 5.5 refuse \"off\", so pick \"left to the model\" for them and raise the answer length, because thinking uses the same budget."}
  >
    <select value={judgeOf(props).thinking ?? "disabled"} onChange={(event) => updateConfig(setJudge(config, { thinking: event.target.value === "model_default" ? "model_default" : "disabled" }))}>
      <option value="disabled">Off: one direct pass</option>
      <option value="model_default">Left to the model</option>
    </select>
  </Field>;
}

export function JudgeTemperatureField(props: FieldProps) {
  const { config, updateConfig } = props;
  const temperature = judgeOf(props).temperature ?? null;
  return <>
    <Field path="judge.temperature" label="Temperature" note="Current Claude models reject a temperature, so leave it unsent unless you picked an older model that accepts one.">
      <select value={temperature === null ? "unset" : "set"} onChange={(event) => updateConfig(setJudge(config, { temperature: event.target.value === "set" ? 0 : null }))}>
        <option value="unset">Not sent: the model uses its own setting</option>
        <option value="set">Send a value</option>
      </select>
    </Field>
    {temperature === null ? null : <div className="field"><label className="field__label">Temperature value (0 to 1)
      <NumberInput value={temperature} fractional min={0} max={1} step={0.1} onCommit={(value) => { if (value !== null) updateConfig(setJudge(config, { temperature: Math.min(1, Math.max(0, value)) })); }} />
    </label></div>}
  </>;
}

// ── narrative ────────────────────────────────────────────────────────────────

/** The model that writes the report's narrative: one of the study's own models, taken whole. */
export function NarrativeModelField({ config, updateConfig }: FieldProps) {
  const current = config.narrative_model;
  const listed = config.models.some((model) => model.id === current.id);
  return <Field
    path="narrative_model"
    label="Narrative model"
    note="Writes the prose of the report. It may only restate numbers that are in the tables; figures and statistics come from scripts."
  >
    <select value={current.id} onChange={(event) => { const model = config.models.find(({ id }) => id === event.target.value); if (model) updateConfig(setNarrativeModel(config, model)); }}>
      {listed ? null : <option value={current.id}>{current.served_name} (not one of the study's models)</option>}
      {config.models.map((model) => <option key={model.id} value={model.id}>{model.served_name}</option>)}
    </select>
  </Field>;
}

// ── supervised sessions ──────────────────────────────────────────────────────

export function SessionDisplayField({ config, updateConfig }: FieldProps) {
  const supervised = config.supervised;
  if (supervised === null || supervised === undefined) return null;
  return <Field path="supervised.display" label="Display">
    <select value={supervised.display ?? "single"} onChange={(event) => updateConfig(setSupervised(config, { display: event.target.value === "tiled" ? "tiled" : "single" }))}>
      <option value="single">Single stream</option>
      <option value="tiled">Four-stream tile</option>
    </select>
  </Field>;
}

export function IntentionTimerField({ config, updateConfig }: FieldProps) {
  const supervised = config.supervised;
  if (supervised === null || supervised === undefined) return null;
  return <Field path="supervised.intention_timer_min" label="Intention timer (min)" note="2 to 5 minutes is recommended. The timer never starts a session; a person always does.">
    <NumberInput value={supervised.intention_timer_min ?? 3} min={1} onCommit={(value) => { if (value !== null) updateConfig(setSupervised(config, { intention_timer_min: value })); }} />
  </Field>;
}

export function RefocusField({ config, updateConfig }: FieldProps) {
  const supervised = config.supervised;
  if (supervised === null || supervised === undefined) return null;
  return <Field path="supervised.refocus_s" label="Refocus (seconds)">
    <NumberInput value={supervised.refocus_s ?? 30} min={0} onCommit={(value) => { if (value !== null) updateConfig(setSupervised(config, { refocus_s: value })); }} />
  </Field>;
}

export function GroupSessionField({ config, updateConfig }: FieldProps) {
  const id = useFieldId("supervised.group");
  const supervised = config.supervised;
  if (supervised === null || supervised === undefined) return null;
  return <div className="field" id={id} data-path="supervised.group">
    <label className="toggle-line">
      <input type="checkbox" checked={supervised.group ?? false} onChange={(event) => updateConfig(setSupervised(config, { group: event.target.checked }))} />
      Group session
    </label>
  </div>;
}

const MODE_LABELS: Record<DisplayMode, string> = { single: "Single stream", tiled: "Four-stream tile" };

/** How one display mode runs a session: lengths, streams, text size, whether thinking is shown. */
export function DisplaySettingsField({ config, updateConfig, mode }: FieldProps & { mode: DisplayMode }) {
  const supervised = config.supervised;
  if (supervised === null || supervised === undefined) return null;
  const settings: DisplaySettings = supervised.settings_by_display?.[mode] ?? DEFAULT_DISPLAY_SETTINGS[mode];
  const set = (patch: Partial<DisplaySettings>) => updateConfig(setDisplaySettings(config, mode, { ...settings, ...patch }));
  const at = (key: keyof DisplaySettings) => `supervised.settings_by_display.${mode}.${key}`;
  const number = (key: "session_min" | "break_min" | "streams", label: string, min: number) => <label className="field__label" data-path={at(key)}>{label}
    <NumberInput value={settings[key]} min={min} onCommit={(value) => { if (value !== null) set({ [key]: value }); }} />
  </label>;
  return <FieldGroup path={`supervised.settings_by_display.${mode}`} className="field-grid" label={MODE_LABELS[mode]}>
    {number("session_min", "Session (min)", 1)}
    {number("break_min", "Break (min)", 0)}
    {number("streams", "Streams", 1)}
    <label className="field__label" data-path={at("text_size")}>Text size
      <select value={settings.text_size} onChange={(event) => set({ text_size: event.target.value as DisplaySettings["text_size"] })}>
        <option value="sm">Small</option><option value="md">Medium</option><option value="lg">Large</option>
      </select>
    </label>
    <label className="toggle-line" data-path={at("show_thinking")}>
      <input type="checkbox" checked={settings.show_thinking} onChange={(event) => set({ show_thinking: event.target.checked })} />
      Show thinking
    </label>
  </FieldGroup>;
}
