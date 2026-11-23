import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cards, type HypothesisPlan } from "@qr-evals/contracts";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach } from "vitest";
import { setCardItems, setClaim, setSensitivityEpsilon } from "../../../lib/config-edits";
import { studyOneTemplate, type WizardStudyConfig } from "../../../lib/config-store";
import { draftHypotheses, familySizes } from "../../../lib/design";
import { UNREACHABLE_TEXT as MAP_UNREACHABLE_TEXT } from "../../../lib/map/graph";
import { rate, REC_STATES } from "../../../lib/recommendations";
import { VERDICT_STAMP } from "../../results/verdict-text";
import { FixedRules, fixedRules } from "./FixedRules";
import { HypothesisCard, RatingMark } from "./HypothesisCard";
import { NO_CLOSED_FORM, WORST_CASE, detectableWords, marginWords, plannedWords, recommendedWords, sampleSizeWords, statementWords, testWords } from "./hypothesis-text";
import { RecLegend } from "./RecLegend";
import { SampleSizeTable } from "./SampleSizeTable";
import { HypothesisVerdicts, VerdictStamp } from "./VerdictStamp";
import { UNREACHABLE_TEXT, VERDICT_MEANING, VERDICT_ORDER, verdictReach } from "./verdicts";

afterEach(cleanup);

const template = studyOneTemplate();
const [ks, mmd, probe] = draftHypotheses(template, cards) as [HypothesisPlan, HypothesisPlan, HypothesisPlan];

/** Study 1 with its ε 0.10 arm switched on and the absence of a difference claimed. */
function withEquivalence(): WizardStudyConfig {
  const arms = template.arms.map((arm) => (arm.source === "prng_epsilon" && arm.epsilon === 0.1 ? { ...arm, availability: "available" as const, unavailable_reason: null } : arm));
  return setClaim(setSensitivityEpsilon({ ...template, arms }, 0.1), "difference_or_equivalence");
}

describe("verdictReach", () => {
  it("lists every verdict once, findings first, in the words of the results page", () => {
    const reach = verdictReach(probe, "difference");
    expect(reach.map((entry) => entry.verdict)).toEqual(VERDICT_ORDER);
    expect(reach.map((entry) => entry.label)).toEqual(VERDICT_ORDER.map((verdict) => VERDICT_STAMP[verdict].label));
    expect(Object.keys(VERDICT_MEANING).sort()).toEqual([...VERDICT_ORDER].sort());
  });

  it("says why Study 1 cannot return equivalent, and stops saying it once a control is detected and the claim is made", () => {
    const equivalent = (hypothesis: HypothesisPlan, claim: string) => verdictReach(hypothesis, claim).find((entry) => entry.verdict === "equivalent")!;
    expect(equivalent(probe, "difference")).toMatchObject({ reachable: false, why: "equivalence_not_claimed", reason: UNREACHABLE_TEXT.equivalence_not_claimed });
    const claimed = withEquivalence();
    const [, , designedProbe] = draftHypotheses(claimed, cards);
    expect(equivalent(designedProbe!, "difference_or_equivalence")).toMatchObject({ reachable: true, reason: null });
    // A test on pooled tokens has no paired difference, so it never has a margin to be equivalent within.
    const [designedKs] = draftHypotheses(claimed, cards);
    expect(equivalent(designedKs!, "difference_or_equivalence")).toMatchObject({ reachable: false, why: "equivalence_not_testable" });
  });

  it("rules out an effect below the prompt floor", () => {
    const [, , small] = draftHypotheses(setCardItems(template, "self_referential_probe", 6), cards);
    const reach = verdictReach(small!, "difference");
    expect(reach.find((entry) => entry.verdict === "effect")).toMatchObject({ reachable: false, reason: "too few prompts to reach significance at any effect size" });
    // Six prompts are also too few for a seed pair to fail its control, so the hypothesis could only come out
    // invalid: no finding is within reach, and neither is 'inconclusive'.
    expect(reach.find((entry) => entry.verdict === "inconclusive")).toMatchObject({
      reachable: false, reason: "fewer prompts than the seed-against-seed control is asked for, so the hypothesis is invalid",
    });
    expect(reach.find((entry) => entry.verdict === "invalid")).toMatchObject({ reachable: true });
  });
});

describe("VerdictStamp", () => {
  it("is text with a state class and a drawn outline, the same for the same verdict", () => {
    const { container } = render(<><VerdictStamp verdict="effect" /><VerdictStamp verdict="effect" state="unreachable" /><VerdictStamp verdict="equivalent" state="reached" /></>);
    const [open, closed, reached] = [...container.querySelectorAll(".verdict-stamp")];
    expect(open).toHaveClass("verdict-stamp--effect", "is-reachable");
    expect(open).toHaveTextContent("Effect");
    expect(open?.querySelector(".verdict-stamp__glyph")).toHaveTextContent(VERDICT_STAMP.effect.glyph);
    expect(closed).toHaveClass("is-unreachable");
    // The state is said as well as drawn.
    expect(closed).toHaveTextContent("Effect (unreachable)");
    expect(reached).toHaveClass("verdict-stamp--equivalent", "is-reached");
    const paths = [...container.querySelectorAll(".verdict-stamp__outline path")].map((path) => path.getAttribute("d"));
    expect(paths[0]).toMatch(/^M[\d.-]+ [\d.-]+ ?C/);
    expect(paths[0]).toBe(paths[1]);
    expect(paths[2]).not.toBe(paths[0]);
    expect(container.querySelector(".verdict-stamp__outline")).toHaveAttribute("aria-hidden", "true");
  });

  it("draws a hypothesis's verdicts as a row and writes out the reason for each it cannot reach", () => {
    render(<HypothesisVerdicts hypothesis={ks} claim="difference" />);
    const row = within(screen.getByRole("list", { name: "Verdicts H1 can end with" })).getAllByRole("listitem");
    expect(row).toHaveLength(6);
    const closed = row.filter((item) => item.querySelector(".is-unreachable")).map((item) => item.querySelector(".verdict-stamp")?.getAttribute("data-verdict"));
    expect(closed).toEqual(["effect_within_margin", "equivalent"]);
    const reasons = within(screen.getByRole("list", { name: "Verdicts H1 cannot reach" })).getAllByRole("listitem").map((item) => item.textContent);
    expect(reasons).toEqual([
      "Effect within margin: unreachable. This test has no equivalence margin.",
      "Equivalent: unreachable. The study registers no equivalence claim.",
    ]);
  });

  it("writes no list of reasons when every verdict can be reached", () => {
    const [, , designedProbe] = draftHypotheses(withEquivalence(), cards);
    render(<HypothesisVerdicts hypothesis={designedProbe!} claim="difference_or_equivalence" />);
    expect(document.querySelectorAll(".is-unreachable")).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Verdicts H3 cannot reach" })).not.toBeInTheDocument();
  });
});

describe("hypothesis in plain words", () => {
  it("names the test and its sidedness", () => {
    expect(testWords(probe)).toBe("Paired permutation test on the mean difference, with the prompt as the unit of analysis. Two-sided: a difference in either direction counts.");
    expect(testWords(mmd)).toContain("One-sided: only a divergence larger than chance counts.");
    expect(testWords(ks)).toContain("Kolmogorov–Smirnov");
  });

  it("says where a margin comes from, and why a test has none", () => {
    expect(marginWords(probe)).toBe("±0.05, the smallest effect of interest stated on the eval card.");
    expect(marginWords(ks)).toBe("None. This test has no paired difference a margin could apply to.");
    expect(marginWords({ ...probe, margin: 0.02, margin_source: "declared" })).toBe("±0.02, declared in this study.");
    expect(marginWords({ ...probe, margin: null, margin_source: "none" })).toBe("None. Without a margin this outcome cannot be reported as equivalent.");
  });

  it("counts planned data in prompts for a paired test and in generations for a pooled one", () => {
    expect(plannedWords(probe)).toBe(`30 prompts, ${new Intl.NumberFormat("en").format(probe.planned_pairs)} paired values (prompt, repetition and told cell).`);
    expect(ks.planned_units).toBeGreaterThan(1000);
    expect(plannedWords(ks)).toBe(`${new Intl.NumberFormat("en").format(ks.planned_units)} generations per arm, pooled token by token. Evals derived from the replies of another eval are not counted.`);
    expect(plannedWords(ks)).toMatch(/^\d{2},\d{3} generations/);
  });

  it("never claims a power for a pooled test", () => {
    for (const pooledTest of [ks, mmd]) {
      expect(pooledTest.sample_size.rating).toBe("unknown");
      expect(sampleSizeWords(pooledTest)).toBe(NO_CLOSED_FORM);
      expect(detectableWords(pooledTest)).toBe("Unknown: no closed-form power for this test.");
    }
    expect(sampleSizeWords(probe)).toMatch(/^30 prompts detect a difference of 0\.049 or more with 80% power\. A difference as large as the margin ±0\.05 is detected with \d+\.\d% power\.$/);
    expect(detectableWords(probe)).toMatch(/^0\.049 in the units of the outcome; \d+\.\d% power for a difference as large as the margin ±0\.05\.$/);
  });

  it("drops the id from the generated statement, which the card shows on its own", () => {
    expect(probe.statement.startsWith("H3: on ")).toBe(true);
    expect(statementWords(probe)).toBe(`On ${probe.statement.slice("H3: on ".length)}`);
  });
});

describe("HypothesisCard", () => {
  it("shows id, label, comparison, test, margin, planned data, rating and verdicts", () => {
    render(<HypothesisCard hypothesis={probe} claim="difference" />);
    const card = screen.getByRole("article", { name: "H3 Experience-claim rate" });
    expect(card).toHaveAttribute("data-hypothesis", "H3");
    expect(within(card).getByRole("heading", { level: 4 })).toHaveTextContent("H3 Experience-claim rate");
    expect(within(card).getByText(/qr-llm · Creative sampling/)).toBeInTheDocument();
    expect(within(card).getByText(statementWords(probe))).toBeInTheDocument();
    expect(within(card).getByText("Test").nextElementSibling).toHaveTextContent("Paired permutation test");
    expect(within(card).getByText("Margin").nextElementSibling).toHaveTextContent("±0.05");
    expect(within(card).getByText("Planned data").nextElementSibling).toHaveTextContent("30 prompts");
    const size = within(card).getByText("Sample size · preview").nextElementSibling as HTMLElement;
    expect(size.querySelector(".rating-mark")).toHaveClass("rec--sufficient");
    expect(size.querySelector(".rating-mark")).toHaveTextContent("sufficient");
    expect(within(card).getByRole("list", { name: "Verdicts H3 can end with" })).toBeInTheDocument();
  });

  it("rates a pooled test as unknown, never as enough, and labels a registered plan as no preview", () => {
    render(<HypothesisCard hypothesis={mmd} claim="difference" headingLevel={3} preview={false} />);
    const card = screen.getByRole("article", { name: /^H2 / });
    expect(within(card).getByRole("heading", { level: 3 })).toBeInTheDocument();
    const size = within(card).getByText("Sample size").nextElementSibling as HTMLElement;
    expect(size).toHaveTextContent("Unknown: no closed-form power for this test.");
    expect(size.querySelector(".rating-mark")).toHaveClass("rec--unknown");
    expect(card.querySelector(".rec--sufficient")).toBeNull();
    expect(within(card).queryByText("Sample size · preview")).not.toBeInTheDocument();
  });
});

describe("RatingMark", () => {
  it("is the chip's glyph and class with the rating in words", () => {
    const { container } = render(<RatingMark rating="insufficient" />);
    const mark = container.querySelector(".rating-mark");
    expect(mark).toHaveClass(REC_STATES.insufficient.className);
    expect(mark).toHaveTextContent(`${REC_STATES.insufficient.glyph}insufficient`);
    expect(mark?.tagName).toBe("SPAN");
  });
});

describe("SampleSizeTable", () => {
  it("has one row per hypothesis and says what the ratings rest on", () => {
    render(<SampleSizeTable hypotheses={[ks, mmd, probe]} />);
    const table = screen.getByRole("table", { name: "Sample size of each hypothesis, as a preview" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.getAttribute("data-hypothesis"))).toEqual(["H1", "H2", "H3"]);
    for (const row of rows.slice(0, 2)) {
      expect(within(row).getByText("Unknown: no closed-form power for this test.")).toBeInTheDocument();
      expect(row.querySelector(".rating-mark")).toHaveClass("rec--unknown");
      expect(row.querySelector(".rec--sufficient")).toBeNull();
      expect(row).toHaveTextContent("Not rated");
    }
    expect(rows[2]!.querySelector(".rating-mark")).toHaveClass("rec--sufficient");
    expect(rows[2]).toHaveTextContent("30 prompts");
    expect(rows[2]!.lastElementChild).toHaveTextContent(String(probe.sample_size.n_recommended));
    expect(screen.getByText(WORST_CASE)).toBeInTheDocument();
    expect(screen.getByText("Worst-case variance, no prompt-by-source heterogeneity; true power may be lower.")).toBeInTheDocument();
    expect(screen.getByText(/A rating is not a verdict and changes no decision rule\./)).toBeInTheDocument();
    expect(screen.getByText("Computed in this browser as a preview. The registered numbers are the server's.")).toBeInTheDocument();
  });

  it("says when more prompts are recommended than the eval has", () => {
    const short: HypothesisPlan = { ...probe, sample_size: { ...probe.sample_size, n_recommended: 117, reachable: false, rating: "insufficient" } };
    render(<SampleSizeTable hypotheses={[short]} preview={false} />);
    // Where the eval has no more prompts, the repetitions that make them enough are named, as in the pre-registration.
    expect(screen.getByText("117, more than the eval has. With k = 24, its prompts are enough")).toBeInTheDocument();
    expect(screen.queryByText(/Computed in this browser/)).not.toBeInTheDocument();
  });

  it("says so when no number of repetitions makes the eval's prompts enough, and says nothing of k for an outcome without a margin", () => {
    const hopeless: HypothesisPlan = { ...probe, sample_size: { ...probe.sample_size, n_recommended: 400, reachable: false, rating: "insufficient", k_recommended: null } };
    expect(recommendedWords(hopeless.sample_size)).toBe("400, more than the eval has. No number of repetitions the eval allows makes its prompts enough");
    expect(recommendedWords({ ...hopeless.sample_size, basis: "standardized_reference" })).toBe("400, more than the eval has");
    expect(recommendedWords(probe.sample_size)).toBe(String(probe.sample_size.n_recommended));
  });

  it("says that too few prompts for the seed-against-seed control can only end in invalid, in the pre-registration's words", () => {
    const few: HypothesisPlan = { ...probe, sample_size: { ...probe.sample_size, n: 8, n_floor: 6, control_floor: 10 } };
    expect(detectableWords(few)).toBe("None: with 8 prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the hypothesis can only resolve to invalid. At least 10 prompts are needed.");
    expect(sampleSizeWords(few)).toBe("With 8 prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the hypothesis can only resolve to invalid. At least 10 prompts are needed.");
    // The sentence is the pre-registration's (`_detectable` in prereg/render.py), and so is the reason a verdict is out of reach.
    const render = readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/prereg/render.py"), "utf-8").replace(/"\s*\n\s*f?"/g, "");
    expect(render).toContain("None: with {size.n} prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the hypothesis can only resolve to invalid. At least {size.control_floor} prompts are needed.");
    expect(render).toContain(`"${UNREACHABLE_TEXT.control_cannot_fail}"`);
    expect(UNREACHABLE_TEXT.control_cannot_fail).toBe(MAP_UNREACHABLE_TEXT.control_cannot_fail);
    expect(`${Object.values(UNREACHABLE_TEXT).join(" ")} ${Object.values(MAP_UNREACHABLE_TEXT).join(" ")}`).not.toMatch(/cannot fail|able to fail/);
    // The floor of the test itself comes first, and a hypothesis without a control floor is unchanged.
    expect(detectableWords({ ...few, sample_size: { ...few.sample_size, n: 4 } })).toMatch(/^None: 4 prompts cannot reach significance/);
    expect(detectableWords({ ...probe, sample_size: { ...probe.sample_size, control_floor: null } })).toBe(detectableWords({ ...probe, sample_size: { ...probe.sample_size, control_floor: 2 } }));
  });
});

describe("FixedRules", () => {
  const recs = rate(template, cards);

  it("lists each fixed rule once, with its value and its reason", () => {
    const rules = fixedRules(recs);
    expect(rules.map((rec) => rec.path)).toEqual([
      "fixed.test.tier0_pit_residuals/pit_ks_two_sample", "fixed.test.tier0_mmd_hamming/mmd_hamming", "fixed.test.self_referential_probe/experience_claim_rate",
      "fixed.correction", "fixed.ci_level", "fixed.power", "fixed.permutations", "fixed.completeness", "fixed.envelope",
    ]);
    expect(rules.every((rec) => rec.state === "locked")).toBe(true);

    const { container } = render(<FixedRules recs={recs} families={familySizes([ks, mmd, probe])} />);
    const details = container.querySelector("details.fixed-rules");
    expect(details).not.toHaveAttribute("open");
    expect(within(details as HTMLElement).getByText(/^Fixed by the harness/)).toBeInTheDocument();
    const row = (path: string) => container.querySelector(`[data-path="${path}"]`) as HTMLElement;
    expect(row("fixed.test.self_referential_probe/experience_claim_rate")).toHaveTextContent("Test of Experience-claim rate");
    expect(row("fixed.correction")).toHaveTextContent("Holm · m = 3");
    expect(row("fixed.ci_level")).toHaveTextContent("95%");
    expect(row("fixed.power")).toHaveTextContent("80%");
    expect(row("fixed.permutations")).toHaveTextContent("10000");
    expect(row("fixed.completeness")).toHaveTextContent("≥ 90%");
    expect(row("fixed.envelope")).toHaveTextContent("Amplifier zscore_mean, calibration 0, prefetch off.");
    expect(row("fixed.families")).toHaveTextContent("3 tests for a difference, 1 test for equivalence and 9 tests of seed against seed.");
    expect(container.querySelectorAll(".fixed-rules__list .rec--locked")).toHaveLength(rules.length);
  });

  it("draws nothing when there is no fixed rule to list", () => {
    const { container } = render(<FixedRules recs={recs.filter((rec) => !rec.path.startsWith("fixed."))} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("RecLegend", () => {
  it("names every state in words beside its mark", () => {
    const { container } = render(<RecLegend />);
    const entries = [...container.querySelectorAll(".rec-legend__list li")];
    expect(entries.map((entry) => entry.lastElementChild?.textContent)).toEqual(Object.values(REC_STATES).map((state) => state.legend));
    entries.forEach((entry, index) => expect(entry.querySelector(".rec")).toHaveClass(Object.values(REC_STATES)[index]!.className));
  });
});
