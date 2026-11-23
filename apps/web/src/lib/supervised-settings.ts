import type { DisplaySettings, StudyConfig } from "@qr-evals/contracts";
import { browserStorage, loadConfig, saveConfig, type WizardStudyConfig } from "./config-store";

export type DisplayMode = "single" | "tiled";

const PREFIX = "qr-evals.supervised-display.v1.";
export const DEFAULT_DISPLAY_SETTINGS: Record<DisplayMode, DisplaySettings> = {
  single: { session_min: 30, break_min: 10, streams: 1, text_size: "md", show_thinking: true },
  tiled: { session_min: 30, break_min: 10, streams: 4, text_size: "sm", show_thinking: false },
};

function valid(value: unknown): value is DisplaySettings {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<DisplaySettings>;
  return typeof item.session_min === "number" && item.session_min > 0 && typeof item.break_min === "number" && item.break_min >= 0 && typeof item.streams === "number" && item.streams >= 1 && ["sm", "md", "lg"].includes(item.text_size ?? "") && typeof item.show_thinking === "boolean";
}

export function loadDisplaySettings(mode: DisplayMode, storage: Storage | null = browserStorage()): DisplaySettings {
  try {
    const item = storage?.getItem(`${PREFIX}${mode}`);
    if (item) { const parsed: unknown = JSON.parse(item); if (valid(parsed)) return parsed; }
  } catch { /* Fall through to the preregistered config/default. */ }
  const configured = loadConfig(storage).config.supervised?.settings_by_display?.[mode];
  return configured && valid(configured) ? configured : { ...DEFAULT_DISPLAY_SETTINGS[mode] };
}

export function saveDisplaySettings(mode: DisplayMode, settings: DisplaySettings, storage: Storage | null = browserStorage()): boolean {
  if (!valid(settings) || storage === null) return false;
  try { storage.setItem(`${PREFIX}${mode}`, JSON.stringify(settings)); } catch { return false; }
  // Only the stored draft follows the display: a study shown from a share link, or the template standing in
  // for a draft that could not be read, is not written over the draft from here.
  const { config, source } = loadConfig(storage);
  if (source === "storage" && config.supervised) {
    const updated: StudyConfig = { ...config, supervised: { ...config.supervised, settings_by_display: { ...config.supervised.settings_by_display, [mode]: settings } } };
    saveConfig(updated as WizardStudyConfig, storage);
  }
  return true;
}
