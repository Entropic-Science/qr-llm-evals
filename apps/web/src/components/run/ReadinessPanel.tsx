import type { ReadinessReport } from "@qr-evals/contracts";
import { Badge } from "../Badge";

export function ReadinessPanel({ readiness, tier0Source, determinismTier }: { readiness: ReadinessReport | null | undefined; tier0Source: string | null | undefined; determinismTier: string | null | undefined }) {
  const arms = Object.entries(readiness?.arms ?? {});
  return <section className="run-panel readiness-panel" aria-labelledby="readiness-title">
    <header className="run-section-heading"><div><p className="eyebrow">Instrument check</p><h2 id="readiness-title">Readiness &amp; provenance</h2></div><Badge tone={readiness?.equivalence_ok ? "positive" : "warning"}>{readiness ? (readiness.equivalence_ok ? "Equivalent" : "Check failed") : "Unavailable"}</Badge></header>
    <dl className="readiness-ledger">
      <div><dt>Determinism tier</dt><dd><code>{determinismTier ?? readiness?.determinism_tier ?? "unavailable"}</code></dd></div>
      <div><dt>Tier 0 source</dt><dd><code>{tier0Source ?? readiness?.tier0_source ?? "unavailable"}</code></dd></div>
      <div><dt>Entropy RPC</dt><dd>{readiness ? (readiness.entropy.rpc_ok ? "reachable" : "unavailable") : "unavailable"}</dd></div>
      <div><dt>Engine fallbacks</dt><dd className="data-value">{readiness?.entropy.fallback_count ?? "unavailable"}</dd></div>
    </dl>
    {arms.length > 0 ? <ul className="readiness-arms">{arms.map(([armId, arm]) => <li key={armId}><code>{armId}</code><span>{arm?.available ? "available" : `unavailable${arm?.reason ? ` — ${arm.reason}` : ""}`}</span></li>)}</ul> : <p className="run-unavailable">Per-arm readiness unavailable.</p>}
  </section>;
}
