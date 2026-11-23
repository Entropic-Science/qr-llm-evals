import { cards, type StudyDesign } from "@qr-evals/contracts";
import fixture from "../../../../packages/contracts/fixtures/design.json";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "./config-store";
import { draftHypotheses } from "./design";
import { problems } from "./problems";
import { REC_STATES, rate, recFor, throughAmplifier, type Rec, type RecState } from "./recommendations";

const fixtureConfigs = (fixture.cases as unknown as { name: string; config: WizardStudyConfig }[]).map((item) => ({ name: item.name, config: structuredClone(item.config) }));
const named = (name: string) => fixtureConfigs.find((item) => item.name === name)!.config;

const withDesign = (config: WizardStudyConfig, patch: Partial<StudyDesign>): WizardStudyConfig => ({ ...config, design: { ...config.design!, ...patch } });
const withOverride = (config: WizardStudyConfig, cardId: string, overrides: { k?: number; items?: number }): WizardStudyConfig => ({
  ...config, cards: config.cards.map((selection) => (selection.card_id === cardId ? { ...selection, overrides: { ...selection.overrides, ...overrides } } : selection)),
});

function rec(config: WizardStudyConfig, path: string): Rec {
  const found = recFor(rate(config, cards), path);
  if (!found) throw new Error(`no rec for ${path}`);
  return found;
}
const stateOf = (config: WizardStudyConfig, path: string): RecState => rec(config, path).state;
const lines = ({ have, need, basis }: Rec) => [have, need, basis];

describe("REC_STATES", () => {
  it("is the state table: class, glyph, border and legend copy, in legend order", () => {
    expect(Object.entries(REC_STATES).map(([state, def]) => [state, def.className, def.glyph, def.border, def.legend])).toEqual([
      ["sufficient", "rec--sufficient", "✓", "solid", "Enough for the stated margin"],
      ["marginal", "rec--marginal", "≈", "solid", "Close, or no raw-unit margin"],
      ["insufficient", "rec--insufficient", "↓", "dashed", "Too few prompts"],
      ["unknown", "rec--unknown", "?", "dotted", "No closed-form power"],
      ["recommended", "rec--recommended", "=", "solid", "Recommended value"],
      ["custom", "rec--custom", "≠", "solid", "Your choice"],
      ["invalid", "rec--invalid", "✕", "solid", "Not runnable"],
      ["locked", "rec--locked", null, "hairline", "Set by the harness"],
    ]);
  });

  it("gives every state a mark of its own, so colour is never the only difference", () => {
    const marks = Object.values(REC_STATES).map((def) => `${def.glyph}`);
    expect(new Set(marks).size).toBe(marks.length);
  });
});

describe("rate: sample sizes", () => {
  const template = studyOneTemplate();

  it("rates Study 1's probe sufficient, with the numbers behind it", () => {
    const probe = rec(template, "cards.self_referential_probe.items");
    expect(probe).toMatchObject({ state: "sufficient", label: "Prompts", value: "30 prompts", recommended: "30", stepId: "design", nodeId: "card:self_referential_probe" });
    expect(lines(probe)).toEqual([
      "30 prompts detect ≥ 0.049 at 80% power.",
      "±0.05 needs ≥ 30.",
      "Paired sign-flip test, Holm over 3 tests (α′ = 0.0167), worst-case variance.",
    ]);
  });

  it("says of too few prompts for the control what the backend's problem says, not that the control cannot fail", () => {
    const probe = rec(named("control_below_floor"), "cards.self_referential_probe.items");
    expect(probe.state).toBe("insufficient");
    expect(probe.have).toBe("With 8 prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the outcome could only come out invalid.");
    expect(probe.need).toMatch(/^At least 10 are needed/);
    // The same clause as the problem the backend lists for this study.
    const message = (fixture.cases as unknown as { name: string; problems: { code: string; message: string }[] }[])
      .find((item) => item.name === "control_below_floor")!.problems.find((problem) => problem.code === "design.control_below_floor")!.message;
    expect(message).toContain(probe.have.slice("With 8 prompts ".length, -1));
  });

  it("never rates a KS or MMD outcome: PIT and MMD are unknown", () => {
    const pit = rec(template, "cards.tier0_pit_residuals.items");
    const mmd = rec(template, "cards.tier0_mmd_hamming.items");
    expect(pit.state).toBe("unknown");
    expect(mmd.state).toBe("unknown");
    // The template has no positive control, and one would not bear on these outcomes anyway: nothing is said of one.
    expect(lines(pit)).toEqual(["No closed-form power for a KS test on pooled tokens.", "Its sample size is not rated.", "This outcome has no margin, so it cannot return 'equivalent'."]);
    expect(rate(template, cards).some((item) => /positive control/i.test(`${item.need} ${item.basis}`) && item.state === "unknown" && item.path.startsWith("cards."))).toBe(false);
    expect(lines(mmd)[0]).toBe("No closed-form power for an MMD test on pooled tokens.");
    for (const { config } of fixtureConfigs) {
      for (const item of rate(config, cards)) {
        if (/^cards\.tier0_(pit_residuals|mmd_hamming)\.items$/.test(item.path)) expect(["unknown", "recommended", "custom"]).toContain(item.state);
      }
    }
  });

  it("is green only where a sample size was computed to be enough for a margin", () => {
    let green = 0;
    for (const { name, config } of [...fixtureConfigs, { name: "template", config: template }]) {
      const hypotheses = draftHypotheses(config, cards);
      for (const item of rate(config, cards)) {
        if (item.state !== "sufficient") continue;
        green += 1;
        const cardId = /^cards\.([^.]+)\.items$/.exec(item.path)?.[1];
        expect(cardId, `${name}: ${item.path}`).toBeDefined();
        const own = hypotheses.filter((hypothesis) => hypothesis.card_id === cardId);
        expect(own.length, name).toBeGreaterThan(0);
        for (const hypothesis of own) {
          expect(hypothesis.sample_size).toMatchObject({ rating: "sufficient", basis: "margin_worst_case" });
          expect(hypothesis.margin).not.toBeNull();
        }
      }
    }
    expect(green).toBeGreaterThan(0);
  });

  it("says how many prompts an outcome without a raw-unit margin would need, and that the eval is short of them", () => {
    const opinion = rec(named("control_switched_off"), "cards.opinion_quantum_sampling.items");
    expect(opinion.state).toBe("insufficient");
    expect(lines(opinion)).toEqual(["20 prompts detect ≥ 0.72 SD.", "0.30 SD needs ≥ 117; this eval has 20.", "No raw-unit margin for this outcome."]);
  });

  it("rates a card by its worst hypothesis, and says so when no effect could reach significance", () => {
    const few = rec(withOverride(template, "self_referential_probe", { items: 6 }), "cards.self_referential_probe.items");
    expect(few.state).toBe("insufficient");
    expect(few.have).toBe("6 prompts cannot reach significance at any effect size.");
    expect(few.need).toBe("At least 7 are needed.");
    // In the two-comparison study every card has two hypotheses; the card is as good as the worse one.
    const two = named("too_many_tests");
    const ratings = draftHypotheses(two, cards).filter((hypothesis) => hypothesis.card_id === "self_referential_probe").map((hypothesis) => hypothesis.sample_size.rating);
    expect(ratings.length).toBeGreaterThan(1);
    expect(stateOf(two, "cards.self_referential_probe.items")).toBe(ratings.includes("insufficient") ? "insufficient" : ratings.includes("marginal") ? "marginal" : "sufficient");
  });

  it("calls the prompts of an eval that decides nothing recommended at the card's value and custom below it", () => {
    expect(rec(template, "cards.novelty_bench.items")).toMatchObject({ state: "recommended", value: "100 prompts", stepId: "measure" });
    const fewer = rec(withOverride(template, "novelty_bench", { items: 40 }), "cards.novelty_bench.items");
    expect(fewer).toMatchObject({ state: "custom", value: "40 prompts", recommended: "100", need: "Uses the first 40 prompts in file order." });
    expect(stateOf(withOverride(template, "novelty_bench", { items: 101 }), "cards.novelty_bench.items")).toBe("invalid");
  });
});

describe("rate: repetitions", () => {
  const template = studyOneTemplate();

  it("is recommended at the card's k, custom inside its bounds and invalid outside", () => {
    const k = rec(template, "cards.self_referential_probe.k");
    // The template runs the probe at the recommended k = 24: at the eval's own 20 its 30 prompts are too few.
    expect(k).toMatchObject({ state: "recommended", value: "k = 24", recommended: "24", nodeId: "card:self_referential_probe" });
    // Fewer repetitions than recommended leave the eval's prompts short of enough. The chip names the k that is
    // enough and is not called the researcher's choice; how short is the rating of the outcome it follows.
    const fewer = withOverride(template, "self_referential_probe", { k: 20 });
    expect(rec(fewer, "cards.self_referential_probe.k")).toMatchObject({
      state: "marginal", value: "k = 20", recommended: "24",
      have: "Each prompt's value is the mean of 20 repetitions.",
      need: "With k = 24, the 30 prompts of this eval are enough for its margin.",
    });
    expect(stateOf(fewer, "cards.self_referential_probe.items")).toBe("marginal");
    expect(rec(fewer, "cards.self_referential_probe.items").need).toBe("±0.05 needs ≥ 35; this eval has 30. With k = 24, they are enough.");
    // An eval whose own k is enough keeps it as the recommended value.
    expect(rec(template, "cards.altered_state_meq.k")).toMatchObject({ state: "recommended", value: "k = 10", recommended: "10" });
    // The tooltip does not say that k adds no evidence: the rating divides the variance by it.
    expect(lines(k)).toEqual([
      "Each prompt's value is the mean of 24 repetitions.",
      "Fewer repetitions call for more prompts; more repetitions call for fewer.",
      "The unit of analysis is the prompt; the sample-size rating takes k into account.",
    ]);
    for (const item of rate(template, cards)) expect(`${item.have} ${item.need} ${item.basis}`).not.toMatch(/adds no evidence/);
    expect(stateOf(withOverride(template, "self_referential_probe", { k: 25 }), "cards.self_referential_probe.k")).toBe("custom");
    expect(stateOf(withOverride(template, "self_referential_probe", { k: 31 }), "cards.self_referential_probe.k")).toBe("invalid");
  });

  it("is rated by the outcome it follows: the repetitions, the prompts and the problem list never disagree", () => {
    // One source for all three: the rating of the worst outcome of the eval. Red beside yellow, or a red chip
    // over an empty problem list, would say two things about one setting.
    const second = named("two_cells");
    const studies = [
      template,
      ...[10, 16, 20, 22, 24, 27, 30].map((k) => withOverride(template, "self_referential_probe", { k })),
      // With a second comparison there are twice the tests, and the probe needs more repetitions than the template's 24.
      second,
      ...[10, 20, 24].map((k) => withOverride(second, "self_referential_probe", { k })),
    ];
    const seen = new Set<string>();
    for (const config of studies) {
      const k = rec(config, "cards.self_referential_probe.k");
      const prompts = rec(config, "cards.self_referential_probe.items");
      const listed = problems(config, cards).some((problem) => problem.code === "design.insufficient" && problem.nodeId === "card:self_referential_probe");
      seen.add(k.state);
      if (k.state === "insufficient" || k.state === "marginal") {
        expect(k.state).toBe(prompts.state);
        expect(k.need).toBe(`With k = ${k.recommended}, the 30 prompts of this eval are enough for its margin.`);
      }
      // Red means the list has the row; anything milder means it has not.
      expect(k.state === "insufficient").toBe(listed && Number(k.value.slice(4)) < Number(k.recommended));
      expect(prompts.state === "insufficient").toBe(listed);
    }
    // The cases above reach both rated states, and the two unrated ones.
    expect([...seen].sort()).toEqual(["custom", "insufficient", "marginal", "recommended"]);
    // The case the review found: two comparisons at k = 24 were a red k beside yellow prompts and no problem.
    const atTemplateK = withOverride(second, "self_referential_probe", { k: 24 });
    expect(stateOf(atTemplateK, "cards.self_referential_probe.k")).toBe("marginal");
    expect(stateOf(atTemplateK, "cards.self_referential_probe.items")).toBe("marginal");
  });

  it("is locked where the outcome is defined over a fixed k", () => {
    // KS-100 is a pass rate over exactly 100 repetitions; source guessing has one k in its bounds.
    expect(rec(template, "cards.unpredictabench.k")).toMatchObject({ state: "locked", need: "Not editable." });
    expect(stateOf(template, "cards.source_guessing.k")).toBe("locked");
    expect(stateOf(withOverride(template, "unpredictabench", { k: 50 }), "cards.unpredictabench.k")).toBe("invalid");
  });

  it("has no k for an eval that has none", () => {
    expect(recFor(rate(template, cards), "cards.tier0_pit_residuals.k")).toBeUndefined();
  });
});

describe("rate: control seeds", () => {
  const withSeeds = (keep: number[]): WizardStudyConfig => {
    const template = studyOneTemplate();
    return { ...template, arms: template.arms.filter((arm) => arm.source !== "prng_seeded" || keep.includes(arm.study_seed ?? -1)) };
  };

  it("is invalid with one, custom with two and recommended from three", () => {
    expect(rec(withSeeds([1]), "roster.seeds")).toMatchObject({ state: "invalid", value: "1 seed", stepId: "compare", nodeId: "deck:qr-llm/creative" });
    expect(rec(withSeeds([1, 7]), "roster.seeds")).toMatchObject({ state: "custom", value: "2 seeds" });
    expect(rec(withSeeds([1, 7, 42]), "roster.seeds")).toMatchObject({ state: "recommended", value: "3 seeds", recommended: "3" });
  });

  it("has one per comparison", () => {
    const seeds = rate(named("two_cells"), cards).filter((item) => item.path === "roster.seeds");
    expect(seeds.map((item) => item.nodeId).sort()).toEqual(["deck:qr-llm/creative", "deck:qr-llm/t1_minp"]);
  });
});

describe("rate: design", () => {
  const template = studyOneTemplate();
  const DESIGN_PATHS = ["design.claim", "design.confirmatory", "design.alpha", "design.sensitivity_epsilon"];

  it("is recommended throughout for the Study 1 template", () => {
    for (const path of DESIGN_PATHS) expect(stateOf(template, path), path).toBe("recommended");
    expect(rec(template, "design.alpha").value).toBe("α = 0.05");
    expect(rec(template, "design.claim").value).toBe("A difference only");
    expect(rec(template, "design.sensitivity_epsilon")).toMatchObject({ value: "None", nodeId: "gate:pos:qr-llm/creative" });
    expect(rec(template, "design.confirmatory").value).toBe("m = 3");
  });

  it("is custom where the value differs from the recommended one and nothing is wrong with it", () => {
    expect(rec(withDesign(template, { alpha: 0.01 }), "design.alpha")).toMatchObject({ state: "custom", value: "α = 0.01", recommended: "0.05" });
    const fewer = withDesign(template, { confirmatory: template.design!.confirmatory.slice(0, 2) });
    expect(stateOf(fewer, "design.confirmatory")).toBe("custom");
    expect(stateOf(fewer, "design.alpha")).toBe("recommended");
  });

  it("is invalid where an error names the setting, even at the recommended value", () => {
    const missing = withDesign(template, { sensitivity_epsilon: 0.5 });
    expect(stateOf(missing, "design.sensitivity_epsilon")).toBe("invalid");
    expect(stateOf(missing, "design.claim")).toBe("recommended");

    const unbacked = withDesign(template, { claim: "difference_or_equivalence" });
    expect(stateOf(unbacked, "design.claim")).toBe("invalid");
    // The positive control is "None", which is what is recommended, and still the claim cannot run with it.
    expect(stateOf(unbacked, "design.sensitivity_epsilon")).toBe("invalid");

    const many = named("too_many_tests");
    expect(stateOf(many, "design.alpha")).toBe("invalid");
    expect(stateOf(many, "design.confirmatory")).toBe("invalid");

    expect(stateOf(named("draft_not_eligible"), "design.confirmatory")).toBe("invalid");
  });

  it("locks a card's margin, marks a declared one as a choice and a missing one as unknown", () => {
    expect(rec(template, "design.margins.self_referential_probe/experience_claim_rate")).toMatchObject({
      state: "locked", value: "±0.05", nodeId: "endpoint:self_referential_probe/experience_claim_rate", basis: "Fixed now. Never derived from a pilot.",
    });
    expect(rec(template, "design.margins.tier0_pit_residuals/pit_ks_two_sample")).toMatchObject({ state: "unknown", value: "none" });
    const declared = named("equivalence_claim");
    expect(rec(declared, "design.margins.altered_state_meq/meq30_total")).toMatchObject({ state: "custom", value: "±0.05", need: "Between ±0.001 and ±0.1 (0.1% to 10% of its range)." });
    const refused = withDesign(template, { margins: [{ card_id: "self_referential_probe", endpoint: "experience_claim_rate", margin: 0.02 }] });
    expect(stateOf(refused, "design.margins.self_referential_probe/experience_claim_rate")).toBe("invalid");
  });

  it("locks what the harness fixes", () => {
    const all = rate(template, cards);
    const fixed = all.filter((item) => item.path.startsWith("fixed."));
    expect(fixed.every((item) => item.state === "locked" && item.need === "Not editable." && item.recommended === null)).toBe(true);
    expect(new Set(fixed.map((item) => item.path))).toEqual(new Set([
      "fixed.test.tier0_pit_residuals/pit_ks_two_sample", "fixed.test.tier0_mmd_hamming/mmd_hamming", "fixed.test.self_referential_probe/experience_claim_rate",
      "fixed.correction", "fixed.ci_level", "fixed.power", "fixed.permutations", "fixed.completeness", "fixed.envelope",
    ]));
    expect(lines(rec(template, "fixed.envelope"))).toEqual([
      "The same in every arm that samples through the amplifier; such arms differ only in the entropy source and the seed.",
      "Not editable.", "Amplifier zscore_mean, calibration 0, prefetch off.",
    ]);
    expect(rec(template, "fixed.power").value).toBe("80%");
    expect(rec(template, "fixed.ci_level").value).toBe("95%");
    expect(rec(template, "fixed.permutations").value).toBe("10000");
    expect(rec(withDesign(template, { alpha: 0.01 }), "fixed.ci_level").value).toBe("99%");
    expect(all.filter((item) => item.path === "fixed.envelope").map((item) => item.nodeId)).toEqual(template.arms.map((arm) => `arm:${arm.id}`));
    expect(rec(template, "roster.quantum")).toMatchObject({ state: "locked", value: "1 per comparison" });
  });

  it("prints the confidence level the plan registers at α = 0.005: 99.5%, not 100%", () => {
    const strict = withDesign(template, { alpha: 0.005 });
    expect(rec(strict, "fixed.ci_level").value).toBe("99.5%");
    expect(rec(strict, "design.alpha").basis).toBe("Holm over 3 tests; the confidence level is 99.5%.");
    for (const item of rate(strict, cards)) expect(`${item.value} ${item.have} ${item.need} ${item.basis}`).not.toContain("100%");
    // The whole percentages keep their text.
    expect(rec(template, "design.alpha").basis).toContain("95%");
    expect(rec(template, "fixed.completeness").value).toBe("≥ 90%");
  });

  it("sizes the permutations on the difference tests, and on the seed-pair controls where those need more", () => {
    const withSeeds = (extra: number[]) => withDesign({ ...template, seeds: [1, 7, 42, ...extra, 1001], arms: [...template.arms, ...extra.map((seed) => ({ id: `prng_seed_${seed}`, model: "qr-llm", sampling: "creative" as const, source: "prng_seeded" as const, study_seed: seed, deterministic: true }))] }, { alpha: 0.005 });
    const controls = (study: WizardStudyConfig) => draftHypotheses(study, cards).reduce((total, hypothesis) => total + hypothesis.negative_controls.length, 0);
    // Five control seeds: ten seed pairs for each of three outcomes. The three difference tests decide: 20 × 3 / 0.005.
    const five = withSeeds([2, 3]);
    expect(controls(five)).toBe(30);
    expect(rec(five, "fixed.permutations").value).toBe("12000");
    // Seven control seeds: 63 seed pairs, which 12 000 permutations could not fail. They decide: 2 × 63 / 0.005.
    const seven = withSeeds([2, 3, 4, 5]);
    expect(controls(seven)).toBe(63);
    expect(rec(seven, "fixed.permutations").value).toBe("25200");
    expect(rec(seven, "fixed.permutations").basis).toBe("The largest of three numbers: 10 000, 20 × the difference tests ÷ α, and 2 × the seed-pair controls ÷ α.");
  });

  it("says the sampling envelope is pinned only of arms that sample through the amplifier, and what is bent in a biased arm", () => {
    const provider = { id: "gpt", family: "external" as const, provider: "openai" as const, served_name: "gpt-x", checkpoint: null };
    const study: WizardStudyConfig = {
      ...template,
      models: [...template.models, provider],
      arms: [
        ...template.arms,
        { id: "provider_arm", model: "gpt", sampling: "creative", source: "provider" },
        { id: "greedy_arm", model: "qr-llm", sampling: "greedy", source: "prng_seeded", study_seed: 1 },
      ],
    };
    const enveloped = rate(study, cards).filter((item) => item.path === "fixed.envelope");
    expect(enveloped.map((item) => item.nodeId)).toEqual(template.arms.map((arm) => `arm:${arm.id}`));
    expect(study.arms.filter((arm) => !throughAmplifier(arm)).map((arm) => arm.id)).toEqual(["provider_arm", "greedy_arm"]);
    const biased = enveloped.find((item) => item.nodeId === "arm:prng_epsilon_010")!;
    expect(biased.have).toBe("The same amplifier as the other arms, with its reference mean lowered by ε standard errors on purpose.");
    // No arm is told that every arm is the same.
    for (const item of enveloped) expect(item.have).not.toBe("Same in every arm.");
  });
});

describe("rate: a study on the built-in plan", () => {
  const legacy = parseStudyConfig({ ...studyOneTemplate(), design: null })!;

  it("has no design settings and rates no sample size", () => {
    const all = rate(legacy, cards);
    expect(all.some((item) => item.path.startsWith("design.") || item.path.startsWith("fixed.test"))).toBe(false);
    const states = new Set(all.filter((item) => item.path.startsWith("cards.")).map((item) => item.state));
    for (const state of states) expect(["recommended", "custom", "locked"]).toContain(state);
    // The token-level evals have no prompts of their own and, without a design, nothing to rate.
    expect(recFor(all, "cards.tier0_pit_residuals.items")).toBeUndefined();
  });
});

describe("rate: every rec", () => {
  it("names a step, a node and a known state, with three tooltip lines", () => {
    for (const { name, config } of [...fixtureConfigs, { name: "template", config: studyOneTemplate() }]) {
      for (const item of rate(config, cards)) {
        expect(Object.keys(REC_STATES), `${name}: ${item.path}`).toContain(item.state);
        expect(["compare", "measure", "design", "instruments", "review"]).toContain(item.stepId);
        expect(item.nodeId, `${name}: ${item.path}`).toMatch(/^(study|family|gate:completeness|(card|endpoint|deck|gate:pos|arm|test):.+)$/);
        for (const line of lines(item)) expect(line.length, `${name}: ${item.path}`).toBeGreaterThan(0);
        expect(item.label.length).toBeGreaterThan(0);
        expect(item.value.length).toBeGreaterThan(0);
      }
    }
  });

  it("accepts the problem list the caller already has", () => {
    const config = studyOneTemplate();
    expect(rate(config, cards, [])).toEqual(rate(config, cards));
  });
});
