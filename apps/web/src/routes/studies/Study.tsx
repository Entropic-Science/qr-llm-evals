import { DecisionsTableSchema } from "@qr-evals/contracts";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "wouter";
import { API_BASE, api } from "../../lib/api";
import {
  artifactPath,
  excludedSamples,
  figureCaption,
  isEndpointTable,
  loadAnalysis,
  loadStudy,
  loadText,
  studyPath,
  type DescriptiveTableData,
} from "../../lib/results";
import { ComparisonRows } from "../../components/results/ComparisonRows";
import { ConfigSummary } from "../../components/results/ConfigSummary";
import { Downloads } from "../../components/results/Downloads";
import { HashPanel } from "../../components/results/HashPanel";
import { NarrativePanel } from "../../components/results/NarrativePanel";
import {
  OutputHealth,
  OutputHealthUnavailable,
} from "../../components/results/OutputHealth";
import { PrimaryEndpoint } from "../../components/results/PrimaryEndpoint";
import { ProvenanceSummary } from "../../components/results/ProvenanceSummary";
import { ResultsTable } from "../../components/results/ResultsTable";
import { SvgFigure } from "../../components/results/SvgFigure";
import { TaintBanner } from "../../components/results/TaintBanner";
import { Verdicts } from "../../components/results/Verdicts";
import { WithdrawnBanner } from "../../components/results/WithdrawnBanner";
import { PreregRenderer, preregSectionId } from "../Prereg";
import "../../styles/results.css";

/** The table a designed analysis writes its verdicts to (`tables/decisions.json`). */
const DECISIONS_TABLE = "decisions";

export function Study({ params }: { params: { studyId: string } }) {
  return <StudyPage studyId={params.studyId} page="results" />;
}
export function StudyPage({
  studyId,
  page,
}: {
  studyId: string;
  page: "results" | "prereg" | "data";
}) {
  const studyQuery = useQuery({
    queryKey: ["public-study", studyId],
    queryFn: ({ signal }) => loadStudy(studyId, signal),
  });
  const study = studyQuery.data;
  const options = study
    ? study.runs.flatMap((run) =>
        (study.analyses[run.run_id] ?? []).map((analysisId) => ({
          run,
          analysisId,
          key: JSON.stringify([run.run_id, analysisId]),
        })),
      )
    : [];
  const [selection, setSelection] = useState("");
  const selected =
    options.find((option) => option.key === selection) ?? options[0];
  const analysisQuery = useQuery({
    queryKey: [
      "public-analysis",
      studyId,
      selected?.run.run_id,
      selected?.analysisId,
    ],
    queryFn: ({ signal }) =>
      loadAnalysis(studyId, selected!.analysisId, selected!.run.run_id, signal),
    enabled: Boolean(selected) && page === "results",
  });
  // What the selected run's files hold: the taint counts, the flags, the stops by a check and the cautions.
  const healthQuery = useQuery({
    queryKey: ["run-health", selected?.run.run_id],
    queryFn: ({ signal }) => api.health(selected!.run.run_id, signal),
    enabled: Boolean(selected) && page === "results",
    retry: false,
  });
  const preregQuery = useQuery({
    queryKey: ["public-prereg", studyId],
    queryFn: ({ signal }) =>
      loadText(`${studyPath(studyId)}/prereg.md`, signal),
    enabled: page === "prereg",
  });
  if (studyQuery.isPending)
    return <p role="status">Loading published study...</p>;
  if (studyQuery.isError || !study)
    return (
      <section role="alert">
        <div className="page-heading"><h1>Study unavailable</h1></div>
        <p>{studyQuery.error?.message ?? "The study could not be loaded."}</p>
        <button
          className="button button--secondary"
          onClick={() => void studyQuery.refetch()}
        >
          Retry
        </button>
      </section>
    );
  const analysis = analysisQuery.data;
  // A designed analysis carries a decisions table, which is not an endpoint
  // table: it is shown as verdicts and kept out of the endpoint lists.
  const designed =
    analysis !== undefined &&
    (DECISIONS_TABLE in analysis.loadedTables || analysis.plan.design != null);
  const decisions = designed
    ? DecisionsTableSchema.safeParse(analysis.loadedTables[DECISIONS_TABLE])
    : null;
  const loaded = Object.entries(analysis?.loadedTables ?? {}).filter(
    ([name]) => name !== DECISIONS_TABLE,
  );
  const tables = loaded.map(([, table]) => table).filter(isEndpointTable);
  // A table without comparison rows or a tier describes the run and tests
  // nothing (the top-k coverage of the token records). It is listed apart.
  const descriptive = loaded.filter(([, table]) => !isEndpointTable(table));
  const tiers = [
    ...new Set(
      tables
        .filter((table) => table.role !== "primary")
        .map((table) => table.tier),
    ),
  ].sort((a, b) => a - b);
  const route = `/studies/${encodeURIComponent(studyId)}`;
  const expectedArms = (study.study.config?.arms ?? [])
    .map((arm) => arm.id)
    .filter((id): id is string => Boolean(id));
  return (
    <div className="results-page">
      <header className="results-masthead">
        <div className="page-heading"><p className="eyebrow">Public research record | {studyId}</p>
        <h1>{study.study.config?.title ?? studyId}</h1></div>
        <p>Frozen protocol, measured results, inspectable provenance.</p>
        <nav aria-label="Study pages">
          {[
            ["results", route, "Results"],
            ["prereg", `${route}/prereg`, "Preregistration"],
            ["data", `${route}/data`, "Data & licence"],
          ].map(([key, href, label]) => (
            <Link
              key={key}
              href={href!}
              aria-current={page === key ? "page" : undefined}
            >
              {label}
            </Link>
          ))}
        </nav>
      </header>
      <WithdrawnBanner
        withdrawn={
          study.withdrawn ||
          (page === "results" && selected?.run.withdrawn === true)
        }
      />
      <HashPanel
        prereg={study.study.prereg_hash}
        config={study.study.config_hash}
        plan={page === "results" ? analysis?.manifest.plan_hash : undefined}
      />
      {page === "prereg" && (
        <PreregRenderer
          document={
            preregQuery.data
              ? {
                  markdown: preregQuery.data,
                  prereg_hash: study.study.prereg_hash ?? "",
                  config_hash: study.study.config_hash ?? "",
                }
              : null
          }
          readOnly
          isRendering={preregQuery.isPending}
          error={preregQuery.error?.message ?? null}
        />
      )}
      {page === "results" && (
        <>
          <ConfigSummary study={study} />
          <section className="results-selection">
            <label htmlFor="analysis-selection">Run / analysis</label>
            <select
              id="analysis-selection"
              value={selected?.key ?? ""}
              disabled={!options.length}
              onChange={(event) => setSelection(event.target.value)}
            >
              {!options.length && (
                <option value="">No published analysis</option>
              )}
              {options.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.run.kind} | {option.run.run_id} | {option.analysisId}
                  {option.run.withdrawn ? " | withdrawn" : ""}
                </option>
              ))}
            </select>
            {selected && (
              <p>
                <strong>
                  {selected.run.kind === "pilot"
                    ? study.study.config?.design != null
                      ? "Pilot run - labelled non-confirmatory"
                      : "Pilot - calibration only, excluded from full-run inference"
                    : "Full run"}
                </strong>{" "}
                | {selected.run.state} | Determinism tier:{" "}
                {selected.run.determinism_tier ?? "unavailable"}
              </p>
            )}
          </section>
          {!selected ? (
            <p role="status">
              No analysis has been published for this study yet.
            </p>
          ) : analysisQuery.isPending ? (
            <p role="status">Loading archived analysis...</p>
          ) : analysisQuery.isError ? (
            <section role="alert">
              <h2>Analysis unavailable</h2>
              <p>{analysisQuery.error.message}</p>
              <button
                className="button button--secondary"
                onClick={() => void analysisQuery.refetch()}
              >
                Retry analysis
              </button>
            </section>
          ) : (
            analysis && (
              <>
                <ProvenanceSummary
                  arms={analysis.provenance}
                  expectedArms={expectedArms}
                />
                <TaintBanner
                  summary={healthQuery.data}
                  loading={healthQuery.isPending}
                  rule={analysis.plan.exclusion_rule}
                  excluded={excludedSamples(analysis.provenance)}
                />
                {healthQuery.data ? (
                  <OutputHealth summary={healthQuery.data} />
                ) : (
                  <OutputHealthUnavailable
                    loading={healthQuery.isPending}
                    message={healthQuery.error?.message}
                  />
                )}
                {designed ? (
                  <>
                    {decisions?.success ? (
                      <Verdicts
                        table={decisions.data}
                        plan={analysis.plan}
                        preregHref={`${route}/prereg#${preregSectionId("Decision rules")}`}
                      />
                    ) : (
                      <section role="alert" className="results-notice">
                        <h2>Verdicts</h2>
                        <p>
                          This analysis belongs to a designed study, but its
                          decisions table is missing or could not be read. No
                          verdict is shown.
                        </p>
                      </section>
                    )}
                    <section>
                      <h2>Confirmatory outcomes | comparison rows</h2>
                      <p>
                        The rows the verdicts were read from. They are
                        uncorrected; the Holm-adjusted p-values are in the
                        verdicts above.
                      </p>
                      {tables
                        .filter((table) => table.role === "primary")
                        .map((table) => (
                          <ComparisonRows
                            key={`${table.card_id}--${table.endpoint}`}
                            table={table}
                          />
                        ))}
                      {!tables.some((table) => table.role === "primary") && (
                        <p>No confirmatory outcome tables available.</p>
                      )}
                    </section>
                  </>
                ) : (
                  <section>
                    <h2>Primary endpoints</h2>
                    {tables
                      .filter((table) => table.role === "primary")
                      .map((table) => (
                        <PrimaryEndpoint
                          key={`${table.card_id}--${table.endpoint}`}
                          table={table}
                          plan={analysis.plan}
                        />
                      ))}
                    {!tables.some((table) => table.role === "primary") && (
                      <p>No primary endpoint tables available.</p>
                    )}
                  </section>
                )}
                {tiers.map((tier) => (
                  <section key={tier}>
                    <h2>Tier {tier} | secondary & exploratory endpoints</h2>
                    {tables
                      .filter(
                        (table) =>
                          table.role !== "primary" && table.tier === tier,
                      )
                      .map((table) =>
                        designed ? (
                          <ComparisonRows
                            key={`${table.card_id}--${table.endpoint}`}
                            table={table}
                          />
                        ) : (
                        <article key={`${table.card_id}--${table.endpoint}`}>
                          <h3>
                            {[table.card_id, table.endpoint]
                              .filter(Boolean)
                              .join(" | ")}
                          </h3>
                          <p>
                            {[table.role, table.test]
                              .filter(
                                (part) => typeof part === "string" && part,
                              )
                              .join(" | ") || "role and test not recorded"}
                          </p>
                          <ResultsTable
                            rows={table.comparisons}
                            caption={table.endpoint}
                            correction={
                              typeof table.correction === "string" &&
                              table.correction !== ""
                                ? table.correction.toUpperCase()
                                : null
                            }
                          />
                          <details>
                            <summary>Exact table JSON</summary>
                            <pre className="results-json">
                              {JSON.stringify(table, null, 2)}
                            </pre>
                          </details>
                        </article>
                        ),
                      )}
                  </section>
                ))}
                {descriptive.length > 0 && (
                  <section>
                    <h2>Descriptive tables</h2>
                    <p>
                      These tables describe the run. They test nothing, so
                      they have no p-value and no correction.
                    </p>
                    {descriptive.map(([name, table]) => (
                      <DescriptiveTable key={name} name={name} table={table} />
                    ))}
                  </section>
                )}
                <section>
                  <h2>Script-generated figures</h2>
                  {analysis.figures.map((name) => (
                    <SvgFigure
                      key={`${selected.key}-${name}`}
                      url={`${API_BASE}${artifactPath(studyId, selected.analysisId, selected.run.run_id, `figures/${encodeURIComponent(name)}.svg`)}`}
                      caption={figureCaption(name, analysis.loadedTables)}
                    />
                  ))}
                  {!analysis.figures.length && <p>No figures available.</p>}
                </section>
                <NarrativePanel
                  published={analysis.manifest.narrative?.published === true}
                  narrative={analysis.narrative}
                />
                <details>
                  <summary>Reproducibility manifest</summary>
                  <pre className="results-json">
                    {JSON.stringify(analysis.manifest, null, 2)}
                  </pre>
                </details>
              </>
            )
          )}
        </>
      )}
      <Downloads studyId={studyId} />
    </div>
  );
}

const cellText = (value: unknown) =>
  value == null
    ? "not recorded"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

/** A table that is not an endpoint table: its label, its per-arm values when it has them, and its JSON. */
function DescriptiveTable({
  name,
  table,
}: {
  name: string;
  table: DescriptiveTableData;
}) {
  const title = typeof table.endpoint === "string" ? table.endpoint : name;
  const arms = Object.entries(
    typeof table.arms === "object" && table.arms !== null ? table.arms : {},
  ).filter(
    (entry): entry is [string, Record<string, unknown>] =>
      typeof entry[1] === "object" && entry[1] !== null,
  );
  const columns = [
    ...new Set(arms.flatMap(([, values]) => Object.keys(values))),
  ];
  return (
    <article>
      <h3>{title}</h3>
      <p>
        Descriptive | no test
        {table.available === false && " | no data available"}
      </p>
      {typeof table.label === "string" && <p>{table.label}</p>}
      {arms.length > 0 && columns.length > 0 && (
        <div className="results-table" tabIndex={0}>
          <table>
            <caption>{title}: values by arm</caption>
            <thead>
              <tr>
                <th>Arm</th>
                {columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {arms.map(([arm, values]) => (
                <tr key={arm}>
                  <th scope="row">
                    <code>{arm}</code>
                  </th>
                  {columns.map((column) => (
                    <td key={column}>{cellText(values[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details>
        <summary>Exact table JSON</summary>
        <pre className="results-json">{JSON.stringify(table, null, 2)}</pre>
      </details>
    </article>
  );
}
