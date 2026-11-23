import type { HypothesisPlan } from "@qr-evals/contracts";
import { RatingMark } from "./HypothesisCard";
import {
  NO_CLOSED_FORM, POWER_TEXT, RATING_IS_NOT_A_VERDICT, RATING_MEANING, WORST_CASE, comparisonWords, detectableWords,
  plannedShort, pooled, recommendedWords,
} from "./hypothesis-text";

const RATINGS = ["sufficient", "marginal", "insufficient", "unknown"] as const;

interface SampleSizeTableProps {
  hypotheses: readonly HypothesisPlan[];
  /** Whether the numbers are the browser's draft rather than a registered plan's. */
  preview?: boolean;
}

/**
 * Whether the planned prompts can carry each hypothesis, with what that judgement rests on.
 *
 * A test on pooled tokens has no closed-form power: its row says so and is never rated as enough. Every other
 * row is a worst-case bound computed before any data exist, and the table says that too.
 */
export function SampleSizeTable({ hypotheses, preview = true }: SampleSizeTableProps) {
  const detects = `Smallest difference detected with ${POWER_TEXT} power`;
  return <div className="sample-size">
    <div className="design-table-frame"><table className="design-table sample-size__table">
      <caption className="sr-only">Sample size of each hypothesis{preview ? ", as a preview" : ""}</caption>
      <thead><tr>
        <th scope="col">Hypothesis</th><th scope="col">Planned</th><th scope="col">Rating</th>
        <th scope="col">{detects}</th><th scope="col">Prompts recommended</th>
      </tr></thead>
      <tbody>{hypotheses.map((hypothesis) => <tr key={hypothesis.id} data-hypothesis={hypothesis.id}>
        <th scope="row">{hypothesis.id} · {hypothesis.label}<small>{comparisonWords(hypothesis)}</small></th>
        <td data-label="Planned">{plannedShort(hypothesis)}</td>
        <td data-label="Rating"><RatingMark rating={hypothesis.sample_size.rating} /></td>
        <td data-label={detects}>{pooled(hypothesis) ? NO_CLOSED_FORM : detectableWords(hypothesis)}</td>
        <td data-label="Prompts recommended">{recommendedWords(hypothesis.sample_size)}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="field-note">{WORST_CASE}</p>
    <p className="field-note">{RATING_IS_NOT_A_VERDICT}</p>
    <details className="settings-details sample-size__ratings">
      <summary>What the ratings mean</summary>
      <dl>{RATINGS.map((rating) => <div key={rating}><dt><RatingMark rating={rating} /></dt><dd>{RATING_MEANING[rating]}</dd></div>)}</dl>
    </details>
    {preview ? <p className="field-note">Computed in this browser as a preview. The registered numbers are the server's.</p> : null}
  </div>;
}
