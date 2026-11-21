import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api } from "../../lib/api";
import { CONFIG_STORAGE_KEY, studyOneTemplate } from "../../lib/config-store";
import { clearSession, setSession } from "../../lib/session";
import { authResponse } from "../../lib/session-fixture";
import { Configure } from "./index";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

beforeEach(() => { Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() }); window.history.pushState({}, "", "/"); clearSession(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
    expect(screen.queryByLabelText("api.entropic.science launch key")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=/");
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-secret" } });
    await waitFor(() => expect(window.localStorage.getItem("qr-evals.study-config.v1")).not.toContain("secret"));
  });

  it("refuses to open the launch gate while signed out", () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: /07Review/ }));
    fireEvent.click(screen.getByRole("button", { name: "Launch run" }));
    expect(screen.getByRole("status")).toHaveTextContent("Sign in before launching");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("starts the judge on the current Haiku and saves the settings with the study", async () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: "Load Study 1 template" }));
    fireEvent.click(screen.getByRole("button", { name: /05Judge/ }));
    expect(screen.getByLabelText("Judge model")).toHaveValue("claude-haiku-5-5");
    expect(screen.getByLabelText("Longest answer, in tokens")).toHaveValue(1024);
    expect(screen.getByLabelText("Extended thinking")).toHaveValue("disabled");
    expect(screen.getByLabelText("Temperature")).toHaveValue("unset");
    expect(screen.queryByLabelText("Temperature value (0 to 1)")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "claude-sonnet-5-5" } });
    fireEvent.change(screen.getByLabelText("Extended thinking"), { target: { value: "model_default" } });
    fireEvent.change(screen.getByLabelText("Longest answer, in tokens"), { target: { value: "4096" } });
    fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "set" } });
    fireEvent.change(screen.getByLabelText("Temperature value (0 to 1)"), { target: { value: "0.3" } });
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) ?? "{}") as { judge?: unknown };
      expect(saved.judge).toMatchObject({ model: "claude-sonnet-5-5", thinking: "model_default", max_tokens: 4096, temperature: 0.3 });
    });

    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("Exact model id"), { target: { value: "claude-sonnet-4-6" } });
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toContain("claude-sonnet-4-6"));
  });

  it("tests the judge with the typed key and the chosen settings", async () => {
    const session = setSession(authResponse());
    const judgeCheck = vi.spyOn(api, "judgeCheck").mockResolvedValue({ ok: false, status: 401, model: null, detail: "invalid x-api-key" });
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: "Load Study 1 template" }));
    fireEvent.click(screen.getByRole("button", { name: /06Keys/ }));
    expect(screen.getByText(/Required\. This study has evals scored by the judge/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test the judge" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Test the judge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The judge call failed (401): invalid x-api-key");
    expect(judgeCheck).toHaveBeenCalledWith({ judge: expect.objectContaining({ model: "claude-haiku-5-5", thinking: "disabled", temperature: null }), api_key: "sk-ant-typed" }, session.token);

    judgeCheck.mockResolvedValue({ ok: true, status: 200, model: "claude-haiku-5-5", detail: "The judge answered." });
    fireEvent.click(screen.getByRole("button", { name: "Test the judge" }));
    expect(await screen.findByText("claude-haiku-5-5 answered. The judge answered.")).toBeInTheDocument();
  });

  it("will not open the launch gate for a judged study without the Anthropic key", () => {
    setSession(authResponse());
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: "Load Study 1 template" }));
    fireEvent.click(screen.getByRole("button", { name: /07Review/ }));
    fireEvent.click(screen.getByRole("button", { name: "Launch run" }));
    expect(screen.getByRole("status")).toHaveTextContent("runs on your own Anthropic key");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("loads a study file through the server and reports what it holds", async () => {
    const thesis = { ...studyOneTemplate(), title: "Prompt or Physics?" };
    const parseConfig = vi.spyOn(api, "parseConfig").mockResolvedValue({ config: thesis, config_hash: "c".repeat(64), unknown_cards: [] });
    render(<Configure />);
    const file = new File(["title: Prompt or Physics?\n"], "study.yaml", { type: "text/yaml" });
    fireEvent.change(screen.getByLabelText("Load study file"), { target: { files: [file] } });
    expect(await screen.findByRole("status")).toHaveTextContent(`Loaded study.yaml: ${thesis.arms.length} arms, ${thesis.cards.length} evals.`);
    expect(parseConfig).toHaveBeenCalledWith({ text: "title: Prompt or Physics?\n" });
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toContain("Prompt or Physics?"));
  });

  it("says why a study file was not loaded and keeps the current study", async () => {
    vi.spyOn(api, "parseConfig").mockRejectedValue(new Error("not a valid study configuration: arms: Field required (3 problem(s) in all)"));
    render(<Configure />);
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toBeNull());
    const before = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    fireEvent.change(screen.getByLabelText("Load study file"), { target: { files: [new File(["x"], "broken.yaml")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("broken.yaml was not loaded: not a valid study configuration: arms: Field required");
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(before);
  });

  it("lets an arm be the biased positive control and asks for its bias", () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("button", { name: /02Sampling/ }));
    const source = screen.getAllByLabelText("Source")[0] as HTMLSelectElement;
    const option = [...source.options].find((item) => item.value === "prng_epsilon");
    expect(option?.disabled).toBe(false);
    fireEvent.change(source, { target: { value: "prng_epsilon" } });
    expect(screen.getAllByLabelText(/Bias ε/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/must never be compared as if it were a fair source/).length).toBeGreaterThan(0);
  });
});
