import { render, screen } from "@testing-library/react";
import { ProvenanceBadge } from "./ProvenanceBadge";

describe("ProvenanceBadge", () => {
  it.each(["seeded_prng", "system_prng", "seeded_prng_biased", "resampled_prng", "provider_prng"] as const)("never calls %s quantum", (sourceLabel) => {
    render(<ProvenanceBadge sourceLabel={sourceLabel} />);
    expect(screen.queryByText(/quantum/i)).not.toBeInTheDocument();
    expect(screen.getByText(/prng/i)).toBeInTheDocument();
  });

  it("names the resampled device pool", () => {
    render(<ProvenanceBadge sourceLabel="resampled_prng" />);
    expect(screen.getByText("Resampled device-pool PRNG")).toBeInTheDocument();
  });

  it("uses the quantum claim only for the exact quantum source label", () => {
    const { rerender } = render(<ProvenanceBadge sourceLabel="quantum" />);
    expect(screen.getByText("Quantum source")).toBeInTheDocument();
    rerender(<ProvenanceBadge sourceLabel={null} />);
    expect(screen.queryByText("Quantum source")).not.toBeInTheDocument();
    expect(screen.getByText("Source unavailable")).toBeInTheDocument();
  });
});
