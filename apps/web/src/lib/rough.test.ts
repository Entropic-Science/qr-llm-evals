import { inkLinePath } from "../components/InkLine";
import * as rough from "./rough";
import type { Pt } from "./rough";

const { roughBrace, roughConnector, roughHachure, roughHex, roughLine, roughRect } = rough;

interface Bounds { x0: number; y0: number; x1: number; y1: number }

const NUMBER = /-?\d+\.\d/g;

function points(d: string): Pt[] {
  const numbers = (d.match(NUMBER) ?? []).map(Number);
  expect(numbers.length % 2).toBe(0);
  return Array.from({ length: numbers.length / 2 }, (_, index) => ({ x: numbers[index * 2]!, y: numbers[index * 2 + 1]! }));
}

const strokes = (d: string) => (d.match(/M/g) ?? []).length;

/** Absolute M and C commands, one decimal, nothing else. */
function expectWellFormed(d: string) {
  expect(d).not.toMatch(/NaN|Infinity|undefined|-0\.0(?!\d)/);
  expect(d.replace(NUMBER, "").replace(/[MC ]/g, "")).toBe("");
  if (d) expect(d).toMatch(/^M-?\d+\.\d -?\d+\.\d( C(-?\d+\.\d ){5}-?\d+\.\d| M-?\d+\.\d -?\d+\.\d)*$/);
}

function expectWithin(d: string, box: Bounds, margin: number) {
  // Half a tenth of slack: coordinates are rounded to one decimal after the bound is met.
  const slack = margin + 0.05;
  const all = points(d);
  expect(all.length).toBeGreaterThan(0);
  expect(Math.min(...all.map((point) => point.x))).toBeGreaterThanOrEqual(box.x0 - slack);
  expect(Math.max(...all.map((point) => point.x))).toBeLessThanOrEqual(box.x1 + slack);
  expect(Math.min(...all.map((point) => point.y))).toBeGreaterThanOrEqual(box.y0 - slack);
  expect(Math.max(...all.map((point) => point.y))).toBeLessThanOrEqual(box.y1 + slack);
}

const SEEDS = Array.from({ length: 40 }, (_, index) => index * 2654435761 >>> 0);
const ROUGHNESSES = [undefined, 0.5, 3] as const;
const options = (seed: number, roughness: number | undefined) => (roughness === undefined ? { seed } : { seed, roughness });

describe("the pen shared with InkLine", () => {
  // Pinned on the tree before mulberry32 moved to lib/prng.ts: the extraction must not move a single anchor.
  it("draws inkLinePath(7, 6) exactly as before", () => {
    expect(inkLinePath(7, 6)).toBe("M0.00 -0.98 C3.33 -0.75 13.33 0.25 20.00 0.40 C26.67 0.55 33.33 -0.08 40.00 -0.07 C46.67 -0.06 53.33 0.36 60.00 0.46 C66.67 0.56 73.33 0.65 80.00 0.53 C86.67 0.41 96.67 -0.13 100.00 -0.26");
  });

  it("draws another seed and anchor count exactly as before", () => {
    expect(inkLinePath(13, 9)).toBe("M0.00 0.13 C2.08 -0.04 8.33 -0.75 12.50 -0.90 C16.67 -1.06 20.83 -1.09 25.00 -0.82 C29.17 -0.54 33.33 0.67 37.50 0.74 C41.67 0.81 45.83 -0.25 50.00 -0.42 C54.17 -0.59 58.33 -0.21 62.50 -0.27 C66.67 -0.33 70.83 -0.85 75.00 -0.75 C79.17 -0.66 83.33 0.10 87.50 0.31 C91.67 0.52 97.92 0.46 100.00 0.49");
  });
});

describe("determinism", () => {
  const draw = (lib: typeof rough, seed: number) => [
    lib.roughLine(0, 0, 220, 0, { seed }),
    lib.roughRect(0, 0, 220, 72, { seed }),
    lib.roughRect(0, 0, 220, 72, { seed, radius: 8 }),
    lib.roughHex(100, 32, 30, { seed }),
    lib.roughBrace(0, 0, 240, { seed }),
    lib.roughHachure(0, 0, 220, 72, { seed }),
    lib.roughConnector({ x: 220, y: 36 }, { x: 320, y: 140 }, { seed, arrow: true }).path,
    lib.roughConnector({ x: 220, y: 36 }, { x: 320, y: 140 }, { seed, arrow: true }).head,
  ];

  it("returns the same string for the same seed", () => {
    expect(draw(rough, 11)).toEqual(draw(rough, 11));
  });

  it("returns the same string from a module that has never drawn it, so the result does not lean on the cache", async () => {
    const first = draw(rough, 12);
    vi.resetModules();
    const fresh = await import("./rough");
    expect(fresh.roughLine).not.toBe(roughLine);
    expect(draw(fresh, 12)).toEqual(first);
  });

  it("returns a different string for a different seed", () => {
    const a = draw(rough, 21);
    const b = draw(rough, 22);
    a.forEach((path, index) => expect(path).not.toBe(b[index]));
  });

  it("keeps options apart: roughness, passes, bowing and geometry each change the stroke", () => {
    const base = roughLine(0, 0, 220, 0, { seed: 5 });
    expect(roughLine(0, 0, 220, 0, { seed: 5, roughness: 2 })).not.toBe(base);
    expect(roughLine(0, 0, 220, 0, { seed: 5, passes: 1 })).not.toBe(base);
    expect(roughLine(0, 0, 220, 0, { seed: 5, bowing: 0 })).not.toBe(base);
    expect(roughLine(0, 0, 221, 0, { seed: 5 })).not.toBe(base);
    expect(roughRect(0, 0, 220, 72, { seed: 5, radius: 8 })).not.toBe(roughRect(0, 0, 220, 72, { seed: 5 }));
  });
});

describe("bounds: every coordinate stays within the shape's box ± 2 × roughness", () => {
  for (const roughness of ROUGHNESSES) {
    const margin = 2 * (roughness ?? 1.2);
    const label = `roughness ${roughness ?? "default"}`;

    it(`roughLine, ${label}`, () => {
      for (const seed of SEEDS) {
        for (const [x1, y1, x2, y2] of [[0, 0, 220, 0], [10, 200, 10, -40], [-30, -30, 90, 55], [5, 5, 9, 7]] as const) {
          const d = roughLine(x1, y1, x2, y2, options(seed, roughness));
          expectWellFormed(d);
          expectWithin(d, { x0: Math.min(x1, x2), y0: Math.min(y1, y2), x1: Math.max(x1, x2), y1: Math.max(y1, y2) }, margin);
        }
      }
    });

    it(`roughRect, square and rounded, ${label}`, () => {
      for (const seed of SEEDS) {
        for (const [x, y, w, h] of [[0, 0, 260, 160], [0, 0, 220, 72], [40, -20, 180, 56], [0, 0, 12, 12]] as const) {
          for (const radius of [undefined, 0, 6, 14, 999]) {
            const d = roughRect(x, y, w, h, radius === undefined ? options(seed, roughness) : { ...options(seed, roughness), radius });
            expectWellFormed(d);
            expectWithin(d, { x0: x, y0: y, x1: x + w, y1: y + h }, margin);
          }
        }
      }
    });

    it(`roughHex, ${label}`, () => {
      for (const seed of SEEDS) {
        const d = roughHex(100, 32, 30, options(seed, roughness));
        expectWellFormed(d);
        expectWithin(d, { x0: 70, y0: 32 - 30 * Math.sin(Math.PI / 3), x1: 130, y1: 32 + 30 * Math.sin(Math.PI / 3) }, margin);
      }
    });

    it(`roughBrace, ${label}`, () => {
      for (const seed of SEEDS) {
        for (const [y1, y2] of [[0, 240], [20, 60], [300, 100], [0, 8]] as const) {
          const d = roughBrace(50, y1, y2, options(seed, roughness));
          expectWellFormed(d);
          expectWithin(d, { x0: 50, y0: Math.min(y1, y2), x1: 50 + Math.min(12, Math.abs(y2 - y1) / 4), y1: Math.max(y1, y2) }, margin);
        }
      }
    });

    it(`roughHachure, ${label}`, () => {
      for (const seed of SEEDS) {
        const d = roughHachure(4, 4, 212, 64, options(seed, roughness));
        expectWellFormed(d);
        expectWithin(d, { x0: 4, y0: 4, x1: 216, y1: 68 }, margin);
      }
    });

    it(`roughConnector, ${label}`, () => {
      for (const seed of SEEDS) {
        for (const [from, to] of [[{ x: 220, y: 36 }, { x: 320, y: 140 }], [{ x: 220, y: 300 }, { x: 320, y: 300 }], [{ x: 500, y: 80 }, { x: 320, y: 10 }], [{ x: 0, y: 0 }, { x: 0, y: 90 }]] as const) {
          const { path, head } = roughConnector(from, to, { ...options(seed, roughness), arrow: true });
          expectWellFormed(path);
          expectWellFormed(head);
          expectWithin(path, { x0: Math.min(from.x, to.x), y0: Math.min(from.y, to.y), x1: Math.max(from.x, to.x), y1: Math.max(from.y, to.y) }, margin);
          expectWithin(head, { x0: to.x - 8, y0: to.y - 8, x1: to.x + 8, y1: to.y + 8 }, margin);
        }
      }
    });
  }
});

describe("degenerate geometry never produces NaN", () => {
  it("zero-length, zero-size and inverted inputs", () => {
    const seed = 3;
    const drawn = [
      roughLine(5, 5, 5, 5, { seed }),
      roughRect(0, 0, 0, 0, { seed }),
      roughRect(0, 0, 0, 0, { seed, radius: 4 }),
      roughRect(0, 0, 40, 0, { seed, radius: 4 }),
      roughHex(0, 0, 0, { seed }),
      roughBrace(0, 10, 10, { seed }),
      roughConnector({ x: 1, y: 1 }, { x: 1, y: 1 }, { seed, arrow: true }).path,
      roughConnector({ x: 1, y: 1 }, { x: 1, y: 1 }, { seed, arrow: true }).head,
      roughLine(0, 0, 100, 0, { seed, roughness: -4, bowing: -1 }),
    ];
    for (const d of drawn) {
      expect(d).not.toBe("");
      expectWellFormed(d);
    }
    expect(roughHachure(0, 0, 0, 30, { seed })).toBe("");
    expect(roughHachure(0, 0, 30, -1, { seed })).toBe("");
    expectWellFormed(roughHachure(0, 0, 30, 30, { seed, gap: 0 }));
  });
});

describe("shape", () => {
  it("with no roughness a line is the line and a box is the box", () => {
    expect(roughLine(0, 0, 100, 0, { seed: 1, roughness: 0, passes: 1 })).toMatch(/^M0\.0 0\.0 .* 100\.0 0\.0$/);
    for (const point of points(roughLine(0, 0, 100, 0, { seed: 1, roughness: 0 }))) expect(point.y).toBe(0);
    for (const point of points(roughRect(10, 20, 100, 50, { seed: 1, roughness: 0 }))) {
      expect(point.x === 10 || point.x === 110 || point.y === 20 || point.y === 70).toBe(true);
    }
  });

  it("draws each stroke once per pass", () => {
    expect(strokes(roughLine(0, 0, 100, 0, { seed: 1 }))).toBe(2);
    expect(strokes(roughLine(0, 0, 100, 0, { seed: 1, passes: 1 }))).toBe(1);
    expect(strokes(roughRect(0, 0, 100, 50, { seed: 1 }))).toBe(8);
    expect(strokes(roughRect(0, 0, 100, 50, { seed: 1, passes: 1 }))).toBe(4);
    expect(strokes(roughHex(0, 0, 30, { seed: 1 }))).toBe(12);
    expect(strokes(roughBrace(0, 0, 200, { seed: 1 }))).toBe(2);
    expect(strokes(roughConnector({ x: 0, y: 0 }, { x: 100, y: 40 }, { seed: 1 }).path)).toBe(2);
  });

  it("rounds a box in one continuous stroke per pass that ends where it began", () => {
    const d = roughRect(0, 0, 220, 72, { seed: 9, radius: 8, passes: 1 });
    expect(strokes(d)).toBe(1);
    const all = points(d);
    const first = all[0]!, last = all[all.length - 1]!;
    expect(Math.abs(first.x - 8)).toBeLessThanOrEqual(1.3);
    expect(Math.abs(first.y)).toBeLessThanOrEqual(1.3);
    expect(Math.hypot(last.x - first.x, last.y - first.y)).toBeLessThanOrEqual(2.6);
    // No anchor sits in a corner the radius cut away.
    for (const point of all) expect(point.x + point.y).toBeGreaterThan(8 * (1 - Math.SQRT1_2) - 2.4);
  });

  it("clamps a radius larger than the box to half its short side", () => {
    expect(roughRect(0, 0, 100, 20, { seed: 4, radius: 999 })).toBe(roughRect(0, 0, 100, 20, { seed: 4, radius: 10 }));
  });

  it("points a hexagon's vertices left and right", () => {
    const xs = points(roughHex(100, 50, 30, { seed: 2, roughness: 0 })).map((point) => point.x);
    const ys = points(roughHex(100, 50, 30, { seed: 2, roughness: 0 })).map((point) => point.y);
    expect(Math.min(...xs)).toBe(70);
    expect(Math.max(...xs)).toBe(130);
    expect(Math.max(...ys)).toBeCloseTo(50 + 30 * Math.sin(Math.PI / 3), 1);
  });

  it("stands a brace on x, tip to the right, from y1 to y2", () => {
    const all = points(roughBrace(50, 0, 240, { seed: 6, roughness: 0, passes: 1 }));
    expect(all[0]).toEqual({ x: 50, y: 0 });
    expect(all[all.length - 1]).toEqual({ x: 50, y: 240 });
    expect(Math.max(...all.map((point) => point.x))).toBe(62);
    expect(all.find((point) => point.x === 62)).toEqual({ x: 62, y: 120 });
    expect(Math.min(...all.map((point) => point.x))).toBe(50);
  });

  it("spaces hachure by the gap and draws each line once whatever the passes", () => {
    expect(strokes(roughHachure(0, 0, 60, 30, { seed: 1 }))).toBe(14);
    expect(strokes(roughHachure(0, 0, 60, 30, { seed: 1, gap: 10 }))).toBe(8);
    expect(strokes(roughHachure(0, 0, 60, 30, { seed: 1, passes: 2 }))).toBe(14);
    // With no roughness every line runs at 45 degrees between two edges of the box.
    const all = points(roughHachure(0, 0, 60, 30, { seed: 1, gap: 10, roughness: 0 }));
    for (const point of all) expect(Math.round(point.x + point.y) % 10).toBe(0);
  });

  it("gives a connector a head only when asked, and never lets the head change the thread", () => {
    const from = { x: 220, y: 36 }, to = { x: 320, y: 140 };
    const plain = roughConnector(from, to, { seed: 8 });
    const arrowed = roughConnector(from, to, { seed: 8, arrow: true });
    expect(plain.head).toBe("");
    expect(roughConnector(from, to, { seed: 8, arrow: false }).head).toBe("");
    expect(arrowed.path).toBe(plain.path);
    expect(strokes(arrowed.head)).toBe(2);
  });

  it("leaves and arrives horizontally, with the head's barbs behind the tip", () => {
    const from = { x: 220, y: 36 }, to = { x: 320, y: 140 };
    const exact = roughConnector(from, to, { seed: 8, roughness: 0, passes: 1, arrow: true });
    expect(exact.path).toBe("M220.0 36.0 C270.0 36.0 270.0 140.0 320.0 140.0");
    const barbs = points(exact.head);
    expect(barbs.filter((point) => point.x === 320 && point.y === 140)).toHaveLength(2);
    for (const point of barbs) expect(point.x).toBeLessThanOrEqual(320);
    expect(Math.min(...barbs.map((point) => point.x))).toBeCloseTo(320 - 8 * Math.cos(0.5), 1);
    // Right to left, the barbs sit to the right of the tip; straight down, above it.
    for (const point of points(roughConnector(to, from, { seed: 8, roughness: 0, arrow: true }).head)) expect(point.x).toBeGreaterThanOrEqual(220);
    for (const point of points(roughConnector({ x: 0, y: 0 }, { x: 0, y: 90 }, { seed: 8, roughness: 0, arrow: true }).head)) expect(point.y).toBeLessThanOrEqual(90);
  });
});
