import type { Arm } from "@qr-evals/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { FIXED_CONFIRMATORY_SECONDARIES, FIXED_PRIMARIES, fixedPlanSentences, fixedPlanUse, observedArmInFixedPlan, type FixedPlanUse } from "./fixed-plan";

const backend = (path: string) => readFileSync(resolve(process.cwd(), "../../backend", path), "utf-8");

/** The module that builds the fixed Study 1 plan. Read, never run: the map only mirrors two of its tables and one rule. */
const planSource = backend("src/qr_llm_evals/cards/statistical_plan.py");

/** The (eval, outcome) keys of a table of that module, as "eval/outcome". */
function keysOf(table: string): string[] {
  const body = new RegExp(`^${table} = \\{\\n([\\s\\S]*?)^\\}`, "m").exec(planSource)?.[1] ?? "";
  return [...body.matchAll(/\("([a-z0-9_]+)", "([a-z0-9_]+)"\)/g)].map((match) => `${match[1]}/${match[2]}`);
}

interface FrozenEndpoint { card_id: string; endpoint: string; tier: number; role: string; correction: "holm" | "bh" | "none" }
interface FrozenPlan { endpoints: FrozenEndpoint[]; contrasts: { kind: string; a: string[]; b: string[] }[]; supervised_family: string; alpha: number; design?: unknown }

/** Plans without a design block as the backend froze them, with the correction of every outcome. */
const frozen = (bundle: string) => JSON.parse(backend(`tests/fixtures/${bundle}/prereg-plan.json`)) as FrozenPlan;
const USE_OF: Readonly<Record<FrozenEndpoint["correction"], FixedPlanUse>> = { holm: "primary", bh: "secondary", none: "ungated" };

describe("what the fixed Study 1 plan does with an outcome", () => {
  it("names the primary outcomes and the tested Tier 4 outcomes the backend names", () => {
    expect(keysOf("PRIMARY_TESTS")).toEqual([...FIXED_PRIMARIES]);
    expect(keysOf("CONFIRMATORY_SECONDARIES")).toEqual([...FIXED_CONFIRMATORY_SECONDARIES]);
    // the rule `fixedPlanUse` restates; if this line moves, the three uses have to be read again
    expect(planSource).toContain('"holm" if primary else "none" if card.tier == 4 and not confirmatory else "bh"');
  });

  it.each(["bundle_wide", "bundle_mini"])("agrees with every outcome of the frozen plan %s", (bundle) => {
    const plan = frozen(bundle);
    expect(plan.design ?? null).toBeNull();
    expect(plan.endpoints.length).toBeGreaterThan(2);
    for (const endpoint of plan.endpoints) {
      expect(fixedPlanUse(endpoint.card_id, endpoint.endpoint, endpoint.tier), `${endpoint.card_id}/${endpoint.endpoint}`).toBe(USE_OF[endpoint.correction]);
    }
  });

  it("is checked on all three uses, and on the level the sentences state", () => {
    const plan = frozen("bundle_wide");
    expect(new Set(plan.endpoints.map((endpoint) => endpoint.correction))).toEqual(new Set(["holm", "bh", "none"]));
    // a Tier 4 outcome the plan tests, and Tier 4 outcomes it does not
    expect(fixedPlanUse("source_guessing", "guess_accuracy", 4)).toBe("secondary");
    expect(fixedPlanUse("source_guessing", "d_prime", 4)).toBe("ungated");
    expect(fixedPlanUse("introspection_scales", "item_variance", 4)).toBe("ungated");
    expect(fixedPlanUse("creative_diversity", "vendi_embed", 1)).toBe("secondary");
    expect(plan.alpha).toBe(0.05);
  });

  it("says each use in a sentence, the tested outcomes first", () => {
    expect(fixedPlanSentences([{ label: "KS@100 pass rate", use: "primary" }], 1)).toEqual([
      "KS@100 pass rate is a primary outcome of the fixed Study 1 plan: it is tested with Holm correction at α = 0.05 across the primary outcomes of that plan.",
    ]);
    expect(fixedPlanSentences([
      { label: "d′", use: "ungated" }, { label: "Guess accuracy", use: "secondary" }, { label: "Accuracy by variant", use: "ungated" },
    ], 4)).toEqual([
      "Guess accuracy is a secondary outcome of the fixed Study 1 plan: it is tested with Benjamini–Hochberg correction at 0.05 across the secondary outcomes of tier 4.",
      "d′ and Accuracy by variant are reported as exploratory by the fixed Study 1 plan, with no significance threshold.",
    ]);
    // one sentence about all of them where the plan treats them alike
    expect(fixedPlanSentences([{ label: "A", use: "secondary" }, { label: "B", use: "secondary" }, { label: "C", use: "secondary" }], 1)).toEqual([
      "All 3 outcomes are secondary outcomes of the fixed Study 1 plan: they are tested with Benjamini–Hochberg correction at 0.05 across the secondary outcomes of tier 1.",
    ]);
    expect(fixedPlanSentences([{ label: "Item variance", use: "ungated" }], 4)).toEqual(["Item variance is reported as exploratory by the fixed Study 1 plan, with no significance threshold."]);
    expect(fixedPlanSentences([], 4)).toEqual([]);
  });

  it("promises a test only for an outcome the plan corrects", () => {
    for (const use of ["primary", "secondary", "ungated"] as const) {
      const [sentence] = fixedPlanSentences([{ label: "X", use }], 4);
      expect(/\btested\b/.test(sentence!), use).toBe(use !== "ungated");
    }
  });
});

describe("what the fixed Study 1 plan does with an observed quantum arm", () => {
  const arm = (id: string, source: Arm["source"], more: Partial<Arm> = {}): Arm => ({ id, model: "qr-llm", sampling: "creative", source, ...more } as Arm);
  const watched = arm("qrng_supervised", "qrng", { observer_mode: "supervised" });
  const unwatched = arm("qrng_unsupervised", "qrng");
  const seeded = [arm("prng_seed_1", "prng_seeded", { study_seed: 1 }), arm("prng_seed_7", "prng_seeded", { study_seed: 7 })];

  it("registers the contrast with the unobserved quantum arm, in a Holm family of its own", () => {
    // what the copy restates: the frozen plans hold the contrast and name its family
    for (const bundle of ["bundle_wide", "bundle_mini"]) {
      const plan = frozen(bundle);
      expect(plan.contrasts).toContainEqual(expect.objectContaining({ kind: "supervised", a: ["qrng_supervised"], b: ["qrng_unsupervised"] }));
      expect(plan.supervised_family).toBe("Separate Holm family across the same primary endpoints for A vs B");
    }
    expect(observedArmInFixedPlan(watched, [watched, unwatched, ...seeded])).toEqual({
      sub: "tested against unobserved quantum",
      story: "The fixed Study 1 plan tests it against the unobserved quantum arm on the primary outcomes of that plan, corrected with Holm in a family of their own.",
      design: "Compared with the unobserved quantum arm.",
    });
  });

  it("says what the plan does when the arm has no unobserved quantum arm beside it, or is switched off", () => {
    const off = { ...unwatched, availability: "unavailable" } as Arm;
    // `for a in baseline or qrng` in the plan: without an unobserved arm that runs, the observed one meets the seeded controls
    expect(observedArmInFixedPlan(watched, [watched, off, ...seeded])).toMatchObject({
      sub: "tested against the seeded controls",
      story: "This comparison has no unobserved quantum arm, so the fixed Study 1 plan tests this arm against the seeded controls in its place.",
    });
    expect(observedArmInFixedPlan(watched, [watched])).toMatchObject({ sub: "nothing to compare it with", design: "Compared with no arm." });
    // an observed seeded arm is not a control of the plan
    expect(observedArmInFixedPlan(watched, [watched, arm("prng_watched", "prng_seeded", { observer_mode: "supervised" })]).sub).toBe("nothing to compare it with");
    expect(observedArmInFixedPlan({ ...watched, availability: "unavailable" } as Arm, [watched, unwatched, ...seeded])).toMatchObject({
      sub: "switched off, not compared",
      story: "It is switched off, so the fixed Study 1 plan does not compare it with any arm.",
    });
  });

  it("never calls the contrast exploratory or speaks of a verdict: the fixed plan has neither", () => {
    const cases = [[watched, unwatched, ...seeded], [watched, ...seeded], [watched]];
    for (const cell of cases) {
      const said = Object.values(observedArmInFixedPlan(watched, cell)).join(" ");
      expect(said).not.toMatch(/exploratory|verdict/);
    }
  });
});
