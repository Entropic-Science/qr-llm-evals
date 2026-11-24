import { setConditioning } from "../../../lib/config-edits";
import {
  BYTES_PER_TOKEN, DIGEST_BYTES, HASH_LABELS, MAX_IN_BYTES, MODE_LABELS, conditioningProblems, rawBytesPerToken, readRatio, resolveConditioning,
  type ConditioningHash, type ConditioningMode,
} from "../../../lib/conditioning";
import { Field, FieldGroup, NumberInput, type FieldProps } from "./Field";

// How the bytes of the entropy server reach the arms that draw from it: hashed, or as the device measured them.

const count = (value: number) => value.toLocaleString("en-US");
const UNUSED = "Not used for raw bytes.";

/** Hashed or raw. A study from before the setting has no block and is shown as raw, which is how it ran. */
export function ConditioningModeField({ config, updateConfig }: FieldProps) {
  const { mode } = resolveConditioning(config.conditioning);
  const absent = config.conditioning === null || config.conditioning === undefined;
  return <FieldGroup
    path="conditioning.mode"
    label="Entropy served to the quantum arm"
    note={mode === "hash_truncate"
      ? "The entropy server hashes its raw read block by block and serves only part of each digest. The bytes are uniform whatever the device's own byte distribution is, so the quantum arm and the seeded controls start from the same distribution."
      : <>
        The quantum arm samples from the bytes exactly as the device measured them. Raw device bytes are not uniform, so a raw quantum arm differs from the pseudo-random arms by the device&rsquo;s byte statistics as well as by the source, and the harness has no matched control for that.
        {absent ? " This study has no conditioning block: it is from before the setting, and it runs on raw bytes." : ""}
      </>}
  >
    {(Object.keys(MODE_LABELS) as ConditioningMode[]).map((option) => <label className="toggle-line" key={option}>
      <input type="radio" name="conditioning-mode" checked={mode === option} onChange={() => updateConfig(setConditioning(config, { mode: option }))} />
      {MODE_LABELS[option]}
    </label>)}
  </FieldGroup>;
}

export function ConditioningHashField({ config, updateConfig }: FieldProps) {
  const { mode, hash } = resolveConditioning(config.conditioning);
  return <Field path="conditioning.hash" label="Hash" note={mode === "raw" ? UNUSED : `A ${HASH_LABELS[hash]} digest is ${DIGEST_BYTES[hash]} bytes.`}>
    <select value={hash} disabled={mode === "raw"} onChange={(event) => updateConfig(setConditioning(config, { hash: event.target.value as ConditioningHash }))}>
      {(Object.keys(HASH_LABELS) as ConditioningHash[]).map((option) => <option key={option} value={option}>{HASH_LABELS[option]}</option>)}
    </select>
  </Field>;
}

export function ConditioningInBytesField({ config, updateConfig }: FieldProps) {
  const { mode, in_bytes, keep_bytes } = resolveConditioning(config.conditioning);
  return <Field path="conditioning.in_bytes" label="Raw bytes hashed per block" note={mode === "raw" ? UNUSED : `At least twice the bytes kept (${2 * keep_bytes}), at most ${count(MAX_IN_BYTES)}.`}>
    <NumberInput value={in_bytes} min={2} max={MAX_IN_BYTES} disabled={mode === "raw"} onCommit={(value) => { if (value !== null) updateConfig(setConditioning(config, { in_bytes: value })); }} />
  </Field>;
}

export function ConditioningKeepBytesField({ config, updateConfig }: FieldProps) {
  const { mode, hash, keep_bytes } = resolveConditioning(config.conditioning);
  const half = DIGEST_BYTES[hash] / 2;
  return <Field path="conditioning.keep_bytes" label="Bytes kept from each digest" note={mode === "raw" ? UNUSED : `1 to ${half}. Serving more than half of each digest may compromise the integrity of the signal.`}>
    <NumberInput value={keep_bytes} min={1} max={half} disabled={mode === "raw"} onCommit={(value) => { if (value !== null) updateConfig(setConditioning(config, { keep_bytes: value })); }} />
  </Field>;
}

/** What the parameters cost, and what the server would refuse. */
export function ConditioningSummary({ config }: FieldProps) {
  const resolved = resolveConditioning(config.conditioning);
  const problems = conditioningProblems(config.conditioning);
  if (resolved.mode === "raw") {
    return <p className="field-note">Each token uses the bytes it draws from the device as they are.</p>;
  }
  return <>
    <p className="field-note">
      {resolved.keep_bytes} of every {resolved.in_bytes} bytes read from the device are served, so each byte a token uses costs {Number(readRatio(config.conditioning).toFixed(2))} raw bytes:
      {" "}{count(rawBytesPerToken(config.conditioning))} for a token of {count(BYTES_PER_TOKEN)} bytes, the sampler&apos;s default draw. The seeded and system pseudo-random arms are uniform already and are not hashed.
    </p>
    {problems.map((problem) => <p className="field-note" role="alert" key={problem.path}>{problem.message}</p>)}
  </>;
}
