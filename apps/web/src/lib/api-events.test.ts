import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SUMMARIES } from "../components/run/checks-fixture";
import { api, ApiError, RUN_EVENT_TYPES } from "./api";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("the run event vocabulary", () => {
  it("listens for a stop by a check", () => {
    expect(RUN_EVENT_TYPES).toContain("check.stopped");
  });

  it("listens for every event the backend can send", () => {
    // The browser only hears the names it subscribes to; one the backend adds and this list lacks is lost.
    const source = readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/runner/events.py"), "utf-8");
    const block = /EVENT_TYPES: Final\[frozenset\[str\]\] = frozenset\(\s*\{([^}]*)\}/.exec(source)?.[1] ?? "";
    const backend = [...block.matchAll(/"([^"]+)"/g)].map((match) => match[1]!);
    expect(backend).toContain("run.state");
    expect(backend.filter((type) => !(RUN_EVENT_TYPES as readonly string[]).includes(type))).toEqual([]);
  });
});

describe("api.health", () => {
  it("asks for the health of one run and gives the summary back", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify(SUMMARIES.complete)));
    vi.stubGlobal("fetch", fetcher);
    await expect(api.health("run 1")).resolves.toEqual(SUMMARIES.complete);
    expect(String(fetcher.mock.calls[0]?.[0])).toMatch(/\/api\/runs\/run%201\/health$/);
  });

  it("refuses an answer that is not a health summary", async () => {
    const partial: Record<string, unknown> = { ...SUMMARIES.complete };
    delete partial.cautions;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(partial))));
    const failure = await api.health("run-1").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).code).toBe("contract_mismatch");
    expect((failure as ApiError).message).toBe("The run service answered with a health summary this page cannot read.");
  });

  it("passes the service's own refusal on", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { message: "run not found" } }), { status: 404 })));
    await expect(api.health("gone")).rejects.toMatchObject({ status: 404, message: "run not found" });
  });
});
