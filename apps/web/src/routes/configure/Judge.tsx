import { DEFAULT_JUDGE, JUDGE_MODELS, needsStudyJudge } from "../../lib/judge";
import type { WizardStepProps } from "./index";

const CUSTOM = "__custom__";

export function Judge({ config, updateConfig, cards }: WizardStepProps) {
  const judge = { ...DEFAULT_JUDGE, ...config.judge };
  const setJudge = (patch: Partial<typeof judge>) => updateConfig({ ...config, judge: { ...judge, ...patch } });
  const known = JUDGE_MODELS.find(({ id }) => id === judge.model);
  const used = needsStudyJudge(config, cards);
  const sendsTemperature = judge.temperature !== null && judge.temperature !== undefined;

  return <section aria-labelledby="step-judge"><p className="eyebrow">Protocol 05</p><h2 id="step-judge">Judge &amp; narrative</h2>
    <p className="step-intro">Some evals are scored by a second model, the judge. It is a Claude model called with your own Anthropic key, with the same settings for every arm. These settings are part of the pre-registration, so choose them before you export it.</p>
    {used ? null : <p className="validation-note">None of the selected evals uses the judge, so these settings will not be used and no Anthropic key is needed.</p>}
    <div className="field-stack">
      <label>Judge model
        <select value={known ? judge.model : CUSTOM} onChange={(event) => setJudge({ model: event.target.value === CUSTOM ? "" : event.target.value })}>
          {JUDGE_MODELS.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
          <option value={CUSTOM}>Another model (type its exact id)</option>
        </select>
      </label>
      {known ? <p className="field-note">{known.note}</p> : <label>Exact model id<input value={judge.model} placeholder="claude-…" onChange={(event) => setJudge({ model: event.target.value.trim() })} /></label>}
      <label>Longest answer, in tokens
        <input type="number" min={64} max={16000} step={64} value={judge.max_tokens ?? 1024} onChange={(event) => { const value = Number(event.target.value); if (Number.isFinite(value)) setJudge({ max_tokens: Math.round(value) }); }} />
      </label>
      <p className="field-note">The judge's reply is cut off at this length. 1024 is enough for a score with a short reason.</p>
      <label>Extended thinking
        <select value={judge.thinking ?? "disabled"} onChange={(event) => setJudge({ thinking: event.target.value === "model_default" ? "model_default" : "disabled" })}>
          <option value="disabled">Off: one direct pass</option>
          <option value="model_default">Left to the model</option>
        </select>
      </label>
      <p className="field-note">Off is cheaper and keeps each judgement a single pass. Sonnet 5.5 and Opus 5.5 refuse "off", so pick "left to the model" for them and raise the answer length, because thinking uses the same budget.</p>
      <label>Temperature
        <select value={sendsTemperature ? "set" : "unset"} onChange={(event) => setJudge({ temperature: event.target.value === "set" ? 0 : null })}>
          <option value="unset">Not sent: the model uses its own setting</option>
          <option value="set">Send a value</option>
        </select>
      </label>
      {sendsTemperature ? <label>Temperature value (0 to 1)<input type="number" min={0} max={1} step={0.1} value={judge.temperature ?? 0} onChange={(event) => { const value = Number(event.target.value); if (Number.isFinite(value)) setJudge({ temperature: Math.min(1, Math.max(0, value)) }); }} /></label> : null}
      <p className="field-note">Current Claude models reject a temperature, so leave it unsent unless you picked an older model that accepts one. You can try the settings with your key on the next step.</p>
      <label>Narrative model<select value={config.narrative_model.id} onChange={(event) => { const selected = config.models.find(({ id }) => id === event.target.value); if (selected) updateConfig({ ...config, narrative_model: selected }); }}>{config.models.map((model) => <option key={model.id} value={model.id}>{model.served_name}</option>)}</select></label>
      <label>Prior expectations<textarea rows={5} value={config.expectations ?? ""} onChange={(event) => updateConfig({ ...config, expectations: event.target.value })} placeholder="State expectations before observing results." /></label>
    </div>
  </section>;
}
