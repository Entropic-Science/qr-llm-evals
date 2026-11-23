import type { HypothesisPlan, SampleSize } from "@qr-evals/contracts";
import { PAIRED_TEST, formatG } from "../../../lib/design";
import { MARGINAL_POWER, POWER, REFERENCE_EFFECT } from "../../../lib/power";

// A hypothesis in plain words: its test, its margin, the data it plans for and what that data can detect.
//
// The sentences follow the pre-registration's (`prereg/render.py`), so a reader meets the same wording on the
// setup page, on the map and in the document. Every number is read from the `HypothesisPlan`; nothing is
// computed here.

const count = new Intl.NumberFormat("en");
const percent = (fraction: number, digits = 0) => `${(fraction * 100).toFixed(digits)}%`;

const SAMPLING_WORDS: Readonly<Record<string, string>> = {
  creative: "Creative sampling",
  t1_minp: "Temperature 1 + min-p 0.10",
  greedy: "Greedy",
};

const TEST_WORDS: Readonly<Record<HypothesisPlan["test"], string>> = {
  paired_permutation: "Paired permutation test on the mean difference, with the prompt as the unit of analysis",
  ks_two_sample: "Two-sample Kolmogorov–Smirnov test on the token sampling residuals, pooled per side",
  mmd_hamming_permutation: "Permutation test on the maximum mean discrepancy (Hamming kernel) between token sequences",
};

const SIDED_WORDS: Readonly<Record<HypothesisPlan["sided"], string>> = {
  two_sided: "Two-sided: a difference in either direction counts",
  one_sided_upper: "One-sided: only a divergence larger than chance counts",
};

/** The comparison a hypothesis belongs to: its model and its sampling preset. Comparisons are never pooled. */
export function comparisonWords(hypothesis: Pick<HypothesisPlan, "model" | "sampling">): string {
  return `${hypothesis.model} · ${SAMPLING_WORDS[hypothesis.sampling] ?? hypothesis.sampling}`;
}

/** The generated statement without its "H1: " lead, which the card shows as a mark of its own. */
export function statementWords(hypothesis: Pick<HypothesisPlan, "id" | "statement">): string {
  const lead = `${hypothesis.id}: `;
  const body = hypothesis.statement.startsWith(lead) ? hypothesis.statement.slice(lead.length) : hypothesis.statement;
  return body.charAt(0).toUpperCase() + body.slice(1);
}

export function testWords(hypothesis: Pick<HypothesisPlan, "test" | "sided">): string {
  return `${TEST_WORDS[hypothesis.test]}. ${SIDED_WORDS[hypothesis.sided]}.`;
}

export function marginWords(hypothesis: Pick<HypothesisPlan, "test" | "margin" | "margin_source">): string {
  if (hypothesis.margin === null) {
    return hypothesis.test === PAIRED_TEST
      ? "None. Without a margin this outcome cannot be reported as equivalent."
      : "None. This test has no paired difference a margin could apply to.";
  }
  const source = hypothesis.margin_source === "card_sesoi" ? "the smallest effect of interest stated on the eval card" : "declared in this study";
  return `±${formatG(hypothesis.margin)}, ${source}.`;
}

/** Whether the test runs on pooled tokens, which have no closed-form power. */
export function pooled(hypothesis: Pick<HypothesisPlan, "sample_size">): boolean {
  return hypothesis.sample_size.basis === "no_closed_form";
}

export function plannedWords(hypothesis: Pick<HypothesisPlan, "test" | "planned_units" | "planned_pairs">): string {
  return hypothesis.test === PAIRED_TEST
    ? `${count.format(hypothesis.planned_units)} prompts, ${count.format(hypothesis.planned_pairs)} paired values (prompt, repetition and told cell).`
    : `${count.format(hypothesis.planned_units)} generations per arm, pooled token by token. Evals derived from the replies of another eval are not counted.`;
}

/** What the planned data is counted in, for a table cell. */
export function plannedShort(hypothesis: Pick<HypothesisPlan, "sample_size">): string {
  const size = hypothesis.sample_size;
  return pooled(hypothesis) ? `${count.format(size.n)} generations per arm` : `${count.format(size.n)} prompts`;
}

/** The sentence for a test without a closed-form power. Such a test is never rated as enough. */
export const NO_CLOSED_FORM = "Unknown: no closed-form power for this test.";
/** What every rated sample size rests on. */
export const WORST_CASE = "Worst-case variance, no prompt-by-source heterogeneity; true power may be lower.";
/** What a rating is not. */
export const RATING_IS_NOT_A_VERDICT = "A rating is not a verdict and changes no decision rule. With an insufficient or unknown rating, an inconclusive result is likely even if the sources differ.";
/** The power every sample size is rated at, as the page prints it. */
export const POWER_TEXT = percent(POWER);

/** Whether the hypothesis has fewer prompts than its seed-against-seed control is asked for (`control_floor`). */
function belowControlFloor(size: SampleSize): boolean {
  const floor = size.control_floor ?? null;
  return floor !== null && size.n < floor;
}

/** What too few prompts for the control mean, in the pre-registration's words (`_detectable`). */
function controlFloorWords(size: SampleSize): string {
  return `with ${size.n} prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the hypothesis can only resolve to invalid. At least ${size.control_floor ?? 0} prompts are needed.`;
}

/** The smallest difference the planned prompts detect at the rated power, as a sentence. */
export function detectableWords(hypothesis: Pick<HypothesisPlan, "margin" | "sample_size">): string {
  const size = hypothesis.sample_size;
  if (size.basis === "no_closed_form") return NO_CLOSED_FORM;
  if (size.n < size.n_floor) return `None: ${size.n} prompts cannot reach significance at any effect size. At least ${size.n_floor} are needed.`;
  if (belowControlFloor(size)) return `None: ${controlFloorWords(size)}`;
  if (size.mde_raw !== null && size.power_at_margin !== null && hypothesis.margin !== null) {
    return `${size.mde_raw.toFixed(3)} in the units of the outcome; ${percent(size.power_at_margin, 1)} power for a difference as large as the margin ±${formatG(hypothesis.margin)}.`;
  }
  if (size.mde_standardized !== null) return `${size.mde_standardized.toFixed(2)} standard deviations of the per-prompt difference.`;
  return "Not computed.";
}

/** What the planned prompts can detect, as a sentence that stands on its own on a hypothesis card. */
export function sampleSizeWords(hypothesis: Pick<HypothesisPlan, "margin" | "sample_size">): string {
  const size = hypothesis.sample_size;
  if (size.basis === "no_closed_form") return NO_CLOSED_FORM;
  if (size.n < size.n_floor) return `${size.n} prompts cannot reach significance at any effect size. At least ${size.n_floor} are needed.`;
  if (belowControlFloor(size)) { const words = controlFloorWords(size); return words.charAt(0).toUpperCase() + words.slice(1); }
  if (size.mde_raw !== null && size.power_at_margin !== null && hypothesis.margin !== null) {
    return `${size.n} prompts detect a difference of ${size.mde_raw.toFixed(3)} or more with ${POWER_TEXT} power. A difference as large as the margin ±${formatG(hypothesis.margin)} is detected with ${percent(size.power_at_margin, 1)} power.`;
  }
  if (size.mde_standardized !== null) return `${size.n} prompts detect a difference of ${size.mde_standardized.toFixed(2)} standard deviations or more with ${POWER_TEXT} power.`;
  return "Not computed.";
}

/**
 * How many prompts the rating recommends and, where the eval has fewer, the repetitions that make the ones it
 * has enough. The wording is the pre-registration's (`_sample_size_row`).
 */
export function recommendedWords(size: SampleSize): string {
  if (size.basis === "no_closed_form") return "Not rated";
  if (size.n_recommended === null) return "Not computed";
  if (size.reachable !== false) return String(size.n_recommended);
  const more = `${size.n_recommended}, more than the eval has`;
  // Repetitions are the other way to sharpen a prompt's value; they enter only a margin's worst-case bound.
  if (size.basis !== "margin_worst_case") return more;
  const k = size.k_recommended ?? null;
  return k !== null ? `${more}. With k = ${k}, its prompts are enough` : `${more}. No number of repetitions the eval allows makes its prompts enough`;
}

/** What each rating means, in the pre-registration's words. */
export const RATING_MEANING: Readonly<Record<SampleSize["rating"], string>> = {
  sufficient: `At least ${POWER_TEXT} power for a difference as large as the margin, under the largest variance a bounded outcome can have.`,
  marginal: `At least ${percent(MARGINAL_POWER)} power at the margin. For an outcome without a margin in its own units: a detectable difference of at most ${formatG(REFERENCE_EFFECT)} standard deviations. Such an outcome is never rated sufficient.`,
  insufficient: "Less than that, or too few prompts to reach significance at all.",
  unknown: "The test runs on pooled tokens and has no closed-form power.",
};
