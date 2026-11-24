import { cards } from "@qr-evals/contracts";
import studyOneJson from "../../../../../packages/contracts/fixtures/study1.json";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import { PANELS } from "../../lib/panels";
import { REGISTRY_CODES, type Problem, type StepId } from "../../lib/problems";
import { problemPath } from "./problem-target";
import { STEPS, stepState } from "./steps";

function legacy(): WizardStudyConfig {
  const parsed = parseStudyConfig(studyOneJson);
  if (!parsed) throw new Error("study1.json does not match StudyConfig.");
  return parsed;
}

const step = (id: StepId) => {
  const found = STEPS.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no step ${id}`);
  return found;
};
const panelIds = (id: StepId, config: WizardStudyConfig) => step(id).panels(config, cards).map((panel) => panel.id);
const problem = (code: string, severity: Problem["severity"] = "error", nodeId = "study"): Problem => ({ code, severity, message: code, stepId: code.split(".", 1)[0] as StepId, nodeId });

describe("step registry", () => {
  it("lists the five steps in order, each with a label, a heading and an intro in whole sentences", () => {
    expect(STEPS.map((candidate) => candidate.id)).toEqual(["compare", "measure", "design", "instruments", "review"]);
    expect(STEPS.map((candidate) => candidate.label)).toEqual(["Compare", "Measure", "Design", "Instruments", "Review & launch"]);
    for (const candidate of STEPS) {
      expect(candidate.heading.length, candidate.id).toBeGreaterThan(0);
      expect(candidate.intro, candidate.id).toMatch(/^[A-Z].*\.$/);
      // Steps are numbered by their place; none is named by number in copy.
      expect(`${candidate.label} ${candidate.heading} ${candidate.intro}`, candidate.id).not.toMatch(/\d/);
    }
  });

  it("gives every setting that is not advanced a step, and no step an advanced panel", () => {
    const template = studyOneTemplate();
    const drawn = new Set(STEPS.flatMap((candidate) => candidate.panels(template, cards).map((panel) => panel.id)));
    for (const candidate of STEPS) {
      for (const panel of candidate.panels(template, cards)) expect(panel.advanced ?? false, `${candidate.id}: ${panel.id}`).toBe(false);
    }
    // The title is the header's; everything else that is not advanced is on a step.
    const expected = PANELS.filter((panel) => !panel.advanced && panel.id !== "title").map((panel) => panel.id);
    expect([...drawn].sort()).toEqual([...expected].sort());
  });

  it("composes each step from its panels", () => {
    const template = studyOneTemplate();
    expect(panelIds("compare", template)).toEqual(["comparison", "conditioning"]);
    expect(panelIds("measure", template)).toEqual(["evals", "prompts"]);
    expect(panelIds("design", template)).toEqual(["claim", "outcomes", "alpha", "prompts", "positive-control", "margins", "expectations"]);
    expect(panelIds("instruments", template)).toEqual(["judge", "sessions"]);
    expect(panelIds("review", template)).toEqual(["people"]);
  });

  it("draws only the claim and the expectations on Design for a study without a design block", () => {
    expect(panelIds("design", legacy())).toEqual(["claim", "expectations"]);
  });

  it("offers Instruments only for a study with a judged eval or supervised sessions", () => {
    const template = studyOneTemplate();
    const tokenLevel = template.cards.filter((selection) => selection.card_id.startsWith("tier0_"));
    expect(step("instruments").shown(template, cards)).toBe(true);
    expect(step("instruments").shown({ ...template, supervised: null }, cards)).toBe(true);
    expect(panelIds("instruments", { ...template, supervised: null })).toEqual(["judge"]);
    expect(step("instruments").shown({ ...template, cards: tokenLevel }, cards)).toBe(true);
    expect(panelIds("instruments", { ...template, cards: tokenLevel })).toEqual(["sessions"]);
    expect(step("instruments").shown({ ...template, cards: tokenLevel, supervised: null }, cards)).toBe(false);
    for (const candidate of STEPS.filter(({ id }) => id !== "instruments")) expect(candidate.shown({ ...template, cards: [], arms: [], supervised: null }, cards), candidate.id).toBe(true);
  });

  it("looks up its own problems out of all of them", () => {
    const all = [problem("compare.no_cell"), problem("design.no_expectations", "warning"), problem("design.no_outcome"), problem("review.contact_placeholder")];
    expect(step("compare").problems(all).map(({ code }) => code)).toEqual(["compare.no_cell"]);
    expect(step("design").problems(all).map(({ code }) => code)).toEqual(["design.no_expectations", "design.no_outcome"]);
    expect(step("measure").problems(all)).toEqual([]);
    expect(step("review").problems(all).map(({ code }) => code)).toEqual(["review.contact_placeholder"]);
  });
});

describe("stepState", () => {
  it("says errors, warnings or clear in words, with the class the rail carries", () => {
    expect(stepState([])).toEqual({ state: "is-clear", text: "clear" });
    expect(stepState([problem("design.no_expectations", "warning")])).toEqual({ state: "has-warnings", text: "1 to check" });
    expect(stepState([problem("compare.no_cell")])).toEqual({ state: "has-errors", text: "1 to fix" });
    expect(stepState([problem("design.no_outcome"), problem("design.below_floor"), problem("design.no_expectations", "warning")])).toEqual({ state: "has-errors", text: "2 to fix, 1 to check" });
  });
});

describe("problemPath", () => {
  it("names a setting for every problem the study itself can have", () => {
    for (const code of [...REGISTRY_CODES, "compare.custom_arms", "instruments.judge", "review.contact_placeholder"]) {
      expect(problemPath(problem(code)), code).toEqual(expect.any(String));
    }
  });

  it("names none for a problem no single setting answers", () => {
    expect(problemPath(problem("review.server_rejected"))).toBeUndefined();
    expect(problemPath({ ...problem("draft.not_saved", "warning"), stepId: "review" })).toBeUndefined();
  });

  it("leads from a draft that is not stored because of a setting to that setting", () => {
    expect(problemPath({ ...problem("draft.not_saved", "warning"), stepId: "instruments", path: "judge.max_tokens" })).toBe("judge.max_tokens");
    // An outcome tested on the generated tokens with nothing generating for it is answered in the eval checklist.
    expect(problemPath(problem("design.no_planned_data", "error", "endpoint:tier0_pit_residuals/pit_ks_two_sample"))).toBe("cards[].card_id");
  });

  it("points at the eval or the outcome the problem names", () => {
    expect(problemPath(problem("design.below_floor", "error", "card:self_referential_probe"))).toBe("cards.self_referential_probe.items");
    expect(problemPath(problem("measure.out_of_bounds", "error", "card:novelty_bench"))).toBe("cards.novelty_bench.items");
    expect(problemPath(problem("design.margin_not_allowed", "error", "endpoint:altered_state_meq/meq30_total"))).toBe("design.margins.altered_state_meq/meq30_total");
    expect(problemPath(problem("design.epsilon_missing", "error", "gate:pos:qr-llm/creative"))).toBe("design.sensitivity_epsilon");
  });
});
