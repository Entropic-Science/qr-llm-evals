import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { HealthSummary } from "@qr-evals/contracts";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SUMMARIES } from "../run/checks-fixture";
import { HaltHistory, OutputHealth, OutputHealthUnavailable } from "./OutputHealth";
import { TaintBanner } from "./TaintBanner";

afterEach(cleanup);

function health(summary: HealthSummary, variant: "run" | "results" = "results") {
  render(<OutputHealth summary={summary} variant={variant} />);
  return screen.getByRole("region", { name: "Output health" });
}
/** The table row of one arm and eval pair. */
function row(section: HTMLElement, armId: string, cardId: string): HTMLElement {
  const found = within(section).getAllByRole("row").find((candidate) => {
    const cells = within(candidate).queryAllByRole("rowheader");
    return cells[0]?.textContent?.startsWith(armId) && within(candidate).queryByText(cardId) !== null;
  });
  if (!found) throw new Error(`no row for ${armId} and ${cardId}`);
  return found;
}
const cell = (line: HTMLElement, label: string) => line.querySelector(`td[data-label="${label}"]`) as HTMLElement;

describe("OutputHealth", () => {
  it("lists every arm and eval pair of the summary with its counts", () => {
    const summary = SUMMARIES.complete;
    const section = health(summary);
    expect(within(section).getAllByRole("row")).toHaveLength(summary.cells.length + 1);
    expect(within(section).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Arm", "Eval", "Completed", "Not completed", "Tainted", "Flagged", "Judgements"]);

    const quantum = row(section, "qrng", "eval_awareness");
    expect(within(quantum).getByRole("rowheader")).toHaveTextContent("qrngquantum arm");
    expect(cell(quantum, "Completed")).toHaveTextContent("38 of 40 planned");
    expect(cell(quantum, "Not completed")).toHaveTextContent("2");
    expect(cell(quantum, "Tainted")).toHaveTextContent("engine fallback 1");
    expect(cell(quantum, "Tainted")).toHaveTextContent("stream error 2");
    // Flags carry the catalogue's label, taken from the summary's own legend.
    expect(cell(quantum, "Flagged")).toHaveTextContent("empty reply 1");
    expect(cell(quantum, "Flagged")).toHaveTextContent("ended at the token limit 30");
    expect(cell(quantum, "Flagged")).toHaveTextContent("ends in a loop 3");
    expect(cell(quantum, "Flagged")).toHaveTextContent("judge reply without a score 2");
    expect(cell(quantum, "Judgements")).toHaveTextContent("scored 34");
    expect(cell(quantum, "Judgements")).toHaveTextContent("no judge reply 1");
    expect(cell(quantum, "Judgements")).toHaveTextContent("judge reply without a score 2");
    expect(cell(quantum, "Judgements")).toHaveTextContent("judge score not allowed 1");
    // Scored first, then the judgements without a score, whatever order the summary lists them in.
    expect([...cell(quantum, "Judgements").querySelectorAll("li")].map((item) => item.getAttribute("data-kind"))).toEqual(["ok", "failed", "unreadable", "out_of_range"]);
    expect(Object.keys(summary.cells[0]!.judgements)).toEqual(["failed", "ok", "out_of_range", "unreadable"]);

    const control = row(section, "bent", "eval_awareness");
    expect(within(control).getByRole("rowheader")).toHaveTextContent("biased pseudo-random control arm");
    expect(cell(control, "Flagged")).toHaveTextContent(/^none$/);
    expect(cell(control, "Tainted")).toHaveTextContent(/^none$/);
    // An eval without a judge has no judge rows; that is not "0 scored".
    expect(cell(row(section, "bent", "psychobench"), "Judgements")).toHaveTextContent(/^no judge rows$/);
  });

  it("prints every number of the summary and no other count", () => {
    for (const summary of Object.values(SUMMARIES)) {
      const section = health(summary);
      for (const line of summary.cells) {
        const drawn = row(section, line.arm_id, line.card_id);
        const numbers = (label: string) => [...cell(drawn, label).querySelectorAll("b")].map((b) => Number(b.textContent)).sort((a, b) => a - b);
        const stored = (counts: Record<string, number | undefined> | null) => Object.values(counts ?? {}).filter((n): n is number => typeof n === "number" && n > 0).sort((a, b) => a - b);
        expect(numbers("Tainted")).toEqual(stored(line.taints));
        expect(numbers("Flagged")).toEqual(stored(line.flags));
        expect(numbers("Judgements")).toEqual(stored(line.judgements));
        expect(cell(drawn, "Completed").textContent).toBe(line.planned == null ? `${line.completed}plan not recorded` : `${line.completed} of ${line.planned} planned`);
        expect(cell(drawn, "Not completed").textContent).toBe(String(line.errored));
      }
      cleanup();
    }
  });

  it("prints the cautions as the harness wrote them, above the table", () => {
    const summary = SUMMARIES.complete;
    const section = health(summary);
    const cautions = within(within(section).getByRole("list", { name: "Cautions" })).getAllByRole("listitem");
    expect(cautions.map((item) => item.textContent)).toEqual(summary.cautions.map((caution) => caution.text));
    expect(summary.cautions.length).toBeGreaterThan(0);
    expect(cautions[0]).toHaveTextContent('On card eval_awareness, the flag "ends in a loop" is on at least 5.0% of an arm\'s completed samples: arm qrng (quantum arm) 3 of 38 (7.9%); arm prng7 (seeded pseudo-random arm) 1 of 40 (2.5%); arm bent (biased pseudo-random control arm) 0 of 40 (0.0%).');
    const list = within(section).getByRole("list", { name: "Cautions" });
    expect(list.compareDocumentPosition(within(section).getByRole("table")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(section).queryByText("The summary raises no caution for this run.")).not.toBeInTheDocument();
  });

  it("marks in the table the flags a caution is about, on every arm of that eval", () => {
    const section = health(SUMMARIES.complete);
    const marked = [...section.querySelectorAll(".health-kind--caution")].map((item) => `${item.closest("tr")?.querySelector("th code")?.textContent}/${item.getAttribute("data-kind")}`);
    expect(marked.sort()).toEqual(["prng7/repetition", "qrng/judge_unreadable", "qrng/repetition"]);
  });

  it("counts replies that ended at the token limit and never treats the count as a warning", () => {
    const summary = SUMMARIES.complete;
    // The fixture has an arm in which every reply of an eval ended at the token limit.
    expect(summary.cells.some((line) => line.flags?.truncated === line.completed && line.completed > 0)).toBe(true);
    expect(summary.cautions.some((caution) => caution.flag === "truncated" || /token limit/.test(caution.text))).toBe(false);
    const section = health(summary);
    const truncated = [...section.querySelectorAll('td[data-label="Flagged"] li[data-kind="truncated"]')];
    expect(truncated.length).toBe(summary.cells.filter((line) => (line.flags?.truncated ?? 0) > 0).length);
    for (const item of truncated) {
      expect(item).toHaveClass("health-kind--quiet");
      expect(item).not.toHaveClass("health-kind--caution");
    }
    expect(within(section).getByRole("list", { name: "Cautions" })).not.toHaveTextContent("token limit");
    // The legend says why, in the words the summary's legend backs (`cautions: false`).
    const legend = section.querySelector('.health-legend [data-kind="truncated"]');
    expect(legend).toHaveTextContent("Flag: ended at the token limit");
    expect(legend).toHaveTextContent("The reply ended because it reached the token limit. This flag is counted and shown, and it never raises a caution.");
    expect(section.querySelector('.health-legend [data-kind="repetition"]')).not.toHaveTextContent("never raises a caution");
  });

  it("explains every flag kind with the catalogue's label and sentence", () => {
    const summary = SUMMARIES.complete;
    const section = health(summary);
    for (const legend of summary.flag_kinds) {
      const entry = section.querySelector(`.health-legend [data-kind="${legend.kind}"]`);
      expect(entry).toHaveTextContent(`Flag: ${legend.label}`);
      expect(entry).toHaveTextContent(legend.sentence);
    }
    expect(section).toHaveTextContent("A flag marks a sample that looks wrong. It does not remove the sample: a flagged sample stays in every analysis.");
  });

  it("lists the stops of a run that was stopped and resumed", () => {
    const summary = SUMMARIES.complete;
    const section = health(summary);
    expect(section).toHaveTextContent("A check stopped this run 2 times. The host resumed it each time.");
    const stops = [...section.querySelectorAll(".health-halts li")];
    expect(stops).toHaveLength(2);
    stops.forEach((stop, index) => {
      expect(stop).toHaveTextContent(summary.halts[index]!.sentence);
      expect(stop).toHaveTextContent("Resumed by the host on ");
      expect(stop).toHaveAttribute("data-open", "false");
    });
    expect(section).not.toHaveTextContent("Not resumed.");
  });

  it("shows a run with an open stop as stopped and not resumed", () => {
    const summary = SUMMARIES.stopped;
    const section = health(summary);
    expect(section).toHaveTextContent("A check stopped this run 2 times. The host resumed it once; one stop has not been resumed.");
    const [first, second] = [...section.querySelectorAll(".health-halts li")];
    expect(first).toHaveTextContent("Resumed by the host on ");
    expect(second).toHaveAttribute("data-open", "true");
    // The sentence is the harness's own, read from the fixture, so a reworded rule does not break this test.
    expect(second).toHaveTextContent(summary.halts[1]!.sentence);
    expect(second).toHaveTextContent("were all empty");
    expect(second).toHaveTextContent("Not resumed.");
    const cautions = within(within(section).getByRole("list", { name: "Cautions" })).getAllByRole("listitem").map((item) => item.textContent);
    expect(cautions).toEqual(summary.cautions.map((caution) => caution.text));
    expect(cautions[0]).toBe("The run is in the state paused, not complete: the counts describe what is stored so far.");
    expect(cautions[1]).toMatch(/^A check stopped the run and the run has not been resumed\. /);
    // A pair that has stored nothing is a row of zeros with its plan, not a missing row.
    expect(cell(row(section, "qrng", "psychobench"), "Completed")).toHaveTextContent("0 of 40 planned");
  });

  it("says a run was made before result checks and prints no flag count for it", () => {
    const summary = SUMMARIES.legacy;
    expect(summary.checks_version).toBeNull();
    const section = health(summary);
    expect(section).toHaveTextContent("This run was made before result checks, so no flags were recorded for it.");
    for (const line of summary.cells) expect(cell(row(section, line.arm_id, line.card_id), "Flagged")).toHaveTextContent(/^not recorded$/);
    // Nothing on the page reads as "no flags", "no cautions" or "never stopped": none of that was recorded.
    expect(section.querySelectorAll('td[data-label="Flagged"] b')).toHaveLength(0);
    expect(section).not.toHaveTextContent("The summary raises no caution for this run.");
    expect(section).not.toHaveTextContent("No check stopped this run.");
    expect(section).not.toHaveTextContent("a flagged sample stays in every analysis");
    expect(section.querySelector(".health-legend [data-kind]")).toBeNull();
    // What an old run does have is shown: its taints and its judge rows, with the row that cannot say why it has no score.
    const quantum = row(section, "qrng", "eval_awareness");
    expect(cell(quantum, "Tainted")).toHaveTextContent("qrng degraded 1");
    expect(cell(quantum, "Judgements")).toHaveTextContent("scored 1");
    expect(cell(quantum, "Judgements")).toHaveTextContent("no score, reason not recorded 1");
  });

  it("says that nothing is stored for a run that has not generated yet", () => {
    const summary = SUMMARIES.fresh;
    const section = health(summary);
    expect(within(section).queryByRole("table")).not.toBeInTheDocument();
    expect(section).toHaveTextContent("No sample of this run is stored yet.");
    expect(section).toHaveTextContent("No check stopped this run.");
    expect(within(within(section).getByRole("list", { name: "Cautions" })).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["The run is in the state ready, not complete: the counts describe what is stored so far."]);
  });

  it("says so when a checked run has no caution", () => {
    const section = health({ ...SUMMARIES.complete, cautions: [] });
    expect(within(section).queryByRole("list", { name: "Cautions" })).not.toBeInTheDocument();
    expect(section).toHaveTextContent("The summary raises no caution for this run.");
    expect(section.querySelectorAll(".health-kind--caution")).toHaveLength(0);
  });

  it("uses the run page's heading on the run page", () => {
    const section = health(SUMMARIES.complete, "run");
    expect(section).toHaveClass("run-panel");
    expect(within(section).getByRole("heading", { level: 2, name: "Output health" })).toBeInTheDocument();
  });
});

describe("HaltHistory", () => {
  const [first, second] = SUMMARIES.stopped.halts as [HealthSummary["halts"][number], HealthSummary["halts"][number]];
  it.each([
    [[], "No check stopped this run."],
    [[first], "A check stopped this run once. The host resumed it."],
    [[second], "A check stopped this run once. It has not been resumed."],
    [[second, { ...second, at: "2026-11-22T16:00:00Z" }], "A check stopped this run 2 times. None of the stops has been resumed."],
    [[first, second, { ...second, at: "2026-11-22T16:00:00Z" }], "A check stopped this run 3 times. The host resumed it once; 2 stops have not been resumed."],
  ])("counts the stops and the resumes (%#)", (halts, text) => {
    const { container } = render(<HaltHistory halts={halts} />);
    expect(container.querySelector("p")).toHaveTextContent(text);
    expect(container.querySelectorAll("li")).toHaveLength(halts.length);
  });
});

describe("OutputHealthUnavailable", () => {
  it("prints no count while the summary loads or when it cannot be read", () => {
    render(<OutputHealthUnavailable loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the health summary of this run…");
    cleanup();
    render(<OutputHealthUnavailable loading={false} message="Request failed with status 404" />);
    expect(screen.getByRole("status")).toHaveTextContent("The health summary of this run could not be loaded, so no counts are shown. Request failed with status 404");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("TaintBanner", () => {
  it("takes its counts from the health summary", () => {
    const summary = SUMMARIES.complete;
    render(<TaintBanner summary={summary} rule="Tainted samples are excluded." />);
    const report = screen.getByRole("region", { name: "Taint report" });
    expect(report).toHaveClass("results-notice");
    expect(report).toHaveTextContent(`${summary.tainted_samples} samples carry a taint in this run.`);
    const kinds = within(within(report).getByRole("list", { name: "Taints by kind" })).getAllByRole("listitem").map((item) => item.textContent);
    expect(kinds).toEqual(Object.entries(summary.taints).map(([kind, count]) => `${kind} ${count}`));
    expect(report).toHaveTextContent("Registered exclusion rule: Tainted samples are excluded.");
    // Without the analysis's own count the report does not let the tainted count stand in for it.
    expect(report).toHaveTextContent("How many of them the analysis leaves out is not recorded here.");
  });

  it("says how many samples the analysis leaves out, apart from how many carry a taint", () => {
    const summary = { taints: { stream_error: 16 }, tainted_samples: 16 };
    render(<TaintBanner summary={summary} excluded={0} rule="Exclude fallback/degraded/error/aborted samples." />);
    const report = screen.getByRole("region", { name: "Taint report" });
    expect(within(report).getAllByRole("paragraph").map((p) => p.textContent).slice(0, 2)).toEqual([
      "16 samples carry a taint in this run.",
      "The clean analysis leaves out 0 samples for a taint. A taint marks a sample and does not always remove it: a sample whose only taint is stream_error and that was generated again and completed stays in the analysis.",
    ]);
    // No sentence puts "16" beside "tainted samples", which is how the rule below names what it leaves out.
    expect(report).not.toHaveTextContent(/16 tainted/);
    cleanup();
    render(<TaintBanner summary={{ taints: { engine_fallback: 1 }, tainted_samples: 1 }} excluded={1} rule="Rule." />);
    const same = screen.getByRole("region", { name: "Taint report" });
    expect(same).toHaveTextContent("1 sample carries a taint in this run.");
    expect(same).toHaveTextContent("The clean analysis leaves out 1 sample for a taint.");
    expect(same).not.toHaveTextContent("does not always remove it");
  });

  it("ties the sentence about a retried sample to the analysis's exclusion rule", () => {
    const source = readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals/analysis/endpoints.py"), "utf-8");
    expect(source).toMatch(/if row\.get\("kind"\) == STREAM_ERROR and ref in completed:\s+continue/);
  });

  it("is calm for a run without taints and says one sample in the singular", () => {
    render(<TaintBanner summary={SUMMARIES.stopped} />);
    const clean = screen.getByRole("region", { name: "Taint report" });
    expect(clean).toHaveClass("results-integrity");
    expect(clean).toHaveTextContent("0 samples carry a taint in this run.");
    expect(within(clean).queryByRole("list")).not.toBeInTheDocument();
    cleanup();
    render(<TaintBanner summary={SUMMARIES.legacy} />);
    expect(screen.getByRole("region", { name: "Taint report" })).toHaveTextContent("1 sample carries a taint in this run.");
  });

  it("prints no zero when there is no summary to count from", () => {
    render(<TaintBanner summary={undefined} rule="Tainted samples are excluded." />);
    const report = screen.getByRole("region", { name: "Taint report" });
    expect(report).toHaveTextContent("The taint counts of this run could not be loaded, so none are shown.");
    expect(report).not.toHaveTextContent(/\d/);
    expect(report).toHaveClass("results-notice");
  });
});

describe("the sentences this file writes about the checks", () => {
  // The page may only say what the harness does (invariant 10). These are the claims it adds to the summary's
  // own texts; each is tied to the line of the backend that makes it true.
  const backend = (path: string) => readFileSync(resolve(process.cwd(), "../../backend/src/qr_llm_evals", path), "utf-8");

  it("a flag is not an exclusion: no analysis module reads the flags file", () => {
    expect(backend("cards/result_checks.py")).toContain("**A flag is not an exclusion.**");
    expect(backend("analysis/endpoints.py")).not.toMatch(/flags_jsonl|iter_flag_rows|ResultFlag/);
    expect(backend("analysis/run.py")).not.toMatch(/flags_jsonl|iter_flag_rows|ResultFlag/);
  });

  it("the column notes repeat what the summary's fields are", () => {
    const source = backend("store/health.py");
    expect(source).toContain("Samples that have a completed reply.");
    expect(source).toContain('"Samples that were tried and have no completed reply: every attempt ended in an "');
    expect(source).toContain("the latest row of a sample counts");
    // A judge row with neither status nor score is the one the table calls "no score, reason not recorded".
    expect(source).toContain('if row.get("status") is None and row.get("score") is None:');
  });
});
