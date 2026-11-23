import { REC_STATES, type RecState } from "../../../lib/recommendations";
import { RecGlyph } from "../../RecChip";

/**
 * What the marks beside the settings mean: one entry per state, in the order of `REC_STATES`.
 *
 * Each state is a glyph and a border style as well as a colour, and the legend says it in words.
 */
export function RecLegend({ states }: { states?: readonly RecState[] }) {
  const shown = states ?? (Object.keys(REC_STATES) as RecState[]);
  return <details className="settings-details rec-legend">
    <summary>What the marks beside the settings mean</summary>
    <ul className="rec-legend__list">
      {shown.map((state) => <li key={state}>
        <span className={`rec rating-mark ${REC_STATES[state].className}`}><RecGlyph state={state} /></span>
        <span>{REC_STATES[state].legend}</span>
      </li>)}
    </ul>
    <p className="field-note">The check mark is given only where a sample size was computed to be enough for a stated margin. A value that merely equals its default gets the equals sign.</p>
  </details>;
}
