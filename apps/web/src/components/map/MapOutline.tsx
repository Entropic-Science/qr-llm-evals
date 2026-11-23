import { useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { STATIONS, type MapNode, type StudyGraph } from "../../lib/map/graph";
import type { MapNote } from "../../lib/map/notes";
import type { Problem } from "../../lib/problems";
import { VerdictStamps } from "../study/design";
import type { Subject } from "./Inspector";
import { MapChip, StatePip } from "./MapChip";
import { noteName } from "./MapNotes";
import { editable, problemWords, settingOf, severityOf, stateWords } from "./model";

interface OutlineTools {
  graph: StudyGraph;
  notes: readonly MapNote[];
  /** The node or note whose settings are open under its entry, or `null`. */
  open: string | null;
  /** Opens the settings of a node or a note under its entry, on the field of `path` when one is named; `null` closes them. */
  onOpen: (id: string | null, path?: string) => void;
  subjectOf: (id: string) => Subject | null;
  /** What an open entry holds: the fields, problems and prompts of its subject. */
  inspect: (subject: Subject) => ReactNode;
  onSetting: (path: string) => void;
  onProblem: (problem: Problem) => void;
  onFix: (problem: Problem) => void;
}

interface MapOutlineProps extends OutlineTools {
  /** The outline's heading, which takes the focus when the outline is asked for from the keyboard. */
  heading: RefObject<HTMLHeadingElement | null>;
}

interface EntryProps {
  tools: OutlineTools;
  node: MapNode;
}

/** A nested list this short is shown unfolded. */
const OPEN_UP_TO = 4;
/** Whether an entry has something to say that must not be folded away. */
const troubled = (node: MapNode) => node.problems.length > 0;

interface FoldProps {
  summary: string;
  nodes: readonly MapNode[];
  /** The open node or note, if its settings are under an entry of this list; `null` otherwise. */
  opened: string | null;
  children: ReactNode;
}

/** The open node or note, if it is one of `nodes` or a note that hangs on one of them. */
function openedIn(nodes: readonly MapNode[], tools: Pick<OutlineTools, "open" | "notes">): string | null {
  if (tools.open === null) return null;
  const note = tools.notes.find((item) => item.id === tools.open);
  const holder = note === undefined ? tools.open : note.anchor.type === "node" ? note.anchor.node : null;
  return nodes.some((node) => node.id === holder) ? tools.open : null;
}

/**
 * A folded list of the outline. It starts unfolded when it is short or holds a problem, and unfolds when the
 * settings of one of its entries are opened. After that only the reader folds it: closing an entry leaves the
 * list it sits in as it is.
 */
function Fold({ summary, nodes, opened, children }: FoldProps) {
  const must = nodes.length <= OPEN_UP_TO || nodes.some(troubled);
  const [unfolded, setUnfolded] = useState(must || opened !== null);
  const [seen, setSeen] = useState({ must, opened });
  if (seen.must !== must || seen.opened !== opened) {
    setSeen({ must, opened });
    if ((must && !seen.must) || (opened !== null && opened !== seen.opened)) setUnfolded(true);
  }
  return <details className="map-outline__fold" open={unfolded} onToggle={(event) => setUnfolded(event.currentTarget.open)}>
    <summary>{summary}</summary>
    <ul className="map-outline__list">{children}</ul>
  </details>;
}

/** A marginal note as a button of the outline: what it says, how it stands, and the way to its settings. */
function NoteButton({ tools, note }: { tools: OutlineTools; note: MapNote }) {
  const open = tools.open === note.id;
  return <button
    type="button"
    className={`map-outline__note${note.state !== null ? ` is-${note.state}` : ""}${note.severity !== null ? ` has-${note.severity}` : ""}${open ? " is-open" : ""}`}
    data-note={note.id}
    data-state={note.state ?? "none"}
    aria-label={noteName(note)}
    aria-expanded={open}
    onClick={() => tools.onOpen(open ? null : note.id)}
  >
    {note.severity !== null ? <span className={`map-marginal__problem map-marginal__problem--${note.severity}`} aria-hidden="true">{note.severity === "error" ? "✕" : "!"}</span> : null}
    {note.state !== null ? <StatePip state={note.state} /> : null}
    <span>{note.text}</span>
  </button>;
}

/** The settings of the open node or note, under the entry they belong to. Escape puts them away and returns to what opened them. */
function Opened({ tools, id }: { tools: OutlineTools; id: string }) {
  const subject = tools.subjectOf(id);
  if (subject === null) return null;
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    event.preventDefault();
    const opener = event.currentTarget.parentElement?.querySelector<HTMLElement>(`[data-opens="${id}"], [data-note="${id}"]`);
    tools.onOpen(null);
    opener?.focus();
  };
  return <section className={`map-sheet map-sheet--inline map-sheet--${subject.kind}`} data-sheet={id} aria-label={`Settings of ${subject.label}`} onKeyDown={onKeyDown}>
    {tools.inspect(subject)}
  </section>;
}

/** One node of the graph as a list entry, with the nodes of its own station that fold into it nested under it. */
function Entry({ tools, node }: EntryProps) {
  const { graph, onSetting, onProblem, onFix } = tools;
  const byId = (id: string) => graph.nodes.find((item) => item.id === id);
  const parent = node.parent === null ? undefined : byId(node.parent);
  const nested = node.children.flatMap((id) => byId(id) ?? []).filter((child) => child.station === node.station);
  const notes = tools.notes.filter((note) => note.anchor.type === "node" && note.anchor.node === node.id);
  const setting = settingOf(node);
  const severity = severityOf(node.problems);
  const open = tools.open === node.id;
  const openNote = notes.find((note) => note.id === tools.open);
  const classes = ["map-outline__node", node.state !== null ? `is-${node.state}` : "is-unrated", node.ghost !== null ? "is-ghost" : "", severity === "error" ? "has-error" : severity === "warning" ? "has-warning" : "", open ? "is-open" : ""].filter(Boolean).join(" ");
  return <li className={classes} data-node={node.id} data-kind={node.kind} data-state={node.state ?? "none"}>
    <div className="map-outline__row">
      {node.state !== null ? <StatePip state={node.state} /> : null}
      <span className="map-outline__label">{node.label}</span>
      <span className="map-outline__sub">{node.sub}{parent !== undefined && parent.station !== node.station ? ` · of ${parent.label}` : ""}</span>
      <span className="sr-only">{[stateWords(node.state), problemWords(node.problems)].filter(Boolean).map((text) => ` ${text}.`).join("")}</span>
      <span className="map-outline__tools">
        <button type="button" className="text-button map-outline__toggle" data-opens={node.id} aria-expanded={open} aria-label={`${open ? "Close" : "Open"} ${node.label}`} onClick={() => tools.onOpen(open ? null : node.id)}>{open ? "Close" : "Open"}</button>
        {setting !== null ? <button type="button" className="text-button map-outline__open" aria-label={`Open the settings of ${node.label}`} data-setting={setting} onClick={() => onSetting(setting)}>All settings</button> : null}
      </span>
    </div>
    <p className="map-outline__story">{node.facts.story}</p>
    {node.ref.type === "verdict" ? <VerdictStamps subject={node.ref.hypothesis.id} stamps={node.ref.stamps} /> : null}
    {node.recs.length > 0 ? <div className="map-outline__chips">
      {node.recs.map((rec) => <MapChip key={rec.path} rec={rec} onOpen={editable(rec.path) ? () => tools.onOpen(node.id, rec.path) : undefined} />)}
    </div> : null}
    {notes.length > 0 ? <div className="map-outline__notes">
      {notes.map((note) => <NoteButton key={note.id} tools={tools} note={note} />)}
    </div> : null}
    {/* An open entry lists its problems itself, with the same fixes. */}
    {node.problems.length > 0 && !open ? <ul className="map-outline__problems" aria-label={`Problems on ${node.label}`}>
      {node.problems.map((problem) => <li key={`${problem.code}|${problem.message}`} className={`map-sheet__problem map-sheet__problem--${problem.severity}`} data-code={problem.code}>
        <strong><span aria-hidden="true">{problem.severity === "error" ? "✕" : "!"}</span> {problem.severity === "error" ? "Blocks launch" : "Warning"}</strong>
        <span>{problem.message}</span>
        <span className="map-sheet__actions">
          {problem.fix ? <button type="button" className="text-button" onClick={() => onFix(problem)}>{problem.fix.label}</button> : null}
          <button type="button" className="text-button" onClick={() => onProblem(problem)}>Show in all settings</button>
        </span>
      </li>)}
    </ul> : null}
    {open ? <Opened tools={tools} id={node.id} /> : null}
    {openNote !== undefined ? <Opened tools={tools} id={openNote.id} /> : null}
    {nested.length > 0 ? <Fold summary={`${nested.length} ${nested.length === 1 ? "part" : "parts"} of ${node.label}`} nodes={nested} opened={openedIn(nested, tools)}>
      {nested.map((child) => <Entry key={child.id} tools={tools} node={child} />)}
    </Fold> : null}
  </li>;
}

/**
 * The study map as a nested list: the same graph, station by station and comparison by comparison, with the
 * same states, the same chips, the same marginal notes and the same fields behind each of them. It is what a
 * phone gets, and what anyone can ask for who would rather read the study than look at it.
 */
export function MapOutline({ heading, ...tools }: MapOutlineProps) {
  const { graph } = tools;
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  // A node heads its own entry unless it folds into a node of the same station, where it is nested.
  const heads = graph.nodes.filter((node) => node.parent === null || byId.get(node.parent)?.station !== node.station);
  return <div className="map-outline">
    <h2 className="map-outline__title" tabIndex={-1} ref={heading}>The study, station by station</h2>
    {STATIONS.map((station) => {
      const own = heads.filter((node) => node.station === station.id);
      // What the diagram keeps folded into a node of another station (an outcome that decides nothing) is listed last, folded.
      const aside = own.filter((node) => node.parent !== null && !node.pinned && node.lane === null);
      const shared = own.filter((node) => node.lane === null && !aside.includes(node));
      const lanes = graph.lanes.map((lane) => ({ lane, nodes: own.filter((node) => node.lane === lane.key) })).filter((group) => group.nodes.length > 0);
      const notes = tools.notes.filter((note) => note.anchor.type === "station" && note.anchor.station === station.id);
      const openNote = notes.find((note) => note.id === tools.open);
      return <section key={station.id} className="map-outline__station" data-station={station.id} aria-labelledby={`map-outline-${station.id}`}>
        <h3 id={`map-outline-${station.id}`}>{station.label}</h3>
        <p className="map-outline__sentence">{station.sentence}</p>
        {notes.length > 0 ? <div className="map-outline__notes map-outline__notes--station">
          {notes.map((note) => <NoteButton key={note.id} tools={tools} note={note} />)}
          {openNote !== undefined ? <Opened tools={tools} id={openNote.id} /> : null}
        </div> : null}
        {own.length === 0 ? <p className="map-outline__empty">Nothing is drawn here yet.</p> : null}
        {shared.length > 0 ? <ul className="map-outline__list">
          {shared.map((node) => <Entry key={node.id} tools={tools} node={node} />)}
        </ul> : null}
        {/* A study on the fixed Study 1 plan has tests the map does not draw: nothing is said of their verdicts. */}
        {aside.length > 0 ? <Fold summary={graph.legacy ? `${aside.length} more` : `${aside.length} more, reported without a verdict`} nodes={aside} opened={openedIn(aside, tools)}>
          {aside.map((node) => <Entry key={node.id} tools={tools} node={node} />)}
        </Fold> : null}
        {lanes.map(({ lane, nodes }) => <div key={lane.key} className="map-outline__lane" data-lane={lane.key}>
          <h4>{lane.label}</h4>
          <ul className="map-outline__list">
            {nodes.map((node) => <Entry key={node.id} tools={tools} node={node} />)}
          </ul>
        </div>)}
      </section>;
    })}
  </div>;
}
