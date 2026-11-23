// The study map's camera, as pure reducers. A view places the world on screen as
// `translate(x, y) scale(k)`: a world point p lands at p × k + (x, y). Nothing here touches the DOM; the map
// keeps the view in a ref and writes it to CSS variables itself.

export interface View { x: number; y: number; k: number }
/** A rectangle in world units. */
export interface Box { x: number; y: number; w: number; h: number }
/** The viewport element's size in CSS pixels. */
export interface Size { w: number; h: number }
/** A point in viewport pixels, measured from the viewport's top-left corner. */
export interface ScreenPoint { x: number; y: number }
export type ZoomTier = "story" | "design" | "detail";

export const MIN_K = 0.35;
export const MAX_K = 2.5;
/** Below this the map shows the story: the skeleton of the study, each node with its name. */
export const STORY_BELOW_K = 0.55;
/** Above this the map shows every chip and the hover note. */
export const DETAIL_ABOVE_K = 1.35;
/** Stands in for a viewport that has not been measured, or measures 0 × 0 as it does under jsdom. */
export const DEFAULT_SIZE: Size = { w: 1200, h: 720 };
export const IDENTITY_VIEW: View = { x: 0, y: 0, k: 1 };

const ENSURE_MARGIN = 24;

export function clampK(k: number): number {
  return Number.isFinite(k) ? Math.min(MAX_K, Math.max(MIN_K, k)) : 1;
}

function measured(size: Size | null | undefined): Size {
  return size && size.w > 0 && size.h > 0 ? size : DEFAULT_SIZE;
}

export function panBy(view: View, dx: number, dy: number): View {
  return dx === 0 && dy === 0 ? view : { x: view.x + dx, y: view.y + dy, k: view.k };
}

/**
 * Zoom by `factor` keeping the world point under `point` where it is on screen. At a zoom limit the view is
 * returned unchanged, so a wheel held at the limit neither drifts nor asks for a frame.
 */
export function zoomAt(view: View, point: ScreenPoint, factor: number): View {
  const k = clampK(view.k * factor);
  if (k === view.k) return view;
  const ratio = k / view.k;
  return { x: point.x - (point.x - view.x) * ratio, y: point.y - (point.y - view.y) * ratio, k };
}

/** The largest zoom "Fit" takes while it shows the story: just inside the story tier. */
export const STORY_FIT_K = 0.54;

/**
 * The view that shows all of `bounds`, centred, with `padding` pixels clear on every side. It never magnifies
 * past `maxK` (natural size, unless told otherwise): "Fit" on a small graph should land in the design tier, not
 * blow three nodes up to detail.
 *
 * Bounds too large to show at the smallest zoom are shown from their top-left corner, an axis at a time: that is
 * where the question and the station headings are, and the middle of a tall map may hold nothing at all.
 */
export function fit(bounds: Box, size?: Size | null, padding = 48, maxK = 1): View {
  const { w, h } = measured(size);
  const roomW = Math.max(1, w - 2 * padding), roomH = Math.max(1, h - 2 * padding);
  const k = clampK(Math.min(maxK, roomW / Math.max(1, bounds.w), roomH / Math.max(1, bounds.h)));
  const axis = (viewport: number, start: number, length: number) => (
    length * k + 2 * padding <= viewport + 1e-9 ? (viewport - length * k) / 2 - start * k : padding - start * k
  );
  return { x: axis(w, bounds.x, bounds.w), y: axis(h, bounds.y, bounds.h), k };
}

/**
 * The view "Fit" goes to: the whole study, as it is drawn at the zoom that shows it.
 *
 * `whole` holds everything at full density and `story` what the story zoom draws (the skeleton, the station
 * headings and the lanes). Where the whole fits at a zoom that draws all of it, that is the view. Where it
 * does not, the map would be at the story zoom, which leaves the unfolded parts out: the view is fitted to
 * what the story draws, and stays inside the story tier so that what it was fitted to is what is on screen.
 * The result depends on the two boxes and the viewport only, never on the zoom the map is at.
 */
export function fitWhole(whole: Box, story: Box, size?: Size | null, padding = 48): View {
  const all = fit(whole, size, padding);
  return zoomTier(all.k) === "story" ? fit(story, size, padding, STORY_FIT_K) : all;
}

/**
 * The smallest camera move that brings `box` fully on screen at a zoom of at least `minK`. Zooming in, when
 * needed, is done about the box's centre; a box too large for the viewport is shown from its top-left corner.
 * A view that already satisfies both is returned unchanged.
 */
export function ensureVisible(view: View, box: Box, size?: Size | null, minK = MIN_K, margin = ENSURE_MARGIN): View {
  const { w, h } = measured(size);
  const k = clampK(Math.max(view.k, minK));
  // Zooming about the box's centre moves the origin by the centre's world position times the change in k.
  const grow = k - view.k;
  const x = settle(view.x - (box.x + box.w / 2) * grow, box.x * k, box.w * k, w, margin);
  const y = settle(view.y - (box.y + box.h / 2) * grow, box.y * k, box.h * k, h, margin);
  return x === view.x && y === view.y && k === view.k ? view : { x, y, k };
}

/** One axis of `ensureVisible`: the offset nearest `offset` that puts [start, start + length] inside the viewport. */
function settle(offset: number, start: number, length: number, viewport: number, margin: number): number {
  const pad = Math.max(0, Math.min(margin, (viewport - length) / 2));
  const low = offset + start, high = low + length;
  if (length + 2 * pad > viewport || low < pad) return pad - start;
  if (high > viewport - pad) return viewport - pad - length - start;
  return offset;
}

export function zoomTier(k: number): ZoomTier {
  if (k < STORY_BELOW_K) return "story";
  return k > DETAIL_ABOVE_K ? "detail" : "design";
}
