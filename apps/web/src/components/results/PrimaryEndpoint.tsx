import type { FrozenPlan, ResultsTableData } from "../../lib/results";
import { ResultsTable } from "./ResultsTable";
export function PrimaryEndpoint({
  table,
  plan,
}: {
  table: ResultsTableData;
  plan: FrozenPlan;
}) {
  const treatment = table.comparisons.filter((row) => row.kind === "source");
  return (
    <article className="results-endpoint">
      <header>
        <p className="eyebrow">
          Primary | tier {table.tier} | {table.card_id}
        </p>
        <h3>{table.endpoint}</h3>
        <p>
          {table.test} |{" "}
          {table.available ? "Analysis available" : "Endpoint unavailable"}
        </p>
      </header>
      <div className="results-evidence">
        <section>
          <h4>Treatment estimate</h4>
          <ResultsTable
            rows={treatment}
            caption="Source comparison"
            correction="Holm-adjusted"
          />
          {treatment.map((row) => (
            <p key={row.id}>
              Claim:{" "}
              {row.available &&
              table.controls.filter((control) => control.available).length >=
                3 &&
              row.claim_supported === true
                ? "supported by the archived analysis"
                : "not supported / unavailable"}
            </p>
          ))}
        </section>
        <section>
          <h4>Seed-vs-seed controls</h4>
          <ResultsTable
            rows={table.controls}
            caption="All three baseline contrasts"
          />
          {table.controls.filter((row) => row.available).length < 3 && (
            <p className="results-notice">
              One or more of the three controls is unavailable. A source-effect
              claim is precluded.
            </p>
          )}
        </section>
      </div>
      <div className="results-claim">
        <strong>Preregistered claim rule</strong>
        <p>{plan.claim_rule || "Unavailable - no claim may be inferred."}</p>
      </div>
      <h4>Epsilon sensitivity curve</h4>
      {table.epsilon_curve.length ? (
        <ResultsTable
          rows={table.epsilon_curve}
          caption="Epsilon curve: script-generated estimates"
          correction="Holm-adjusted"
        />
      ) : (
        <p>Unavailable - no biased-arm epsilon curve was produced.</p>
      )}
      <details>
        <summary>All contrasts and exact table JSON</summary>
        <ResultsTable
          rows={table.comparisons}
          caption={`${table.endpoint}: all comparisons`}
          correction="Holm-adjusted"
        />
        <pre className="results-json">{JSON.stringify(table, null, 2)}</pre>
      </details>
    </article>
  );
}
