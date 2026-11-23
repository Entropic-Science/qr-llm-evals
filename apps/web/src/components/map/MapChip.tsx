import { useId, useState } from "react";
import { REC_STATES, type Rec, type RecState } from "../../lib/recommendations";
import { RecGlyph } from "../RecChip";

interface MapChipProps {
  rec: Rec;
  /** The map keeps one node in the tab order at a time; the chips of the others are reached through their node. */
  tabIndex?: number;
  /** Opens the setting. Without it the chip pins its own note open, as a setting the harness fixes has nowhere to go. */
  onOpen?: ((rec: Rec) => void) | undefined;
}

/**
 * One setting on the map: the chip of the setup steps (`RecChip`), with the same classes, glyph, border and
 * spoken name, and a tab stop the map can hand out. Its state is carried by the glyph and the border style as
 * well as by its colour.
 */
export function MapChip({ rec, tabIndex = 0, onOpen }: MapChipProps) {
  const tipId = useId();
  const [pinned, setPinned] = useState(false);
  const state = REC_STATES[rec.state];
  return <span
    className={`rec-anchor${pinned ? " is-pinned" : ""}`}
    onKeyDown={(event) => {
      if (event.key !== "Escape" || !pinned) return;
      // The key closes the note and nothing behind it.
      event.stopPropagation();
      setPinned(false);
    }}
  >
    <button
      type="button"
      className={`rec ${state.className}`}
      data-rec={rec.path}
      data-state={rec.state}
      tabIndex={tabIndex}
      aria-label={`${rec.label}: ${rec.value}. ${state.legend}. ${rec.need}`}
      aria-describedby={tipId}
      aria-expanded={onOpen ? undefined : pinned}
      onClick={() => { if (onOpen) onOpen(rec); else setPinned((open) => !open); }}
      onBlur={() => setPinned(false)}
    >
      <RecGlyph state={rec.state} />
      <span className="rec__value">{rec.value}</span>
    </button>
    <span className="rec-tip" role="tooltip" id={tipId}>
      <strong>{rec.label}</strong>
      <span>{rec.have}</span>
      <span>{rec.need}</span>
      <span>{rec.basis}</span>
    </span>
  </span>;
}

/** A state as a mark without a value: the glyph in the chip's border and colour. Not a control. */
export function StatePip({ state, className = "" }: { state: RecState; className?: string }) {
  return <span className={`rec map-pip ${REC_STATES[state].className}${className ? ` ${className}` : ""}`} data-state={state} aria-hidden="true">
    <RecGlyph state={state} />
  </span>;
}
