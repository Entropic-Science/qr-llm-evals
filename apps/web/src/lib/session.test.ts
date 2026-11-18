import { authResponse } from "./session-fixture";
import { clearSession, getSession, SESSION_STORAGE_KEY, setSession, updateSessionUser } from "./session";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

beforeEach(() => { Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); clearSession(); });

describe("session store", () => {
  it("round-trips a session and clears it", () => {
    expect(getSession()).toBeNull();
    const session = setSession(authResponse());
    expect(getSession()).toEqual(session);
    expect(getSession()?.user.username).toBe("alice");
    clearSession();
    expect(getSession()).toBeNull();
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });

  it("treats an expired or malformed session as signed out", () => {
    setSession(authResponse({}, new Date(Date.now() - 1000).toISOString()));
    expect(getSession()).toBeNull();
    window.localStorage.setItem(SESSION_STORAGE_KEY, "{not json");
    expect(getSession()).toBeNull();
    window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ token: 5 }));
    expect(getSession()).toBeNull();
  });

  it("updates the stored user without touching the token", () => {
    const before = setSession(authResponse());
    updateSessionUser({ ...before.user, key_name: "new-key" });
    expect(getSession()?.user.key_name).toBe("new-key");
    expect(getSession()?.token).toBe(before.token);
  });

  it("stores nothing but the session token and the public account fields", () => {
    setSession(authResponse());
    const stored = JSON.parse(window.localStorage.getItem(SESSION_STORAGE_KEY) ?? "{}") as Record<string, unknown>;
    expect(Object.keys(stored).sort()).toEqual(["expiresAt", "token", "user"]);
    expect(JSON.stringify(stored)).not.toContain("qrk_");
  });
});
