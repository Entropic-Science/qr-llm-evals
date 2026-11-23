import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cards } from "@qr-evals/contracts";
import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { studyOneTemplate } from "../../../lib/config-store";
import { getDocSlugs } from "../../../lib/markdown";
import { rate } from "../../../lib/recommendations";
import { FixedRules, RESULT_CHECKS_DOC, RESULT_CHECKS_TEXT, fixedRules } from "./FixedRules";

afterEach(cleanup);

describe("FixedRules: result checks", () => {
  const recs = rate(studyOneTemplate(), cards);

  it("says in one sentence what the harness checks, and links to the analysis plan", () => {
    const { container } = render(<FixedRules recs={recs} />);
    const row = container.querySelector('[data-path="fixed.result_checks"]') as HTMLElement;
    expect(within(row).getByText("Result checks")).toBeInTheDocument();
    expect(row).toHaveTextContent("The harness checks every reply, score and judgement as the run goes: it flags what looks wrong without excluding it from any analysis, and it pauses the run when something is plainly broken.");
    expect(row).toHaveTextContent(RESULT_CHECKS_TEXT);
    const link = within(row).getByRole("link", { name: 'See "Result checks" in the analysis plan.' });
    expect(link).toHaveAttribute("href", "/docs/ANALYSIS_PLAN");
    // It sits in the list and is not a rule with a value.
    expect(row.closest("dl")).toHaveClass("fixed-rules__list");
    expect(row.querySelector(".rec--locked")).toBeNull();
  });

  it("counts in the heading the rows it lists, the Holm families and the result checks included", () => {
    const plain = render(<FixedRules recs={recs} />);
    const listed = plain.container.querySelectorAll(".fixed-rules__list > div").length;
    expect(listed).toBe(fixedRules(recs).length + 1);
    expect(plain.container.querySelector("summary small")).toHaveTextContent(`${listed} rules that are not choices`);
    plain.unmount();
    const withFamilies = render(<FixedRules recs={recs} families={{ difference: 2, equivalence: 0, negative_control: 3 }} />);
    const rows = withFamilies.container.querySelectorAll(".fixed-rules__list > div").length;
    expect(rows).toBe(listed + 1);
    expect(withFamilies.container.querySelector("summary small")).toHaveTextContent(`${rows} rules that are not choices`);
  });

  it("names no flag kind and no stop rule: the setup lists none", () => {
    const { container } = render(<FixedRules recs={recs} />);
    const catalogue = readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/cards/result_checks.py"), "utf-8");
    // The identifiers of the kinds and rules. One-word kinds are left out: "repetition" is also an ordinary word of this list.
    const names = [...catalogue.matchAll(/^ {4}"([a-z0-9]+_[a-z0-9_]+)",$/gm)].map((match) => match[1]!);
    expect(names).toContain("empty_output");
    expect(names).toContain("judge_phase_error");
    for (const name of names) expect(container.textContent).not.toContain(name);
    // Nor by label: the sentence stays general.
    for (const label of ["empty reply", "ends in a loop", "damaged characters", "no judge reply"]) {
      expect(catalogue).toContain(`"${label}"`);
      expect(container.textContent).not.toContain(label);
    }
  });

  it("links to a document the app really shows", () => {
    expect(getDocSlugs().map((slug) => `/docs/${slug}`)).toContain(RESULT_CHECKS_DOC);
  });

  it("claims only what the catalogue says the checks do", () => {
    const catalogue = readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/cards/result_checks.py"), "utf-8");
    // Flags are not exclusions, a stop is a pause, and replies, scores and judgements each have flag kinds.
    expect(catalogue).toContain("**A flag is not an exclusion.**");
    expect(catalogue).toContain("**A stop is a pause.**");
    expect(catalogue).toContain('FlagPhase = Literal["generation", "tier0", "score", "judge"]');
  });
});
