import type { ResultsTableData } from "../../lib/results";
import { ResultsTable } from "./ResultsTable";

/**
 * One endpoint table of a designed analysis: its comparison rows as written.
 *
 * These rows carry no adjusted p and no claim field. What they decide is in
 * the decisions table, which `Verdicts` shows.
 */
export function ComparisonRows({ table }: { table: ResultsTableData }) {
  const reason = typeof table.reason === "string" ? table.reason : null;
  return (
    <article className="results-rows">
      <h3>
        {table.card_id} | {table.endpoint}
      </h3>
      <p>
        {table.role} | tier {table.tier} | {table.test} |{" "}
        {table.available
          ? "Analysis available"
          : `Endpoint unavailable${reason ? `: ${reason}` : ""}`}
      </p>
      <ResultsTable
        rows={table.comparisons}
        caption={`${table.endpoint}: all comparisons, uncorrected`}
        correction={null}
      />
      <details>
        <summary>Exact table JSON</summary>
        <pre className="results-json">{JSON.stringify(table, null, 2)}</pre>
      </details>
    </article>
  );
}
