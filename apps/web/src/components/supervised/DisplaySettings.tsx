import type { DisplaySettings as Settings } from "@qr-evals/contracts";
import type { DisplayMode } from "../../lib/supervised-settings";

export function DisplaySettings({ mode, settings, onMode, onChange }: { mode: DisplayMode; settings: Settings; onMode: (mode: DisplayMode) => void; onChange: (settings: Settings) => void }) {
  return <fieldset className="display-settings"><legend>Display</legend><label>Layout<select value={mode} onChange={(event) => onMode(event.target.value as DisplayMode)}><option value="single">Single readout</option><option value="tiled">Tiled comparison</option></select></label><label>Text size<select value={settings.text_size} onChange={(event) => onChange({ ...settings, text_size: event.target.value as Settings["text_size"] })}><option value="sm">Small</option><option value="md">Medium</option><option value="lg">Large</option></select></label><label className="toggle-line"><input type="checkbox" checked={settings.show_thinking} onChange={(event) => onChange({ ...settings, show_thinking: event.target.checked })} /> Show model thinking</label></fieldset>;
}
