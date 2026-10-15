export function TaintBanner({
  taints,
  taintedSamples = 0,
  rule,
}: {
  taints: unknown[];
  taintedSamples?: number;
  rule?: string | undefined;
}) {
  return (
    <section
      className={
        taints.length || taintedSamples ? "results-notice" : "results-integrity"
      }
      aria-label="Taint report"
    >
      <h2>Taint report</h2>
      <p>
        {taints.length} recorded taint events | {taintedSamples} tainted samples
        in the provenance ledger.
      </p>
      {rule && <p>{rule}</p>}
      {taints.length > 0 && (
        <details>
          <summary>Inspect taint records</summary>
          <pre className="results-json">{JSON.stringify(taints, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
