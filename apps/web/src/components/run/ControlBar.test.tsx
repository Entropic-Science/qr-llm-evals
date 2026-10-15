import { render, screen } from "@testing-library/react";
import { ControlBar } from "./ControlBar";

describe("ControlBar", () => {
  it("is entirely hidden for viewers without a host token", () => {
    const { container } = render(<ControlBar hostToken={undefined} state="running" onAction={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows only valid controls for an authorized running host", () => {
    render(<ControlBar hostToken="host-secret" state="running" onAction={() => undefined} />);
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Abort" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
  });
});
