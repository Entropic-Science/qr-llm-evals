import { StudyConfigSchema, type StudyConfig } from "@qr-evals/contracts";
import studyOneDesignedJson from "../../../../packages/contracts/fixtures/study1.designed.json";
import { DEFAULT_JUDGE } from "./judge";

export type WizardStudyConfig = StudyConfig & { title: string };

export const CONFIG_STORAGE_KEY = "qr-evals.study-config.v2";
// Where the page kept its draft before a study could carry a design block. It is read once, to carry an old
// draft over, and never written or removed: a cached copy of the older page validates what it finds there, and
// would replace a designed draft it cannot read with the template.
const LEGACY_STORAGE_KEY = "qr-evals.study-config.v1";
/** Where the text of a stored draft this page could not read is set aside before the template takes its place. */
export const UNREADABLE_STORAGE_KEY = "qr-evals.study-config.unreadable.v1";

/** Where the study on screen came from, and which saved source, if any, could not be read on the way. */
export interface LoadedConfig {
  config: WizardStudyConfig;
  source: "share" | "storage" | "template";
  rejected: "share" | "storage" | null;
}

/** What to tell the researcher when a saved source could not be read, or null when nothing was rejected. */
export function rejectedNote({ source, rejected }: LoadedConfig): string | null {
  if (rejected === "storage") return "Your saved draft no longer matches this version and was replaced by the Study 1 template. The draft that could not be read is kept in this browser.";
  if (rejected === "share") {
    return source === "storage"
      ? "The shared link could not be read; your saved draft was loaded instead."
      : "The shared link could not be read; the Study 1 template was loaded instead.";
  }
  return null;
}

/** The value as a study this page can hold, or null. */
export function parseStudyConfig(value: unknown): WizardStudyConfig | null {
  const parsed = StudyConfigSchema.safeParse(value);
  return parsed.success ? parsed.data as WizardStudyConfig : null;
}

function utf8ToBase64Url(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function base64UrlToUtf8(value: string): string {
  const base64 = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(base64);
  return new TextDecoder().decode(Uint8Array.from(binary, (character) => character.charCodeAt(0)));
}

export function studyOneTemplate(): WizardStudyConfig {
  // Browser-ready editable copy of studies/study1.yaml as a designed study. The designed contract fixture
  // supplies the typed base, the recommended design block and the seed list; these values preserve the YAML's
  // optional positive-control arms and preregistration-time placeholders.
  const source = structuredClone(studyOneDesignedJson) as Record<string, unknown>;
  // The biased arms run on the fixture's own seed for them. On a control seed a positive control would share
  // that arm's random stream, and a design that names it would be refused.
  const epsilonSeed = studyOneDesignedJson.arms.find((arm) => arm.source === "prng_epsilon")?.study_seed;
  if (typeof epsilonSeed !== "number") throw new Error("The bundled Study 1 template has no seed for its biased arms.");
  // The repetitions the fixture recommends. Where an eval's own k leaves its prompts too few for an outcome
  // that decides the study, the designed template runs it at the fewest repetitions that make them enough
  // (`recommend_k` of `cards/design.py`): the self-referential probe at 24, where its card says 20.
  const recommendedK = new Map(studyOneDesignedJson.cards.flatMap((selection) => (
    typeof selection.overrides?.k === "number" ? [[selection.card_id, selection.overrides.k] as const] : []
  )));
  source.title = "Study 1 — quantum randomness and supervised observation";
  source.contact = "REPLACE_WITH_STUDY_CONTACT";
  source.arms = [
    { id: "qrng_supervised", model: "qr-llm", sampling: "creative", source: "qrng", deterministic: true, observer_mode: "supervised" },
    { id: "qrng_unsupervised", model: "qr-llm", sampling: "creative", source: "qrng", deterministic: true },
    ...[1, 7, 42].map((seed) => ({ id: `prng_seed_${seed}`, model: "qr-llm", sampling: "creative", source: "prng_seeded", study_seed: seed, deterministic: true })),
    ...([0.01, 0.03, 0.10] as const).map((epsilon) => ({ id: `prng_epsilon_0${String(epsilon * 100).padStart(2, "0")}`, model: "qr-llm", sampling: "creative", source: "prng_epsilon", study_seed: epsilonSeed, epsilon, deterministic: true, availability: "unavailable", unavailable_reason: "Optional DEP-2 control; enable only after epsilon readiness succeeds." })),
  ];
  source.cards = [
    { card_id: "tier0_pit_residuals" }, { card_id: "tier0_mmd_hamming" }, { card_id: "tier0_logprob_endpoints" },
    { card_id: "unpredictabench", overrides: { k: 100, items: 150 } }, { card_id: "novelty_bench", overrides: { k: 20 } },
    { card_id: "creative_diversity", overrides: { k: 10, items: 300 } }, { card_id: "semantic_entropy_sqa", overrides: { k: 20, items: 300 } },
    { card_id: "self_referential_probe", overrides: { k: recommendedK.get("self_referential_probe") ?? 20 } }, { card_id: "sentience_statements", overrides: { k: 10 } },
    { card_id: "introspection_scales", overrides: { k: 20 } }, { card_id: "altered_state_meq", overrides: { k: 10 } }, { card_id: "source_guessing" },
    { card_id: "moralchoice_consistency", overrides: { k: 10 } }, { card_id: "thurstonian_self_outcomes", overrides: { k: 10 } },
    { card_id: "opinion_quantum_sampling", overrides: { k: 20 } }, { card_id: "stream_deviation_windows" }, { card_id: "target_hit_ranking" },
    { card_id: "self_reported_coincidence", overrides: { k: 10 } },
  ];
  source.judge = { ...DEFAULT_JUDGE };
  const parsed = parseStudyConfig(source);
  if (!parsed) throw new Error("The bundled Study 1 template does not match StudyConfig.");
  return parsed;
}

/** The study as the `c` parameter of a share link, or null for a study that is not valid yet. */
export function encodeShareConfig(config: WizardStudyConfig): string | null {
  try {
    const parsed = parseStudyConfig(config);
    return parsed ? utf8ToBase64Url(JSON.stringify(parsed)) : null;
  } catch { return null; }
}

export function decodeShareConfig(encoded: string): WizardStudyConfig | null {
  try { return parseStudyConfig(JSON.parse(base64UrlToUtf8(encoded)) as unknown); } catch { return null; }
}

/** Takes `c` out of the address bar, so a reload opens the saved draft with its later edits, not the link again. */
export function dropShareParameter(search: string = window.location.search): void {
  // A query string the caller supplied itself is not the address bar's to rewrite.
  if (search !== window.location.search) return;
  const url = new URL(window.location.href);
  if (!url.searchParams.has("c")) return;
  url.searchParams.delete("c");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

/** This browser's storage, or null where the browser refuses access to it (all site data blocked). */
export function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

/** The saved draft and its text. `rejected` is set when one was found and could not be read as a study. */
function readStored(storage: Storage | null): { config: WizardStudyConfig | null; rejected: boolean } {
  if (storage === null) return { config: null, rejected: false };
  let text: string | null;
  let carriedOver = false;
  try {
    text = storage.getItem(CONFIG_STORAGE_KEY);
    if (text === null) {
      text = storage.getItem(LEGACY_STORAGE_KEY);
      carriedOver = text !== null;
    }
  } catch { return { config: null, rejected: false }; /* Storage can be blocked. */ }
  if (text === null) return { config: null, rejected: false };
  let config: WizardStudyConfig | null = null;
  try { config = parseStudyConfig(JSON.parse(text) as unknown); } catch { /* Malformed JSON is a draft that cannot be read. */ }
  if (config && carriedOver) {
    // The old draft moves over as it was written: it has no design block, and it does not get one here.
    try { storage.setItem(CONFIG_STORAGE_KEY, text); } catch { /* It is carried over again on the next load. */ }
  }
  if (config === null && !carriedOver) {
    // The page is about to write another study over this key. The text it cannot read is the only copy of
    // that work, so it is set aside first, where `unreadableDraft` finds it.
    try { storage.setItem(UNREADABLE_STORAGE_KEY, text); } catch { /* Nothing more can be done for it. */ }
  }
  return { config, rejected: config === null };
}

/** The saved draft, or null when there is none this page can read. A share link in the address is not looked at. */
export function readDraft(storage: Storage | null = browserStorage()): WizardStudyConfig | null {
  return readStored(storage).config;
}

/** The text of a draft this page found and could not read, kept when the template took its place; or null. */
export function unreadableDraft(storage: Storage | null = browserStorage()): string | null {
  try { return storage?.getItem(UNREADABLE_STORAGE_KEY) ?? null; } catch { return null; }
}

export interface LoadOptions {
  /**
   * Whether a share link is used up by this read: saved as the draft, and `c` taken out of the address bar.
   * Only the setup does that, and only once it knows no unsaved work is lost by it. Every other page shows
   * the link's study and leaves the stored draft alone.
   */
  consume?: boolean;
}

/**
 * The study to open: a share link first, then the saved draft, then the Study 1 template.
 *
 * Reading changes nothing unless `consume` is set. A consumed share link is saved as the draft and `c` leaves
 * the address bar, so reloading the page does not put the link's study back over what was edited since.
 */
export function loadConfig(storage: Storage | null = browserStorage(), search = window.location.search, { consume = false }: LoadOptions = {}): LoadedConfig {
  let rejected: LoadedConfig["rejected"] = null;
  const shared = new URLSearchParams(search).get("c");
  if (shared) {
    const decoded = decodeShareConfig(shared);
    if (decoded) {
      if (consume) consumeShareLink(decoded, storage, search);
      return { config: decoded, source: "share", rejected: null };
    }
    rejected = "share";
  }
  const stored = readStored(storage);
  if (stored.config) return { config: stored.config, source: "storage", rejected };
  // A draft that was lost matters more than a link that could not be read.
  if (stored.rejected) rejected = "storage";
  return { config: studyOneTemplate(), source: "template", rejected };
}

/**
 * Makes the study of a share link the draft. `false` when the browser did not store it: without a saved copy
 * the link is the only place the study lives, so it stays in the address bar.
 */
export function consumeShareLink(config: WizardStudyConfig, storage: Storage | null = browserStorage(), search = window.location.search): boolean {
  if (!saveConfig(config, storage)) return false;
  dropShareParameter(search);
  return true;
}

export function saveConfig(config: WizardStudyConfig, storage: Storage | null = browserStorage()): boolean {
  const parsed = parseStudyConfig(config);
  if (!parsed || storage === null) return false;
  try { storage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(parsed)); return true; } catch { return false; }
}
