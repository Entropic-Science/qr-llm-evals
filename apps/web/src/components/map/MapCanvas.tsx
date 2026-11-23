import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent, type MouseEvent, type MutableRefObject, type PointerEvent, type ReactNode, type Ref,
} from "react";
import { lineage, worstState, type MapNode, type StudyGraph, type Visible } from "../../lib/map/graph";
import { OPEN_SIZE, STATION_X, type MapLayout } from "../../lib/map/layout";
import { isNoteId, type MapNote } from "../../lib/map/notes";
import { LEAN_MAX, LEAN_RADIUS, keepInView, lean, popoverBox, unionBox, type PopoverSide } from "../../lib/map/popover";
import { panBy, zoomAt, type Box, type ScreenPoint, type Size, type View, type ZoomTier } from "../../lib/map/viewport";
import type { Problem } from "../../lib/problems";
import type { RecState } from "../../lib/recommendations";
import { StatePip } from "./MapChip";
import { MapInk } from "./MapInk";
import { MapNodeView } from "./MapNode";
import { MapNotes } from "./MapNotes";
import { nearestDrawn, problemCounts, problemWords, severityOf, type MapMemory } from "./model";
import { useMapCamera, type MapCamera } from "./useMapCamera";

// The diagram: a viewport onto a world that holds the ink and the nodes under one transform. This component
// owns the camera, the pointer, the wheel and the keyboard; what is drawn is decided by the map above it.

export interface Standing {
  states: Readonly<Record<string, RecState | null>>;
  problems: Readonly<Record<string, readonly Problem[]>>;
  errors: ReadonlySet<string>;
}

interface MapCanvasProps {
  graph: StudyGraph;
  visible: Visible;
  layout: MapLayout;
  standing: Standing;
  tier: ZoomTier;
  expanded: ReadonlySet<string>;
  /** The marginal notes, and where each one that is drawn hangs. */
  notes: readonly MapNote[];
  noteBoxes: Readonly<Record<string, Box>>;
  /** Everything the map draws, notes included: what "Fit" shows. */
  bounds: Box;
  /** What the story zoom draws of it: what "Fit" shows where everything does not fit. */
  storyBounds: Box;
  /** The node in the tab order. */
  current: string | null;
  /** The node or note whose popover is open, or `null`. */
  open: string | null;
  animated: boolean;
  memory: MutableRefObject<MapMemory>;
  /** Handed the camera once it exists, for the toolbar. */
  camera: MutableRefObject<MapCamera | null>;
  onTier: (tier: ZoomTier) => void;
  onView: (view: View) => void;
  onToggle: (id: string, open?: boolean) => void;
  /** Opens the popover of a node or a note, on the field of `path` when one is named; `null` closes it. */
  onOpen: (id: string | null, path?: string) => void;
  onCurrent: (id: string) => void;
  onHover: (id: string | null) => void;
  onSetting: (path: string) => void;
  onProblems: (problems: readonly Problem[]) => void;
  /**
   * The open popover, placed by the canvas: it is given where it hangs in the world and on which side of its
   * subject, the way to close it, and the ref of its element (the canvas keeps it inside the viewport).
   */
  sheet: (id: string, placement: { side: PopoverSide; box: Box }, close: () => void, frame: Ref<HTMLElement>) => ReactNode;
}

const PAN_STEP = 96;
const ZOOM_STEP = 1.25;
/** How far the pointer travels before a press becomes a drag. */
const DRAG_SLOP = 4;
/** How long a single click waits for a second one, where the two would undo each other. */
const DOUBLE_CLICK_MS = 230;
const ARROWS: Readonly<Record<string, { side: "left" | "right" | "up" | "down"; dx: number; dy: number; step: 1 | -1 }>> = {
  ArrowLeft: { side: "left", dx: 1, dy: 0, step: -1 },
  ArrowRight: { side: "right", dx: -1, dy: 0, step: 1 },
  ArrowUp: { side: "up", dx: 0, dy: 1, step: -1 },
  ArrowDown: { side: "down", dx: 0, dy: -1, step: 1 },
};
/** What a press on it must not turn into a drag of the map. */
const NOT_DRAGGABLE = ".map-sheet, .map-legend, .rec-tip, .map-stamp__rule, input, select, textarea";
/** What a click on it is not a click on the paper. */
const NOT_PAPER = ".map-node, .map-sheet, .map-legend, .map-marginal, .map-station__problems";

/** The size of a node's popover: an eval has room for its prompts, a test for its hypothesis card. */
export function sheetSize(node: Pick<MapNode, "kind">): Size {
  if (node.kind === "card") return { w: OPEN_SIZE.card.w + 60, h: 560 };
  if (node.kind === "test" || node.kind === "verdict") return { w: 460, h: 480 };
  return { w: OPEN_SIZE.other.w + 20, h: 420 };
}
/** The size of a note's popover. */
const NOTE_SHEET: Size = { w: 360, h: 400 };

/** The nodes a node stands for when it is lit: itself and what folds into it in its own station. */
function ownNodes(graph: StudyGraph, id: string): Set<string> {
  const found = new Set<string>();
  const add = (nodeId: string) => {
    const node = graph.nodes.find((item) => item.id === nodeId);
    if (node === undefined || found.has(nodeId)) return;
    found.add(nodeId);
    for (const child of node.children) if (graph.nodes.find((item) => item.id === child)?.station === node.station) add(child);
  };
  add(id);
  return found;
}

export function MapCanvas({
  graph, visible, layout, standing, tier, expanded, notes, noteBoxes, bounds, storyBounds, current, open, animated, memory, camera: cameraOut,
  onTier, onView, onToggle, onOpen, onCurrent, onHover, onSetting, onProblems, sheet,
}: MapCanvasProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const world = useRef<HTMLDivElement>(null);
  const titles = useRef(new Map<string, HTMLButtonElement>());
  const cameraRef = useRef<MapCamera | null>(null);
  // The open popover: its element, where it hangs, and how tall it was last measured.
  const popover = useRef<HTMLElement | null>(null);
  const hung = useRef<{ box: Box; height: number } | null>(null);

  /** Keeps the open popover inside the viewport. Called on every move of the camera: arithmetic and five style writes, no measuring. */
  const fitPopover = useCallback(() => {
    const element = popover.current, at = hung.current, held = cameraRef.current;
    if (!element || !at || !held) return;
    const fitted = keepInView(held.view(), at.box, at.height, held.size());
    element.style.setProperty("--pop-dx", `${fitted.dx.toFixed(2)}px`);
    element.style.setProperty("--pop-dy", `${fitted.dy.toFixed(2)}px`);
    element.style.setProperty("--pop-s", fitted.scale.toFixed(4));
    element.style.setProperty("--pop-max-w", `${fitted.maxW.toFixed(1)}px`);
    element.style.setProperty("--pop-max-h", `${fitted.maxH.toFixed(1)}px`);
    element.classList.toggle("is-moved", fitted.moved);
  }, []);
  const viewed = useCallback((view: View) => { onView(view); fitPopover(); }, [onView, fitPopover]);

  const camera = useMapCamera({ viewport, world, memory, bounds, storyBounds, animated, onTier, onView: viewed });
  // What the handlers read at the time they run. They are stable, so the nodes are not redrawn on every pan.
  const latest = useRef({ graph, visible, layout, expanded, open, notes, noteBoxes });
  // Set before the popover is first fitted, which happens ahead of the ordinary effects.
  useLayoutEffect(() => { cameraRef.current = camera; }, [camera]);
  useEffect(() => { cameraOut.current = camera; return () => { cameraOut.current = null; }; }, [camera, cameraOut]);

  const byKeyboard = useRef(false);
  // The node the pointer is on and the node the keyboard is on: the one whose lineage is lit.
  const hovered = useRef<string | null>(null);
  const focusedNode = useRef<string | null>(null);
  // The node whose title holds the focus, by any means, and where it was drawn: where the focus goes from if the
  // node stops being drawn (the story zoom leaves it out, or it is folded away).
  const held = useRef<{ id: string; box: Box | undefined } | null>(null);
  const drag = useRef<{ id: number; x: number; y: number; moved: boolean } | null>(null);
  const touches = useRef(new Map<number, ScreenPoint>());
  const pinch = useRef<{ distance: number; middle: ScreenPoint } | null>(null);
  const swallowClick = useRef(false);
  const pendingClick = useRef<number | null>(null);

  const nodeOf = useCallback((id: string) => latest.current.graph.nodes.find((node) => node.id === id), []);
  const dropPendingClick = useCallback(() => {
    if (pendingClick.current !== null) window.clearTimeout(pendingClick.current);
    pendingClick.current = null;
  }, []);
  useEffect(() => dropPendingClick, [dropPendingClick]);

  const register = useCallback((id: string, element: HTMLButtonElement | null) => {
    if (element) titles.current.set(id, element);
    else titles.current.delete(id);
  }, []);

  const focusNode = useCallback((id: string) => {
    onCurrent(id);
    titles.current.get(id)?.focus({ preventScroll: true });
    const box = latest.current.layout.boxes[id];
    if (box) camera.reveal(box);
  }, [camera, onCurrent]);

  /** Where the popover of a node or a note hangs: the box of what it is about, and the popover's own size. */
  const anchorOf = useCallback((id: string): { box: Box; size: Size } | null => {
    const now = latest.current;
    if (isNoteId(id)) {
      const box = now.noteBoxes[id];
      return box === undefined ? null : { box, size: NOTE_SHEET };
    }
    const node = nodeOf(id), box = now.layout.boxes[id];
    return node === undefined || box === undefined ? null : { box, size: sheetSize(node) };
  }, [nodeOf]);

  const openSheet = useCallback((id: string, path?: string) => {
    const anchor = anchorOf(id);
    if (anchor === null) return;
    onOpen(id, path);
    // The popover is read, so it is brought into view with what it is about, at a size at which it can be.
    // The viewport is measured for it now: where it goes depends on the room there is.
    camera.reveal(unionBox(anchor.box, popoverBox(anchor.box, anchor.size, camera.resize()).box), 1);
  }, [anchorOf, camera, onOpen]);

  const closeSheet = useCallback(() => {
    const id = latest.current.open;
    if (id === null) return;
    onOpen(null);
    // The focus goes back to what the popover was opened from.
    if (isNoteId(id)) world.current?.querySelector<HTMLElement>(`.map-marginal[data-note="${id}"]`)?.focus({ preventScroll: true });
    else titles.current.get(id)?.focus({ preventScroll: true });
  }, [onOpen]);

  /**
   * Lights the lineage of one node and quiets the rest: everything upstream and downstream of the node under
   * the pointer, else of the node the keyboard is on, else of the node whose popover is open. Classes are set
   * on the elements directly, so resting the pointer on a node draws no React frame.
   */
  const applyLineage = useCallback(() => {
    const root = world.current;
    if (!root) return;
    const now = latest.current;
    const openNote = isNoteId(now.open) ? now.notes.find((note) => note.id === now.open) : undefined;
    const opened = now.open === null ? null : openNote === undefined ? now.open : openNote.anchor.type === "node" ? openNote.anchor.node : null;
    const wanted = hovered.current ?? focusedNode.current ?? opened;
    const id = wanted !== null && now.layout.boxes[wanted] !== undefined ? wanted : null;
    const line = id === null ? null : lineage(now.visible, id);
    const own = id === null ? new Set<string>() : ownNodes(now.graph, id);
    const up = new Set(line?.upstream.nodes), down = new Set(line?.downstream.nodes);
    const edges = new Set([...(line?.upstream.edges ?? []), ...(line?.downstream.edges ?? [])]);
    root.classList.toggle("has-lineage", id !== null);
    if (id === null) root.removeAttribute("data-lineage"); else root.setAttribute("data-lineage", id);
    const mark = (element: Element, key: string | null | undefined) => {
      const self = key != null && own.has(key), above = key != null && up.has(key), below = key != null && down.has(key);
      element.classList.toggle("is-lit", self);
      element.classList.toggle("is-upstream", above && !self);
      element.classList.toggle("is-downstream", below && !self);
      element.classList.toggle("is-quiet", id !== null && !self && !above && !below);
    };
    for (const element of root.querySelectorAll<HTMLElement>(".map-node")) mark(element, element.dataset.node);
    for (const element of root.querySelectorAll(".map-ink__node")) mark(element, element.getAttribute("data-ink"));
    for (const element of root.querySelectorAll<HTMLElement>(".map-marginal")) mark(element, element.dataset.anchor);
    for (const element of root.querySelectorAll(".map-edge")) {
      const lit = edges.has(element.getAttribute("data-edge") ?? "");
      element.classList.toggle("is-lit", lit);
      element.classList.toggle("is-quiet", id !== null && !lit);
    }
  }, []);
  // React writes a node's classes anew when one of its own changes; the lineage is put back before the frame is painted.
  useLayoutEffect(() => {
    latest.current = { graph, visible, layout, expanded, open, notes, noteBoxes };
    applyLineage();
  });

  const hoverNode = useCallback((id: string | null) => {
    // A map that is being dragged is not being read.
    if (drag.current?.moved) return;
    hovered.current = id;
    applyLineage();
    onHover(id);
  }, [applyLineage, onHover]);

  const blurred = useCallback((id: string) => {
    if (focusedNode.current !== id) return;
    focusedNode.current = null;
    applyLineage();
  }, [applyLineage]);

  const openField = useCallback((id: string, path: string) => openSheet(id, path), [openSheet]);

  const activate = useCallback((id: string, byPointer: boolean) => {
    const node = nodeOf(id);
    if (!node) return;
    dropPendingClick();
    const run = () => {
      pendingClick.current = null;
      if (!node.expandable) { openSheet(id); return; }
      const unfolds = !latest.current.expanded.has(id);
      onToggle(id);
      // An eval opens to its prompts as it unfolds its outcomes, and puts them away as it folds.
      if (node.kind !== "card") return;
      if (unfolds) openSheet(id);
      else if (latest.current.open === id) onOpen(null);
    };
    // A double-click zooms to the node. Unfolding can go ahead of it; folding and opening wait to see if it comes.
    const waits = byPointer && (!node.expandable || latest.current.expanded.has(id));
    if (waits) pendingClick.current = window.setTimeout(run, DOUBLE_CLICK_MS);
    else run();
  }, [dropPendingClick, nodeOf, onOpen, onToggle, openSheet]);

  const zoomTo = useCallback((id: string) => {
    dropPendingClick();
    const box = latest.current.layout.boxes[id];
    if (box) camera.zoomTo(box);
  }, [camera, dropPendingClick]);

  // The camera keeps what the keyboard reaches inside the diagram; this keeps the diagram inside the window. The
  // focus is moved without scrolling the page, so a diagram that hangs below the fold would hide it otherwise.
  const keepInWindow = useCallback(() => {
    const element = viewport.current;
    if (!element || typeof element.scrollIntoView !== "function") return;
    const rect = element.getBoundingClientRect();
    if (rect.bottom > window.innerHeight || rect.top < 0) element.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []);

  const focused = useCallback((id: string) => {
    // The lineage follows the keyboard. A node that only kept the focus after a press of the mouse does not hold it lit.
    focusedNode.current = byKeyboard.current ? id : null;
    // The keyboard has moved on: a pointer left resting on the map no longer says what is being read.
    if (byKeyboard.current) hovered.current = null;
    applyLineage();
    onCurrent(id);
    // The camera follows the keyboard, not a press of the mouse: that would move the map under the pointer.
    const box = latest.current.layout.boxes[id];
    if (byKeyboard.current && box) camera.reveal(box);
    if (byKeyboard.current) keepInWindow();
  }, [applyLineage, camera, keepInWindow, onCurrent]);

  // A note is a tab stop of its own, and a station's note can lie outside the viewport: the camera brings a note
  // the keyboard reaches into view, as it does a node, so the focus is never on something that cannot be seen.
  const focusedNote = useCallback((id: string) => {
    if (!byKeyboard.current) return;
    const box = latest.current.noteBoxes[id];
    if (box) camera.reveal(box);
    keepInWindow();
  }, [camera, keepInWindow]);

  // The Tab key brings the focus in from outside the diagram, where the diagram sees no key press of its own: the
  // page is listened to, so the camera knows it was the keyboard. A press of the mouse anywhere says it was not.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === "Tab") byKeyboard.current = true; };
    const onPress = () => { byKeyboard.current = false; };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onPress, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onPress, true);
    };
  }, []);

  // The wheel zooms about the pointer. The listener is added by hand: it has to be able to stop the page scrolling.
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement | null)?.closest?.(".map-sheet, .map-legend")) return;
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? Math.max(1, rect.height) : 1;
      const dx = event.deltaX * unit, dy = event.deltaY * unit;
      // A sideways swipe, or the wheel with Shift, pans; everything else zooms.
      if (event.shiftKey || Math.abs(dx) > Math.abs(dy)) {
        camera.follow(panBy(camera.view(), event.shiftKey && dx === 0 ? -dy : -dx, 0));
        return;
      }
      const factor = Math.exp(-dy * (event.ctrlKey ? 0.01 : 0.0015));
      camera.follow(zoomAt(camera.view(), { x: event.clientX - rect.left, y: event.clientY - rect.top }, factor));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [camera]);

  function local(point: ScreenPoint): ScreenPoint {
    const rect = viewport.current?.getBoundingClientRect();
    return rect ? { x: point.x - rect.left, y: point.y - rect.top } : point;
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    byKeyboard.current = false;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if ((event.target as HTMLElement).closest(NOT_DRAGGABLE)) return;
    touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (touches.current.size === 1) drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
    else { drag.current = null; pinch.current = null; }
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!touches.current.has(event.pointerId)) return;
    touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (touches.current.size >= 2) {
      // Two fingers: the distance between them zooms, their middle pans.
      const [a, b] = [...touches.current.values()] as [ScreenPoint, ScreenPoint];
      const distance = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      const middle = local({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
      const before = pinch.current;
      if (before !== null) {
        const zoomed = zoomAt(camera.view(), middle, distance / before.distance);
        camera.follow(panBy(zoomed, middle.x - before.middle.x, middle.y - before.middle.y));
      }
      pinch.current = { distance, middle };
      swallowClick.current = true;
      return;
    }
    const held = drag.current;
    if (held === null || held.id !== event.pointerId) return;
    const dx = event.clientX - held.x, dy = event.clientY - held.y;
    if (!held.moved) {
      if (Math.hypot(dx, dy) < DRAG_SLOP) return;
      held.moved = true;
      viewport.current?.setPointerCapture?.(event.pointerId);
      viewport.current?.classList.add("is-panning");
    }
    held.x = event.clientX;
    held.y = event.clientY;
    camera.follow(panBy(camera.view(), dx, dy));
  }

  function onPointerEnd(event: PointerEvent<HTMLDivElement>) {
    touches.current.delete(event.pointerId);
    if (touches.current.size < 2) pinch.current = null;
    const held = drag.current;
    if (held !== null && held.id === event.pointerId) {
      if (held.moved) {
        // The click that ends a drag is not a click on what the drag started on.
        swallowClick.current = true;
        viewport.current?.releasePointerCapture?.(event.pointerId);
        viewport.current?.classList.remove("is-panning");
      }
      drag.current = null;
    }
    if (swallowClick.current) window.setTimeout(() => { swallowClick.current = false; }, 0);
    // One finger left after a pinch goes on as a drag.
    const rest = [...touches.current.entries()][0];
    if (rest && drag.current === null) drag.current = { id: rest[0], x: rest[1].x, y: rest[1].y, moved: true };
  }

  function onClickCapture(event: MouseEvent<HTMLDivElement>) {
    if (!swallowClick.current) return;
    swallowClick.current = false;
    event.preventDefault();
    event.stopPropagation();
  }

  function onClick(event: MouseEvent<HTMLDivElement>) {
    // A click on the paper, not on anything drawn, puts an open sheet away.
    const target = event.target as HTMLElement;
    if (latest.current.open !== null && !target.closest(NOT_PAPER)) closeSheet();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    byKeyboard.current = true;
    if (event.defaultPrevented || event.altKey || event.metaKey || event.ctrlKey) return;
    const target = event.target as HTMLElement;
    const id = target.closest<HTMLElement>("[data-node]")?.dataset.node ?? null;
    const now = latest.current;

    if (event.key === "Escape") {
      if (now.open !== null) { event.preventDefault(); closeSheet(); return; }
      const node = id === null ? undefined : nodeOf(id);
      if (!node) return;
      if (node.expandable && now.expanded.has(node.id)) { event.preventDefault(); onToggle(node.id, false); return; }
      // A node that was unfolded out of another folds back into it.
      if (node.parent !== null && !node.pinned && now.expanded.has(node.parent) && now.layout.boxes[node.parent]) {
        event.preventDefault();
        onToggle(node.parent, false);
        focusNode(node.parent);
      }
      return;
    }
    if (target.closest(".map-sheet, .map-legend") || target.matches("input, select, textarea")) return;

    const arrow = ARROWS[event.key];
    if (arrow !== undefined) {
      event.preventDefault();
      if (event.shiftKey || id === null) { camera.pan(arrow.dx * PAN_STEP, arrow.dy * PAN_STEP); return; }
      // The nearest node that way; where the layout names none, the next one in reading order.
      const order = now.layout.order;
      const next = now.layout.neighbors[id]?.[arrow.side] ?? order[order.indexOf(id) + arrow.step];
      if (next !== undefined) focusNode(next);
      return;
    }
    if (id !== null && (event.key === "Home" || event.key === "End")) {
      const order = now.layout.order;
      const next = event.key === "Home" ? order[0] : order[order.length - 1];
      if (next !== undefined) { event.preventDefault(); focusNode(next); }
      return;
    }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); camera.zoom(ZOOM_STEP); }
    else if (event.key === "-" || event.key === "_") { event.preventDefault(); camera.zoom(1 / ZOOM_STEP); }
    else if (event.key === "0") { event.preventDefault(); camera.fitAll(); }
  }

  // Which node holds the focus is followed on the world itself, so a chip or a tool of a node counts as its title
  // does. A node that is taken off the map loses the focus without the reader having moved it: it is forgotten
  // only if, a moment after the focus left, it is still drawn.
  useEffect(() => {
    const root = world.current;
    if (!root) return;
    const nodeAt = (target: EventTarget | null) => (target instanceof Element ? target.closest<HTMLElement>(".map-node")?.dataset.node ?? null : null);
    const onFocusIn = (event: FocusEvent) => {
      const id = nodeAt(event.target);
      held.current = id === null ? null : { id, box: latest.current.layout.boxes[id] };
    };
    const onFocusOut = (event: FocusEvent) => {
      const id = nodeAt(event.target);
      if (id === null) return;
      window.setTimeout(() => {
        if (held.current?.id === id && titles.current.has(id) && nodeAt(document.activeElement) !== id) held.current = null;
      }, 0);
    };
    root.addEventListener("focusin", onFocusIn);
    root.addEventListener("focusout", onFocusOut);
    return () => {
      root.removeEventListener("focusin", onFocusIn);
      root.removeEventListener("focusout", onFocusOut);
    };
  }, []);

  // The node that held the focus is no longer drawn (the zoom crossed into the story, or it was folded away):
  // the focus goes to the nearest node that is, so the keyboard is never left on nothing.
  useEffect(() => {
    const was = held.current;
    if (was === null || layout.boxes[was.id] !== undefined) return;
    held.current = null;
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    const next = nearestDrawn(graph, layout.boxes, layout.order, was.id, was.box);
    if (next !== null) focusNode(next);
  }, [graph, layout, focusNode]);

  // A station answers for its drawn nodes and, at the story zoom, for what the story leaves out of it.
  const stationStanding = useMemo(() => Object.fromEntries(layout.stations.map((station) => {
    const own = visible.nodes.filter((node) => node.station === station.id);
    const left = visible.left[station.id];
    const problems = [...own.flatMap((node) => standing.problems[node.id] ?? []), ...(left?.problems ?? [])];
    return [station.id, { state: worstState([...own.map((node) => standing.states[node.id] ?? null), left?.state ?? null]), problems }];
  })), [layout.stations, visible.nodes, visible.left, standing]);

  // Where the open popover hangs. It is worked out on every draw: an edit can move what it hangs on.
  const noteBox = open !== null && isNoteId(open) ? noteBoxes[open] : undefined;
  const nodeBox = open !== null && !isNoteId(open) ? layout.boxes[open] : undefined;
  const anchor = noteBox !== undefined ? { box: noteBox, size: NOTE_SHEET }
    : nodeBox !== undefined ? { box: nodeBox, size: sheetSize(visible.nodes.find((node) => node.id === open) ?? { kind: "study" }) }
      : null;
  const placement = anchor === null ? null : popoverBox(anchor.box, anchor.size, camera.size());
  const frame = useCallback((element: HTMLElement | null) => { popover.current = element; }, []);
  const [, setResized] = useState(0);
  useLayoutEffect(() => {
    const element = popover.current;
    hung.current = placement === null || !element ? null : { box: placement.box, height: element.offsetHeight || placement.box.h };
    fitPopover();
  });
  // The popover grows and shrinks with what is in it (a list that has loaded, a problem that was fixed).
  useEffect(() => {
    const element = popover.current;
    if (!element || open === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (hung.current) hung.current.height = element.offsetHeight || hung.current.box.h;
      const held = cameraRef.current;
      if (held) {
        const was = held.size(), now = held.resize();
        // Another size of viewport may have room for the popover on another side: it is placed anew, and
        // brought into view with what it is about, as when it was opened.
        if (was.w !== now.w || was.h !== now.h) {
          setResized((count) => count + 1);
          const anchor = latest.current.open === null ? null : anchorOf(latest.current.open);
          if (anchor !== null) held.reveal(unionBox(anchor.box, popoverBox(anchor.box, anchor.size, now).box), 1);
        }
      }
      fitPopover();
    });
    observer.observe(element);
    // A viewport that changes its size has other edges to keep the popover inside of.
    if (viewport.current) observer.observe(viewport.current);
    return () => observer.disconnect();
  }, [open, fitPopover, anchorOf]);

  // Nodes lean toward a pointer that comes near and the notes shy away from it; both settle back when it
  // leaves. One listener and at most one frame per frame; the frame does arithmetic on the layout's boxes and
  // writes three custom properties on the few elements in reach. Not for touch, and not when motion is reduced.
  useEffect(() => {
    const element = viewport.current;
    if (!animated || !element || typeof window.requestAnimationFrame !== "function") return;
    let frame: number | null = null;
    let point: ScreenPoint | null = null;
    let rect: DOMRect | null = null;
    const leaning = new Set<string>();
    const elements = (selector: string) => world.current?.querySelectorAll<HTMLElement | SVGElement>(selector) ?? [];
    const settle = (selector: string) => {
      for (const item of elements(selector)) { item.style.removeProperty("--near-x"); item.style.removeProperty("--near-y"); item.style.removeProperty("--near-r"); }
      leaning.delete(selector);
    };
    const run = () => {
      frame = null;
      const near = new Set<string>();
      if (point !== null) {
        const view = camera.view();
        const at = { x: (point.x - view.x) / view.k, y: (point.y - view.y) / view.k };
        const radius = LEAN_RADIUS / view.k;
        const now = latest.current;
        const consider = (selector: string, box: Box, max: number) => {
          const by = lean(at, box, radius, max);
          if (by.x === 0 && by.y === 0) return;
          near.add(selector);
          leaning.add(selector);
          for (const item of elements(selector)) {
            item.style.setProperty("--near-x", `${by.x.toFixed(2)}px`);
            item.style.setProperty("--near-y", `${by.y.toFixed(2)}px`);
            item.style.setProperty("--near-r", `${(by.x * 0.45).toFixed(2)}deg`);
          }
        };
        for (const id of now.layout.order) consider(`[data-node="${id}"], [data-ink="${id}"]`, now.layout.boxes[id]!, LEAN_MAX);
        for (const [id, box] of Object.entries(now.noteBoxes)) consider(`[data-note="${id}"]`, box, -LEAN_MAX);
      }
      for (const selector of [...leaning]) if (!near.has(selector)) settle(selector);
    };
    const ask = () => { if (frame === null) frame = window.requestAnimationFrame(run); };
    const onMove = (event: globalThis.PointerEvent) => {
      if (event.pointerType === "touch") return;
      const over = event.target as Element | null;
      if (drag.current?.moved || over?.closest?.(".map-sheet, .map-legend")) point = null;
      else {
        rect ??= element.getBoundingClientRect();
        point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      }
      ask();
    };
    const onLeave = () => { point = null; rect = null; ask(); };
    const forget = () => { rect = null; };
    element.addEventListener("pointermove", onMove, { passive: true });
    element.addEventListener("pointerleave", onLeave);
    window.addEventListener("scroll", forget, { passive: true, capture: true });
    window.addEventListener("resize", forget);
    return () => {
      element.removeEventListener("pointermove", onMove);
      element.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("scroll", forget, { capture: true });
      window.removeEventListener("resize", forget);
      if (frame !== null) window.cancelAnimationFrame?.(frame);
      for (const selector of [...leaning]) settle(selector);
    };
  }, [animated, camera]);

  return <div
    className="map-viewport"
    ref={viewport}
    tabIndex={-1}
    role="group"
    aria-label="Diagram of the study. Arrow keys move from node to node; Shift and an arrow key pans; plus and minus zoom; 0 shows the whole study."
    onPointerDown={onPointerDown}
    onPointerMove={onPointerMove}
    onPointerUp={onPointerEnd}
    onPointerCancel={onPointerEnd}
    onClickCapture={onClickCapture}
    onClick={onClick}
    onKeyDown={onKeyDown}
    // A focused node off screen would make the browser scroll the viewport under the camera: undo that.
    onScroll={(event) => { event.currentTarget.scrollLeft = 0; event.currentTarget.scrollTop = 0; }}
  >
    <div className="map-world" ref={world}>
      <MapInk graph={graph} visible={visible} layout={layout} states={standing.states} errors={standing.errors} />
      <div className="map-stations">
        {layout.stations.map((station, index) => {
          const own = stationStanding[station.id];
          const severity = severityOf(own?.problems ?? []);
          return <div key={station.id} className="map-station" data-station={station.id} style={{ transform: `translate(${station.header.x}px, ${station.header.y}px)`, width: station.header.w, height: station.header.h }}>
            <h3 className="map-station__label"><span className="map-station__number" aria-hidden="true">{index + 1}</span>{station.label}</h3>
            <p className="map-station__sentence">{station.sentence}</p>
            {own && (own.state !== null || severity !== null) ? <p className="map-station__standing">
              {own.state !== null ? <StatePip state={own.state} /> : null}
              {/* A way to the problems from the heading: at the story zoom some of them sit on nodes that are not drawn. */}
              {severity !== null ? <button
                type="button"
                className={`map-station__problems map-station__problems--${severity}`}
                aria-label={`${problemWords(own.problems)} at ${station.label}. Show ${own.problems.length === 1 ? "it" : "them"} in the settings.`}
                title={own.problems.map((problem) => problem.message).join("\n")}
                onClick={() => onProblems(own.problems)}
              >
                <span className="map-station__words"><span aria-hidden="true">{severity === "error" ? "✕" : "!"}</span> {problemWords(own.problems)}</span>
                {/* The same as counts, for the story zoom, where the words are wider than the station. */}
                <span className="map-station__counts" aria-hidden="true">{problemCounts(own.problems)}</span>
              </button> : null}
            </p> : null}
          </div>;
        })}
        {layout.lanes.map((lane) => <p key={lane.key} className="map-lane" data-lane={lane.key} style={{ transform: `translate(${lane.labelBox.x}px, ${lane.labelBox.y}px)`, width: lane.labelBox.w, height: lane.labelBox.h }}>{lane.label}</p>)}
        {/* The same name again where the lane goes on, past the evals that every comparison shares. */}
        {layout.lanes.map((lane) => <p key={`${lane.key}:tests`} className="map-lane map-lane--again" aria-hidden="true" style={{ transform: `translate(${STATION_X.tests}px, ${lane.labelBox.y}px)`, width: 420, height: lane.labelBox.h }}>{lane.label}</p>)}
      </div>
      <div className="map-nodes">
        {visible.nodes.map((node) => {
          const box = layout.boxes[node.id];
          if (box === undefined) return null;
          return <MapNodeView
            key={node.id}
            node={node}
            box={box}
            tier={tier}
            state={standing.states[node.id] ?? null}
            problems={standing.problems[node.id] ?? node.problems}
            unfolds={node.children.filter((id) => layout.boxes[id] === undefined).length}
            expanded={expanded.has(node.id)}
            open={open === node.id}
            current={current === node.id}
            order={layout.stations.findIndex((station) => station.id === node.station)}
            onActivate={activate}
            onOpen={openSheet}
            onZoomTo={zoomTo}
            onFocus={focused}
            onBlur={blurred}
            onHover={hoverNode}
            onField={openField}
            onSetting={onSetting}
            onProblems={onProblems}
            register={register}
          />;
        })}
      </div>
      <MapNotes notes={notes} boxes={noteBoxes} open={open} current={current} onOpen={openSheet} onFocus={focusedNote} />
      {open !== null && placement !== null ? sheet(open, placement, closeSheet, frame) : null}
    </div>
  </div>;
}
