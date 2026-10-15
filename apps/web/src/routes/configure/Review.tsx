import { Link } from "wouter";
import { encodeShareConfig } from "../../lib/config-store";
import type { WizardStepProps } from "./index";

export function Review({ config, hash, launchState, onLaunch }: WizardStepProps) {
  const share = encodeShareConfig(config);
  return <section aria-labelledby="step-review"><p className="eyebrow">Protocol 07 · freeze point</p><h2 id="step-review">Review &amp; preregister</h2><p className="step-intro">Inspect the frozen machine configuration before spending QRNG and GPU time.</p>
    <dl className="review-ledger"><div><dt>Study</dt><dd>{config.title}</dd></div><div><dt>Models</dt><dd>{config.models.length}</dd></div><div><dt>Arms</dt><dd>{config.arms.length}</dd></div><div><dt>Evals</dt><dd>{config.cards.length}</dd></div><div><dt>Config SHA-256</dt><dd><code>{hash || "Calculating…"}</code></dd></div></dl>
    <div className="review-actions"><Link className="button button--primary" href={`/prereg?c=${encodeURIComponent(share)}`}>Generate pre-registration</Link><button className="button button--secondary" type="button" onClick={onLaunch}>Launch run</button></div>
    {launchState && <p className="validation-note" role="status">{launchState}</p>}
  </section>;
}
