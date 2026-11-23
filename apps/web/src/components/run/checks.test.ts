import type { Halt } from "@qr-evals/contracts";
import { describe, expect, it } from "vitest";
import { flagKindsOfEvent, haltFromEvent, openHalts, phaseText, scopeText, tally, tallyLabel, withHalt } from "./checks";
import { HALTS, halt, runView } from "./checks-fixture";

describe("openHalts", () => {
  it("keeps the halts nobody has resumed", () => {
    const resumed = halt("generation_errors/arm", { resumed_at: "2026-11-22T12:20:00Z" });
    const open = halt("empty_outputs/card");
    expect(openHalts([resumed, open])).toEqual([open]);
    expect(openHalts([resumed])).toEqual([]);
    expect(openHalts(undefined)).toEqual([]);
    expect(openHalts(null)).toEqual([]);
  });
});

describe("haltFromEvent", () => {
  it("reads the halt out of a check.stopped payload, which carries the run id beside it", () => {
    for (const stored of Object.values(HALTS)) expect(haltFromEvent({ run_id: "run-1", ...stored })).toEqual(stored);
  });

  it("gives nothing for a payload that is not a halt", () => {
    expect(haltFromEvent(null)).toBeNull();
    expect(haltFromEvent("stopped")).toBeNull();
    expect(haltFromEvent({ run_id: "run-1", rule: "generation_errors" })).toBeNull();
    expect(haltFromEvent({ run_id: "run-1", ...halt("judge_phase_error/run"), rule: "a_rule_the_contract_does_not_have" })).toBeNull();
  });
});

describe("withHalt", () => {
  it("adds a halt once, however often the same event arrives", () => {
    const stop = halt("judge_unusable/streak");
    const once = withHalt(runView(), stop);
    expect(once.run.halts).toEqual([stop]);
    expect(withHalt(once, { ...stop })).toBe(once);
    const later = halt("judge_unusable/streak", { at: "2026-11-22T15:00:00Z" });
    expect(withHalt(once, later).run.halts).toEqual([stop, later]);
  });

  it("works on a run record from before halts existed", () => {
    const legacy = { ...runView().run };
    delete legacy.halts;
    expect(withHalt({ live: false, run: legacy }, halt("unfinished/judge")).run.halts).toHaveLength(1);
  });
});

describe("the words around a halt", () => {
  it("names where each kind of scope is", () => {
    expect(scopeText(halt("generation_errors/arm"))).toBe("Arm qrng");
    expect(scopeText(halt("scorer_errors/card"))).toBe("Eval psychobench");
    expect(scopeText(halt("unfinished/generation"))).toBe("The end of generation");
    expect(scopeText(halt("unfinished/judge"))).toBe("The end of judging");
    expect(scopeText(halt("judge_rejected/judge"))).toBe("The whole run");
  });

  it("has words for every scope and phase a fixture halt uses", () => {
    const scopes = new Set<Halt["scope"]>();
    const phases = new Set<Halt["phase"]>();
    for (const stored of Object.values(HALTS)) {
      scopes.add(stored.scope); phases.add(stored.phase);
      expect(scopeText(stored)).not.toMatch(/unknown|undefined|null/);
      expect(phaseText(stored)).toMatch(/^(before the first generation|during generation|during judging)$/);
    }
    // The fixture reaches every scope and phase the contract has, so no word above is untested.
    expect([...scopes].sort()).toEqual(["arm", "card", "phase", "run"]);
    expect([...phases].sort()).toEqual(["generation", "judge", "preflight"]);
  });

  it("orders the tally largest first and names it for what it holds", () => {
    expect(tally(halt("generation_errors/arm"))).toEqual([["HTTP 502", 6], ["ReadTimeout", 2]]);
    expect(tally(halt("empty_outputs/card"))).toEqual([]);
    expect(tallyLabel(halt("generation_errors/arm"))).toBe("Causes, as the harness names them");
    // At the end of a phase the harness tallies the missing samples by arm and eval, not by cause.
    expect(tally(halt("unfinished/generation"))).toEqual([["bent/psychobench", 40], ["qrng/eval_awareness", 9]]);
    expect(tallyLabel(halt("unfinished/generation"))).toBe("Missing, by arm and eval");
  });
});

describe("flagKindsOfEvent", () => {
  it("reads the flag kinds a finished sample carries", () => {
    expect(flagKindsOfEvent({ sample_ref: "a/b/c/0", flags: [{ kind: "empty_output", detail: "", count: null }, { kind: "truncated" }] })).toEqual(["empty_output", "truncated"]);
    expect(flagKindsOfEvent({ sample_ref: "a/b/c/0" })).toEqual([]);
    expect(flagKindsOfEvent({ flags: "empty_output" })).toEqual([]);
    expect(flagKindsOfEvent({ flags: [null, 3, { detail: "no kind" }] })).toEqual([]);
    expect(flagKindsOfEvent("text")).toEqual([]);
  });
});
