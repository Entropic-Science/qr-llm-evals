import type { LaunchResponse, PreregRenderResponse, ReadinessReport, StudyConfig } from "@qr-evals/contracts";
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
  vi.spyOn(api, "prereg").mockResolvedValue({ prereg_hash: "a".repeat(64) } as PreregRenderResponse);
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

  it("sends the pre-registration hash and the chosen run size", async () => {
    setSession(authResponse());
    const report = { tier0_source: "engine_log" } as ReadinessReport;
    vi.spyOn(api, "readiness").mockResolvedValue(report);
    const prereg = vi.spyOn(api, "prereg").mockResolvedValue({ prereg_hash: "b".repeat(64) } as PreregRenderResponse);
    const launch = vi.spyOn(api, "launch").mockResolvedValue(launched);
    const onLaunched = vi.fn();
    open({ onLaunched });
    // the dialog starts on what the study file says; the host can change it
    expect(screen.getByRole("radio", { name: config.pilot?.enabled ? /Pilot/ : /Full run/ })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /Full run/ }));
    fireEvent.click(screen.getByRole("button", { name: "Check readiness & launch" }));
    await waitFor(() => expect(onLaunched).toHaveBeenCalled());
    // the hash is for this configuration with this readiness report
    expect(prereg).toHaveBeenCalledWith({ config, readiness: report });
    expect(launch.mock.calls[0]?.[0]).toMatchObject({ kind: "full", prereg_hash: "b".repeat(64), readiness: report });
  });

  it("starts on the kind the study's pilot setting names, and on a pilot for a study that says nothing about one", () => {
    const startsOn = (study: StudyConfig) => {
      const view = render(<LaunchDialog open config={study} providerKeys={{}} onClose={() => undefined} onClearKeys={() => undefined} onLaunched={() => undefined} />);
      const kind = (screen.getByRole("radio", { name: /^Pilot/ }) as HTMLInputElement).checked ? "pilot" : "full";
      expect((screen.getByRole("radio", { name: /^Full run/ }) as HTMLInputElement).checked).toBe(kind === "full");
      view.unmount();
      return kind;
    };
    expect(startsOn({ ...config, pilot: { enabled: true, prompts: 200 } })).toBe("pilot");
    expect(startsOn({ ...config, pilot: { enabled: false, prompts: 200 } })).toBe("full");
    // The server's default for a pilot that is not mentioned is "enabled", and the settings show it ticked.
    const silent: Partial<StudyConfig> = { ...config };
    delete silent.pilot;
    expect(startsOn(silent as StudyConfig)).toBe("pilot");
    expect(startsOn({ ...config, pilot: { prompts: 50 } })).toBe("pilot");
  });

  it("says what a pilot of a designed study is: the whole study, labelled non-confirmatory", () => {
    expect(config.design).toBeTruthy();
    open();
    expect(screen.getByRole("group", { name: "Kind of run" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "Pilot: runs the study as configured and is labelled non-confirmatory. It does not set margins." })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Full run: runs the study as configured. Only the first full run of a study can be confirmatory." })).toBeInTheDocument();
    // Nothing subsamples a pilot, so nothing here may say it is smaller.
    expect(document.body.textContent).not.toMatch(/a few prompts|subsample|base rates|smaller|size of this run/i);
  });

  it("promises no confirmatory run to a study on the fixed Study 1 plan", () => {
    render(<LaunchDialog open config={{ ...config, design: null }} providerKeys={{}} onClose={() => undefined} onClearKeys={() => undefined} onLaunched={() => undefined} />);
    expect(screen.getByRole("radio", { name: "Pilot: runs the study as configured. Its results are labelled as a pilot. It does not set margins." })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Full run: runs the study as configured." })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/confirmatory|a few prompts|base rates/i);
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
