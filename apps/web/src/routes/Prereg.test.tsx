import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { webcrypto } from "node:crypto";
import { vi } from "vitest";

import type * as ApiModule from "../lib/api";
import { studyOneTemplate } from "../lib/config-store";
import { normalizePreregMarkdown, preregMarkdownHash, verifiedPreregBytes } from "../lib/prereg";
import { Prereg } from "./Prereg";

const preregMock = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof ApiModule>();
  return { ...original, api: { ...original.api, prereg: preregMock } };
});

function memoryStorage(initialConfig?: ReturnType<typeof studyOneTemplate>): Storage {
  const values = new Map<string, string>();
  if (initialConfig) values.set("qr-evals.study-config.v1", JSON.stringify(initialConfig));
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

beforeAll(() => {
  if (!globalThis.crypto?.subtle) Object.defineProperty(globalThis, "crypto", { configurable: true, value: webcrypto });
});

beforeEach(() => {
  preregMock.mockReset();
  window.history.pushState({}, "", "/prereg");
});

describe("preregistration bytes", () => {
  it("normalizes exported bytes so their browser SHA-256 equals the server hash", async () => {
    const serverMarkdown = "# Frozen\r\n\rMethods\rFinal\n";
    const normalized = normalizePreregMarkdown(serverMarkdown);
    const serverHash = await preregMarkdownHash(normalized);
    const bytes = await verifiedPreregBytes(serverMarkdown, serverHash);
    expect(new TextDecoder().decode(bytes)).toBe("# Frozen\n\nMethods\nFinal\n");
    expect(await preregMarkdownHash(new TextDecoder().decode(bytes))).toBe(serverHash);
  });
});

describe("preregistration page", () => {
  it("requires expectations before rendering or launch-capable state", () => {
    const config = studyOneTemplate();
    config.expectations = "";
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(config) });
    render(<Prereg />);
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
    expect(screen.getByText(/required before this study can be preregistered or launched/i)).toBeInTheDocument();
  });

  it("marks the rendered document stale when expectations change", async () => {
    const config = studyOneTemplate();
    config.expectations = "No directional effect is expected.";
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(config) });
    const markdown = "# Frozen protocol";
    preregMock.mockResolvedValue({ markdown, prereg_hash: await preregMarkdownHash(markdown), config_hash: "b".repeat(64), plan_hash: "c".repeat(64), plan: {} });
    render(<Prereg />);
    fireEvent.click(screen.getByRole("button", { name: "Render document" }));
    await waitFor(() => expect(screen.getByText("Current with configuration")).toBeInTheDocument());
    const request = preregMock.mock.calls[0]?.[0] as { config: { expectations?: string }; expectations?: string };
    expect(request.config.expectations).toBe(request.expectations);
    fireEvent.change(screen.getByRole("textbox", { name: "Researcher expectations" }), { target: { value: "A changed prior expectation." } });
    expect(screen.getByText(/Stale · update required/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download .md" })).toBeDisabled();
  });
});
