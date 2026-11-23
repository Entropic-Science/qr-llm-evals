import type { Arm, ModelRef } from "@qr-evals/contracts";
import { setArms, setCreatedAt, setEstimates, setModelField, setModels, setPilot, setSeeds } from "../../../lib/config-edits";
import { formatG } from "../../../lib/design";
import { EPSILON_LEVELS } from "../../../lib/roster";
import { SAMPLING_LABELS, SOURCE_LABELS } from "./compare";
import { Field, FieldGroup, ListInput, NumberInput, ReadOnlyField, useFieldId, type FieldProps } from "./Field";

// The fields only the settings page shows: the arms, models and seeds as they are written, the assumptions
// behind the estimates, the pilot, and the values a study records but nobody sets.

const text = (value: string) => (value === "" ? null : value);

// ── arms as written ──────────────────────────────────────────────────────────

function newArm(arms: readonly Arm[], model: string, seed: number): Arm {
  let n = arms.length + 1;
  while (arms.some((arm) => arm.id === `arm_${n}`)) n += 1;
  return {
    id: `arm_${n}`, model, sampling: "creative", source: "prng_seeded", study_seed: seed, epsilon: null,
    deterministic: false, observer_mode: "unsupervised", availability: "available", unavailable_reason: null,
  };
}

/**
 * Every arm with every one of its fields, to add, change or remove.
 *
 * This is the editor for studies whose arms are not a roster. It writes exactly what is typed and repairs
 * nothing: the models, the seed list and the supervised block are separate settings here.
 */
export function ArmTableField({ config, updateConfig }: FieldProps) {
  const id = useFieldId("arms[]");
  const { arms } = config;
  const change = (index: number, patch: Partial<Arm>) => updateConfig(setArms(config, arms.map((arm, at) => (at === index ? { ...arm, ...patch } : arm))));
  const models = [...new Set([...config.models.map((model) => model.id), ...arms.map((arm) => arm.model)])];
  return <div className="field" id={id} data-path="arms[]">
    <div className="arm-ledger">
      {arms.map((arm, index) => {
        const levels = [...new Set([...EPSILON_LEVELS, ...(arm.epsilon !== null && arm.epsilon !== undefined ? [arm.epsilon] : [])])].sort((a, b) => a - b);
        return <fieldset className="arm-row" key={index}>
          <legend>{arm.id || `arm ${index + 1}`}</legend>
          <label data-path="arms[].id">Arm id<input value={arm.id} pattern="[a-z0-9_]+" maxLength={64} onChange={(event) => change(index, { id: event.target.value })} /></label>
          <label data-path="arms[].model">Model<select value={arm.model} onChange={(event) => change(index, { model: event.target.value })}>
            {models.map((model) => <option key={model} value={model}>{model}</option>)}
          </select></label>
          <label data-path="arms[].sampling">Sampling<select value={arm.sampling} onChange={(event) => change(index, { sampling: event.target.value as Arm["sampling"] })}>
            {Object.entries(SAMPLING_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label data-path="arms[].source">Source<select value={arm.source} onChange={(event) => change(index, { source: event.target.value as Arm["source"] })}>
            {Object.entries(SOURCE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label data-path="arms[].study_seed">Seed<NumberInput value={arm.study_seed ?? null} nullable min={0} onCommit={(value) => change(index, { study_seed: value })} /></label>
          <label data-path="arms[].epsilon">Bias ε<select value={arm.epsilon === null || arm.epsilon === undefined ? "" : String(arm.epsilon)} onChange={(event) => change(index, { epsilon: event.target.value === "" ? null : Number(event.target.value) })}>
            <option value="">None</option>
            {levels.map((level) => <option key={level} value={String(level)}>ε {formatG(level)}</option>)}
          </select></label>
          <label data-path="arms[].observer_mode">Observer mode<select value={arm.observer_mode ?? "unsupervised"} onChange={(event) => change(index, { observer_mode: event.target.value === "supervised" ? "supervised" : "unsupervised" })}>
            <option value="unsupervised">Unsupervised</option><option value="supervised">Supervised</option>
          </select></label>
          <label data-path="arms[].availability">Runs<select value={arm.availability ?? "available"} onChange={(event) => change(index, { availability: event.target.value === "unavailable" ? "unavailable" : "available" })}>
            <option value="available">Yes</option><option value="unavailable">No: listed, switched off</option>
          </select></label>
          <label data-path="arms[].unavailable_reason">Reason it is switched off<input value={arm.unavailable_reason ?? ""} onChange={(event) => change(index, { unavailable_reason: text(event.target.value) })} /></label>
          <label className="toggle-line" data-path="arms[].deterministic"><input type="checkbox" checked={arm.deterministic ?? false} onChange={(event) => change(index, { deterministic: event.target.checked })} /> Deterministic</label>
          <button type="button" className="text-button" onClick={() => updateConfig(setArms(config, arms.filter((_, at) => at !== index)))}>Remove {arm.id || `arm ${index + 1}`}</button>
        </fieldset>;
      })}
    </div>
    <button type="button" className="button button--secondary" onClick={() => updateConfig(setArms(config, [...arms, newArm(arms, config.models[0]?.id ?? "", config.seeds?.[0] ?? 1)]))}>Add arm</button>
    <p className="field-note">A seeded or biased arm needs a seed that is in the seed list; a biased arm needs ε; a quantum or provider arm takes neither. An arm that is switched off needs a reason. The server checks all of this when the study is rendered or launched.</p>
  </div>;
}

// ── models as written ────────────────────────────────────────────────────────

const FAMILIES: Record<ModelRef["family"], string> = { qr_llm: "Quantum-ready (qr-llm)", external: "External" };
const PROVIDERS: Record<ModelRef["provider"], string> = { qr_server: "qr-server", openai: "OpenAI", anthropic_compat: "Anthropic-compatible", custom: "Custom endpoint" };

export function ModelsTableField({ config, updateConfig, modelOptions }: FieldProps) {
  const id = useFieldId("models[]");
  const { models } = config;
  // A change of a model's id is followed by the arms and the narrative model that name it (`setModelField`).
  const change = (index: number, patch: Partial<ModelRef>) => updateConfig(setModelField(config, index, patch));
  const offered = modelOptions.filter((option) => !models.some((model) => model.id === option.id));
  const users = (id: string) => config.arms.filter((arm) => arm.model === id).length;
  return <div className="field" id={id} data-path="models[]">
    <div className="arm-ledger">
      {models.map((model, index) => <fieldset className="arm-row" key={index}>
        <legend>{model.id || `model ${index + 1}`}</legend>
        <label data-path="models[].id">Model id<input value={model.id} onChange={(event) => change(index, { id: event.target.value })} /></label>
        <label data-path="models[].served_name">Served name<input value={model.served_name} onChange={(event) => change(index, { served_name: event.target.value })} /></label>
        <label data-path="models[].family">Family<select value={model.family} onChange={(event) => change(index, { family: event.target.value as ModelRef["family"] })}>
          {Object.entries(FAMILIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label data-path="models[].provider">Provider<select value={model.provider} onChange={(event) => change(index, { provider: event.target.value as ModelRef["provider"] })}>
          {Object.entries(PROVIDERS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label data-path="models[].checkpoint">Checkpoint<input value={model.checkpoint ?? ""} placeholder="org/model@revision" onChange={(event) => change(index, { checkpoint: text(event.target.value) })} /></label>
        {/* A model an arm runs on stays: removing it would leave that arm on a model the study does not list. */}
        <button type="button" className="text-button" disabled={users(model.id) > 0} onClick={() => updateConfig(setModels(config, models.filter((_, at) => at !== index)))}>Remove {model.id || `model ${index + 1}`}</button>
        {users(model.id) > 0 ? <small>{users(model.id) === 1 ? "1 arm runs" : `${users(model.id)} arms run`} on this model, so it cannot be removed here. Untick its comparisons first, or move its arms in the arm table.</small> : null}
      </fieldset>)}
    </div>
    {offered.map((option) => <button type="button" className="button button--secondary" key={option.id} onClick={() => updateConfig(setModels(config, [...models, option]))}>Add {option.served_name}</button>)}
    <button type="button" className="button button--secondary" onClick={() => updateConfig(setModels(config, [...models, { id: "", family: "external", provider: "custom", served_name: "", checkpoint: null }]))}>Add a model by hand</button>
    <p className="field-note">The served name is the exact string sent to the provider. Every arm names one of these models by its id; changing an id here changes it in the arms and in the narrative model too. The checkpoint is the exact revision of the weights. Nothing fills it in later: without one, the pre-registration says in a validity warning that the revision is not frozen.</p>
  </div>;
}

// ── seeds ────────────────────────────────────────────────────────────────────

export function SeedListField({ config, updateConfig }: FieldProps) {
  return <Field path="seeds[]" label="Seed list" note="Whole numbers, separated by commas. Every seed an arm runs on has to be listed here once.">
    <ListInput
      values={config.seeds ?? []}
      inputMode="numeric"
      parse={(entry) => (/^\d+$/.test(entry) ? Number(entry) : undefined)}
      onCommit={(seeds) => updateConfig(setSeeds(config, seeds))}
    />
  </Field>;
}

// ── estimate assumptions ─────────────────────────────────────────────────────

export function ThroughputField({ config, updateConfig }: FieldProps) {
  const assumed = config.estimates?.tps_by_model ?? {};
  const ids = [...new Set([...config.models.map((model) => model.id), ...Object.keys(assumed)])];
  return <FieldGroup path="estimates.tps_by_model" label="Generated tokens per second, by model" note="An assumption for the time estimate, not a measurement. Leave a model empty to assume nothing for it.">
    {ids.map((id) => <label className="field__label" key={id}><code>{id}</code>
      <NumberInput
        value={assumed[id] ?? null}
        fractional
        nullable
        min={0}
        onCommit={(value) => {
          const next = { ...assumed };
          if (value === null) delete next[id]; else next[id] = value;
          updateConfig(setEstimates(config, { tps_by_model: next }));
        }}
      />
    </label>)}
  </FieldGroup>;
}

export function ReadingSpeedField({ config, updateConfig }: FieldProps) {
  return <Field path="estimates.reading_tps" label="Reading speed, tokens per second" note="How fast an observer is assumed to read a stream.">
    <NumberInput value={config.estimates?.reading_tps ?? 45} fractional min={0} onCommit={(value) => { if (value !== null && value > 0) updateConfig(setEstimates(config, { reading_tps: value })); }} />
  </Field>;
}

export function TiledStreamsField({ config, updateConfig }: FieldProps) {
  return <Field path="estimates.tiled_streams" label="Streams assumed in a tiled session">
    <NumberInput value={config.estimates?.tiled_streams ?? 4} min={1} onCommit={(value) => { if (value !== null) updateConfig(setEstimates(config, { tiled_streams: value })); }} />
  </Field>;
}

export function EstimateSessionField({ config, updateConfig }: FieldProps) {
  return <Field path="estimates.session_min" label="Session length assumed, in minutes">
    <NumberInput value={config.estimates?.session_min ?? 30} min={1} onCommit={(value) => { if (value !== null) updateConfig(setEstimates(config, { session_min: value })); }} />
  </Field>;
}

// ── pilot ────────────────────────────────────────────────────────────────────

export function PilotEnabledField({ config, updateConfig }: FieldProps) {
  const id = useFieldId("pilot.enabled");
  return <div className="field" id={id} data-path="pilot.enabled">
    <label className="toggle-line">
      <input type="checkbox" checked={config.pilot?.enabled ?? true} onChange={(event) => updateConfig(setPilot(config, { enabled: event.target.checked }))} />
      Preselect a pilot run in the launch dialog
    </label>
    <p className="field-note">The launch dialog offers both kinds of run whatever this says; this chooses which one is selected when it opens. A pilot runs the study as configured and is labelled non-confirmatory. It does not set margins.</p>
  </div>;
}

export function PilotPromptsField({ config, updateConfig }: FieldProps) {
  return <Field path="pilot.prompts" label="Pilot prompts" note="Recorded with the study. A designed study's margins never come from it.">
    <NumberInput value={config.pilot?.prompts ?? 200} min={1} onCommit={(value) => { if (value !== null) updateConfig(setPilot(config, { prompts: value })); }} />
  </Field>;
}

// ── recorded, not set ────────────────────────────────────────────────────────

// A hash the study does not carry is said to be missing. Nothing in the harness writes one into a study later.
const hash = (value: string | null | undefined) => (value ? <code>{value}</code> : "not pinned");
const HASH_NOTE = "This page cannot set it. It can be written into a study file by hand; without it the pre-registration carries a validity warning.";

/** The date the pre-registration prints as the configuration date. A study started from the template carries the template's. */
export function CreatedAtField({ config, updateConfig }: FieldProps) {
  const id = useFieldId("created_at");
  return <div className="field" id={id} data-path="created_at">
    <span className="field__label">Configuration date</span>
    <span className="field__value"><time dateTime={config.created_at}>{config.created_at}</time></span>
    <button type="button" className="button button--secondary" onClick={() => updateConfig(setCreatedAt(config, new Date()))}>Set to now</button>
    <p className="field-note">Printed in the pre-registration as the configuration date. A study started from the Study 1 template or from a file carries that one&apos;s date until it is set here.</p>
  </div>;
}

/** What the study records and the page does not let anyone change: versions, the licence, and the hashes of assets and rubrics. */
export function RecordFields({ config }: FieldProps) {
  return <>
    <ReadOnlyField path="schema_version" label="Study file version" value={String(config.schema_version ?? 1)} />
    <ReadOnlyField path="template" label="Template" value={config.template ?? "none"} note="Set by the file the study started from." />
    <ReadOnlyField path="data_licence" label="Data licence" value={config.data_licence ?? "CC-BY-4.0"} note="Every study on this harness shares its data under this licence." />
    <ReadOnlyField path="design.version" label="Design block version" value={config.design ? String(config.design.version ?? 1) : "no design block"} />
    <ReadOnlyField path="judge.rubric_hash" label="Judge rubric hash" value={hash(config.judge.rubric_hash)} note="Each eval's rubric has a hash of its own, recorded with every judgement." />
    <ReadOnlyField path="supervised.target_pool_hash" label="Target pool hash" value={config.supervised ? hash(config.supervised.target_pool_hash) : "no supervised sessions"} note={config.supervised && !config.supervised.target_pool_hash ? HASH_NOTE : undefined} />
    <ReadOnlyField path="supervised.questionnaires_hash" label="Questionnaires hash" value={config.supervised ? hash(config.supervised.questionnaires_hash) : "no supervised sessions"} note={config.supervised && !config.supervised.questionnaires_hash ? HASH_NOTE : undefined} />
  </>;
}
