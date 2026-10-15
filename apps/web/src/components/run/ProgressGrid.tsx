import type { Progress, Provenance } from "@qr-evals/contracts";
import { ProvenanceBadge } from "./ProvenanceBadge";

export function ProgressGrid({ progress, sourceByArm = {} }: { progress: Progress | null | undefined; sourceByArm: Record<string, Provenance["source_label"]> | undefined }) {
  const arms = Object.entries(progress?.by_arm ?? {});
  const cardIds = [...new Set(arms.flatMap(([, arm]) => Object.keys(arm?.by_card ?? {})))];
  return <section className="run-panel" aria-labelledby="progress-title">
    <header className="run-section-heading"><div><p className="eyebrow">Live counters</p><h2 id="progress-title">Arm × evaluation matrix</h2></div><span className="run-section-note">done / total · aborted</span></header>
    {arms.length === 0 || cardIds.length === 0 ? <p className="run-unavailable">Progress is unavailable until the runner reports its first work unit.</p> : <div className="progress-frame"><table><caption className="sr-only">Progress by arm and evaluation card</caption><thead><tr><th scope="col">Arm</th>{cardIds.map((cardId) => <th key={cardId} scope="col"><code>{cardId}</code></th>)}</tr></thead><tbody>{arms.map(([armId, arm]) => <tr key={armId}><th scope="row"><code>{armId}</code><ProvenanceBadge sourceLabel={sourceByArm[armId]} /></th>{cardIds.map((cardId) => { const cell = arm?.by_card?.[cardId]; const done = cell?.done ?? 0; const total = cell?.total ?? 0; const aborted = cell?.aborted ?? 0; const pct = total > 0 ? Math.min(100, done / total * 100) : 0; return <td key={cardId}><div className="progress-cell"><span className="progress-cell__count">{done} / {total}</span><span className="progress-cell__track" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span><small>{aborted} aborted</small></div></td>; })}</tr>)}</tbody></table></div>}
  </section>;
}
