import { cards, type Arm, type StudyDesign } from "@qr-evals/contracts";
import fixture from "../../../../packages/contracts/fixtures/design.json";
import studyOneJson from "../../../../packages/contracts/fixtures/study1.json";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "./config-store";
import { REGISTRY_CODES, armContradictions, hasErrors, loadRefusals, problems, problemsFor, type Problem, type StepId } from "./problems";
import { applyRoster, rosterFromArms, withEpsilon, type Roster } from "./roster";

interface Case { name: string; config: WizardStudyConfig; problems: { code: string; severity: string; message: string; step_id: string; node_id: string }[] }
const cases = fixture.cases as unknown as Case[];
const named = (name: string): WizardStudyConfig => {
  const found = cases.find((item) => item.name === name);
  if (!found) throw new Error(`design.json has no case ${name}`);
  return structuredClone(found.config);
};

const STEPS: readonly StepId[] = ["compare", "measure", "design", "instruments", "review"];
const PROBE = { card_id: "self_referential_probe", endpoint: "experience_claim_rate" };
const OPINION = { card_id: "opinion_quantum_sampling", endpoint: "actual_main_effect_axis_score" };

const HASH = "a".repeat(64);
/**
 * The template with a contact, its expectations, a pinned model checkpoint and pinned session assets, so that
 * the only problems left are the ones a test adds. The template itself ships without the pins; what the page
 * says about that is tested under "what the pre-registration will warn about".
 */
function study(change: (config: WizardStudyConfig) => WizardStudyConfig = (config) => config): WizardStudyConfig {
  const template = studyOneTemplate();
  return change({
    ...template,
    contact: "study@example.org",
    expectations: "A difference on the probe.",
    models: template.models.map((model) => ({ ...model, checkpoint: "org/qr-llm@0123abc" })),
    supervised: template.supervised ? { ...template.supervised, questionnaires_hash: HASH, target_pool_hash: HASH } : null,
  });
}
const designOf = (config: WizardStudyConfig): StudyDesign => {
  if (!config.design) throw new Error("the study has no design block");
  return config.design;
};
const withDesign = (patch: Partial<StudyDesign>) => (config: WizardStudyConfig): WizardStudyConfig => ({ ...config, design: { ...designOf(config), ...patch } });
const withArms = (change: (arms: Arm[]) => Arm[]) => (config: WizardStudyConfig): WizardStudyConfig => ({ ...config, arms: change(config.arms) });
const withRoster = (change: (roster: Roster) => Roster) => (config: WizardStudyConfig): WizardStudyConfig => {
  const roster = rosterFromArms(config.arms, config.seeds);
  if (!roster) throw new Error("the arms are not a roster");
  return applyRoster(config, change(roster));
};
const withItems = (cardId: string, items: number) => (config: WizardStudyConfig): WizardStudyConfig => ({
  ...config, cards: config.cards.map((selection) => (selection.card_id === cardId ? { ...selection, overrides: { ...selection.overrides, items } } : selection)),
});

function only(list: Problem[], code: string): Problem {
  const found = list.filter((problem) => problem.code === code);
  expect(found, `${code} in ${list.map((problem) => problem.code).join(", ") || "no problems"}`).toHaveLength(1);
  return found[0]!;
}
const find = (config: WizardStudyConfig, code: string) => only(problems(config, cards), code);
const codes = (config: WizardStudyConfig) => problems(config, cards).map((problem) => problem.code);

describe("problems, against the backend", () => {
  it.each(cases.map((item) => item.name))("reports what the backend reports for %s", (name) => {
    const expected = cases.find((item) => item.name === name)!.problems;
    const actual = problems(named(name), cards)
      .filter((problem) => REGISTRY_CODES.includes(problem.code))
      .map(({ code, severity, message, stepId, nodeId }) => ({ code, severity, message, step_id: stepId, node_id: nodeId }));
    expect(actual).toEqual(expected);
  });

  it("counts every code in the fixture among the ones it mirrors, so none is filtered out of the comparison", () => {
    const seen = new Set(cases.flatMap((item) => item.problems.map((problem) => problem.code)));
    expect(seen.size).toBeGreaterThan(10);
    for (const code of seen) expect(REGISTRY_CODES).toContain(code);
  });

  it("mirrors exactly the codes the backend reports, and each of them has a shared case", () => {
    // The list is the backend's own (`PROBLEM_CODES`), so a code added on one side fails here.
    expect(REGISTRY_CODES).toEqual(fixture.problem_codes);
    const seen = new Set(cases.flatMap((item) => item.problems.map((problem) => problem.code)));
    expect([...seen].sort()).toEqual([...REGISTRY_CODES].sort());
  });

  it("shares a case for every reason a margin is refused", () => {
    const messages = cases.flatMap((item) => item.problems).filter((problem) => problem.code === "design.margin_not_allowed").map((problem) => problem.message);
    for (const reason of [
      "has no paired difference a margin could apply to.", "takes its margin from the eval card.", "has no fixed range to state a margin against.",
      "cannot take a margin above 0.1 (10% of its range).", "cannot take a margin below 0.001 (0.1% of its range).",
    ]) {
      expect(messages.some((message) => message.endsWith(reason)), reason).toBe(true);
    }
  });
});

describe("problems, one case per code", () => {
  it("finds nothing wrong with a complete Study 1", () => {
    expect(problems(study(), cards)).toEqual([]);
  });

  it("finds one error in the Study 1 template: the placeholder contact", () => {
    const all = problems(studyOneTemplate(), cards);
    expect(all.filter((problem) => problem.severity === "error").map((problem) => problem.code)).toEqual(["review.contact_placeholder"]);
    expect(all.map((problem) => problem.code)).toEqual(["design.no_expectations", "review.contact_placeholder", "compare.checkpoint_unpinned", "instruments.assets_unpinned"]);
  });

  it("compare.no_cell", () => {
    const problem = find(study(withArms((arms) => arms.filter((arm) => arm.id !== "qrng_unsupervised"))), "compare.no_cell");
    expect(problem).toMatchObject({ severity: "error", message: "Tick at least one model and sampling preset.", stepId: "compare", nodeId: "study" });
  });

  it("compare.too_few_seeds", () => {
    const problem = find(study(withArms((arms) => arms.filter((arm) => arm.id !== "prng_seed_7" && arm.id !== "prng_seed_42"))), "compare.too_few_seeds");
    expect(problem).toMatchObject({ severity: "error", message: "Each comparison needs at least 2 control seeds.", stepId: "compare", nodeId: "deck:qr-llm/creative" });
  });

  it("compare.custom_arms", () => {
    const config = study(withArms((arms) => [...arms, { id: "by_hand", model: "qr-llm", sampling: "greedy", source: "prng_system" }]));
    expect(find(config, "compare.custom_arms")).toMatchObject({
      severity: "warning", message: "These arms were written by hand. Rebuild from roles to edit them here.", stepId: "compare",
    });
    expect(codes(study())).not.toContain("compare.custom_arms");
  });

  it("measure.no_eval", () => {
    const problem = find(study((config) => ({ ...config, cards: [], design: null })), "measure.no_eval");
    expect(problem).toMatchObject({ severity: "error", message: "Select at least one eval.", stepId: "measure", nodeId: "ghost:evals" });
  });

  it("measure.out_of_bounds, for k and for prompts", () => {
    const config = study((base) => ({
      ...base,
      cards: base.cards.map((selection) => (selection.card_id === "novelty_bench" ? { ...selection, overrides: { k: 99, items: 101 } } : selection)),
    }));
    // The message names the eval by its title, which the registry carries and the generated type leaves out.
    const title = (cards.find((card) => card.id === "novelty_bench") as { title?: string }).title;
    expect(title).toBeTruthy();
    const found = problems(config, cards).filter((problem) => problem.code === "measure.out_of_bounds");
    expect(found.map((problem) => problem.message)).toEqual([`${title}: k must be between 10 and 50.`, `${title}: prompts must be between 1 and 100.`]);
    expect(found[0]).toMatchObject({ severity: "error", stepId: "measure", nodeId: "card:novelty_bench" });
  });

  it("design.legacy_plan, with the way to a design", () => {
    const legacy = study((config) => ({ ...config, design: null }));
    const problem = find(legacy, "design.legacy_plan");
    expect(problem).toMatchObject({ severity: "warning", message: "This draft uses the fixed Study 1 plan.", stepId: "design", nodeId: "family" });
    expect(problem.fix?.label).toBe("Declare a design");
    const declared = problem.fix!.apply(legacy, cards);
    expect(declared.design).toEqual(study().design);
    // Declaring adds the design block and nothing else, so the study it was taken from comes back whole.
    expect(declared).toEqual(study());
    expect({ ...declared, design: null }).toEqual(legacy);
    // A legacy study is asked for nothing else about a design it does not have.
    expect(codes(legacy).filter((code) => code.startsWith("design."))).toEqual(["design.legacy_plan"]);
  });

  it("design.no_outcome", () => {
    const config = study(withDesign({ confirmatory: [] }));
    const problem = find(config, "design.no_outcome");
    expect(problem).toMatchObject({ severity: "error", message: "Choose at least one outcome that decides the study.", stepId: "design", nodeId: "family" });
    expect(codes(problem.fix!.apply(config, cards))).toEqual([]);
  });

  it("design.not_eligible, for an outcome that is not computed and for one whose eval was deselected", () => {
    const base = study();
    const computed = study(withDesign({ confirmatory: [...designOf(base).confirmatory, { card_id: "unpredictabench", endpoint: "ks100_pass_rate" }] }));
    expect(find(computed, "design.not_eligible")).toMatchObject({
      severity: "error",
      message: "KS-100 pass rate can no longer decide a study: it is scored per prompt group, which is not computed yet.",
      nodeId: "endpoint:unpredictabench/ks100_pass_rate",
    });
    const deselected = study((config) => ({ ...config, cards: config.cards.filter((selection) => selection.card_id !== PROBE.card_id) }));
    const problem = find(deselected, "design.not_eligible");
    expect(problem.message).toBe("Experience-claim rate can no longer decide a study: the eval is not selected.");
    // The fix takes that one outcome out and leaves the rest of the researcher's list alone.
    expect(problem.fix?.label).toBe("Remove this outcome");
    expect(codes(problem.fix!.apply(deselected, cards))).toEqual([]);
    const MEQ = { card_id: "altered_state_meq", endpoint: "meq30_total" };
    const own = study((config) => ({
      ...config,
      cards: config.cards.filter((selection) => selection.card_id !== PROBE.card_id),
      // The researcher's own list: MMD taken out, MEQ-30 put in, and a margin on it.
      design: { ...designOf(config), confirmatory: [{ card_id: "tier0_pit_residuals", endpoint: "pit_ks_two_sample" }, PROBE, MEQ], margins: [{ ...MEQ, margin: 0.05 }] },
    }));
    const fixed = find(own, "design.not_eligible").fix!.apply(own, cards);
    expect(fixed.design?.confirmatory).toEqual([{ card_id: "tier0_pit_residuals", endpoint: "pit_ks_two_sample" }, MEQ]);
    expect(fixed.design?.margins).toEqual([{ ...MEQ, margin: 0.05 }]);
  });

  it("design.below_floor, cleared by using the recommended prompts", () => {
    const config = study(withItems(PROBE.card_id, 6));
    const problem = find(config, "design.below_floor");
    expect(problem).toMatchObject({
      severity: "error",
      message: "Experience-claim rate: 6 prompts cannot reach significance at any effect size. At least 7 are needed.",
      stepId: "design",
      nodeId: "card:self_referential_probe",
    });
    expect(problem.fix?.label).toBe("Use recommended");
    expect(problems(problem.fix!.apply(config, cards), cards)).toEqual([]);
  });

  it("design.control_below_floor: enough prompts for the test, too few for the seed-against-seed control", () => {
    // Eight prompts reach significance in a family of three. They are fewer than the control of nine seed
    // pairs is asked for: below ten it is not certain that a pair could fail, so the analysis does not take it.
    const config = study(withItems(PROBE.card_id, 8));
    expect(codes(config)).not.toContain("design.below_floor");
    const problem = find(config, "design.control_below_floor");
    expect(problem).toMatchObject({
      severity: "error",
      message: "Experience-claim rate: with 8 prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the outcome could only come out invalid. At least 10 prompts are needed.",
      stepId: "design",
      nodeId: "card:self_referential_probe",
    });
    expect(problem.fix?.label).toBe("Use recommended");
    expect(problems(problem.fix!.apply(config, cards), cards)).toEqual([]);
    expect(codes(study(withItems(PROBE.card_id, 10)))).not.toContain("design.control_below_floor");
  });

  it("design.insufficient names the repetitions that would do where the eval has no more prompts", () => {
    const problem = only(problems(named("fewer_repetitions"), cards).filter((item) => item.message.includes("With k")), "design.insufficient");
    expect(problem.message).toBe("Experience-claim rate: 30 prompts detect ≥ 0.082; ±0.05 needs 81. With k = 27, the 30 prompts of the eval are enough.");
  });

  it("design.control_not_rated and design.study_absence_unreachable, only for a claim of absence", () => {
    const config = named("equivalence_claim");
    const found = problems(config, cards);
    expect(found.filter((problem) => problem.code === "design.control_not_rated").map((problem) => problem.message)).toEqual([
      "'Equivalent' on Experience-claim rate needs the biased arm to move it by at least 0.060. Whether a sampler bias of ε = 0.10 does that is not known before the run.",
      "'Equivalent' on MEQ-30 total needs the biased arm to move it by at least 0.072. Whether a sampler bias of ε = 0.10 does that is not known before the run.",
    ]);
    expect(only(found, "design.study_absence_unreachable")).toMatchObject({
      severity: "warning",
      message: "The study as a whole cannot conclude 'no effect within margins': H1, H2 cannot return 'equivalent'. An outcome that has a margin can still be reported as equivalent on its own.",
      nodeId: "family",
    });
    // A study that claims a difference only is told neither.
    expect(codes(study())).toEqual([]);
  });

  it("design.too_many_tests, cleared by the mildest level", () => {
    const config = named("too_many_tests");
    const problem = find(config, "design.too_many_tests");
    expect(problem).toMatchObject({
      severity: "error",
      message: "30 tests with 40 seed-pair controls at α = 0.005 need more permutations than the harness runs. Remove outcomes, comparisons or control seeds.",
      nodeId: "family",
    });
    const fixed = problem.fix!.apply(config, cards);
    expect(fixed.design?.alpha).toBe(0.05);
    expect(codes(fixed)).not.toContain("design.too_many_tests");
  });

  it("design.epsilon_missing", () => {
    const config = study(withDesign({ sensitivity_epsilon: 0.5 }));
    const problem = find(config, "design.epsilon_missing");
    expect(problem).toMatchObject({
      severity: "error", message: "No ε = 0.50 arm in qr-llm · creative. Add it in Compare, or choose 'None'.", nodeId: "gate:pos:qr-llm/creative",
    });
    // No other level runs, so the control goes; the button says so.
    expect(problem.fix?.label).toBe("Use no positive control");
    expect(problem.fix!.apply(config, cards).design?.sensitivity_epsilon).toBeNull();
    expect(codes(problem.fix!.apply(config, cards))).toEqual([]);
  });

  it("names what the fix of a positive-control problem does: another level, or no control and a claim of difference only", () => {
    const off = named("control_switched_off");
    const dropped = find(off, "design.epsilon_off");
    expect(off.design?.claim).toBe("difference_or_equivalence");
    expect(dropped.fix?.label).toBe("Drop the positive control and claim a difference only");
    expect(dropped.fix!.apply(off, cards).design).toMatchObject({ claim: "difference", sensitivity_epsilon: null });
    const other = study((base) => withDesign({ sensitivity_epsilon: 0.03 })(withRoster((roster) => withEpsilon(roster, 0.1, "on"))(base)));
    expect(find(other, "design.epsilon_off").fix?.label).toBe("Use ε = 0.10 instead");
    for (const config of [off, other]) for (const problem of problems(config, cards)) expect(problem.code.includes("epsilon") && problem.fix?.label === "Use recommended").toBe(false);
  });

  it("design.epsilon_off", () => {
    const config = study(withDesign({ sensitivity_epsilon: 0.03 }));
    expect(find(config, "design.epsilon_off")).toMatchObject({
      severity: "error", message: "The ε = 0.03 arm is switched off in qr-llm · creative.", nodeId: "gate:pos:qr-llm/creative",
    });
    // With another control switched on, that one is the recommended alternative.
    const other = study((base) => withDesign({ sensitivity_epsilon: 0.03 })(withRoster((roster) => withEpsilon(roster, 0.1, "on"))(base)));
    expect(find(other, "design.epsilon_off").fix!.apply(other, cards).design?.sensitivity_epsilon).toBe(0.1);
  });

  it("design.epsilon_seed_shared", () => {
    const config = study((base) => withDesign({ sensitivity_epsilon: 0.1 })(withArms((arms) => arms.map((arm) => (
      arm.source === "prng_epsilon" ? { ...arm, study_seed: 42, availability: "available", unavailable_reason: null } : arm
    )))(base)));
    expect(find(config, "design.epsilon_seed_shared")).toMatchObject({
      severity: "error", message: "The ε arm shares a seed with a control arm, which overstates sensitivity.", nodeId: "gate:pos:qr-llm/creative",
    });
  });

  it("design.equivalence_needs_control, cleared by the recommended claim", () => {
    const config = study(withDesign({ claim: "difference_or_equivalence" }));
    const problem = find(config, "design.equivalence_needs_control");
    expect(problem).toMatchObject({ severity: "error", message: "'Absence within a margin' needs a positive control.", nodeId: "study" });
    const fixed = problem.fix!.apply(config, cards);
    expect(fixed.design?.claim).toBe("difference");
    expect(codes(fixed)).toEqual([]);
  });

  it("design.margin_not_allowed, cleared by withdrawing the margin", () => {
    const config = study(withDesign({ margins: [{ ...PROBE, margin: 0.02 }] }));
    const problem = find(config, "design.margin_not_allowed");
    expect(problem).toMatchObject({
      severity: "error", message: "Experience-claim rate takes its margin from the eval card.", nodeId: "endpoint:self_referential_probe/experience_claim_rate",
    });
    const fixed = problem.fix!.apply(config, cards);
    expect(fixed.design?.margins).toEqual([]);
    expect(codes(fixed)).toEqual([]);
  });

  it("design.insufficient, with more prompts offered only while the eval has more", () => {
    const base = study();
    const config = study(withDesign({ confirmatory: [...designOf(base).confirmatory, OPINION] }));
    const problem = find(config, "design.insufficient");
    expect(problem.severity).toBe("warning");
    expect(problem.message).toMatch(/^Opinion axis score: 20 prompts detect ≥ \d\.\d\d SD; 0\.30 SD needs \d+\.$/);
    expect(problem.nodeId).toBe("card:opinion_quantum_sampling");
    // The eval has 20 prompts and the study uses all of them: there is nothing to recommend.
    expect(problem.fix).toBeUndefined();
    const fewer = withItems(OPINION.card_id, 10)(config);
    const offered = find(fewer, "design.insufficient").fix!;
    expect(offered.apply(fewer, cards).cards.find((selection) => selection.card_id === OPINION.card_id)?.overrides?.items ?? null).toBeNull();
  });

  it("design.insufficient offers the repetitions that make the eval's prompts enough, where it has no more prompts", () => {
    // Fewer repetitions than the template's on the probe: its 30 prompts are all it has.
    const halved = study((config) => ({ ...config, cards: config.cards.map((selection) => (selection.card_id === PROBE.card_id ? { ...selection, overrides: { ...selection.overrides, k: 10 } } : selection)) }));
    const problem = find(halved, "design.insufficient");
    expect(problem.message).toMatch(/needs \d+\. With k = 24, the 30 prompts of the eval are enough\.$/);
    expect(problem.fix?.label).toBe("Use k = 24");
    const fixed = problem.fix!.apply(halved, cards);
    expect(fixed.cards.find((selection) => selection.card_id === PROBE.card_id)?.overrides?.k).toBe(24);
    expect(problems(fixed, cards)).toEqual([]);
    // In every study the button sets the number it names: also with a second comparison, where more tests ask for more repetitions.
    const second = named("two_cells");
    const two = { ...second, cards: second.cards.map((selection) => (selection.card_id === PROBE.card_id ? { ...selection, overrides: { ...selection.overrides, k: 10 } } : selection)) };
    const rows = problems(two, cards).filter((item) => item.code === "design.insufficient" && item.fix?.label.startsWith("Use k = "));
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      const promised = Number(row.fix!.label.slice("Use k = ".length));
      expect(row.message).toContain(`With k = ${promised}, the 30 prompts of the eval are enough.`);
      expect(row.fix!.apply(two, cards).cards.find((selection) => selection.card_id === PROBE.card_id)?.overrides?.k).toBe(promised);
    }
  });

  it("design.margin_below_floor", () => {
    expect(find(named("equivalence_claim"), "design.margin_below_floor")).toMatchObject({
      severity: "warning",
      message: "±0.05 cannot be confirmed with 50 prompts; the smallest confirmable margin is ±0.084.",
      nodeId: "endpoint:altered_state_meq/meq30_total",
    });
  });

  it("design.equivalent_unreachable, with the recommended claim as the way out", () => {
    const config = named("control_switched_off");
    const problem = find(config, "design.equivalent_unreachable");
    expect(problem).toMatchObject({ severity: "warning", message: "No outcome can return 'equivalent' as configured.", nodeId: "family" });
    expect(codes(problem.fix!.apply(config, cards))).not.toContain("design.equivalent_unreachable");
  });

  it("design.no_expectations", () => {
    expect(find(study((config) => ({ ...config, expectations: "  " })), "design.no_expectations")).toMatchObject({
      severity: "warning", message: "No expectations recorded.", stepId: "design", nodeId: "study",
    });
  });

  it("instruments.judge, only while a selected eval needs the judge", () => {
    const blank = study((config) => ({ ...config, judge: { ...config.judge, model: " " } }));
    expect(find(blank, "instruments.judge")).toMatchObject({ severity: "error", message: "Choose a judge model.", stepId: "instruments", nodeId: "card:self_referential_probe" });
    const unjudged = { ...blank, design: null, cards: [{ card_id: "tier0_pit_residuals" }, { card_id: "tier0_mmd_hamming" }] };
    expect(codes(unjudged)).not.toContain("instruments.judge");
  });

  it("review.contact_placeholder", () => {
    for (const contact of ["REPLACE_WITH_STUDY_CONTACT", "  "]) {
      expect(find(study((config) => ({ ...config, contact })), "review.contact_placeholder")).toMatchObject({
        severity: "error", message: "Replace the placeholder contact.", stepId: "review", nodeId: "study",
      });
    }
  });

  it("review.server_rejected carries the server's own words", () => {
    const message = "30 confirmatory tests at alpha 0.005 need 120000 permutations each; the analysis runs at most 100000";
    expect(only(problems(study(), cards, { serverError: message }), "review.server_rejected")).toMatchObject({ severity: "error", message, stepId: "review" });
    expect(problems(study(), cards, { serverError: null })).toEqual([]);
  });

  it("draft.not_saved, only for a study the page can hold and the browser did not store", () => {
    expect(only(problems(study(), cards, { saved: false }), "draft.not_saved")).toMatchObject({
      severity: "warning", message: "This browser did not store the draft, so closing the page loses it. Save a study file to keep it.", stepId: "review",
    });
    expect(codes(study())).toEqual([]);
    expect(problems(study(), cards, { saved: true })).toEqual([]);
  });

  it("draft.not_saved, for a draft the browser does not store because a setting is outside what a study can hold", () => {
    // The page keeps such a draft on screen and the browser keeps the study as it was before: every later edit
    // is lost on a reload. That is said beside the problem that names the setting, with the setting named.
    const short = study((config) => ({ ...config, judge: { ...config.judge, max_tokens: 32 } }));
    const list = problems(short, cards, { saved: false });
    expect(list.map((problem) => problem.code)).toEqual(["draft.invalid", "draft.not_saved"]);
    expect(only(list, "draft.not_saved")).toMatchObject({
      severity: "warning", stepId: "instruments", path: "judge.max_tokens",
      message: "This browser does not store the draft while the setting judge.max_tokens is outside what a study can hold. Every edit made since then is lost when the page is closed or reloaded. Fix that setting and the draft is stored again. A study file saved in the meantime will not load back.",
    });
    // It does not send the reader to a study file, which would not load back, and it is not said of a draft that was stored.
    expect(only(list, "draft.not_saved").message).not.toMatch(/Save a study file/);
    expect(problems(short, cards, { saved: true }).map((problem) => problem.code)).toEqual(["draft.invalid"]);
    expect(problems(short, cards).map((problem) => problem.code)).toEqual(["draft.invalid"]);

    // Several settings are named together, on the step of the first, and once.
    const several = study((config) => ({ ...config, title: "", authors: [], judge: { ...config.judge, max_tokens: 32 }, pilot: { ...config.pilot, prompts: 0 } }));
    const said = only(problems(several, cards, { saved: false }), "draft.not_saved");
    expect(said.message).toMatch(/^This browser does not store the draft while the settings title, authors, judge\.max_tokens and 1 more are outside what a study can hold\. .* Fix those settings and the draft is stored again\./);
    expect(said).toMatchObject({ stepId: "review", path: "title" });

    // A setting another problem names in its own words stops the draft from being stored just the same: with
    // every eval unticked the page says "Select at least one eval." and that the draft is not being kept.
    const none = study((config) => ({ ...config, cards: [], design: null }));
    expect(parseStudyConfig(none)).toBeNull();
    const unkept = problems(none, cards, { saved: false });
    expect(unkept.map((problem) => problem.code)).toEqual(expect.arrayContaining(["measure.no_eval", "draft.not_saved"]));
    expect(unkept.map((problem) => problem.code)).not.toContain("draft.invalid");
    expect(only(unkept, "draft.not_saved")).toMatchObject({ stepId: "measure", path: "cards[].card_id" });
    expect(only(unkept, "draft.not_saved").message).toMatch(/while the setting cards is outside what a study can hold/);
  });
});

describe("draft.invalid: a setting outside what a study can hold", () => {
  const invalid = (change: (config: WizardStudyConfig) => WizardStudyConfig) => {
    const config = study(change);
    expect(parseStudyConfig(config)).toBeNull();
    return problems(config, cards).filter((problem) => problem.code === "draft.invalid");
  };

  it.each([
    ["an empty title", (config: WizardStudyConfig) => ({ ...config, title: "" }), "The setting title is empty.", "title", "review"],
    ["no authors", (config: WizardStudyConfig) => ({ ...config, authors: [] }), "The setting authors is empty: it needs at least one entry.", "authors", "review"],
    ["a judge answer that is too short", (config: WizardStudyConfig) => ({ ...config, judge: { ...config.judge, max_tokens: 32 } }), "The setting judge.max_tokens must be at least 64.", "judge.max_tokens", "instruments"],
    ["a judge answer that is too long", (config: WizardStudyConfig) => ({ ...config, judge: { ...config.judge, max_tokens: 20000 } }), "The setting judge.max_tokens must be at most 16000.", "judge.max_tokens", "instruments"],
    ["an intention timer of zero", (config: WizardStudyConfig) => ({ ...config, supervised: { ...config.supervised!, intention_timer_min: 0 } }), "The setting supervised.intention_timer_min must be at least 1.", "supervised.intention_timer_min", "instruments"],
    ["no pilot prompts", (config: WizardStudyConfig) => ({ ...config, pilot: { prompts: 0 } }), "The setting pilot.prompts must be at least 1.", "pilot.prompts", "review"],
    ["no tiled streams", (config: WizardStudyConfig) => ({ ...config, estimates: { tiled_streams: 0 } }), "The setting estimates.tiled_streams must be at least 1.", "estimates.tiled_streams", "review"],
    ["an arm id with a capital", withArms((arms) => arms.map((arm, index) => (index === 3 ? { ...arm, id: "PRNG_7" } : arm))), "The setting arms[3].id may hold only lowercase letters, digits and underscores.", "arms[].id", "compare"],
    ["an empty seed list", (config: WizardStudyConfig) => ({ ...config, seeds: [] }), "The setting seeds is empty: it needs at least one entry.", "seeds", "compare"],
    ["a model added by hand and not filled in", (config: WizardStudyConfig) => ({ ...config, models: [...config.models, { id: "", family: "external" as const, provider: "custom" as const, served_name: "x", checkpoint: "c" }] }), "The setting models[1].id is empty.", "models[].id", "compare"],
    ["a false-finding rate off the grid", withDesign({ alpha: 0.2 as never }), "The setting design.alpha is not one of the values it can take.", "design.alpha", "design"],
  ] as const)("names %s as an error, with the setting it is about", (_what, change, message, path, stepId) => {
    const found = invalid(change);
    expect(found.map((problem) => problem.message)).toContain(message);
    const problem = found.find((item) => item.message === message)!;
    expect(problem).toMatchObject({ severity: "error", path, stepId, nodeId: "study" });
  });

  it("blocks the launch, which the server would refuse anyway", () => {
    expect(hasErrors(problems(study((config) => ({ ...config, judge: { ...config.judge, max_tokens: 32 } })), cards))).toBe(true);
  });

  it("names a seed too large for a browser to keep exactly, on the arm that carries it", () => {
    const found = invalid(withArms((arms) => arms.map((arm) => (arm.id === "prng_seed_7" ? { ...arm, study_seed: 2 ** 53 } : arm))));
    expect(found.map((problem) => problem.message)).toContain("The setting arms[3].study_seed is larger than 9007199254740991, the largest whole number this page can keep exactly.");
  });

  it("does not say twice what a problem of its own already says", () => {
    const noArms = study((config) => ({ ...config, arms: [] }));
    expect(codes(noArms).filter((code) => code === "draft.invalid")).toEqual([]);
    expect(codes(noArms)).toContain("compare.no_cell");
    const noEvals = study((config) => ({ ...config, cards: [] }));
    expect(problems(noEvals, cards).filter((problem) => problem.code === "draft.invalid")).toEqual([]);
    const noContact = study((config) => ({ ...config, contact: "" }));
    expect(codes(noContact)).toEqual(["review.contact_placeholder"]);
  });

  it("names a key the page does not know, and stops naming settings after eight", () => {
    const unknown = study((config) => ({ ...config, surprise: 1 } as WizardStudyConfig));
    expect(problems(unknown, cards).map((problem) => problem.message)).toEqual(["The study holds 1 key this page does not know: surprise."]);
    const many = invalid(withArms((arms) => [...arms, ...Array.from({ length: 10 }, (_, index) => ({ ...arms[2]!, id: `Arm ${index}`, study_seed: 1 }))]));
    expect(many).toHaveLength(9);
    expect(many.at(-1)!.message).toBe("2 more settings cannot be kept as they are. They are named here once the ones above are fixed.");
  });
});

describe("what the pre-registration will warn about", () => {
  it("says of the template that its model has no checkpoint and its session assets no hashes, as warnings", () => {
    const all = problems({ ...studyOneTemplate(), contact: "study@example.org", expectations: "x" }, cards);
    expect(all.map((problem) => [problem.code, problem.severity, problem.stepId, problem.path])).toEqual([
      ["compare.checkpoint_unpinned", "warning", "compare", "models[].checkpoint"],
      ["instruments.assets_unpinned", "warning", "instruments", "supervised.questionnaires_hash"],
    ]);
    expect(all[0]!.message).toBe("The model qr-llm has no checkpoint pinned, so the pre-registration will open with a validity warning that its exact revision is not frozen.");
    expect(all[1]!.message).toContain("This page cannot fill the hashes in");
    expect(hasErrors(all)).toBe(false);
    // Without the server's catalogue there is nothing to pin the checkpoint to, so no fix is offered.
    expect(all[0]!.fix).toBeUndefined();
  });

  it("offers the checkpoint the server has pinned for that model, and the warning goes once it is taken", () => {
    const config = study((base) => ({ ...base, models: base.models.map((model) => ({ ...model, checkpoint: null })) }));
    const modelOptions = [{ ...config.models[0]!, checkpoint: "rdtand/Qwen3.8-27B@5.5bit" }, { id: "other", family: "external" as const, provider: "openai" as const, served_name: "other", checkpoint: "x" }];
    const problem = only(problems(config, cards, { modelOptions }), "compare.checkpoint_unpinned");
    expect(problem.fix?.label).toBe("Pin the server's checkpoint");
    const pinned = problem.fix!.apply(config, cards);
    expect(pinned.models[0]!.checkpoint).toBe("rdtand/Qwen3.8-27B@5.5bit");
    expect(problems(pinned, cards, { modelOptions })).toEqual([]);
    // A catalogue that has no checkpoint for the model offers nothing.
    expect(only(problems(config, cards, { modelOptions: [{ ...config.models[0]!, checkpoint: null }] }), "compare.checkpoint_unpinned").fix).toBeUndefined();
  });

  it("says nothing about session assets of a study without supervised sessions, and warns of a judge temperature", () => {
    const unobserved = study((config) => ({ ...config, supervised: null, arms: config.arms.filter((arm) => arm.observer_mode !== "supervised") }));
    expect(codes(unobserved)).toEqual([]);
    const half = study((config) => ({ ...config, supervised: { ...config.supervised!, target_pool_hash: null } }));
    expect(codes(half)).toEqual(["instruments.assets_unpinned"]);
    const warm = study((config) => ({ ...config, judge: { ...config.judge, temperature: 0.7 } }));
    expect(only(problems(warm, cards), "instruments.judge_temperature")).toMatchObject({ severity: "warning", path: "judge.temperature" });
    expect(codes(study((config) => ({ ...config, judge: { ...config.judge, temperature: 0 } })))).toEqual([]);
  });
});

describe("the cross-references the server checks", () => {
  it("names an arm on a model the study does not list, and one on a seed it does not list", () => {
    const renamed = study((config) => ({ ...config, models: config.models.map((model) => ({ ...model, id: "qr-llm-v2" })) }));
    const unknown = problems(renamed, cards).filter((problem) => problem.code === "compare.unknown_model");
    expect(unknown).toHaveLength(renamed.arms.length);
    expect(unknown[0]).toMatchObject({ severity: "error", stepId: "compare", path: "arms[].model", nodeId: "arm:qrng_supervised", message: "Arm qrng_supervised names the model qr-llm, which is not in the study's model list." });

    // A study that names no seed list runs on the default one (1, 7, 42), as the server reads it. Study 1
    // without its `seeds` key is therefore the same study to the page: nothing new is said and nothing blocks.
    const withSeeds = parseStudyConfig(studyOneJson)!;
    const unnamed = parseStudyConfig(Object.fromEntries(Object.entries(studyOneJson).filter(([key]) => key !== "seeds")))!;
    expect(unnamed.seeds).toBeUndefined();
    const said = (config: WizardStudyConfig) => problems(config, cards).map((problem) => `${problem.severity} ${problem.code}: ${problem.message}`);
    expect(said(unnamed)).toEqual(said(withSeeds));
    expect(said(unnamed).join(" ")).not.toContain("compare.seed_not_listed");
    expect(hasErrors(problems(unnamed, cards))).toBe(hasErrors(problems(withSeeds, cards)));
    // An arm on a seed outside the default list is still named, and its fix adds the seed to that list.
    const stray: WizardStudyConfig = { ...unnamed, arms: unnamed.arms.map((arm) => (arm.id === "prng_seed_42" ? { ...arm, study_seed: 43 } : arm)) };
    const outside = find(stray, "compare.seed_not_listed");
    expect(outside.message).toBe("Arm prng_seed_42 runs on seed 43, which is not in the study's seed list.");
    expect(outside.fix!.apply(stray, cards).seeds).toEqual([1, 7, 42, 43]);

    const unlisted = study((config) => ({ ...config, seeds: (config.seeds ?? []).filter((seed) => seed !== 42) }));
    const problem = find(unlisted, "compare.seed_not_listed");
    expect(problem).toMatchObject({ severity: "error", message: "Arm prng_seed_42 runs on seed 42, which is not in the study's seed list.", path: "seeds[]" });
    expect(problem.fix?.label).toBe("Add the seed to the list");
    expect(codes(problem.fix!.apply(unlisted, cards))).toEqual([]);
  });

  it("names ids used twice, a seed listed twice and an observed arm without session settings", () => {
    const twins = study(withArms((arms) => [...arms, { ...arms[2]!, study_seed: 7 }]));
    expect(find(twins, "compare.duplicate_arm").message).toBe("Two arms have the id prng_seed_1. Every arm needs an id of its own.");
    const repeated = study((config) => ({ ...config, seeds: [...(config.seeds ?? []), 7] }));
    const seed = find(repeated, "compare.duplicate_seed");
    expect(seed.message).toBe("The seed list names 7 twice. Each seed is listed once.");
    expect(codes(seed.fix!.apply(repeated, cards))).toEqual([]);
    expect(find(study((config) => ({ ...config, cards: [...config.cards, { card_id: "novelty_bench" }] })), "measure.duplicate_eval").message).toBe("The eval novelty_bench is selected twice.");
    expect(find(study((config) => ({ ...config, models: [...config.models, config.models[0]!] })), "compare.duplicate_model").severity).toBe("error");
    expect(find(study((config) => ({ ...config, supervised: null })), "compare.observed_without_sessions").message).toBe("Arm qrng_supervised is observed, but the study has no settings for supervised sessions.");
  });

  it.each([
    // What the arm table can be made to say, and the server's `Arm` refuses: each is a 400 on launch and on load.
    ["a seeded arm without its seed", "prng_seed_7", { study_seed: null }, "Arm prng_seed_7 is a seeded pseudo-random arm and has no seed. Give it one.", "arms[].study_seed"],
    ["a biased arm without its seed", "prng_epsilon_010", { study_seed: null }, "Arm prng_epsilon_010 is a biased pseudo-random arm and has no seed. Give it one.", "arms[].study_seed"],
    ["a quantum arm with a seed", "qrng_unsupervised", { study_seed: 7 }, "Arm qrng_unsupervised has the seed 7, and only a seeded or a biased pseudo-random arm runs on a seed. Clear the seed, or change the source.", "arms[].study_seed"],
    ["a biased arm without a bias", "prng_epsilon_010", { epsilon: null }, "Arm prng_epsilon_010 is a biased pseudo-random arm and needs a bias ε of 0.01, 0.03, 0.10 or 0.50.", "arms[].epsilon"],
    ["a biased arm with a bias off the grid", "prng_epsilon_010", { epsilon: 0.2 }, "Arm prng_epsilon_010 is a biased pseudo-random arm and needs a bias ε of 0.01, 0.03, 0.10 or 0.50.", "arms[].epsilon"],
    ["a seeded arm with a bias", "prng_seed_7", { epsilon: 0.1 }, "Arm prng_seed_7 has a bias ε, and only a biased pseudo-random arm has one. Set the bias to None, or change the source.", "arms[].epsilon"],
    ["a quantum arm that decodes greedily", "qrng_unsupervised", { sampling: "greedy" as const }, "Arm qrng_unsupervised decodes greedily, which draws no randomness, so its source cannot be Quantum.", "arms[].sampling"],
    ["an arm switched off without a reason", "prng_epsilon_010", { unavailable_reason: null }, "Arm prng_epsilon_010 is switched off and gives no reason. An arm that is listed and does not run has to say why.", "arms[].unavailable_reason"],
  ])("names an arm that contradicts itself: %s", (_what, id, patch, message, path) => {
    const config = study(withArms((arms) => arms.map((arm) => (arm.id === id ? { ...arm, ...patch } : arm))));
    const found = problems(config, cards).filter((problem) => problem.code === "compare.arm_contradiction" && problem.message === message);
    expect(found, problems(config, cards).map((problem) => `${problem.code}: ${problem.message}`).join(" | ")).toHaveLength(1);
    expect(found[0]).toMatchObject({ severity: "error", stepId: "compare", nodeId: `arm:${id}`, path });
    expect(hasErrors(problems(config, cards))).toBe(true);
    // It is what the page's own check of a study file goes by when the server cannot be asked.
    expect(loadRefusals(config)).toContain(message);
  });

  it("finds no contradiction in the arms the page itself writes, and none in Study 1 as it ships", () => {
    expect(study().arms.flatMap(armContradictions)).toEqual([]);
    expect(parseStudyConfig(studyOneJson)!.arms.flatMap(armContradictions)).toEqual([]);
    for (const { config } of cases) expect(config.arms.flatMap(armContradictions), "a fixture study").toEqual([]);
    expect(loadRefusals(study())).toEqual([]);
    // A draft may be anything while it is typed; the check does not throw on it.
    expect(loadRefusals({ arms: [null, 3, { id: "x", source: "prng_seeded" }] })).toEqual(expect.arrayContaining(["Arm x is a seeded pseudo-random arm and has no seed. Give it one."]));
    expect(loadRefusals(null).length).toBeGreaterThan(0);
  });

  it("names a comparison whose roles are filled twice: control runs on one seed, two quantum arms, two arms at the registered ε", () => {
    const sameSeed = study(withArms((arms) => arms.map((arm) => (arm.id === "prng_seed_7" ? { ...arm, study_seed: 1 } : arm))));
    expect(find(sameSeed, "compare.repeated_control_seed")).toMatchObject({
      severity: "error", message: "Two control runs of qr-llm · creative use the seed 1. Each control run needs a seed of its own.", nodeId: "deck:qr-llm/creative",
    });
    // Three control runs on two seeds are still two distinct seeds, so the mirrored count is satisfied; the repeat is what blocks.
    expect(codes(sameSeed)).not.toContain("compare.too_few_seeds");

    const twoQuantum = study(withArms((arms) => [...arms, { id: "qrng_again", model: "qr-llm", sampling: "creative", source: "qrng", deterministic: true }]));
    expect(find(twoQuantum, "compare.quantum_arms").message).toBe("qr-llm · creative has 2 quantum arms that run unobserved (qrng_unsupervised, qrng_again). A comparison has exactly one.");

    const twoBiased = study((base) => withDesign({ sensitivity_epsilon: 0.1 })(withArms((arms) => [
      ...arms.map((arm) => (arm.epsilon === 0.1 ? { ...arm, availability: "available" as const, unavailable_reason: null } : arm)),
      { id: "prng_epsilon_010_b", model: "qr-llm", sampling: "creative" as const, source: "prng_epsilon" as const, study_seed: 1001, epsilon: 0.1, deterministic: true },
    ])(base)));
    expect(find(twoBiased, "design.epsilon_repeated").message).toBe("qr-llm · creative has 2 arms at ε = 0.10. The positive control of a comparison is one arm.");
  });

  it("names a margin on an outcome that does not decide the study, and an outcome listed twice", () => {
    const MEQ = { card_id: "altered_state_meq", endpoint: "meq30_total" };
    const stray = study(withDesign({ margins: [{ ...MEQ, margin: 0.05 }] }));
    const problem = find(stray, "design.margin_without_outcome");
    expect(problem).toMatchObject({ severity: "error", message: "A margin is declared for MEQ-30 total, which is not an outcome that decides the study.", nodeId: "endpoint:altered_state_meq/meq30_total", stepId: "design" });
    expect(problem.fix?.label).toBe("Remove the margin");
    expect(codes(problem.fix!.apply(stray, cards))).toEqual([]);
    const twice = study((config) => withDesign({ confirmatory: [...designOf(config).confirmatory, PROBE] })(config));
    expect(find(twice, "design.duplicate_outcome").message).toBe("Experience-claim rate is listed twice among the outcomes that decide the study.");
  });

  it("asks none of the roles of a study on the built-in plan, and does not block it for a comparison the server does not ask of it", () => {
    const legacy = study((config) => ({ ...config, design: null }));
    const oneSeed = withArms((arms) => arms.filter((arm) => arm.id !== "prng_seed_7" && arm.id !== "prng_seed_42"))(legacy);
    expect(find(oneSeed, "compare.too_few_seeds").severity).toBe("warning");
    const noQuantum = withArms((arms) => arms.filter((arm) => arm.source !== "qrng"))({ ...legacy, supervised: null });
    expect(find(noQuantum, "compare.no_cell").severity).toBe("warning");
    expect(hasErrors(problems(oneSeed, cards))).toBe(false);
    expect(hasErrors(problems(noQuantum, cards))).toBe(false);
    // With a design block both block, as the server refuses them; and a study with no arm at all is refused whatever its plan.
    expect(find(withArms((arms) => arms.filter((arm) => arm.id !== "prng_seed_7" && arm.id !== "prng_seed_42"))(study()), "compare.too_few_seeds").severity).toBe("error");
    expect(find({ ...legacy, arms: [] }, "compare.no_cell").severity).toBe("error");
  });
});

describe("evals the study cannot be planned with", () => {
  it("names an eval that is not in the registry, and removes it on request", () => {
    const config = study((base) => ({ ...base, cards: [...base.cards, { card_id: "made_up_eval" }] }));
    const problem = find(config, "measure.unknown_eval");
    expect(problem).toMatchObject({ severity: "error", stepId: "measure", message: "The eval made_up_eval is not in this server's registry, so the study cannot be planned with it." });
    expect(problem.fix?.label).toBe("Remove this eval");
    expect(codes(problem.fix!.apply(config, cards))).toEqual([]);
  });

  it("names a k on an eval that takes no repetitions and a prompt count on an eval that reads every token, each with the way back", () => {
    const override = (cardId: string, overrides: { k?: number; items?: number }) => (config: WizardStudyConfig): WizardStudyConfig => ({
      ...config, cards: config.cards.map((selection) => (selection.card_id === cardId ? { ...selection, overrides } : selection)),
    });
    const noK = study(override("target_hit_ranking", { k: 5 }));
    const strayK = find(noK, "measure.k_not_taken");
    expect(strayK.message).toMatch(/: this eval takes no repetitions, so it cannot be given a k\.$/);
    expect(strayK.fix?.label).toBe("Use the card's value");
    expect(codes(strayK.fix!.apply(noK, cards))).toEqual([]);

    const noPrompts = study(override("tier0_logprob_endpoints", { items: 5 }));
    const strayItems = find(noPrompts, "measure.out_of_bounds");
    expect(strayItems.message).toMatch(/: prompts must be between 1 and 0\.$/);
    expect(strayItems.fix?.label).toBe("Use the card's value");
    expect(codes(strayItems.fix!.apply(noPrompts, cards))).toEqual([]);

    // Both values of one eval off: one press puts both back.
    const both = study(override("novelty_bench", { k: 500, items: 5000 }));
    const fixes = problems(both, cards).filter((problem) => problem.code === "measure.out_of_bounds");
    expect(fixes.map((problem) => problem.fix?.label)).toEqual(["Use the card's values", "Use the card's values"]);
    expect(codes(fixes[0]!.fix!.apply(both, cards))).toEqual([]);
  });
});

describe("problems, as a list", () => {
  const everything = [
    ...cases.map((item) => structuredClone(item.config)),
    studyOneTemplate(),
    study(withItems(PROBE.card_id, 6)),
    study(withDesign({ confirmatory: [] })),
    study(withDesign({ claim: "difference_or_equivalence" })),
    study(withDesign({ sensitivity_epsilon: 0.5 })),
  ];

  it("puts every problem on a step and a node", () => {
    for (const config of everything) {
      for (const problem of problems(config, cards)) {
        expect(STEPS, problem.code).toContain(problem.stepId);
        expect(problem.nodeId, problem.code).toMatch(/^(study|family|ghost:evals|(card|endpoint|deck|gate:pos|arm):.+)$/);
        if (REGISTRY_CODES.includes(problem.code)) expect(problem.stepId).toBe(problem.code.split(".")[0]);
      }
    }
  });

  it("offers a fix only where it changes the study, and an error's fix removes that error", () => {
    let fixes = 0;
    for (const config of everything) {
      for (const problem of problems(config, cards)) {
        if (!problem.fix) continue;
        fixes += 1;
        const fixed = problem.fix.apply(config, cards);
        expect(JSON.stringify(fixed), `${problem.code}: ${problem.message}`).not.toBe(JSON.stringify(config));
        if (problem.severity === "error") {
          const left = problems(fixed, cards).filter((other) => other.code === problem.code && other.nodeId === problem.nodeId);
          expect(left, `${problem.code}: ${problem.message}`).toEqual([]);
        }
      }
    }
    expect(fixes).toBeGreaterThan(8);
  });

  it("splits by step and knows an error from a warning", () => {
    const all = problems(studyOneTemplate(), cards);
    expect(problemsFor("design", all).map((problem) => problem.code)).toEqual(["design.no_expectations"]);
    expect(problemsFor("review", all).map((problem) => problem.code)).toEqual(["review.contact_placeholder"]);
    expect(problemsFor("compare", all).map((problem) => problem.code)).toEqual(["compare.checkpoint_unpinned"]);
    expect(problemsFor("instruments", all).map((problem) => problem.code)).toEqual(["instruments.assets_unpinned"]);
    expect(problemsFor("measure", all)).toEqual([]);
    expect(hasErrors(all)).toBe(true);
    expect(hasErrors(problemsFor("design", all))).toBe(false);
  });

  it("does not change the study it is given", () => {
    const config = studyOneTemplate();
    const before = JSON.stringify(config);
    problems(config, cards);
    expect(JSON.stringify(config)).toBe(before);
  });
});
