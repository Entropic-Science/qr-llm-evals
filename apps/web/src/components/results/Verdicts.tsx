import type { DecisionRow, DecisionsTable } from "@qr-evals/contracts";
import type { ReactNode } from "react";
import { Link } from "wouter";

import {
  NOT_CONFIRMATORY_TEXT,
  REASON_TEXT,
  STUDY_VERDICT_TEXT,
  TEST_TEXT,
  VERDICT_STAMP,
  epsilonText,
  negativeControlText,
  pRelation,
  pText,
  percentText,
  tableNumber,
} from "./verdict-text";

/** What the frozen plan registered for one hypothesis, where the plan can be read. */
interface Registered {
  statement: string | null;
  sided: string | null;
  quantum: string[];
  control: string[];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The registered hypotheses of a frozen plan by id; empty when the plan has no design block. */
function registeredHypotheses(plan: unknown): Map<string, Registered> {
  const found = new Map<string, Registered>();
  if (typeof plan !== "object" || plan === null) return found;
  const { design, contrasts } = plan as { design?: unknown; contrasts?: unknown };
  const hypotheses = typeof design === "object" && design !== null ? (design as { hypotheses?: unknown }).hypotheses : null;
  if (!Array.isArray(hypotheses)) return found;
  for (const entry of hypotheses) {
    if (typeof entry !== "object" || entry === null) continue;
    const hypothesis = entry as { id?: unknown; statement?: unknown; sided?: unknown; contrast?: unknown };
    if (typeof hypothesis.id !== "string") continue;
    const contrast: unknown = Array.isArray(contrasts) && typeof hypothesis.contrast === "number" ? contrasts[hypothesis.contrast] : null;
    const arms = typeof contrast === "object" && contrast !== null ? (contrast as { a?: unknown; b?: unknown }) : {};
    found.set(hypothesis.id, {
      statement: typeof hypothesis.statement === "string" ? hypothesis.statement : null,
      sided: hypothesis.sided === "two_sided" ? "two-sided" : hypothesis.sided === "one_sided_upper" ? "one-sided, upper tail" : null,
      quantum: strings(arms.a),
      control: strings(arms.b),
    });
  }
  return found;
}

function Stamp({ kind, label, glyph }: { kind: string; label: string; glyph: string }) {
  return <span className={`results-stamp results-stamp--${kind}`}>
    <span className="results-stamp__glyph" aria-hidden="true">{glyph}</span>{label}
  </span>;
}

type GateState = "passed" | "failed" | "none";
const GATE_GLYPH: Readonly<Record<GateState, string>> = { passed: "✓", failed: "✕", none: "–" };

function Gate({ name, state, word, children }: { name: string; state: GateState; word: string; children: ReactNode }) {
  return <li className={`results-gate results-gate--${state}`}>
    <span className="results-gate__name">{name}</span>
    <strong className="results-gate__state"><span aria-hidden="true">{GATE_GLYPH[state]}</span> {word}</strong>
    <span className="results-gate__detail">{children}</span>
  </li>;
}

function NegativeControlGate({ row, table }: { row: DecisionRow; table: DecisionsTable }) {
  const { passed, text } = negativeControlText(row, table);
  return <Gate name="Negative control (seed against seed)" state={passed ? "passed" : "failed"} word={passed ? "Passed" : "Failed"}>{text}</Gate>;
}

function PositiveControlGate({ row, precision }: { row: DecisionRow; precision: number }) {
  const { epsilon, status, p_value: p } = row.positive_control;
  const name = epsilon === null ? "Positive control (biased sampler)" : `Positive control (biased sampler, ε = ${epsilonText(epsilon)})`;
  const tail = p === null ? "." : `, ${pRelation(p, precision)}.`;
  if (status === "passed") return <Gate name={name} state="passed" word="Passed">The assay detected the known sampler bias{tail}</Gate>;
  if (status === "failed") return <Gate name={name} state="failed" word="Failed">The assay did not detect the known sampler bias{tail}</Gate>;
  if (epsilon === null) {
    return <Gate name={name} state="none" word="Not registered">This study registered no positive control, so no hypothesis can resolve to equivalent.</Gate>;
  }
  return <Gate name={name} state="none" word="Not run">The registered positive control produced no comparison.</Gate>;
}

function CompletenessGate({ row, required }: { row: DecisionRow; required: number }) {
  const name = "Completeness";
  if (row.completeness === null) {
    return row.verdict === "unavailable"
      ? <Gate name={name} state="none" word="Not assessed">The comparison was not computed.</Gate>
      : <Gate name={name} state="none" word="Not applied">No planned amount of data is registered for this comparison.</Gate>;
  }
  // The script applies this gate before every later one, so a computed
  // comparison failed it exactly when the recorded reason says so.
  const failed = row.reason === "incomplete";
  return <Gate name={name} state={failed ? "failed" : "passed"} word={failed ? "Failed" : "Passed"}>
    {percentText(row.completeness)} of the planned data was analysed; at least {percentText(required)} is required.
  </Gate>;
}

/** One figure of a hypothesis: a number of the table, or a sentence where there is none. */
function Figure({ term, number, text = "Not computed.", note, wide = false }: { term: string; number?: string | null; text?: string; note?: ReactNode; wide?: boolean }) {
  return <div className={wide ? "is-wide" : undefined}>
    <dt>{term}</dt>
    <dd>
      {number == null ? <span className="results-verdict__prose">{text}</span> : <span className="results-verdict__number">{number}</span>}
      {note ? <small>{note}</small> : null}
    </dd>
  </div>;
}

function tests(count: number, kind: string): string {
  return count === 1 ? `1 ${kind} test` : `${count} ${kind} tests`;
}

function Hypothesis({ row, table, registered }: { row: DecisionRow; table: DecisionsTable; registered: Registered | undefined }) {
  const stamp = VERDICT_STAMP[row.verdict];
  const test = TEST_TEXT[row.test];
  const { precision } = table;
  const headingId = `verdict-${row.hypothesis_id}`;
  const cell = `${row.model} · ${row.sampling}`;
  const comparison = registered && registered.quantum.length > 0 && registered.control.length > 0
    ? `${registered.quantum.join(", ")} (quantum) against ${registered.control.join(", ")} (seeded pseudo-random), ${cell}.`
    : `Quantum against seeded pseudo-random sampling, ${cell}.`;
  return <li>
    <article className={`results-verdict results-verdict--${row.verdict}`} aria-labelledby={headingId}>
      <header className="results-verdict__head">
        <p className="eyebrow">{row.hypothesis_id} · {cell}</p>
        <h3 id={headingId}>{row.label}</h3>
        <p className="results-verdict__stamps">
          <Stamp kind={row.verdict} label={stamp.label} glyph={stamp.glyph} />
          {!table.confirmatory && <span className="results-stamp__note">Not confirmatory</span>}
        </p>
        <p className="results-verdict__wording">{row.wording}</p>
        {row.reason !== null && <p className="results-verdict__reason">
          <strong>Why {stamp.label.toLowerCase()}:</strong> {REASON_TEXT[row.reason]}. <code>{row.reason}</code>
        </p>}
        <dl className="results-verdict__registered">
          {registered?.statement && <div><dt>Registered hypothesis</dt><dd>{registered.statement}</dd></div>}
          <div><dt>Comparison</dt><dd>{comparison}</dd></div>
          <div><dt>Outcome</dt><dd>Endpoint <code>{row.endpoint}</code> of eval card <code>{row.card_id}</code>.</dd></div>
          <div><dt>Test</dt><dd>{test.name}{registered?.sided ? `; ${registered.sided}` : ""}.</dd></div>
        </dl>
      </header>
      <div className="results-verdict__evidence">
        <dl className="results-verdict__figures">
          <Figure term="Estimate" number={row.estimate === null ? null : tableNumber(row.estimate)} note={row.estimate === null ? undefined : test.estimate} />
          <Figure
            term={`${percentText(row.ci_level)} interval`}
            number={row.ci_lower !== null && row.ci_upper !== null ? `[${tableNumber(row.ci_lower)}, ${tableNumber(row.ci_upper)}]` : null}
            text={row.estimate === null ? "Not computed." : "This test reports no interval."}
          />
          <Figure term="p" number={row.p_value === null ? null : pText(row.p_value, precision)} />
          <Figure
            term="Holm-adjusted p"
            number={row.p_adjusted === null ? null : pText(row.p_adjusted, precision)}
            note={`Corrected over ${tests(table.families.difference, "difference")}; the level is α = ${tableNumber(table.alpha)}.`}
          />
          {row.margin === null
            ? <Figure wide term="Registered margin" text="None. No equivalence test applies to this outcome." />
            : <>
              <Figure wide term="Registered margin" number={`±${tableNumber(row.margin)}`} />
              <Figure term="Equivalence p" number={row.p_equivalence === null ? null : pText(row.p_equivalence, precision)} />
              <Figure
                term="Holm-adjusted equivalence p"
                number={row.p_equivalence_adjusted === null ? null : pText(row.p_equivalence_adjusted, precision)}
                note={`Corrected over ${tests(table.families.equivalence, "equivalence")}; the level is α = ${tableNumber(table.alpha)}.`}
              />
            </>}
          {row.planned_mde_raw !== null
            ? <Figure
              term="Planned sensitivity"
              number={tableNumber(row.planned_mde_raw)}
              note={`In the units of the outcome${row.planned_mde_standardized === null ? "" : `; ${tableNumber(row.planned_mde_standardized)} standard deviations`}. The smallest difference the plan could detect with ${percentText(table.power)} power.`}
            />
            : row.planned_mde_standardized !== null
              ? <Figure
                term="Planned sensitivity"
                number={tableNumber(row.planned_mde_standardized)}
                note={`Standard deviations. The smallest difference the plan could detect with ${percentText(table.power)} power.`}
              />
              : <Figure term="Planned sensitivity" text="Unknown: no closed-form power for this test." />}
          <Figure
            term="Above seed noise"
            text={row.above_seed_noise === null
              ? "Not reported."
              : row.above_seed_noise
                ? "Yes: the estimate is larger than every seed-against-seed difference of its comparison."
                : "No: the estimate is not larger than every seed-against-seed difference of its comparison."}
            note="Reported beside the verdict. It is not a condition."
          />
        </dl>
        <h4>Gates</h4>
        <ul className="results-gates">
          <NegativeControlGate row={row} table={table} />
          <PositiveControlGate row={row} precision={precision} />
          <CompletenessGate row={row} required={table.min_completeness} />
        </ul>
      </div>
    </article>
  </li>;
}

interface VerdictsProps {
  table: DecisionsTable;
  /** The frozen plan of the analysis; adds each hypothesis's registered statement and arms. */
  plan?: unknown;
  /** Where the decision rules can be read. */
  preregHref?: string;
}

/**
 * The script-computed verdicts of a designed study: the study in one sentence,
 * then one block per registered hypothesis. Every number shown is the one in
 * `tables/decisions.json`; nothing is computed here.
 */
export function Verdicts({ table, plan, preregHref }: VerdictsProps) {
  const study = STUDY_VERDICT_TEXT[table.study_verdict];
  const notConfirmatory = !table.confirmatory || table.study_verdict === "not_confirmatory";
  const why = table.non_confirmatory_reason === null ? null : NOT_CONFIRMATORY_TEXT[table.non_confirmatory_reason];
  const registered = registeredHypotheses(plan);
  return <section className="results-verdicts" aria-labelledby="results-verdicts-title">
    <h2 id="results-verdicts-title">Verdicts</h2>
    <div className={`results-study-verdict results-study-verdict--${notConfirmatory ? "not_confirmatory" : table.study_verdict}`}>
      <p className="eyebrow">Study verdict · computed by script</p>
      <p className="results-study-verdict__line">
        {notConfirmatory
          ? <Stamp kind="not_confirmatory" label={STUDY_VERDICT_TEXT.not_confirmatory.label} glyph={STUDY_VERDICT_TEXT.not_confirmatory.glyph} />
          : <Stamp kind={table.study_verdict} label={study.label} glyph={study.glyph} />}
        <strong className="results-study-verdict__sentence">
          {notConfirmatory ? why?.sentence ?? STUDY_VERDICT_TEXT.not_confirmatory.sentence : study.sentence}
        </strong>
      </p>
      {notConfirmatory && <p className="results-study-verdict__detail">
        {why ? `${why.detail} ` : ""}The verdicts below show what the script returned for this run. They decide nothing.
      </p>}
    </div>
    {table.rows.length > 0
      ? <ol className="results-verdict-list">
        {table.rows.map((row) => <Hypothesis key={row.hypothesis_id} row={row} table={table} registered={registered.get(row.hypothesis_id)} />)}
      </ol>
      : <p>The decisions table lists no hypothesis.</p>}
    <p className="results-verdicts__rules">
      {table.claim === "difference_or_equivalence"
        ? "The study registered a claim of difference, or of absence within a margin."
        : "The study registered a claim of difference only."}{" "}
      A script resolved each hypothesis under decision matrix version {table.matrix_version ?? 1}, at α = {tableNumber(table.alpha)} across all confirmatory tests.
      Holm–Bonferroni families: {tests(table.families.difference, "difference")}, {tests(table.families.equivalence, "equivalence")}, {tests(table.families.negative_control, "seed-pair")}.
      Every number is the one in <code>tables/decisions.json</code>.
      {preregHref ? <> <Link className="text-link" href={preregHref}>Read the decision rules in the pre-registration</Link>.</> : null}
    </p>
  </section>;
}
