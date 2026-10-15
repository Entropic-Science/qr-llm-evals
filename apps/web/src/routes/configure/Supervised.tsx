import type { WizardStepProps } from "./index";

export function Supervised({ config, updateConfig }: WizardStepProps) {
  const supervised = config.supervised;
  const enabled = supervised !== null && supervised !== undefined;
  return <section aria-labelledby="step-supervised"><p className="eyebrow">Protocol 04</p><h2 id="step-supervised">Supervised observation</h2><p className="step-intro">Observation is host-paced. Timers never start or resume a session; a person must always act.</p>
    <label className="protocol-choice"><input type="checkbox" checked={enabled} onChange={(event) => updateConfig({ ...config, supervised: event.target.checked ? { display: "single", intention_timer_min: 3, refocus_s: 30, group: false, settings_by_display: { single: { session_min: 30, break_min: 10, streams: 1, text_size: "md", show_thinking: true }, tiled: { session_min: 30, break_min: 10, streams: 4, text_size: "sm", show_thinking: false } } } : null })} /><span><strong>Include supervised sessions</strong><small>Study consciousness, observation, intent, and other potential correlates in real time.</small></span></label>
    {enabled && supervised && <div className="field-grid">
      <label>Display<select value={supervised.display ?? "single"} onChange={(event) => updateConfig({ ...config, supervised: { ...supervised, display: event.target.value as "single" | "tiled" } })}><option value="single">Single stream</option><option value="tiled">Four-stream tile</option></select></label>
      <label>Intention timer (min)<input type="number" min="2" max="5" value={supervised.intention_timer_min ?? 3} onChange={(event) => updateConfig({ ...config, supervised: { ...supervised, intention_timer_min: Number(event.target.value) } })} /></label>
      <label>Refocus (seconds)<input type="number" min="0" value={supervised.refocus_s ?? 30} onChange={(event) => updateConfig({ ...config, supervised: { ...supervised, refocus_s: Number(event.target.value) } })} /></label>
      <label className="toggle-line"><input type="checkbox" checked={supervised.group ?? false} onChange={(event) => updateConfig({ ...config, supervised: { ...supervised, group: event.target.checked } })} /> Group session</label>
    </div>}
  </section>;
}
