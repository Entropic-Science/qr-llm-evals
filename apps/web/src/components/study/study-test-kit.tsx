import { vi } from "vitest";
import type { ReactNode } from "react";
import { CONFIG_STORAGE_KEY, studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import type { StudyFileHandle } from "../../lib/study-file";
import { STEPS } from "../../routes/configure/steps";
import { StudyProvider } from "../../routes/configure/StudyContext";
import { AllSettings } from "./AllSettings";
import { LoadReport } from "./LoadReport";
import { ReplaceConfirm } from "./ReplaceConfirm";
import { StudyBar } from "./StudyBar";

// What the tests of the study bar, the settings dialog and the study file share. Not part of the app.

export function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

/** Fresh storage and a bare address, as every test of the setup starts. */
export function resetBrowser(): void {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() });
  window.history.pushState({}, "", "/");
  // jsdom has no "save as" dialog, like Firefox and Safari: a save is a download unless a test installs one.
  Reflect.deleteProperty(window, "showSaveFilePicker");
}

/**
 * Stands in for the browser's "save as" dialog and the files behind it, as Chromium has them. `answer` is what
 * the researcher does with the dialog; `asked` is every time it opened; `files` is what was written and closed.
 */
export function fakeSavePicker(answer: "pick" | "cancel" = "pick", pickedName = "picked.study.yaml") {
  const asked: { suggestedName?: string }[] = [];
  const files = new Map<string, { written: string[]; closed: number; failWrite: boolean }>();
  const state = { answer, pickedName };
  const handleFor = (name: string): StudyFileHandle => {
    const file = files.get(name) ?? { written: [], closed: 0, failWrite: false };
    files.set(name, file);
    return {
      name,
      createWritable: () => Promise.resolve({
        write: (data: string) => { if (file.failWrite) return Promise.reject(new DOMException("The disk is full.", "QuotaExceededError")); file.written.push(data); return Promise.resolve(); },
        close: () => { file.closed += 1; return Promise.resolve(); },
      }),
    };
  };
  Object.defineProperty(window, "showSaveFilePicker", {
    configurable: true,
    writable: true,
    value: (options: { suggestedName?: string } = {}) => {
      asked.push(options);
      if (state.answer === "cancel") return Promise.reject(new DOMException("The user aborted a request.", "AbortError"));
      return Promise.resolve(handleFor(state.pickedName));
    },
  });
  return { asked, files, state, handleFor };
}

/**
 * A small study: the token-level evals, one eval with prompts of its own that no judge scores, and no
 * supervised sessions. It draws fast, which a dialog of every panel needs in jsdom. The eval with prompts is
 * there because the token-level outcomes are tested on the replies the study generates: without one the study
 * would plan no data for them, which is a problem of its own.
 */
export function smallStudy(): WizardStudyConfig {
  const template = studyOneTemplate();
  const tokenLevel = (cardId: string) => cardId.startsWith("tier0_") || cardId === "introspection_scales";
  return {
    ...template,
    title: "A small study",
    // Pinned, so that the study has no problem of its own: what the page says of a missing pin is tested where it is raised.
    models: template.models.map((model) => ({ ...model, checkpoint: "org/qr-llm@0123abc" })),
    contact: "lab@example.org",
    expectations: "No difference between the arms.",
    supervised: null,
    arms: template.arms.filter((arm) => arm.observer_mode !== "supervised"),
    cards: template.cards.filter((selection) => tokenLevel(selection.card_id)),
    design: template.design ? { ...template.design, confirmatory: template.design.confirmatory.filter((ref) => tokenLevel(ref.card_id)) } : null,
  };
}

/**
 * The Study 1 template as a researcher would launch it: its model checkpoint and the assets of its supervised
 * sessions pinned. The template itself ships without the pins, and the page warns about each; a test that
 * counts the problems of a study starts from this one so that it counts only its own.
 */
export function pinnedTemplate(): WizardStudyConfig {
  const template = studyOneTemplate();
  const hash = "a".repeat(64);
  return {
    ...template,
    models: template.models.map((model) => ({ ...model, checkpoint: "org/qr-llm@0123abc" })),
    supervised: template.supervised ? { ...template.supervised, questionnaires_hash: hash, target_pool_hash: hash } : null,
  };
}

/** Puts a study in storage as the draft the setup opens with. */
export function storeDraft(config: WizardStudyConfig): void {
  window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config));
}

export function storedDraft(): Partial<WizardStudyConfig> {
  return JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) ?? "{}") as Partial<WizardStudyConfig>;
}

/**
 * Stands in for the browser's download: jsdom has no object URLs and would try to navigate on the click.
 * Returns what was handed over, in order: the Blobs, and the file names they were to be saved under.
 */
export function captureDownloads(): { blobs: Blob[]; names: string[]; revoked: string[] } {
  const blobs: Blob[] = [];
  const names: string[] = [];
  const revoked: string[] = [];
  Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: (blob: Blob) => { blobs.push(blob); return `blob:study-${blobs.length}`; } });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: (url: string) => { revoked.push(url); } });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) { names.push(this.download); });
  return { blobs, names, revoked };
}

/** A Blob's text, read the way the page reads a file. */
export function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("The blob could not be read."));
    reader.readAsText(blob);
  });
}

/** The study bar with everything it opens, over the shared study, without the steps. */
export function BarHarness({ children }: { children?: ReactNode }) {
  return <StudyProvider steps={STEPS}>
    <div className="setup-shell">
      <StudyBar />
      <LoadReport />
      {children}
      <AllSettings />
      <ReplaceConfirm />
    </div>
  </StudyProvider>;
}
