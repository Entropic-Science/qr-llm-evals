import { cards as registryCards, type EvalCard, type ModelRef } from "@qr-evals/contracts";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DeterminismBriefing } from "../../components/DeterminismBriefing";
import { api } from "../../lib/api";
import {
  CONFIG_STORAGE_KEY, consumeShareLink, dropShareParameter, loadConfig, parseStudyConfig, readDraft, rejectedNote, saveConfig,
  studyOneTemplate, unreadableDraft as readUnreadableDraft, type LoadedConfig, type WizardStudyConfig,
} from "../../lib/config-store";
import { draftHypotheses } from "../../lib/design";
import type { PanelProps, SectionId } from "../../lib/panels";
import { problems as findProblems, type Problem, type StepId } from "../../lib/problems";
import { rate, type Rec } from "../../lib/recommendations";
import { DEFAULT_SEEDS, rosterFromArms, type Roster } from "../../lib/roster";
import {
  FILE_MARK_STORAGE_KEY, canPickSaveFile, checkStudyFile, fileMarkOf, fileStateOf, loadStudyFile, readFileMark, saveStudyFile, studySnapshot,
  writeFileMark, type FileCheck, type FileMark, type FileState, type LoadOutcome, type SaveOutcome, type StudyFileHandle,
} from "../../lib/study-file";
import type { StepDef } from "./steps";

// The study being set up, held once for every view of it.
//
// The setup steps, the estimate sidebar, the study bar, the settings dialog and the map all read this context;
// none of them keeps a copy of the study. The problem list and the recommendation states are computed here,
// once per change, so a setting cannot be an error in one place and fine in another. The study's file (the
// last one loaded or saved, and whether the study has changed since) is kept here for the same reason.

/** A place in the setup: a step, and optionally the setting to put the focus on (a study key path or a `Rec` path). */
export interface StepTarget {
  stepId: StepId;
  path?: string;
}

/** A `goTo` that the page has not acted on yet. `nonce` tells two requests for the same place apart. */
export interface PendingFocus extends StepTarget {
  nonce: number;
}

/** The two views of the study: the steps, and the map. */
export type SetupView = "steps" | "map";

/** Where to open the settings dialog: a section, a setting (a study key path or a `Rec` path), or only the problems. */
export interface SettingsTarget {
  section?: SectionId;
  path?: string;
  problemsOnly?: boolean;
}

/** An `openSettings` the dialog is showing. `nonce` tells two requests for the same place apart. */
export interface SettingsRequest extends SettingsTarget {
  nonce: number;
}

/** What the page has to say about the last thing done with a study file. */
export type FileReport =
  | { kind: "reading"; name: string }
  | {
    kind: "loaded";
    name: string;
    /** The study as it was when it was loaded. Later edits do not change what the file held. */
    arms: number;
    evals: number;
    /** The hypotheses of its design block, or null when the file has none. */
    hypotheses: number | null;
    unknownCards: readonly string[];
    /** Whether the server read and validated the file. */
    checked: boolean;
    /** What the server would refuse the study for at launch, in its own words. Empty for a file it has nothing against. */
    serverProblems: readonly string[];
  }
  | { kind: "failed"; message: string; setting: string | null }
  | { kind: "note"; message: string; failed: boolean };

/** A study waiting to take the place of the one on screen, which has work that is in no file. */
export type PendingReplace = ({ source: "file"; name: string } | { source: "template" } | { source: "share" }) & {
  /**
   * Why "Save current study first" replaced nothing, in whole sentences: the file it wrote, or would have
   * written, will not load back. The question stays open with this said in it.
   */
  notSaved?: string;
  /** With `notSaved`: the name of the file of the last save, which was left as it was because the check came first. */
  keptFile?: string;
};

/** A save held back: it would put a file that will not load back in the place of one that, as far as the page knows, does. */
export interface PendingOverwrite {
  /** The file of the last save, which the save would write to without asking where. */
  name: string;
  /** Why the file would not load back, in whole sentences (`FileCheck.reason`). */
  reason: string;
}

export interface StudyValue {
  config: WizardStudyConfig;
  /** An edit to the study. It is saved as the draft at once. */
  updateConfig: (config: WizardStudyConfig) => void;
  /** Another study in place of this one (a loaded file, the template). The setup returns to its first step. */
  replaceStudy: (config: WizardStudyConfig) => void;
  cards: readonly EvalCard[];
  modelOptions: readonly ModelRef[];
  /** `problems(config, cards, { saved, serverError })`. */
  problems: readonly Problem[];
  /** `rate(config, cards, problems)`. */
  recs: readonly Rec[];
  /** What every settings panel takes: `<panel.Component {...panelProps} />`. */
  panelProps: PanelProps;
  /** Whether the draft was written to this browser's storage. */
  saved: boolean;
  /** The server's last refusal of this study, raised as the `review.server_rejected` problem. */
  serverError: string | null;
  setServerError: (message: string | null) => void;
  /** What to say when a saved source could not be read on the way in, or `null`. */
  loadNote: string | null;
  /** The text of a stored draft the page could not read and set aside, for the researcher to keep; or `null`. */
  unreadableDraft: string | null;

  /** The view on screen. It is mirrored as `?view=map` without navigating, so nothing is remounted. */
  view: SetupView;
  setView: (view: SetupView) => void;

  /** The steps offered for this study, in order. */
  steps: readonly StepDef[];
  /** The step on screen. */
  step: StepDef;
  stepIndex: number;
  /** Shows a step and leaves the focus where it is (the rail). */
  setStep: (stepId: StepId) => void;
  /** Shows a step, switching to the steps view if the map is on screen, and puts the focus on a setting of it or on its heading. */
  goTo: (target: StepTarget) => void;
  /** The step a problem is listed under: its own, or the last one when its own is not offered. */
  stepFor: (problem: Problem) => StepDef;
  /** The study's problems by the step they are listed under. */
  problemsByStep: Readonly<Record<StepId, readonly Problem[]>>;
  /** The `goTo` waiting for the page to draw its step; whoever draws it focuses the field and clears this. */
  pendingFocus: PendingFocus | null;
  clearPendingFocus: () => void;

  /** The last file this study was loaded from or saved to, or null. */
  fileMark: FileMark | null;
  /** `none` with no file yet, `saved` while the study equals that file, `changed` after an edit. */
  fileState: FileState;
  /**
   * Saves the study to a file. It never refuses a draft with problems. Where the browser lets the page write a
   * file, the first save asks where and later ones write the same file; `saveAs` asks again. Elsewhere the
   * file is a download. Resolves to `false` when nothing was written: the dialog was closed, or the file could
   * not be written.
   *
   * Every save is checked: the server is asked to read the text that was written, as a load would, and the
   * page's own check answers when the server cannot be asked. The file report says which of the two answered
   * and what it said. A file that will not load back is marked as such (`fileMark.loadsBack === false`).
   *
   * A save that opens the browser's dialog is checked after the write, because the dialog must open in the
   * click. A save to the file of an earlier save opens no dialog, so it is checked before the write: when the
   * text would not load back and that file is not already known to be one that does not, the write waits for
   * `answerOverwrite`.
   */
  saveFile: (options?: { saveAs?: boolean }) => Promise<boolean>;
  /** Whether the next Save writes to the file of an earlier save without asking where. */
  savesWithoutAsking: boolean;
  /** A save waiting for the researcher to say whether a file that loads may be overwritten by one that will not, or null. */
  pendingOverwrite: PendingOverwrite | null;
  answerOverwrite: (answer: "overwrite" | "cancel") => void;
  /** Whether this browser can ask where to save, so "Save as" means something. */
  canSaveAs: boolean;
  /** Reads a study file and, when it is one, puts it in place of the study. The study is untouched by a file that fails. */
  loadFile: (files: Iterable<File>) => Promise<void>;
  /** The Study 1 template in place of the study, after asking when the study has work that is in no file. */
  startFromTemplate: () => void;
  /** The Study 1 template in place of the study, without asking. */
  loadTemplate: () => void;
  /** A study held back until the researcher says what to do with unsaved work, or null. */
  pendingReplace: PendingReplace | null;
  answerReplace: (answer: "save" | "replace" | "cancel") => void;
  /** What to say about the last file action, under the study bar, or null. */
  fileReport: FileReport | null;
  dismissFileReport: () => void;
  /** Says something in the place of the file report: the outcome of an action of the study bar. */
  tell: (message: string, failed?: boolean) => void;

  /** The request the settings dialog is open for, or null while it is closed. */
  settings: SettingsRequest | null;
  /** Opens the settings dialog, optionally on a section, on a setting, or showing only the problems. */
  openSettings: (target?: SettingsTarget) => void;
  closeSettings: () => void;
}

const StudyContext = createContext<StudyValue | null>(null);

export function useStudy(): StudyValue {
  const value = useContext(StudyContext);
  if (value === null) throw new Error("useStudy() is called outside <StudyProvider>.");
  return value;
}

const ACK_KEY = "qr-evals.determinism-briefing.acknowledged";

function modelRef(model: { id: string; family: string; provider: string; served_name: string; checkpoint?: string | null }): ModelRef | null {
  if ((model.family !== "qr_llm" && model.family !== "external") || !["qr_server", "openai", "anthropic_compat", "custom"].includes(model.provider)) return null;
  return { id: model.id, family: model.family, provider: model.provider as ModelRef["provider"], served_name: model.served_name, checkpoint: model.checkpoint ?? null };
}

/** Whether an edit asked an arm that was not replayable to be so: the moment the briefing is for. */
function asksForDeterminism(before: WizardStudyConfig, after: WizardStudyConfig): boolean {
  return after.arms.some((arm) => arm.deterministic === true && before.arms.some((earlier) => earlier.id === arm.id && earlier.deterministic !== true));
}

function briefingAcknowledged(): boolean {
  try { return window.sessionStorage.getItem(ACK_KEY) === "yes"; } catch { return false; }
}

function viewInAddress(): SetupView {
  return new URLSearchParams(window.location.search).get("view") === "map" ? "map" : "steps";
}

/** Mirrors the view in the address bar. It replaces the entry, so the page is not navigated and nothing remounts. */
function writeViewToAddress(view: SetupView): void {
  const url = new URL(window.location.href);
  if (view === "map") url.searchParams.set("view", "map");
  else url.searchParams.delete("view");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

let templateSnapshot: string | null = null;
/** Whether the study is the Study 1 template exactly as it ships: nothing would be lost by replacing it. */
function isUntouchedTemplate(config: WizardStudyConfig): boolean {
  templateSnapshot ??= studySnapshot(studyOneTemplate());
  return studySnapshot(config) === templateSnapshot;
}

/** Whether replacing a study would lose work that is in no file. */
function unfiled(config: WizardStudyConfig, mark: FileMark | null): boolean {
  const state = fileStateOf(config, mark);
  // A file that will not load back holds the text of the study and is no way back to it.
  if (state === "saved") return mark?.loadsBack === false;
  return state === "changed" || !isUntouchedTemplate(config);
}

/** What a save came to, for the file report: where the file went, and whether it will load back and on whose word. */
function savedText(outcome: Exclude<SaveOutcome, { status: "cancelled" }>, check: FileCheck): string {
  const where = outcome.status === "saved" ? `Saved to ${outcome.name}.` : `Handed to the browser as a download named ${outcome.name}.`;
  if (!check.loads) return `${where} This file will not load back as it is. ${check.reason}`;
  return check.by === "server"
    ? `${where} The server read the same text back and accepts it, so the file will load.`
    : `${where} The server could not be asked to read it back. This page's own check found nothing in it that stops a study file from loading.`;
}

/** The roles of a study's arms, when it has comparisons: what the next first comparison is given once the last one is unticked. */
function rolesOf(config: WizardStudyConfig, before: Roster | null): Roster | null {
  if (config.arms.length === 0) return before;
  const roster = rosterFromArms(config.arms, config.seeds ?? DEFAULT_SEEDS);
  return roster !== null && roster.cells.length > 0 ? roster : before;
}

interface Opening {
  /** The study the setup opens on. */
  loaded: LoadedConfig;
  /** The mark of the file that study came from or went to, or null. */
  mark: FileMark | null;
  /** The study of a share link that is held back until the researcher says what to do with unsaved work. */
  shared: WizardStudyConfig | null;
}

/**
 * The study to open the setup on. A share link takes the place of the stored draft at once only when nothing
 * is lost by that; over a draft with work that is in no file it waits for an answer, like a loaded file.
 */
function openStudy(): Opening {
  const loaded = loadConfig();
  if (loaded.source === "storage") return { loaded, mark: readFileMark(), shared: null };
  if (loaded.source === "share") {
    const stored = readDraft();
    const mark = readFileMark();
    if (stored !== null) {
      const onScreen: LoadedConfig = { config: stored, source: "storage", rejected: null };
      // The link holds the draft itself: there is nothing to replace, and the file it is saved to stays its file.
      if (studySnapshot(stored) === studySnapshot(loaded.config)) { dropShareParameter(); return { loaded: onScreen, mark, shared: null }; }
      if (unfiled(stored, mark)) return { loaded: onScreen, mark, shared: loaded.config };
    }
    consumeShareLink(loaded.config);
  }
  // A study that arrived another way (a share link, the template in place of a draft that could not be read)
  // has no file yet.
  writeFileMark(null);
  return { loaded, mark: null, shared: null };
}

export function StudyProvider({ steps, children }: { steps: readonly StepDef[]; children: ReactNode }) {
  const [opening] = useState(openStudy);
  const { loaded } = opening;
  const [draft, setDraft] = useState(() => ({ config: loaded.config, saved: saveConfig(loaded.config), lastRoster: rolesOf(loaded.config, null) }));
  const [cards, setCards] = useState<readonly EvalCard[]>(registryCards);
  const [modelOptions, setModelOptions] = useState<readonly ModelRef[]>(loaded.config.models);
  const [serverError, setServerError] = useState<string | null>(null);
  const [stepId, setStepId] = useState<StepId | null>(null);
  const [pendingFocus, setPendingFocus] = useState<PendingFocus | null>(null);
  const [briefingOpen, setBriefingOpen] = useState(false);
  const [view, setViewState] = useState<SetupView>(viewInAddress);
  // The mark names the file the stored draft came from or went to.
  const [fileMark, setFileMark] = useState<FileMark | null>(opening.mark);
  const [fileReport, setFileReport] = useState<FileReport | null>(null);
  const [pendingReplace, setPendingReplace] = useState<(PendingReplace & { outcome?: Extract<LoadOutcome, { ok: true }>; shared?: WizardStudyConfig }) | null>(
    () => (opening.shared === null ? null : { source: "share", shared: opening.shared }),
  );
  const [unreadableDraft] = useState(() => (loaded.rejected === "storage" ? readUnreadableDraft() : null));
  const [settings, setSettings] = useState<SettingsRequest | null>(null);
  const latest = useRef(draft.config);
  const latestMark = useRef(fileMark);
  const latestCards = useRef(cards);
  const latestView = useRef(view);
  const nonce = useRef(0);
  const loadTurn = useRef(0);
  // The file of the last save, where the browser lets the page write one. It belongs to the study on screen:
  // another study in its place must never be written into it without asking.
  const fileHandle = useRef<StudyFileHandle | null>(null);
  const [savesWithoutAsking, setSavesWithoutAsking] = useState(false);
  const hold = useCallback((handle: StudyFileHandle | null) => { fileHandle.current = handle; setSavesWithoutAsking(handle !== null); }, []);
  const [pendingOverwrite, setPendingOverwrite] = useState<PendingOverwrite | null>(null);
  const overwriteAnswer = useRef<((go: boolean) => void) | null>(null);
  const saving = useRef(false);
  const saveTurn = useRef(0);
  const { config, saved, lastRoster } = draft;

  useEffect(() => { latestCards.current = cards; }, [cards]);

  // Every change of the study goes through here, so the draft in storage is the study on screen.
  const store = useCallback((next: WizardStudyConfig) => {
    if (next === latest.current) return;
    latest.current = next;
    const stored = saveConfig(next);
    setDraft((before) => ({ config: next, saved: stored, lastRoster: rolesOf(next, before.lastRoster) }));
  }, []);
  const updateConfig = useCallback((next: WizardStudyConfig) => {
    if (asksForDeterminism(latest.current, next) && !briefingAcknowledged()) setBriefingOpen(true);
    store(next);
  }, [store]);
  const replaceStudy = useCallback((next: WizardStudyConfig) => {
    if (next !== latest.current) {
      latest.current = next;
      const stored = saveConfig(next);
      // Another study starts with its own roles, not with those of the one it replaces.
      setDraft({ config: next, saved: stored, lastRoster: rolesOf(next, null) });
    }
    hold(null);
    setServerError(null);
    setStepId(null);
    setPendingFocus(null);
  }, [store, hold]);
  const closeBriefing = useCallback(() => {
    try { window.sessionStorage.setItem(ACK_KEY, "yes"); } catch { /* The briefing is shown again next time. */ }
    setBriefingOpen(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([api.cards(), api.models()]).then(([cardResult, modelResult]) => {
      if (controller.signal.aborted) return;
      if (cardResult.status === "fulfilled" && cardResult.value.cards.length > 0) setCards(cardResult.value.cards.map(({ card }) => card));
      if (modelResult.status === "fulfilled") {
        const models = modelResult.value.models.map(modelRef).filter((model): model is ModelRef => model !== null);
        if (models.length > 0) setModelOptions(models);
      }
    });
    return () => controller.abort();
  }, []);

  const problems = useMemo(() => findProblems(config, cards, { saved, serverError, modelOptions }), [config, cards, saved, serverError, modelOptions]);
  const recs = useMemo(() => rate(config, cards, problems), [config, cards, problems]);
  const panelProps = useMemo<PanelProps>(() => ({ config, updateConfig, cards, modelOptions, recs, lastRoster }), [config, updateConfig, cards, modelOptions, recs, lastRoster]);

  const shown = useMemo(() => {
    const offered = steps.filter((candidate) => candidate.shown(config, cards));
    return offered.length > 0 ? offered : steps.slice(0, 1);
  }, [steps, config, cards]);
  // A step can stop being offered while it is on screen; the one before it takes its place.
  const step = useMemo(() => {
    const at = steps.findIndex((candidate) => candidate.id === stepId);
    for (let index = at; index >= 0; index -= 1) {
      const candidate = steps[index];
      if (candidate && shown.includes(candidate)) return candidate;
    }
    return shown[0]!;
  }, [steps, shown, stepId]);

  // A problem of a step that is not offered is listed under the last step, where the launch is.
  const stepFor = useCallback((problem: Problem) => shown.find((candidate) => candidate.problems([problem]).length > 0) ?? shown[shown.length - 1]!, [shown]);
  const problemsByStep = useMemo(() => {
    const by: Record<StepId, Problem[]> = { compare: [], measure: [], design: [], instruments: [], review: [] };
    for (const problem of problems) by[stepFor(problem).id].push(problem);
    return by;
  }, [problems, stepFor]);

  const setView = useCallback((next: SetupView) => {
    if (next === latestView.current) return;
    latestView.current = next;
    setViewState(next);
    writeViewToAddress(next);
  }, []);

  const setStep = useCallback((id: StepId) => { setStepId(id); setPendingFocus(null); }, []);
  const goTo = useCallback((target: StepTarget) => {
    const home = shown.find((candidate) => candidate.id === target.stepId) ?? shown[shown.length - 1]!;
    nonce.current += 1;
    // A step is drawn only in the steps view, so going to one leaves the map.
    setView("steps");
    setStepId(home.id);
    setPendingFocus({ ...target, stepId: home.id, nonce: nonce.current });
  }, [shown, setView]);
  const clearPendingFocus = useCallback(() => setPendingFocus(null), []);

  const mark = useCallback((next: FileMark | null) => { latestMark.current = next; setFileMark(next); }, []);
  const fileState = useMemo(() => fileStateOf(config, fileMark), [config, fileMark]);

  const tell = useCallback((message: string, failed = false) => setFileReport({ kind: "note", message, failed }), []);
  const dismissFileReport = useCallback(() => setFileReport(null), []);

  // Another tab of the setup writes the same draft. Its change is taken over here, so that the two tabs do not
  // overwrite each other's work with a study that is a few edits behind, and the question before a replace
  // is asked about the study as it now stands. The tab that wrote hears nothing of its own change.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === FILE_MARK_STORAGE_KEY) {
        latestMark.current = readFileMark();
        setFileMark(latestMark.current);
        // The other tab saved or loaded a file. This tab's own file is no longer known to be the study's.
        hold(null);
        return;
      }
      if (event.key !== CONFIG_STORAGE_KEY || event.newValue === null) return;
      let next: WizardStudyConfig | null = null;
      try { next = parseStudyConfig(JSON.parse(event.newValue) as unknown); } catch { /* Not a study: this tab keeps its own. */ }
      if (next === null || studySnapshot(next) === studySnapshot(latest.current)) return;
      latest.current = next;
      const taken = next;
      // Nothing is written back: the draft in storage already is this study.
      setDraft((before) => ({ config: taken, saved: true, lastRoster: rolesOf(taken, before.lastRoster) }));
      setServerError(null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  /** A save and its check: whether a file was written, and whether that file will load back. */
  const save = useCallback(async ({ saveAs = false, keepFileThatLoads = false }: { saveAs?: boolean; keepFileThatLoads?: boolean } = {}): Promise<{ written: boolean; check: FileCheck | null; keptFile?: string }> => {
    // One save at a time: a second dialog cannot be opened over the first.
    if (saving.current) return { written: false, check: null };
    saving.current = true;
    const study = latest.current;
    const handle = saveAs ? null : fileHandle.current;
    let checking: Promise<FileCheck>;
    let outcome: Exclude<SaveOutcome, { status: "cancelled" }>;
    try {
      if (handle === null) {
        // The browser's dialog must open in the click, so the check cannot come first. It is of the text this
        // save writes and is asked for now, so that the answer is there when the write is; it never rejects.
        checking = checkStudyFile(study);
      } else {
        // No dialog is needed to write to the file of an earlier save, so the check comes before the write:
        // a file that loads is not replaced by one that will not unless the researcher says so.
        const before = await checkStudyFile(study);
        checking = Promise.resolve(before);
        // The study was replaced, or another tab took the file over, while the check was out.
        if (fileHandle.current !== handle) {
          tell("The study's file changed while the save was being checked, so nothing was written. Save again.", true);
          return { written: false, check: null };
        }
        if (!before.loads && latestMark.current?.loadsBack !== false) {
          if (keepFileThatLoads) return { written: false, check: before, keptFile: handle.name };
          const go = await new Promise<boolean>((resolve) => {
            overwriteAnswer.current = resolve;
            setPendingOverwrite({ name: handle.name, reason: before.reason });
          });
          if (!go || fileHandle.current !== handle) {
            tell(`Nothing was written, so ${handle.name} is as it was. A file of the study as it stands will not load back. ${before.reason}`, true);
            return { written: false, check: before, keptFile: handle.name };
          }
        }
      }
      const result = await saveStudyFile(study, { handle });
      if (result.status === "cancelled") return { written: false, check: null };
      outcome = result;
      hold(result.status === "saved" ? result.handle : null);
      mark(result.mark);
    } catch (cause) {
      // A browser's own failure (a DOMException) carries a sentence of its own; anything else is only named.
      const said = (cause as { message?: unknown } | null)?.message;
      tell(`The study could not be written to a file: ${typeof said === "string" && said !== "" ? said : "the browser refused."} Nothing was saved.`, true);
      return { written: false, check: null };
    } finally {
      // The file is written or it is not: the next save need not wait for the check of this one.
      saving.current = false;
    }
    saveTurn.current += 1;
    const turn = saveTurn.current;
    const check = await checking;
    // A later save, a load or another tab may have moved the mark on meanwhile. The answer is then about a
    // file that is no longer the study's, and is said to nobody.
    if (turn === saveTurn.current && latestMark.current === outcome.mark) {
      if (!check.loads) {
        const flagged: FileMark = { ...outcome.mark, loadsBack: false };
        writeFileMark(flagged);
        mark(flagged);
      }
      setFileReport({ kind: "note", message: savedText(outcome, check), failed: !check.loads });
    }
    return { written: true, check };
  }, [mark, tell, hold]);
  const answerOverwrite = useCallback((answer: "overwrite" | "cancel") => {
    const resolve = overwriteAnswer.current;
    overwriteAnswer.current = null;
    setPendingOverwrite(null);
    resolve?.(answer === "overwrite");
  }, []);
  const saveFile = useCallback(async (options?: { saveAs?: boolean }) => (await save(options)).written, [save]);
  const canSaveAs = useMemo(canPickSaveFile, []);

  /** Whether replacing the study would lose work that is in no file. */
  const hasUnfiledWork = useCallback(() => unfiled(latest.current, latestMark.current), []);

  const applyLoaded = useCallback((outcome: Extract<LoadOutcome, { ok: true }>) => {
    replaceStudy(outcome.config);
    // The mark is of the study as loaded, so the state reads "Saved to" until the first edit.
    const next = fileMarkOf(outcome.name, outcome.config, "loaded");
    writeFileMark(next);
    mark(next);
    const designed = outcome.config.design !== null && outcome.config.design !== undefined;
    setFileReport({
      kind: "loaded",
      name: outcome.name,
      arms: outcome.config.arms.length,
      evals: outcome.config.cards.length,
      hypotheses: designed ? draftHypotheses(outcome.config, latestCards.current).length : null,
      unknownCards: outcome.unknownCards,
      checked: outcome.checked,
      serverProblems: outcome.problems,
    });
  }, [replaceStudy, mark]);

  const loadTemplate = useCallback(() => {
    replaceStudy(studyOneTemplate());
    writeFileMark(null);
    mark(null);
    setFileReport({ kind: "note", message: "The Study 1 template is loaded. It is not saved to a file yet.", failed: false });
  }, [replaceStudy, mark]);

  /** The study of a share link in place of the study. The link is used up: it leaves the address bar. */
  const loadShared = useCallback((shared: WizardStudyConfig) => {
    replaceStudy(shared);
    writeFileMark(null);
    mark(null);
    dropShareParameter();
    setFileReport({ kind: "note", message: "The study from the share link is loaded. It is not saved to a file yet.", failed: false });
  }, [replaceStudy, mark]);

  const loadFile = useCallback(async (files: Iterable<File>) => {
    const list = [...files];
    loadTurn.current += 1;
    const turn = loadTurn.current;
    // A share link that was still waiting for an answer is answered by this: it is not opened.
    dropShareParameter();
    setPendingReplace(null);
    setFileReport(list.length === 1 && list[0] ? { kind: "reading", name: list[0].name } : null);
    const outcome = await loadStudyFile(list);
    // A later file was chosen while this one was read: that one decides.
    if (turn !== loadTurn.current) return;
    if (!outcome.ok) { setFileReport({ kind: "failed", message: outcome.message, setting: outcome.setting }); return; }
    if (hasUnfiledWork()) { setFileReport(null); setPendingReplace({ source: "file", name: outcome.name, outcome }); return; }
    applyLoaded(outcome);
  }, [applyLoaded, hasUnfiledWork]);

  const startFromTemplate = useCallback(() => {
    loadTurn.current += 1;
    dropShareParameter();
    if (hasUnfiledWork()) { setPendingReplace({ source: "template" }); return; }
    loadTemplate();
  }, [hasUnfiledWork, loadTemplate]);

  const answerReplace = useCallback((answer: "save" | "replace" | "cancel") => {
    const pending = pendingReplace;
    if (pending === null) return;
    const settle = () => {
      setPendingReplace(null);
      if (pending.source === "share") {
        if (answer === "cancel" || !pending.shared) {
          dropShareParameter();
          setFileReport({ kind: "note", message: "The share link was not opened. The study you were editing is unchanged.", failed: false });
        } else loadShared(pending.shared);
        return;
      }
      if (answer === "cancel") return;
      if (pending.source === "file" && pending.outcome) applyLoaded(pending.outcome);
      else loadTemplate();
    };
    if (answer !== "save") { settle(); return; }
    // "Save current study first" keeps the question open until the study is in a file that loads back: a
    // dialog that was closed, a file that could not be written and a file that no load would accept all
    // replace nothing, so nothing is lost by answering.
    // Where the file of an earlier save would be written without asking, a text that will not load back is
    // not written over it at all: the question says so and stays open.
    void save({ keepFileThatLoads: true }).then(({ written, check, keptFile }) => {
      if (check !== null && !check.loads) {
        const notSaved = check.reason;
        setPendingReplace((now) => (now === pending ? { ...pending, notSaved, ...(written || keptFile === undefined ? {} : { keptFile }) } : now));
        return;
      }
      if (!written) return;
      settle();
    });
  }, [pendingReplace, save, applyLoaded, loadTemplate, loadShared]);

  const openSettings = useCallback((target: SettingsTarget = {}) => {
    nonce.current += 1;
    setSettings({ ...target, nonce: nonce.current });
  }, []);
  const closeSettings = useCallback(() => setSettings(null), []);

  const loadNote = useMemo(() => rejectedNote(loaded), [loaded]);
  const value = useMemo<StudyValue>(() => ({
    config, updateConfig, replaceStudy, cards, modelOptions, problems, recs, panelProps, saved, serverError, setServerError, loadNote, unreadableDraft,
    view, setView,
    steps: shown, step, stepIndex: shown.indexOf(step), setStep, goTo, stepFor, problemsByStep, pendingFocus, clearPendingFocus,
    fileMark, fileState, saveFile, canSaveAs, loadFile, startFromTemplate, loadTemplate, pendingReplace, answerReplace,
    savesWithoutAsking, pendingOverwrite, answerOverwrite,
    fileReport, dismissFileReport, tell,
    settings, openSettings, closeSettings,
  }), [
    config, updateConfig, replaceStudy, cards, modelOptions, problems, recs, panelProps, saved, serverError, loadNote, unreadableDraft,
    view, setView,
    shown, step, setStep, goTo, stepFor, problemsByStep, pendingFocus, clearPendingFocus,
    fileMark, fileState, saveFile, canSaveAs, loadFile, startFromTemplate, loadTemplate, pendingReplace, answerReplace,
    savesWithoutAsking, pendingOverwrite, answerOverwrite,
    fileReport, dismissFileReport, tell,
    settings, openSettings, closeSettings,
  ]);

  return <StudyContext.Provider value={value}>
    {children}
    <DeterminismBriefing open={briefingOpen} onClose={closeBriefing} />
  </StudyContext.Provider>;
}
