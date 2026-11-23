import { afterEach } from "vitest";
import { getObserverIdentity, saveObserverName } from "./observer-id";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

describe("observer identity", () => {
  const own = Object.getOwnPropertyDescriptor(window, "localStorage");
  afterEach(() => { if (own) Object.defineProperty(window, "localStorage", own); else Reflect.deleteProperty(window, "localStorage"); });

  it("is made once and remembered, with the name the observer gave", () => {
    const storage = memoryStorage();
    const first = getObserverIdentity(storage);
    expect(first.observerId).toMatch(/^obs_[0-9a-f]{24}$/);
    expect(first.displayName).toBe("");
    saveObserverName("  Ada  ", storage);
    expect(getObserverIdentity(storage)).toEqual({ observerId: first.observerId, displayName: "Ada" });
  });

  it("still gives an identity for the visit where the browser refuses access to its storage altogether", () => {
    // With all site data blocked, reading `window.localStorage` is itself what throws: the pages that ask for
    // an identity (joining a session, the live view) must draw all the same.
    Object.defineProperty(window, "localStorage", { configurable: true, get: () => { throw new DOMException("The operation is insecure.", "SecurityError"); } });
    const identity = getObserverIdentity();
    expect(identity.observerId).toMatch(/^obs_[0-9a-f]{24}$/);
    expect(identity.displayName).toBe("");
    expect(() => saveObserverName("Ada")).not.toThrow();
  });

  it("still gives an identity where storage can be reached and refuses every read and write", () => {
    const refusing = { ...memoryStorage(), getItem: () => { throw new DOMException("Denied.", "SecurityError"); }, setItem: () => { throw new DOMException("Full.", "QuotaExceededError"); } };
    expect(getObserverIdentity(refusing).observerId).toMatch(/^obs_[0-9a-f]{24}$/);
    expect(() => saveObserverName("Ada", refusing)).not.toThrow();
  });
});
