import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryEndpoint } from "../../components/results/PrimaryEndpoint";
import { ProvenanceSummary } from "../../components/results/ProvenanceSummary";
import { HashPanel } from "../../components/results/HashPanel";
import { NarrativePanel } from "../../components/results/NarrativePanel";
import { ResultsTable } from "../../components/results/ResultsTable";
import {
  loadAnalysis,
  type FrozenPlan,
  type ResultsTableData,
} from "../../lib/results";
import { StudyPage } from "./Study";

const fixtureRoot = resolve(
  process.cwd(),
  "../../backend/tests/fixtures/bundle_mini",
);
const fixture = <T,>(name: string): T =>
  JSON.parse(readFileSync(resolve(fixtureRoot, name), "utf-8")) as T;
const primary = fixture<ResultsTableData>(
  "golden/tables/unpredictabench--ks100_pass_rate.json",
);
const unavailable = fixture<ResultsTableData>(
  "golden/tables/unpredictabench--not_yet_scored.json",
);
const plan = fixture<FrozenPlan>("prereg-plan.json");
const tableName = "unpredictabench--ks100_pass_rate";
const index = {
  plan,
  manifest: { plan_hash: "plan-hash" },
  tables: [tableName],
  figures: [tableName],
  provenance: [
    {
      arm_id: "prng_seed_1",
      source_label: "seeded_prng",
      quantum: true,
      samples: 3,
      tainted_samples: 0,
    },
  ],
};
const study = {
  study_id: "demo",
  study: {
    config_hash: "config-hash",
    prereg_hash: "prereg-hash",
    config: {
      title: "Published fixture",
      arms: [{ id: "prng_seed_1" }, { id: "missing" }],
    },
  },
  runs: [
    {
      run_id: "pilot-run",
      kind: "pilot",
      state: "completed",
      withdrawn: true,
      taints: [],
    },
    { run_id: "full-run", kind: "full", state: "completed", taints: [] },
  ],
  analyses: { "pilot-run": ["same-id"], "full-run": ["same-id"] },
  withdrawn: false,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
function mockFetch(published = false, narrativeFails = false) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/index?"))
      return new Response(
        JSON.stringify({
          ...index,
          manifest: { ...index.manifest, narrative: { published } },
        }),
      );
    if (url.includes("/tables/")) return new Response(JSON.stringify(primary));
    if (url.includes("narrative.md"))
      return new Response("Verified narrative", {
        status: narrativeFails ? 404 : 200,
      });
    if (url.endsWith("prereg.md"))
      return new Response("# Frozen published preregistration");
    return new Response(JSON.stringify(study));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}
function renderPage(page: "results" | "data" | "prereg" = "results") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <StudyPage studyId="demo" page={page} />
    </QueryClientProvider>,
  );
}

describe("public scientific evidence", () => {
  it("places all three exact fixture controls beside treatment and the frozen claim rule", () => {
    render(<PrimaryEndpoint table={primary} plan={plan} />);
    expect(screen.getByText(plan.claim_rule)).toBeVisible();
    const controls = screen.getByRole("table", {
      name: "All three baseline contrasts",
    });
    expect(within(controls).getAllByRole("row")).toHaveLength(4);
    expect(
      within(controls).getByText("prng_seed_1 vs prng_seed_7"),
    ).toBeVisible();
    expect(
      within(controls).getByText("prng_seed_1 vs prng_seed_42"),
    ).toBeVisible();
    expect(
      within(controls).getByText("prng_seed_7 vs prng_seed_42"),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("table", { name: "Source comparison" }),
      ).getByText("0.222222"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Unavailable - no biased-arm epsilon curve was produced.",
      ),
    ).toBeVisible();
  });
  it("does not display correction placeholder p=1 as measured data for unavailable endpoints", () => {
    render(<ResultsTable rows={unavailable.comparisons} caption="Missing" />);
    expect(screen.queryByText("1")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Unavailable/).length).toBeGreaterThan(0);
  });
  it("requires source_label quantum, ignores the quantum boolean, and lists missing arms", () => {
    const { container } = render(
      <ProvenanceSummary
        expectedArms={["missing"]}
        arms={[
          ...index.provenance,
          {
            arm_id: "q",
            source_label: "quantum",
            quantum: false,
            samples: 3,
            tainted_samples: 0,
          },
        ]}
      />,
    );
    expect(container.querySelectorAll(".badge--quantum")).toHaveLength(1);
    expect(screen.getByText("seeded_prng")).not.toHaveClass("badge--quantum");
    expect(screen.getByText("(not quantum)")).toBeVisible();
    expect(
      screen.getByText("Unavailable - no recorded provenance"),
    ).toBeVisible();
  });
  it.each([
    "seeded_prng",
    "system_prng",
    "seeded_prng_biased",
    "provider_prng",
    "unverified",
  ])("never badges %s as quantum", (source_label) => {
    const { container } = render(
      <ProvenanceSummary
        arms={[
          {
            arm_id: "control",
            source_label,
            quantum: true,
            samples: 2,
            tainted_samples: 0,
          },
        ]}
      />,
    );
    expect(container.querySelector(".badge--quantum")).toBeNull();
    expect(screen.getByText("(not quantum)")).toBeVisible();
  });
  it("renders verified narrative with its model-written label", () => {
    render(<NarrativePanel published narrative="Verified prose" />);
    expect(screen.getByText("Verified prose")).toBeVisible();
    expect(screen.getByText(/LLM-written/)).toBeVisible();
  });
  it("renders supplied epsilon estimates without constructing a chart", () => {
    const epsilon = {
      ...primary.comparisons[0]!,
      id: "epsilon:5",
      kind: "epsilon",
      epsilon: 0.01,
    };
    render(
      <PrimaryEndpoint
        table={{ ...primary, epsilon_curve: [epsilon] }}
        plan={plan}
      />,
    );
    const curve = screen.getByRole("table", {
      name: "Epsilon curve: script-generated estimates",
    });
    expect(within(curve).getByText(/epsilon 0.01/)).toBeVisible();
    expect(within(curve).getByText("0.222222")).toBeVisible();
  });
  it("copies exact hashes and reports success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<HashPanel prereg="abc" config="def" plan="ghi" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy Analysis plan hash" }),
    );
    expect(await screen.findByText("Analysis plan hash copied")).toBeVisible();
    expect(writeText).toHaveBeenCalledWith("ghi");
  });
  it("withholds unverified narrative even when text exists", () => {
    render(
      <NarrativePanel
        published={false}
        narrative="Unsafe unverified conclusion"
      />,
    );
    expect(screen.getByText("withheld: unverified numbers")).toBeVisible();
    expect(
      screen.queryByText("Unsafe unverified conclusion"),
    ).not.toBeInTheDocument();
  });
  it("loads exact JSON with explicit run identity and never requests an unverified narrative", async () => {
    const fetcher = mockFetch();
    const loaded = await loadAnalysis(
      "demo",
      "same-id",
      "full-run",
      new AbortController().signal,
    );
    expect(loaded.loadedTables[tableName]).toEqual(primary);
    expect(
      fetcher.mock.calls.every(([url]) =>
        String(url).includes("run_id=full-run"),
      ),
    ).toBe(true);
    expect(
      fetcher.mock.calls.some(([url]) => String(url).includes("narrative.md")),
    ).toBe(false);
  });
  it("preserves numerical results when a published narrative artifact is missing", async () => {
    mockFetch(true, true);
    const loaded = await loadAnalysis(
      "demo",
      "same-id",
      "full-run",
      new AbortController().signal,
    );
    expect(loaded.loadedTables[tableName]).toEqual(primary);
    expect(loaded.narrative).toBeNull();
  });
  it("separates pilot and full analyses sharing an ID, embeds SVG and exposes downloads", async () => {
    const fetcher = mockFetch();
    renderPage();
    expect(await screen.findByText(plan.claim_rule)).toBeVisible();
    expect(screen.getByText(/Pilot - calibration/)).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Withdrawn");
    fireEvent.change(screen.getByLabelText("Run / analysis"), {
      target: { value: JSON.stringify(["full-run", "same-id"]) },
    });
    await screen.findByText(plan.claim_rule);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).includes("run_id=full-run"),
      ),
    ).toBe(true);
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining(".svg?run_id=full-run"),
    );
    expect(
      screen.getByRole("link", { name: "Complete reproducibility bundle" }),
    ).toHaveAttribute("href", expect.stringContaining("/demo/bundle.tar.zst"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("renders the published preregistration through the shared read-only renderer", async () => {
    mockFetch();
    renderPage("prereg");
    expect(
      await screen.findByRole("heading", {
        name: "Frozen published preregistration",
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Render the document" }),
    ).not.toBeInTheDocument();
  });
  it("keeps data downloads available without requesting analysis", async () => {
    const fetcher = mockFetch();
    renderPage("data");
    expect(
      await screen.findByRole("link", { name: "Data licence (LICENSE-data)" }),
    ).toBeVisible();
    expect(
      fetcher.mock.calls.some(([url]) => String(url).includes("/analysis/")),
    ).toBe(false);
  });
});
