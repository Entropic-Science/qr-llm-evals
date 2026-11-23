import type { HealthSummary, RunView } from "@qr-evals/contracts";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HALTS, SUMMARIES, halt, runView } from "../components/run/checks-fixture";
import { clearHostToken, setHostToken } from "../lib/host-token";
import { eventSummary, Run } from "./Run";

const RUN_ID = "run-fixture";

/** Stands in for the browser's EventSource: the test fires the events the server would send. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  private listeners = new Map<string, ((event: unknown) => void)[]>();
  constructor(readonly url: string | URL) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: (event: unknown) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]); }
  close() { /* nothing to release */ }
  /** True when the page listens for this event type at all. */
  listensFor(type: string) { return (this.listeners.get(type) ?? []).length > 0; }
  emit(type: string, data: unknown) { for (const listener of this.listeners.get(type) ?? []) listener({ data: JSON.stringify(data), lastEventId: "" }); }
}

interface Served { view: RunView; health: HealthSummary | number; holdRunAfterFirst?: boolean }
let served: Served;
let controls: unknown[];

function mockApi() {
  let runReads = 0;
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/health")) return typeof served.health === "number" ? new Response(JSON.stringify({ error: { message: "run health is not available" } }), { status: served.health }) : new Response(JSON.stringify(served.health));
    if (url.endsWith("/control")) { controls.push(JSON.parse(String(init?.body))); return new Response(JSON.stringify({ run_id: RUN_ID, action: "resume", state: "running" })); }
    runReads += 1;
    // The record as it was before the stop is never served again, so only the event can bring the stop to the page.
    if (served.holdRunAfterFirst && runReads > 1) return new Promise<Response>(() => undefined);
    return new Response(JSON.stringify(served.view));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function renderRun() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Run params={{ runId: RUN_ID }} /></QueryClientProvider>);
}
const source = () => FakeEventSource.instances.at(-1)!;
const notices = () => screen.queryAllByRole("alert", { name: "Stopped by a check" });

beforeEach(() => {
  FakeEventSource.instances = [];
  controls = [];
  served = { view: runView({ state: "running" }, { live: true }), health: SUMMARIES.fresh };
  vi.stubGlobal("EventSource", FakeEventSource);
  mockApi();
});
afterEach(() => { cleanup(); clearHostToken(RUN_ID); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Run page: stopped by a check", () => {
  it("shows the notice of an open stop beside the run controls", async () => {
    setHostToken(RUN_ID, "host-token");
    const stop = halt("generation_errors/arm");
    served.view = runView({ state: "paused", halts: [stop] });
    served.health = SUMMARIES.stopped;
    renderRun();
    const notice = await screen.findByRole("alert", { name: "Stopped by a check" });
    expect(within(notice).getByText(stop.sentence)).toBeInTheDocument();
    expect(within(notice).getByText(stop.recovery)).toBeInTheDocument();
    const controlBar = screen.getByRole("region", { name: "Host run controls" });
    expect(within(controlBar).getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(within(controlBar).getByRole("button", { name: "Abort" })).toBeInTheDocument();
    // The notice comes before the controls it speaks of as "below".
    expect(notice.compareDocumentPosition(controlBar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(within(controlBar).getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(controls).toEqual([{ action: "resume" }]));
  });

  it("follows the open stop, not the run's state", async () => {
    // Paused for another reason, with a stop that was resumed long ago: no notice.
    served.view = runView({ state: "paused", halts: [halt("generation_errors/arm", { resumed_at: "2026-11-22T12:20:00Z" })] }, { owner_quota_exhausted: true });
    renderRun();
    expect(await screen.findByText("Token quota used up")).toBeInTheDocument();
    expect(notices()).toHaveLength(0);
    cleanup();
    // A paused run from before halts existed has no notice either.
    const legacy = { ...runView({ state: "paused" }).run };
    delete legacy.halts;
    delete legacy.checks_version;
    served.view = { live: false, run: legacy };
    renderRun();
    expect(await screen.findByText("paused")).toBeInTheDocument();
    expect(notices()).toHaveLength(0);
    cleanup();
    // An open stop on a run whose state is not paused is still shown, without a promise about Resume.
    served.view = runView({ state: "aborted", halts: [halt("empty_outputs/card")] });
    renderRun();
    const notice = await screen.findByRole("alert", { name: "Stopped by a check" });
    expect(notice).toHaveTextContent("The run was aborted after this stop and before it was resumed.");
    expect(within(notice).queryByText("What Resume recovers")).not.toBeInTheDocument();
  });

  it.each(Object.entries(HALTS))("shows a %s stop as soon as its event arrives, without reading the run again", async (_key, stop) => {
    served.holdRunAfterFirst = true;
    renderRun();
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(notices()).toHaveLength(0);
    expect(source().listensFor("check.stopped")).toBe(true);
    act(() => source().emit("check.stopped", { run_id: RUN_ID, ...stop }));
    const notice = await screen.findByRole("alert", { name: "Stopped by a check" });
    expect(notice).toHaveAttribute("data-rule", stop.rule);
    expect(within(notice).getByText(stop.sentence)).toBeInTheDocument();
    // The same event sent twice is one stop.
    act(() => source().emit("check.stopped", { run_id: RUN_ID, ...stop }));
    expect(notices()).toHaveLength(1);
    // Both events are in the activity list, in the harness's words.
    expect(screen.getAllByText(`stopped by a check · ${stop.sentence}`)).toHaveLength(2);
  });

  it("reads the stored record again after a stop and lets it decide", async () => {
    const fetchMock = mockApi();
    renderRun();
    expect(await screen.findByText("running")).toBeInTheDocument();
    const before = fetchMock.mock.calls.length;
    // The server has stored the stop and paused the run before it sends the event.
    served.view = runView({ state: "paused", halts: [halt("judge_unusable/streak")] });
    served.health = SUMMARIES.stopped;
    act(() => source().emit("check.stopped", { run_id: RUN_ID, ...halt("judge_unusable/streak") }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(before + 2));
    expect(await screen.findByText("paused")).toBeInTheDocument();
    expect(notices()).toHaveLength(1);
    // The health summary was read again too: it now lists the stops.
    expect(await screen.findByText(/A check stopped this run 2 times\./)).toBeInTheDocument();
    // After a resume the record has no open stop, and the notice goes with the next state event.
    served.view = runView({ state: "running", halts: [halt("judge_unusable/streak", { resumed_at: "2026-11-22T13:00:00Z" })] });
    act(() => source().emit("run.state", { run_id: RUN_ID, state: "running", at: "2026-11-22T13:00:00Z" }));
    await waitFor(() => expect(notices()).toHaveLength(0));
  });

  it("ignores a check.stopped event that does not carry a halt", async () => {
    served.holdRunAfterFirst = true;
    renderRun();
    expect(await screen.findByText("running")).toBeInTheDocument();
    act(() => source().emit("check.stopped", { run_id: RUN_ID, rule: "generation_errors" }));
    expect(notices()).toHaveLength(0);
  });

  it("offers the key form in the notice when the judge refused requests, to the host only", async () => {
    setHostToken(RUN_ID, "host-token");
    served.view = runView({ state: "paused", halts: [halt("judge_rejected/judge")] });
    renderRun();
    const notice = await screen.findByRole("alert", { name: "Stopped by a check" });
    expect(within(notice).getByText("Supply a judge key")).toBeInTheDocument();
    expect(screen.getAllByLabelText("Provider key")).toHaveLength(1);
    cleanup();
    clearHostToken(RUN_ID);
    renderRun();
    const viewed = await screen.findByRole("alert", { name: "Stopped by a check" });
    expect(screen.queryByLabelText("Provider key")).not.toBeInTheDocument();
    expect(viewed).toHaveTextContent("This view has no token, so it shows no controls.");
    expect(screen.queryByRole("region", { name: "Host run controls" })).not.toBeInTheDocument();
  });

  it("shows one key form, the run's own, when the server restarted on a stopped run", async () => {
    setHostToken(RUN_ID, "host-token");
    served.view = runView({ state: "needs_keys", needs_keys: true, halts: [halt("judge_rejected/judge")] });
    renderRun();
    const notice = await screen.findByRole("alert", { name: "Stopped by a check" });
    expect(notice).toHaveTextContent("The server restarted after this stop, so the run first needs its provider keys again.");
    expect(screen.getAllByLabelText("Provider key")).toHaveLength(1);
    expect(within(notice).queryByLabelText("Provider key")).not.toBeInTheDocument();
    expect(screen.getByText("Provider keys required")).toBeInTheDocument();
  });
});

describe("Run page: after a key is supplied", () => {
  it("reads the run and its health summary again, and then offers Resume", async () => {
    setHostToken(RUN_ID, "host-token");
    const fetchMock = mockApi();
    const reads = (ending: string) => fetchMock.mock.calls.filter(([url]) => String(url).endsWith(ending)).length;
    served.view = runView({ state: "needs_keys", needs_keys: true, halts: [halt("judge_rejected/judge")] });
    served.health = SUMMARIES.stopped;
    renderRun();
    expect(await screen.findByText("Provider keys required")).toBeInTheDocument();
    await waitFor(() => expect(reads("/health")).toBe(1));
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    // The server accepts the key and the run is paused again.
    served.view = runView({ state: "paused", halts: [halt("judge_rejected/judge")] });
    fireEvent.change(screen.getByLabelText("Provider key"), { target: { value: "a-made-up-test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Supply key" }));
    await waitFor(() => expect(reads("/keys")).toBe(1));
    // The summary names the run's state, so it is read again with the record.
    await waitFor(() => expect(reads("/health")).toBe(2));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText("Supply a judge key")).toBeInTheDocument();
    expect(screen.queryByText("Provider keys required")).not.toBeInTheDocument();
  });
});

describe("Run page: output health", () => {
  it("draws the table, the cautions and the stops from the run's health summary", async () => {
    served.view = runView({ state: "complete", halts: SUMMARIES.complete.halts });
    served.health = SUMMARIES.complete;
    renderRun();
    const section = await screen.findByRole("region", { name: "Output health" });
    await within(section).findByRole("table");
    expect(within(section).getAllByRole("row")).toHaveLength(SUMMARIES.complete.cells.length + 1);
    expect(within(within(section).getByRole("list", { name: "Cautions" })).getAllByRole("listitem").map((item) => item.textContent)).toEqual(SUMMARIES.complete.cautions.map((caution) => caution.text));
    expect(section).toHaveTextContent("A check stopped this run 2 times. The host resumed it each time.");
    // Both stops were resumed, so there is no notice.
    expect(notices()).toHaveLength(0);
  });

  it("says a run was made before result checks", async () => {
    served.view = runView({ state: "complete", checks_version: null });
    served.health = SUMMARIES.legacy;
    renderRun();
    const section = await screen.findByRole("region", { name: "Output health" });
    expect(await within(section).findByText("This run was made before result checks, so no flags were recorded for it.")).toBeInTheDocument();
    expect(section.querySelectorAll('td[data-label="Flagged"] b')).toHaveLength(0);
  });

  it("shows no counts when the run service has no summary to give", async () => {
    served.health = 404;
    renderRun();
    const section = await screen.findByRole("region", { name: "Output health" });
    expect(await within(section).findByText(/The health summary of this run could not be loaded, so no counts are shown\. run health is not available/)).toBeInTheDocument();
    expect(within(section).queryByRole("table")).not.toBeInTheDocument();
  });

  it("refuses a summary that does not match the contract", async () => {
    served.health = { ...SUMMARIES.complete, cells: "none" } as unknown as HealthSummary;
    renderRun();
    const section = await screen.findByRole("region", { name: "Output health" });
    expect(await within(section).findByText(/The run service answered with a health summary this page cannot read\./)).toBeInTheDocument();
    expect(within(section).queryByRole("table")).not.toBeInTheDocument();
  });

  it("names the flags of a finished sample in the activity list with the catalogue's labels", async () => {
    served.health = SUMMARIES.complete;
    renderRun();
    await within(await screen.findByRole("region", { name: "Output health" })).findByRole("table");
    act(() => source().emit("sample.finished", { sample_ref: "qrng/eval_awareness/p1/0", finish_reason: "length", tokens: 64, flags: [{ kind: "truncated", detail: "finish reason length", count: null }, { kind: "empty_output", detail: "", count: null }] }));
    expect(await screen.findByText("length · 64 tokens · flagged: ended at the token limit, empty reply")).toBeInTheDocument();
  });
});

describe("eventSummary", () => {
  it("adds the flags of a finished sample and leaves an unflagged one as it was", () => {
    expect(eventSummary({ type: "sample.finished", data: { finish_reason: "stop", tokens: 12 } })).toBe("stop · 12 tokens");
    expect(eventSummary({ type: "sample.finished", data: { finish_reason: "stop", tokens: 12, flags: [] } })).toBe("stop · 12 tokens");
    // Without the summary's legend the kind is printed as it is stored, with spaces.
    expect(eventSummary({ type: "sample.finished", data: { finish_reason: "stop", tokens: 0, flags: [{ kind: "empty_output" }] } })).toBe("stop · 0 tokens · flagged: empty output");
  });

  it("prints a stop in the harness's sentence", () => {
    const stop = halt("nothing_parsed/first");
    expect(eventSummary({ type: "check.stopped", data: { run_id: RUN_ID, ...stop } })).toBe(`stopped by a check · ${stop.sentence}`);
    expect(eventSummary({ type: "check.stopped", data: {} })).toBe("stopped by a check · see the notice above");
  });
});
