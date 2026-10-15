import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Configure } from "./index";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

beforeEach(() => { Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() }); window.history.pushState({}, "", "/"); });

describe("configure wizard", () => {
  it("moves through steps and opens the determinism briefing once", async () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("heading", { name: "Sampling & source" })).toBeInTheDocument();
    const deterministic = screen.getAllByRole("checkbox", { name: "Deterministic" })[0];
    if (!deterministic) throw new Error("Missing deterministic control");
    fireEvent.click(deterministic); fireEvent.click(deterministic);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "I understand" }));
    expect(window.sessionStorage.getItem("qr-evals.determinism-briefing.acknowledged")).toBe("yes");
  });

  it("loads Study 1 and never writes keys to localStorage", async () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: "Load Study 1 template" }));
    fireEvent.click(screen.getByRole("button", { name: /06Keys/ }));
    fireEvent.change(screen.getByLabelText("api.entropic.science launch key"), { target: { value: "qrk_secret" } });
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-secret" } });
    await waitFor(() => expect(window.localStorage.getItem("qr-evals.study-config.v1")).not.toContain("secret"));
  });
});
