import { CONFIG_STORAGE_KEY, decodeShareConfig, encodeShareConfig, loadConfig, saveConfig, studyOneTemplate } from "./config-store";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

beforeEach(() => { Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); });

describe("configuration persistence", () => {
  it("round-trips validated unicode share links", () => { const config = studyOneTemplate(); config.title = "Quantum Δ"; expect(decodeShareConfig(encodeShareConfig(config))?.title).toBe("Quantum Δ"); });
  it("fails safely for malformed storage and share data", () => { window.localStorage.setItem(CONFIG_STORAGE_KEY, "{bad"); expect(loadConfig(window.localStorage, "?c=nope").template).toBe("study1"); });
  it("persists configuration without volatile keys", () => { const config = studyOneTemplate(); expect(saveConfig(config)).toBe(true); expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain("sk-ant-secret"); expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain("qrk_secret"); });
  it("contains the exact Study 1 eighteen-card set", () => { expect(studyOneTemplate().cards.map(({ card_id }) => card_id)).toHaveLength(18); });
});
