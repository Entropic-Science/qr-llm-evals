import type { PublicStudy } from "../../lib/results";
export function ConfigSummary({ study }: { study: PublicStudy }) {
  const config = study.study.config;
  return (
    <section>
      <h2>Frozen study setup</h2>
      <p>
        {study.study.frozen_at
          ? `Frozen ${study.study.frozen_at}`
          : "Freeze timestamp unavailable"}
      </p>
      {config ? (
        <>
          <dl className="results-setup">
            <div>
              <dt>Models</dt>
              <dd>
                {config.models
                  ?.map((model) => `${model.served_name} (${model.provider})`)
                  .join(", ") || "Unavailable"}
              </dd>
            </div>
            <div>
              <dt>Selected evaluations</dt>
              <dd>
                {config.cards?.map((card) => card.card_id).join(", ") ||
                  "Unavailable"}
              </dd>
            </div>
            <div>
              <dt>Analysis narrative model</dt>
              <dd>{config.narrative_model?.served_name ?? "Unavailable"}</dd>
            </div>
          </dl>
          <div className="results-table">
            <table>
              <caption>
                Configured arms (settings, not measured provenance)
              </caption>
              <thead>
                <tr>
                  <th>Arm / model</th>
                  <th>Sampling</th>
                  <th>Configured source</th>
                  <th>Computation</th>
                  <th>Observation</th>
                </tr>
              </thead>
              <tbody>
                {config.arms?.map((arm) => (
                  <tr key={arm.id}>
                    <th scope="row">
                      {arm.id}
                      <small>{arm.model}</small>
                    </th>
                    <td>{arm.sampling}</td>
                    <td>
                      {arm.source}
                      {arm.study_seed != null && ` / seed ${arm.study_seed}`}
                      {arm.epsilon != null && ` / epsilon ${arm.epsilon}`}
                    </td>
                    <td>
                      {arm.deterministic
                        ? "Deterministic requested"
                        : "Standard"}
                    </td>
                    <td>{arm.observer_mode ?? "Unspecified"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details>
            <summary>Inspect complete configuration</summary>
            <pre className="results-json">
              {JSON.stringify(config, null, 2)}
            </pre>
          </details>
        </>
      ) : (
        <p>Configuration unavailable.</p>
      )}
    </section>
  );
}
