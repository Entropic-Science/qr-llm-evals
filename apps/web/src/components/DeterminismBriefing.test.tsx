import { render, screen } from "@testing-library/react";
import { DeterminismBriefing, determinismPopupText } from "./DeterminismBriefing";

describe("DeterminismBriefing", () => {
  it("renders the popup section sourced from the documentation", () => {
    expect(determinismPopupText).toContain("sampling-deterministic");
    render(<DeterminismBriefing open onClose={() => undefined} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("The QRNG arm is intentionally fresh");
    expect(screen.getByRole("dialog")).toHaveTextContent("sampling-deterministic");
  });
});
