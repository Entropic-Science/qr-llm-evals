import { render, screen } from "@testing-library/react";
import { Evals } from "./Evals";

describe("Evals", () => {
  it("distinguishes implemented and planned cards", () => {
    render(<Evals />);
    expect(screen.getAllByText("implemented").length).toBeGreaterThan(0);
    expect(screen.getAllByText("planned").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Selectable").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not selectable").length).toBeGreaterThan(0);
  });
});
