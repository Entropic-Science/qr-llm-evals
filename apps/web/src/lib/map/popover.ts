import type { Box, ScreenPoint, Size, View } from "./viewport";

// Geometry of what the study map lays over itself and of how it answers the pointer: where the popover of an
// opened node goes, how it is kept inside the viewport while the map moves under it, and how far a node leans
// toward a pointer that comes near. Pure arithmetic on boxes; nothing here measures the page.

export type PopoverSide = "below" | "right";

/** The room kept between a popover and what it hangs on, and between it and the edge of the viewport. */
export const POPOVER_GAP = 20;
export const POPOVER_PAD = 8;
/** A popover is never squeezed below this height, however short the viewport is. */
export const POPOVER_MIN_H = 160;

/**
 * Where the popover of an anchor (a node or a note) goes, in world units: under it when anchor and popover fit
 * the height of the viewport together, else beside it when they fit its width, else under it all the same.
 * The anchor stays in sight either way, so a value edited in the popover can be watched changing on the map.
 */
export function popoverBox(anchor: Box, size: Size, viewport: Size, gap = POPOVER_GAP): { side: PopoverSide; box: Box } {
  const room = 2 * POPOVER_PAD;
  const below = { x: anchor.x, y: anchor.y + anchor.h + gap, w: size.w, h: size.h };
  if (anchor.h + gap + size.h + room <= viewport.h) return { side: "below", box: below };
  if (anchor.w + gap + size.w + room <= viewport.w) return { side: "right", box: { x: anchor.x + anchor.w + gap, y: anchor.y, w: size.w, h: size.h } };
  return { side: "below", box: below };
}

/** The box that holds two boxes. */
export function unionBox(a: Box, b: Box): Box {
  const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
}

export interface PopoverFit {
  /** How far the popover is moved from where it hangs, in world units, to stay inside the viewport. */
  dx: number;
  dy: number;
  /** The popover's own scale: it grows with the map up to its reading size and no further. */
  scale: number;
  /** The most room the viewport has for it, in the popover's own px. */
  maxW: number;
  maxH: number;
  /** Whether it had to be moved, so that it no longer sits where its pointer says. */
  moved: boolean;
}

/**
 * Keeps a popover inside the viewport. `box` is where it hangs in the world and `height` how tall it is drawn
 * (in its own px). The popover moves with the map; when the map would carry it over an edge it stops at the
 * edge, and when the viewport is smaller than the popover its size is capped at what the viewport has.
 */
export function keepInView(view: View, box: Box, height: number, viewport: Size, pad = POPOVER_PAD): PopoverFit {
  const scale = 1 / Math.max(1, view.k);
  // Screen px per px of the popover: the zoom below 1, exactly 1 above it.
  const onScreen = view.k * scale;
  const maxW = Math.max(1, (viewport.w - 2 * pad) / onScreen);
  const maxH = Math.max(Math.min(POPOVER_MIN_H, box.h), (viewport.h - 2 * pad) / onScreen);
  const w = Math.min(box.w, maxW) * onScreen;
  const h = Math.min(height, box.h, maxH) * onScreen;
  const left = view.x + view.k * box.x, top = view.y + view.k * box.y;
  const clamp = (at: number, extent: number, room: number) => Math.min(Math.max(at, pad), Math.max(pad, room - pad - extent));
  const dx = (clamp(left, w, viewport.w) - left) / view.k;
  const dy = (clamp(top, h, viewport.h) - top) / view.k;
  return { dx, dy, scale, maxW, maxH, moved: Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5 };
}

/** How near the pointer has to come for a node to answer it, in screen px. */
export const LEAN_RADIUS = 90;
/** The farthest a node moves, in world units. */
export const LEAN_MAX = 2.5;

/**
 * How far a box leans toward a pointer, in world units (`{0, 0}` when the pointer is out of reach). The pull is
 * strongest with the pointer on the box and fades to nothing `radius` away from its edge; its direction is from
 * the middle of the box to the pointer. Pass a negative `max` for a box that shies away instead.
 */
export function lean(point: ScreenPoint, box: Box, radius: number, max = LEAN_MAX): ScreenPoint {
  const dx = Math.max(box.x - point.x, 0, point.x - (box.x + box.w));
  const dy = Math.max(box.y - point.y, 0, point.y - (box.y + box.h));
  const distance = Math.hypot(dx, dy);
  if (!(radius > 0) || distance >= radius) return { x: 0, y: 0 };
  const falloff = 1 - distance / radius;
  const unit = (offset: number, half: number) => Math.max(-1, Math.min(1, offset / (half + radius)));
  return {
    x: unit(point.x - (box.x + box.w / 2), box.w / 2) * max * falloff,
    y: unit(point.y - (box.y + box.h / 2), box.h / 2) * max * falloff,
  };
}
