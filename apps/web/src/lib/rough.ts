import { mulberry32 } from "./prng";

// Hand-drawn SVG path data for the study map: lines, boxes, hexagons, braces, hachure and connectors that look
// pencilled but are fully determined by their seed. Strokes are joined as Catmull-Rom curves turned into cubics,
// the same construction as `inkLinePath`, so the map and the header underline share one pen.
//
// Every function returns absolute `M`/`C` path data with coordinates to one decimal. No anchor moves further than
// `roughness` from the ideal geometry on either axis (for `bowing` up to 1), and no control point further than
// 4/3 of that, so a caller can size an SVG as the shape's box plus 2 × roughness and nothing is clipped.

export interface Pt { x: number; y: number }

export interface RoughOptions {
  seed: number;
  /** How far the pen strays from the ideal geometry, in the caller's units. Default 1.2. */
  roughness?: number;
  /** How many times each stroke is drawn; the second pass is what makes a line look sketched. Default 2. */
  passes?: 1 | 2;
  /** How much a straight stroke arcs between its ends, as a multiple of the default. Default 1. */
  bowing?: number;
}

interface Pen { random: () => number; roughness: number; passes: 1 | 2; bowing: number }

const ARROW_LENGTH = 8;
const ARROW_SPREAD = 0.5; // radians either side of the arriving stroke
const BRACE_DEPTH = 12;
const HACHURE_GAP = 6;
const KAPPA = 0.5523; // cubic handle length that makes a quarter circle
// A stroke shorter than this wobbles in proportion, so an arrowhead or a corner hachure is not all jitter.
const FULL_WOBBLE_LENGTH = 20;

// The same call returns the same string without redoing the arithmetic. This is a cost saving only: the functions
// are pure, so a miss and a hit are indistinguishable. Sizes come from the layout's constant table, so the key
// space is small; the cap is a backstop, not a tuning knob.
const CACHE_LIMIT = 2000;
const cache = new Map<string, unknown>();

function memo<T>(kind: string, o: RoughOptions, rest: readonly (number | boolean | undefined)[], make: (pen: Pen) => T): T {
  const pen: Pen = { random: mulberry32(o.seed), roughness: Math.max(0, o.roughness ?? 1.2), passes: o.passes ?? 2, bowing: Math.max(0, o.bowing ?? 1) };
  const key = `${kind}:${o.seed}:${pen.roughness}:${pen.passes}:${pen.bowing}:${rest.join(",")}`;
  if (cache.has(key)) return cache.get(key) as T;
  if (cache.size >= CACHE_LIMIT) cache.clear();
  const value = make(pen);
  cache.set(key, value);
  return value;
}

function f(value: number): string {
  const text = value.toFixed(1);
  return text === "-0.0" ? "0.0" : text;
}

const spread = (pen: Pen) => pen.random() * 2 - 1;

function reachFor(pen: Pen, length: number): number {
  return pen.roughness * Math.min(1, length / FULL_WOBBLE_LENGTH);
}

function nudge(point: Pt, pen: Pen, amount: number): Pt {
  return { x: point.x + spread(pen) * amount, y: point.y + spread(pen) * amount };
}

/** Two anchors along a→b, pushed off the line by one shared bow (the arc of a hand) plus a little of their own. */
function mids(a: Pt, b: Pt, pen: Pen, reach: number): Pt[] {
  const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
  const nx = length ? -dy / length : 0, ny = length ? dx / length : 0;
  const bow = spread(pen) * reach * 0.75 * pen.bowing;
  return [0.3, 0.6].map((from) => {
    const t = from + pen.random() * 0.1;
    return nudge({ x: a.x + dx * t + nx * bow, y: a.y + dy * t + ny * bow }, pen, reach * 0.25);
  });
}

/** Cubic segments through `points[1..]`, for a pen already standing on `points[0]`. */
function curveThrough(points: readonly Pt[]): string {
  const at = (index: number) => points[Math.min(points.length - 1, Math.max(0, index))]!;
  let d = "";
  for (let i = 0; i < points.length - 1; i++) {
    const [p0, p1, p2, p3] = [at(i - 1), at(i), at(i + 1), at(i + 2)];
    d += ` C${f(p1.x + (p2.x - p0.x) / 6)} ${f(p1.y + (p2.y - p0.y) / 6)} ${f(p2.x - (p3.x - p1.x) / 6)} ${f(p2.y - (p3.y - p1.y) / 6)} ${f(p2.x)} ${f(p2.y)}`;
  }
  return d;
}

const moveTo = (point: Pt) => `M${f(point.x)} ${f(point.y)}`;
const cubicTo = (c1: Pt, c2: Pt, end: Pt) => ` C${f(c1.x)} ${f(c1.y)} ${f(c2.x)} ${f(c2.y)} ${f(end.x)} ${f(end.y)}`;

/** One pen stroke from a to b: both ends land near, not on, their targets. */
function stroke(a: Pt, b: Pt, pen: Pen): string {
  const reach = reachFor(pen, Math.hypot(b.x - a.x, b.y - a.y));
  const start = nudge(a, pen, reach * 0.5);
  const end = nudge(b, pen, reach * 0.5);
  return moveTo(start) + curveThrough([start, ...mids(a, b, pen, reach), end]);
}

function eachPass(pen: Pen, draw: () => string): string {
  const parts: string[] = [];
  for (let pass = 0; pass < pen.passes; pass++) parts.push(draw());
  return parts.filter(Boolean).join(" ");
}

export function roughLine(x1: number, y1: number, x2: number, y2: number, o: RoughOptions): string {
  return memo("line", o, [x1, y1, x2, y2], (pen) => eachPass(pen, () => stroke({ x: x1, y: y1 }, { x: x2, y: y2 }, pen)));
}

/**
 * A box. Square corners are four separate strokes that cross slightly, as a ruled box does; with a `radius` the
 * pen goes round in one stroke and stops near where it started.
 */
export function roughRect(x: number, y: number, w: number, h: number, o: RoughOptions & { radius?: number }): string {
  const radius = Math.max(0, Math.min(o.radius ?? 0, w / 2, h / 2));
  return memo("rect", o, [x, y, w, h, radius], (pen) => eachPass(pen, () => {
    const sides: [Pt, Pt][] = [
      [{ x: x + radius, y }, { x: x + w - radius, y }],
      [{ x: x + w, y: y + radius }, { x: x + w, y: y + h - radius }],
      [{ x: x + w - radius, y: y + h }, { x: x + radius, y: y + h }],
      [{ x, y: y + h - radius }, { x, y: y + radius }],
    ];
    if (radius === 0) return sides.map(([a, b]) => stroke(a, b, pen)).join(" ");
    const corners: Pt[] = [{ x: x + w, y }, { x: x + w, y: y + h }, { x, y: y + h }, { x, y }];
    const jitter = reachFor(pen, Math.min(w, h)) * 0.5;
    let current = nudge(sides[0]![0], pen, jitter);
    let d = moveTo(current);
    sides.forEach(([a, b], index) => {
      const end = nudge(b, pen, jitter);
      d += curveThrough([current, ...mids(a, b, pen, reachFor(pen, Math.hypot(b.x - a.x, b.y - a.y))), end]);
      const corner = corners[index]!;
      const next = sides[(index + 1) % 4]![0];
      const landing = nudge(next, pen, jitter);
      d += cubicTo(
        { x: end.x + (corner.x - b.x) * KAPPA, y: end.y + (corner.y - b.y) * KAPPA },
        { x: landing.x + (corner.x - next.x) * KAPPA, y: landing.y + (corner.y - next.y) * KAPPA },
        landing,
      );
      current = landing;
    });
    return d;
  }));
}

/** A regular hexagon with a vertex at the left and right middle, where the map's edges enter and leave. */
export function roughHex(cx: number, cy: number, r: number, o: RoughOptions): string {
  return memo("hex", o, [cx, cy, r], (pen) => eachPass(pen, () => {
    const vertex = (index: number): Pt => ({ x: cx + r * Math.cos((index * Math.PI) / 3), y: cy + r * Math.sin((index * Math.PI) / 3) });
    return Array.from({ length: 6 }, (_, index) => stroke(vertex(index), vertex(index + 1), pen)).join(" ");
  }));
}

/**
 * A curly brace standing at `x` from `y1` to `y2`, opening to the left and pointing right: the ends sit on `x`,
 * the point reaches at most 12 units to its right.
 */
export function roughBrace(x: number, y1: number, y2: number, o: RoughOptions): string {
  return memo("brace", o, [x, y1, y2], (pen) => eachPass(pen, () => {
    const span = Math.abs(y2 - y1);
    const depth = Math.min(BRACE_DEPTH, span / 4);
    const half = depth / 2;
    const curl = (y2 < y1 ? -1 : 1) * depth;
    const middle = (y1 + y2) / 2;
    const reach = reachFor(pen, span);
    const ideal: Pt[] = [
      { x, y: y1 }, { x: x + half, y: y1 + curl }, { x: x + half, y: middle - curl }, { x: x + depth, y: middle },
      { x: x + half, y: middle + curl }, { x: x + half, y: y2 - curl }, { x, y: y2 },
    ];
    const [top, upperStart, upperEnd, tip, lowerStart, lowerEnd, bottom] = ideal.map((point) => nudge(point, pen, reach * 0.5)) as [Pt, Pt, Pt, Pt, Pt, Pt, Pt];
    const straight = (from: Pt, to: Pt, a: Pt, b: Pt) => curveThrough([from, ...mids(a, b, pen, reachFor(pen, Math.hypot(b.x - a.x, b.y - a.y))), to]);
    return moveTo(top)
      + cubicTo({ x: top.x + half * 0.9, y: top.y }, { x: upperStart.x, y: upperStart.y - curl * 0.7 }, upperStart)
      + straight(upperStart, upperEnd, ideal[1]!, ideal[2]!)
      + cubicTo({ x: upperEnd.x, y: upperEnd.y + curl * 0.7 }, { x: tip.x - half * 0.9, y: tip.y }, tip)
      + cubicTo({ x: tip.x - half * 0.9, y: tip.y }, { x: lowerStart.x, y: lowerStart.y - curl * 0.7 }, lowerStart)
      + straight(lowerStart, lowerEnd, ideal[4]!, ideal[5]!)
      + cubicTo({ x: lowerEnd.x, y: lowerEnd.y + curl * 0.7 }, { x: bottom.x + half * 0.9, y: bottom.y }, bottom);
  }));
}

/**
 * Diagonal fill lines across a box, `gap` apart (default 6). Each line is drawn once whatever `passes` says: a
 * doubled hachure reads as a solid fill, which is not what a soft tint is for.
 */
export function roughHachure(x: number, y: number, w: number, h: number, o: RoughOptions & { gap?: number }): string {
  const gap = Math.max(1, o.gap ?? HACHURE_GAP);
  return memo("hachure", o, [x, y, w, h, gap], (pen) => {
    if (!(w > 0) || !(h > 0)) return "";
    const lines: string[] = [];
    for (let c = gap; c < w + h; c += gap) {
      const upper: Pt = c <= w ? { x: x + c, y } : { x: x + w, y: y + c - w };
      const lower: Pt = c <= h ? { x, y: y + c } : { x: x + c - h, y: y + h };
      lines.push(stroke(upper, lower, pen));
    }
    return lines.join(" ");
  });
}

/**
 * The thread between two boxes: it leaves `from` and arrives at `to` horizontally, as the layout's edges do. `head`
 * is the arrowhead at `to` as its own path (empty without `arrow`), so it can be drawn without the draw-in dash.
 */
export function roughConnector(from: Pt, to: Pt, o: RoughOptions & { arrow?: boolean }): { path: string; head: string } {
  const arrow = o.arrow === true;
  return memo("connector", o, [from.x, from.y, to.x, to.y, arrow], (pen) => {
    const handle = (to.x - from.x) / 2;
    const reach = reachFor(pen, Math.hypot(to.x - from.x, to.y - from.y));
    const path = eachPass(pen, () => {
      const start = nudge(from, pen, reach * 0.5);
      const end = nudge(to, pen, reach * 0.5);
      const sway = reach * 0.75 * pen.bowing;
      return moveTo(start) + cubicTo(nudge({ x: from.x + handle, y: from.y }, pen, sway), nudge({ x: to.x - handle, y: to.y }, pen, sway), end);
    });
    if (!arrow) return Object.freeze({ path, head: "" });
    // The stroke arrives along its last handle; a vertical connector has none, so fall back to the chord.
    let dx = handle, dy = 0;
    if (dx === 0) dy = to.y - from.y;
    const length = Math.hypot(dx, dy);
    if (length === 0) { dx = 1; dy = 0; } else { dx /= length; dy /= length; }
    const single: Pen = { ...pen, passes: 1 };
    const barb = (angle: number) => {
      const cos = Math.cos(angle), sin = Math.sin(angle);
      const back: Pt = { x: to.x - (dx * cos - dy * sin) * ARROW_LENGTH, y: to.y - (dx * sin + dy * cos) * ARROW_LENGTH };
      return stroke(back, to, single);
    };
    return Object.freeze({ path, head: `${barb(ARROW_SPREAD)} ${barb(-ARROW_SPREAD)}` });
  });
}
