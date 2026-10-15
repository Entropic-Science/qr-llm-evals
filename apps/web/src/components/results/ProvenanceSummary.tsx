import type { ProvenanceArm } from "../../lib/results";
export function ProvenanceSummary({
  arms,
  expectedArms = [],
}: {
  arms: ProvenanceArm[];
  expectedArms?: string[];
}) {
  const missing = expectedArms.filter(
    (id) => !arms.some((arm) => arm.arm_id === id),
  );
  return (
    <section>
      <h2>Arm provenance ledger</h2>
      <p>
        Labels describe recorded samples. A configured entropy source alone does
        not establish quantum provenance.
      </p>
      <div className="results-table">
        <table>
          <caption>Every arm and recorded source label</caption>
          <thead>
            <tr>
              <th>Arm</th>
              <th>Source label</th>
              <th>Samples</th>
              <th>Tainted samples</th>
            </tr>
          </thead>
          <tbody>
            {arms.map((arm, i) => (
              <tr key={`${arm.arm_id}-${arm.source_label}-${i}`}>
                <th scope="row">
                  <code>{arm.arm_id}</code>
                </th>
                <td>
                  <span
                    className={
                      arm.source_label === "quantum"
                        ? "badge badge--quantum"
                        : "badge badge--control"
                    }
                  >
                    {arm.source_label}
                  </span>
                  {arm.source_label !== "quantum" && (
                    <small> (not quantum)</small>
                  )}
                </td>
                <td>{arm.samples}</td>
                <td>{arm.tainted_samples}</td>
              </tr>
            ))}
            {missing.map((id) => (
              <tr key={id}>
                <th scope="row">{id}</th>
                <td colSpan={3}>Unavailable - no recorded provenance</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!arms.length && !missing.length && (
        <p>Recorded provenance unavailable.</p>
      )}
    </section>
  );
}
