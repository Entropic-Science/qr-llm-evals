import type { MapNode } from "../../lib/map/graph";
import type { Box, Size } from "../../lib/map/viewport";
import { seedFromId } from "../../lib/prng";
import { roughConnector, roughHachure, roughHex, roughLine, roughRect, type Pt } from "../../lib/rough";
import type { RecState } from "../../lib/recommendations";

// The pen strokes of the study map, as path data. Everything is seeded by the id of what it draws, so a node's
// outline is the same on every render, pan and edit, and nothing shimmers. No DOM, no React.

/** What a stroke is, which decides its weight and colour in CSS. */
export type InkRole = "fill" | "hatch" | "outline" | "second" | "detail" | "dots" | "port" | "port-open";

export interface InkStroke {
  role: InkRole;
  d: string;
}

const ROUGHNESS = 1.3;
/** How far apart the hatching lines of each state are: the denser, the more the state asks for attention. */
const HATCH_GAP: Readonly<Partial<Record<RecState, number>>> = {
  invalid: 5, insufficient: 7, marginal: 9, sufficient: 9, unknown: 12, custom: 12,
};
/** The dot lattice of an eval: prompts across, repetitions down, capped so it stays a mark and not a table. */
const LATTICE = { cols: 12, rows: 6, step: 6, radius: 1.25 };

const seed = (id: string, part: string) => seedFromId(`${id}:${part}`);
const n = (value: number) => (Math.round(value * 10) / 10).toString();

/** A plain rounded rectangle, for the paper under a node: the pen's wobble stays inside the outline drawn over it. */
function plate(x: number, y: number, w: number, h: number, r = 0): string {
  const radius = Math.max(0, Math.min(r, w / 2, h / 2));
  if (radius === 0) return `M${n(x)} ${n(y)} H${n(x + w)} V${n(y + h)} H${n(x)} Z`;
  return `M${n(x + radius)} ${n(y)} H${n(x + w - radius)} Q${n(x + w)} ${n(y)} ${n(x + w)} ${n(y + radius)} V${n(y + h - radius)} Q${n(x + w)} ${n(y + h)} ${n(x + w - radius)} ${n(y + h)} H${n(x + radius)} Q${n(x)} ${n(y + h)} ${n(x)} ${n(y + h - radius)} V${n(y + radius)} Q${n(x)} ${n(y)} ${n(x + radius)} ${n(y)} Z`;
}

function polygon(points: readonly Pt[]): string {
  return `${points.map((point, index) => `${index === 0 ? "M" : "L"}${n(point.x)} ${n(point.y)}`).join(" ")} Z`;
}

/** A closed shape drawn side by side, each side its own stroke, as a ruled figure is. */
function roughPolygon(points: readonly Pt[], id: string, part: string): string {
  return points.map((point, index) => {
    const next = points[(index + 1) % points.length]!;
    return roughLine(point.x, point.y, next.x, next.y, { seed: seed(id, `${part}${index}`), roughness: ROUGHNESS });
  }).join(" ");
}

function circle(cx: number, cy: number, r: number): string {
  return `M${n(cx - r)} ${n(cy)} a${n(r)} ${n(r)} 0 1 0 ${n(2 * r)} 0 a${n(r)} ${n(r)} 0 1 0 ${n(-2 * r)} 0`;
}

/** The corners of a gate: a box whose left and right ends come to a point, where the thread enters and leaves. */
function gatePoints(w: number, h: number): Pt[] {
  const tip = 14;
  return [{ x: 1, y: h / 2 }, { x: tip, y: 1 }, { x: w - tip, y: 1 }, { x: w - 1, y: h / 2 }, { x: w - tip, y: h - 1 }, { x: tip, y: h - 1 }];
}

/** How many sheets a stack shows behind its front one. */
function sheetsBehind(node: MapNode): number {
  if (node.ref.type === "deck") return Math.max(1, Math.min(4, new Set(node.ref.seeds).size - 1));
  return 2;
}

/**
 * The strokes of one node in its own coordinates (its box starts at 0, 0), back to front: the paper, the hatching
 * of its state, the outline of its kind, and the marks that tell the kinds apart.
 */
export function nodeInk(node: MapNode, size: Size, state: RecState | null): InkStroke[] {
  const { w, h } = size;
  const id = node.id;
  const strokes: InkStroke[] = [];
  const outline = (radius: number, inset = 1, part = "outline") =>
    roughRect(inset, inset, w - 2 * inset, h - 2 * inset, { seed: seed(id, part), roughness: ROUGHNESS, radius });
  const gap = state === null ? undefined : HATCH_GAP[state];
  const hatch = (x: number, y: number, width: number, height: number) => {
    if (gap !== undefined) strokes.push({ role: "hatch", d: roughHachure(x, y, width, height, { seed: seed(id, "hatch"), roughness: 0.8, gap }) });
    // The quantum arm is tinted in its own ink where no state claims the hatching.
    else if (node.tone === "quantum" && node.ghost === null) strokes.push({ role: "hatch", d: roughHachure(x, y, width, height, { seed: seed(id, "hatch"), roughness: 0.8, gap: 10 }) });
  };

  switch (node.kind) {
    case "study": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2) });
      hatch(4, 4, w - 8, h - 8);
      strokes.push({ role: "outline", d: outline(0) });
      // A ruled line under the title, as on a sheet of notes.
      strokes.push({ role: "detail", d: roughLine(12, 44, w - 12, 44, { seed: seed(id, "rule"), roughness: 1, passes: 1 }) });
      break;
    }
    case "quantum_arm":
    case "supervised_arm": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, 12) });
      hatch(5, 5, w - 10, h - 10);
      strokes.push({ role: "outline", d: outline(12) });
      // Observed: a ring around the arm, for the people watching it.
      if (node.kind === "supervised_arm") strokes.push({ role: "second", d: roughRect(-4, -4, w + 8, h + 8, { seed: seed(id, "ring"), roughness: ROUGHNESS, radius: 16, passes: 1 }) });
      break;
    }
    case "control_deck":
    case "card_group": {
      // One sheet per seed (or a few for a tier of evals), fanned behind the front one.
      for (let sheet = sheetsBehind(node); sheet >= 1; sheet--) {
        const o = sheet * 3.5;
        strokes.push({
          role: "second",
          d: `${roughLine(8 + o, 1 - o, w - 1 + o, 1 - o, { seed: seed(id, `top${sheet}`), roughness: 1, passes: 1 })} ${roughLine(w - 1 + o, 1 - o, w - 1 + o, h - 8 - o, { seed: seed(id, `side${sheet}`), roughness: 1, passes: 1 })}`,
        });
      }
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, 4) });
      hatch(4, 4, w - 8, h - 8);
      strokes.push({ role: "outline", d: outline(4) });
      break;
    }
    case "positive_control": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, 2) });
      hatch(7, 7, w - 14, h - 14);
      strokes.push({ role: "outline", d: outline(2) });
      // Drawn twice over: biased on purpose.
      strokes.push({ role: "second", d: outline(2, 5, "inner") });
      break;
    }
    case "tier0_band": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2) });
      hatch(4, 4, w - 8, h - 8);
      strokes.push({ role: "outline", d: outline(0) });
      // A ruler along the bottom: these audits read the token stream.
      let ticks = "";
      for (let x = 10; x <= w - 10; x += 8) ticks += `M${n(x)} ${n(h - 2)} V${n(h - (((x - 10) / 8) % 5 === 0 ? 9 : 6))} `;
      strokes.push({ role: "detail", d: ticks.trim() });
      break;
    }
    case "card": {
      const fold = 14;
      const corners: Pt[] = [{ x: 1, y: 1 }, { x: w - fold, y: 1 }, { x: w - 1, y: fold }, { x: w - 1, y: h - 1 }, { x: 1, y: h - 1 }];
      strokes.push({ role: "fill", d: polygon(corners) });
      hatch(4, 4, w - 8 - fold, h - 8);
      strokes.push({ role: "outline", d: roughPolygon(corners, id, "edge") });
      strokes.push({ role: "detail", d: `M${n(w - fold)} 1 V${n(fold)} H${n(w - 1)}` });
      if (node.ref.type === "card" && node.ref.prompts > 0) {
        const cols = Math.min(LATTICE.cols, node.ref.prompts), rows = Math.min(LATTICE.rows, node.ref.k ?? 1);
        const right = w - 12, bottom = h - 30;
        let dots = "";
        for (let row = 0; row < rows; row++) {
          for (let col = 0; col < cols; col++) dots += `${circle(right - col * LATTICE.step, bottom - row * LATTICE.step, LATTICE.radius)} `;
        }
        strokes.push({ role: "dots", d: dots.trim() });
      }
      break;
    }
    case "endpoint": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, 3) });
      hatch(4, 4, w - 8, h - 8);
      strokes.push({ role: "outline", d: outline(3) });
      const confirmatory = node.ref.type === "endpoint" && node.ref.role === "confirmatory";
      strokes.push({ role: confirmatory ? "port" : "port-open", d: circle(1, h / 2, 4.5) });
      break;
    }
    case "test": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, 3) });
      hatch(4, 4, w - 8, h - 8);
      strokes.push({ role: "outline", d: outline(3) });
      // The hexagon of the mark, holding the hypothesis' number.
      strokes.push({ role: "second", d: roughHex(22, h / 2, 14, { seed: seed(id, "hex"), roughness: 1 }) });
      break;
    }
    case "gate": {
      const corners = gatePoints(w, h);
      strokes.push({ role: "fill", d: polygon(corners) });
      hatch(16, 4, w - 32, h - 8);
      strokes.push({ role: "outline", d: roughPolygon(corners, id, "edge") });
      break;
    }
    case "family": {
      // The family is a brace beside the tests; its label only gets a line under it.
      strokes.push({ role: "outline", d: roughLine(2, h - 3, w - 2, h - 3, { seed: seed(id, "rule"), roughness: ROUGHNESS }) });
      break;
    }
    case "verdict": {
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, 14) });
      hatch(8, 4, w - 16, h - 8);
      strokes.push({ role: "outline", d: outline(14) });
      break;
    }
    default: {
      // A seeded run, an arm outside every comparison, a placeholder.
      const radius = node.kind === "ghost_stack" ? 8 : 4;
      strokes.push({ role: "fill", d: plate(1, 1, w - 2, h - 2, radius) });
      hatch(4, 4, w - 8, h - 8);
      strokes.push({ role: "outline", d: outline(radius) });
    }
  }
  return strokes;
}

export interface EdgeInk {
  /** The thread, as one path (or several pieces joined, for a thread led around what stands in its way). */
  path: string;
  /** The arrowhead at the far end, its own path so it can be drawn without the draw-in. */
  head: string;
  /** Whether the thread was led around other nodes. */
  detour: boolean;
}

const EDGE_START = 3;
const EDGE_END = 4;
const DETOUR_LEAD = 52;
const DETOUR_CLEAR = 26;

function cubicAt(from: Pt, to: Pt, t: number): Pt {
  const handle = (to.x - from.x) / 2;
  const c1 = { x: from.x + handle, y: from.y }, c2 = { x: to.x - handle, y: to.y };
  const u = 1 - t;
  return {
    x: u * u * u * from.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * to.x,
    y: u * u * u * from.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * to.y,
  };
}

const holds = (box: Box, point: Pt, margin: number) =>
  point.x > box.x - margin && point.x < box.x + box.w + margin && point.y > box.y - margin && point.y < box.y + box.h + margin;

/**
 * The thread from one node to another. It leaves the right-middle of one box and enters the left-middle of the
 * other; where other nodes stand in the way (the positive-control arm reaches its gate across the evals), it is
 * led under them.
 */
export function edgeInk(edgeId: string, from: Pt, to: Pt, others: readonly Box[]): EdgeInk {
  const a = { x: from.x + EDGE_START, y: from.y }, b = { x: to.x - EDGE_END, y: to.y };
  const options = { seed: seedFromId(edgeId), roughness: 1.1 };
  let blocked = false;
  for (let step = 1; step < 12 && !blocked; step++) {
    const point = cubicAt(a, b, step / 12);
    blocked = others.some((box) => holds(box, point, 4));
  }
  if (!blocked) return { ...roughConnector(a, b, { ...options, arrow: true }), detour: false };

  const between = others.filter((box) => box.x + box.w > a.x && box.x < b.x);
  const under = Math.max(a.y, b.y, ...between.map((box) => box.y + box.h)) + DETOUR_CLEAR;
  const down = { x: a.x + DETOUR_LEAD, y: under }, up = { x: b.x - DETOUR_LEAD, y: under };
  const last = roughConnector(up, b, { ...options, seed: seedFromId(`${edgeId}:up`), arrow: true });
  return {
    path: [
      roughConnector(a, down, { ...options, seed: seedFromId(`${edgeId}:down`) }).path,
      roughLine(down.x, down.y, up.x, up.y, { ...options, seed: seedFromId(`${edgeId}:along`) }),
      last.path,
    ].join(" "),
    head: last.head,
    detour: true,
  };
}
