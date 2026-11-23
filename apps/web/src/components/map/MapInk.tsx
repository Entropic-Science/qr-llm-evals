import { memo, useMemo, type CSSProperties } from "react";
import { STATIONS, type MapNode, type StudyGraph, type Visible } from "../../lib/map/graph";
import { COLUMN_W, STATION_X, type MapLayout } from "../../lib/map/layout";
import type { Box } from "../../lib/map/viewport";
import { seedFromId } from "../../lib/prng";
import { roughBrace, roughConnector, roughHex, roughLine, roughRect } from "../../lib/rough";
import type { RecState } from "../../lib/recommendations";
import { edgeInk, nodeInk } from "./ink";

// The ink of the study map: one SVG under the HTML nodes, in the same world coordinates, so the two cannot drift
// apart. Lane bands and the thread of the stations first, then the connectors, then each node's own strokes.
// Every path is seeded by what it draws; the picture is the same on every render.

interface MapInkProps {
  graph: StudyGraph;
  visible: Visible;
  layout: MapLayout;
  /** How each drawn node stands, by node id. */
  states: Readonly<Record<string, RecState | null>>;
  /** The node ids with a blocking problem. */
  errors: ReadonlySet<string>;
}

const PAD = 96;
/** Where the thread that joins the stations runs: between the headers and the first nodes. */
const THREAD_Y = -12;
const STATION_INDEX = new Map(STATIONS.map((station, index) => [station.id, index]));

const px = (x: number, y: number): CSSProperties => ({ transform: `translate(${x}px, ${y}px)` });

/** The strokes of one node. Its group is moved, not redrawn, when the layout moves the node. */
const NodeInk = memo(function NodeInk({ node, box, state, error }: { node: MapNode; box: Box; state: RecState | null; error: boolean }) {
  const strokes = useMemo(() => nodeInk(node, { w: box.w, h: box.h }, state), [node, box.w, box.h, state]);
  const dashed = node.ghost !== null || node.kind === "other_arm";
  const classes = [
    "map-ink__node", `ink--${node.kind}`, `ink--${node.tone}`,
    state !== null ? `ink--${state}` : "",
    node.ghost !== null ? "ink--ghost" : "",
    error ? "ink--error" : "",
  ].filter(Boolean).join(" ");
  return <g className={classes} data-ink={node.id} style={{ ...px(box.x, box.y), "--map-order": STATION_INDEX.get(node.station) ?? 0 } as CSSProperties}>
    {strokes.map((stroke, index) => {
      // A solid stroke is drawn in once; a dashed one keeps its dashes, which a drawing-in would take from it.
      const draws = (stroke.role === "outline" || stroke.role === "second") && !dashed;
      return <path key={`${stroke.role}${index}`} className={`ink-${stroke.role}${draws ? " ink-draws" : ""}`} d={stroke.d} pathLength={draws ? 1 : undefined} />;
    })}
  </g>;
});

export const MapInk = memo(function MapInk({ graph, visible, layout, states, errors }: MapInkProps) {
  const { bounds, boxes } = layout;
  const drawn = visible.nodes.filter((node) => boxes[node.id] !== undefined);

  const edges = useMemo(() => visible.edges.flatMap((edge) => {
    const ends = layout.edges[edge.id];
    if (ends === undefined) return [];
    const others = layout.order.filter((id) => id !== edge.from && id !== edge.to).map((id) => boxes[id]!);
    return [{ edge, ink: edgeInk(edge.id, ends.from, ends.to, others), at: `${ends.from.x},${ends.from.y},${ends.to.x},${ends.to.y}` }];
  }), [visible.edges, layout, boxes]);

  // The Holm family is a brace beside every test it holds; the completeness gate a rule over the lanes.
  const tests = drawn.filter((node) => node.kind === "test").map((node) => boxes[node.id]!);
  const family = boxes["family"];
  const brace = !graph.legacy && tests.length > 0 ? {
    top: Math.min(...tests.map((box) => box.y)) - 6,
    bottom: Math.max(...tests.map((box) => box.y + box.h)) + 6,
  } : null;
  const completeness = boxes["gate:completeness"];
  const first = layout.stations[0]!, last = layout.stations[layout.stations.length - 1]!;
  const thread = roughConnector({ x: first.x, y: THREAD_Y }, { x: last.x + last.w, y: THREAD_Y }, { seed: seedFromId("map:thread"), roughness: 1.6, arrow: true });

  return <svg
    className="map-ink"
    aria-hidden="true"
    focusable="false"
    style={{ left: bounds.x - PAD, top: bounds.y - PAD, width: bounds.w + 2 * PAD, height: bounds.h + 2 * PAD }}
    viewBox={`${bounds.x - PAD} ${bounds.y - PAD} ${bounds.w + 2 * PAD} ${bounds.h + 2 * PAD}`}
  >
    <g className="map-ink__lanes">
      {layout.lanes.flatMap((lane) => lane.segments.map((segment, index) => <g key={`${lane.key}:${index}`} className="map-lane-band" data-lane={lane.key} style={px(segment.x, segment.y)}>
        <path d={roughRect(0, 0, segment.w, segment.h, { seed: seedFromId(`lane:${lane.key}:${index}`), roughness: 1.8, radius: 12, passes: 1 })} />
      </g>))}
    </g>
    <g className="map-ink__thread">
      <path className="map-thread ink-draws" d={thread.path} pathLength={1} />
      <path className="map-thread__head" d={thread.head} />
      {layout.stations.map((station) => <path
        key={station.id}
        className="map-thread__stop"
        data-station={station.id}
        d={roughHex(station.x + 9, THREAD_Y, 7, { seed: seedFromId(`stop:${station.id}`), roughness: 0.7, passes: 1 })}
      />)}
    </g>
    {brace !== null ? <g className="map-brace" data-ink="family-brace">
      {/* `roughBrace` points right; mirrored, it opens toward the tests it holds. */}
      <g transform={`translate(${STATION_X.tests - 8} 0) scale(-1 1)`}>
        <path className="ink-draws" d={roughBrace(0, brace.top, brace.bottom, { seed: seedFromId("family:brace"), roughness: 1.1 })} pathLength={1} />
      </g>
      {family !== undefined ? <path
        className="map-brace__lead"
        d={roughLine(family.x + 10, family.y + family.h + 2, STATION_X.tests - 8, brace.top, { seed: seedFromId("family:lead"), roughness: 1, passes: 1 })}
      /> : null}
    </g> : null}
    {completeness !== undefined && tests.length > 0 ? <path
      className="map-rule"
      data-ink="completeness-rule"
      d={roughLine(STATION_X.tests, completeness.y + completeness.h + 20, STATION_X.verdict + COLUMN_W.verdict, completeness.y + completeness.h + 20, { seed: seedFromId("gate:completeness:rule"), roughness: 1.4, passes: 1 })}
    /> : null}
    <g className="map-ink__edges">
      {edges.map(({ edge, ink, at }) => <g
        key={`${edge.id}@${at}`}
        className={`map-edge map-edge--${edge.tone} map-edge--${edge.kind}${edge.dashed ? " is-dashed" : ""}${ink.detour ? " is-detour" : ""}`}
        data-edge={edge.id}
        data-from={edge.from}
        data-to={edge.to}
        data-hypotheses={edge.hypotheses.join(" ")}
      >
        <path className={`map-edge__line${edge.dashed ? "" : " ink-draws"}`} d={ink.path} pathLength={edge.dashed ? undefined : 1} />
        <path className="map-edge__head" d={ink.head} />
      </g>)}
    </g>
    <g className="map-ink__nodes">
      {drawn.map((node) => <NodeInk key={node.id} node={node} box={boxes[node.id]!} state={states[node.id] ?? null} error={errors.has(node.id)} />)}
    </g>
  </svg>;
});
