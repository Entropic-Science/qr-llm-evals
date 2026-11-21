import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "wouter";
import { API_BASE } from "../../lib/api";
import {
  artifactPath,
  figureCaption,
  loadAnalysis,
  loadStudy,
  loadText,
  studyPath,
} from "../../lib/results";
import { ConfigSummary } from "../../components/results/ConfigSummary";
import { Downloads } from "../../components/results/Downloads";
import { HashPanel } from "../../components/results/HashPanel";
import { NarrativePanel } from "../../components/results/NarrativePanel";
import { PrimaryEndpoint } from "../../components/results/PrimaryEndpoint";
import { ProvenanceSummary } from "../../components/results/ProvenanceSummary";
import { ResultsTable } from "../../components/results/ResultsTable";
import { SvgFigure } from "../../components/results/SvgFigure";
import { TaintBanner } from "../../components/results/TaintBanner";
import { WithdrawnBanner } from "../../components/results/WithdrawnBanner";
import { PreregRenderer } from "../Prereg";
import "../../styles/results.css";

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
  const tables = Object.values(analysis?.loadedTables ?? {});
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
                    ? "Pilot - calibration only, excluded from full-run inference"
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
                  taints={selected.run.taints ?? []}
                  taintedSamples={analysis.provenance.reduce(
                    (sum, arm) => sum + arm.tainted_samples,
                    0,
                  )}
                  rule={analysis.plan.exclusion_rule}
                />
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
                {tiers.map((tier) => (
                  <section key={tier}>
                    <h2>Tier {tier} | secondary & exploratory endpoints</h2>
                    {tables
                      .filter(
                        (table) =>
                          table.role !== "primary" && table.tier === tier,
                      )
                      .map((table) => (
                        <article key={`${table.card_id}--${table.endpoint}`}>
                          <h3>
                            {table.card_id} | {table.endpoint}
                          </h3>
                          <p>
                            {table.role} | {table.test}
                          </p>
                          <ResultsTable
                            rows={table.comparisons}
                            caption={table.endpoint}
                            correction={table.correction.toUpperCase()}
                          />
                          <details>
                            <summary>Exact table JSON</summary>
                            <pre className="results-json">
                              {JSON.stringify(table, null, 2)}
                            </pre>
                          </details>
                        </article>
                      ))}
                  </section>
                ))}
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
