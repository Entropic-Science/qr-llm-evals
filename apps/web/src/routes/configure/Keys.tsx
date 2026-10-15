import type { WizardStepProps } from "./index";

export function Keys({ keys, setKeys }: WizardStepProps) {
  return <section aria-labelledby="step-keys"><p className="eyebrow">Protocol 06 · volatile memory</p><h2 id="step-keys">Access keys</h2><p className="step-intro">Keys stay in this page's component state, are sent once at launch, and are never stored in this browser configuration.</p>
    <div className="field-stack">
      <label>api.entropic.science launch key<input type="password" autoComplete="off" value={keys.launch} onChange={(event) => setKeys({ ...keys, launch: event.target.value })} placeholder="qrk_…" /></label>
      <label>Anthropic judge key<input type="password" autoComplete="off" value={keys.anthropic} onChange={(event) => setKeys({ ...keys, anthropic: event.target.value })} placeholder="sk-ant-…" /></label>
      <label>External provider key <span className="optional">optional</span><input type="password" autoComplete="off" value={keys.external} onChange={(event) => setKeys({ ...keys, external: event.target.value })} /></label>
    </div><p className="privacy-note">Nothing typed here is included in localStorage or the share link.</p>
  </section>;
}
