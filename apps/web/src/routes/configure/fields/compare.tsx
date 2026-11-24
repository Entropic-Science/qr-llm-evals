import type { Arm, ModelRef } from "@qr-evals/contracts";
import { OBSERVED_ARM_IN_DESIGN, OBSERVED_ARM_IN_FIXED_PLAN } from "../../../lib/map/fixed-plan";
import { recFor } from "../../../lib/recommendations";
import {
  DEFAULT_SEEDS, EPSILON_LEVELS, MAX_CONTROL_SEEDS, MIN_CONTROL_SEEDS, ROSTER_SAMPLINGS, applyRoster, epsilonState,
  nearestRoster, rosterFromArms, withCell, withEpsilon, withSeedCount, type EpsilonState, type Roster, type RosterMemory,
  type RosterSampling,
} from "../../../lib/roster";
import { Field, FieldGroup, FieldState, NumberInput, useFieldId, type FieldProps } from "./Field";

// The fields of "what is compared". They edit the study's arms through its roster (`lib/roster.ts`): the
// comparisons, the control seeds, the positive controls and the observed arm. A study whose arms are not a
// roster shows none of them; `CustomArmsNotice` says so and offers to rebuild. Their element ids are those of
// the roster (`roster.seeds`), not of the study keys, which belong to the fields that edit the lists as written.

export const SAMPLING_LABELS: Record<Arm["sampling"], string> = {
  creative: "Creative sampling",
  t1_minp: "Temperature 1 + min-p 0.10",
  greedy: "Greedy",
};
export const SOURCE_LABELS: Record<Arm["source"], string> = {
  qrng: "Quantum",
  prng_seeded: "Seeded pseudo-random · control",
  prng_system: "System pseudo-random · control",
  prng_epsilon: "Biased pseudo-random · positive control",
  provider: "Provider's own (opaque)",
};
const EPSILON_STATE_TEXT: Record<EpsilonState, string> = { absent: "absent", off: "planned, off", on: "on" };
const NEXT_EPSILON_STATE: Record<EpsilonState, EpsilonState> = { absent: "off", off: "on", on: "absent" };

/** What a study without arms is given back: its own seed list, and the roles its comparisons last had on this page. */
function memoryOf(config: FieldProps["config"], lastRoster: Roster | null | undefined): RosterMemory {
  const seeds = config.seeds ?? [...DEFAULT_SEEDS];
  if (lastRoster === null || lastRoster === undefined) return { seeds };
  return { seeds, epsilons: lastRoster.epsilons, supervised: lastRoster.supervised, deterministic: lastRoster.deterministic };
}

/** The study's roster and the way to change it, or `null` for arms written by hand. */
export function rosterOf({ config, updateConfig, modelOptions, lastRoster }: FieldProps): { roster: Roster; setRoster: (roster: Roster) => void } | null {
  const roster = rosterFromArms(config.arms, memoryOf(config, lastRoster));
  if (roster === null) return null;
  return { roster, setRoster: (next) => updateConfig(applyRoster(config, next, modelOptions)) };
}

/** The models a comparison can be made on: only the quantum-ready lane has a quantum arm to compare. */
function quantumModels(props: FieldProps): ModelRef[] {
  const seen = new Map<string, ModelRef>();
  for (const model of [...props.config.models, ...props.modelOptions]) if (!seen.has(model.id)) seen.set(model.id, model);
  return [...seen.values()].filter((model) => model.family === "qr_llm");
}

export function CellGridField(props: FieldProps) {
  const held = rosterOf(props);
  if (held === null) return null;
  const { roster, setRoster } = held;
  const models = quantumModels(props);
  const ticked = (model: string, sampling: RosterSampling) => roster.cells.some((cell) => cell.model === model && cell.sampling === sampling);
  return <FieldGroup path="roster.cells" className="cell-grid" label="Models and sampling presets" note="Each ticked pair is its own comparison. Pairs are never pooled.">
    {models.length === 0 ? <p className="field-note">No quantum-ready model is offered by this server.</p> : null}
    {models.flatMap((model) => ROSTER_SAMPLINGS.map((sampling) => <label className="toggle-line" key={`${model.id}/${sampling}`}>
      <input type="checkbox" checked={ticked(model.id, sampling)} onChange={(event) => setRoster(withCell(roster, { model: model.id, sampling }, event.target.checked))} />
      {model.served_name} · {SAMPLING_LABELS[sampling]}
    </label>))}
  </FieldGroup>;
}

export function QuantumArmField(props: FieldProps) {
  if (rosterOf(props) === null) return null;
  // The chip carries the value, so it is not printed a second time beside it.
  return <div className="field field--readonly role-row" data-path="roster.quantum">
    <span className="field__label">Quantum arm</span>
    <FieldState rec={recFor(props.recs, "roster.quantum")} {...props} />
  </div>;
}

export function ControlSeedsField(props: FieldProps) {
  const held = rosterOf(props);
  if (held === null) return null;
  const { roster, setRoster } = held;
  return <Field
    path="roster.seeds"
    label="Control seeds"
    note="Seeded pseudo-random runs. Their mean is the comparison side; pairs of them are the negative control."
    state={<FieldState rec={recFor(props.recs, "roster.seeds")} {...props} />}
  >
    <NumberInput
      value={roster.seeds.length}
      min={MIN_CONTROL_SEEDS}
      max={MAX_CONTROL_SEEDS}
      onCommit={(count) => { if (count !== null && count >= MIN_CONTROL_SEEDS && count <= MAX_CONTROL_SEEDS) setRoster(withSeedCount(roster, count)); }}
    />
  </Field>;
}

export function EpsilonArmsField(props: FieldProps) {
  const held = rosterOf(props);
  if (held === null) return null;
  const { roster, setRoster } = held;
  return <FieldGroup
    path="roster.epsilons"
    className="role-row"
    label="Positive control (ε)"
    note="A deliberately biased pseudo-random arm. If the analysis cannot detect it, it cannot claim that nothing is there."
  >
    <div className="chip-row">
      {EPSILON_LEVELS.map((level) => {
        const state = epsilonState(roster, level);
        const next = NEXT_EPSILON_STATE[state];
        return <button
          type="button"
          key={level}
          className={`chip-toggle is-${state}`}
          aria-label={`ε ${level.toFixed(2)}: ${EPSILON_STATE_TEXT[state]}. Switch to ${EPSILON_STATE_TEXT[next]}.`}
          onClick={() => setRoster(withEpsilon(roster, level, next))}
        >
          <span>ε {level.toFixed(2)}</span><small>{EPSILON_STATE_TEXT[state]}</small>
        </button>;
      })}
    </div>
  </FieldGroup>;
}

export function SupervisedToggleField(props: FieldProps) {
  const id = useFieldId("roster.supervised");
  const held = rosterOf(props);
  if (held === null) return null;
  const { roster, setRoster } = held;
  return <div className="field" id={id} data-path="roster.supervised">
    <label className="toggle-line">
      <input type="checkbox" checked={roster.supervised} onChange={(event) => setRoster({ ...roster, supervised: event.target.checked })} />
      Supervised sessions
    </label>
    {/* A study without a design block is analysed with the fixed plan, which does test the observed arm. */}
    <p className="field-note">Adds an observed quantum arm. {props.config.design != null ? OBSERVED_ARM_IN_DESIGN : OBSERVED_ARM_IN_FIXED_PLAN}</p>
  </div>;
}

export function SeedValuesField(props: FieldProps) {
  const held = rosterOf(props);
  if (held === null) return null;
  const { roster, setRoster } = held;
  return <FieldGroup path="roster.seed_values" label="Seed values" note="One seeded control run per value, the same in every comparison. Biased arms run on a seed of their own.">
    <div className="chip-row">
      {roster.seeds.map((seed, index) => <label key={index} className="field__label">
        <span className="sr-only">Seed {index + 1}</span>
        <NumberInput
          value={seed}
          min={0}
          onCommit={(value) => {
            // Two control runs on one seed are one run twice; the edit waits for a seed of its own.
            if (value === null || value < 0 || roster.seeds.includes(value)) return;
            setRoster({ ...roster, seeds: roster.seeds.map((existing, at) => (at === index ? value : existing)) });
          }}
        />
      </label>)}
    </div>
  </FieldGroup>;
}

export function DeterministicField(props: FieldProps) {
  const id = useFieldId("roster.deterministic");
  const held = rosterOf(props);
  if (held === null) return null;
  const { roster, setRoster } = held;
  return <div className="field" id={id} data-path="roster.deterministic">
    <label className="toggle-line">
      <input type="checkbox" checked={roster.deterministic} onChange={(event) => setRoster({ ...roster, deterministic: event.target.checked })} />
      Deterministic
    </label>
    <p className="field-note">Asks the engine for replayable sampling in every arm. A quantum arm is never replayable, whatever this says.</p>
  </div>;
}

/** The arms as they will run, for reading. The editable table is in the advanced settings. */
export function ArmTable({ config }: Pick<FieldProps, "config">) {
  return <div className="design-table-frame">
    <table className="design-table">
      <caption className="sr-only">Arms of this study</caption>
      <thead><tr><th scope="col">Arm</th><th scope="col">Model · preset</th><th scope="col">Source</th><th scope="col">Seed</th><th scope="col">Runs</th></tr></thead>
      <tbody>
        {config.arms.map((arm) => <tr key={arm.id}>
          <th scope="row"><code>{arm.id}</code></th>
          <td data-label="Model · preset">{arm.model} · {arm.sampling}</td>
          <td data-label="Source">{SOURCE_LABELS[arm.source]}{arm.epsilon !== null && arm.epsilon !== undefined ? ` · ε ${arm.epsilon.toFixed(2)}` : ""}{arm.observer_mode === "supervised" ? " · observed" : ""}</td>
          <td data-label="Seed">{arm.study_seed ?? "none"}</td>
          <td data-label="Runs">{arm.availability === "unavailable" ? "switched off" : "yes"}</td>
        </tr>)}
      </tbody>
    </table>
  </div>;
}

/** Shown in place of the roster fields when the arms are not a roster. */
export function CustomArmsNotice({ config, updateConfig, modelOptions }: FieldProps) {
  if (rosterFromArms(config.arms, config.seeds ?? DEFAULT_SEEDS) !== null) return null;
  return <div className="field">
    <p className="validation-note">These arms were written by hand. Rebuild from roles to edit them here.</p>
    <ArmTable config={config} />
    <button
      type="button"
      className="button button--secondary"
      onClick={() => updateConfig(applyRoster(config, nearestRoster(config.arms, config.seeds ?? DEFAULT_SEEDS), modelOptions))}
    >Rebuild from roles</button>
    <p className="field-note">Rebuilding keeps the comparisons that have a quantum arm, every control seed and every biased level, and rewrites the arms in the standard roles. The arm table under All settings edits them as written.</p>
  </div>;
}
