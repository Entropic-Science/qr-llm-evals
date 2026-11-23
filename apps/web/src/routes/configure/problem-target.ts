import type { Problem } from "../../lib/problems";

/**
 * The setting a problem is about, as the `data-path` of the field that edits it, or `undefined` for a problem
 * no single field answers (a study the browser's storage has no room for, a study the server refused). A
 * problem that carries the path of its setting (a value outside what a study can hold and the notice that
 * the draft is not stored because of it, a broken cross-reference, a missing pin) leads there.
 *
 * `goTo({ stepId: problem.stepId, path: problemPath(problem) })` opens the step and puts the focus there.
 */
export function problemPath(problem: Problem): string | undefined {
  const endpoint = problem.nodeId.startsWith("endpoint:") ? problem.nodeId.slice("endpoint:".length) : null;
  const card = problem.nodeId.startsWith("card:") ? problem.nodeId.slice("card:".length) : null;
  switch (problem.code) {
    case "compare.no_cell": return "roster.cells";
    case "compare.too_few_seeds": return "roster.seeds";
    // Hand-written arms have no roster field; the comparison panel holds the notice and its one button.
    case "compare.custom_arms": return "arms";
    case "measure.no_eval":
    // An outcome tested on the generated tokens with no eval that generates on its own prompts: the way out is to select one.
    case "design.no_planned_data": return "cards[].card_id";
    // The prompts and repetitions of the eval the problem names, in the table that holds them all.
    case "measure.out_of_bounds":
    case "design.below_floor":
    case "design.control_below_floor":
    case "design.insufficient": return card === null ? "cards[].overrides" : `cards.${card}.items`;
    case "design.legacy_plan": return "design";
    case "design.no_outcome":
    case "design.not_eligible":
    case "design.too_many_tests": return "design.confirmatory";
    case "design.epsilon_missing":
    case "design.epsilon_off":
    case "design.epsilon_seed_shared":
    case "design.control_not_rated": return "design.sensitivity_epsilon";
    case "design.equivalence_needs_control":
    case "design.equivalent_unreachable":
    case "design.study_absence_unreachable": return "design.claim";
    case "design.margin_not_allowed":
    case "design.margin_below_floor": return endpoint === null ? "design.margins" : `design.margins.${endpoint}`;
    case "design.no_expectations": return "expectations";
    case "instruments.judge": return "judge.model";
    case "review.contact_placeholder": return "contact";
    default: return problem.path;
  }
}
