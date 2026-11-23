import type { HypothesisPlan } from "@qr-evals/contracts";
import { reachableVerdicts, type Unreachable, type Verdict } from "../../../lib/design";
import { VERDICT_STAMP } from "../../results/verdict-text";

// The six answers a hypothesis can end with, in the words every view uses for them.
//
// Nothing here decides anything. Which verdicts a hypothesis can reach is `reachableVerdicts` (the mirror of
// the backend's gates); the labels and glyphs are the ones the results page stamps a verdict with.

/** The findings first, then the two answers that say the run itself fell short. */
export const VERDICT_ORDER: readonly Verdict[] = ["effect", "effect_within_margin", "equivalent", "inconclusive", "invalid", "unavailable"];

/** What each verdict says, after the wording of the decision matrix. */
export const VERDICT_MEANING: Readonly<Record<Verdict, string>> = {
  effect: "Quantum and pseudo-random sampling differ on this outcome.",
  effect_within_margin: "A difference is detectable, but it is smaller than the margin.",
  equivalent: "No difference larger than the margin, and the positive control was detected.",
  inconclusive: "Neither a difference nor its absence is established.",
  invalid: "No conclusion: too little of the planned data was analysed, or two seeds of the same pseudo-random generator differed, or their comparison is missing or could not have failed.",
  unavailable: "Not assessed: the required data were not produced.",
};

/**
 * Why a verdict is out of reach, as it is known before any data exist. The wording is the pre-registration's
 * (`_UNREACHABLE_TEXT` in `prereg/render.py`).
 */
export const UNREACHABLE_TEXT: Readonly<Partial<Record<Unreachable, string>>> = {
  equivalence_not_claimed: "the study registers no equivalence claim",
  equivalence_not_testable: "this test has no equivalence margin",
  no_margin: "no margin is registered for this outcome",
  positive_control_absent: "no positive control is available",
  below_n_floor: "too few prompts to reach significance at any effect size",
  control_cannot_fail: "fewer prompts than the seed-against-seed control is asked for, so the hypothesis is invalid",
};

export function verdictLabel(verdict: Verdict): string {
  return VERDICT_STAMP[verdict].label;
}

export function verdictGlyph(verdict: Verdict): string {
  return VERDICT_STAMP[verdict].glyph;
}

/** Why a verdict cannot be reached, as the end of a sentence. */
export function unreachableText(why: Unreachable): string {
  return UNREACHABLE_TEXT[why] ?? why.replaceAll("_", " ");
}

/** One verdict of one hypothesis: whether the study as configured can reach it and, if not, why. */
export interface VerdictReach {
  verdict: Verdict;
  label: string;
  reachable: boolean;
  why: Unreachable | null;
  /** Why it cannot be reached, as the end of a sentence; `null` when it can. */
  reason: string | null;
}

/** Every verdict of a hypothesis, in `VERDICT_ORDER`. `claim` is the study's `design.claim`. */
export function verdictReach(hypothesis: HypothesisPlan, claim: string): VerdictReach[] {
  const reach = reachableVerdicts(hypothesis, claim);
  return VERDICT_ORDER.map((verdict) => {
    const why = reach[verdict];
    return { verdict, label: verdictLabel(verdict), reachable: why === null, why, reason: why === null ? null : unreachableText(why) };
  });
}
