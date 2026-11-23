import { numberText, type Comparison } from "../../lib/results";
export function ResultsTable({
  rows,
  caption,
  correction = "Adjusted",
}: {
  rows: Comparison[];
  caption: string;
  /** Names the adjusted-p column; `null` leaves it out, for rows that carry no adjusted p. */
  correction?: string | null;
}) {
  return (
    <div className="results-table" tabIndex={0}>
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th>Contrast / arms</th>
            <th>Status</th>
            <th>Effect</th>
            <th>Confidence interval</th>
            <th>Raw p</th>
            {correction !== null && <th>{correction} p</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={`${row.id}-${i}`}>
              <th scope="row">
                {row.kind}
                {row.epsilon != null && ` | epsilon ${row.epsilon}`}
                <small>
                  {row.arms_a.join(", ")} vs {row.arms_b.join(", ")}
                </small>
              </th>
              <td>
                {row.available
                  ? "Available"
                  : `Unavailable: ${row.reason ?? "no measurement"}`}
              </td>
              <td>
                {row.available
                  ? numberText(row.effect ?? row.statistic)
                  : "Unavailable"}
              </td>
              <td>
                {row.available && row.ci_lower != null && row.ci_upper != null
                  ? `${numberText(row.ci_level == null ? undefined : row.ci_level * 100)}% [${row.ci_lower}, ${row.ci_upper}]`
                  : "Unavailable"}
              </td>
              <td>{row.available ? numberText(row.p_value) : "Unavailable"}</td>
              {correction !== null && (
                <td>
                  {row.available ? numberText(row.p_adjusted) : "Unavailable"}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <p>No comparisons available.</p>}
    </div>
  );
}
