import { memo, useId, type CSSProperties, type MouseEvent } from "react";
import type { MapNode } from "../../lib/map/graph";
import type { Box, ZoomTier } from "../../lib/map/viewport";
import type { Problem } from "../../lib/problems";
import type { Rec, RecState } from "../../lib/recommendations";
import { VerdictStamp } from "../study/design";
import { MapChip, StatePip } from "./MapChip";
import { chipsFor, editable, nodeName, problemWords, settingOf, severityOf } from "./model";

export interface MapNodeActions {
  /** The node's own action: unfold or fold it when it holds others, open its details when it does not. */
  onActivate: (id: string, byPointer: boolean) => void;
  /** Opens the node's details. */
  onOpen: (id: string) => void;
  /** Brings the node to the middle of the view, large enough to read. */
  onZoomTo: (id: string) => void;
  onFocus: (id: string) => void;
  /** The focus left the node's title. */
  onBlur: (id: string) => void;
  onHover: (id: string | null) => void;
  /** Opens the node's popover on the field of one of its settings (a `Rec` path). */
  onField: (id: string, path: string) => void;
  /** Opens the settings dialog on a setting (a study key path or a `Rec` path). */
  onSetting: (path: string) => void;
  /** Opens the settings dialog on the problems of a node. */
  onProblems: (problems: readonly Problem[]) => void;
  register: (id: string, element: HTMLButtonElement | null) => void;
}

interface MapNodeViewProps extends MapNodeActions {
  node: MapNode;
  box: Box;
  tier: ZoomTier;
  /** How the node stands, with what is folded into it. */
  state: RecState | null;
  problems: readonly Problem[];
  expanded: boolean;
  open: boolean;
  /** How many nodes unfolding it would add: its children that are not drawn. */
  unfolds: number;
  /** Whether the node is the one in the tab order. */
  current: boolean;
  /** The place of the node's station, for the staggered first drawing. */
  order: number;
}

/**
 * The name a node is drawn with. At the story zoom a name is cut where its box ends, and "Verdict of H1" would
 * lose the one part that tells it from the next: there it starts with the number. The spoken name stays whole.
 */
function shownLabel(node: MapNode, tier: ZoomTier): string {
  return tier === "story" && node.ref.type === "verdict" ? `${node.ref.hypothesis.id} verdict` : node.label;
}

/** What stands in the hexagon of a test: the number of its hypothesis. */
function mark(node: MapNode): string | null {
  return node.ref.type === "test" ? node.ref.hypothesis.id : null;
}

/**
 * One node of the study map, as HTML over its pen strokes: its name, what it says at this zoom, the settings
 * that sit on it and the problems it answers for.
 *
 * The node's state is said three ways: the glyph and border of its pip, the class (and with it the hatching
 * drawn behind), and the words of its accessible name.
 */
export const MapNodeView = memo(function MapNodeView({
  node, box, tier, state, problems, expanded, open, unfolds, current, order,
  onActivate, onOpen, onZoomTo, onFocus, onBlur, onHover, onField, onSetting, onProblems, register,
}: MapNodeViewProps) {
  const noteId = useId();
  const ruleId = useId();
  const severity = severityOf(problems);
  const chips = chipsFor(node, tier);
  const more = tier === "design" ? node.recs.filter((rec) => rec.state !== "locked").length - chips.length : 0;
  const setting = settingOf(node);
  const tab = current ? 0 : -1;
  const openRec = (rec: Rec) => onField(node.id, rec.path);
  const glyph = mark(node);
  const classes = [
    "map-node", `map-node--${node.kind}`, `map-node--${node.tone}`,
    state !== null ? `is-${state}` : "is-unrated",
    node.ghost !== null ? "is-ghost" : "",
    expanded ? "is-expanded" : "",
    open ? "is-open" : "",
    current ? "is-current" : "",
    severity === "error" ? "has-error" : severity === "warning" ? "has-warning" : "",
  ].filter(Boolean).join(" ");

  // A second click of a double-click zooms; it does not undo what the first one did.
  const activate = (event: MouseEvent) => { if (event.detail <= 1) onActivate(node.id, event.detail === 1); };

  return <div
    className={classes}
    role="group"
    aria-label={node.label}
    data-node={node.id}
    data-kind={node.kind}
    data-station={node.station}
    data-state={state ?? "none"}
    style={{ transform: `translate(${box.x}px, ${box.y}px)`, width: box.w, height: box.h, "--map-order": order } as CSSProperties}
    onDoubleClick={(event) => {
      if ((event.target as HTMLElement).closest(".map-node__chips, .map-node__tools, .map-node__problems, .map-node__stamps")) return;
      onZoomTo(node.id);
    }}
    onPointerEnter={() => onHover(node.id)}
    onPointerLeave={() => onHover(null)}
  >
    {glyph !== null ? <span className="map-node__mark" aria-hidden="true">{glyph}</span> : null}
    <button
      type="button"
      className="map-node__title"
      ref={(element) => register(node.id, element)}
      tabIndex={tab}
      aria-label={nodeName(node, state, problems)}
      aria-expanded={node.expandable ? expanded : undefined}
      aria-haspopup={node.expandable ? undefined : "dialog"}
      aria-describedby={noteId}
      title={node.label}
      onClick={activate}
      onFocus={() => onFocus(node.id)}
      onBlur={() => onBlur(node.id)}
    >
      {state !== null ? <StatePip state={state} className="map-node__pip" /> : null}
      <span className="map-node__label">{shownLabel(node, tier)}</span>
      {node.expandable ? <span className="map-node__fold" aria-hidden="true">{expanded ? "−" : `+${unfolds}`}</span> : null}
    </button>
    <p className="map-node__sub">{node.sub}</p>
    {tier === "story" || node.kind === "study" ? <p className="map-node__story">{node.facts.story}</p> : null}
    {tier === "detail" ? <p className="map-node__facts">{[...node.facts.design, ...node.facts.detail].join(" ")}</p> : null}
    {node.ref.type === "verdict" && tier !== "story" ? <ul className="map-node__stamps" aria-label={`Verdicts ${node.ref.hypothesis.id} can end with`}>
      {node.ref.stamps.map((stamp) => <li key={stamp.verdict} className="map-stamp" data-stamp={stamp.verdict}>
        {/* A stamp answers the pointer and the keyboard with the rule that leads to it; a press opens the verdict's popover, which has them all. */}
        <button type="button" className="map-stamp__button" tabIndex={tab} aria-describedby={`${ruleId}-${stamp.verdict}`} onClick={() => onOpen(node.id)}>
          <VerdictStamp verdict={stamp.verdict} label={stamp.label} state={stamp.reachable ? "reachable" : "unreachable"} />
        </button>
        <span className="map-stamp__rule" role="tooltip" id={`${ruleId}-${stamp.verdict}`}>
          <strong>{stamp.label}{stamp.reachable ? "" : ": unreachable"}</strong>
          {stamp.reachable ? null : <span>{stamp.reason}</span>}
          <span>{stamp.rules.length > 1 ? "Returned when one of these holds: " : "Returned when: "}{stamp.rules.join(" Or: ")}</span>
        </span>
      </li>)}
    </ul> : null}
    {chips.length > 0 ? <div className="map-node__chips">
      {chips.map((rec) => <MapChip key={rec.path} rec={rec} tabIndex={tab} onOpen={editable(rec.path) ? openRec : undefined} />)}
      {more > 0 ? <span className="map-node__more" title={`${more} more ${more === 1 ? "setting" : "settings"}; zoom in or open the node to see ${more === 1 ? "it" : "them"}.`}>+{more}</span> : null}
    </div> : null}
    {problems.length > 0 ? <button
      type="button"
      className={`map-node__problems map-node__problems--${severity ?? "warning"}`}
      tabIndex={tab}
      aria-label={`${problemWords(problems)} on ${node.label}. Show ${problems.length === 1 ? "it" : "them"} in the settings.`}
      title={problems.map((problem) => problem.message).join("\n")}
      onClick={() => onProblems(problems)}
    ><span aria-hidden="true">{severity === "error" ? "✕" : "!"}</span>{problems.length}</button> : null}
    <span className="map-node__tools">
      {node.expandable ? <button type="button" className="map-node__tool" tabIndex={tab} aria-label={`Details of ${node.label}`} title="Details" onClick={() => onOpen(node.id)}><span aria-hidden="true">⋯</span></button> : null}
      {setting !== null ? <button type="button" className="map-node__tool" tabIndex={tab} aria-label={`Open the settings of ${node.label}`} title="Open in the settings" data-setting={setting} onClick={() => onSetting(setting)}><span aria-hidden="true">✎</span></button> : null}
    </span>
    <p className="sr-only" id={noteId}>{node.describes}</p>
    {/* The same sentence for the eye, beside a node the pointer or the keyboard rests on. */}
    {tier !== "story" && node.describes ? <p className="map-node__note" aria-hidden="true">{node.describes}</p> : null}
  </div>;
});
