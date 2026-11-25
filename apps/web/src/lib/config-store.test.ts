import { cards } from "@qr-evals/contracts";
import studyOneJson from "../../../../packages/contracts/fixtures/study1.json";
import {
  CONFIG_STORAGE_KEY, UNREADABLE_STORAGE_KEY, consumeShareLink, decodeShareConfig, encodeShareConfig, loadConfig, parseStudyConfig,
  readDraft, rejectedNote, saveConfig, studyOneTemplate, unreadableDraft, type WizardStudyConfig,
} from "./config-store";
import { loadDisplaySettings, saveDisplaySettings } from "./supervised-settings";
import { draftHypotheses, recommendDesign } from "./design";

const LEGACY_KEY = "qr-evals.study-config.v1";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

/** Storage that can be read but refuses every write, as a full or locked-down browser does. */
function readOnlyStorage(): Storage {
  return { ...memoryStorage(), setItem: () => { throw new DOMException("The quota has been exceeded.", "QuotaExceededError"); } };
}

/** Study 1 as the page before designed studies saved it: no design block. */
function legacyDraft(): WizardStudyConfig {
  const parsed = parseStudyConfig(studyOneJson);
  if (!parsed) throw new Error("study1.json does not match StudyConfig.");
  return parsed;
}

describe("a study's token limits, as the page holds them", () => {
  const limitKeys = (config: WizardStudyConfig) => config.cards.filter((selection) => Object.hasOwn(selection.overrides ?? {}, "max_tokens")).map((selection) => selection.card_id);

  it("holds no limit a study does not set, not even as null, so the stored and shared study keeps its bytes", () => {
    expect(limitKeys(studyOneTemplate())).toEqual([]);
    expect(limitKeys(legacyDraft())).toEqual([]);
    const written = { ...legacyDraft(), cards: legacyDraft().cards.map((selection) => ({ ...selection, overrides: { ...selection.overrides, max_tokens: null } })) };
    expect(limitKeys(parseStudyConfig(written)!)).toEqual([]);
    expect(JSON.stringify(parseStudyConfig(written))).toBe(JSON.stringify(legacyDraft()));
    expect(JSON.stringify(studyOneTemplate())).not.toContain("max_tokens\":null");
  });

  it("keeps a limit a study sets through the browser's storage and a share link", () => {
    const template = studyOneTemplate();
    const set: WizardStudyConfig = { ...template, cards: template.cards.map((selection) => (selection.card_id === "novelty_bench" ? { ...selection, overrides: { ...selection.overrides, max_tokens: 2400 } } : selection)) };
    expect(limitKeys(parseStudyConfig(set)!)).toEqual(["novelty_bench"]);
    const storage = memoryStorage();
    expect(saveConfig(set, storage)).toBe(true);
    expect(readDraft(storage)).toEqual(set);
    expect(decodeShareConfig(encodeShareConfig(set)!)).toEqual(set);
    // Above what any study may hold, the schema refuses it.
    expect(parseStudyConfig({ ...set, cards: [{ card_id: "novelty_bench", overrides: { max_tokens: 64_001 } }] })).toBeNull();
  });
});

function shareOf(config: WizardStudyConfig): string {
  const encoded = encodeShareConfig(config);
  if (encoded === null) throw new Error("A valid study has a share link.");
  return encoded;
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
  window.history.replaceState(null, "", "/");
});

describe("configuration persistence", () => {
  it("round-trips validated unicode share links", () => { const config = studyOneTemplate(); config.title = "Quantum Δ"; expect(decodeShareConfig(shareOf(config))?.title).toBe("Quantum Δ"); });
  it("fails safely for malformed storage and share data", () => {
    window.localStorage.setItem(CONFIG_STORAGE_KEY, "{bad");
    const loaded = loadConfig(window.localStorage, "?c=nope");
    expect(loaded.config.template).toBe("study1");
    expect(loaded.config).toEqual(studyOneTemplate());
    expect(loaded.source).toBe("template");
  });
  it("persists configuration without volatile keys", () => { const config = studyOneTemplate(); expect(saveConfig(config)).toBe(true); expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain("sk-ant-secret"); expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain("qrk_secret"); });
  it("refuses to store a study that carries anything a study has no field for, such as a key", () => {
    const config = studyOneTemplate();
    const withKey = { ...config, provider_keys: { anthropic: "sk-ant-secret" } } as WizardStudyConfig;
    const withJudgeKey = { ...config, judge: { ...config.judge, api_key: "sk-ant-secret" } } as WizardStudyConfig;
    expect(saveConfig(withKey)).toBe(false);
    expect(saveConfig(withJudgeKey)).toBe(false);
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBeNull();
    expect(encodeShareConfig(withKey)).toBeNull();
  });
  it("contains the exact Study 1 eighteen-card set", () => { expect(studyOneTemplate().cards.map(({ card_id }) => card_id)).toHaveLength(18); });
});

describe("the Study 1 template", () => {
  it("is a designed study whose design block is the recommended one, unedited", () => {
    const template = studyOneTemplate();
    expect(template.design).not.toBeNull();
    expect(template.design).toEqual(recommendDesign(template, cards));
    expect(template.design?.confirmatory.map(({ endpoint }) => endpoint)).toEqual(["pit_ks_two_sample", "mmd_hamming", "experience_claim_rate"]);
  });

  it("drafts its hypotheses with nothing to fix: the rated one is sufficient", () => {
    const hypotheses = draftHypotheses(studyOneTemplate(), cards);
    expect(hypotheses.map(({ id, endpoint, sample_size }) => [id, endpoint, sample_size.rating])).toEqual([
      ["H1", "pit_ks_two_sample", "unknown"], ["H2", "mmd_hamming", "unknown"], ["H3", "experience_claim_rate", "sufficient"],
    ]);
  });

  it("keeps its optional biased arms off the control seeds and every seed in the seed list", () => {
    const { arms, seeds } = studyOneTemplate();
    const control = arms.filter((arm) => arm.source === "prng_seeded").map((arm) => arm.study_seed);
    const biased = arms.filter((arm) => arm.source === "prng_epsilon");
    expect(control).toEqual([1, 7, 42]);
    expect(biased.map((arm) => arm.epsilon)).toEqual([0.01, 0.03, 0.1]);
    for (const arm of biased) {
      expect(control).not.toContain(arm.study_seed);
      expect(arm.availability).toBe("unavailable");
    }
    for (const arm of [...arms.filter((item) => item.source === "prng_seeded"), ...biased]) expect(seeds).toContain(arm.study_seed);
  });
});

describe("share links", () => {
  it("returns null instead of throwing for a study that is not valid", () => {
    const template = studyOneTemplate();
    const invalid = [
      { ...template, arms: [{ id: "broken" }] },
      { ...template, design: { ...template.design, alpha: 0.2 } },
      { ...template, title: undefined },
      null,
    ] as unknown as WizardStudyConfig[];
    for (const config of invalid) {
      expect(() => encodeShareConfig(config)).not.toThrow();
      expect(encodeShareConfig(config)).toBeNull();
    }
    expect(encodeShareConfig(template)).toEqual(expect.any(String));
  });

  it("carries the design block", () => {
    const template = studyOneTemplate();
    expect(decodeShareConfig(shareOf(template))).toEqual(template);
  });

  it("is only shown by a read: the stored draft and the address bar are left as they were", () => {
    const mine = { ...studyOneTemplate(), title: "My study", expectations: "Three hours of edits." };
    expect(saveConfig(mine)).toBe(true);
    const stored = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    const shared = { ...studyOneTemplate(), title: "A colleague's study" };
    const address = `/prereg?c=${shareOf(shared)}`;
    window.history.replaceState(null, "", address);

    expect(loadConfig()).toEqual({ config: shared, source: "share", rejected: null });
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(stored);
    expect(readDraft()).toEqual(mine);
    expect(`${window.location.pathname}${window.location.search}`).toBe(address);
    // The link is still the study on a reload, and the draft is still the researcher's own.
    expect(loadConfig().config).toEqual(shared);
  });

  it("does not let the session display settings of a shared study write over the stored draft", () => {
    const mine = { ...studyOneTemplate(), title: "My study" };
    saveConfig(mine);
    const stored = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    window.history.replaceState(null, "", `/live?c=${shareOf({ ...studyOneTemplate(), title: "A colleague's study" })}`);
    const settings = { ...loadDisplaySettings("single"), session_min: 12 };
    expect(saveDisplaySettings("single", settings)).toBe(true);
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(stored);
    // Without a link in the address the stored draft follows the display, as before.
    window.history.replaceState(null, "", "/live");
    expect(saveDisplaySettings("single", settings)).toBe(true);
    expect(readDraft()?.supervised?.settings_by_display?.single?.session_min).toBe(12);
  });

  it("is consumed once by the setup: saved as the draft and taken out of the address bar", () => {
    const shared = { ...studyOneTemplate(), title: "From a link" };
    window.history.replaceState({ kept: true }, "", `/configure?view=map&c=${shareOf(shared)}#design`);

    const first = loadConfig(window.localStorage, window.location.search, { consume: true });
    expect(first).toEqual({ config: shared, source: "share", rejected: null });
    expect(window.location.pathname).toBe("/configure");
    expect(window.location.search).toBe("?view=map");
    expect(window.location.hash).toBe("#design");
    expect(window.history.state).toEqual({ kept: true });
    expect(JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) ?? "null")).toEqual(shared);

    // A reload now opens the draft, with whatever was edited since, and not the link's study again.
    const edited = { ...shared, title: "Edited after opening the link" };
    expect(saveConfig(edited)).toBe(true);
    expect(loadConfig()).toEqual({ config: edited, source: "storage", rejected: null });
  });

  it("stays in the address bar when the study could not be saved, so a reload still opens it", () => {
    const shared = { ...studyOneTemplate(), title: "From a link" };
    const address = `/?c=${shareOf(shared)}`;
    window.history.replaceState(null, "", address);
    const loaded = loadConfig(readOnlyStorage(), window.location.search, { consume: true });
    expect(loaded).toEqual({ config: shared, source: "share", rejected: null });
    expect(`${window.location.pathname}${window.location.search}`).toBe(address);
    expect(consumeShareLink(shared, readOnlyStorage())).toBe(false);
    expect(`${window.location.pathname}${window.location.search}`).toBe(address);
  });

  it("leaves the address bar alone when the query string was supplied by the caller", () => {
    const shared = { ...studyOneTemplate(), title: "From a link" };
    window.history.replaceState(null, "", "/?c=not-this-one");
    expect(loadConfig(window.localStorage, `?c=${shareOf(shared)}`, { consume: true }).source).toBe("share");
    expect(window.location.search).toBe("?c=not-this-one");
  });

  it("reports a link that could not be read and opens the saved draft instead", () => {
    const draft = { ...studyOneTemplate(), title: "My draft" };
    saveConfig(draft);
    window.history.replaceState(null, "", "/?c=nope");
    const loaded = loadConfig();
    expect(loaded).toEqual({ config: draft, source: "storage", rejected: "share" });
    expect(rejectedNote(loaded)).toBe("The shared link could not be read; your saved draft was loaded instead.");
    expect(window.location.search).toBe("?c=nope");
  });

  it("says the template was loaded when there was no draft to fall back on", () => {
    window.history.replaceState(null, "", "/?c=nope");
    const loaded = loadConfig();
    expect(loaded).toMatchObject({ source: "template", rejected: "share" });
    expect(rejectedNote(loaded)).toBe("The shared link could not be read; the Study 1 template was loaded instead.");
  });
});

describe("the saved draft", () => {
  it("opens the template, with nothing rejected, when nothing was saved", () => {
    const loaded = loadConfig();
    expect(loaded).toEqual({ config: studyOneTemplate(), source: "template", rejected: null });
    expect(rejectedNote(loaded)).toBeNull();
    // Loading alone writes nothing.
    expect(window.localStorage.length).toBe(0);
  });

  it("reports a draft that no longer matches this version", () => {
    const outdated = JSON.stringify({ ...studyOneTemplate(), a_field_from_another_version: true });
    for (const text of [outdated, "{bad", "null", ""]) {
      window.localStorage.setItem(CONFIG_STORAGE_KEY, text);
      const loaded = loadConfig();
      expect(loaded, JSON.stringify(text)).toEqual({ config: studyOneTemplate(), source: "template", rejected: "storage" });
      expect(rejectedNote(loaded)).toBe("Your saved draft no longer matches this version and was replaced by the Study 1 template. The draft that could not be read is kept in this browser.");
    }
  });

  it("sets the text of a draft it cannot read aside before anything is written over it", () => {
    const outdated = JSON.stringify({ ...studyOneTemplate(), title: "Work of a week", a_field_from_another_version: true });
    window.localStorage.setItem(CONFIG_STORAGE_KEY, outdated);
    expect(unreadableDraft()).toBeNull();
    const loaded = loadConfig();
    expect(loaded.rejected).toBe("storage");
    // The setup now writes the template as the draft, as it does on every load.
    expect(saveConfig(loaded.config)).toBe(true);
    expect(readDraft()?.title).toBe(studyOneTemplate().title);
    expect(window.localStorage.getItem(UNREADABLE_STORAGE_KEY)).toBe(outdated);
    expect(unreadableDraft()).toBe(outdated);
  });

  it("reads a draft with a resampled device pool arm and raw entropy as it was saved", () => {
    const template = studyOneTemplate();
    const pooled = {
      ...template,
      conditioning: { mode: "raw", hash: "sha256", in_bytes: 128, keep_bytes: 16 },
      arms: [...template.arms, { id: "pool", model: "qr-llm", sampling: "creative", source: "prng_resample" }],
    };
    window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(pooled));
    const loaded = loadConfig();
    expect(loaded.source).toBe("storage");
    expect(loaded.rejected).toBeNull();
    expect(loaded.config.arms.at(-1)).toMatchObject({ id: "pool", source: "prng_resample" });
    expect(loaded.config.conditioning?.mode).toBe("raw");
    expect(unreadableDraft()).toBeNull();
  });

  it("sets nothing aside for a draft it can read", () => {
    saveConfig({ ...studyOneTemplate(), title: "Readable" });
    loadConfig();
    expect(unreadableDraft()).toBeNull();
  });

  it("reports the lost draft rather than the unreadable link when both failed", () => {
    window.localStorage.setItem(CONFIG_STORAGE_KEY, "{bad");
    expect(loadConfig(window.localStorage, "?c=nope")).toMatchObject({ source: "template", rejected: "storage" });
  });

  it("does not fail, or report a lost draft, when storage cannot be read at all", () => {
    const blocked = { ...memoryStorage(), getItem: () => { throw new DOMException("Access is denied.", "SecurityError"); } };
    expect(loadConfig(blocked)).toEqual({ config: studyOneTemplate(), source: "template", rejected: null });
  });

  it("opens the template and saves nothing, without throwing, where the browser refuses access to storage itself", () => {
    // With all site data blocked, reading `window.localStorage` is what throws, before any call on it.
    const own = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", { configurable: true, get: () => { throw new DOMException("The operation is insecure.", "SecurityError"); } });
    try {
      expect(loadConfig()).toEqual({ config: studyOneTemplate(), source: "template", rejected: null });
      expect(saveConfig(studyOneTemplate())).toBe(false);
      expect(readDraft()).toBeNull();
      expect(unreadableDraft()).toBeNull();
      expect(loadDisplaySettings("tiled").streams).toBe(4);
      expect(saveDisplaySettings("tiled", loadDisplaySettings("tiled"))).toBe(false);
      const shared = { ...studyOneTemplate(), title: "From a link" };
      expect(loadConfig(undefined, `?c=${shareOf(shared)}`, { consume: true }).config).toEqual(shared);
    } finally {
      if (own) Object.defineProperty(window, "localStorage", own); else Reflect.deleteProperty(window, "localStorage");
    }
  });
});

describe("carrying a draft over from the previous storage key", () => {
  it("copies the old draft as it was written, without a design block, and leaves the old key alone", () => {
    const old = JSON.stringify(legacyDraft());
    window.localStorage.setItem(LEGACY_KEY, old);

    const loaded = loadConfig();
    expect(loaded.source).toBe("storage");
    expect(loaded.rejected).toBeNull();
    expect(loaded.config).toEqual(legacyDraft());
    expect(loaded.config.design ?? null).toBeNull();
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(old);
    expect(window.localStorage.getItem(LEGACY_KEY)).toBe(old);
  });

  it("never writes the old key again, so an older copy of the page does not meet a designed draft", () => {
    const old = JSON.stringify(legacyDraft());
    window.localStorage.setItem(LEGACY_KEY, old);
    loadConfig();
    const designed = { ...studyOneTemplate(), title: "Designed later" };
    expect(saveConfig(designed)).toBe(true);
    expect(window.localStorage.getItem(LEGACY_KEY)).toBe(old);
    expect(loadConfig().config).toEqual(designed);
  });

  it("prefers a draft under the current key and does not touch the old one", () => {
    const current = { ...studyOneTemplate(), title: "Current" };
    saveConfig(current);
    window.localStorage.setItem(LEGACY_KEY, JSON.stringify({ ...legacyDraft(), title: "Old" }));
    expect(loadConfig()).toEqual({ config: current, source: "storage", rejected: null });
  });

  it("reports an old draft that cannot be read, and carries nothing over", () => {
    window.localStorage.setItem(LEGACY_KEY, "{bad");
    expect(loadConfig()).toEqual({ config: studyOneTemplate(), source: "template", rejected: "storage" });
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(LEGACY_KEY)).toBe("{bad");
  });

  it("still opens the old draft when it cannot be copied", () => {
    const storage = readOnlyStorage();
    const seeded = memoryStorage();
    seeded.setItem(LEGACY_KEY, JSON.stringify(legacyDraft()));
    const loaded = loadConfig({ ...storage, getItem: (key) => seeded.getItem(key) });
    expect(loaded).toEqual({ config: legacyDraft(), source: "storage", rejected: null });
  });
});
