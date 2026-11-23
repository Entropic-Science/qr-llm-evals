import { REC_STATES, type RecState } from "../../lib/recommendations";
import { RecGlyph } from "../RecChip";

interface MapLegendProps {
  open: boolean;
  onToggle: (open: boolean) => void;
}

const WHO: readonly { tone: string; label: string }[] = [
  { tone: "quantum", label: "Quantum arm" },
  { tone: "control", label: "Seeded pseudo-random controls" },
  { tone: "positive", label: "Positive control, biased on purpose" },
];

const LINES: readonly { line: string; label: string }[] = [
  { line: "solid", label: "Carries a hypothesis to its verdict" },
  { line: "dashed", label: "Exploratory, switched off, or not in force" },
];

/**
 * What the map's marks mean, in a strip over the map that can be folded away: whose ink a stroke is, what a dashed
 * thread says, and the eight states a setting or a node can be in. Each state is shown with its glyph and border
 * as well as its colour, and named in words. It sits in the flow of the page, so it never lies on a node.
 */
export function MapLegend({ open, onToggle }: MapLegendProps) {
  return <details className="map-legend" open={open} onToggle={(event) => onToggle(event.currentTarget.open)}>
    <summary>Legend</summary>
    <div className="map-legend__body">
      <section aria-label="Whose ink">
        <h4>Who</h4>
        <ul>
          {WHO.map(({ tone, label }) => <li key={tone}>
            <svg className={`map-legend__stroke map-legend__stroke--${tone}`} viewBox="0 0 28 10" aria-hidden="true" focusable="false"><path d="M1.5 5.6 C7 3.2 12 7.4 17 4.6 S24 5.4 26.5 4.4" /></svg>
            <span>{label}</span>
          </li>)}
          {LINES.map(({ line, label }) => <li key={line}>
            <svg className={`map-legend__stroke map-legend__stroke--${line}`} viewBox="0 0 28 10" aria-hidden="true" focusable="false"><path d="M1.5 5.4 C8 4.2 14 6.2 26.5 4.8" /></svg>
            <span>{label}</span>
          </li>)}
        </ul>
      </section>
      <section aria-label="How a value compares">
        <h4>How it compares</h4>
        <ul>
          {(Object.keys(REC_STATES) as RecState[]).map((state) => <li key={state} data-state={state}>
            <span className={`rec map-pip ${REC_STATES[state].className}`}><RecGlyph state={state} /></span>
            <span>{REC_STATES[state].legend}</span>
          </li>)}
        </ul>
        <p className="map-legend__note">The check mark is given only where a sample size was computed to be enough for a stated margin.</p>
      </section>
    </div>
  </details>;
}
