import { authResponse } from "./session-fixture";
import { clearSession, getSession, nextPath, SESSION_STORAGE_KEY, setSession, updateSessionUser } from "./session";

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

  it("removes an expired session from storage and rejects an unreadable expiry", () => {
    setSession(authResponse({}, new Date(Date.now() - 1000).toISOString()));
    expect(getSession()).toBeNull();
    expect(window.localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    setSession(authResponse({}, "not a date"));
    expect(getSession()).toBeNull();
  });

  it("stays signed in for the page when localStorage refuses writes", () => {
    const blocked = memoryStorage();
    blocked.setItem = () => { throw new DOMException("blocked", "SecurityError"); };
    Object.defineProperty(window, "localStorage", { configurable: true, value: blocked });
    const session = setSession(authResponse());
    expect(getSession()).toEqual(session);
    clearSession();
    expect(getSession()).toBeNull();
  });

  it("only follows a same-site next path", () => {
    expect(nextPath("?next=/admin")).toBe("/admin");
    expect(nextPath("?next=/")).toBe("/");
    expect(nextPath("")).toBe("/account");
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "admin"]) {
      expect(nextPath(`?next=${encodeURIComponent(bad)}`)).toBe("/account");
    }
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
    expect(Object.keys(stored.user as object).sort()).toEqual(["created_at", "is_admin", "key_fingerprint", "key_linked_at", "key_name", "user_id", "username"]);
  });
});
