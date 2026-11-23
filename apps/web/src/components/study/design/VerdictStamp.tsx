import type { HypothesisPlan } from "@qr-evals/contracts";
import type { Verdict } from "../../../lib/design";
import { seedFromId } from "../../../lib/prng";
import { roughRect } from "../../../lib/rough";
import { verdictGlyph, verdictLabel, verdictReach, type VerdictReach } from "./verdicts";

/**
 * How a stamp stands: a verdict the hypothesis can end with, one it cannot, or the one a run did end with.
 * The state is a class (`is-reachable`, `is-unreachable`, `is-reached`) and is also said in the stamp's text.
 */
export type VerdictStampState = "reachable" | "unreachable" | "reached";

interface VerdictStampProps {
  verdict: Verdict;
  state?: VerdictStampState;
  /** The stamp's word, when a view has its own for the verdict. */
  label?: string;
}

// The outline is drawn in a 100 × 30 box and stretched to the stamp, with a stroke that keeps its width.
const BOX = { w: 100, h: 30, inset: 2.5, radius: 6 };

/** A stamp's outline: a pencilled lozenge, the same for the same verdict wherever it is drawn. */
function outline(verdict: Verdict): string {
  return roughRect(BOX.inset, BOX.inset, BOX.w - 2 * BOX.inset, BOX.h - 2 * BOX.inset, { seed: seedFromId(`verdict-stamp:${verdict}`), roughness: 1.1, radius: BOX.radius });
}

/**
 * One verdict as a stamp: a glyph and a word inside a hand-drawn outline.
 *
 * The verdict is carried by the word and the glyph, the state by the outline (solid, or dashed for a verdict
 * out of reach), a struck label and a spoken "unreachable"; colour only repeats both.
 */
export function VerdictStamp({ verdict, state = "reachable", label }: VerdictStampProps) {
  return <span className={`verdict-stamp verdict-stamp--${verdict} is-${state}`} data-verdict={verdict}>
    <svg className="verdict-stamp__outline" viewBox={`0 0 ${BOX.w} ${BOX.h}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path d={outline(verdict)} />
    </svg>
    <span className="verdict-stamp__glyph" aria-hidden="true">{verdictGlyph(verdict)}</span>
    <span className="verdict-stamp__label">{label ?? verdictLabel(verdict)}</span>
    {state === "unreachable" ? <span className="sr-only"> (unreachable)</span> : null}
    {state === "reached" ? <span className="sr-only"> (the verdict reached)</span> : null}
  </span>;
}

interface VerdictStampsProps {
  /** Whose verdicts these are, for the list's name: a hypothesis id. */
  subject: string;
  stamps: readonly Pick<VerdictReach, "verdict" | "label" | "reachable" | "reason">[];
}

/**
 * The verdicts of one hypothesis as a row of stamps, and under it, for each verdict the study cannot reach as
 * configured, the reason in a sentence.
 */
export function VerdictStamps({ subject, stamps }: VerdictStampsProps) {
  const closed = stamps.filter((stamp) => !stamp.reachable);
  return <div className="verdict-stamps">
    <ul className="verdict-stamps__row" aria-label={`Verdicts ${subject} can end with`}>
      {stamps.map((stamp) => <li key={stamp.verdict}>
        <VerdictStamp verdict={stamp.verdict} label={stamp.label} state={stamp.reachable ? "reachable" : "unreachable"} />
      </li>)}
    </ul>
    {closed.length > 0 ? <ul className="verdict-stamps__reasons" aria-label={`Verdicts ${subject} cannot reach`}>
      {closed.map((stamp) => <li key={stamp.verdict} data-verdict={stamp.verdict}>
        <strong>{stamp.label}</strong>: unreachable. {sentence(stamp.reason ?? "it is ruled out by the design")}
      </li>)}
    </ul> : null}
  </div>;
}

/** The stamps of a drafted or registered hypothesis under the study's claim (`design.claim`). */
export function HypothesisVerdicts({ hypothesis, claim }: { hypothesis: HypothesisPlan; claim: string }) {
  return <VerdictStamps subject={hypothesis.id} stamps={verdictReach(hypothesis, claim)} />;
}

function sentence(text: string): string {
  const trimmed = text.trim();
  const capital = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}
