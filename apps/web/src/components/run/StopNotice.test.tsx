import type { Halt } from "@qr-evals/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { HALTS, RULES, halt } from "./checks-fixture";
import { StopNotice } from "./StopNotice";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

/** Every stop rule of the contract. A rule added to the catalogue fails to compile here until it is listed. */
const EVERY_RULE: Record<Halt["rule"], true> = {
  generation_errors: true, entropy_degraded: true, tier0_failed: true, scorer_errors: true, empty_outputs: true, nothing_parsed: true,
  unfinished: true, judge_rejected: true, judge_unusable: true, nothing_judged: true, judge_phase_error: true,
};

function notice(stop: Halt, props: Partial<Parameters<typeof StopNotice>[0]> = {}) {
  render(<StopNotice runId="run-1" halt={stop} state="paused" hostToken="host-token" onKeysSupplied={() => undefined} {...props} />);
  return screen.getByRole("alert", { name: "Stopped by a check" });
}

describe("StopNotice", () => {
  it("has a fixture halt for every stop rule the harness has", () => {
    expect(Object.keys(EVERY_RULE).sort()).toEqual([...RULES].sort());
    expect([...new Set(Object.values(HALTS).map((stop) => stop.rule))].sort()).toEqual([...RULES].sort());
  });

  it.each(Object.entries(HALTS))("shows what %s saw, its tally, its samples and what Resume recovers", (_key, stop) => {
    const section = notice(stop);
    expect(section).toHaveAttribute("data-rule", stop.rule);
    // The harness's own sentences, word for word.
    expect(within(section).getByText(stop.sentence)).toBeInTheDocument();
    expect(within(section).getByText(stop.recovery)).toBeInTheDocument();
    for (const [cause, count] of Object.entries(stop.causes ?? {})) {
      const item = within(section).getByText(cause).closest("li");
      expect(item).toHaveTextContent(`${cause} × ${count}`);
    }
    const refs = stop.sample_refs ?? [];
    expect(refs.length).toBeLessThanOrEqual(3);
    for (const ref of refs) expect(within(section).getByText(ref)).toBeInTheDocument();
    // A row is drawn only for what the halt holds.
    expect(within(section).queryByText(/Causes, as the harness names them|Missing, by arm and eval/) !== null).toBe(Object.keys(stop.causes ?? {}).length > 0);
    expect(within(section).queryByText("Samples the stop names") !== null).toBe(refs.length > 0);
    // Abort is terminal, and the notice says so for every rule.
    expect(section).toHaveTextContent("Abort ends the run for good. An aborted run cannot be resumed, and only a complete run can be analysed. What is stored so far is kept.");
  });

  it("says where and in which part of the run the check fired", () => {
    expect(notice(halt("tier0_failed/arm"))).toHaveTextContent("Arm prng7, during generation, on ");
    cleanup();
    expect(notice(halt("nothing_judged/card"))).toHaveTextContent("Eval eval_awareness, during judging, on ");
    cleanup();
    expect(notice(halt("judge_rejected/preflight"))).toHaveTextContent("The whole run, before the first generation, on ");
    cleanup();
    expect(notice(halt("unfinished/judge"))).toHaveTextContent("The end of judging, during judging, on ");
  });

  it("tells the tab that holds the host token that its buttons work, and any other view that it cannot resume", () => {
    const host = notice(halt("generation_errors/arm"));
    expect(host).toHaveTextContent("This browser tab holds the run's host token, so the Resume and Abort buttons below work here. The run stays paused until Resume is pressed.");
    cleanup();
    const viewer = notice(halt("generation_errors/arm"), { hostToken: undefined });
    expect(viewer).toHaveTextContent("Only a browser tab that holds the run's host token can resume or abort the run.");
    expect(viewer).toHaveTextContent("This view has no token, so it shows no controls.");
    expect(viewer).not.toHaveTextContent("buttons below work here");
  });

  it("promises no Resume on a run that was aborted while stopped", () => {
    const section = notice(halt("empty_outputs/card"), { state: "aborted" });
    expect(section).toHaveTextContent("The run was aborted after this stop and before it was resumed. It cannot be resumed. What was stored is kept.");
    expect(within(section).queryByText("What Resume recovers")).not.toBeInTheDocument();
    expect(within(section).queryByText("Who can resume")).not.toBeInTheDocument();
    expect(section).not.toHaveTextContent("Abort ends the run");
    expect(within(section).getByText(halt("empty_outputs/card").sentence)).toBeInTheDocument();
  });

  it("sends a run that lost its keys in a server restart to the key form first", () => {
    const host = notice(halt("judge_unusable/streak"), { state: "needs_keys" });
    expect(host).toHaveTextContent("The server restarted after this stop, so the run first needs its provider keys again.");
    expect(host).toHaveTextContent("The Resume button appears once a key is accepted.");
    // There is no Abort button in that state, so the notice does not speak of one.
    expect(host).not.toHaveTextContent("Abort ends the run");
    expect(within(host).getByText(halt("judge_unusable/streak").recovery)).toBeInTheDocument();
    cleanup();
    // The record's own flag counts as well as the state.
    const viewer = notice(halt("judge_unusable/streak"), { state: "paused", needsKeys: true, hostToken: undefined });
    expect(viewer).toHaveTextContent("Only a browser tab that holds the run's host token can supply them and resume.");
  });

  it("says that requests under way are finishing while the run is still running after the check fired", () => {
    const stop = halt("scorer_errors/card");
    const section = notice(stop, { state: "running" });
    expect(section).toHaveTextContent("The check has fired and the run takes no new work. Requests that were already under way are finishing. The run changes to paused when they are done, and it can be resumed from then.");
    expect(within(section).getByText(stop.recovery)).toBeInTheDocument();
    // There is no Resume button yet, so the notice does not point at one. Abort is there and is still final.
    expect(within(section).queryByText("Who can resume")).not.toBeInTheDocument();
    expect(section).not.toHaveTextContent("buttons below work here");
    expect(section).toHaveTextContent("Abort ends the run for good.");
    // The runner stores the halt first and changes the state afterwards, and a resumed run closes its halts as it starts.
    const runner = readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/runner/orchestrator.py"), "utf-8");
    expect(runner).toContain("the state changes to ``paused`` when the orchestrator returns.");
    expect(runner).toContain('self._set_state("running", **self._start_fields())');
    expect(runner).toContain('fields["halts"] = self.run_record.with_halts_resumed(self.clock()).halts');
  });

  it.each(["created", "ready", "analysing", "complete", undefined])("says only what the record says when the run is in a state a stopped run should not be in (%s)", (state) => {
    const section = notice(halt("scorer_errors/card"), { state });
    expect(section).toHaveTextContent(`The run is in the state ${state ?? "unknown"}. Its record still lists this stop as not resumed.`);
    expect(within(section).queryByText("What Resume recovers")).not.toBeInTheDocument();
    expect(within(section).queryByText("Who can resume")).not.toBeInTheDocument();
    expect(section).not.toHaveTextContent("Abort ends the run");
  });

  it("states who may control a run as the server enforces it", () => {
    // Control and keys are behind the host token and nothing else; the token is made once, at launch.
    const read = (path: string) => readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/api", path), "utf-8");
    const routes = read("routes/runs.py");
    expect(routes).toMatch(/async def control_run\(\s*run_id: str, body: RunControlRequest, live: HostRun, state: State\s*\)/);
    expect(routes).toMatch(/async def supply_keys\(run_id: str, body: RunKeysRequest, live: HostRun\)/);
    expect(routes.match(/new_host_token\(\)/g)).toHaveLength(1);
    expect(read("deps.py")).toContain('raise ApiError(403, "host token does not match this run")');
    // An aborted run cannot be resumed and only a complete run is analysed.
    expect(read("registry.py")).toContain('TERMINAL_STATES: frozenset[str] = frozenset({"complete", "aborted"})');
    expect(read("routes/analysis.py")).toContain('if run_record.state != "complete":');
  });

  describe("the key form", () => {
    it.each(["judge_rejected/preflight", "judge_rejected/judge"])("is offered to the host when the judge refused requests (%s)", (key) => {
      const section = notice(halt(key));
      expect(within(section).getByText("Supply a judge key")).toBeInTheDocument();
      expect(within(section).getByLabelText("Provider slot")).toHaveValue("anthropic");
      expect(within(section).getByLabelText("Provider key")).toHaveAttribute("type", "password");
      expect(section).toHaveTextContent("it stays in the server's memory for this run and is not written to disk");
    });

    it("hands the key to the run and clears it from the form", async () => {
      const supply = vi.spyOn(api, "supplyKeys").mockResolvedValue({ run_id: "run-1", keys_present: { anthropic: true }, state: "paused", needs_keys: false });
      const supplied = vi.fn();
      const section = notice(halt("judge_rejected/judge"), { onKeysSupplied: supplied });
      fireEvent.change(within(section).getByLabelText("Provider key"), { target: { value: "a-new-judge-key" } });
      fireEvent.click(within(section).getByRole("button", { name: "Supply key" }));
      await waitFor(() => expect(supplied).toHaveBeenCalledTimes(1));
      expect(supply).toHaveBeenCalledWith("run-1", { provider_keys: { anthropic: "a-new-judge-key" } }, "host-token");
      expect(within(section).getByLabelText("Provider key")).toHaveValue("");
      expect(within(section).getByRole("status")).toHaveTextContent("Key accepted and cleared from this form.");
    });

    it("is not shown to a viewer, on another rule, or beside the form of a run that needs all its keys", () => {
      expect(within(notice(halt("judge_rejected/judge"), { hostToken: undefined })).queryByLabelText("Provider key")).not.toBeInTheDocument();
      cleanup();
      for (const [key, stop] of Object.entries(HALTS)) {
        if (stop.rule === "judge_rejected") continue;
        expect(within(notice(stop)).queryByLabelText("Provider key"), key).not.toBeInTheDocument();
        cleanup();
      }
      expect(within(notice(halt("judge_rejected/judge"), { state: "needs_keys" })).queryByLabelText("Provider key")).not.toBeInTheDocument();
      cleanup();
      expect(within(notice(halt("judge_rejected/judge"), { state: "aborted" })).queryByLabelText("Provider key")).not.toBeInTheDocument();
    });
  });
});
