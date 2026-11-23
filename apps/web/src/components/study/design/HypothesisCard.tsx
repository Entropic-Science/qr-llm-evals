import type { HypothesisPlan, SampleSize } from "@qr-evals/contracts";
import { useId } from "react";
import { REC_STATES } from "../../../lib/recommendations";
import { RecGlyph } from "../../RecChip";
import { comparisonWords, marginWords, plannedWords, sampleSizeWords, statementWords, testWords } from "./hypothesis-text";
import { HypothesisVerdicts } from "./VerdictStamp";

/**
 * A sample-size rating as a mark: the glyph, border and colour of the recommendation chips, and the word.
 * It is a label, not a control; the chip that opens a setting is `RecChip`.
 */
export function RatingMark({ rating }: { rating: SampleSize["rating"] }) {
  return <span className={`rec rating-mark ${REC_STATES[rating].className}`} data-rating={rating}>
    <RecGlyph state={rating} />
    <span className="rec__value">{rating}</span>
  </span>;
}

interface HypothesisCardProps {
  hypothesis: HypothesisPlan;
  /** The study's `design.claim`: it decides whether "equivalent" can be reached. */
  claim: string;
  /** The level of the card's heading on the page it is drawn in. */
  headingLevel?: 3 | 4 | 5;
  /**
   * Whether the numbers are the browser's draft. A hypothesis read from a plan the server registered is not
   * a preview.
   */
  preview?: boolean;
}

/**
 * One hypothesis as the study would register it: what is compared, on which outcome, by which test, within
 * which margin, on how much data, and which verdicts it can end with.
 *
 * Nothing on the card is typed by the researcher. It is generated from the design block, so it changes with
 * every choice above it.
 */
export function HypothesisCard({ hypothesis, claim, headingLevel = 4, preview = true }: HypothesisCardProps) {
  const titleId = useId();
  const Heading = `h${headingLevel}` as "h3" | "h4" | "h5";
  const rating = hypothesis.sample_size.rating;
  return <article className="hypothesis-card" aria-labelledby={titleId} data-hypothesis={hypothesis.id}>
    <header className="hypothesis-card__head">
      <Heading id={titleId} className="hypothesis-card__title">
        <span className="hypothesis-card__id">{hypothesis.id}</span> {hypothesis.label}
      </Heading>
      <p className="hypothesis-card__cell">{comparisonWords(hypothesis)} · <code>{hypothesis.card_id}</code></p>
    </header>
    <p className="hypothesis-card__statement">{statementWords(hypothesis)}</p>
    <dl className="hypothesis-card__facts">
      <div><dt>Test</dt><dd>{testWords(hypothesis)}</dd></div>
      <div><dt>Margin</dt><dd>{marginWords(hypothesis)}</dd></div>
      <div><dt>Planned data</dt><dd>{plannedWords(hypothesis)}</dd></div>
      <div>
        <dt>Sample size{preview ? " · preview" : ""}</dt>
        <dd><RatingMark rating={rating} /> <span className="hypothesis-card__detects">{sampleSizeWords(hypothesis)}</span></dd>
      </div>
    </dl>
    <HypothesisVerdicts hypothesis={hypothesis} claim={claim} />
  </article>;
}
