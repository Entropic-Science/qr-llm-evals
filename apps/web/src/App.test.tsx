import { cleanup, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { App } from "./App";

function renderAt(path: string) { window.history.pushState({}, "", path); return render(<App />); }
afterEach(() => { cleanup(); window.history.pushState({}, "", "/"); });

describe("public routes", () => {
  it("renders the configure wizard", () => { renderAt("/"); expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Build the comparison"); });
  it("renders the documentation route", () => { renderAt("/docs/determinism"); expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Determinism in the evaluation harness"); });
  it("renders the eval registry", () => { renderAt("/evals"); expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Evaluation registry"); });
  it("renders the contribution route", () => { renderAt("/evals/add"); expect(screen.getByRole("button", { name: "Copy agent brief" })).toBeInTheDocument(); });
  it("renders a quiet fallback", () => { renderAt("/not-registered"); expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("not registered"); });
});
