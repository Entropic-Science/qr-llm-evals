import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, type MutableRefObject, type RefObject } from "react";
import {
  DEFAULT_SIZE, IDENTITY_VIEW, ensureVisible, fitWhole, panBy, zoomAt, zoomTier,
  type Box, type ScreenPoint, type Size, type View, type ZoomTier,
} from "../../lib/map/viewport";
import { homeView, zoomToBox, type MapMemory } from "./model";

// The study map's camera. The view lives in a ref and is written straight to CSS variables on the world, so
// panning and zooming draw no React frame; React is told only when the zoom crosses into another tier.

export interface MapCamera {
  view: () => View;
  size: () => Size;
  /** Measures the viewport anew and returns its size: for a moment that has to be right, such as placing a popover. */
  resize: () => Size;
  /** Moves the camera. `glide` eases there, unless motion is reduced. */
  set: (view: View, glide?: boolean) => void;
  /** Moves the camera on the next frame: for a drag or a wheel, which report many times per frame. */
  follow: (view: View) => void;
  pan: (dx: number, dy: number) => void;
  /** Zooms about a point of the viewport, or about its middle. */
  zoom: (factor: number, point?: ScreenPoint) => void;
  fitAll: () => void;
  home: () => void;
  /** Brings a box of the world into view, at a zoom of at least `minK`. */
  reveal: (box: Box, minK?: number) => void;
  /** Puts a box in the middle of the view, large enough to read. */
  zoomTo: (box: Box) => void;
}

interface CameraOptions {
  viewport: RefObject<HTMLElement | null>;
  world: RefObject<HTMLElement | null>;
  memory: MutableRefObject<MapMemory>;
  /** Everything the map draws, in world units. */
  bounds: Box;
  /** What the story zoom draws of it: what "Fit" shows where everything does not fit at a zoom that draws it. */
  storyBounds: Box;
  /** Whether the camera may ease. */
  animated: boolean;
  onTier: (tier: ZoomTier) => void;
  /** Told of every move, for a readout that is written without a render. */
  onView?: (view: View) => void;
}

const GLIDE_MS = 380;

function measure(element: HTMLElement | null): Size {
  const rect = element?.getBoundingClientRect();
  return rect && rect.width > 0 && rect.height > 0 ? { w: rect.width, h: rect.height } : DEFAULT_SIZE;
}

export function useMapCamera({ viewport, world, memory, bounds, storyBounds, animated, onTier, onView }: CameraOptions): MapCamera {
  const view = useRef<View>(memory.current.view ?? IDENTITY_VIEW);
  const size = useRef<Size>(DEFAULT_SIZE);
  const tier = useRef<ZoomTier>(zoomTier(view.current.k));
  const frame = useRef<number | null>(null);
  const gliding = useRef<number | null>(null);
  // The latest of what changes between renders, read by callbacks that must not change with it.
  const latest = useRef({ bounds, storyBounds, animated, onTier, onView });
  useEffect(() => { latest.current = { bounds, storyBounds, animated, onTier, onView }; });

  const write = useCallback(() => {
    const element = world.current;
    const { x, y, k } = view.current;
    if (element) {
      element.style.setProperty("--map-x", `${x.toFixed(2)}px`);
      element.style.setProperty("--map-y", `${y.toFixed(2)}px`);
      element.style.setProperty("--map-k", k.toFixed(4));
    }
    latest.current.onView?.(view.current);
    const next = zoomTier(k);
    if (next !== tier.current) {
      tier.current = next;
      latest.current.onTier(next);
    }
  }, [world]);

  const stopGlide = useCallback(() => {
    if (gliding.current !== null) window.clearTimeout(gliding.current);
    gliding.current = null;
    world.current?.classList.remove("is-gliding");
  }, [world]);

  const set = useCallback((next: View, glide = false) => {
    if (frame.current !== null) { window.cancelAnimationFrame?.(frame.current); frame.current = null; }
    view.current = next;
    memory.current.view = next;
    stopGlide();
    if (glide && latest.current.animated && world.current) {
      world.current.classList.add("is-gliding");
      gliding.current = window.setTimeout(stopGlide, GLIDE_MS + 40);
    }
    write();
  }, [memory, stopGlide, write]);

  const follow = useCallback((next: View) => {
    view.current = next;
    memory.current.view = next;
    stopGlide();
    if (typeof window.requestAnimationFrame !== "function") { write(); return; }
    if (frame.current !== null) return;
    frame.current = window.requestAnimationFrame(() => { frame.current = null; write(); });
  }, [memory, stopGlide, write]);

  // The first view is set before anything is painted: the remembered one, or the opening one for this window.
  useLayoutEffect(() => {
    size.current = measure(viewport.current);
    set(memory.current.view ?? homeView(latest.current.bounds, size.current));
    const element = viewport.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => { size.current = measure(element); });
    observer.observe(element);
    return () => observer.disconnect();
  }, [viewport, memory, set]);

  useEffect(() => () => {
    if (frame.current !== null) window.cancelAnimationFrame?.(frame.current);
    if (gliding.current !== null) window.clearTimeout(gliding.current);
  }, []);

  return useMemo<MapCamera>(() => ({
    view: () => view.current,
    size: () => size.current,
    resize: () => { size.current = measure(viewport.current); return size.current; },
    set,
    follow,
    pan: (dx, dy) => set(panBy(view.current, dx, dy), true),
    zoom: (factor, point) => set(zoomAt(view.current, point ?? { x: size.current.w / 2, y: size.current.h / 2 }, factor), true),
    fitAll: () => set(fitWhole(latest.current.bounds, latest.current.storyBounds, size.current, 32), true),
    home: () => set(homeView(latest.current.bounds, size.current), true),
    reveal: (box, minK) => set(ensureVisible(view.current, box, size.current, minK), true),
    zoomTo: (box) => set(zoomToBox(box, size.current), true),
  }), [set, follow, viewport]);
}
