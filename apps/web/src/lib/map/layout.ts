import type { Pt } from "../rough";
import { STATIONS, shownByExpansion, visible, type MapNode, type NodeKind, type StationId, type StudyGraph } from "./graph";
import type { Box, Size, ZoomTier } from "./viewport";

// Where the study map puts everything, in world units. No DOM is measured: every size comes from one table,
// and text that does not fit is cut in CSS.
//
// A station is a column. The question, the evals and their outcomes run down from the top; arms, tests, gates
// and verdicts stack inside the lane of their comparison, under what all lanes share (the Holm family, the
// completeness gate). A node that folds into one of another station sits level with it: an outcome beside its
// eval, a verdict beside its test.
//
// Every stack grows downward only, so unfolding a node moves what is below it and nothing else. The zoom tier
// decides which nodes get a box, never where a box is: zooming never moves a node.

/** The left edge of each station's column. */
export const STATION_X: Readonly<Record<StationId, number>> = {
  question: 0, arms: 320, prompts: 640, measures: 960, tests: 1240, gates: 1500, verdict: 1740,
};
/** The width of each column: the width of the nodes that stand in it. */
export const COLUMN_W: Readonly<Record<StationId, number>> = {
  question: 260, arms: 220, prompts: 260, measures: 220, tests: 200, gates: 180, verdict: 200,
};

const ARM: Size = { w: 220, h: 72 };
export const NODE_SIZE: Readonly<Record<Exclude<NodeKind, "ghost_stack">, Size>> = {
  study: { w: 260, h: 160 },
  quantum_arm: ARM, supervised_arm: ARM, seeded_arm: ARM, positive_control: ARM, other_arm: ARM,
  control_deck: { w: 220, h: 88 },
  card: { w: 260, h: 96 },
  card_group: { w: 260, h: 56 },
  tier0_band: { w: 260, h: 56 },
  endpoint: { w: 220, h: 56 },
  test: { w: 200, h: 64 },
  family: { w: 200, h: 56 },
  gate: { w: 180, h: 56 },
  verdict: { w: 200, h: 56 },
};
/** A placeholder is as wide as its column. */
export const GHOST_H = 56;
/** The size a node grows to, in place and above its neighbours, while it is open. */
export const OPEN_SIZE: Readonly<{ card: Size; other: Size }> = { card: { w: 560, h: 420 }, other: { w: 360, h: 240 } };

export const GAP = 16;
export const LANE_GAP = 48;
/** The strip at the top of a lane that holds its label. */
export const LANE_LABEL_H = 28;
/** How far a lane's band reaches beyond the columns it frames. */
export const LANE_PAD = 12;
export const HEADER_H = 72;
/** The clear space between a station header and the first node under it. */
export const HEADER_GAP = 24;
/** The room every lane column keeps above the first lane, for what all lanes share. */
const SHARED_ROW_H = 56;
/** A lane with nothing in it is still this tall under its label. */
const EMPTY_LANE_H = 72;

export interface StationLayout { id: StationId; label: string; sentence: string; x: number; w: number; header: Box }

export interface LaneLayout {
  key: string;
  label: string;
  /** From the arms to the verdicts. The eval and outcome columns cross it; they belong to every lane. */
  band: Box;
  /** The two parts of the band that hold lane nodes: the arms, and the tests through to the verdicts. */
  segments: Box[];
  /** Where the label goes: the strip above the lane's arms. */
  labelBox: Box;
}

export interface Neighbors { left?: string; right?: string; up?: string; down?: string }

export interface MapLayout {
  /** A box for every node drawn at this tier, by node id. */
  boxes: Record<string, Box>;
  /** The drawn node ids in reading order. */
  order: string[];
  stations: StationLayout[];
  lanes: LaneLayout[];
  /** Per drawn edge that is a line (not `implied`): it leaves the right-middle of one box and enters the left-middle of the other. */
  edges: Record<string, { from: Pt; to: Pt }>;
  /** Everything at full density: the same at every tier, so "Fit" does not jump when the zoom crosses one. */
  bounds: Box;
  /** What the story zoom draws (its nodes, the station headings, the lanes): also the same at every tier. */
  storyBounds: Box;
  /** The nearest drawn node in each direction, for the arrow keys. */
  neighbors: Record<string, Neighbors>;
}

export function sizeOf(node: Pick<MapNode, "kind" | "station">): Size {
  return node.kind === "ghost_stack" ? { w: COLUMN_W[node.station], h: GHOST_H } : NODE_SIZE[node.kind];
}

const NO_EXPANSION: ReadonlySet<string> = new Set();

/**
 * The layout of a graph for a set of unfolded nodes at a zoom tier. Pure and deterministic: the same graph,
 * set and tier give the same numbers.
 */
export function layoutGraph(graph: StudyGraph, expanded: ReadonlySet<string> = NO_EXPANSION, tier: ZoomTier = "design"): MapLayout {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const shown = shownByExpansion(graph, expanded);
  const placed: Record<string, Box> = {};

  /** Puts a node at `y` with everything that hangs off it, and returns the height that took. */
  const place = (node: MapNode, y: number): number => {
    const size = sizeOf(node);
    placed[node.id] = { x: STATION_X[node.station], y, w: size.w, h: size.h };
    const children = node.children.flatMap((id) => (shown.has(id) ? byId.get(id) ?? [] : []));
    // Children of another station stand level with the node, in their own column.
    let beside = y;
    for (const child of children.filter((item) => item.station !== node.station)) beside += place(child, beside) + GAP;
    const row = Math.max(size.h, beside - y - GAP);
    // Children of the same station follow under it.
    let below = y + row;
    for (const child of children.filter((item) => item.station === node.station)) below += GAP + place(child, below + GAP);
    return below - y;
  };
  const stack = (nodes: readonly MapNode[], y: number): number => {
    let cursor = y;
    for (const node of nodes) cursor += place(node, cursor) + GAP;
    return nodes.length === 0 ? 0 : cursor - y - GAP;
  };

  const laneKeys = new Set(graph.lanes.map((lane) => lane.key));
  const roots = graph.nodes.filter((node) => node.parent === null && shown.has(node.id));
  const inLane = (node: MapNode) => node.lane !== null && laneKeys.has(node.lane);
  const laneStations = new Set(roots.filter(inLane).map((node) => node.station));

  // What all lanes share, and the columns that have no lanes, run down from the top.
  let shared = SHARED_ROW_H;
  for (const station of STATIONS) {
    const height = stack(roots.filter((node) => node.station === station.id && !inLane(node)), 0);
    if (laneStations.has(station.id)) shared = Math.max(shared, height);
  }

  const lanes: LaneLayout[] = [];
  let top = shared + LANE_GAP;
  const left = STATION_X.arms - LANE_PAD;
  const right = STATION_X.verdict + COLUMN_W.verdict + LANE_PAD;
  for (const lane of graph.lanes) {
    let content = EMPTY_LANE_H;
    for (const station of STATIONS) {
      content = Math.max(content, stack(roots.filter((node) => node.station === station.id && node.lane === lane.key), top + LANE_LABEL_H));
    }
    const h = LANE_LABEL_H + content;
    lanes.push({
      key: lane.key, label: lane.label,
      band: { x: left, y: top, w: right - left, h },
      segments: [
        { x: left, y: top, w: COLUMN_W.arms + 2 * LANE_PAD, h },
        { x: STATION_X.tests - LANE_PAD, y: top, w: right - (STATION_X.tests - LANE_PAD), h },
      ],
      labelBox: { x: STATION_X.arms, y: top, w: COLUMN_W.arms, h: LANE_LABEL_H },
    });
    top += h + LANE_GAP;
  }

  const stations = STATIONS.map((station): StationLayout => ({
    ...station, x: STATION_X[station.id], w: COLUMN_W[station.id],
    header: { x: STATION_X[station.id], y: -(HEADER_H + HEADER_GAP), w: COLUMN_W[station.id], h: HEADER_H },
  }));

  const drawn = visible(graph, expanded, tier);
  const boxes: Record<string, Box> = {};
  const order: string[] = [];
  for (const node of drawn.nodes) {
    const box = placed[node.id];
    if (box === undefined) continue;
    boxes[node.id] = box;
    order.push(node.id);
  }
  const edges: Record<string, { from: Pt; to: Pt }> = {};
  for (const edge of drawn.edges) {
    const from = boxes[edge.from], to = boxes[edge.to];
    if (edge.implied || from === undefined || to === undefined) continue;
    edges[edge.id] = { from: { x: from.x + from.w, y: from.y + from.h / 2 }, to: { x: to.x, y: to.y + to.h / 2 } };
  }

  const frame = [...stations.map((station) => station.header), ...lanes.map((lane) => lane.band)];
  const story = tier === "story" ? drawn : visible(graph, expanded, "story");
  return {
    boxes, order, stations, lanes, edges,
    bounds: union([...Object.values(placed), ...frame]),
    storyBounds: union([...story.nodes.flatMap((node) => placed[node.id] ?? []), ...frame]),
    neighbors: neighborsOf(order, boxes),
  };
}

function union(boxes: readonly Box[]): Box {
  if (boxes.length === 0) return { x: 0, y: 0, w: 0, h: 0 };
  const x = Math.min(...boxes.map((box) => box.x)), y = Math.min(...boxes.map((box) => box.y));
  return { x, y, w: Math.max(...boxes.map((box) => box.x + box.w)) - x, h: Math.max(...boxes.map((box) => box.y + box.h)) - y };
}

/**
 * For each node, the nearest other node in each direction, by box centre. Straying off the axis costs double,
 * so "right" prefers the node across the gutter to one a row away. The first in reading order wins a tie.
 */
function neighborsOf(order: readonly string[], boxes: Readonly<Record<string, Box>>): Record<string, Neighbors> {
  const centre = (id: string) => { const box = boxes[id]!; return { x: box.x + box.w / 2, y: box.y + box.h / 2 }; };
  const result: Record<string, Neighbors> = {};
  for (const id of order) {
    const own = centre(id);
    const best: Record<keyof Neighbors, { id: string; cost: number } | null> = { left: null, right: null, up: null, down: null };
    for (const other of order) {
      if (other === id) continue;
      const at = centre(other);
      const dx = at.x - own.x, dy = at.y - own.y;
      const offer = (direction: keyof Neighbors, along: number, across: number) => {
        if (along <= 0) return;
        const cost = along + 2 * Math.abs(across);
        const current = best[direction];
        if (current === null || cost < current.cost) best[direction] = { id: other, cost };
      };
      offer("right", dx, dy);
      offer("left", -dx, dy);
      offer("down", dy, dx);
      offer("up", -dy, dx);
    }
    const entry: Neighbors = {};
    for (const direction of ["left", "right", "up", "down"] as const) {
      const found = best[direction];
      if (found !== null) entry[direction] = found.id;
    }
    result[id] = entry;
  }
  return result;
}
