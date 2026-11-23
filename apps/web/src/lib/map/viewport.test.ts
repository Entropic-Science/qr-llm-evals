import {
  DEFAULT_SIZE, DETAIL_ABOVE_K, IDENTITY_VIEW, MAX_K, MIN_K, STORY_BELOW_K, STORY_FIT_K, clampK, ensureVisible, fit, fitWhole, panBy, zoomAt, zoomTier,
  type Box, type Size, type View,
} from "./viewport";

const SIZE: Size = { w: 1000, h: 600 };
// The world of a one-lane Study 1 map: seven stations, the last ending at 1740 + 200.
const WORLD: Box = { x: 0, y: 0, w: 1940, h: 900 };

/** Where a world point lands on screen under a view. */
const project = (view: View, x: number, y: number) => ({ x: x * view.k + view.x, y: y * view.k + view.y });
const onScreen = (view: View, box: Box) => {
  const a = project(view, box.x, box.y), b = project(view, box.x + box.w, box.y + box.h);
  return { left: a.x, top: a.y, right: b.x, bottom: b.y };
};

describe("clampK", () => {
  it("holds k to [0.35, 2.5]", () => {
    expect(MIN_K).toBe(0.35);
    expect(MAX_K).toBe(2.5);
    expect(clampK(0.01)).toBe(0.35);
    expect(clampK(0.35)).toBe(0.35);
    expect(clampK(1)).toBe(1);
    expect(clampK(2.5)).toBe(2.5);
    expect(clampK(40)).toBe(2.5);
    expect(clampK(-3)).toBe(0.35);
  });

  it("falls back to natural size for a k that is not a number", () => {
    expect(clampK(Number.NaN)).toBe(1);
    expect(clampK(Number.POSITIVE_INFINITY)).toBe(1);
  });
});

describe("panBy", () => {
  it("moves the origin and leaves the zoom alone", () => {
    expect(panBy({ x: 10, y: -20, k: 1.5 }, 5, 7)).toEqual({ x: 15, y: -13, k: 1.5 });
  });

  it("does not mutate, and returns the same view for no movement", () => {
    const view: View = Object.freeze({ x: 1, y: 2, k: 1 });
    expect(panBy(view, 0, 0)).toBe(view);
    expect(panBy(view, 3, 0)).not.toBe(view);
    expect(view).toEqual({ x: 1, y: 2, k: 1 });
  });

  it("composes: two pans equal their sum", () => {
    expect(panBy(panBy(IDENTITY_VIEW, 30, -4), -12, 9)).toEqual(panBy(IDENTITY_VIEW, 18, 5));
  });
});

describe("zoomAt", () => {
  it("keeps the world point under the cursor where it is", () => {
    const view: View = { x: 120, y: -80, k: 0.8 };
    const cursor = { x: 400, y: 250 };
    const world = { x: (cursor.x - view.x) / view.k, y: (cursor.y - view.y) / view.k };
    for (const factor of [1.1, 0.9, 2, 0.5]) {
      const next = zoomAt(view, cursor, factor);
      expect(next.k).toBeCloseTo(0.8 * factor, 12);
      expect(project(next, world.x, world.y).x).toBeCloseTo(cursor.x, 9);
      expect(project(next, world.x, world.y).y).toBeCloseTo(cursor.y, 9);
    }
  });

  it("zooming at the origin of an untranslated view only scales", () => {
    expect(zoomAt(IDENTITY_VIEW, { x: 0, y: 0 }, 2)).toEqual({ x: 0, y: 0, k: 2 });
  });

  it("clamps at both limits and still pins the cursor", () => {
    const view: View = { x: 50, y: 50, k: 2 };
    const cursor = { x: 300, y: 200 };
    const world = { x: (cursor.x - view.x) / view.k, y: (cursor.y - view.y) / view.k };
    const zoomedIn = zoomAt(view, cursor, 10);
    expect(zoomedIn.k).toBe(2.5);
    expect(project(zoomedIn, world.x, world.y).x).toBeCloseTo(cursor.x, 9);
    const zoomedOut = zoomAt(view, cursor, 0.001);
    expect(zoomedOut.k).toBe(0.35);
    expect(project(zoomedOut, world.x, world.y).y).toBeCloseTo(cursor.y, 9);
  });

  it("returns the same view at a limit, so a held wheel does not drift", () => {
    const atMax: View = { x: 7, y: 9, k: 2.5 };
    expect(zoomAt(atMax, { x: 300, y: 200 }, 1.2)).toBe(atMax);
    const atMin: View = { x: 7, y: 9, k: 0.35 };
    expect(zoomAt(atMin, { x: 300, y: 200 }, 0.8)).toBe(atMin);
    expect(zoomAt(atMin, { x: 300, y: 200 }, 1)).toBe(atMin);
  });

  it("in then out by the same factor returns to the start", () => {
    const view: View = { x: -340, y: 12, k: 1 };
    const back = zoomAt(zoomAt(view, { x: 500, y: 300 }, 1.25), { x: 500, y: 300 }, 1 / 1.25);
    expect(back.k).toBeCloseTo(view.k, 12);
    expect(back.x).toBeCloseTo(view.x, 9);
    expect(back.y).toBeCloseTo(view.y, 9);
  });
});

describe("fitWhole", () => {
  const VIEWPORT: Size = { w: 1024, h: 560 };
  // What the story zoom draws of the Study 1 map, and everything at full density with two groups unfolded.
  const STORY: Box = { x: 0, y: -122, w: 1984, h: 730 };
  const UNFOLDED: Box = { x: 0, y: -122, w: 1984, h: 2298 };

  it("fits everything where that lands at a zoom that draws everything", () => {
    const small: Box = { x: 0, y: 0, w: 900, h: 400 };
    expect(fitWhole(small, { x: 0, y: 0, w: 500, h: 200 }, VIEWPORT, 32)).toEqual(fit(small, VIEWPORT, 32));
    expect(zoomTier(fitWhole(small, small, VIEWPORT, 32).k)).toBe("design");
  });

  it("fits what the story draws where everything would only fit at the story zoom", () => {
    const view = fitWhole(UNFOLDED, STORY, VIEWPORT, 32);
    expect(zoomTier(fit(UNFOLDED, VIEWPORT, 32).k)).toBe("story");
    expect(view).toEqual(fit(STORY, VIEWPORT, 32, STORY_FIT_K));
    expect(view.k).toBeCloseTo((1024 - 64) / 1984, 12);
    // All of the story is on screen: the headings at the top and the last node at the bottom.
    const drawn = onScreen(view, STORY);
    expect(drawn.left).toBeGreaterThanOrEqual(32 - 1e-9);
    expect(drawn.top).toBeGreaterThanOrEqual(32 - 1e-9);
    expect(drawn.right).toBeLessThanOrEqual(1024 - 32 + 1e-9);
    expect(drawn.bottom).toBeLessThanOrEqual(560 - 32 + 1e-9);
  });

  it("stays inside the story tier, so that what it was fitted to is what is drawn", () => {
    // The story alone would fit at a zoom that draws the unfolded parts too, which do not fit.
    const story: Box = { x: 0, y: 0, w: 600, h: 300 };
    const view = fitWhole({ x: 0, y: 0, w: 600, h: 4000 }, story, VIEWPORT, 32);
    expect(view.k).toBe(STORY_FIT_K);
    expect(STORY_FIT_K).toBeLessThan(STORY_BELOW_K);
    expect(zoomTier(view.k)).toBe("story");
  });
});

describe("fit", () => {
  it("shows the whole world, centred, with the padding clear", () => {
    const view = fit(WORLD, SIZE);
    const { left, top, right, bottom } = onScreen(view, WORLD);
    expect(view.k).toBeCloseTo((1000 - 96) / 1940, 12);
    expect(left).toBeCloseTo(48, 9);
    expect(right).toBeCloseTo(1000 - 48, 9);
    expect(top).toBeCloseTo(600 - bottom, 9);
    expect(top).toBeGreaterThanOrEqual(48);
  });

  it("is limited by the tighter axis", () => {
    const tall: Box = { x: 0, y: 0, w: 400, h: 1000 };
    const view = fit(tall, SIZE);
    expect(view.k).toBeCloseTo((600 - 96) / 1000, 12);
    expect(onScreen(view, tall).top).toBeCloseTo(48, 9);
    expect(onScreen(view, tall).left).toBeCloseTo(1000 - onScreen(view, tall).right, 9);
  });

  it("honours a bounds origin that is not zero", () => {
    const offset: Box = { x: -300, y: 150, w: 1940, h: 900 };
    const { left, right } = onScreen(fit(offset, SIZE), offset);
    expect(left).toBeCloseTo(48, 9);
    expect(right).toBeCloseTo(952, 9);
  });

  it("takes the padding it is given", () => {
    expect(onScreen(fit(WORLD, SIZE, 100), WORLD).left).toBeCloseTo(100, 9);
    expect(onScreen(fit(WORLD, SIZE, 0), WORLD).left).toBeCloseTo(0, 9);
  });

  it("never magnifies a small graph past natural size, and centres it", () => {
    const small: Box = { x: 0, y: 0, w: 260, h: 160 };
    const view = fit(small, SIZE);
    expect(view).toEqual({ x: 370, y: 220, k: 1 });
    expect(zoomTier(view.k)).toBe("design");
  });

  // This used to expect the vast world centred, which put the middle of it on screen and its start off screen.
  it("stops at the minimum zoom when the world cannot fit, and shows it from its left edge", () => {
    const vast: Box = { x: 0, y: 0, w: 20000, h: 400 };
    const view = fit(vast, SIZE);
    expect(view.k).toBe(0.35);
    expect(onScreen(view, vast).left).toBeCloseTo(48, 9);
    // The axis that does fit is still centred.
    expect(onScreen(view, vast).top).toBeCloseTo(600 - onScreen(view, vast).bottom, 9);
  });

  it("shows a world too tall for the minimum zoom from its top, where the headings are", () => {
    // The Study 1 map with every group unfolded, in the viewport of a 1440-wide window.
    const tall: Box = { x: 0, y: -122, w: 1984, h: 5290 };
    const view = fit(tall, { w: 1024, h: 560 }, 32);
    expect(view.k).toBe(0.35);
    expect(onScreen(view, tall).top).toBeCloseTo(32, 9);
    expect(onScreen(view, tall).left).toBeCloseTo(1024 - onScreen(view, tall).right, 9);
    // The question node (y 0 to 160) is on screen, which it was not while the world was centred.
    const question = onScreen(view, { x: 0, y: 0, w: 260, h: 160 });
    expect(question.top).toBeGreaterThanOrEqual(32);
    expect(question.bottom).toBeLessThanOrEqual(560);
  });

  it("takes a largest zoom other than natural size", () => {
    const small: Box = { x: 0, y: 0, w: 260, h: 160 };
    expect(fit(small, SIZE, 48, 0.5).k).toBe(0.5);
    expect(fit(WORLD, SIZE, 48, 0.4).k).toBe(0.4);
  });

  it("uses 1200 × 720 when the viewport is unmeasured", () => {
    expect(DEFAULT_SIZE).toEqual({ w: 1200, h: 720 });
    const expected = fit(WORLD, DEFAULT_SIZE);
    expect(fit(WORLD)).toEqual(expected);
    expect(fit(WORLD, null)).toEqual(expected);
    expect(fit(WORLD, { w: 0, h: 0 })).toEqual(expected);
    expect(fit(WORLD, { w: 800, h: 0 })).toEqual(expected);
    // The default lands Study 1's map in the design tier.
    expect(zoomTier(expected.k)).toBe("design");
  });

  it("survives empty bounds", () => {
    const view = fit({ x: 40, y: 40, w: 0, h: 0 }, SIZE);
    expect(view.k).toBe(1);
    expect(project(view, 40, 40)).toEqual({ x: 500, y: 300 });
  });
});

describe("ensureVisible", () => {
  const node: Box = { x: 960, y: 300, w: 260, h: 96 };

  it("returns the same view when the box is already on screen at a sufficient zoom", () => {
    const view: View = { x: -700, y: -100, k: 1 };
    expect(ensureVisible(view, node, SIZE, 1)).toBe(view);
    expect(ensureVisible(view, node, SIZE)).toBe(view);
  });

  it("pans the least distance that brings an off-screen box in, with a margin", () => {
    const view: View = { x: 0, y: 0, k: 1 };
    const next = ensureVisible(view, node, SIZE, 1);
    expect(next.k).toBe(1);
    // The box was past the right edge only: it comes in to the right margin and does not move vertically.
    expect(onScreen(next, node).right).toBe(1000 - 24);
    expect(next.y).toBe(0);
  });

  it("pans from each side", () => {
    const fromLeft = ensureVisible({ x: -1500, y: 0, k: 1 }, node, SIZE, 1);
    expect(onScreen(fromLeft, node).left).toBe(24);
    const fromAbove = ensureVisible({ x: -700, y: -900, k: 1 }, node, SIZE, 1);
    expect(onScreen(fromAbove, node).top).toBe(24);
    expect(fromAbove.x).toBe(-700);
    const fromBelow = ensureVisible({ x: -700, y: 500, k: 1 }, node, SIZE, 1);
    expect(onScreen(fromBelow, node).bottom).toBe(600 - 24);
  });

  it("zooms in to minK about the box when the view is further out, then shows the whole box", () => {
    const start = fit(WORLD, SIZE);
    expect(start.k).toBeLessThan(1);
    const next = ensureVisible(start, node, SIZE, 1);
    expect(next.k).toBe(1);
    const { left, top, right, bottom } = onScreen(next, node);
    expect(left).toBeGreaterThanOrEqual(24);
    expect(top).toBeGreaterThanOrEqual(24);
    expect(right).toBeLessThanOrEqual(1000 - 24);
    expect(bottom).toBeLessThanOrEqual(600 - 24);
  });

  it("keeps the box's centre still when zooming in is all that is needed", () => {
    const view: View = { x: 100, y: 100, k: 0.5 };
    const centre = project(view, node.x + node.w / 2, node.y + node.h / 2);
    const next = ensureVisible(view, node, SIZE, 1);
    expect(next.k).toBe(1);
    expect(project(next, node.x + node.w / 2, node.y + node.h / 2).x).toBeCloseTo(centre.x, 9);
    expect(project(next, node.x + node.w / 2, node.y + node.h / 2).y).toBeCloseTo(centre.y, 9);
  });

  it("never zooms out: a view closer than minK keeps its zoom", () => {
    const next = ensureVisible({ x: 0, y: 0, k: 2 }, node, SIZE, 1);
    expect(next.k).toBe(2);
    expect(onScreen(next, node).right).toBeLessThanOrEqual(1000 - 24);
    expect(onScreen(next, node).left).toBeGreaterThanOrEqual(24);
  });

  it("clamps minK to the zoom limits", () => {
    expect(ensureVisible({ x: 0, y: 0, k: 1 }, node, SIZE, 9).k).toBe(2.5);
    expect(ensureVisible({ x: -700, y: -100, k: 1 }, node, SIZE, 0).k).toBe(1);
  });

  it("shows a box larger than the viewport from its top-left corner", () => {
    const sheet: Box = { x: 200, y: 100, w: 3000, h: 2000 };
    const next = ensureVisible({ x: -900, y: -900, k: 1 }, sheet, SIZE, 1);
    expect(onScreen(next, sheet).left).toBe(0);
    expect(onScreen(next, sheet).top).toBe(0);
  });

  it("shrinks the margin for a box that only just fits", () => {
    const snug: Box = { x: 0, y: 0, w: 990, h: 100 };
    const next = ensureVisible({ x: 400, y: 0, k: 1 }, snug, SIZE, 1);
    expect(onScreen(next, snug).left).toBe(5);
    expect(onScreen(next, snug).right).toBe(995);
  });

  it("is idempotent", () => {
    const once = ensureVisible({ x: 0, y: 0, k: 0.4 }, node, SIZE, 1);
    expect(ensureVisible(once, node, SIZE, 1)).toBe(once);
  });

  it("uses 1200 × 720 when the viewport is unmeasured", () => {
    const view: View = { x: 0, y: 0, k: 1 };
    expect(ensureVisible(view, node, undefined, 1)).toEqual(ensureVisible(view, node, DEFAULT_SIZE, 1));
    expect(ensureVisible(view, node, { w: 0, h: 0 }, 1)).toEqual(ensureVisible(view, node, DEFAULT_SIZE, 1));
    // 960 + 260 = 1220 overflows 1200 by 20, plus the margin.
    expect(ensureVisible(view, node, null, 1).x).toBe(-44);
  });
});

describe("zoomTier", () => {
  it("is story below 0.55, detail above 1.35 and design between, limits included", () => {
    expect(STORY_BELOW_K).toBe(0.55);
    expect(DETAIL_ABOVE_K).toBe(1.35);
    expect(zoomTier(0.35)).toBe("story");
    expect(zoomTier(0.5499)).toBe("story");
    expect(zoomTier(0.55)).toBe("design");
    expect(zoomTier(1)).toBe("design");
    expect(zoomTier(1.35)).toBe("design");
    expect(zoomTier(1.3501)).toBe("detail");
    expect(zoomTier(2.5)).toBe("detail");
  });

  it("changes tier as a wheel zooms through the thresholds", () => {
    let view: View = { x: 0, y: 0, k: 1 };
    const tiers = new Set<string>();
    for (let step = 0; step < 30; step++) { view = zoomAt(view, { x: 500, y: 300 }, 1.1); tiers.add(zoomTier(view.k)); }
    expect(view.k).toBe(2.5);
    for (let step = 0; step < 60; step++) { view = zoomAt(view, { x: 500, y: 300 }, 0.9); tiers.add(zoomTier(view.k)); }
    expect(view.k).toBe(0.35);
    expect([...tiers].sort()).toEqual(["design", "detail", "story"]);
  });
});
