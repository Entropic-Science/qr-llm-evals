import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import studyOneJson from "../../../../../packages/contracts/fixtures/study1.json";
import { api } from "../../lib/api";
import { CONFIG_STORAGE_KEY, parseStudyConfig, type WizardStudyConfig } from "../../lib/config-store";
import { clearSession } from "../../lib/session";
import { Configure } from "./index";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

// The Design step of Study 1 is a large page for jsdom, and the gate runs beside other suites.
vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() });
  window.history.pushState({}, "", "/");
  clearSession();
  // The page works from its bundled registry; nothing here reaches a server.
  vi.spyOn(api, "cards").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "models").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "prereg").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const rail = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}: `) });
const open = (label: string) => fireEvent.click(rail(label));
const saved = () => JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) ?? "{}") as Partial<WizardStudyConfig>;
const panels = (container: HTMLElement) => [...container.querySelectorAll(".wizard-step .settings-panel")].map((panel) => panel.id.replace(/^panel-/, ""));
/** The table of the outcomes that decide the study: a folded table of the other evals is on the page too. */
const deciding = () => within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" }));
const card = (id: string) => {
  const found = document.querySelector<HTMLElement>(`.hypothesis-card[data-hypothesis="${id}"]`);
  if (!found) throw new Error(`No hypothesis card ${id}.`);
  return found;
};
const stamp = (id: string, verdict: string) => card(id).querySelector(`.verdict-stamp[data-verdict="${verdict}"]`);

/** Study 1 as a loaded legacy file: no design block. */
function legacy(): WizardStudyConfig {
  const parsed = parseStudyConfig(studyOneJson);
  if (!parsed) throw new Error("study1.json does not match StudyConfig.");
  return parsed;
}

describe("Design step of Study 1 as it ships", () => {
  it("draws the six decisions in order, each pre-filled and each with its mark", () => {
    const { container } = render(<Configure />);
    open("Design");
    expect(panels(container)).toEqual(["claim", "outcomes", "alpha", "prompts", "positive-control", "margins", "expectations"]);
    const mark = (panel: string) => container.querySelector(`#panel-${panel} .rec`);
    expect(mark("claim")).toHaveClass("rec--recommended");
    expect(mark("outcomes")).toHaveClass("rec--recommended");
    expect(mark("alpha")).toHaveClass("rec--recommended");
    expect(mark("positive-control")).toHaveClass("rec--recommended");
    // Prompts: the probe's sample size was computed to be enough; the two pooled token tests are never rated.
    const prompts = (cardId: string) => deciding().getByText(cardId).closest("tr")!.querySelector(".rec");
    expect(prompts("self_referential_probe")).toHaveClass("rec--sufficient");
    expect(prompts("tier0_pit_residuals")).toHaveClass("rec--unknown");
    expect(prompts("tier0_mmd_hamming")).toHaveClass("rec--unknown");
    // Margins: the probe takes the eval card's; a pooled test has none.
    expect([...container.querySelectorAll("#panel-margins .rec")].map((chip) => chip.className.replace(/^rec /, ""))).toEqual(["rec--unknown", "rec--unknown", "rec--locked"]);

    // The values are the recommended ones, with nothing typed.
    expect(screen.getByRole("radio", { name: "A difference only" })).toBeChecked();
    expect(screen.getByRole("group", { name: "Outcomes that decide the study (m = 3)" })).toBeInTheDocument();
    expect(screen.getByLabelText("False-finding rate (α)")).toHaveValue("0.05");
    expect(screen.getByLabelText("Positive control that must be detected")).toHaveValue("");
    // No decision is in error: the one thing to check is that no expectations are recorded yet.
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to check");
  });

  it("puts what the choices add up to between the decisions and the expectations, and the fixed rules last", () => {
    const { container } = render(<Configure />);
    open("Design");
    const order = [...container.querySelectorAll(".wizard-step__panels > *")].map((element) => element.id || element.getAttribute("aria-labelledby"));
    expect(order).toEqual([
      "panel-claim", "panel-outcomes", "panel-alpha", "panel-prompts", "panel-positive-control", "panel-margins",
      "design-hypotheses", "design-sample-size", "panel-expectations",
    ]);
    const fixed = container.querySelector(".wizard-step > details.fixed-rules");
    expect(fixed).not.toBeNull();
    expect(fixed).not.toHaveAttribute("open");
    expect(fixed?.previousElementSibling).toHaveClass("wizard-step__panels");
    expect(within(fixed as HTMLElement).getByText(/^Fixed by the harness/)).toBeInTheDocument();
    for (const path of ["fixed.correction", "fixed.ci_level", "fixed.power", "fixed.permutations", "fixed.completeness", "fixed.envelope", "fixed.families"]) {
      expect(fixed?.querySelector(`[data-path="${path}"]`)).not.toBeNull();
    }
    expect(fixed?.querySelectorAll('[data-path^="fixed.test."]')).toHaveLength(3);
    expect(fixed?.querySelector("input, select, textarea, button")).toBeNull();
  });

  it("generates one hypothesis card per outcome and comparison, with the verdicts each can reach", () => {
    const { container } = render(<Configure />);
    open("Design");
    const cards = [...container.querySelectorAll<HTMLElement>(".hypothesis-card")];
    expect(cards.map((element) => element.getAttribute("data-hypothesis"))).toEqual(["H1", "H2", "H3"]);
    expect(cards.map((element) => element.querySelector("h4")?.textContent)).toEqual(["H1 Token sampling residuals (KS)", "H2 Token sequence divergence (MMD)", "H3 Experience-claim rate"]);
    for (const element of cards) {
      expect(within(element).getByText(/^qr-llm · Creative sampling/)).toBeInTheDocument();
      expect(within(element).getByText("Test")).toBeInTheDocument();
      expect(within(element).getByText("Margin")).toBeInTheDocument();
      expect(within(element).getByText("Planned data")).toBeInTheDocument();
      expect(within(element).getByText("Sample size · preview")).toBeInTheDocument();
      expect(element.querySelectorAll(".verdict-stamp")).toHaveLength(6);
      // No field: a hypothesis is generated, never typed.
      expect(element.querySelector("input, select, textarea")).toBeNull();
    }
    // Study 1 claims a difference only, so no hypothesis can end as equivalent, and each card says why.
    for (const id of ["H1", "H2", "H3"]) {
      expect(stamp(id, "effect")).toHaveClass("is-reachable");
      expect(stamp(id, "equivalent")).toHaveClass("is-unreachable");
      expect(within(card(id)).getByRole("list", { name: `Verdicts ${id} cannot reach` })).toHaveTextContent("Equivalent: unreachable. The study registers no equivalence claim.");
    }
    expect(stamp("H3", "effect_within_margin")).toHaveClass("is-reachable");
    expect(stamp("H1", "effect_within_margin")).toHaveClass("is-unreachable");
    expect(within(card("H1")).getByRole("list", { name: "Verdicts H1 cannot reach" })).toHaveTextContent("Effect within margin: unreachable. This test has no equivalence margin.");
  });

  it("is honest about sample size: worst-case variance, and no rating for a test on pooled tokens", () => {
    render(<Configure />);
    open("Design");
    const table = screen.getByRole("table", { name: "Sample size of each hypothesis, as a preview" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    for (const id of ["H1", "H2"]) {
      const row = table.querySelector<HTMLElement>(`tr[data-hypothesis="${id}"]`)!;
      expect(within(row).getByText("Unknown: no closed-form power for this test.")).toBeInTheDocument();
      // Never green: neither in the table nor on the card.
      expect(row.querySelector(".rec--sufficient")).toBeNull();
      expect(row.querySelector(".rating-mark")).toHaveClass("rec--unknown");
      expect(card(id).querySelector(".rec--sufficient")).toBeNull();
      expect(within(card(id)).getByText("Unknown: no closed-form power for this test.")).toBeInTheDocument();
    }
    expect(table.querySelector('tr[data-hypothesis="H3"] .rating-mark')).toHaveClass("rec--sufficient");
    expect(screen.getByText("Worst-case variance, no prompt-by-source heterogeneity; true power may be lower.")).toBeInTheDocument();
    expect(screen.getByText(/^A rating is not a verdict and changes no decision rule\./)).toBeInTheDocument();
  });

  it("counts missing expectations as a warning that blocks nothing", () => {
    render(<Configure />);
    open("Design");
    const row = within(screen.getByRole("list", { name: "Problems on this step" })).getByText("No expectations recorded.").closest("li")!;
    expect(row).toHaveAttribute("data-code", "design.no_expectations");
    expect(within(row).getByText("Warning")).toBeInTheDocument();
    expect(rail("Design")).toHaveClass("has-warnings");
    expect(rail("Design")).not.toHaveClass("has-errors");
    fireEvent.change(screen.getByLabelText("What do you expect to see?"), { target: { value: "No difference between the arms." } });
    expect(screen.queryByRole("list", { name: "Problems on this step" })).not.toBeInTheDocument();
    expect(rail("Design")).toHaveAccessibleName("Design: clear");
  });

  it("makes no promise the harness does not keep", () => {
    const { container } = render(<Configure />);
    open("Design");
    container.querySelectorAll("details").forEach((details) => { details.open = true; });
    const text = container.querySelector(".wizard-step")?.textContent ?? "";
    expect(text).not.toMatch(/interleav|replay control|grouped per-prompt|registers? this study|locks? the study|subsampl|pilot-derived/i);
    // A pilot is named only to say what it is not: a source of margins, or a confirmatory run.
    expect(text).toContain("Never derived from a pilot.");
    expect(text).toContain("a pilot run never is.");
  });
});

describe("design.below_floor", () => {
  it("is raised when the probe is cut to 6 prompts, and 'Use recommended' clears it", async () => {
    render(<Configure />);
    open("Design");
    fireEvent.change(deciding().getByLabelText("Prompts for self_referential_probe"), { target: { value: "6" } });

    // Two things to fix: six prompts cannot reach significance, and no seed pair could fail its control.
    expect(rail("Design")).toHaveAccessibleName("Design: 2 to fix, 1 to check");
    const message = "Experience-claim rate: 6 prompts cannot reach significance at any effect size. At least 7 are needed.";
    const row = within(screen.getByRole("list", { name: "Problems on this step" })).getByText(message).closest("li")!;
    expect(row).toHaveAttribute("data-code", "design.below_floor");
    expect(within(row).getByText("Blocks launch")).toBeInTheDocument();
    // The generated hypothesis follows: too few prompts, so no effect can be found on it.
    expect(card("H3").querySelector(".rating-mark")).toHaveClass("rec--insufficient");
    expect(stamp("H3", "effect")).toHaveClass("is-unreachable");
    // With a control that cannot fail the hypothesis could only come out invalid.
    expect(stamp("H3", "inconclusive")).toHaveClass("is-unreachable");
    expect(within(card("H3")).getByRole("list", { name: "Verdicts H3 cannot reach" })).toHaveTextContent("Effect: unreachable. Too few prompts to reach significance at any effect size.");
    expect(within(card("H3")).getByText("6 prompts cannot reach significance at any effect size. At least 7 are needed.")).toBeInTheDocument();
    await waitFor(() => expect(saved().cards?.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.items).toBe(6));

    fireEvent.click(deciding().getByRole("button", { name: "Use recommended" }));
    expect(screen.queryByText(message)).not.toBeInTheDocument();
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to check");
    expect(card("H3").querySelector(".rating-mark")).toHaveClass("rec--sufficient");
    expect(stamp("H3", "effect")).toHaveClass("is-reachable");
    expect(deciding().queryByRole("button", { name: "Use recommended" })).not.toBeInTheDocument();
    await waitFor(() => expect(saved().cards?.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.items ?? 30).toBe(30));
  });
});

describe("the equivalence claim", () => {
  it("is offered once the 0.10 positive control is switched on, and then opens the 'equivalent' verdict", async () => {
    render(<Configure />);
    open("Design");
    const claim = () => screen.getByRole("radio", { name: /^A difference, or its absence within a margin/ });
    expect(claim()).toBeDisabled();
    expect(claim()).toHaveAccessibleName(/needs a positive control that is switched on in every comparison/);

    open("Compare");
    fireEvent.click(screen.getByRole("button", { name: "ε 0.10: planned, off. Switch to on." }));
    open("Design");
    expect(claim()).toBeEnabled();
    expect(claim()).not.toBeChecked();
    // Offered, not taken: the study still claims a difference only.
    expect(stamp("H3", "equivalent")).toHaveClass("is-unreachable");

    fireEvent.click(claim());
    expect(claim()).toBeChecked();
    await waitFor(() => expect(saved().design).toMatchObject({ claim: "difference_or_equivalence", sensitivity_epsilon: 0.1 }));
    expect(screen.getByLabelText("Positive control that must be detected")).toHaveValue("0.1");
    // The probe has a margin and a positive control now; a test on pooled tokens still has no margin.
    expect(stamp("H3", "equivalent")).toHaveClass("is-reachable");
    expect(within(card("H3")).queryByRole("list", { name: "Verdicts H3 cannot reach" })).not.toBeInTheDocument();
    expect(stamp("H1", "equivalent")).toHaveClass("is-unreachable");
    expect(within(card("H1")).getByRole("list", { name: "Verdicts H1 cannot reach" })).toHaveTextContent("Equivalent: unreachable. This test has no equivalence margin.");
    expect(rail("Design")).not.toHaveClass("has-errors");
  });
});

describe("a study without a design block", () => {
  it("shows the claim panel with 'Declare a design' and says what applies until then", async () => {
    const study = legacy();
    window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(study));
    const { container } = render(<Configure />);
    open("Design");
    expect(panels(container)).toEqual(["claim", "expectations"]);
    expect(screen.getByText(/^Until a design is declared, the built-in Study 1 plan applies\./)).toBeInTheDocument();
    expect(container.querySelector(".hypothesis-card")).toBeNull();
    expect(container.querySelector(".sample-size")).toBeNull();
    expect(container.querySelector(".fixed-rules")).toBeNull();
    expect(container.querySelector(".rec-legend")).toBeNull();
    // The built-in plan is a warning, not an error.
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to check");
    expect(screen.getByLabelText("What do you expect to see?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Declare a design" }));
    expect(screen.queryByText(/^Until a design is declared/)).not.toBeInTheDocument();
    expect(panels(container)).toEqual(["claim", "outcomes", "alpha", "prompts", "positive-control", "margins", "expectations"]);
    expect(container.querySelectorAll(".hypothesis-card")).toHaveLength(3);
    expect(container.querySelector(".fixed-rules")).not.toBeNull();
    await waitFor(() => expect(saved().design).toMatchObject({ claim: "difference", alpha: 0.05 }));
  });
});
