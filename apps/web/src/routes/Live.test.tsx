import type { RunView, SessionView } from "@qr-evals/contracts";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { halt, runView } from "../components/run/checks-fixture";
import { clearHostToken, setHostToken } from "../lib/host-token";
import { Live } from "./Live";

const RUN_ID = "run-fixture";
const SESSION_KEY = `qr-evals.live-session.v1.${RUN_ID}`;
const session: SessionView = {
  session: { session_id: "ses-1", run_id: RUN_ID, index: 0, display: "single", target_s: 600, host_observer_id: "obs-host", target: null },
  state: "running",
  join_code: "JOIN42",
  questionnaires_hash: "q".repeat(64),
  target_pool_hash: "t".repeat(64),
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  private listeners = new Map<string, ((event: unknown) => void)[]>();
  constructor(readonly url: string | URL) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: (event: unknown) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]); }
  close() { /* nothing to release */ }
  emit(type: string, data: unknown) { for (const listener of this.listeners.get(type) ?? []) listener({ data: JSON.stringify(data), lastEventId: "" }); }
}

/** The page keeps its session in local storage, which this test environment does not provide. */
function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return { get length() { return items.size; }, clear: () => items.clear(), getItem: (key) => items.get(key) ?? null, key: (index) => [...items.keys()][index] ?? null, removeItem: (key) => { items.delete(key); }, setItem: (key, value) => { items.set(key, value); } };
}

let view: RunView;
function renderLive() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Live params={{ runId: RUN_ID }} /></QueryClientProvider>);
}
const line = () => screen.queryByRole("alert");

beforeEach(() => {
  FakeEventSource.instances = [];
  view = runView({ state: "running" }, { live: true });
  setHostToken(RUN_ID, "host-token");
  vi.stubGlobal("localStorage", memoryStorage());
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("/presence") ? new Response(JSON.stringify({ observers: [] })) : new Response(JSON.stringify(view))));
});
afterEach(() => { cleanup(); clearHostToken(RUN_ID); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Live page: a stop by a check", () => {
  it("says nothing while no check has stopped the run", async () => {
    renderLive();
    expect(await screen.findByRole("heading", { name: "Live observatory" })).toBeInTheDocument();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(line()).not.toBeInTheDocument();
  });

  it("shows one line with the stop's sentence and the way to the run page when the event arrives", async () => {
    renderLive();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const stop = halt("entropy_degraded/tainted");
    act(() => FakeEventSource.instances[0]!.emit("check.stopped", { run_id: RUN_ID, ...stop }));
    const notice = await screen.findByRole("alert");
    expect(within(notice).getByText("Stopped by a check")).toBeInTheDocument();
    expect(notice).toHaveTextContent(stop.sentence);
    expect(notice).toHaveTextContent("The run takes no new work: requests already under way finish, and the run then stays paused until it is resumed.");
    expect(within(notice).getByRole("link", { name: "Open the run record" })).toHaveAttribute("href", `/runs/${RUN_ID}`);
    // What Resume recovers is the run page's to say; the line does not repeat it.
    expect(notice).not.toHaveTextContent(stop.recovery);
    // A host resume is the only way back to running; the line goes when the run says it is running again.
    act(() => FakeEventSource.instances[0]!.emit("run.state", { run_id: RUN_ID, state: "running", at: "2026-11-22T13:00:00Z" }));
    await waitFor(() => expect(line()).not.toBeInTheDocument());
  });

  it("shows the line for a stop that is already in the run record when the page opens", async () => {
    const stop = halt("unfinished/generation");
    view = runView({ state: "paused", halts: [halt("generation_errors/arm", { resumed_at: "2026-11-22T12:20:00Z" }), stop] });
    renderLive();
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent(stop.sentence);
    expect(notice).not.toHaveTextContent(halt("generation_errors/arm").sentence);
  });

  it("says paused, with the way to resume, only while the run is paused", async () => {
    view = runView({ state: "paused", halts: [halt("unfinished/generation")] });
    renderLive();
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("The run is paused and stays paused until it is resumed. Open the run record to read what Resume recovers and to resume or abort the run.");
  });

  it("promises no Resume once the run was aborted, at load and when the abort arrives as an event", async () => {
    const stop = halt("unfinished/generation");
    view = runView({ state: "aborted", halts: [stop] });
    renderLive();
    const loaded = await screen.findByRole("alert");
    expect(loaded).toHaveTextContent(`${stop.sentence} The run was aborted after this stop and before it was resumed. It cannot be resumed. What was stored is kept. Open the run record to read what the stop saw.`);
    expect(loaded).not.toHaveTextContent(/stays paused|to resume|Resume recovers/);
    cleanup();
    FakeEventSource.instances = [];
    view = runView({ state: "paused", halts: [stop] });
    renderLive();
    expect(await screen.findByRole("alert")).toHaveTextContent("stays paused until it is resumed");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => FakeEventSource.instances[0]!.emit("run.state", { run_id: RUN_ID, state: "aborted", at: "2026-11-22T13:00:00Z" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("It cannot be resumed."));
    expect(screen.getByRole("alert")).not.toHaveTextContent(/stays paused|to resume|Resume recovers/);
  });

  it.each(["complete", "analysing"])("says only what the record says for a run in the state %s with an open stop", async (state) => {
    view = runView({ state, halts: [halt("unfinished/generation")] } as Parameters<typeof runView>[0]);
    renderLive();
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent(`The run is now in the state ${state}. Its record still lists this stop as not resumed.`);
    expect(notice).not.toHaveTextContent(/stays paused|to resume|Resume recovers/);
  });

  it("says the keys come first for a run that needs them again", async () => {
    view = runView({ state: "needs_keys", halts: [halt("unfinished/generation")] });
    renderLive();
    expect(await screen.findByRole("alert")).toHaveTextContent("The server restarted after this stop, so the run needs its provider keys again before it can be resumed. Open the run record to supply them and resume the run.");
  });

  it("shows no line for a run whose stops were all resumed, or for a payload that is not a halt", async () => {
    view = runView({ state: "running", halts: [halt("generation_errors/arm", { resumed_at: "2026-11-22T12:20:00Z" })] });
    renderLive();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await screen.findByRole("heading", { name: "Live observatory" });
    expect(line()).not.toBeInTheDocument();
    act(() => FakeEventSource.instances[0]!.emit("check.stopped", { run_id: RUN_ID, rule: "generation_errors" }));
    expect(line()).not.toBeInTheDocument();
  });
});
