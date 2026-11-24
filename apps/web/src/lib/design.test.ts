import { cardById, cards, tokenLimits, type EvalCard, type HypothesisPlan, type StudyConfig } from "@qr-evals/contracts";
import fixture from "../../../../packages/contracts/fixtures/design.json";
import {
  MAX_PERMUTATIONS, cardMargin, controlPermutations, draftHypotheses, eligible, familySizes, ineligibleReason, marginRefusal,
  metaFor, planContrasts, planPermutations, plannedPermutations, plannedTest, positiveControlShift, reachableVerdicts,
  recommendDesign, recommendK, sourceCells,
} from "./design";

const TOLERANCE = 1e-9;

interface Case {
  name: string;
  config: StudyConfig;
  recommend: unknown;
  recommend_k: Record<string, number>;
  hypotheses: HypothesisPlan[];
  families: unknown;
  permutations: number | null;
  reachable_verdicts: Record<string, Record<string, string | null>>;
}
const cases = fixture.cases as unknown as Case[];
const named = (name: string) => {
  const found = cases.find((item) => item.name === name);
  if (!found) throw new Error(`design.json has no case ${name}`);
  return found;
};
const card = (id: string): EvalCard => {
  const found = cardById(id);
  if (!found) throw new Error(`the registry has no card ${id}`);
  return found;
};

/** Deep equality in which two numbers may differ by the fixture's tolerance. */
function expectClose(actual: unknown, expected: unknown, path: string) {
  if (typeof expected === "number" && typeof actual === "number") {
    expect(Math.abs(actual - expected), `${path}: got ${actual}, the backend has ${expected}`).toBeLessThanOrEqual(TOLERANCE);
  } else if (Array.isArray(expected)) {
    expect(Array.isArray(actual), `${path} is a list`).toBe(true);
    expect((actual as unknown[]).length, `${path}.length`).toBe(expected.length);
    expected.forEach((item, index) => expectClose((actual as unknown[])[index], item, `${path}[${index}]`));
  } else if (expected !== null && typeof expected === "object") {
    expect(actual !== null && typeof actual === "object", `${path} is an object`).toBe(true);
    expect(Object.keys(actual as object).sort(), `${path} keys`).toEqual(Object.keys(expected).sort());
    for (const [name, item] of Object.entries(expected)) expectClose((actual as Record<string, unknown>)[name], item, `${path}.${name}`);
  } else {
    expect(actual, path).toBe(expected);
  }
}

describe("design: which endpoints can decide a study", () => {
  const refs = Object.keys(fixture.eligible).map((ref) => ref.split("/") as [string, string]);

  it("covers every headline endpoint of the registry", () => {
    const headline = cards.flatMap((item) => item.endpoints.primary.map((endpoint) => `${item.id}/${endpoint}`)).sort();
    expect(Object.keys(fixture.eligible).sort()).toEqual(headline);
  });

  it("agrees with the backend on each one, and on why not", () => {
    for (const [cardId, endpoint] of refs) {
      const ref = `${cardId}/${endpoint}`;
      expect(eligible(card(cardId), endpoint), ref).toBe((fixture.eligible as Record<string, boolean>)[ref]);
      expect(ineligibleReason(card(cardId), endpoint), ref).toBe((fixture.ineligible_reason as Record<string, string | null>)[ref]);
    }
    expect(refs.filter(([cardId, endpoint]) => eligible(card(cardId), endpoint)).length).toBeGreaterThan(0);
  });

  it("refuses an endpoint that is not a headline outcome, and a card that is not in the registry", () => {
    expect(ineligibleReason(card("tier0_pit_residuals"), "pit_ks_one_sample")).toBe("it is not the eval's headline outcome");
    expect(ineligibleReason(undefined, "anything")).toBe("the eval is not in the registry");
    expect(eligible(card("tier0_pit_residuals"), "pit_ks_one_sample")).toBe(false);
  });

  it("names tests as the built-in plan does", () => {
    expect(plannedTest(card("tier0_pit_residuals"), "pit_ks_two_sample")).toBe("ks_two_sample");
    expect(plannedTest(card("tier0_pit_residuals"), "pit_ks_one_sample")).toBe("ks_uniform");
    expect(plannedTest(card("self_referential_probe"), "experience_claim_rate")).toBe("paired_permutation");
    expect(plannedTest(card("source_guessing"), "guess_accuracy")).toBe("paired_binomial");
  });

  it("falls back to a cautious default for an endpoint without a registered row", () => {
    expect(metaFor("no_such_card", "no_such_endpoint")).toEqual({ label: "no_such_endpoint", scale: "statistic", level: "sample", range: null, executable: false });
  });
});

describe("design: margins", () => {
  it("takes a margin from the card only where the test has a paired difference", () => {
    expect(cardMargin(card("self_referential_probe"), "experience_claim_rate")).toBe(0.05);
    // The card states a SESOI, but a KS test on pooled tokens has nothing to apply it to.
    expect(card("tier0_pit_residuals").sesoi?.pit_ks_two_sample).toBeGreaterThan(0);
    expect(cardMargin(card("tier0_pit_residuals"), "pit_ks_two_sample")).toBeNull();
    expect(cardMargin(card("altered_state_meq"), "meq30_total")).toBeNull();
  });

  it("says of a KS outcome that it has no paired difference, although its card states a SESOI", () => {
    // The card's SESOI is not the outcome's margin: the hypothesis is registered without one.
    expect(marginRefusal(card("tier0_pit_residuals"), "pit_ks_two_sample", 0.01)).toBe("has no paired difference a margin could apply to");
    const refused = named("margin_refusals");
    const pit = refused.hypotheses.find((hypothesis) => hypothesis.endpoint === "pit_ks_two_sample");
    expect(pit).toMatchObject({ margin: null, margin_source: "none" });
  });

  it("refuses a margin below a thousandth of the range, which no number of prompts could confirm", () => {
    expect(marginRefusal(card("altered_state_meq"), "meq30_total", 1e-160)).toBe("cannot take a margin below 0.001 (0.1% of its range)");
    expect(marginRefusal(card("altered_state_meq"), "meq30_total", 0.0009)).toBe("cannot take a margin below 0.001 (0.1% of its range)");
    expect(marginRefusal(card("altered_state_meq"), "meq30_total", 0.001)).toBeNull();
    // The draft sets the refused margin aside instead of asking for an infinite number of prompts.
    const meq = draftHypotheses(named("margin_too_small").config, cards).find((hypothesis) => hypothesis.endpoint === "meq30_total");
    expect(meq).toMatchObject({ margin: null, margin_source: "none" });
    expect(Number.isFinite(meq?.sample_size.n_recommended)).toBe(true);
  });

  it("refuses a declared margin with the backend's reasons", () => {
    expect(marginRefusal(card("self_referential_probe"), "experience_claim_rate", 0.05)).toBe("takes its margin from the eval card");
    expect(marginRefusal(card("tier0_mmd_hamming"), "mmd_hamming", 0.05)).toBe("has no paired difference a margin could apply to");
    expect(marginRefusal(card("opinion_quantum_sampling"), "actual_main_effect_axis_score", 0.05)).toBe("has no fixed range to state a margin against");
    expect(marginRefusal(card("altered_state_meq"), "meq30_total", 0.11)).toBe("cannot take a margin above 0.1 (10% of its range)");
    expect(marginRefusal(card("altered_state_meq"), "meq30_total", 0.1)).toBeNull();
    expect(marginRefusal(card("altered_state_meq"), "meq30_total", 0.05)).toBeNull();
  });
});

describe("design: parity with cards/design.py on whole studies", () => {
  it("has the cases this file names", () => {
    expect(cases.map((item) => item.name)).toEqual(expect.arrayContaining([
      "study1_legacy", "study1_designed", "equivalence_claim", "control_switched_off", "two_cells", "below_floor",
      "too_many_tests", "draft_not_eligible", "draft_epsilon_and_seeds", "fallback_first_eligible", "nothing_eligible",
      "no_cell", "no_eval", "out_of_bounds", "no_outcome", "equivalence_needs_control", "margin_refusals", "margin_too_small",
      "control_below_floor", "seven_seeds", "alpha_0_01", "alpha_0_005", "epsilon_fallback", "fewer_repetitions",
      "five_seeds_alpha_0_005", "three_comparisons_alpha_0_005", "five_seeds_six_hypotheses_alpha_0_01",
      "four_seeds_five_hypotheses_alpha_0_005", "legacy_one_seed", "legacy_no_cell", "no_planned_data",
    ]));
    expect(MAX_PERMUTATIONS).toBe(fixture.max_permutations);
  });

  it.each(cases.map((item) => [item.name, item] as const))("%s: recommended design", (_name, item) => {
    expect(recommendDesign(item.config, cards)).toEqual(item.recommend);
  });

  it.each(cases.map((item) => [item.name, item] as const))("%s: recommended repetitions", (_name, item) => {
    expect(recommendK(item.config, cards)).toEqual(item.recommend_k);
  });

  it.each(cases.map((item) => [item.name, item] as const))("%s: hypotheses, families and permutations", (_name, item) => {
    const hypotheses = draftHypotheses(item.config, cards);
    expectClose(hypotheses, item.hypotheses, "hypotheses");
    if (item.hypotheses.length === 0) {
      expect(item.families).toBeNull();
      expect(item.permutations).toBeNull();
      return;
    }
    const families = familySizes(hypotheses);
    expect(families).toEqual(item.families);
    expect(planPermutations(families, item.config.design?.alpha ?? 0.05)).toBe(item.permutations);
  });

  it.each(cases.map((item) => [item.name, item] as const))("%s: reachable verdicts", (_name, item) => {
    const claim = item.config.design?.claim ?? "difference";
    const reachable = Object.fromEntries(draftHypotheses(item.config, cards).map((hypothesis) => [hypothesis.id, reachableVerdicts(hypothesis, claim)]));
    expect(reachable).toEqual(item.reachable_verdicts);
  });

  it("drafts nothing for a study without a design block", () => {
    expect(named("study1_legacy").config.design ?? null).toBeNull();
    expect(draftHypotheses(named("study1_legacy").config, cards)).toEqual([]);
  });

  it("recommends nothing when no selected eval can decide a study", () => {
    expect(recommendDesign(named("nothing_eligible").config, cards)).toBeNull();
  });
});

describe("design: what the mirror must hold to", () => {
  const designed = named("study1_designed").config;

  it("Study 1: the probe is sufficient, the two token-stream tests are not rated", () => {
    const byEndpoint = Object.fromEntries(draftHypotheses(designed, cards).map((hypothesis) => [hypothesis.endpoint, hypothesis]));
    // The template runs the probe at k = 24: at the eval's own 20 its 30 prompts would be marginal.
    expect(designed.cards.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.k).toBe(24);
    expect(byEndpoint.experience_claim_rate?.sample_size).toMatchObject({
      rating: "sufficient", n: 30, n_floor: 7, control_floor: 10, n_recommended: 30, reachable: true, k_recommended: 24,
    });
    expect(byEndpoint.pit_ks_two_sample?.sample_size).toMatchObject({ rating: "unknown", basis: "no_closed_form" });
    expect(byEndpoint.mmd_hamming?.sample_size).toMatchObject({ rating: "unknown", basis: "no_closed_form" });
    expect(byEndpoint.mmd_hamming?.sided).toBe("one_sided_upper");
  });

  it("reads an arm that leaves out the keys with defaults as the backend's dump would", () => {
    // The fixture configs are full dumps. A config typed in by hand, or built by the page, may omit these.
    const sparse: StudyConfig = {
      ...designed,
      arms: designed.arms.map(({ observer_mode, availability, ...arm }) => ({
        ...arm,
        ...(observer_mode === "supervised" ? { observer_mode } : {}),
        ...(availability === "unavailable" ? { availability } : {}),
      })),
    };
    expect(sparse.arms.some((arm) => arm.observer_mode === undefined && arm.availability === undefined)).toBe(true);
    expect(sourceCells(sparse)).toEqual(sourceCells(designed));
    expect(planContrasts(sparse)).toEqual(planContrasts(designed));
    expect(draftHypotheses(sparse, cards)).toEqual(draftHypotheses(designed, cards));
    expect(recommendDesign(sparse, cards)).toEqual(recommendDesign(designed, cards));
  });

  it("lists the plan's contrasts in the plan's order", () => {
    expect(planContrasts(designed).map(({ kind, a, b }) => [kind, a.join("+"), b.join("+")])).toEqual([
      ["source", "qrng_unsupervised", "prng_seed_1+prng_seed_7+prng_seed_42"],
      ["seed_vs_seed", "prng_seed_1", "prng_seed_7"],
      ["seed_vs_seed", "prng_seed_1", "prng_seed_42"],
      ["seed_vs_seed", "prng_seed_7", "prng_seed_42"],
      ["supervised", "qrng_supervised", "qrng_unsupervised"],
    ]);
  });

  it("drafts nothing while a selected eval is unknown or an override is out of bounds", () => {
    const withCards = (change: (selections: StudyConfig["cards"]) => StudyConfig["cards"]): StudyConfig => ({ ...designed, cards: change(structuredClone(designed.cards)) });
    expect(draftHypotheses(designed, cards).length).toBeGreaterThan(0);
    expect(draftHypotheses(withCards((selections) => [...selections, { card_id: "no_such_card" }]), cards)).toEqual([]);
    const probe = card("self_referential_probe");
    const override = (overrides: { k?: number; items?: number }) => withCards((selections) => selections.map((selection) => (selection.card_id === probe.id ? { ...selection, overrides } : selection)));
    expect(draftHypotheses(override({ items: probe.dataset.items + 1 }), cards)).toEqual([]);
    expect(draftHypotheses(override({ items: 0 }), cards)).toEqual([]);
    expect(draftHypotheses(override({ k: (probe.k_bounds?.[1] ?? probe.k ?? 1) + 1 }), cards)).toEqual([]);
    // A token limit outside the eval's bounds, one that is not whole, and one on an eval that generates nothing.
    const limits = tokenLimits[probe.id]!;
    const limit = (max_tokens: number) => withCards((selections) => selections.map((selection) => (selection.card_id === probe.id ? { ...selection, overrides: { ...selection.overrides, max_tokens } } : selection)));
    expect(draftHypotheses(limit(limits.min - 1), cards)).toEqual([]);
    expect(draftHypotheses(limit(limits.max + 1), cards)).toEqual([]);
    expect(draftHypotheses(limit(limits.min + 0.5), cards)).toEqual([]);
    expect(tokenLimits.target_hit_ranking).toBeUndefined();
    expect(designed.cards.some((selection) => selection.card_id === "target_hit_ranking")).toBe(true);
    expect(draftHypotheses(withCards((selections) => selections.map((selection) => (selection.card_id === "target_hit_ranking" ? { ...selection, overrides: { max_tokens: 500 } } : selection))), cards)).toEqual([]);
    // Within the bounds it changes nothing about what is planned.
    for (const value of [limits.min, limits.safe, limits.max]) expect(draftHypotheses(limit(value), cards)).toEqual(draftHypotheses(designed, cards));
    // In bounds, the override is what the hypothesis plans.
    const six = draftHypotheses(override({ items: 6 }), cards).find((hypothesis) => hypothesis.card_id === probe.id);
    expect(six?.sample_size).toMatchObject({ n: 6, n_floor: 7, rating: "insufficient" });
  });

  it("leaves out a confirmatory endpoint whose eval is not selected, and counts the family without it", () => {
    const without: StudyConfig = { ...designed, cards: designed.cards.filter((selection) => selection.card_id !== "self_referential_probe") };
    const hypotheses = draftHypotheses(without, cards);
    expect(hypotheses.map((hypothesis) => hypothesis.endpoint)).toEqual(["pit_ks_two_sample", "mmd_hamming"]);
    expect(familySizes(hypotheses)).toEqual({ difference: 2, equivalence: 0, negative_control: 6 });
  });

  it("caps nothing itself: the permutation count grows with the family and the level", () => {
    expect(plannedPermutations(3, 0.05)).toBe(10_000);
    expect(plannedPermutations(50, 0.01)).toBe(100_000);
    expect(plannedPermutations(30, 0.005)).toBe(120_000);
  });

  it("gives the seed-pair controls the permutations they need to fail, where the difference tests leave too few", () => {
    // Seven control seeds: 21 pairs for each of three outcomes. Sized on the three difference tests alone,
    // 12 000 permutations at α = 0.005 leave the smallest corrected p of a seed pair above the level, so no
    // pair could fail its control. The plan runs twice the seed-pair tests divided by α.
    const seven = named("seven_seeds");
    expect(seven.config.design?.alpha).toBe(0.005);
    const families = familySizes(draftHypotheses(seven.config, cards));
    expect(families).toEqual({ difference: 3, equivalence: 1, negative_control: 63 });
    expect(plannedPermutations(families.difference, 0.005)).toBe(12_000);
    expect(families.negative_control / (plannedPermutations(families.difference, 0.005) + 1)).toBeGreaterThan(0.005);
    expect(controlPermutations(63, 0.005)).toBe(25_200);
    expect(planPermutations(families, 0.005)).toBe(25_200);
    expect(planPermutations(families, 0.005)).toBeLessThanOrEqual(MAX_PERMUTATIONS);
    // The smallest p a seed pair can return, corrected over its family, is below half the level.
    expect(families.negative_control / (planPermutations(families, 0.005) + 1)).toBeLessThan(0.005 / 2);
    // At the milder levels: the floor of 10 000 covers the pairs at 0.05, and at 0.01 they decide again.
    expect(planPermutations(families, 0.05)).toBe(10_000);
    expect(planPermutations(families, 0.01)).toBe(12_600);
    // Exact where the division is a hair off in floating point.
    expect(controlPermutations(250, 0.005)).toBe(100_000);
    expect(controlPermutations(500, 0.01)).toBe(100_000);
  });

  it("does not let the seed-pair controls refuse a design the setup can build", () => {
    // Up to five control seeds the difference tests decide the count. Sized like a difference test, the seed
    // pairs asked for 108 000 to 120 000 permutations in each of these and the plan was refused.
    for (const [name, difference, pairs, permutations] of [
      ["five_seeds_alpha_0_005", 3, 30, 12_000],
      ["three_comparisons_alpha_0_005", 9, 27, 36_000],
      ["five_seeds_six_hypotheses_alpha_0_01", 6, 60, 12_000],
      ["four_seeds_five_hypotheses_alpha_0_005", 5, 30, 20_000],
    ] as const) {
      const item = named(name);
      const alpha = item.config.design?.alpha ?? 0.05;
      const families = familySizes(draftHypotheses(item.config, cards));
      expect([families.difference, families.negative_control], name).toEqual([difference, pairs]);
      expect(planPermutations(families, alpha), name).toBe(permutations);
      expect(planPermutations(families, alpha), name).toBe(plannedPermutations(difference, alpha));
      expect(pairs / (permutations + 1), name).toBeLessThan(alpha / 2);
    }
    // The template keeps the counts of its three difference tests at every level.
    const template = { difference: 3, equivalence: 1, negative_control: 9 };
    expect([0.05, 0.01, 0.005].map((alpha) => planPermutations(template, alpha))).toEqual([10_000, 10_000, 12_000]);
  });

  it("recommends the repetitions that make an eval's prompts enough, and nothing where its own k is", () => {
    // Whatever k the study set, the recommendation is the same: following it does not move it.
    const probe = "self_referential_probe";
    const withK = (k: number | null): StudyConfig => ({
      ...designed,
      cards: designed.cards.map((selection) => (selection.card_id === probe ? { ...selection, overrides: { k, items: null } } : selection)),
    });
    for (const k of [null, 10, 20, 24, 30]) expect(recommendK(withK(k), cards), `k = ${k}`).toEqual({ [probe]: 24 });
    const own = draftHypotheses(withK(null), cards).find((hypothesis) => hypothesis.card_id === probe);
    expect(own?.sample_size).toMatchObject({ rating: "marginal", n_recommended: 35, reachable: false, k_recommended: 24 });
    // A study without a design block is told what the recommended design would need.
    expect(recommendK(named("study1_legacy").config, cards)).toEqual({ [probe]: 24 });
    expect(recommendK(named("nothing_eligible").config, cards)).toEqual({});
  });

  it("marks every finding out of reach where the study has fewer prompts than the seed-against-seed control is asked for", () => {
    const eight = named("control_below_floor");
    const probe = draftHypotheses(eight.config, cards).find((hypothesis) => hypothesis.endpoint === "experience_claim_rate")!;
    expect(probe.sample_size).toMatchObject({ n: 8, n_floor: 7, control_floor: 10, rating: "insufficient" });
    expect(reachableVerdicts(probe, "difference")).toEqual({
      unavailable: null, invalid: null, effect: "control_cannot_fail", effect_within_margin: "control_cannot_fail",
      equivalent: "equivalence_not_claimed", inconclusive: "control_cannot_fail",
    });
  });

  it("states how far the biased arm must move an outcome to be detected", () => {
    // One seeded run of k = 24 against three: 0.5 * sqrt(1/24 + 1/72) = 0.117851. At the uncorrected level,
    // (1.959964 + 0.841621) / sqrt(30) = 0.511497 standard deviations. The product is 0.060280.
    const claim = named("equivalence_claim");
    const probe = draftHypotheses(claim.config, cards).find((hypothesis) => hypothesis.endpoint === "experience_claim_rate")!;
    expect(positiveControlShift(probe, 24, 3, 0.05)).toBeCloseTo(0.06028, 5);
    const pit = draftHypotheses(claim.config, cards).find((hypothesis) => hypothesis.endpoint === "pit_ks_two_sample")!;
    expect(positiveControlShift(pit, null, 3, 0.05)).toBeNull();
  });
});
