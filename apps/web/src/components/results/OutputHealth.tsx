import type { CellHealth, Halt, HealthSummary } from "@qr-evals/contracts";
import { useId } from "react";
import { timeText } from "../run/checks";
import "../../styles/checks.css";

/**
 * "Output health": what a run's files hold for each arm and eval, read from the run's health summary.
 *
 * The summary is written by the harness (`store/health.py`). The cautions, the flag labels and their meanings
 * and the sentences of the stops are printed as they come; this file adds the table around them and says
 * "not recorded" where the summary has no count, never a zero.
 */

type Variant = "run" | "results";
type Counts = Record<string, number | undefined>;

const spaced = (kind: string) => kind.replaceAll("_", " ");
const times = (count: number) => count === 1 ? "once" : `${count} times`;

function entries(counts: Counts | null | undefined): [string, number][] {
  return Object.entries(counts ?? {}).filter((entry): entry is [string, number] => typeof entry[1] === "number" && entry[1] > 0);
}

/** A judgement status in words. The three failures use the label of the flag raised for them. */
function judgementLabel(status: string, flagLabel: (kind: string) => string): string {
  if (status === "ok") return "scored";
  if (status === "failed") return flagLabel("judge_failed");
  if (status === "unreadable") return flagLabel("judge_unreadable");
  if (status === "out_of_range") return flagLabel("judge_out_of_range");
  // A judge row from before rows had a status: it has no score and cannot say whether a reply came.
  if (status === "no_score") return "no score, reason not recorded";
  return spaced(status);
}

/** Scored first, then the three ways a judgement can be without a score, then the rows that cannot say which. */
const JUDGEMENT_ORDER = ["ok", "failed", "unreadable", "out_of_range", "no_score"];
function judgementEntries(counts: Counts): [string, number][] {
  const rank = (status: string) => { const at = JUDGEMENT_ORDER.indexOf(status); return at < 0 ? JUDGEMENT_ORDER.length : at; };
  return entries(counts).sort((a, b) => rank(a[0]) - rank(b[0]));
}

function KindList({ items, empty }: { items: { key: string; label: string; count: number; tone?: "caution" | "quiet" }[]; empty: string }) {
  if (!items.length) return <span className="health-none">{empty}</span>;
  return <ul className="health-kinds">{items.map((item) => <li key={item.key} className={item.tone ? `health-kind--${item.tone}` : undefined} data-kind={item.key}>
    <span>{item.label}</span> <b>{item.count}</b>
  </li>)}</ul>;
}

/** The run's stops by a check, oldest first, each with what became of it. */
export function HaltHistory({ halts }: { halts: readonly Halt[] }) {
  if (!halts.length) return <p className="health-halts__none">No check stopped this run.</p>;
  const resumed = halts.filter((halt) => halt.resumed_at != null).length;
  const open = halts.length - resumed;
  return <div className="health-halts">
    <p>
      A check stopped this run {times(halts.length)}.{" "}
      {resumed === halts.length ? (halts.length === 1 ? "The host resumed it." : "The host resumed it each time.") : null}
      {resumed > 0 && open > 0 ? `The host resumed it ${times(resumed)}; ${open === 1 ? "one stop has" : `${open} stops have`} not been resumed.` : null}
      {resumed === 0 ? (halts.length === 1 ? "It has not been resumed." : "None of the stops has been resumed.") : null}
    </p>
    <ol>{halts.map((halt, index) => <li key={`${halt.at}-${index}`} data-rule={halt.rule} data-open={halt.resumed_at == null}>
      <time dateTime={halt.at}>{timeText(halt.at)}</time>
      <span>{halt.sentence}</span>
      <small>{halt.resumed_at != null ? <>Resumed by the host on <time dateTime={halt.resumed_at}>{timeText(halt.resumed_at)}</time>.</> : "Not resumed."}</small>
    </li>)}</ol>
  </div>;
}

function Row({ cell, checked, flagLabel, quiet, cautioned }: {
  cell: CellHealth;
  checked: boolean;
  flagLabel: (kind: string) => string;
  quiet: ReadonlySet<string>;
  cautioned: ReadonlySet<string>;
}) {
  const flags = entries(cell.flags).map(([kind, count]) => {
    const tone = cautioned.has(`${cell.card_id}/${kind}`) ? "caution" as const : quiet.has(kind) ? "quiet" as const : undefined;
    return { key: kind, label: flagLabel(kind), count, ...(tone ? { tone } : {}) };
  });
  return <tr>
    <th scope="row"><code>{cell.arm_id}</code><small>{cell.role}</small></th>
    <td data-label="Eval"><code>{cell.card_id}</code></td>
    <td data-label="Completed">{cell.completed}{cell.planned != null ? <> of {cell.planned} planned</> : <small>plan not recorded</small>}</td>
    <td data-label="Not completed">{cell.errored}</td>
    <td data-label="Tainted"><KindList items={entries(cell.taints).map(([kind, count]) => ({ key: kind, label: spaced(kind), count }))} empty="none" /></td>
    <td data-label="Flagged">{checked && cell.flags != null ? <KindList items={flags} empty="none" /> : <span className="health-none">not recorded</span>}</td>
    <td data-label="Judgements"><KindList items={judgementEntries(cell.judgements).map(([status, count]) => ({ key: status, label: judgementLabel(status, flagLabel), count }))} empty="no judge rows" /></td>
  </tr>;
}

export function OutputHealth({ summary, variant = "results" }: { summary: HealthSummary; variant?: Variant }) {
  const titleId = useId();
  const checked = summary.checks_version != null;
  const labels = new Map(summary.flag_kinds.map((legend) => [legend.kind as string, legend.label]));
  const flagLabel = (kind: string) => labels.get(kind) ?? spaced(kind);
  const quiet = new Set(summary.flag_kinds.filter((legend) => !legend.cautions).map((legend) => legend.kind as string));
  const cautioned = new Set(summary.cautions.flatMap((caution) => caution.kind === "flag_share" && caution.card_id && caution.flag ? [`${caution.card_id}/${caution.flag}`] : []));
  const title = <h2 id={titleId}>Output health</h2>;
  return <section className={variant === "run" ? "run-panel output-health" : "output-health"} aria-labelledby={titleId}>
    {variant === "run" ? <header className="run-section-heading"><div><p className="eyebrow">Result checks</p>{title}</div><span className="run-section-note">from the stored rows</span></header> : title}
    {checked
      ? <p className="output-health__lede">What the run's files hold for each arm and eval, and what the harness's result checks flagged. A flag marks a sample that looks wrong. It does not remove the sample: a flagged sample stays in every analysis.</p>
      : <p className="output-health__lede" data-legacy="true">{summary.note ?? "This run has no result-check version, so no flags were recorded for it."}</p>}
    {summary.cautions.length
      ? <ul className="health-cautions" aria-label="Cautions">{summary.cautions.map((caution, index) => <li key={`${caution.kind}-${index}`} data-kind={caution.kind}>{caution.text}</li>)}</ul>
      : checked ? <p className="health-cautions__none">The summary raises no caution for this run.</p> : null}
    {checked || summary.halts.length ? <HaltHistory halts={summary.halts} /> : null}
    {summary.cells.length
      ? <div className="health-table">
          <table>
            <caption>One row for each arm and eval. The harness's own texts call an eval a card.</caption>
            <thead><tr><th scope="col">Arm</th><th scope="col">Eval</th><th scope="col">Completed</th><th scope="col">Not completed</th><th scope="col">Tainted</th><th scope="col">Flagged</th><th scope="col">Judgements</th></tr></thead>
            <tbody>{summary.cells.map((cell) => <Row key={`${cell.arm_id}/${cell.card_id}`} cell={cell} checked={checked} flagLabel={flagLabel} quiet={quiet} cautioned={cautioned} />)}</tbody>
          </table>
        </div>
      : <p className="run-unavailable">No sample of this run is stored yet.</p>}
    <details className="health-legend">
      <summary>How to read this table</summary>
      <dl>
        <div><dt>Completed</dt><dd>Samples that have a completed reply, beside the number the runner planned when it recorded one.</dd></div>
        <div><dt>Not completed</dt><dd>Samples that were tried and have no completed reply: every attempt ended in an error or was cancelled.</dd></div>
        <div><dt>Tainted</dt><dd>Samples that carry a taint, by kind.</dd></div>
        <div><dt>Judgements</dt><dd>Samples by what became of their judgement. The latest judge row of a sample counts.</dd></div>
        {checked ? summary.flag_kinds.map((legend) => <div key={legend.kind} data-kind={legend.kind}>
          <dt>Flag: {legend.label}</dt>
          <dd>{legend.sentence}{legend.cautions ? null : " This flag is counted and shown, and it never raises a caution."}</dd>
        </div>) : null}
      </dl>
    </details>
  </section>;
}

/** The section while the summary is on its way, or when the run service could not give one. */
export function OutputHealthUnavailable({ loading, message, variant = "results" }: { loading: boolean; message?: string | undefined; variant?: Variant }) {
  const titleId = useId();
  const title = <h2 id={titleId}>Output health</h2>;
  return <section className={variant === "run" ? "run-panel output-health" : "output-health"} aria-labelledby={titleId}>
    {variant === "run" ? <header className="run-section-heading"><div><p className="eyebrow">Result checks</p>{title}</div></header> : title}
    <p role="status" className="run-unavailable">{loading ? "Loading the health summary of this run…" : `The health summary of this run could not be loaded, so no counts are shown. ${message ?? ""}`.trim()}</p>
  </section>;
}
