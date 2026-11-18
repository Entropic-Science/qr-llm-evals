import type { LaunchResponse, ReadinessReport, StudyConfig } from "@qr-evals/contracts";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api, ApiError } from "../../lib/api";
import { studyOneTemplate } from "../../lib/config-store";
import { clearSession, getSession, setSession } from "../../lib/session";
import { authResponse } from "../../lib/session-fixture";
import { LaunchDialog } from "./LaunchDialog";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

const config = studyOneTemplate() as StudyConfig;
const launched: LaunchResponse = { run_id: "run_1", study_id: "study_1", host_token: "host_" + "token", join_code: null, prereg_stale: false };

function open(handlers: { onClearKeys?: () => void; onLaunched?: (runId: string) => void } = {}) {
  return render(<LaunchDialog open config={config} providerKeys={{ anthropic: "sk-typed" }} onClose={() => undefined} onClearKeys={handlers.onClearKeys ?? (() => undefined)} onLaunched={handlers.onLaunched ?? (() => undefined)} />);
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() });
  clearSession();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("launch dialog", () => {
  it("launches with the session token and clears the provider keys", async () => {
    const session = setSession(authResponse());
    const readiness = vi.spyOn(api, "readiness").mockResolvedValue({} as ReadinessReport);
    const launch = vi.spyOn(api, "launch").mockResolvedValue(launched);
    const onClearKeys = vi.fn();
    const onLaunched = vi.fn();
    open({ onClearKeys, onLaunched });
    fireEvent.click(screen.getByRole("button", { name: "Check readiness & launch" }));
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith("run_1"));
    expect(readiness.mock.calls[0]?.[1]).toBe(session.token);
    expect(launch.mock.calls[0]?.[1]).toBe(session.token);
    expect(launch.mock.calls[0]?.[0].provider_keys).toEqual({ anthropic: "sk-typed" });
    expect(onClearKeys).toHaveBeenCalled();
  });

  it("refuses without a session and never calls the API", async () => {
    const readiness = vi.spyOn(api, "readiness");
    open();
    fireEvent.click(screen.getByRole("button", { name: "Check readiness & launch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has ended");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=/");
    expect(readiness).not.toHaveBeenCalled();
  });

  it("signs out and offers sign-in when the server rejects the session", async () => {
    setSession(authResponse());
    vi.spyOn(api, "readiness").mockRejectedValue(new ApiError("session is invalid or has expired; sign in again", 401));
    const launch = vi.spyOn(api, "launch");
    open();
    fireEvent.click(screen.getByRole("button", { name: "Check readiness & launch" }));
    expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument();
    expect(getSession()).toBeNull();
    expect(launch).not.toHaveBeenCalled();
  });

  it("shows the server's quota refusal and keeps the session", async () => {
    setSession(authResponse());
    vi.spyOn(api, "readiness").mockResolvedValue({} as ReadinessReport);
    vi.spyOn(api, "launch").mockRejectedValue(new ApiError("this account's token quota is used up (2,000,100 of 2,000,000 qr-server generation tokens); ask an admin to raise it", 403));
    const onLaunched = vi.fn();
    open({ onLaunched });
    fireEvent.click(screen.getByRole("button", { name: "Check readiness & launch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("token quota is used up");
    expect(getSession()).not.toBeNull();
    expect(onLaunched).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
  });
});
