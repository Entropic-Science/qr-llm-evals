import type { WizardStepProps } from "./index";

export function Judge({ config, updateConfig }: WizardStepProps) {
  return <section aria-labelledby="step-judge"><p className="eyebrow">Protocol 05</p><h2 id="step-judge">Judge &amp; narrative</h2><p className="step-intro">The judge runs at temperature 0, identically across arms. Scripted statistics remain separate from the narrative model.</p>
    <div className="field-stack"><label>Anthropic judge model<input value={config.judge.model} onChange={(event) => updateConfig({ ...config, judge: { ...config.judge, model: event.target.value, temperature: 0 } })} /></label>
      <label>Narrative model<select value={config.narrative_model.id} onChange={(event) => { const selected = config.models.find(({ id }) => id === event.target.value); if (selected) updateConfig({ ...config, narrative_model: selected }); }}>{config.models.map((model) => <option key={model.id} value={model.id}>{model.served_name}</option>)}</select></label>
      <label>Prior expectations<textarea rows={5} value={config.expectations ?? ""} onChange={(event) => updateConfig({ ...config, expectations: event.target.value })} placeholder="State expectations before observing results." /></label>
    </div>
  </section>;
}
