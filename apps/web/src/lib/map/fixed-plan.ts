import type { Arm } from "@qr-evals/contracts";

// What the fixed Study 1 plan does with an outcome and with an observed arm. That plan is the plan of every
// study without a design block (`_legacy` in `cards/statistical_plan.py`; "Study 1 legacy plan", "Secondary
// tests and equivalence" and "Supervision" in docs/ANALYSIS_PLAN.md). The map does not draw it, so where the
// map speaks of it, it has to say what the plan registers and nothing more. Checked in `fixed-plan.test.ts`
// against the backend's own tables and a frozen plan.

const refKey = (cardId: string, endpoint: string) => `${cardId}/${endpoint}`;

/** The four primary outcomes of the plan: `PRIMARY_TESTS` of `cards/statistical_plan.py`. */
export const FIXED_PRIMARIES: readonly string[] = [
  "tier0_pit_residuals/pit_ks_two_sample",
  "tier0_mmd_hamming/mmd_hamming",
  "unpredictabench/ks100_pass_rate",
  "novelty_bench/distinct_k",
];

/** The Tier 4 outcomes the plan still tests: `CONFIRMATORY_SECONDARIES` of `cards/statistical_plan.py`. */
export const FIXED_CONFIRMATORY_SECONDARIES: readonly string[] = [
  "source_guessing/guess_accuracy",
  "source_guessing_self_report/guess_accuracy",
  "opinion_quantum_sampling/actual_main_effect_axis_score",
];

/**
 * What the plan does with an outcome:
 * - `primary`: one of its four primary outcomes, Holm-corrected at 0.05;
 * - `secondary`: corrected by Benjamini–Hochberg at 0.05 within the eval's tier;
 * - `ungated`: a Tier 4 outcome it calls exploratory and holds to no significance level.
 */
export type FixedPlanUse = "primary" | "secondary" | "ungated";

export const FIXED_PLAN_USES: readonly FixedPlanUse[] = ["primary", "secondary", "ungated"];

export function fixedPlanUse(cardId: string, endpoint: string, tier: number | null): FixedPlanUse {
  const key = refKey(cardId, endpoint);
  if (FIXED_PRIMARIES.includes(key)) return "primary";
  return tier === 4 && !FIXED_CONFIRMATORY_SECONDARIES.includes(key) ? "ungated" : "secondary";
}

const listed = (names: readonly string[]): string => (names.length <= 1 ? names[0] ?? "" : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`);

function useClause(use: FixedPlanUse, many: boolean, tier: number | null): string {
  const it = many ? "they are" : "it is";
  switch (use) {
    case "primary":
      return `${many ? "are primary outcomes" : "is a primary outcome"} of the fixed Study 1 plan: ${it} tested with Holm correction at α = 0.05 across the primary outcomes of that plan.`;
    case "secondary":
      return `${many ? "are secondary outcomes" : "is a secondary outcome"} of the fixed Study 1 plan: ${it} tested with Benjamini–Hochberg correction at 0.05 across the secondary outcomes of ${tier === null ? "its tier" : `tier ${tier}`}.`;
    case "ungated":
      return `${many ? "are" : "is"} reported as exploratory by the fixed Study 1 plan, with no significance threshold.`;
  }
}

/**
 * What the fixed Study 1 plan does with the outcomes of one eval, as sentences: one per use, the tested
 * outcomes first. An eval whose outcomes all have one use gets one sentence about all of them.
 */
export function fixedPlanSentences(outcomes: readonly { label: string; use: FixedPlanUse }[], tier: number | null): string[] {
  const groups = FIXED_PLAN_USES
    .map((use) => ({ use, labels: outcomes.filter((outcome) => outcome.use === use).map((outcome) => outcome.label) }))
    .filter((group) => group.labels.length > 0);
  return groups.map(({ use, labels }) => {
    const subject = groups.length === 1 && labels.length > 1 ? `All ${labels.length} outcomes` : listed(labels);
    return `${subject} ${useClause(use, labels.length > 1, tier)}`;
  });
}

const available = (arm: Arm) => (arm.availability ?? "available") === "available";
const observed = (arm: Arm) => (arm.observer_mode ?? "unsupervised") === "supervised";

/**
 * What the fixed Study 1 plan does with an observed quantum arm, given the arms of its model and sampling
 * preset. The plan compares it with the unobserved quantum arm on its primary outcomes, in a Holm family of
 * its own. Where there is no unobserved quantum arm, the observed one stands in as the quantum arm against
 * the seeded controls. The plan leaves out an arm that is switched off.
 */
/**
 * The same, said of the observed arm before it is known which arms stand beside it: the note of the setting
 * that adds the arm. It covers both cases `observedArmInFixedPlan` tells apart for an arm that runs.
 */
export const OBSERVED_ARM_IN_FIXED_PLAN = "The fixed Study 1 plan tests it against the unobserved quantum arm of the same comparison on the primary outcomes of that plan, corrected with Holm in a family of their own. In a comparison without an unobserved quantum arm it is tested against the seeded controls instead.";
/** What a designed study does with the observed arm. */
export const OBSERVED_ARM_IN_DESIGN = "Its comparison is exploratory and gets no verdict.";

export function observedArmInFixedPlan(arm: Arm, cell: readonly Arm[]): { sub: string; story: string; design: string } {
  if (!available(arm)) {
    return {
      sub: "switched off, not compared",
      story: "It is switched off, so the fixed Study 1 plan does not compare it with any arm.",
      design: "Compared with no arm while it is switched off.",
    };
  }
  const running = cell.filter(available);
  if (running.some((other) => other.source === "qrng" && !observed(other))) {
    return {
      sub: "tested against unobserved quantum",
      story: "The fixed Study 1 plan tests it against the unobserved quantum arm on the primary outcomes of that plan, corrected with Holm in a family of their own.",
      design: "Compared with the unobserved quantum arm.",
    };
  }
  if (running.some((other) => other.source === "prng_seeded" && !observed(other))) {
    return {
      sub: "tested against the seeded controls",
      story: "This comparison has no unobserved quantum arm, so the fixed Study 1 plan tests this arm against the seeded controls in its place.",
      design: "Compared with the seeded controls, pooled.",
    };
  }
  return {
    sub: "nothing to compare it with",
    story: "This comparison has no unobserved quantum arm and no seeded control, so the fixed Study 1 plan has nothing to compare this arm with.",
    design: "Compared with no arm.",
  };
}
