import { StudyConfigSchema, type StudyConfig } from "@qr-evals/contracts";
import studyOneJson from "../../../../packages/contracts/fixtures/study1.json";
import { DEFAULT_JUDGE } from "./judge";

export type WizardStudyConfig = StudyConfig & { title: string };

export const CONFIG_STORAGE_KEY = "qr-evals.study-config.v1";

function validate(value: unknown): WizardStudyConfig | null {
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
  // Browser-ready editable copy of studies/study1.yaml. The generated contract
  // fixture supplies the fully typed base; these values preserve the YAML's
  // optional positive-control arms and preregistration-time placeholders.
  const source = structuredClone(studyOneJson) as Record<string, unknown>;
  source.title = "Study 1 — quantum randomness and supervised observation";
  source.contact = "REPLACE_WITH_STUDY_CONTACT";
  source.arms = [
    { id: "qrng_supervised", model: "qr-llm", sampling: "creative", source: "qrng", deterministic: true, observer_mode: "supervised" },
    { id: "qrng_unsupervised", model: "qr-llm", sampling: "creative", source: "qrng", deterministic: true },
    ...[1, 7, 42].map((seed) => ({ id: `prng_seed_${seed}`, model: "qr-llm", sampling: "creative", source: "prng_seeded", study_seed: seed, deterministic: true })),
    ...([0.01, 0.03, 0.10] as const).map((epsilon) => ({ id: `prng_epsilon_0${String(epsilon * 100).padStart(2, "0")}`, model: "qr-llm", sampling: "creative", source: "prng_epsilon", study_seed: 42, epsilon, deterministic: true, availability: "unavailable", unavailable_reason: "Optional DEP-2 control; enable only after epsilon readiness succeeds." })),
  ];
  source.cards = [
    { card_id: "tier0_pit_residuals" }, { card_id: "tier0_mmd_hamming" }, { card_id: "tier0_logprob_endpoints" },
    { card_id: "unpredictabench", overrides: { k: 100, items: 150 } }, { card_id: "novelty_bench", overrides: { k: 20 } },
    { card_id: "creative_diversity", overrides: { k: 10, items: 300 } }, { card_id: "semantic_entropy_sqa", overrides: { k: 20, items: 300 } },
    { card_id: "self_referential_probe", overrides: { k: 20 } }, { card_id: "sentience_statements", overrides: { k: 10 } },
    { card_id: "introspection_scales", overrides: { k: 20 } }, { card_id: "altered_state_meq", overrides: { k: 10 } }, { card_id: "source_guessing" },
    { card_id: "moralchoice_consistency", overrides: { k: 10 } }, { card_id: "thurstonian_self_outcomes", overrides: { k: 10 } },
    { card_id: "opinion_quantum_sampling", overrides: { k: 20 } }, { card_id: "stream_deviation_windows" }, { card_id: "target_hit_ranking" },
    { card_id: "self_reported_coincidence", overrides: { k: 10 } },
  ];
  source.judge = { ...DEFAULT_JUDGE };
  const parsed = validate(source);
  if (!parsed) throw new Error("The bundled Study 1 template does not match StudyConfig.");
  return parsed;
}

export function encodeShareConfig(config: WizardStudyConfig): string {
  const parsed = validate(config);
  if (!parsed) throw new Error("Cannot share an invalid study configuration.");
  return utf8ToBase64Url(JSON.stringify(parsed));
}

export function decodeShareConfig(encoded: string): WizardStudyConfig | null {
  try { return validate(JSON.parse(base64UrlToUtf8(encoded)) as unknown); } catch { return null; }
}

export function loadConfig(storage: Storage = window.localStorage, search = window.location.search): WizardStudyConfig {
  const shared = new URLSearchParams(search).get("c");
  if (shared) {
    const decoded = decodeShareConfig(shared);
    if (decoded) return decoded;
  }
  try {
    const stored = storage.getItem(CONFIG_STORAGE_KEY);
    if (stored) {
      const decoded = validate(JSON.parse(stored) as unknown);
      if (decoded) return decoded;
    }
  } catch { /* Storage can be blocked or contain malformed JSON. */ }
  return studyOneTemplate();
}

export function saveConfig(config: WizardStudyConfig, storage: Storage = window.localStorage): boolean {
  const parsed = validate(config);
  if (!parsed) return false;
  try { storage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(parsed)); return true; } catch { return false; }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function configHash(config: WizardStudyConfig): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(config)));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
