import { cards } from "@qr-evals/contracts";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { studyOneTemplate } from "../lib/config-store";
import { estimateSelection } from "../lib/estimates";
import { rate, recFor } from "../lib/recommendations";
import { FAMILIES_NOTE, EstimateTotals, type EvidencePreview } from "./EstimateTotals";

afterEach(cleanup);

const estimate = () => estimateSelection(cards.filter((card) => card.id === "self_referential_probe"), { modelId: "qr-llm", tpsByModel: { "qr-llm": 400 } });

function evidence(overrides: Partial<EvidencePreview> = {}): EvidencePreview {
  const recs = rate(studyOneTemplate(), cards);
  return {
    designed: true,
    hypotheses: 2,
    comparisons: 1,
    outcomes: [
      { key: "self_referential_probe/experience_claim_rate", label: "Experience-claim rate", rec: recFor(recs, "cards.self_referential_probe.items") },
      { key: "gone/outcome", label: "An outcome of an eval this registry lacks", rec: undefined },
    ],
    families: { difference: 2, equivalence: 1, negative_control: 6 },
    ...overrides,
  };
}

describe("EstimateTotals", () => {
  it("shows the cost of the study, with the supervised line only for a study with supervised sessions", () => {
    const view = render(<EstimateTotals estimate={estimate()} modelId="qr-llm" armCount={5} supervisedDisplay="tiled" />);
    const readout = within(screen.getByLabelText("Live study estimates"));
    expect(readout.getByText("Per arm")).toBeInTheDocument();
    expect(readout.getByText("Supervised · tiled")).toBeInTheDocument();
    expect(screen.queryByLabelText("Evidence preview")).not.toBeInTheDocument();
    view.rerender(<EstimateTotals estimate={estimate()} modelId="qr-llm" armCount={5} supervisedDisplay={null} />);
    expect(screen.queryByText(/^Supervised/)).not.toBeInTheDocument();
  });

  it("names the model that has no throughput assumption and still shows the evidence", () => {
    render(<EstimateTotals estimate={null} modelId="openai:gpt-x" armCount={5} evidence={evidence()} />);
    expect(screen.getByText("No throughput assumption for openai:gpt-x.")).toBeInTheDocument();
    expect(screen.queryByText("Per arm")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Evidence preview")).toBeInTheDocument();
  });

  it("lists each deciding outcome with the rating of its sample size, and the Holm families", () => {
    const onOpenSetting = vi.fn();
    render(<EstimateTotals estimate={estimate()} modelId="qr-llm" armCount={5} evidence={evidence()} onOpenSetting={onOpenSetting} />);
    const preview = within(screen.getByLabelText("Evidence preview"));
    expect(preview.getByText("Hypotheses").nextElementSibling).toHaveTextContent("2 (2 outcomes × 1 comparison)");
    const [rated, unrated] = within(preview.getByRole("list", { name: "Sample size per outcome" })).getAllByRole("listitem");
    const chip = within(rated!).getByRole("button");
    expect(chip).toHaveClass("rec--sufficient");
    expect(chip).toHaveTextContent("✓");
    expect(chip).toHaveAccessibleName(/^Prompts: 30 prompts\. Enough for the stated margin\./);
    expect(within(unrated!).getByText("not rated")).toBeInTheDocument();
    expect(preview.getByText("Tests for a difference").nextElementSibling).toHaveTextContent("m = 2");
    expect(preview.getByText("Tests for equivalence").nextElementSibling).toHaveTextContent("m = 1");
    expect(preview.getByText("Negative-control tests").nextElementSibling).toHaveTextContent("m = 6");
    // "Holm family" and "m" are said in plain words beside the counts.
    expect(preview.getByText(FAMILIES_NOTE)).toBeInTheDocument();
    expect(FAMILIES_NOTE).toContain("m is how many share it");

    fireEvent.click(chip);
    expect(onOpenSetting).toHaveBeenCalledWith(expect.objectContaining({ path: "cards.self_referential_probe.items", stepId: "design" }));
  });

  it("rates nothing for a study on the built-in plan, and says so", () => {
    render(<EstimateTotals estimate={estimate()} modelId="qr-llm" armCount={5} evidence={evidence({ designed: false, hypotheses: 0, outcomes: [] })} />);
    const preview = within(screen.getByLabelText("Evidence preview"));
    expect(preview.getByText("This draft uses the fixed Study 1 plan, so no sample size is rated here.")).toBeInTheDocument();
    expect(preview.queryByText("Hypotheses")).not.toBeInTheDocument();
    expect(preview.queryByRole("button")).not.toBeInTheDocument();
  });

  it("says when a designed study has no deciding outcome yet", () => {
    render(<EstimateTotals estimate={estimate()} modelId="qr-llm" armCount={5} evidence={evidence({ hypotheses: 0, outcomes: [], families: { difference: 0, equivalence: 0, negative_control: 0 } })} />);
    expect(within(screen.getByLabelText("Evidence preview")).getByText("No outcome decides this study yet.")).toBeInTheDocument();
  });
});
