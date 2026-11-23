import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { DraftBoundary } from "./DraftBoundary";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Drawing({ study }: { study: { title: string; broken?: boolean } }) {
  if (study.broken) throw new Error("this study cannot be drawn");
  return <p>{study.title}</p>;
}

describe("DraftBoundary", () => {
  it("says a draft could not be drawn, offers the template, and draws the next study afresh", () => {
    // React reports the error it caught; the report is not part of what is tested.
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onLoadTemplate = vi.fn();
    const broken = { title: "Broken", broken: true };
    const view = render(<DraftBoundary resetKey={broken} onLoadTemplate={onLoadTemplate}><Drawing study={broken} /></DraftBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("This draft could not be drawn. Load the Study 1 template.");
    fireEvent.click(screen.getByRole("button", { name: "Load the Study 1 template" }));
    expect(onLoadTemplate).toHaveBeenCalledTimes(1);

    // The same study drawn again stays failed; another study gets a fresh attempt.
    view.rerender(<DraftBoundary resetKey={broken} onLoadTemplate={onLoadTemplate}><Drawing study={broken} /></DraftBoundary>);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    const template = { title: "Study 1" };
    view.rerender(<DraftBoundary resetKey={template} onLoadTemplate={onLoadTemplate}><Drawing study={template} /></DraftBoundary>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Study 1")).toBeInTheDocument();
  });

  it("draws its children when nothing fails", () => {
    const study = { title: "Fine" };
    render(<DraftBoundary resetKey={study} onLoadTemplate={() => undefined}><Drawing study={study} /></DraftBoundary>);
    expect(screen.getByText("Fine")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
