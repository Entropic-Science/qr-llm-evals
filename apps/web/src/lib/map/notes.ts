import type { WizardStudyConfig } from "../config-store";
import type { Problem, StepId } from "../problems";
import type { Rec, RecState } from "../recommendations";
import { worstState, type StationId, type StudyGraph } from "./graph";
import type { MapLayout } from "./layout";
import type { Box, ZoomTier } from "./viewport";

// The marginal notes of the study map: the settings that are not a node of their own, pencilled beside the
// station or the node they belong to. The false-finding rate and its correction at the tests, power and
// completeness at the gates, the narrative model at the verdicts, who the study is by under its question, the
// sampling envelope on the quantum arm of each comparison (it is said of the compared arms only), the session
// settings on the observed arm and the judge on every eval it scores.
//
// Pure, like the graph: a note is a label, what it says, how it stands and where it hangs. `layoutNotes`
// gives it a box in the world, so it moves with the view like everything else on the map.

/** Which settings a note stands for; its popover draws the fields of that kind. */
export type NoteKind = "alpha" | "gates" | "narrative" | "people" | "envelope" | "sessions" | "judge";

export interface MapNote {
  /** `note:{kind}` or `note:{kind}:{what it hangs on}`. Never the id of a node. */
  id: string;
  kind: NoteKind;
  /** What the note is attached to: a station's heading, or a node. */
  anchor: { type: "station"; station: StationId } | { type: "node"; node: string };
  station: StationId;
  /** What its settings are called, as the heading of its popover. */
  label: string;
  /** What the note says on the map. */
  text: string;
  /** The same in one sentence, for its popover and for assistive technology. */
  story: string;
  /** The worst state of its settings; `locked` when the harness fixes all of them; `null` when none is rated. */
  state: RecState | null;
  severity: "error" | "warning" | null;
  recs: Rec[];
  problems: Problem[];
  /** The study key paths its popover edits (`panelFor` reads them); empty when nothing in it can be edited. */
  settings: string[];
  stepId: StepId;
}

const NOTE_PREFIX = "note:";

/** Whether an id names a note rather than a node. */
export function isNoteId(id: string | null | undefined): id is string {
  return typeof id === "string" && id.startsWith(NOTE_PREFIX);
}

const listed = (names: readonly string[]): string => {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
};

function severityOf(problems: readonly Problem[]): MapNote["severity"] {
  if (problems.some((problem) => problem.severity === "error")) return "error";
  return problems.length > 0 ? "warning" : null;
}

function stateOf(recs: readonly Rec[]): RecState | null {
  const rated = worstState(recs.map((rec) => rec.state));
  if (rated !== null) return rated;
  return recs.length > 0 ? "locked" : null;
}

const DISPLAY_TEXT: Readonly<Record<string, string>> = { single: "single stream", tiled: "four-stream tile" };

/**
 * The notes of a study, in reading order. `recs` and `problems` are the lists the setup pages hold; the graph
 * says which arms and evals are there to hang a note on.
 */
export function buildNotes(graph: StudyGraph, config: WizardStudyConfig, recs: readonly Rec[], problems: readonly Problem[]): MapNote[] {
  const notes: MapNote[] = [];
  const rec = (path: string) => recs.find((item) => item.path === path);
  const some = (...paths: string[]) => paths.flatMap((path) => rec(path) ?? []);
  const withCode = (...codes: string[]) => problems.filter((problem) => codes.includes(problem.code));
  const add = (note: Omit<MapNote, "state" | "severity">) => notes.push({ ...note, state: stateOf(note.recs), severity: severityOf(note.problems) });

  // ── who the study is by, under its question ──
  const authors = config.authors.filter((author) => author.trim() !== "");
  const contact = config.contact.trim();
  add({
    id: "note:people", kind: "people", anchor: { type: "node", node: "study" }, station: "question", stepId: "review",
    label: "Authors and contact",
    text: `${authors.length === 0 ? "No author yet" : `By ${listed(authors)}`} · ${contact === "" ? "no contact yet" : contact}`,
    story: "The authors and the contact address are printed in the pre-registration.",
    recs: [], problems: withCode("review.contact_placeholder"), settings: ["authors[]", "contact"],
  });

  // ── the sampling envelope on the quantum arm of each comparison, the sessions on the observed arm ──
  const envelope = rec("fixed.envelope");
  for (const node of graph.nodes) {
    if (node.kind === "quantum_arm" && envelope !== undefined) {
      add({
        id: `note:envelope:${node.id}`, kind: "envelope", anchor: { type: "node", node: node.id }, station: "arms", stepId: "compare",
        label: "Sampling envelope",
        text: "Same envelope in the compared arms",
        // A provider's own arm and a greedy arm are outside the envelope, and a biased arm bends it on purpose.
        story: "The harness pins the sampling envelope of the quantum arm and the seeded controls, so the compared arms differ only in their entropy source and its seed.",
        recs: node.recs.filter((item) => item.path === "fixed.envelope").length > 0 ? node.recs.filter((item) => item.path === "fixed.envelope") : [envelope],
        problems: [], settings: [],
      });
    }
    if (node.kind === "supervised_arm" && config.supervised !== null && config.supervised !== undefined) {
      const display = config.supervised.display ?? "single";
      add({
        id: `note:sessions:${node.id}`, kind: "sessions", anchor: { type: "node", node: node.id }, station: "arms", stepId: "instruments",
        label: "Supervised sessions",
        text: `Sessions: ${DISPLAY_TEXT[display] ?? display}, ${config.supervised.intention_timer_min ?? 3} min timer`,
        story: "How an observed session is shown and timed. A timer never starts a session; a person always does.",
        recs: [], problems: [], settings: ["supervised.display", "supervised.intention_timer_min", "supervised.refocus_s", "supervised.group"],
      });
    }
  }

  // ── the judge on every eval it scores ──
  const judge = (config.judge.model ?? "").trim();
  for (const node of graph.nodes) {
    if (node.ref.type !== "card" || !node.ref.judged || !node.ref.selected) continue;
    add({
      id: `note:judge:${node.id}`, kind: "judge", anchor: { type: "node", node: node.id }, station: "prompts", stepId: "instruments",
      label: "Judge",
      text: judge === "" ? "Judge: no model chosen" : `Judge: ${judge}`,
      story: "This eval is scored by a second model, the judge, with the same settings for every arm.",
      recs: [], problems: node.problems.filter((problem) => problem.code === "instruments.judge"),
      settings: ["judge.model", "judge.max_tokens", "judge.thinking", "judge.temperature"],
    });
  }

  if (!graph.legacy) {
    // ── alpha and the Holm family at the tests ──
    const alpha = rec("design.alpha");
    const correction = rec("fixed.correction");
    if (alpha !== undefined) {
      add({
        id: "note:alpha", kind: "alpha", anchor: { type: "station", station: "tests" }, station: "tests", stepId: "design",
        label: "False-finding rate and correction",
        text: `${alpha.value}${correction ? ` · ${correction.value}` : ""}`,
        story: "The false-finding rate is held across all confirmatory tests together, with Holm's correction.",
        recs: [alpha, ...some("fixed.correction", "fixed.ci_level", "fixed.permutations")], problems: [], settings: ["design.alpha"],
      });
    }
    // ── power and completeness at the gates ──
    const fixed = some("fixed.power", "fixed.completeness");
    if (fixed.length > 0) {
      add({
        id: "note:gates", kind: "gates", anchor: { type: "station", station: "gates" }, station: "gates", stepId: "design",
        label: "Power and completeness",
        text: fixed.map((item) => `${item.label.toLowerCase()} ${item.value}`).join(" · "),
        story: "Sample sizes are rated at this power, and an outcome needs this share of its planned pairs to be valid.",
        recs: fixed, problems: [], settings: [],
      });
    }
  }

  // ── the narrative model at the verdicts ──
  add({
    id: "note:narrative", kind: "narrative", anchor: { type: "station", station: "verdict" }, station: "verdict", stepId: "review",
    label: "Narrative model",
    text: `Narrative written by ${config.narrative_model.served_name}`,
    story: "The model that writes the prose of the report. It may only restate numbers that are in the tables.",
    recs: [], problems: [], settings: ["narrative_model"],
  });

  const order = new Map<StationId, number>(["question", "arms", "prompts", "measures", "tests", "gates", "verdict"].map((id, index) => [id as StationId, index]));
  return notes.map((note, index) => ({ note, index })).sort((a, b) => (order.get(a.note.station)! - order.get(b.note.station)!) || a.index - b.index).map(({ note }) => note);
}

/** A note over a station's heading is this tall; one tucked under a node fits the gap between two nodes. */
export const STATION_NOTE_H = 22;
export const NODE_NOTE_H = 14;
/** The note under the study's question has room for two lines. */
export const STUDY_NOTE_H = 34;
const STATION_NOTE_GAP = 4;
/** A station's note may run this far past its column, into the gutter the threads cross. */
export const STATION_NOTE_REACH = 44;

/**
 * Where each note hangs, in world units. A note of a station sits over the station's heading; a note of a node
 * is tucked under it. A note whose node is not drawn (folded away, or left out at this zoom) has no box, and
 * at the story zoom only the stations keep theirs.
 */
export function layoutNotes(notes: readonly MapNote[], layout: Pick<MapLayout, "boxes" | "stations">, tier: ZoomTier = "design"): Record<string, Box> {
  const boxes: Record<string, Box> = {};
  const stacked = new Map<string, number>();
  for (const note of notes) {
    if (note.anchor.type === "station") {
      const station = note.anchor.station;
      const header = layout.stations.find((item) => item.id === station)?.header;
      if (header === undefined) continue;
      const above = stacked.get(station) ?? 0;
      stacked.set(station, above + 1);
      boxes[note.id] = { x: header.x, y: header.y - (above + 1) * (STATION_NOTE_H + STATION_NOTE_GAP), w: header.w + STATION_NOTE_REACH, h: STATION_NOTE_H };
      continue;
    }
    const anchor = layout.boxes[note.anchor.node];
    if (anchor === undefined || tier === "story") continue;
    boxes[note.id] = note.anchor.node === "study"
      ? { x: anchor.x, y: anchor.y + anchor.h + 8, w: anchor.w, h: STUDY_NOTE_H }
      : { x: anchor.x + 8, y: anchor.y + anchor.h + 1, w: anchor.w - 16, h: NODE_NOTE_H };
  }
  return boxes;
}

/** The smallest box that holds `bounds` and every note. */
export function boundsWithNotes(bounds: Box, noteBoxes: Readonly<Record<string, Box>>): Box {
  const all = [bounds, ...Object.values(noteBoxes)];
  const x = Math.min(...all.map((box) => box.x)), y = Math.min(...all.map((box) => box.y));
  return { x, y, w: Math.max(...all.map((box) => box.x + box.w)) - x, h: Math.max(...all.map((box) => box.y + box.h)) - y };
}
