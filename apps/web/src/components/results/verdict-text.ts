import type { DecisionRow, DecisionsTable } from "@qr-evals/contracts";

/**
 * The words the results page uses for a script-computed verdict.
 *
 * Nothing here decides anything: a verdict, its reason and its sentence are
 * read from `tables/decisions.json`. This file only names them for a reader.
 */

export type Verdict = DecisionRow["verdict"];
export type Reason = NonNullable<DecisionRow["reason"]>;
export type StudyVerdict = DecisionsTable["study_verdict"];
export type NonConfirmatoryReason = NonNullable<DecisionsTable["non_confirmatory_reason"]>;

/** The stamp of a verdict: a word and a glyph, so that colour is never the only channel. */
export const VERDICT_STAMP: Readonly<Record<Verdict, { label: string; glyph: string }>> = {
  effect: { label: "Effect", glyph: "≠" },
  effect_within_margin: { label: "Effect within margin", glyph: "≈" },
  equivalent: { label: "Equivalent", glyph: "=" },
  inconclusive: { label: "Inconclusive", glyph: "?" },
  invalid: { label: "Invalid", glyph: "✕" },
  unavailable: { label: "Unavailable", glyph: "∅" },
};

/**
 * Why a verdict is unavailable, invalid or inconclusive. The inconclusive
 * texts are those of `INCONCLUSIVE_REASON_TEXT` in `cards/design.py`, which
 * the pre-registration prints (checked against `fixtures/design.json`).
 */
export const REASON_TEXT: Readonly<Record<Reason, string>> = {
  not_produced: "the required data were not produced",
  incomplete: "too little of the planned data was analysed",
  negative_control: "two seeds of the same pseudo-random generator differed, or a seed control is missing",
  negative_control_cannot_fail: "the seed-against-seed control had too few prompts or permutations to be able to fail, so it shows nothing",
  equivalence_not_claimed: "the study registered no equivalence claim",
  equivalence_not_testable: "this test has no equivalence margin",
  no_margin: "no margin was registered for this outcome",
  interval_exceeds_margin: "the equivalence test did not pass after correction",
  positive_control_absent: "the positive control was not run",
  positive_control_failed: "the positive control was not detected",
};

export const STUDY_VERDICT_TEXT: Readonly<Record<StudyVerdict, { label: string; glyph: string; sentence: string }>> = {
  not_confirmatory: { label: "Not confirmatory", glyph: "○", sentence: "Not confirmatory." },
  invalid: {
    label: "Invalid",
    glyph: "✕",
    sentence: "This run supports no conclusion: at least one hypothesis is invalid.",
  },
  effect: {
    label: "Effect",
    glyph: "≠",
    sentence: "At least one hypothesis resolved to an effect: quantum and pseudo-random sampling differ on a registered outcome.",
  },
  no_effect_within_margins: {
    label: "No effect within margins",
    glyph: "=",
    sentence: "Every hypothesis resolved to equivalent: no registered outcome differs by more than its margin.",
  },
  mixed_or_inconclusive: {
    label: "Mixed or inconclusive",
    glyph: "?",
    // A hypothesis that resolved to "effect within margin" did detect a
    // difference, and the roll-up still ends here (`study_verdict` in
    // `analysis/decisions.py`), so the sentence names the two verdicts by
    // their stamps and never says that nothing differed.
    sentence: `The study as a whole is mixed or inconclusive: no hypothesis resolved to “${VERDICT_STAMP.effect.label}”, and not every hypothesis resolved to “${VERDICT_STAMP.equivalent.label}”. A difference that stays within its margin (“${VERDICT_STAMP.effect_within_margin.label}”) does not count as an effect for the study as a whole. See each hypothesis below.`,
  },
};

/** The headline and the explanation of a run that cannot confirm anything. */
export const NOT_CONFIRMATORY_TEXT: Readonly<Record<NonConfirmatoryReason, { sentence: string; detail: string }>> = {
  pilot_run: {
    sentence: "Not confirmatory: this is a pilot run.",
    detail: "A pilot run never confirms a hypothesis.",
  },
  not_first_full_run: {
    sentence: "Not confirmatory: this is not the first full run of the study.",
    detail: "Only the first full run of a study is confirmatory.",
  },
};

export const TEST_TEXT: Readonly<Record<DecisionRow["test"], { name: string; estimate: string }>> = {
  paired_permutation: {
    name: "Paired permutation test on the mean difference",
    estimate: "Mean difference, quantum minus pseudo-random",
  },
  ks_two_sample: {
    name: "Two-sample Kolmogorov–Smirnov test on pooled tokens",
    estimate: "KS statistic",
  },
  mmd_hamming_permutation: {
    name: "Permutation test on the maximum mean discrepancy (Hamming kernel)",
    estimate: "MMD statistic",
  },
};

/** What "could not have failed" means for a seed pair, as `control_can_fail` of `cards/design.py` decides it. */
const CANNOT_FAIL_TEXT = "At least one seed-pair test had too few prompts or permutations to be able to return a difference at the corrected level.";

function seedPairs(count: number): string {
  return count === 1 ? "1 seed pair" : `${count} seed pairs`;
}

/**
 * The negative-control gate of one hypothesis, in words.
 *
 * The table holds four things about the seed pairs of a hypothesis: whether
 * the control passed, how many pairs were planned, the smallest adjusted p
 * among the pairs that were computed, and whether every computed pair could
 * have failed. It does not hold how many pairs were computed. A control that
 * did not pass therefore says why only in part, and the recorded reason
 * settles the rest only when the seed control is what the script stopped at
 * (`_MATCHES` in `analysis/decisions.py` tries "not produced" and
 * "incomplete" first). Each sentence here states what the row settles, and
 * says so where the row leaves something open.
 */
export function negativeControlText(
  row: Pick<DecisionRow, "negative_control" | "reason">,
  table: Pick<DecisionsTable, "alpha" | "precision">,
): { passed: boolean; text: string } {
  const { passed, n_pairs: pairs, min_p_adjusted: smallest } = row.negative_control;
  if (passed) {
    return { passed, text: `No seed pair differs after Holm correction (${seedPairs(pairs)} tested)${smallest === null ? "." : `; smallest adjusted ${pRelation(smallest, table.precision)}.`}` };
  }
  if (smallest === null) {
    return {
      passed,
      text: pairs === 0
        ? "No seed pair was planned for this comparison, so the control is missing."
        : `None of the ${seedPairs(pairs)} planned was computed, so the control is missing.`,
    };
  }
  const p = pRelation(smallest, table.precision);
  // A table written before the control was asked whether it could fail has no `can_fail`.
  const cannotFail = row.negative_control.can_fail === false;
  // "Clean": every planned pair was computed and none differs. The control
  // passes when it is clean and could have failed, so one that could have
  // failed and did not pass is not clean. Otherwise only the reason tells.
  const clean = row.reason === "negative_control_cannot_fail" ? true : row.reason === "negative_control" || !cannotFail ? false : null;
  // The table rounds, so a smallest p shown as equal to the level may have been just below it.
  let differs = smallest < table.alpha ? true : smallest > table.alpha || clean === true ? false : null;
  // A smallest p exists, so a single planned pair was computed.
  const missing = clean === true || pairs === 1 ? false : clean === false && differs === false ? true : null;
  if (differs === null && clean === false && missing === false) differs = true;

  const sentences: string[] = [];
  if (differs === true) {
    // Only a single planned pair that is known to differ gets here with a p shown at the level.
    const rounded = smallest < table.alpha ? "" : `, which was below the level α = ${tableNumber(table.alpha)} before the table rounded it`;
    sentences.push(`${pairs === 1 ? "The seed pair" : `At least one of the ${pairs} seed pairs`} differs after Holm correction; smallest adjusted ${p}${rounded}.`);
  } else if (differs === false) {
    sentences.push(missing === false
      ? `No seed pair differs after Holm correction (${seedPairs(pairs)} tested); smallest adjusted ${p}.`
      : `No seed pair that was computed differs after Holm correction; smallest adjusted ${p}.`);
  } else if (clean === false) {
    sentences.push(`Either a seed pair differs after Holm correction or at least one of the ${pairs} seed pairs planned was not computed. The table does not show which: the smallest adjusted ${p} is at the level α = ${tableNumber(table.alpha)} after rounding.`);
  } else {
    sentences.push(`The smallest adjusted ${p} is at the level α = ${tableNumber(table.alpha)} after rounding, so the table does not show whether a seed pair differs after Holm correction.`);
  }
  if (missing === true) sentences.push(`At least one of the ${pairs} seed pairs planned was not computed, so the control is incomplete.`);
  if (cannotFail) {
    // Whether something above already says why the control did not pass.
    const failedAbove = differs === true || missing === true || (differs === null && clean === false);
    const opening = failedAbove ? "In addition, the" : differs === false ? "But the" : "The";
    sentences.push(`${opening} control could not have failed. ${CANNOT_FAIL_TEXT}${failedAbove ? "" : " A control that could not have failed shows nothing."}`);
  }
  if (missing === null && clean === null && differs !== true) sentences.push(`The table does not record whether all ${pairs} seed pairs planned were computed.`);
  return { passed, text: sentences.join(" ") };
}

/** A number exactly as the table holds it. */
export function tableNumber(value: number): string {
  return String(value);
}

/**
 * A p-value as the table holds it. The table rounds to `precision` places, so
 * a stored 0 means "smaller than the last place", which is what is printed.
 */
export function pText(value: number, precision: number): string {
  if (value !== 0 || !Number.isInteger(precision) || precision < 1 || precision > 12) return String(value);
  return `< ${(10 ** -precision).toFixed(precision)}`;
}

/** A p-value inside a sentence: "p = 0.0006", or "p < 0.000001" for a stored 0. */
export function pRelation(value: number, precision: number): string {
  const text = pText(value, precision);
  return text.startsWith("<") ? `p ${text}` : `p = ${text}`;
}

/** A share or a level (0.99) as a percentage (99%), without floating-point noise. */
export function percentText(value: number): string {
  return `${Number((value * 100).toFixed(4))}%`;
}

/** ε with two decimals, as the result sentences print it. */
export function epsilonText(value: number): string {
  return value.toFixed(2);
}
