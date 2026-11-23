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
import {
  designedDecisions,
  designedEndpointTable,
  designedExploratoryTable,
  designedPlan,
  notConfirmatory,
} from "../../components/results/decisions-fixture";
import { PrimaryEndpoint } from "../../components/results/PrimaryEndpoint";
import { ProvenanceSummary } from "../../components/results/ProvenanceSummary";
import { HashPanel } from "../../components/results/HashPanel";
import { NarrativePanel } from "../../components/results/NarrativePanel";
import { ResultsTable } from "../../components/results/ResultsTable";
import { SUMMARIES } from "../../components/run/checks-fixture";
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
const LEGACY_HEADINGS = [
  "Frozen study setup",
  "Arm provenance ledger",
  "Taint report",
  "Output health",
  "Primary endpoints",
  "Script-generated figures",
  "Analysis narrative",
  "Data & licence",
];
const DESIGNED_HEADINGS = [
  "Frozen study setup",
  "Arm provenance ledger",
  "Taint report",
  "Output health",
  "Verdicts",
  "Confirmatory outcomes | comparison rows",
  "Tier 0 | secondary & exploratory endpoints",
  "Script-generated figures",
  "Analysis narrative",
  "Data & licence",
];
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
/** What `GET /api/runs/{id}/health` answers in a test: a summary, or the status of a refusal. */
type ServedHealth = (typeof SUMMARIES)[keyof typeof SUMMARIES] | number;
function healthResponse(health: ServedHealth) {
  return typeof health === "number"
    ? new Response(
        JSON.stringify({ error: { message: "run health is not available" } }),
        { status: health },
      )
    : new Response(JSON.stringify(health));
}
function mockFetch(
  published = false,
  narrativeFails = false,
  health: ServedHealth | ((runId: string) => ServedHealth) = SUMMARIES.complete,
) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const healthOf = /\/api\/runs\/([^/]+)\/health$/.exec(url);
    if (healthOf)
      return healthResponse(
        typeof health === "function" ? health(healthOf[1]!) : health,
      );
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
  it("shows the published preregistration when the address ends in a fragment that cannot be decoded", async () => {
    // "#100%" is an escape cut short: decoding it throws, and nothing above the page would catch that.
    const before = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    window.history.pushState({}, "", "/studies/demo/prereg#100%");
    try {
      mockFetch();
      renderPage("prereg");
      expect(
        await screen.findByRole("heading", {
          name: "Frozen published preregistration",
        }),
      ).toBeVisible();
      expect(
        screen.getByRole("navigation", { name: "Study pages" }),
      ).toBeVisible();
    } finally {
      window.history.pushState({}, "", before);
    }
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

describe("a legacy analysis", () => {
  it("renders as before: three-control endpoint view, no verdicts", async () => {
    mockFetch();
    const page = renderPage();
    await screen.findByText(plan.claim_rule);
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent),
    ).toEqual(LEGACY_HEADINGS);
    // The endpoint block is byte-for-byte the PrimaryEndpoint view.
    const inPage = page.container.querySelector(".results-endpoint")!.outerHTML;
    const alone = render(<PrimaryEndpoint table={primary} plan={plan} />);
    expect(inPage).toBe(
      alone.container.querySelector(".results-endpoint")!.outerHTML,
    );
    expect(inPage).toContain("Preregistered claim rule");
    expect(inPage).toContain("Holm-adjusted");
    expect(page.container.querySelector(".results-verdicts")).toBeNull();
    expect(page.container.querySelector(".results-rows")).toBeNull();
    expect(page.container.querySelector(".results-stamp")).toBeNull();
    expect(
      within(page.container).getByText(/Pilot - calibration only/),
    ).toBeVisible();
  });
});

describe("the run's health on the results page", () => {
  const taintReport = () => screen.getByRole("region", { name: "Taint report" });
  const outputHealth = () =>
    screen.getByRole("region", { name: "Output health" });

  it("counts the taints from the health summary of the selected run", async () => {
    const fetcher = mockFetch(false, false, (runId) =>
      runId === "pilot-run" ? SUMMARIES.legacy : SUMMARIES.complete,
    );
    renderPage();
    await screen.findByText(plan.claim_rule);
    // The pilot run is selected first; its summary has one tainted sample.
    expect(
      await within(taintReport()).findByText("1 sample carries a taint in this run."),
    ).toBeVisible();
    expect(taintReport()).toHaveTextContent("qrng_degraded 1");
    expect(taintReport()).toHaveTextContent(plan.exclusion_rule!);
    expect(outputHealth()).toHaveTextContent(
      "This run was made before result checks, so no flags were recorded for it.",
    );
    fireEvent.change(screen.getByLabelText("Run / analysis"), {
      target: { value: JSON.stringify(["full-run", "same-id"]) },
    });
    // The other run's analysis loads first; its taint report follows with that run's own counts.
    expect(
      await screen.findByText(
        `${SUMMARIES.complete.tainted_samples} samples carry a taint in this run.`,
      ),
    ).toBeVisible();
    expect(taintReport()).toHaveTextContent("stream_error 2");
    expect(taintReport()).not.toHaveTextContent("qrng_degraded");
    const asked = fetcher.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.endsWith("/health"));
    expect(asked.map((url) => url.replace(/^.*\/api\/runs\//, ""))).toEqual([
      "pilot-run/health",
      "full-run/health",
    ]);
  });

  it("shows the table, the cautions and the stops for a study without a design block", async () => {
    mockFetch();
    renderPage();
    await screen.findByText(plan.claim_rule);
    const section = outputHealth();
    await within(section).findByRole("table");
    expect(within(section).getAllByRole("row")).toHaveLength(
      SUMMARIES.complete.cells.length + 1,
    );
    expect(
      within(within(section).getByRole("list", { name: "Cautions" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(SUMMARIES.complete.cautions.map((caution) => caution.text));
    expect(section).toHaveTextContent(
      "A check stopped this run 2 times. The host resumed it each time.",
    );
  });

  it("prints no count at all when the run service has no summary", async () => {
    mockFetch(false, false, 404);
    renderPage();
    await screen.findByText(plan.claim_rule);
    expect(
      await within(taintReport()).findByText(
        "The taint counts of this run could not be loaded, so none are shown.",
      ),
    ).toBeVisible();
    expect(taintReport()).not.toHaveTextContent(/\d+ (tainted|samples? carr)/);
    expect(outputHealth()).toHaveTextContent(
      "The health summary of this run could not be loaded, so no counts are shown. run health is not available",
    );
    expect(within(outputHealth()).queryByRole("table")).not.toBeInTheDocument();
    // The numerical results do not depend on the summary.
    expect(screen.getByText(plan.claim_rule)).toBeVisible();
  });

  it("shows how many samples the analysis left out, from the arm ledger of the analysis index", async () => {
    // The run the verifier saw: 16 samples failed once, were generated again and completed.
    const retried = {
      ...SUMMARIES.complete,
      taints: { stream_error: 16 },
      tainted_samples: 16,
    };
    const ledger = [
      { arm_id: "prng_seed_1", source_label: "seeded_prng", samples: 36, tainted_samples: 9, excluded_samples: 0, failed_samples: 0 },
      { arm_id: "qrng", source_label: "quantum", samples: 36, tainted_samples: 7, excluded_samples: 0, failed_samples: 0 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/health")) return new Response(JSON.stringify(retried));
        if (url.includes("/index?"))
          return new Response(JSON.stringify({ ...index, provenance: ledger }));
        if (url.includes("/tables/")) return new Response(JSON.stringify(primary));
        return new Response(JSON.stringify(study));
      }),
    );
    renderPage();
    await screen.findByText(plan.claim_rule);
    expect(
      await within(taintReport()).findByText("16 samples carry a taint in this run."),
    ).toBeVisible();
    expect(taintReport()).toHaveTextContent(
      "The clean analysis leaves out 0 samples for a taint.",
    );
    expect(taintReport()).not.toHaveTextContent(/16 tainted/);
    expect(taintReport()).toHaveTextContent(
      `Registered exclusion rule: ${plan.exclusion_rule!}`,
    );
  });

  it("does not ask for a run's health on the pre-registration and data pages", async () => {
    const fetcher = mockFetch();
    renderPage("data");
    await screen.findByRole("link", { name: "Data licence (LICENSE-data)" });
    expect(
      fetcher.mock.calls.some(([url]) => String(url).endsWith("/health")),
    ).toBe(false);
  });
});

describe("a table that is not an endpoint table", () => {
  // What `_topk_coverage_table` in the backend's analysis/endpoints.py writes for any run with token
  // records: no card, role, tier, test, correction or comparison rows.
  const coverage = {
    schema_version: 1,
    endpoint: "tier0_topk_coverage",
    precision: 6,
    available: true,
    top_logprobs: 20,
    tier0_source: "stream",
    label: "share of sampled tokens inside the returned top-20 (rank_raw < 20)",
    arms: {
      prng_seed_1: { n_tokens: 1200, coverage: 0.9975 },
      qrng: { n_tokens: 1180, coverage: 1 },
    },
  };
  function mockWithCoverage(designed: boolean, endpointTable: unknown = designed ? designedEndpointTable : primary) {
    const names = designed
      ? ["decisions", "self_referential_probe--experience_claim_rate", "tier0_topk_coverage"]
      : [tableName, "tier0_topk_coverage"];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/health")) return healthResponse(SUMMARIES.complete);
        if (url.includes("/index?"))
          return new Response(
            JSON.stringify({
              ...index,
              plan: designed ? designedPlan : plan,
              tables: names,
              figures: ["tier0_topk_coverage"],
            }),
          );
        if (url.includes("/tables/tier0_topk_coverage.json"))
          return new Response(JSON.stringify(coverage));
        if (url.includes("/tables/decisions.json"))
          return new Response(JSON.stringify(designedDecisions));
        if (url.includes("/tables/")) return new Response(JSON.stringify(endpointTable));
        return new Response(JSON.stringify(study));
      }),
    );
  }

  it.each([
    ["a study without a design block", false],
    ["a designed study", true],
  ])("is drawn as descriptive, with no correction, on %s", async (_name, designed) => {
    mockWithCoverage(designed);
    const page = renderPage();
    const heading = await screen.findByRole("heading", { level: 2, name: "Descriptive tables" });
    const section = heading.closest("section")!;
    expect(within(section).getByRole("heading", { level: 3, name: "tier0_topk_coverage" })).toBeVisible();
    expect(section).toHaveTextContent("These tables describe the run. They test nothing, so they have no p-value and no correction.");
    expect(section).toHaveTextContent("Descriptive | no test");
    expect(section).toHaveTextContent(coverage.label);
    const table = within(section).getByRole("table", { name: "tier0_topk_coverage: values by arm" });
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Arm", "n_tokens", "coverage"]);
    expect(within(table).getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual(["prng_seed_11200" + "0.9975", "qrng11801"]);
    expect(section).not.toHaveTextContent(/Holm|adjusted|p-value \d/i);
    // It is in no tier section and is not an endpoint, and the rest of the page is there.
    expect(screen.queryByRole("heading", { name: /Tier undefined|Tier NaN/ })).not.toBeInTheDocument();
    expect(page.container.textContent).not.toContain("undefined");
    expect(screen.getByRole("region", { name: "Taint report" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Output health" })).toBeVisible();
    expect(screen.getByText(/Descriptive: script-generated counts, no test\./)).toBeVisible();
  });

  it("does not go blank on an endpoint table that lacks its role, test or correction", async () => {
    const bare: Record<string, unknown> = { ...unavailable, tier: 2 };
    for (const key of ["role", "test", "correction", "card_id"]) delete bare[key];
    mockWithCoverage(false, bare);
    renderPage();
    const heading = await screen.findByRole("heading", { level: 2, name: "Tier 2 | secondary & exploratory endpoints" });
    const section = heading.closest("section")!;
    expect(section).toHaveTextContent("role and test not recorded");
    // No correction is named, so the table has no adjusted-p column.
    expect(within(within(section).getByRole("table")).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Contrast / arms", "Status", "Effect", "Confidence interval", "Raw p"]);
    expect(section).not.toHaveTextContent("undefined");
  });
});

describe("a designed analysis", () => {
  const tableNames = [
    "decisions",
    "self_referential_probe--experience_claim_rate",
    "tier0_mmd_hamming--mmd_hamming_seed_vs_seed",
  ];
  const designedStudy = (kind: string) => ({
    ...study,
    study: {
      ...study.study,
      config: { ...study.study.config, design: { claim: "difference" } },
    },
    runs: [{ run_id: "only-run", kind, state: "completed", taints: [] }],
    analyses: { "only-run": ["a_1"] },
  });
  function mockDesigned(
    decisions: unknown,
    kind = "full",
    health: ServedHealth = SUMMARIES.stopped,
  ) {
    const mock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health")) return healthResponse(health);
      if (url.includes("/index?"))
        return new Response(
          JSON.stringify({
            plan: designedPlan,
            manifest: { plan_hash: "plan-hash" },
            tables: tableNames,
            figures: [],
            provenance: [],
          }),
        );
      if (url.includes("/tables/decisions.json"))
        return new Response(JSON.stringify(decisions));
      if (url.includes("/tables/tier0_mmd_hamming"))
        return new Response(JSON.stringify(designedExploratoryTable));
      if (url.includes("/tables/"))
        return new Response(JSON.stringify(designedEndpointTable));
      return new Response(JSON.stringify(designedStudy(kind)));
    });
    vi.stubGlobal("fetch", mock);
    return mock;
  }
  const legacyTexts = [
    /Claim:/,
    /Preregistered claim rule/,
    /Seed-vs-seed controls/,
    /Epsilon sensitivity curve/,
    /claim is precluded/,
    /supported by the archived analysis/,
  ];

  it("shows the verdicts of the script first and no legacy claim field", async () => {
    mockDesigned(designedDecisions);
    const page = renderPage();
    const verdicts = await screen.findByRole("region", { name: "Verdicts" });
    expect(
      within(verdicts).getByText(/^The study as a whole is mixed or inconclusive: no hypothesis resolved to “Effect”/),
    ).toBeVisible();
    expect(within(verdicts).getAllByRole("article")).toHaveLength(3);
    // the statement and the arms of a hypothesis are read from the frozen plan
    expect(
      within(verdicts).getByText(designedPlan.design.hypotheses[2]!.statement),
    ).toBeVisible();
    expect(
      within(verdicts).getByRole("link", {
        name: "Read the decision rules in the pre-registration",
      }),
    ).toHaveAttribute("href", "/studies/demo/prereg#prereg-decision-rules");
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent),
    ).toEqual(DESIGNED_HEADINGS);
    for (const text of legacyTexts)
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    expect(page.container.querySelector(".results-endpoint")).toBeNull();
    // Designed comparison rows carry no adjusted p, so no such column exists.
    const rows = screen.getByRole("table", {
      name: "experience_claim_rate: all comparisons, uncorrected",
    });
    expect(
      within(rows)
        .getAllByRole("columnheader")
        .map((th) => th.textContent),
    ).toEqual([
      "Contrast / arms",
      "Status",
      "Effect",
      "Confidence interval",
      "Raw p",
    ]);
    expect(within(rows).getByText("0.001852")).toBeVisible();
    // The decisions table is not an endpoint table.
    expect(page.container.textContent).not.toContain("undefined");
    expect(page.container.querySelectorAll(".results-rows")).toHaveLength(2);
    expect(
      screen.getByText(
        /Endpoint unavailable: required archived endpoint rows are unavailable/,
      ),
    ).toBeVisible();
    expect(screen.getByText("Full run")).toBeVisible();
  });

  it("shows the run's open stop and cautions beside the verdicts", async () => {
    mockDesigned(designedDecisions);
    renderPage();
    await screen.findByRole("region", { name: "Verdicts" });
    const section = screen.getByRole("region", { name: "Output health" });
    await within(section).findByRole("table");
    expect(section).toHaveTextContent(
      "A check stopped this run 2 times. The host resumed it once; one stop has not been resumed.",
    );
    expect(
      within(within(section).getByRole("list", { name: "Cautions" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(SUMMARIES.stopped.cautions.map((caution) => caution.text));
    expect(
      screen.getByRole("region", { name: "Taint report" }),
    ).toHaveTextContent("0 samples carry a taint in this run.");
  });

  it("labels a pilot run of a designed study as not confirmatory", async () => {
    mockDesigned(notConfirmatory("pilot_run"), "pilot");
    renderPage();
    expect(
      await screen.findByText("Not confirmatory: this is a pilot run."),
    ).toBeVisible();
    expect(
      screen.getByText("Pilot run - labelled non-confirmatory"),
    ).toBeVisible();
    expect(screen.queryByText(/calibration only/)).not.toBeInTheDocument();
  });

  it("says so when the decisions table cannot be read, and still shows no legacy claim", async () => {
    mockDesigned({ ...designedDecisions, rows: [{ verdict: "won" }] });
    const page = renderPage();
    const notice = await screen.findByText(
      /its decisions table is missing or could not be read/,
    );
    expect(notice.closest("[role=alert]")).not.toBeNull();
    expect(notice).toHaveTextContent("No verdict is shown.");
    expect(
      screen.getByRole("table", {
        name: "experience_claim_rate: all comparisons, uncorrected",
      }),
    ).toBeVisible();
    for (const text of legacyTexts)
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    expect(page.container.querySelector(".results-endpoint")).toBeNull();
    expect(page.container.querySelector(".results-stamp")).toBeNull();
  });
});
