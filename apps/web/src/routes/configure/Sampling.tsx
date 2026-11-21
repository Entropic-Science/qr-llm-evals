import type { Arm } from "@qr-evals/contracts";
import type { WizardStepProps } from "./index";

const samplingLabels: Record<Arm["sampling"], string> = { creative: "Creative sampling", t1_minp: "Temperature 1 + min-p 0.10", greedy: "Greedy" };
const sourceLabels: Record<Arm["source"], string> = { qrng: "QRNG · quantum", prng_seeded: "Seeded PRNG · control", prng_system: "System PRNG · control", prng_epsilon: "ε-biased PRNG · positive control", provider: "Provider PRNG (opaque)" };

export function Sampling({ config, updateConfig, onDeterministic }: WizardStepProps) {
  const updateArm = (index: number, patch: Partial<Arm>) => updateConfig({ ...config, arms: config.arms.map((arm, armIndex) => armIndex === index ? { ...arm, ...patch } : arm) });
  return <section aria-labelledby="step-sampling"><p className="eyebrow">Protocol 02</p><h2 id="step-sampling">Sampling &amp; source</h2><p className="step-intro">Every arm is explicit. Quantum and control sources remain visibly distinct throughout the run.</p>
    <div className="arm-ledger">{config.arms.map((arm, index) => {
      const greedyQuantum = arm.sampling === "greedy" && arm.source === "qrng";
      return <fieldset className="arm-row" key={arm.id}><legend>{arm.id.replaceAll("_", " ")}</legend>
        <label>Sampling<select value={arm.sampling} onChange={(event) => updateArm(index, { sampling: event.target.value as Arm["sampling"] })}>{Object.entries(samplingLabels).map(([value, label]) => <option key={value} value={value} disabled={value === "greedy" && arm.source === "qrng"}>{label}</option>)}</select></label>
        <label>Source<select value={arm.source} onChange={(event) => updateArm(index, { source: event.target.value as Arm["source"] })}>{Object.entries(sourceLabels).map(([value, label]) => <option key={value} value={value} disabled={value === "qrng" && arm.sampling === "greedy"}>{label}</option>)}</select></label>
        <label className="toggle-line"><input type="checkbox" checked={arm.deterministic ?? false} onChange={(event) => { updateArm(index, { deterministic: event.target.checked }); if (event.target.checked) onDeterministic(); }} /> Deterministic</label>
        {greedyQuantum && <p className="validation-note" role="alert">Greedy sampling consults no randomness, so it cannot use QRNG.</p>}
        {arm.source === "prng_epsilon" && <><label>Bias ε (in standard errors of the byte mean)<input type="number" min={0} max={5} step={0.01} value={arm.epsilon ?? 0} onChange={(event) => { const value = Number(event.target.value); if (Number.isFinite(value)) updateArm(index, { epsilon: value }); }} /></label><p className="validation-note">A positive control: its random numbers are deliberately bent, so it must never be compared as if it were a fair source. It needs a seed, and it runs only if readiness confirms the engine accepts the bend.</p></>}
      </fieldset>;
    })}</div>
  </section>;
}
