import { memo } from "react";
import type { MapNote } from "../../lib/map/notes";
import type { Box } from "../../lib/map/viewport";
import { seedFromId } from "../../lib/prng";
import { roughLine } from "../../lib/rough";
import { StatePip } from "./MapChip";
import { stateWords } from "./model";

// The marginal notes of the map: settings that are not a node of their own, pencilled beside the station or the
// node they belong to. A note says its value, shows how it stands (glyph and border, as on a chip) and opens
// the same popover a node does, with the fields of that setting.

/** What a note says to assistive technology: its name, its value, how it stands and what is wrong with it. */
export function noteName(note: MapNote): string {
  const parts = [`${note.label}: ${note.text}`];
  const words = stateWords(note.state);
  if (words) parts.push(words);
  if (note.problems.length > 0) parts.push(note.problems.map((problem) => problem.message.replace(/\.$/, "")).join(". "));
  return `${parts.join(". ")}.`;
}

interface NoteProps {
  note: MapNote;
  box: Box;
  open: boolean;
  tabIndex: number;
  onOpen: (id: string) => void;
  /** The note took the focus. */
  onFocus?: ((id: string) => void) | undefined;
}

/** The mark that ties a note to what it is about: a short pencil hook, up to a node above it or down to a heading under it. */
function hook(note: MapNote): string {
  const seed = seedFromId(note.id);
  const up = note.anchor.type === "node";
  return up
    ? `${roughLine(3, 1, 3, 9, { seed, roughness: 0.6, passes: 1 })} ${roughLine(3, 9, 10, 9, { seed: seed + 1, roughness: 0.6, passes: 1 })}`
    : `${roughLine(3, 13, 3, 5, { seed, roughness: 0.6, passes: 1 })} ${roughLine(3, 5, 10, 5, { seed: seed + 1, roughness: 0.6, passes: 1 })}`;
}

export const MapNoteView = memo(function MapNoteView({ note, box, open, tabIndex, onOpen, onFocus }: NoteProps) {
  const classes = [
    "map-marginal", `map-marginal--${note.kind}`, `map-marginal--on-${note.anchor.type}`,
    note.state !== null ? `is-${note.state}` : "is-unrated",
    note.severity !== null ? `has-${note.severity}` : "",
    open ? "is-open" : "",
  ].filter(Boolean).join(" ");
  return <button
    type="button"
    className={classes}
    data-note={note.id}
    data-anchor={note.anchor.type === "node" ? note.anchor.node : note.anchor.station}
    data-state={note.state ?? "none"}
    style={{ transform: `translate(${box.x}px, ${box.y}px)`, maxWidth: box.w, height: box.h }}
    tabIndex={tabIndex}
    aria-label={noteName(note)}
    aria-haspopup="dialog"
    aria-expanded={open}
    title={note.text}
    onClick={() => onOpen(note.id)}
    onFocus={onFocus ? () => onFocus(note.id) : undefined}
  >
    <svg className="map-marginal__hook" viewBox="0 0 12 14" aria-hidden="true" focusable="false"><path d={hook(note)} /></svg>
    {note.severity !== null ? <span className={`map-marginal__problem map-marginal__problem--${note.severity}`} aria-hidden="true">{note.severity === "error" ? "✕" : "!"}</span> : null}
    {note.state !== null ? <StatePip state={note.state} /> : null}
    <span className="map-marginal__text">{note.text}</span>
  </button>;
});

interface MapNotesProps {
  notes: readonly MapNote[];
  boxes: Readonly<Record<string, Box>>;
  /** The id of the open popover's subject. */
  open: string | null;
  /** The node in the tab order: the notes that hang on it are in it too, as its chips are. */
  current: string | null;
  onOpen: (id: string) => void;
  onFocus?: ((id: string) => void) | undefined;
}

/** Every note that has a place on the map, in the world, over the ink. */
export const MapNotes = memo(function MapNotes({ notes, boxes, open, current, onOpen, onFocus }: MapNotesProps) {
  return <div className="map-notes">
    {notes.map((note) => {
      const box = boxes[note.id];
      if (box === undefined) return null;
      const reachable = note.anchor.type === "station" || note.anchor.node === current;
      return <MapNoteView key={note.id} note={note} box={box} open={open === note.id} tabIndex={reachable ? 0 : -1} onOpen={onOpen} onFocus={onFocus} />;
    })}
  </div>;
});
