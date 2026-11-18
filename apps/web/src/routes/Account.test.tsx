import type { AdminUsersResponse, UsageResponse } from "@qr-evals/contracts";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { App } from "../App";
import { api, ApiError } from "../lib/api";
import { clearSession, getSession, SESSION_STORAGE_KEY, setSession } from "../lib/session";
import { authResponse } from "../lib/session-fixture";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}
const totals = (calls: number, prompt: number, completion: number, charged: number) => ({ calls, prompt_tokens: prompt, completion_tokens: completion, reasoning_tokens: 0, charged_tokens: charged });
const usage: UsageResponse = {
  quota: { limit: 2_000_000, used: 1_234_567, remaining: 765_433, exhausted: false },
  totals: totals(30, 1_000_000, 240_000, 1_234_567),
  by_kind: [{ key: "generation", totals: totals(24, 994_567, 240_000, 1_234_567) }, { key: "judge", totals: totals(6, 5433, 0, 0) }],
  by_source: [{ key: "quantum", totals: totals(12, 500_000, 120_000, 620_000) }, { key: "seeded_prng", totals: totals(12, 494_567, 120_000, 614_567) }],
  by_day: [{ key: "2026-11-18", totals: totals(30, 1_000_000, 240_000, 1_234_567) }],
  by_run: [{ key: "run_abc", totals: totals(30, 1_000_000, 240_000, 1_234_567) }],
};

function renderAt(path: string) { window.history.pushState({}, "", path); return render(<App />); }

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() });
  clearSession();
  vi.spyOn(api, "me").mockImplementation(() => Promise.resolve(getSession()!.user));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.pushState({}, "", "/"); });

describe("sign in and sign up", () => {
  it("signs in, stores the session, and shows the account", async () => {
    const login = vi.spyOn(api, "login").mockResolvedValue(authResponse());
    vi.spyOn(api, "usage").mockResolvedValue(usage);
    renderAt("/login");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { level: 1, name: "alice" })).toBeInTheDocument();
    expect(login).toHaveBeenCalledWith({ username: "alice", password: "correct horse battery" });
    expect(getSession()?.user.username).toBe("alice");
    expect(screen.getByRole("link", { name: "alice" })).toHaveAttribute("href", "/account");
  });

  it("shows the server's reason when sign-in fails and stores nothing", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError("wrong username or password", 401));
    renderAt("/login");
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong username or password");
    expect(getSession()).toBeNull();
  });

  it("sends the key once at sign-up and never stores it", async () => {
    const signup = vi.spyOn(api, "signup").mockResolvedValue(authResponse());
    vi.spyOn(api, "usage").mockResolvedValue(usage);
    renderAt("/signup?next=/account");
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.change(screen.getByLabelText("api.entropic.science key"), { target: { value: "qrk_typed-once" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { level: 1, name: "alice" });
    expect(signup).toHaveBeenCalledWith({ username: "alice", password: "correct horse battery", api_key: "qrk_typed-once" });
    const stored = window.localStorage.getItem(SESSION_STORAGE_KEY) ?? "";
    expect(stored).not.toContain("qrk_typed-once");
    expect(stored).not.toContain("correct horse battery");
  });
});

describe("account page", () => {
  it("asks a signed-out visitor to sign in", () => {
    renderAt("/account");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("You are signed out");
  });

  it("shows the quota and the usage tables", async () => {
    setSession(authResponse());
    vi.spyOn(api, "usage").mockResolvedValue(usage);
    renderAt("/account");
    const meter = await screen.findByRole("progressbar", { name: "Token quota used" });
    expect(meter).toHaveAttribute("aria-valuenow", "1234567");
    expect(screen.getByText(/of 2,000,000 tokens used/)).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Quantum (QRNG)" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Seeded PRNG" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Judge" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "run_abc" })).toHaveAttribute("href", "/runs/run_abc");
    expect(screen.queryByRole("link", { name: "All accounts" })).not.toBeInTheDocument();
  });

  it("says so when the quota is used up", async () => {
    setSession(authResponse());
    vi.spyOn(api, "usage").mockResolvedValue({ ...usage, quota: { limit: 100, used: 140, remaining: 0, exhausted: true } });
    renderAt("/account");
    expect(await screen.findByText(/The quota is used up/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("links a different key and shows the new name", async () => {
    const session = setSession(authResponse());
    vi.spyOn(api, "usage").mockResolvedValue(usage);
    const linkKey = vi.spyOn(api, "linkKey").mockResolvedValue({ ...session.user, key_name: "second-key" });
    renderAt("/account");
    await screen.findByRole("progressbar");
    fireEvent.change(screen.getByLabelText("Link a different api.entropic.science key"), { target: { value: "qrk_another" } });
    fireEvent.click(screen.getByRole("button", { name: "Link key" }));
    expect(await screen.findByText("second-key")).toBeInTheDocument();
    expect(linkKey).toHaveBeenCalledWith({ api_key: "qrk_another" }, session.token);
    expect(screen.getByLabelText("Link a different api.entropic.science key")).toHaveValue("");
  });

  it("signs out when the server no longer accepts the session", async () => {
    setSession(authResponse());
    vi.spyOn(api, "usage").mockRejectedValue(new ApiError("session is invalid or has expired; sign in again", 401));
    renderAt("/account");
    expect(await screen.findByRole("heading", { level: 1, name: "You are signed out" })).toBeInTheDocument();
    expect(getSession()).toBeNull();
  });

  it("signs out on request", async () => {
    setSession(authResponse());
    vi.spyOn(api, "usage").mockResolvedValue(usage);
    const logout = vi.spyOn(api, "logout").mockResolvedValue({ ok: true });
    renderAt("/account");
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(getSession()).toBeNull());
    expect(logout).toHaveBeenCalled();
  });
});

describe("admin page", () => {
  const admin = authResponse({ is_admin: true });
  const listing: AdminUsersResponse = {
    default_token_quota: 2_000_000,
    users: [
      { user: admin.user, disabled: false, token_quota: null, quota: { limit: 2_000_000, used: 0, remaining: 2_000_000, exhausted: false }, totals: totals(0, 0, 0, 0) },
      { user: { ...admin.user, user_id: "usr_2", username: "bob", is_admin: false, key_name: "bob-key" }, disabled: false, token_quota: 500, quota: { limit: 500, used: 640, remaining: 0, exhausted: true }, totals: totals(8, 400, 240, 640) },
    ],
  };

  it("picks up an admin flag granted after sign-in", async () => {
    setSession(authResponse());
    vi.spyOn(api, "me").mockResolvedValue(admin.user);
    vi.spyOn(api, "adminUsers").mockResolvedValue(listing);
    renderAt("/admin");
    expect(await screen.findByRole("heading", { level: 1, name: "All accounts" })).toBeInTheDocument();
    expect(getSession()?.user.is_admin).toBe(true);
  });

  it("signs out everywhere when the server has revoked the session", async () => {
    setSession(authResponse());
    vi.spyOn(api, "me").mockRejectedValue(new ApiError("session is invalid or has expired; sign in again", 401));
    renderAt("/");
    await waitFor(() => expect(getSession()).toBeNull());
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("is closed to accounts that are not admins", () => {
    setSession(authResponse());
    const adminUsers = vi.spyOn(api, "adminUsers");
    renderAt("/admin");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Admins only");
    expect(adminUsers).not.toHaveBeenCalled();
  });

  it("lists accounts and saves a new quota", async () => {
    setSession(admin);
    vi.spyOn(api, "adminUsers").mockResolvedValue(listing);
    const update = vi.spyOn(api, "adminUpdateUser").mockResolvedValue(listing.users[1]!);
    renderAt("/admin");
    expect(await screen.findByRole("rowheader", { name: /bob/ })).toBeInTheDocument();
    expect(screen.getByText("used up")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable alice" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Disable bob" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("usr_2", { disabled: true }, admin.session_token));

    const input = screen.getByLabelText(/Token quota for bob/);
    expect(input).toHaveValue("500");
    fireEvent.change(input, { target: { value: "9000" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(update).toHaveBeenCalledWith("usr_2", { token_quota: 9000 }, admin.session_token));

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(update).toHaveBeenCalledWith("usr_2", { token_quota: null }, admin.session_token));

    update.mockClear();
    fireEvent.change(input, { target: { value: "lots" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByText("Whole number, 0, or empty")).toBeInTheDocument();
    expect(update).not.toHaveBeenCalled();
  });

  it("signs out when an admin change is rejected as unauthenticated", async () => {
    setSession(admin);
    vi.spyOn(api, "me").mockResolvedValue(admin.user);
    vi.spyOn(api, "adminUsers").mockResolvedValue(listing);
    vi.spyOn(api, "adminUpdateUser").mockRejectedValue(new ApiError("session is invalid or has expired; sign in again", 401));
    renderAt("/admin");
    fireEvent.click(await screen.findByRole("button", { name: "Disable bob" }));
    await waitFor(() => expect(getSession()).toBeNull());
  });
});
