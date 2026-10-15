import type { SelectionEstimate } from "@qr-evals/contracts";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 });
const hours = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

export function EstimateTotals({ estimate, armCount, supervisedDisplay = "single" }: { estimate: SelectionEstimate; armCount: number; supervisedDisplay?: "single" | "tiled" }) {
  const observerHours = supervisedDisplay === "single" ? estimate.observer_hours_single : estimate.observer_hours_tiled;
  const sessions = supervisedDisplay === "single" ? estimate.sessions_single : estimate.sessions_tiled;
  return <aside className="estimate-readout" aria-live="polite" aria-label="Live study estimates">
    <p className="eyebrow">Live estimate · not measured</p>
    <dl>
      <div><dt>Per arm</dt><dd>{compact.format(estimate.tokens_per_arm)} tokens</dd></div>
      <div><dt>All available arms</dt><dd>{compact.format(estimate.tokens_per_arm * armCount)} tokens</dd></div>
      <div><dt>Unsupervised / arm</dt><dd>{hours.format(estimate.time_unsupervised_h)} h</dd></div>
      <div className="estimate-readout__supervised"><dt>Supervised · {supervisedDisplay}</dt><dd>{hours.format(observerHours)} observer-h · {sessions} sessions</dd></div>
    </dl>
    <details><summary>Assumptions</summary><p>{String(estimate.assumptions.tps_model)} generated tok/s · {String(estimate.assumptions.reading_tps)} reading tok/s · {String(estimate.assumptions.session_min)} min sessions.</p><p>Judge calls, queueing, and thinking overhead are excluded.</p></details>
  </aside>;
}
