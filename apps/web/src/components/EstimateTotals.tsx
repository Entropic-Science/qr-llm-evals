import type { SelectionEstimate } from "@qr-evals/contracts";
import type { Rec } from "../lib/recommendations";
import { RecChip } from "./RecChip";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 });
const hours = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

/** One outcome that decides the study, with the rating of its sample size. */
export interface EvidenceOutcome {
  key: string;
  label: string;
  /** The `cards.{id}.items` rec of the outcome's eval, or `undefined` when it could not be rated. */
  rec: Rec | undefined;
}

/** What the design would register, as the browser computes it. The registered numbers are the server's. */
export interface EvidencePreview {
  /** False for a study on the built-in plan: it declares no outcomes, so nothing is rated. */
  designed: boolean;
  hypotheses: number;
  comparisons: number;
  outcomes: readonly EvidenceOutcome[];
  /** How many tests each Holm family corrects for. */
  families: { difference: number; equivalence: number; negative_control: number };
}

/** What a "Holm family" and its `m` are, for a reader who has not met the correction. */
export const FAMILIES_NOTE = "Tests of one kind share the false-finding rate (Holm's correction): m is how many share it, and the more there are, the stricter each one is.";

interface EstimateTotalsProps {
  /** `null` when no estimate could be made for the study's first model. */
  estimate: SelectionEstimate | null;
  modelId: string;
  armCount: number;
  /** How supervised sessions are shown, or `null` for a study without them. */
  supervisedDisplay?: "single" | "tiled" | null;
  evidence?: EvidencePreview;
  /** What activating an outcome's chip does: open its setting. Without it the chip pins its tooltip. */
  onOpenSetting?: (rec: Rec) => void;
}

function Evidence({ evidence, onOpenSetting }: { evidence: EvidencePreview; onOpenSetting: ((rec: Rec) => void) | undefined }) {
  const { designed, hypotheses, comparisons, outcomes, families } = evidence;
  return <section className="evidence-preview" aria-label="Evidence preview">
    <p className="eyebrow">Evidence · preview</p>
    {!designed
      ? <p className="evidence-preview__note">This draft uses the fixed Study 1 plan, so no sample size is rated here.</p>
      : <>
        <dl>
          <div><dt>Hypotheses</dt><dd>{hypotheses} <small>({outcomes.length} {outcomes.length === 1 ? "outcome" : "outcomes"} × {comparisons} {comparisons === 1 ? "comparison" : "comparisons"})</small></dd></div>
        </dl>
        {outcomes.length > 0 ? <ul className="evidence-preview__outcomes" aria-label="Sample size per outcome">
          {outcomes.map(({ key, label, rec }) => <li key={key}>
            <span>{label}</span>
            {rec ? <RecChip rec={rec} {...(onOpenSetting ? { onActivate: onOpenSetting } : {})} /> : <small>not rated</small>}
          </li>)}
        </ul> : <p className="evidence-preview__note">No outcome decides this study yet.</p>}
        <p className="evidence-preview__heading">Holm families</p>
        <dl>
          <div><dt>Tests for a difference</dt><dd>m = {families.difference}</dd></div>
          <div><dt>Tests for equivalence</dt><dd>m = {families.equivalence}</dd></div>
          <div><dt>Negative-control tests</dt><dd>m = {families.negative_control}</dd></div>
        </dl>
        <p className="evidence-preview__note">{FAMILIES_NOTE}</p>
        <p className="evidence-preview__note">Computed in this browser as a preview. The registered numbers are the server's.</p>
      </>}
  </section>;
}

/** The running cost of the study as set up, and next to it what that buys in evidence. */
export function EstimateTotals({ estimate, modelId, armCount, supervisedDisplay = null, evidence, onOpenSetting }: EstimateTotalsProps) {
  return <aside className="estimate-readout" aria-live="polite" aria-label="Live study estimates">
    <p className="eyebrow">Live estimate · not measured</p>
    {estimate === null
      ? <p className="validation-note">No throughput assumption for {modelId}.</p>
      : <>
        <dl>
          <div><dt>Per arm</dt><dd>{compact.format(estimate.tokens_per_arm)} tokens</dd></div>
          <div><dt>All available arms</dt><dd>{compact.format(estimate.tokens_per_arm * armCount)} tokens</dd></div>
          <div><dt>Unsupervised / arm</dt><dd>{hours.format(estimate.time_unsupervised_h)} h</dd></div>
          {supervisedDisplay === null ? null : <div className="estimate-readout__supervised"><dt>Supervised · {supervisedDisplay}</dt><dd>{hours.format(supervisedDisplay === "single" ? estimate.observer_hours_single : estimate.observer_hours_tiled)} observer-h · {supervisedDisplay === "single" ? estimate.sessions_single : estimate.sessions_tiled} sessions</dd></div>}
        </dl>
        <details><summary>Assumptions</summary><p>{String(estimate.assumptions.tps_model)} generated tok/s · {String(estimate.assumptions.reading_tps)} reading tok/s · {String(estimate.assumptions.session_min)} min sessions.</p><p>Judge calls, queueing, and thinking overhead are excluded.</p></details>
      </>}
    {evidence ? <Evidence evidence={evidence} onOpenSetting={onOpenSetting} /> : null}
  </aside>;
}
