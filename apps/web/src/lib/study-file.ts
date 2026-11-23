import { api, ApiError } from "./api";
import { StudyConfigSchema } from "@qr-evals/contracts";
import { parseStudyConfig, type WizardStudyConfig } from "./config-store";
import { loadRefusals } from "./problems";

// The study file: the durable copy of a study, written by the browser and read back by the server's
// `/api/config/parse`. It is YAML restricted to the forms no parser disagrees about: block maps and lists, and
// every string as a JSON double-quoted scalar. Same config, same bytes.
//
// The first half of this module is the writer and is pure. The second half is what the page does with a file:
// saving one (into a file the researcher picks where the browser can do that, through a download elsewhere),
// asking whether the text of a save will load back, reading a file back through the server, and remembering
// which file the study on screen was last loaded from or saved to.

/**
 * How the page came to know of a file: it wrote the file itself and the browser confirmed the write
 * (`written`), it handed the file to the browser as a download and cannot tell what became of it
 * (`downloaded`), or the researcher picked the file to load (`loaded`).
 */
export type FileOrigin = "written" | "downloaded" | "loaded";

/** What the browser remembers about the last file loaded or saved: its name, the study as it stood then, and how the page knows of it. */
export interface FileMark {
  name: string;
  snapshot: string;
  origin: FileOrigin;
  /**
   * Set, and only ever to `false`, on a file that was written and then found not to load back as it is
   * (`checkStudyFile`). Such a file is a copy of the text and not a way back to the study, so the page does
   * not count the study as saved on the strength of it.
   */
  loadsBack?: false;
}
export type FileState = "none" | "saved" | "changed";

const HEADER = [
  "# Study file for evals.entropic.science",
  '# Load it with "Load study file" on the setup page. Edits made by hand are kept, comments are not.',
];

const TOP_LEVEL_ORDER = [
  "schema_version", "template", "title", "authors", "contact", "created_at", "models", "arms", "cards", "design", "seeds",
  "judge", "narrative_model", "supervised", "estimates", "pilot", "expectations", "data_licence",
];

const BARE_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;
// Plain words a YAML 1.1 reader (PyYAML, which the server uses) turns into a boolean or null. As a bare map key
// `on: 1` would load as {True: 1}, so these are quoted like any other awkward key.
const YAML_WORD = /^(?:y|n|yes|no|on|off|true|false|null)$/i;
// Valid inside a JSON string but not inside a YAML one: DEL and the C1 controls are rejected outright by PyYAML,
// NEL and the Unicode line and paragraph separators are line breaks a reader folds into a space, and a byte order
// mark or a noncharacter is refused. Written as \u escapes they mean the same thing to both.
const YAML_UNSAFE = /[\u007f-\u009f\u2028\u2029\ufeff\ufffe\uffff]/g;

function quote(text: string): string {
  return JSON.stringify(text).replace(YAML_UNSAFE, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function keyText(key: string): string {
  return BARE_KEY.test(key) && !YAML_WORD.test(key) ? key : quote(key);
}

function numberText(value: number, path: string): string {
  if (!Number.isFinite(value)) throw new TypeError(`A study file cannot hold ${String(value)} (${path || "the study"}).`);
  const text = String(value);
  // JavaScript prints 1e21 and 1e-7 without a fraction; YAML 1.1 reads a number in exponent form only with one.
  return text.includes("e") && !text.includes(".") ? text.replace("e", ".0e") : text;
}

function isMap(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype: unknown = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

/** A map's entries as they are written: keys holding null or undefined are left out. */
function written(map: Record<string, unknown>): [string, unknown][] {
  return Object.entries(map).filter(([, value]) => value !== null && value !== undefined);
}

/** The value on one line, or null when it needs a block of its own (a non-empty map or list). */
function inline(value: unknown, path: string): string | null {
  // A list keeps its length, so an empty slot is written out; only map keys can be dropped.
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return quote(value);
  if (typeof value === "number") return numberText(value, path);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.length === 0 ? "[]" : null;
  if (isMap(value)) return written(value).length === 0 ? "{}" : null;
  throw new TypeError(`A study file cannot hold a ${typeof value === "object" ? "non-plain object" : typeof value} (${path || "the study"}).`);
}

function mapLines(entries: readonly [string, unknown][], indent: string, path: string): string[] {
  return entries.flatMap(([key, value]) => {
    const at = path ? `${path}.${key}` : key;
    const line = inline(value, at);
    return line === null ? [`${indent}${keyText(key)}:`, ...blockLines(value, `${indent}  `, at)] : [`${indent}${keyText(key)}: ${line}`];
  });
}

function listLines(items: readonly unknown[], indent: string, path: string): string[] {
  return items.flatMap((item, index) => {
    const at = `${path}[${index}]`;
    const line = inline(item, at);
    if (line !== null) return [`${indent}- ${line}`];
    // The item's block is laid out two columns in, then its first line gives those two columns to the dash:
    // a map's first key, or a nested list's first item, sits on the dash line.
    const [first, ...rest] = blockLines(item, `${indent}  `, at);
    return [`${indent}- ${first!.slice(indent.length + 2)}`, ...rest];
  });
}

function blockLines(value: unknown, indent: string, path: string): string[] {
  return Array.isArray(value) ? listLines(value, indent, path) : mapLines(written(value as Record<string, unknown>), indent, path);
}

const byCodeUnit = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * The study as the text of a study file. Deterministic, and it never validates: a draft with problems is
 * written as it stands. It throws only for what YAML cannot hold (NaN, an infinity, a non-plain object).
 */
export function toStudyYaml(config: unknown): string {
  if (!isMap(config)) throw new TypeError("A study file holds one study: an object of settings was expected.");
  const entries = written(config);
  const rank = (key: string) => { const index = TOP_LEVEL_ORDER.indexOf(key); return index === -1 ? TOP_LEVEL_ORDER.length : index; };
  entries.sort(([a], [b]) => rank(a) - rank(b) || byCodeUnit(a, b));
  const body = entries.length === 0 ? ["{}"] : mapLines(entries, "", "");
  return `${[...HEADER, ...body].join("\n")}\n`;
}

/** "<slug>.study.yaml", with the slug rule of `preregDownloadName`. */
export function studyFileName(title: string): string {
  const stem = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "study";
  return `${stem}.study.yaml`;
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map((item) => canonical(item ?? null)).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${written(value as Record<string, unknown>).sort(([a], [b]) => byCodeUnit(a, b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/**
 * The snapshot a `FileMark` keeps: canonical JSON (keys sorted, no whitespace) of what the file holds. Keys that
 * are null are left out exactly as the file leaves them out, so a study differs from its snapshot only when
 * saving it again would write different settings.
 */
export function studySnapshot(config: unknown): string {
  return canonical(config);
}

/** `none` with no file yet, `saved` while the study still equals the last file loaded or saved, else `changed`. */
export function fileStateOf(config: WizardStudyConfig, mark: FileMark | null): FileState {
  if (!mark) return "none";
  return studySnapshot(config) === mark.snapshot ? "saved" : "changed";
}

// ── Saving, loading, and the mark of the last file ──

/** Where the browser keeps the `FileMark`. */
export const FILE_MARK_STORAGE_KEY = "qr-evals.study-file.v1";
/** The longest text `/api/config/parse` reads. A larger file is refused before it is read. */
export const MAX_STUDY_FILE_BYTES = 200_000;
/** What the file picker offers. */
export const STUDY_FILE_ACCEPT = ".yaml,.yml,.json,application/json,text/yaml";
// A download reads the object URL after the click that starts it, and how long after differs by browser.
// Letting the URL go at once loses the file in some of them; holding it for half a minute costs nothing.
const RELEASE_URL_AFTER_MS = 30_000;

function markStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; /* Storage can be blocked. */ }
}

const ORIGINS: readonly FileOrigin[] = ["written", "downloaded", "loaded"];

/** The mark of a study as it stands: what a load or a save of it under `name` leaves behind. */
export function fileMarkOf(name: string, config: unknown, origin: FileOrigin = "loaded"): FileMark {
  return { name, snapshot: studySnapshot(config), origin };
}

/** The mark kept in this browser, or null when there is none or it cannot be read. */
export function readFileMark(storage: Storage | null = markStorage()): FileMark | null {
  try {
    const value: unknown = JSON.parse(storage?.getItem(FILE_MARK_STORAGE_KEY) ?? "null");
    if (value === null || typeof value !== "object") return null;
    const { name, snapshot, origin, loadsBack } = value as Partial<FileMark>;
    if (typeof name !== "string" || typeof snapshot !== "string") return null;
    // A mark without an origin says less than any of the three: it is read as the one that claims least.
    const mark: FileMark = { name, snapshot, origin: origin !== undefined && ORIGINS.includes(origin) ? origin : "downloaded" };
    if (loadsBack === false) mark.loadsBack = false;
    return mark;
  } catch { return null; }
}

/** Keeps the mark, or with `null` forgets it. `false` when the browser would not store it. */
export function writeFileMark(mark: FileMark | null, storage: Storage | null = markStorage()): boolean {
  if (storage === null) return false;
  try {
    if (mark === null) storage.removeItem(FILE_MARK_STORAGE_KEY);
    else storage.setItem(FILE_MARK_STORAGE_KEY, JSON.stringify({ name: mark.name, snapshot: mark.snapshot, origin: mark.origin, ...(mark.loadsBack === false ? { loadsBack: false } : {}) }));
    return true;
  } catch { return false; }
}

/** The part of the File System Access API this page uses: a file the researcher picked, which the page may write. */
export interface StudyFileHandle {
  readonly name: string;
  createWritable: () => Promise<{ write: (data: string) => Promise<void>; close: () => Promise<void> }>;
}

interface SavePickerOptions {
  suggestedName?: string;
  types?: { description: string; accept: Record<string, string[]> }[];
}
type SavePicker = (options?: SavePickerOptions) => Promise<StudyFileHandle>;

/** The browser's "save as" dialog, where it has one (Chromium today). Elsewhere a save is a download. */
function savePicker(): SavePicker | null {
  const picker = (window as unknown as { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
  return typeof picker === "function" ? picker.bind(window) : null;
}

/** Whether this browser lets the page write a file the researcher picks, and so say for certain that it was saved. */
export function canPickSaveFile(): boolean {
  return savePicker() !== null;
}

/** What became of a save. Only `saved` is a file the page knows to exist. */
export type SaveOutcome =
  /** The file was written and closed. `handle` writes to the same file again without asking. */
  | { status: "saved"; name: string; bytes: number; mark: FileMark; handle: StudyFileHandle }
  /** The file was handed to the browser as a download. Whether, where and under which name it was written is not known. */
  | { status: "downloaded"; name: string; bytes: number; mark: FileMark }
  /** The researcher closed the dialog. Nothing was written and nothing is recorded. */
  | { status: "cancelled" };

export interface SaveOptions {
  /** The file of an earlier save of this study. It is written again without a dialog. */
  handle?: StudyFileHandle | null;
}

const isAbort = (cause: unknown) => (cause as { name?: unknown } | null)?.name === "AbortError";

async function writeTo(handle: StudyFileHandle, text: string): Promise<void> {
  const writable = await handle.createWritable();
  await writable.write(text);
  // The file on disk is replaced only when the stream is closed; until then nothing was saved.
  await writable.close();
}

function download(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), RELEASE_URL_AFTER_MS);
}

/**
 * Saves the study to a file and records the mark of it.
 *
 * Where the browser has a "save as" dialog the page writes the file itself: to `handle` when an earlier save
 * left one, else to the file the researcher picks. The mark is recorded only after the write is closed, under
 * the name that was picked, and a dialog that is closed without a choice changes nothing. A file that cannot
 * be written rejects, and nothing is recorded. Elsewhere the file is handed to the browser as a download,
 * which the page cannot follow: the mark says `downloaded`, never that the file was saved.
 *
 * Nothing is sent anywhere and nothing is refused here: a draft with problems is saved as it stands, signed in
 * or not, online or not. Whether the file will load back is a separate question, which `checkStudyFile`
 * answers for the same text. It rejects with what `toStudyYaml` throws for a study YAML cannot hold.
 */
export async function saveStudyFile(config: WizardStudyConfig, { handle = null }: SaveOptions = {}): Promise<SaveOutcome> {
  const yaml = toStudyYaml(config);
  const name = studyFileName(typeof config.title === "string" ? config.title : "");
  const blob = new Blob([yaml], { type: "application/yaml;charset=utf-8" });
  const written = (target: StudyFileHandle): SaveOutcome => {
    const mark = fileMarkOf(target.name, config, "written");
    writeFileMark(mark);
    return { status: "saved", name: target.name, bytes: blob.size, mark, handle: target };
  };

  const pick = savePicker();
  if (handle !== null) {
    // The file may have been moved, or the permission to write it withdrawn: then the researcher is asked
    // where to save, as on a first save. Without a dialog to ask with, the failure is the answer.
    try { await writeTo(handle, yaml); return written(handle); } catch (cause) { if (pick === null) throw cause; }
  }
  if (pick !== null) {
    let target: StudyFileHandle | null = null;
    try {
      target = await pick({ suggestedName: name, types: [{ description: "Study file", accept: { "application/yaml": [".yaml", ".yml"] } }] });
    } catch (cause) {
      if (isAbort(cause)) return { status: "cancelled" };
      // The dialog itself was refused (a page inside a frame, a browser policy): the download is what is left.
    }
    if (target !== null) {
      await writeTo(target, yaml);
      return written(target);
    }
  }
  download(name, blob);
  const mark = fileMarkOf(name, config, "downloaded");
  writeFileMark(mark);
  return { status: "downloaded", name, bytes: blob.size, mark };
}

/** Whether the file a save writes will load back, and on whose word. */
export type FileCheck =
  /** `server`: the server read the same text and the page can hold its answer. `page`: the server could not be asked, and the page's own check found nothing. */
  | { loads: true; by: "server" | "page" }
  /** `reason` is whole sentences: who refuses the file, for what, and what to do about it. */
  | { loads: false; reason: string };

/** How long the server is given to read the text of a save back. After that the page's own check answers. */
export const FILE_CHECK_TIMEOUT_MS = 8_000;

/** The statuses with which the server says "this text is not a study I can read", as opposed to failing or being busy. */
const NOT_A_STUDY: readonly number[] = [400, 422];

const sentence = (text: string) => (/[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);

/**
 * The page's own answer, for when the server cannot be asked: a draft this page cannot hold, or one with an
 * arm that contradicts itself, is one no load accepts.
 */
function ownCheck(config: unknown): FileCheck {
  const refused = loadRefusals(config);
  const first = refused[0];
  if (first === undefined) return { loads: true, by: "page" };
  const more = refused.length === 1 ? "" : refused.length === 2 ? " One more thing in the study would be refused as well." : ` ${refused.length - 1} more things in the study would be refused as well.`;
  return { loads: false, reason: `${first}${more} The server could not be asked, so this is the page's own check. Fix ${refused.length === 1 ? "that" : "those"} and save again.` };
}

/**
 * Whether the study file written from this study will load back.
 *
 * The server is asked to read the very text a save writes, through the route a load uses, so its answer is
 * the answer a later load gets. When it cannot be asked in time (offline, an error on its side, too many
 * requests) the page answers from its own schema: what it cannot hold as a study, no load accepts. It never
 * rejects, and it changes nothing.
 */
export async function checkStudyFile(config: unknown, { timeoutMs = FILE_CHECK_TIMEOUT_MS }: { timeoutMs?: number } = {}): Promise<FileCheck> {
  let text: string;
  try { text = toStudyYaml(config); } catch { return ownCheck(config); }
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const parsed = await api.parseConfig({ text }, controller.signal);
    if (parseStudyConfig(parsed.config)) return { loads: true, by: "server" };
    return { loads: false, reason: unheldReason(parsed.config).message };
  } catch (cause) {
    if (!(cause instanceof ApiError) || !NOT_A_STUDY.includes(cause.status)) return ownCheck(config);
    const setting = refusedSetting(cause.message);
    return { loads: false, reason: `The server read the same text and refuses it: ${sentence(cause.message)}${setting === null ? "" : ` The setting it names is ${setting}.`} Fix that in the study and save again.` };
  } finally {
    globalThis.clearTimeout(timer);
  }
}

/** What became of a file handed to `loadStudyFile`. A failure never carries a study. */
export type LoadOutcome =
  | {
    ok: true;
    name: string;
    config: WizardStudyConfig;
    /** Evals the file names that the server does not have. */
    unknownCards: readonly string[];
    /** Whether the server read and validated the file. `false` for a JSON file loaded while it could not be reached. */
    checked: boolean;
    /**
     * What the server would refuse this study for at launch, in its own words. A file saved from a draft with
     * problems loads all the same; the page lists the problems of the study under its steps.
     */
    problems: readonly string[];
  }
  | {
    ok: false;
    /** The file's name, or null when no single file was given. */
    name: string | null;
    message: string;
    /** For a validation error: the setting the server points at, as a settings path (`arms[0].sampling`). */
    setting: string | null;
  };

const refused = (name: string | null, message: string, setting: string | null = null): LoadOutcome => ({ ok: false, name, message, setting });

/** A file's text. FileReader rather than File.text(), which older browsers lack. */
function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsText(file);
  });
}

const INDEX = /^[0-9]+$/;
const VALIDATION_ERROR = /^not a valid study configuration: ([^ ]+): /;
const JSON_NAME = /[.]json$/i;

/** `arms.0.sampling`, as the server's validation error names a place, written as a settings path: `arms[0].sampling`. */
function settingPath(location: string): string {
  return location.split(".").map((part, index) => (INDEX.test(part) ? `[${part}]` : `${index > 0 ? "." : ""}${part}`)).join("");
}

/** The setting a refusal by the server is about, when it is a validation error that names one. */
function refusedSetting(message: string): string | null {
  const location = VALIDATION_ERROR.exec(message)?.[1];
  return location === undefined ? null : settingPath(location);
}

/** Whether the server answered and said no, as opposed to not answering at all. */
function isRefusal(cause: unknown): cause is ApiError {
  return cause instanceof ApiError && cause.status >= 400 && cause.status < 500;
}

/**
 * A file read without the server: JSON this page can hold is a study; anything else has to wait for the server.
 * `status` is the status of an answer that was an error on the server's side, or null when no answer came:
 * the two are told apart, because "could not be reached" sends the reader to their own connection.
 */
function readWithoutServer(name: string, text: string, status: number | null): LoadOutcome {
  let value: unknown;
  let json = JSON_NAME.test(name);
  try { value = JSON.parse(text); json = true; } catch { value = undefined; }
  const failed = status === null ? "could not be reached" : `answered with an error (status ${status})`;
  if (!json) return refused(name, `"${name}" needs the server to be read, and it ${failed}. Nothing was changed.`);
  const study = value === undefined ? null : parseStudyConfig(value);
  if (!study) return refused(name, `"${name}" could not be checked because the server ${failed}, and on its own it is not a complete study. Nothing was changed.`);
  return { ok: true, name, config: study, unknownCards: [], checked: false, problems: [] };
}

/**
 * Reads a study file. It decides nothing about the study on screen: the caller replaces it only with the
 * `config` of an `ok` outcome, so a file that fails at any point leaves the study as it was.
 *
 * The server's parse is the authority (it validates and fills in defaults), and what it returns must also be a
 * study this page can hold. Only when the server cannot be reached is a JSON file read here instead.
 */
export async function loadStudyFile(input: File | Iterable<File>): Promise<LoadOutcome> {
  const files = input instanceof File ? [input] : [...input];
  if (files.length === 0) return refused(null, "No file was chosen. Nothing was changed.");
  if (files.length > 1) return refused(null, `A study is one file, and ${files.length} were given. Nothing was changed.`);
  const file = files[0]!;
  const { name } = file;
  if (file.size > MAX_STUDY_FILE_BYTES) return refused(name, `"${name}" is larger than a study file can be (200 kB).`);
  if (file.size === 0) return refused(name, `"${name}" is empty.`);
  let text: string;
  try { text = await readText(file); } catch { return refused(name, `"${name}" was not loaded: the file could not be read.`); }
  if (text.trim() === "") return refused(name, `"${name}" is empty.`);
  let parsed: Awaited<ReturnType<typeof api.parseConfig>>;
  try {
    parsed = await api.parseConfig({ text });
  } catch (cause) {
    if (isRefusal(cause)) return refused(name, `"${name}" was not loaded: ${cause.message}`, refusedSetting(cause.message));
    return readWithoutServer(name, text, cause instanceof ApiError ? cause.status : null);
  }
  const study = parseStudyConfig(parsed.config);
  if (!study) return unheld(name, parsed.config);
  // An older server does not send the list: a file it accepted then has no problem it knows of.
  const listed: unknown = (parsed as { problems?: unknown }).problems;
  const problems = Array.isArray(listed) ? listed.filter((item): item is string => typeof item === "string") : [];
  return { ok: true, name, config: study, unknownCards: parsed.unknown_cards, checked: true, problems };
}

/**
 * Why this page cannot hold a study the server accepted: the setting at fault where there is one, whether it
 * is a whole number too large for a browser, and the words for a save that was checked.
 */
function unheldReason(config: unknown): { setting: string | null; tooLarge: boolean; message: string } {
  const issue = StudyConfigSchema.safeParse(config).error?.issues[0];
  const setting = issue === undefined || issue.path.length === 0 ? null : settingPath(issue.path.map(String).join("."));
  let value: unknown = config;
  for (const part of issue?.path ?? []) value = value !== null && typeof value === "object" ? (value as Record<PropertyKey, unknown>)[part as PropertyKey] : undefined;
  // The server reads whole numbers up to 2^63; a browser keeps them exactly only up to 2^53 - 1.
  if (setting !== null && typeof value === "number" && Math.abs(value) > Number.MAX_SAFE_INTEGER) {
    return { setting, tooLarge: true, message: `${setting} holds a whole number larger than ${Number.MAX_SAFE_INTEGER}, the largest this page can keep exactly. Use a smaller number there and save again.` };
  }
  return { setting, tooLarge: false, message: "The server accepts the text, but this page is out of date and cannot hold what the server reads from it. Reload the page and save again." };
}

/** The refusal of a study the server accepted and this page cannot hold, naming the setting where one is at fault. */
function unheld(name: string, config: unknown): LoadOutcome {
  const { setting, tooLarge } = unheldReason(config);
  if (tooLarge && setting !== null) {
    return refused(name, `"${name}" was not loaded: ${setting} holds a whole number larger than ${Number.MAX_SAFE_INTEGER}, the largest this page can keep exactly. Use a smaller number there. Nothing was changed.`, setting);
  }
  return refused(name, `"${name}" was read by the server but this page is out of date. Reload the page and try again.`, setting);
}
