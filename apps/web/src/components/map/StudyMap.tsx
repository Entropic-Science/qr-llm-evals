import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode, type Ref } from "react";
import { LEGACY_BANNER, buildStudyGraph, shownByExpansion, visible as visibleNodes, type MapNode, type StudyGraph } from "../../lib/map/graph";
import { layoutGraph } from "../../lib/map/layout";
import { boundsWithNotes, buildNotes, isNoteId, layoutNotes } from "../../lib/map/notes";
import type { PopoverSide } from "../../lib/map/popover";
import { zoomTier, type Box, type View, type ZoomTier } from "../../lib/map/viewport";
import type { Problem } from "../../lib/problems";
import { REC_STATES, type Rec, type RecState } from "../../lib/recommendations";
import { useStudy } from "../../routes/configure/StudyContext";
import { Inspector, subjectOfNode, subjectOfNote, type Subject } from "./Inspector";
import { MapCanvas, type Standing } from "./MapCanvas";
import { MapLegend } from "./MapLegend";
import { MapOutline } from "./MapOutline";
import { MapSheet } from "./MapSheet";
import { MapToolbar } from "./MapToolbar";
import {
  currentNode, nearestDrawn, newMapMemory, problemTarget, severityOf, standingProblems, standingState, storyOf,
  type MapMemory, type MapMode,
} from "./model";
import type { MapCamera } from "./useMapCamera";
import { PromptQueries } from "./usePrompts";

// The study map: the study as one hand-drawn diagram, from its question to its verdicts.
//
// It is a second view of the study the setup steps edit. It reads the shared study and the lists computed for
// it (problems, recommendation states) and draws them as a graph. A node or a marginal note opens a popover
// with the fields of its settings, which are the fields of the steps and make the same edits; a fix is the
// problem's own; an eval opens down to its single prompts. It keeps nothing but how it is looked at.

export interface StudyMapProps {
  /**
   * Where the map keeps its view while it is not on screen. Whoever mounts the map holds it, so the view, the
   * unfolded nodes and the chosen form outlive a switch to the steps and back.
   */
  memory?: MutableRefObject<MapMemory>;
  /** Told when a node's details are opened, and with `null` when they are closed. */
  onOpenNode?: (node: MapNode | null) => void;
  /** Told when the pointer comes over a node, and with `null` when it leaves. */
  onHoverNode?: (node: MapNode | null) => void;
  /** More about the open node, drawn at the end of its popover. */
  inspector?: (node: MapNode) => ReactNode;
}

/** The nodes to unfold so that a node is drawn: every one that holds it without pinning it. */
function holders(graph: StudyGraph, id: string | null): string[] {
  const found: string[] = [];
  for (let node = graph.nodes.find((item) => item.id === id); node && node.parent !== null; node = graph.nodes.find((item) => item.id === node!.parent)) {
    if (!node.pinned) found.push(node.parent);
  }
  return found;
}

/** What to say when an edit has changed a setting: its new value, and what the study has and needs with it. */
function editSaid(before: readonly Rec[], after: readonly Rec[], own: readonly Rec[]): string | null {
  const was = new Map(before.map((rec) => [`${rec.nodeId}|${rec.path}`, rec]));
  const differs = (rec: Rec) => { const old = was.get(`${rec.nodeId}|${rec.path}`); return old !== undefined && (old.value !== rec.value || old.state !== rec.state); };
  const changed = own.find(differs) ?? after.find(differs);
  if (changed === undefined) return null;
  return `${changed.label}: ${changed.value}. ${REC_STATES[changed.state].legend}. ${changed.have} ${changed.need}`.trim();
}

function useMedia(query: string): boolean {
  const read = () => typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, [query]);
  return matches;
}

function openInAddress(): string | null {
  return new URLSearchParams(window.location.search).get("open");
}

/** Mirrors the open node in the address bar, replacing the entry: nothing navigates and nothing remounts. */
function writeOpenToAddress(id: string | null): void {
  const url = new URL(window.location.href);
  if (id === null) url.searchParams.delete("open");
  else url.searchParams.set("open", id);
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

const TIER_WORDS: Record<ZoomTier, string> = {
  story: "the story of the study",
  design: "the numbers of the design",
  detail: "every setting",
};

export function StudyMap(props: StudyMapProps) {
  return <PromptQueries><StudyMapView {...props} /></PromptQueries>;
}

function StudyMapView({ memory: given, onOpenNode, onHoverNode, inspector }: StudyMapProps) {
  // Without a holder the map remembers for as long as it is mounted.
  const [own] = useState<MutableRefObject<MapMemory>>(() => ({ current: newMapMemory() }));
  const memory = given ?? own;
  const { config, cards, problems, recs, updateConfig, openSettings, goTo, steps } = useStudy();

  const reducedMotion = useMedia("(prefers-reduced-motion: reduce)");
  const narrow = useMedia("(max-width: 760px)");
  const [choice, setChoice] = useState<MapMode | null>(memory.current.mode);
  const mode: MapMode = choice ?? (narrow ? "outline" : "canvas");

  const graph = useMemo(() => buildStudyGraph(config, cards, problems, recs), [config, cards, problems, recs]);
  // What was open when the map was last on screen comes back with it; an address that names a node wins.
  const [openId, setOpenId] = useState<string | null>(() => openInAddress() ?? memory.current.open ?? null);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => {
    // A node opened by the address is unfolded out of whatever holds it.
    return new Set([...memory.current.expanded, ...holders(graph, openId)]);
  });
  // The setting a popover was opened for (from its chip), and a count that tells two requests apart.
  const [focus, setFocus] = useState<{ id: string; path: string; nonce: number } | null>(null);
  // A node opened from inside a popover, which the camera has yet to bring into view.
  const [jump, setJump] = useState<{ id: string; nonce: number } | null>(null);
  const counter = useRef(0);
  const [tier, setTier] = useState<ZoomTier>(() => zoomTier(memory.current.view?.k ?? 1));
  const [wanted, setWanted] = useState<string | null>(memory.current.current);
  // The legend is a strip over the diagram and takes its height from it: it starts unfolded only where the
  // window is wide and tall enough to spare that height.
  const roomy = useMedia("(min-width: 1280px) and (min-height: 960px)");
  const [legendChoice, setLegendOpen] = useState(memory.current.legendOpen);
  const legendOpen = legendChoice ?? roomy;
  const [said, setSaid] = useState("");

  // What is open is unfolded out of whatever holds it, whatever an edit does to it: an outcome that is unticked
  // in its own popover folds into its eval, and the eval is unfolded in the same draw, so the popover and the
  // focus in it stay where the edit is being made. The reader's own fold closes what is open instead (`foldTo`).
  const unfolded = useMemo(() => {
    if (openId === null || isNoteId(openId) || mode !== "canvas") return expanded;
    const missing = holders(graph, openId).filter((holder) => !expanded.has(holder));
    return missing.length === 0 ? expanded : new Set([...expanded, ...missing]);
  }, [expanded, graph, openId, mode]);
  useEffect(() => {
    if (unfolded === expanded) return;
    memory.current.expanded = [...unfolded];
    setExpanded(unfolded);
  }, [unfolded, expanded, memory]);

  const visible = useMemo(() => visibleNodes(graph, unfolded, tier), [graph, unfolded, tier]);
  const layout = useMemo(() => layoutGraph(graph, unfolded, tier), [graph, unfolded, tier]);
  const standing = useMemo<Standing>(() => {
    const states: Record<string, RecState | null> = {};
    const held: Record<string, readonly Problem[]> = {};
    const errors = new Set<string>();
    for (const node of visible.nodes) {
      const folded = visible.folded[node.id];
      states[node.id] = standingState(node, folded);
      held[node.id] = standingProblems(node, folded);
      if (severityOf(held[node.id]!) === "error") errors.add(node.id);
    }
    return { states, problems: held, errors };
  }, [visible]);
  const story = useMemo(() => storyOf(graph), [graph]);
  const notes = useMemo(() => buildNotes(graph, config, recs, problems), [graph, config, recs, problems]);
  const noteBoxes = useMemo(() => layoutNotes(notes, layout, tier), [notes, layout, tier]);
  // What "Fit" shows is the same at every zoom, so the notes are counted in where the design zoom draws them.
  const bounds = useMemo(() => boundsWithNotes(layout.bounds, layoutNotes(notes, layout, "design")), [notes, layout]);
  // And what the story zoom draws, with the notes it keeps: what "Fit" shows where the whole does not fit.
  const storyBounds = useMemo(() => boundsWithNotes(layout.storyBounds, layoutNotes(notes, layout, "story")), [notes, layout]);
  const current = currentNode(graph, layout.order, wanted);
  // What is open is a node or a note that is there to be opened: on the diagram one that is drawn, in the outline any.
  const open = openId === null ? null
    : mode === "canvas" ? (layout.boxes[openId] !== undefined || noteBoxes[openId] !== undefined ? openId : null)
      : (graph.nodes.some((node) => node.id === openId) || notes.some((note) => note.id === openId) ? openId : null);

  const camera = useRef<MapCamera | null>(null);
  // Where what is open was last drawn, and what it was called: where the focus is sent back to, and what is
  // said, if it stops being drawn. By then the study may not have it any more.
  const lastOpen = useRef<{ box: Box | undefined; label: string | undefined }>({ box: undefined, label: undefined });
  useEffect(() => {
    if (open === null) return;
    const label = isNoteId(open) ? notes.find((note) => note.id === open)?.label : graph.nodes.find((node) => node.id === open)?.label;
    lastOpen.current = { box: layout.boxes[open] ?? noteBoxes[open], label };
  });
  const readout = useRef<HTMLSpanElement>(null);
  const outlineHeading = useRef<HTMLHeadingElement>(null);
  const section = useRef<HTMLElement>(null);
  const latest = useRef({ graph, config, cards, expanded: unfolded, notes, openId, onOpenNode, onHoverNode });
  useEffect(() => { latest.current = { graph, config, cards, expanded: unfolded, notes, openId, onOpenNode, onHoverNode }; });

  // The open node is in the address while the map is on screen, and only then: a link to the steps does not
  // carry a node of the map. The map's memory keeps it, so it is open again when the map comes back.
  useEffect(() => {
    memory.current.open = openId;
    writeOpenToAddress(openId);
  }, [openId, memory]);
  useEffect(() => () => writeOpenToAddress(null), []);

  // An edit made while a popover is open is said: the new value and what the study has and needs with it.
  const before = useRef(recs);
  useEffect(() => {
    const was = before.current;
    before.current = recs;
    if (was === recs || open === null) return;
    const own = isNoteId(open) ? notes.find((note) => note.id === open)?.recs : graph.nodes.find((node) => node.id === open)?.recs;
    const text = editSaid(was, recs, own ?? []);
    if (text !== null) setSaid(text);
  }, [recs, open, notes, graph]);

  // A node opened from inside a popover is brought into view, once. The layout this first runs with already
  // has the node unfolded; a jump that was kept would pull the camera back at every later change of the layout.
  useEffect(() => {
    if (jump === null) return;
    const box = layout.boxes[jump.id];
    if (box !== undefined) camera.current?.reveal(box, 1);
    setJump(null);
  }, [jump, layout]);

  /** Puts the focus on the title of a drawn node, if the focus is nowhere: a popover that went took it along. */
  const refocus = useCallback((id: string | null) => {
    const active = document.activeElement;
    if (id === null || (active !== null && active !== document.body)) return;
    for (const title of section.current?.querySelectorAll<HTMLElement>(".map-node__title") ?? []) {
      if (title.closest<HTMLElement>(".map-node")?.dataset.node === id) { title.focus({ preventScroll: true }); return; }
    }
  }, []);

  /** Closes what is open because it can no longer be shown, and says so. */
  const closeGone = useCallback((id: string, why: string) => {
    const now = latest.current;
    const label = (isNoteId(id) ? now.notes.find((note) => note.id === id)?.label : now.graph.nodes.find((node) => node.id === id)?.label) ?? lastOpen.current.label;
    now.openId = null;
    setOpenId(null);
    setFocus(null);
    now.onOpenNode?.(null);
    setSaid(`The details of ${label ?? "that part of the study"} were closed: ${why}`);
  }, []);

  // What is open has to be on the map. What cannot be drawn (a node the edit removed, a node the story zoom
  // leaves out, a note whose node is gone) has its popover closed properly: nothing stays open unseen, to come
  // back later and take the focus.
  useEffect(() => {
    if (openId === null || open !== null) return;
    const node = isNoteId(openId) ? undefined : graph.nodes.find((item) => item.id === openId);
    const note = notes.find((item) => item.id === openId);
    const anchor = note !== undefined && note.anchor.type === "node" ? note.anchor.node : null;
    closeGone(openId, node === undefined && note === undefined ? "it is no longer part of the study."
      : node !== undefined ? "it is not drawn at this zoom." : "what it hangs on is not drawn now.");
    refocus(nearestDrawn(graph, layout.boxes, layout.order, anchor ?? openId, lastOpen.current.box));
  }, [openId, open, graph, notes, layout, closeGone, refocus]);

  const onView = useCallback((view: View) => {
    if (readout.current) readout.current.textContent = `${Math.round(view.k * 100)}%`;
  }, []);
  // The readout is drawn anew with the toolbar; it is filled from the camera again each time.
  useEffect(() => { if (camera.current) onView(camera.current.view()); });

  const onTier = useCallback((next: ZoomTier) => {
    setTier(next);
    setSaid(`The map shows ${TIER_WORDS[next]}.`);
  }, []);

  /** Folds or unfolds to a new set. What is open and would be folded away is closed with it: the reader folded it. */
  const foldTo = useCallback((next: ReadonlySet<string>, words: string | null) => {
    const now = latest.current;
    const shown = now.openId;
    if (shown !== null) {
      const note = isNoteId(shown) ? now.notes.find((item) => item.id === shown) : undefined;
      const on = note === undefined ? shown : note.anchor.type === "node" ? note.anchor.node : null;
      if (on !== null && now.graph.nodes.some((node) => node.id === on) && !shownByExpansion(now.graph, next).has(on)) {
        now.openId = null;
        setOpenId(null);
        setFocus(null);
        now.onOpenNode?.(null);
      }
    }
    now.expanded = next;
    memory.current.expanded = [...next];
    setExpanded(next);
    if (words !== null) setSaid(words);
  }, [memory]);

  const toggle = useCallback((id: string, to?: boolean) => {
    const before = latest.current.expanded;
    const unfold = to ?? !before.has(id);
    if (unfold === before.has(id)) return;
    const next = new Set(before);
    if (unfold) next.add(id);
    else next.delete(id);
    const node = latest.current.graph.nodes.find((item) => item.id === id);
    foldTo(next, node === undefined ? null : unfold ? `${node.label} unfolded: ${node.children.length} ${node.children.length === 1 ? "part" : "parts"}.` : `${node.label} folded.`);
  }, [foldTo]);

  const setOpen = useCallback((id: string | null, path?: string) => {
    // What is opened is unfolded out of whatever holds it.
    const fold = id === null || isNoteId(id) ? [] : holders(latest.current.graph, id).filter((holder) => !latest.current.expanded.has(holder));
    if (fold.length > 0) {
      const next = new Set([...latest.current.expanded, ...fold]);
      latest.current.expanded = next;
      memory.current.expanded = [...next];
      setExpanded(next);
    }
    latest.current.openId = id;
    setOpenId(id);
    setFocus(id !== null && path !== undefined ? { id, path, nonce: ++counter.current } : null);
    latest.current.onOpenNode?.(id === null ? null : latest.current.graph.nodes.find((node) => node.id === id) ?? null);
  }, [memory]);

  /** Opens another node from inside a popover, and has the camera go to it. */
  const openFrom = useCallback((id: string) => {
    setOpen(id);
    setJump({ id, nonce: ++counter.current });
  }, [setOpen]);

  const setCurrent = useCallback((id: string) => {
    memory.current.current = id;
    setWanted(id);
  }, [memory]);

  const hover = useCallback((id: string | null) => {
    const handler = latest.current.onHoverNode;
    if (handler) handler(id === null ? null : latest.current.graph.nodes.find((node) => node.id === id) ?? null);
  }, []);

  const openSetting = useCallback((path: string) => openSettings({ path }), [openSettings]);
  const openProblem = useCallback((problem: Problem) => openSettings(problemTarget(problem)), [openSettings]);
  const openProblems = useCallback((list: readonly Problem[]) => {
    // One problem leads to its setting; several lead to the list of them.
    openSettings(list.length === 1 && list[0] ? problemTarget(list[0]) : { problemsOnly: true });
  }, [openSettings]);
  const fix = useCallback((problem: Problem) => {
    if (problem.fix) updateConfig(problem.fix.apply(latest.current.config, latest.current.cards));
  }, [updateConfig]);

  const pickMode = useCallback((next: MapMode) => {
    memory.current.mode = next;
    setChoice(next);
  }, [memory]);
  const toggleLegend = useCallback((next: boolean) => {
    memory.current.legendOpen = next;
    setLegendOpen(next);
  }, [memory]);

  const legacy = graph.legacy ? problems.find((problem) => problem.code === "design.legacy_plan") : undefined;

  const stepLabel = (stepId: Subject["stepId"]) => (steps.find((step) => step.id === stepId) ?? steps[steps.length - 1])?.label ?? "Review";
  const subjectOf = (id: string): Subject | null => {
    if (isNoteId(id)) {
      const note = notes.find((item) => item.id === id);
      return note === undefined ? null : subjectOfNote(note, graph);
    }
    const node = graph.nodes.find((item) => item.id === id);
    return node === undefined ? null : subjectOfNode(node, standing.states[id] ?? node.state, node.problems);
  };
  /** What a popover says and edits: the same on the diagram and under an entry of the outline. */
  const inspect = (subject: Subject) => <Inspector
    graph={graph}
    subject={subject}
    focus={focus !== null && focus.id === subject.id ? focus : null}
    stepLabel={stepLabel(subject.stepId)}
    onSetting={openSetting}
    onProblem={openProblem}
    onFix={fix}
    onStep={() => goTo({ stepId: subject.stepId })}
    onOpen={openFrom}
  >{inspector && subject.note === null && subject.node !== null ? <div className="map-sheet__inspector" data-inspector={subject.id}>{inspector(subject.node)}</div> : null}</Inspector>;

  const sheet = (id: string, placement: { side: PopoverSide; box: Box }, close: () => void, frame: Ref<HTMLElement>) => {
    const subject = subjectOf(id);
    if (subject === null) return null;
    // One popover per subject: a field half typed on one node is not carried to the next.
    return <MapSheet key={id} subject={subject} box={placement.box} side={placement.side} frame={frame} takeFocus={focus === null || focus.id !== id} onClose={close}>
      {inspect(subject)}
    </MapSheet>;
  };

  return <section
    className={`study-map${reducedMotion ? "" : " is-animated"}${open !== null && mode === "canvas" ? " has-open" : ""}`}
    data-zoom={tier}
    data-mode={mode}
    aria-label="Study map"
    ref={section}
  >
    {mode === "canvas" ? <button
      type="button"
      className="map-skip"
      onClick={() => { pickMode("outline"); window.setTimeout(() => outlineHeading.current?.focus(), 0); }}
    >Skip the map: open the outline</button> : null}
    <MapToolbar
      mode={mode}
      onMode={pickMode}
      readout={readout}
      unfolded={unfolded.size}
      onZoomIn={() => camera.current?.zoom(1.25)}
      onZoomOut={() => camera.current?.zoom(0.8)}
      onFit={() => camera.current?.fitAll()}
      onHome={() => camera.current?.home()}
      onFoldAll={() => {
        foldTo(new Set(), "Everything is folded.");
        // The button goes away with the last unfolded node: the focus goes back to the diagram.
        window.setTimeout(() => refocus(section.current?.querySelector<HTMLElement>(".map-node.is-current")?.dataset.node ?? null), 0);
      }}
      onReview={() => goTo({ stepId: "review" })}
    />
    <p className="map-caption">
      <span className="map-caption__question">{story.question}</span>{" "}
      <span>{story.arms}</span>{" "}
      <span>{story.outcomes}</span>{" "}
      <span>{story.verdicts}</span>
    </p>
    {graph.legacy ? <p className="map-banner" role="note">
      {LEGACY_BANNER}{" "}
      {legacy?.fix ? <button type="button" className="text-button" onClick={() => fix(legacy)}>{legacy.fix.label}</button> : null}
    </p> : null}
    {/* The legend is a strip over the diagram, not a box on it: it never covers a node, and it serves the outline too. */}
    <MapLegend open={legendOpen} onToggle={toggleLegend} />
    {mode === "canvas"
      ? <MapCanvas
        graph={graph}
        visible={visible}
        layout={layout}
        standing={standing}
        tier={tier}
        expanded={unfolded}
        notes={notes}
        noteBoxes={noteBoxes}
        bounds={bounds}
        storyBounds={storyBounds}
        current={current}
        open={open}
        animated={!reducedMotion}
        memory={memory}
        camera={camera}
        onTier={onTier}
        onView={onView}
        onToggle={toggle}
        onOpen={setOpen}
        onCurrent={setCurrent}
        onHover={hover}
        onSetting={openSetting}
        onProblems={openProblems}
        sheet={sheet}
      />
      : <MapOutline
        graph={graph}
        notes={notes}
        heading={outlineHeading}
        open={open}
        onOpen={setOpen}
        subjectOf={subjectOf}
        inspect={inspect}
        onSetting={openSetting}
        onProblem={openProblem}
        onFix={fix}
      />}
    <p className="sr-only map-status" aria-live="polite">{said}</p>
  </section>;
}
