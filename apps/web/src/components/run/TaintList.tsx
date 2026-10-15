import type { Taint } from "@qr-evals/contracts";

export function TaintList({ taints }: { taints: Taint[] | null | undefined }) {
  if (!taints?.length) return null;
  return <section className="run-alert run-alert--taint" aria-labelledby="taints-title"><p className="eyebrow">Integrity ledger</p><h2 id="taints-title">{taints.length} taint{taints.length === 1 ? "" : "s"} recorded</h2><ul>{taints.map((taint, index) => <li key={`${taint.at}-${index}`}><strong>{taint.kind.replaceAll("_", " ")}</strong><span>{taint.detail || "No additional detail."}</span><code>{taint.sample_ref ?? taint.window_id ?? "run-level"}</code></li>)}</ul></section>;
}
