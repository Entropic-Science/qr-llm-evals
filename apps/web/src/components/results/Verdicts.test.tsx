import { DecisionsTableSchema, type DecisionRow } from "@qr-evals/contracts";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import designFixture from "../../../../../packages/contracts/fixtures/design.json";
import decisionsSchema from "../../../../../packages/contracts/schema/DecisionsTable.json";
import {
  cannotFailRow,
  decisionsWith,
  designedDecisions,
  designedPlan,
  effectRow,
  effectWithinMarginRow,
  equivalentRow,
  incompleteRow,
  ksRow,
  mmdRow,
  negativeControlRow,
  noClaimRow,
  notConfirmatory,
  positiveFailedRow,
  unavailableRow,
} from "./decisions-fixture";
import { Verdicts } from "./Verdicts";
import { NOT_CONFIRMATORY_TEXT, REASON_TEXT, STUDY_VERDICT_TEXT, TEST_TEXT, VERDICT_STAMP, negativeControlText, pRelation, pText, percentText } from "./verdict-text";

afterEach(cleanup);

/** The block of one hypothesis, found by its heading. */
function block(label: string): HTMLElement {
  const heading = screen.getByRole("heading", { level: 3, name: label });
  const article = heading.closest("article");
  if (!article) throw new Error(`no block for ${label}`);
  return article;
}

/** The value printed for one term of a hypothesis's figures. */
function figure(article: HTMLElement, term: string): string {
  const dt = within(article).getAllByText(term, { selector: "dt" })[0];
  return dt?.nextElementSibling?.textContent ?? "";
}

function gate(article: HTMLElement, name: RegExp): HTMLElement {
  const item = within(article).getByText(name, { selector: ".results-gate__name" }).closest("li");
  if (!item) throw new Error(`no gate ${String(name)}`);
  return item;
}

/** The values one field of the generated decisions schema may hold, null left out. */
function enumOf(property: unknown): string[] {
  const node = property as { enum?: unknown[]; anyOf?: { enum?: unknown[] }[] };
  const values = node.enum ?? node.anyOf?.flatMap((branch) => branch.enum ?? []) ?? [];
  return values.filter((value): value is string => typeof value === "string");
}

const ROW_SCHEMA = decisionsSchema.$defs.DecisionRow.properties;

describe("the fixtures", () => {
  it("are decisions tables as the generated contract defines them", () => {
    const rows = [ksRow, mmdRow, equivalentRow, effectRow, effectWithinMarginRow, incompleteRow, negativeControlRow, cannotFailRow, unavailableRow, positiveFailedRow, noClaimRow];
    expect(DecisionsTableSchema.safeParse(designedDecisions).success).toBe(true);
    expect(DecisionsTableSchema.safeParse(decisionsWith(rows)).success).toBe(true);
    expect(DecisionsTableSchema.safeParse(notConfirmatory("pilot_run")).success).toBe(true);
    expect(DecisionsTableSchema.safeParse({ ...designedDecisions, rows: [{ ...ksRow, verdict: "supported" }] }).success).toBe(false);
  });

  it("carry the sentences the decision matrix writes for rows 1 to 3", () => {
    const rule = (number: number) => designFixture.verdict_rules.find((entry) => entry.number === number);
    expect(unavailableRow.wording).toBe(rule(1)?.wording);
    expect(incompleteRow.wording).toBe(rule(2)?.wording);
    expect(negativeControlRow.wording).toBe(rule(3)?.wording);
    expect(cannotFailRow.wording).toBe(rule(3)?.wording);
    expect(rule(3)?.reasons).toEqual(["negative_control", "negative_control_cannot_fail"]);
  });
});

describe("study verdict", () => {
  it("comes first, as one sentence with a stamp", () => {
    render(<Verdicts table={designedDecisions} />);
    const section = screen.getByRole("region", { name: "Verdicts" });
    const sentence = within(section).getByText(STUDY_VERDICT_TEXT.mixed_or_inconclusive.sentence);
    const firstBlock = block("Token sampling residuals (KS)");
    expect(sentence.compareDocumentPosition(firstBlock) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sentence.closest(".results-study-verdict")).toHaveTextContent("Mixed or inconclusive");
    expect(screen.queryByText(/Not confirmatory/)).not.toBeInTheDocument();
  });

  it.each([
    ["invalid", [incompleteRow]],
    ["effect", [{ ...effectRow, hypothesis_id: "H4" }, equivalentRow]],
    ["no_effect_within_margins", [equivalentRow]],
    ["mixed_or_inconclusive", [ksRow, equivalentRow]],
  ] as const)("states %s in its own sentence", (verdict, rows) => {
    render(<Verdicts table={decisionsWith([...rows], { study_verdict: verdict })} />);
    const text = STUDY_VERDICT_TEXT[verdict];
    const banner = screen.getByText(text.sentence).closest(".results-study-verdict");
    expect(banner).toHaveClass(`results-study-verdict--${verdict}`);
    expect(within(banner as HTMLElement).getByText(text.label)).toHaveClass(`results-stamp--${verdict}`);
  });

  it("does not say that nothing differed when a hypothesis found a difference within its margin", () => {
    // The roll-up of `analysis/decisions.py` ends at mixed_or_inconclusive for these two rows.
    render(<Verdicts table={decisionsWith([{ ...effectWithinMarginRow, label: "Small outcome" }, ksRow], { study_verdict: "mixed_or_inconclusive" })} />);
    const banner = screen.getByRole("region", { name: "Verdicts" }).querySelector<HTMLElement>(".results-study-verdict")!;
    const sentence = banner.querySelector(".results-study-verdict__sentence")?.textContent ?? "";
    expect(sentence).toBe(STUDY_VERDICT_TEXT.mixed_or_inconclusive.sentence);
    // the sentence that used to stand here contradicted the stamp of the block below it
    expect(sentence).not.toMatch(/no hypothesis resolved to an effect\b/i);
    expect(sentence).toContain("no hypothesis resolved to “Effect”");
    expect(sentence).toContain("not every hypothesis resolved to “Equivalent”");
    expect(sentence).toContain("A difference that stays within its margin (“Effect within margin”) does not count as an effect for the study as a whole.");
    // the words it uses are the stamps the reader sees on the hypotheses
    const small = block("Small outcome");
    expect(within(small).getByText("Effect within margin")).toHaveClass("results-stamp--effect_within_margin");
    expect(within(small).getByText(effectWithinMarginRow.wording)).toBeVisible();
  });

  it("says a pilot run is not confirmatory and why", () => {
    render(<Verdicts table={notConfirmatory("pilot_run")} />);
    const banner = screen.getByText("Not confirmatory: this is a pilot run.").closest(".results-study-verdict") as HTMLElement;
    expect(banner).toHaveClass("results-study-verdict--not_confirmatory");
    expect(banner).toHaveTextContent("A pilot run never confirms a hypothesis.");
    expect(banner).toHaveTextContent("They decide nothing.");
    // Every hypothesis still resolves, and each one is marked in words.
    expect(screen.getAllByText("Not confirmatory", { selector: ".results-stamp__note" })).toHaveLength(3);
    expect(within(block("Experience-claim rate")).getByText(/^Not confirmatory: No difference larger than ±0\.05/)).toBeVisible();
    // The roll-up of a confirmatory run is not claimed.
    expect(screen.queryByText(STUDY_VERDICT_TEXT.mixed_or_inconclusive.sentence)).not.toBeInTheDocument();
  });

  it("says a later full run is not confirmatory and why", () => {
    render(<Verdicts table={notConfirmatory("not_first_full_run")} />);
    const banner = screen.getByText("Not confirmatory: this is not the first full run of the study.").closest(".results-study-verdict");
    expect(banner).toHaveTextContent("Only the first full run of a study is confirmatory.");
  });
});

describe("one block per hypothesis", () => {
  it("shows label, comparison, stamp and the table's sentence verbatim", () => {
    render(<Verdicts table={designedDecisions} plan={designedPlan} />);
    expect(screen.getAllByRole("article")).toHaveLength(3);
    const article = block("Experience-claim rate");
    expect(within(article).getByText("H3 · qr-llm · creative")).toBeVisible();
    expect(within(article).getByText(equivalentRow.wording)).toBeVisible();
    const stamp = within(article).getByText("Equivalent");
    expect(stamp).toHaveClass("results-stamp--equivalent");
    expect(stamp).toHaveTextContent("=Equivalent");
    // the registered statement and the arms come from the frozen plan
    expect(within(article).getByText(designedPlan.design.hypotheses[2]!.statement)).toBeVisible();
    expect(within(article).getByText("qrng_unsupervised (quantum) against prng_seed_1, prng_seed_7, prng_seed_42 (seeded pseudo-random), qr-llm · creative.")).toBeVisible();
    expect(within(article).getByText("Paired permutation test on the mean difference; two-sided.")).toBeVisible();
    expect(within(block("Token sequence divergence (MMD)")).getByText(/one-sided, upper tail\.$/)).toBeVisible();
  });

  it("names the comparison without the plan", () => {
    render(<Verdicts table={designedDecisions} />);
    expect(within(block("Experience-claim rate")).getByText("Quantum against seeded pseudo-random sampling, qr-llm · creative.")).toBeVisible();
    expect(screen.queryByText("Registered hypothesis")).not.toBeInTheDocument();
  });

  it("prints the estimate, its interval and level, p and adjusted p as the table holds them", () => {
    render(<Verdicts table={designedDecisions} />);
    const paired = block("Experience-claim rate");
    expect(figure(paired, "Estimate")).toContain("0.001852");
    expect(figure(paired, "Estimate")).toContain("Mean difference, quantum minus pseudo-random");
    expect(figure(paired, "99% interval")).toBe("[-0.010773, 0.014477]");
    expect(figure(paired, "p")).toBe("1");
    expect(figure(paired, "Holm-adjusted p")).toContain("Corrected over 3 difference tests; the level is α = 0.01.");
    const ks = block("Token sampling residuals (KS)");
    expect(figure(ks, "Estimate")).toContain("0.030556");
    expect(figure(ks, "Estimate")).toContain("KS statistic");
    expect(figure(ks, "99% interval")).toBe("This test reports no interval.");
    expect(figure(ks, "p")).toBe("0.688683");
    expect(figure(block("Token sequence divergence (MMD)"), "Estimate")).toContain("-0.000871");
  });

  it("shows the margin and the equivalence test only where a margin is registered", () => {
    render(<Verdicts table={designedDecisions} />);
    const paired = block("Experience-claim rate");
    expect(figure(paired, "Registered margin")).toBe("±0.05");
    // a p the table rounded to 0 is printed as smaller than its last place
    expect(figure(paired, "Equivalence p")).toBe("< 0.000001");
    expect(figure(paired, "Holm-adjusted equivalence p")).toBe("< 0.000001Corrected over 1 equivalence test; the level is α = 0.01.");
    const ks = block("Token sampling residuals (KS)");
    expect(figure(ks, "Registered margin")).toBe("None. No equivalence test applies to this outcome.");
    expect(within(ks).queryByText("Equivalence p")).not.toBeInTheDocument();
  });

  it("reports planned sensitivity and seed noise beside the verdict", () => {
    render(<Verdicts table={designedDecisions} />);
    const paired = block("Experience-claim rate");
    expect(figure(paired, "Planned sensitivity")).toBe("0.114925In the units of the outcome; 1.090274 standard deviations. The smallest difference the plan could detect with 80% power.");
    expect(figure(paired, "Above seed noise")).toContain("No: the estimate is not larger than every seed-against-seed difference");
    expect(figure(paired, "Above seed noise")).toContain("It is not a condition.");
    expect(figure(block("Token sampling residuals (KS)"), "Planned sensitivity")).toBe("Unknown: no closed-form power for this test.");
  });

  it("states each gate as passed or failed in words", () => {
    render(<Verdicts table={designedDecisions} />);
    const article = block("Experience-claim rate");
    const negative = gate(article, /^Negative control/);
    expect(negative).toHaveClass("results-gate--passed");
    expect(negative).toHaveTextContent("Passed");
    expect(negative).toHaveTextContent("No seed pair differs after Holm correction (3 seed pairs tested); smallest adjusted p = 1.");
    const positive = gate(article, /^Positive control \(biased sampler, ε = 0\.03\)/);
    expect(positive).toHaveTextContent("Passed");
    expect(positive).toHaveTextContent("The assay detected the known sampler bias, p = 0.0006.");
    expect(gate(block("Token sampling residuals (KS)"), /^Positive control/)).toHaveTextContent("The assay detected the known sampler bias, p < 0.000001.");
    const complete = gate(article, /^Completeness/);
    expect(complete).toHaveTextContent("Passed");
    expect(complete).toHaveTextContent("100% of the planned data was analysed; at least 90% is required.");
  });

  it("gives the reason of an inconclusive verdict", () => {
    render(<Verdicts table={decisionsWith([ksRow, positiveFailedRow, noClaimRow].map((row, i) => ({ ...row, hypothesis_id: `H${i + 1}`, label: `Outcome ${i + 1}` })))} />);
    const untestable = block("Outcome 1");
    expect(within(untestable).getByText("Inconclusive")).toHaveClass("results-stamp--inconclusive");
    expect(untestable).toHaveTextContent("Why inconclusive: this test has no equivalence margin. equivalence_not_testable");
    const failed = block("Outcome 2");
    expect(failed).toHaveTextContent("Why inconclusive: the positive control was not detected. positive_control_failed");
    expect(gate(failed, /^Positive control/)).toHaveClass("results-gate--failed");
    expect(gate(failed, /^Positive control/)).toHaveTextContent("FailedThe assay did not detect the known sampler bias, p = 0.4127.");
    const unclaimed = block("Outcome 3");
    expect(unclaimed).toHaveTextContent("Why inconclusive: the study registered no equivalence claim. equivalence_not_claimed");
    const absent = gate(unclaimed, /^Positive control \(biased sampler\)$/);
    expect(absent).toHaveClass("results-gate--none");
    expect(absent).toHaveTextContent("Not registered");
    expect(absent).toHaveTextContent("This study registered no positive control, so no hypothesis can resolve to equivalent.");
  });

  it("gives the reason of an invalid verdict and fails the gate that caused it", () => {
    render(<Verdicts table={decisionsWith([{ ...incompleteRow, label: "Incomplete outcome" }, { ...negativeControlRow, hypothesis_id: "H4", label: "Noisy outcome" }], { study_verdict: "invalid" })} />);
    const incomplete = block("Incomplete outcome");
    expect(within(incomplete).getByText("Invalid")).toHaveClass("results-stamp--invalid");
    expect(incomplete).toHaveTextContent("Why invalid: too little of the planned data was analysed. incomplete");
    expect(within(incomplete).getByText(incompleteRow.wording)).toBeVisible();
    expect(gate(incomplete, /^Completeness/)).toHaveClass("results-gate--failed");
    expect(gate(incomplete, /^Completeness/)).toHaveTextContent("Failed75% of the planned data was analysed; at least 90% is required.");
    expect(gate(incomplete, /^Negative control/)).toHaveClass("results-gate--passed");
    const noisy = block("Noisy outcome");
    expect(noisy).toHaveTextContent("Why invalid: two seeds of the same pseudo-random generator differed, or a seed control is missing. negative_control");
    expect(gate(noisy, /^Negative control/)).toHaveClass("results-gate--failed");
    // 0.0027 is below the level, so a pair does differ: the gate says that, and nothing about a missing pair
    expect(gate(noisy, /^Negative control/).querySelector(".results-gate__detail")?.textContent).toBe("At least one of the 3 seed pairs differs after Holm correction; smallest adjusted p = 0.0027.");
    expect(gate(noisy, /^Completeness/)).toHaveClass("results-gate--passed");
  });

  it("says in plain words when the seed control could not have failed", () => {
    const noisy = { ...negativeControlRow, hypothesis_id: "H4", label: "Noisy short outcome", negative_control: { ...negativeControlRow.negative_control, can_fail: false } };
    render(<Verdicts table={decisionsWith([{ ...cannotFailRow, label: "Short outcome" }, noisy], { study_verdict: "invalid" })} />);
    const short = block("Short outcome");
    expect(within(short).getByText("Invalid")).toHaveClass("results-stamp--invalid");
    expect(short).toHaveTextContent("Why invalid: the seed-against-seed control had too few prompts or permutations to be able to fail, so it shows nothing. negative_control_cannot_fail");
    const control = gate(short, /^Negative control/);
    expect(control).toHaveClass("results-gate--failed");
    expect(control).toHaveTextContent("Failed");
    // no pair differed, and the gate does not say that one did
    expect(control).toHaveTextContent("No seed pair differs after Holm correction (3 seed pairs tested); smallest adjusted p = 1. But the control could not have failed. At least one seed-pair test had too few prompts or permutations to be able to return a difference at the corrected level. A control that could not have failed shows nothing.");
    expect(control.textContent).not.toMatch(/(The seed pair|of the \d+ seed pairs) differs|not computed|does not record/);
    expect(gate(short, /^Completeness/)).toHaveClass("results-gate--passed");
    // a control that differed and could not have failed either says both
    expect(gate(block("Noisy short outcome"), /^Negative control/)).toHaveTextContent("At least one of the 3 seed pairs differs after Holm correction; smallest adjusted p = 0.0027. In addition, the control could not have failed. At least one seed-pair test had too few prompts or permutations to be able to return a difference at the corrected level.");
    // a table written before the question was asked has no can_fail, and nothing is said about it
    cleanup();
    render(<Verdicts table={decisionsWith([{ ...negativeControlRow, negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.0027 } }], { study_verdict: "invalid" })} />);
    expect(gate(block("Experience-claim rate"), /^Negative control/)).not.toHaveTextContent("could not have failed");
  });

  it("shows an unavailable hypothesis without inventing numbers", () => {
    render(<Verdicts table={decisionsWith([unavailableRow])} />);
    const article = block("Experience-claim rate");
    expect(within(article).getByText("Unavailable")).toHaveClass("results-stamp--unavailable");
    expect(article).toHaveTextContent("Why unavailable: the required data were not produced. not_produced");
    for (const term of ["Estimate", "99% interval", "p", "Holm-adjusted p", "Equivalence p"]) expect(figure(article, term)).toMatch(/^Not computed\./);
    expect(figure(article, "Above seed noise")).toContain("Not reported.");
    expect(gate(article, /^Negative control/)).toHaveTextContent("FailedNone of the 3 seed pairs planned was computed, so the control is missing.");
    expect(gate(article, /^Positive control/)).toHaveTextContent("Not runThe registered positive control produced no comparison.");
    expect(gate(article, /^Completeness/)).toHaveTextContent("Not assessedThe comparison was not computed.");
    // the p = 1 that holds the family slot is never shown as a result
    expect(within(article).queryByText("1")).not.toBeInTheDocument();
  });

  it("stamps an effect and an effect within the margin differently", () => {
    render(<Verdicts table={decisionsWith([{ ...effectRow, label: "Clear outcome" }, { ...effectWithinMarginRow, hypothesis_id: "H4", label: "Small outcome" }], { study_verdict: "effect" })} />);
    const clear = block("Clear outcome");
    expect(within(clear).getByText("Effect")).toHaveClass("results-stamp--effect");
    expect(within(clear).getByText(effectRow.wording)).toBeVisible();
    expect(figure(clear, "Holm-adjusted p")).toContain("0.0003");
    expect(figure(clear, "Above seed noise")).toContain("Yes: the estimate is larger than every seed-against-seed difference");
    const small = block("Small outcome");
    expect(within(small).getByText("Effect within margin")).toHaveClass("results-stamp--effect_within_margin");
    expect(figure(small, "Holm-adjusted equivalence p")).toContain("0.000041");
  });
});

// ── the negative-control gate against the rows the script can write ──

/** One planned seed pair after an analysis: not computed, or its Holm-adjusted p and whether its test could have failed. */
type SeedPair = null | { adjusted: number; canFail: boolean };
type Earlier = "not_produced" | "incomplete" | null;
/** What is true of the seed pairs behind a row. The table keeps only part of it. */
interface Truth { planned: number; computed: number; differs: boolean; missing: boolean; cannotFail: boolean }
type ControlRow = Pick<DecisionRow, "negative_control" | "reason">;

const round6 = (value: number) => Number(value.toFixed(6));

/**
 * The seed-control part of a row as `apply` and `resolve` of
 * `analysis/decisions.py` write it: `clean`, `can_fail`, `passed`, the
 * smallest adjusted p rounded as the table rounds, and the first reason of
 * matrix rows 1 to 3 that applies. `earlier` is a row of the matrix that
 * matched before the seed control was looked at. What this returns is checked
 * against rows the script itself wrote (`WRITTEN_BY_THE_SCRIPT`).
 */
function written(pairs: readonly SeedPair[], earlier: Earlier, alpha: number): { row: ControlRow; truth: Truth } {
  const produced = pairs.flatMap((pair) => (pair === null ? [] : [pair.adjusted]));
  const clean = produced.length > 0 && produced.length === pairs.length && Math.min(...produced) >= alpha;
  const canFail = pairs.every((pair) => pair === null || pair.canFail);
  const reason: DecisionRow["reason"] = earlier ?? (!clean ? "negative_control" : !canFail ? "negative_control_cannot_fail" : "equivalence_not_claimed");
  return {
    row: { reason, negative_control: { passed: clean && canFail, n_pairs: pairs.length, min_p_adjusted: produced.length > 0 ? round6(Math.min(...produced)) : null, can_fail: canFail } },
    truth: { planned: pairs.length, computed: produced.length, differs: produced.some((p) => p < alpha), missing: produced.length < pairs.length, cannotFail: !canFail },
  };
}

/** Every way up to three planned seed pairs can come out. The table keeps no order, so none is repeated in another order. */
function everyOutcome(alpha: number): SeedPair[][] {
  // well below the level, just below it (the table rounds it up to the level), at it, above it
  const adjusted = [0.0027, alpha - 2e-7, alpha, 0.4, 1];
  const states: SeedPair[] = [null, ...adjusted.flatMap((p) => [{ adjusted: p, canFail: true }, { adjusted: p, canFail: false }])];
  const out: SeedPair[][] = [[]];
  states.forEach((a, i) => {
    out.push([a]);
    states.slice(i).forEach((b, j) => {
      out.push([a, b]);
      states.slice(i + j).forEach((c) => out.push([a, b, c]));
    });
  });
  return out;
}

/** What a sentence of the gate asserts, and when that is true. A sentence none of these matches fails the test. */
const CLAIMS: readonly { says: RegExp; holds: (truth: Truth) => boolean; cause?: true; open?: "differs" | "missing" }[] = [
  { says: /^No seed pair differs after Holm correction \(\d+ seed pairs? tested\)/, holds: (t) => !t.differs && !t.missing && t.computed > 0 },
  { says: /^No seed pair that was computed differs after Holm correction/, holds: (t) => !t.differs && t.computed > 0 },
  { says: /^(The seed pair|At least one of the \d+ seed pairs) differs after Holm correction/, holds: (t) => t.differs, cause: true },
  { says: /^At least one of the \d+ seed pairs planned was not computed, so the control is incomplete\.$/, holds: (t) => t.missing, cause: true },
  { says: /^Either a seed pair differs after Holm correction or at least one of the \d+ seed pairs planned was not computed\.$/, holds: (t) => t.differs || t.missing, cause: true },
  { says: /^(But the|In addition, the|The) control could not have failed\.$/, holds: (t) => t.cannotFail, cause: true },
  { says: /^At least one seed-pair test had too few prompts or permutations to be able to return a difference at the corrected level\.$/, holds: (t) => t.cannotFail },
  { says: /^A control that could not have failed shows nothing\.$/, holds: (t) => t.cannotFail },
  { says: /^No seed pair was planned for this comparison, so the control is missing\.$/, holds: (t) => t.planned === 0, cause: true },
  { says: /^None of the \d+ seed pairs? planned was computed, so the control is missing\.$/, holds: (t) => t.planned > 0 && t.computed === 0, cause: true },
  { says: /^The table does not show which: the smallest adjusted p = \S+ is at the level α = \S+ after rounding\.$/, holds: () => true, open: "differs" },
  { says: /^The smallest adjusted p = \S+ is at the level α = \S+ after rounding, so the table does not show whether a seed pair differs after Holm correction\.$/, holds: () => true, open: "differs" },
  { says: /^The table does not record whether all \d+ seed pairs planned were computed\.$/, holds: () => true, open: "missing" },
];

const sentencesOf = (text: string) => text.split(/(?<=\.)\s+(?=[A-Z])/);

const OK: SeedPair = { adjusted: 1, canFail: true }, SHORT: SeedPair = { adjusted: 1, canFail: false };
const CANNOT_FAIL = "At least one seed-pair test had too few prompts or permutations to be able to return a difference at the corrected level.";
const UNSETTLED = `No seed pair that was computed differs after Holm correction; smallest adjusted p = 1. But the control could not have failed. ${CANNOT_FAIL} A control that could not have failed shows nothing. The table does not record whether all 3 seed pairs planned were computed.`;

/**
 * Rows of H3 of the designed Study 1 plan (α = 0.05, three seed pairs, nine in the Holm family) as
 * `decisions.apply` wrote them and `analysis.tables.rounded` rounded them, for the seed-pair outcome named.
 * Each stands beside the same outcome as `written` models it and the sentence the gate has to print.
 */
const WRITTEN_BY_THE_SCRIPT: readonly { name: string; pairs: SeedPair[]; earlier: Earlier; row: ControlRow; text: string }[] = [
  {
    name: "a run that stopped early, whose seed pairs do not differ and could not have failed",
    pairs: [SHORT, SHORT, SHORT], earlier: "incomplete",
    row: { reason: "incomplete", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false } },
    text: UNSETTLED,
  },
  {
    // the same row as the one above: the table cannot tell the two apart, so one sentence has to be true of both
    name: "a run that stopped early with one seed pair not computed, whose other pairs could not have failed",
    pairs: [null, SHORT, SHORT], earlier: "incomplete",
    row: { reason: "incomplete", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false } },
    text: UNSETTLED,
  },
  {
    name: "a run that stopped early with one seed pair not computed",
    pairs: [null, OK, OK], earlier: "incomplete",
    row: { reason: "incomplete", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: true } },
    text: "No seed pair that was computed differs after Holm correction; smallest adjusted p = 1. At least one of the 3 seed pairs planned was not computed, so the control is incomplete.",
  },
  {
    name: "a run that stopped early in which a seed pair differs",
    pairs: [{ adjusted: 9e-7, canFail: true }, OK, OK], earlier: "incomplete",
    row: { reason: "incomplete", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.000001, can_fail: true } },
    text: "At least one of the 3 seed pairs differs after Holm correction; smallest adjusted p = 0.000001.",
  },
  {
    name: "a run that stopped early whose smallest adjusted p is exactly the level, with pairs that could not have failed",
    pairs: [{ adjusted: 0.05, canFail: false }, SHORT, SHORT], earlier: "incomplete",
    row: { reason: "incomplete", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.05, can_fail: false } },
    text: `The smallest adjusted p = 0.05 is at the level α = 0.05 after rounding, so the table does not show whether a seed pair differs after Holm correction. The control could not have failed. ${CANNOT_FAIL} A control that could not have failed shows nothing. The table does not record whether all 3 seed pairs planned were computed.`,
  },
  {
    name: "a comparison that was not produced, whose seed pairs do not differ and could not have failed",
    pairs: [SHORT, SHORT, SHORT], earlier: "not_produced",
    row: { reason: "not_produced", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false } },
    text: UNSETTLED,
  },
  {
    name: "a comparison that was not produced, with a clean seed control",
    pairs: [OK, OK, OK], earlier: "not_produced",
    row: { reason: "not_produced", negative_control: { passed: true, n_pairs: 3, min_p_adjusted: 1, can_fail: true } },
    text: "No seed pair differs after Holm correction (3 seed pairs tested); smallest adjusted p = 1.",
  },
  {
    name: "a comparison that was not produced, with no seed pair computed",
    pairs: [null, null, null], earlier: "not_produced",
    row: { reason: "not_produced", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: null, can_fail: true } },
    text: "None of the 3 seed pairs planned was computed, so the control is missing.",
  },
  {
    name: "a complete run whose seed pairs do not differ and could not have failed",
    pairs: [SHORT, SHORT, SHORT], earlier: null,
    row: { reason: "negative_control_cannot_fail", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false } },
    text: `No seed pair differs after Holm correction (3 seed pairs tested); smallest adjusted p = 1. But the control could not have failed. ${CANNOT_FAIL} A control that could not have failed shows nothing.`,
  },
  {
    name: "a complete run in which a seed pair differs",
    pairs: [{ adjusted: 9e-7, canFail: true }, OK, OK], earlier: null,
    row: { reason: "negative_control", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.000001, can_fail: true } },
    text: "At least one of the 3 seed pairs differs after Holm correction; smallest adjusted p = 0.000001.",
  },
  {
    name: "a complete run in which a seed pair differs and another could not have failed",
    pairs: [{ adjusted: 9e-7, canFail: true }, SHORT, OK], earlier: null,
    row: { reason: "negative_control", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.000001, can_fail: false } },
    text: `At least one of the 3 seed pairs differs after Holm correction; smallest adjusted p = 0.000001. In addition, the control could not have failed. ${CANNOT_FAIL}`,
  },
  {
    name: "a complete run with one seed pair not computed",
    pairs: [null, OK, OK], earlier: null,
    row: { reason: "negative_control", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: true } },
    text: "No seed pair that was computed differs after Holm correction; smallest adjusted p = 1. At least one of the 3 seed pairs planned was not computed, so the control is incomplete.",
  },
  {
    name: "a complete run with one seed pair not computed, whose other pairs could not have failed",
    pairs: [null, SHORT, SHORT], earlier: null,
    row: { reason: "negative_control", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false } },
    text: `No seed pair that was computed differs after Holm correction; smallest adjusted p = 1. At least one of the 3 seed pairs planned was not computed, so the control is incomplete. In addition, the control could not have failed. ${CANNOT_FAIL}`,
  },
  {
    name: "a complete run with no seed pair computed",
    pairs: [null, null, null], earlier: null,
    row: { reason: "negative_control", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: null, can_fail: true } },
    text: "None of the 3 seed pairs planned was computed, so the control is missing.",
  },
  {
    name: "a complete run in which a seed pair differs by an adjusted p the table rounds up to the level",
    pairs: [{ adjusted: 0.05 - 2e-7, canFail: true }, OK, OK], earlier: null,
    row: { reason: "negative_control", negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 0.05, can_fail: true } },
    text: "Either a seed pair differs after Holm correction or at least one of the 3 seed pairs planned was not computed. The table does not show which: the smallest adjusted p = 0.05 is at the level α = 0.05 after rounding.",
  },
  {
    name: "a complete run with a clean seed control",
    pairs: [OK, OK, OK], earlier: null,
    row: { reason: "equivalence_not_claimed", negative_control: { passed: true, n_pairs: 3, min_p_adjusted: 1, can_fail: true } },
    text: "No seed pair differs after Holm correction (3 seed pairs tested); smallest adjusted p = 1.",
  },
];

describe("the negative-control gate", () => {
  it.each(WRITTEN_BY_THE_SCRIPT)("says what is true of $name", ({ pairs, earlier, row, text }) => {
    // the model used below writes the row the script wrote for this outcome
    expect(written(pairs, earlier, 0.05).row).toEqual(row);
    expect(negativeControlText(row, { alpha: 0.05, precision: 6 })).toEqual({ passed: row.negative_control.passed, text });
  });

  it.each([0.01, 0.05])("says nothing false of any row the script can write at α = %s", (alpha) => {
    const cases = everyOutcome(alpha).flatMap((pairs) => ([null, "incomplete", "not_produced"] as const).map((earlier) => written(pairs, earlier, alpha)));
    // The table does not keep everything, so several outcomes share a row and a sentence has to be true of all of them.
    const sharing = new Map<string, Truth[]>();
    for (const { row, truth } of cases) sharing.set(JSON.stringify(row), [...(sharing.get(JSON.stringify(row)) ?? []), truth]);
    expect(sharing.size).toBeGreaterThan(30);

    for (const { row, truth } of cases) {
      const { passed, text } = negativeControlText(row, { alpha, precision: 6 });
      const context = `${JSON.stringify(row)} for ${JSON.stringify(truth)}: ${text}`;
      expect(passed, context).toBe(row.negative_control.passed);
      const claims = sentencesOf(text).map((sentence) => {
        const claim = CLAIMS.find((entry) => entry.says.test(sentence));
        if (!claim) throw new Error(`a sentence the test does not know: “${sentence}” in ${context}`);
        return claim;
      });
      for (const claim of claims) expect(claim.holds(truth), `${String(claim.says)} · ${context}`).toBe(true);
      // a control that did not pass says why, and one that passed gives no reason for failing
      expect(claims.some((claim) => claim.cause), context).toBe(!passed);
      // "the table does not show" only where it does not: the same row is also written for an outcome with the other answer
      const same = sharing.get(JSON.stringify(row))!;
      for (const claim of claims) {
        const open = claim.open;
        if (!open) continue;
        expect(new Set(same.map((other) => other[open])).size, `${String(claim.says)} · ${context}`).toBe(2);
      }
    }
  });

  it("prints the sentence on the page, under a failed gate, for the row of a run that stopped early", () => {
    const stopped: DecisionRow = { ...incompleteRow, negative_control: { passed: false, n_pairs: 3, min_p_adjusted: 1, can_fail: false } };
    render(<Verdicts table={decisionsWith([stopped], { study_verdict: "invalid" })} />);
    const control = gate(block(stopped.label), /^Negative control/);
    expect(control).toHaveClass("results-gate--failed");
    expect(control).toHaveTextContent("Failed");
    expect(control.querySelector(".results-gate__detail")?.textContent).toBe(UNSETTLED);
    // neither of these is known of this row, and the page used to say that one of them was the case
    expect(control.textContent).not.toMatch(/(A seed pair|The seed pair|of the \d+ seed pairs) differs|is missing|was not computed, so/);
  });

  it("speaks of one planned seed pair in the singular, and knows that it was computed", () => {
    const one = (control: DecisionRow["negative_control"], reason: DecisionRow["reason"]) => negativeControlText({ negative_control: control, reason }, { alpha: 0.01, precision: 6 }).text;
    expect(one({ passed: false, n_pairs: 1, min_p_adjusted: 0.0027, can_fail: true }, "negative_control")).toBe("The seed pair differs after Holm correction; smallest adjusted p = 0.0027.");
    expect(one({ passed: false, n_pairs: 1, min_p_adjusted: 1, can_fail: false }, "incomplete")).toBe(`No seed pair differs after Holm correction (1 seed pair tested); smallest adjusted p = 1. But the control could not have failed. ${CANNOT_FAIL} A control that could not have failed shows nothing.`);
    // the one pair was computed and the control is not clean, so it differs, by a p the table rounded up to the level
    expect(one({ passed: false, n_pairs: 1, min_p_adjusted: 0.01, can_fail: true }, "negative_control")).toBe("The seed pair differs after Holm correction; smallest adjusted p = 0.01, which was below the level α = 0.01 before the table rounded it.");
    expect(one({ passed: false, n_pairs: 0, min_p_adjusted: null, can_fail: true }, "negative_control")).toBe("No seed pair was planned for this comparison, so the control is missing.");
  });
});

describe("the rules line", () => {
  it("names the claim, the matrix, the level and the families, and links to the rules", () => {
    render(<Verdicts table={designedDecisions} preregHref="/studies/demo/prereg#prereg-decision-rules" />);
    const rules = screen.getByText(/A script resolved each hypothesis under decision matrix version 1, at α = 0\.01/);
    expect(rules).toHaveTextContent("The study registered a claim of difference, or of absence within a margin.");
    expect(rules).toHaveTextContent("3 difference tests, 1 equivalence test, 9 seed-pair tests");
    expect(screen.getByRole("link", { name: "Read the decision rules in the pre-registration" })).toHaveAttribute("href", "/studies/demo/prereg#prereg-decision-rules");
  });

  it("says when the study registered a difference claim only", () => {
    render(<Verdicts table={decisionsWith([noClaimRow], { claim: "difference", sensitivity_epsilon: null })} />);
    expect(screen.getByText(/The study registered a claim of difference only\./)).toBeVisible();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("the words for a verdict", () => {
  it("give every verdict its own label and glyph, so colour is never the only channel", () => {
    const stamps = Object.values(VERDICT_STAMP);
    expect(new Set(stamps.map((stamp) => stamp.label)).size).toBe(6);
    expect(new Set(stamps.map((stamp) => stamp.glyph)).size).toBe(6);
    expect(stamps.every((stamp) => stamp.glyph.length === 1)).toBe(true);
  });

  it("exist for every value the generated contract allows, and for no other", () => {
    const fields: [string, string[], Readonly<Record<string, unknown>>][] = [
      ["reason", enumOf(ROW_SCHEMA.reason), REASON_TEXT],
      ["verdict", enumOf(ROW_SCHEMA.verdict), VERDICT_STAMP],
      ["test", enumOf(ROW_SCHEMA.test), TEST_TEXT],
      ["study_verdict", enumOf(decisionsSchema.properties.study_verdict), STUDY_VERDICT_TEXT],
      ["non_confirmatory_reason", enumOf(decisionsSchema.properties.non_confirmatory_reason), NOT_CONFIRMATORY_TEXT],
    ];
    for (const [field, values, wording] of fields) {
      // an empty list would mean the schema moved and this test reads nothing
      expect(values.length, field).toBeGreaterThan(1);
      expect(Object.keys(wording).sort(), `wording for ${field}`).toEqual([...values].sort());
    }
    // every reason the decision matrix can record is one of them
    const recorded = designFixture.verdict_rules.flatMap((rule) => rule.reasons);
    expect([...recorded].sort()).toEqual(enumOf(ROW_SCHEMA.reason).sort());
  });

  it.each(enumOf(ROW_SCHEMA.reason))("say why in words for the reason %s", (reason) => {
    const text = (REASON_TEXT as Readonly<Record<string, string | undefined>>)[reason];
    // words, not the code: a clause that reads after "Why invalid:" and takes the full stop the page adds
    expect(text).toMatch(/^[a-z][^_]{15,}[a-z)]$/);
    const verdict = designFixture.verdict_rules.find((rule) => rule.reasons.includes(reason))?.verdict as DecisionRow["verdict"];
    render(<Verdicts table={decisionsWith([{ ...equivalentRow, verdict, reason: reason as DecisionRow["reason"] }])} />);
    const why = block("Experience-claim rate").querySelector(".results-verdict__reason")?.textContent;
    expect(why).toBe(`Why ${VERDICT_STAMP[verdict].label.toLowerCase()}: ${text}. ${reason}`);
  });

  it("use the reason texts the pre-registration prints", () => {
    const registered = designFixture.inconclusive_reason_text as Record<string, string>;
    expect(Object.keys(registered).length).toBe(6);
    for (const [reason, text] of Object.entries(registered)) expect(REASON_TEXT[reason as NonNullable<DecisionRow["reason"]>]).toBe(text);
  });

  it("print numbers as stored, a rounded-away p as below the last place, and shares as percentages", () => {
    expect(pText(0.688683, 6)).toBe("0.688683");
    expect(pText(1, 6)).toBe("1");
    expect(pText(0, 6)).toBe("< 0.000001");
    expect(pText(0, 4)).toBe("< 0.0001");
    expect(pRelation(0.0006, 6)).toBe("p = 0.0006");
    expect(pRelation(0, 6)).toBe("p < 0.000001");
    expect(percentText(0.99)).toBe("99%");
    expect(percentText(0.995)).toBe("99.5%");
    expect(percentText(0.57)).toBe("57%");
    expect(percentText(1)).toBe("100%");
  });
});
