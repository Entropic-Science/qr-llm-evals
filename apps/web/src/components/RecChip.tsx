import { useId, useState } from "react";
import { REC_STATES, type Rec, type RecState } from "../lib/recommendations";

/** The state's mark: a character, or the padlock of a setting the harness fixes. Shape carries the state where colour cannot. */
export function RecGlyph({ state }: { state: RecState }) {
  const glyph = REC_STATES[state].glyph;
  return <span className="rec__glyph" aria-hidden="true">{glyph ?? <svg viewBox="0 0 12 12" width="12" height="12" focusable="false">
    <rect x="2" y="5.5" width="8" height="5.5" rx="1" fill="currentColor" />
    <path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
  </svg>}</span>;
}

interface RecChipProps {
  rec: Rec;
  /** What activating the chip does. Without it the chip pins its own tooltip open, so touch and keyboard can read it. */
  onActivate?: (rec: Rec) => void;
}

/**
 * One setting, its value and how it compares with what is recommended.
 *
 * The same chip is drawn in the wizard and on the map. Its tooltip says what the study has, what it needs and
 * on what basis, and opens on hover, on focus and, without `onActivate`, on a click.
 */
export function RecChip({ rec, onActivate }: RecChipProps) {
  const tipId = useId();
  const [pinned, setPinned] = useState(false);
  const state = REC_STATES[rec.state];
  return <span
    className={`rec-anchor${pinned ? " is-pinned" : ""}`}
    onKeyDown={(event) => {
      if (event.key !== "Escape" || !pinned) return;
      // The key closes the tooltip and nothing behind it: a dialog the chip sits in stays open.
      event.stopPropagation();
      setPinned(false);
    }}
  >
    <button
      type="button"
      className={`rec ${state.className}`}
      aria-label={`${rec.label}: ${rec.value}. ${state.legend}. ${rec.need}`}
      aria-describedby={tipId}
      aria-expanded={onActivate ? undefined : pinned}
      onClick={() => { if (onActivate) onActivate(rec); else setPinned((open) => !open); }}
      onBlur={() => setPinned(false)}
    >
      <RecGlyph state={rec.state} />
      <span className="rec__value">{rec.value}</span>
    </button>
    <span className="rec-tip" role="tooltip" id={tipId}>
      <span>{rec.have}</span>
      <span>{rec.need}</span>
      <span>{rec.basis}</span>
    </span>
  </span>;
}
