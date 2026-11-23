import { worstState, type Folded, type MapNode, type StudyGraph } from "../../lib/map/graph";
import type { Box, Size, View, ZoomTier } from "../../lib/map/viewport";
import { clampK } from "../../lib/map/viewport";
import { panelFor } from "../../lib/panels";
import type { Problem } from "../../lib/problems";
import { REC_STATES, type Rec, type RecState } from "../../lib/recommendations";
import { problemPath } from "../../routes/configure/problem-target";

// What the study map's views share and no drawing is needed for: how a node stands, what it is called, which of
// its settings are shown at a zoom, and where the camera starts. Pure; the components only draw what this says.

/** How the map is shown: the hand-drawn diagram, or the same graph as a nested list. */
export type MapMode = "canvas" | "outline";

/**
 * What the map remembers while it is not on screen. The setup shell holds one of these for as long as the page
 * is open, so switching to the steps and back returns to the same place, with the same nodes unfolded.
 */
export interface MapMemory {
  view: View | null;
  expanded: string[];
  /** The viewer's own pick. `null` leaves it to the width of the window. */
  mode: MapMode | null;
  current: string | null;
  /** The viewer's own pick. `null` leaves it to the room there is: open on a wide window, folded on a narrow one. */
  legendOpen: boolean | null;
  /**
   * The node or note whose popover is open. While the map is on screen the address says it too (`?open=`); the
   * address is cleared when the map goes off screen, and this brings the popover back with the map.
   */
  open?: string | null;
}

export function newMapMemory(): MapMemory {
  return { view: null, expanded: [], mode: null, current: null, legendOpen: null, open: null };
}

/** Worst first, with what the harness fixes last: the order chips are shown in when there is room for a few. */
const CHIP_ORDER: readonly RecState[] = ["invalid", "insufficient", "marginal", "unknown", "custom", "sufficient", "recommended", "locked"];
/** How many settings a node shows at the design zoom. */
export const DESIGN_CHIPS = 3;

/** How a drawn node stands: its own state, or the worst of what is folded into it. */
export function standingState(node: MapNode, folded: Folded | undefined): RecState | null {
  return worstState([node.state, folded?.state ?? null]);
}

/** The problems a drawn node answers for: its own and those of what is folded into it. */
export function standingProblems(node: MapNode, folded: Folded | undefined): Problem[] {
  return folded ? [...node.problems, ...folded.problems] : node.problems;
}

export function severityOf(problems: readonly Problem[]): "error" | "warning" | null {
  if (problems.some((problem) => problem.severity === "error")) return "error";
  return problems.length > 0 ? "warning" : null;
}

/** "1 problem blocks the launch", "2 warnings", or nothing. */
export function problemWords(problems: readonly Problem[]): string {
  const errors = problems.filter((problem) => problem.severity === "error").length;
  const warnings = problems.length - errors;
  const parts: string[] = [];
  if (errors > 0) parts.push(errors === 1 ? "1 problem blocks the launch" : `${errors} problems block the launch`);
  if (warnings > 0) parts.push(warnings === 1 ? "1 warning" : `${warnings} warnings`);
  return parts.join(", ");
}

/** The same as marks and counts, as a node's badge has them: "✕ 1 ! 2". For where the words have no room. */
export function problemCounts(problems: readonly Problem[]): string {
  const errors = problems.filter((problem) => problem.severity === "error").length;
  const warnings = problems.length - errors;
  return [errors > 0 ? `✕ ${errors}` : "", warnings > 0 ? `! ${warnings}` : ""].filter(Boolean).join("  ");
}

/** What a state is called, in the words of the legend. */
export function stateWords(state: RecState | null): string {
  return state === null ? "" : REC_STATES[state].legend;
}

/** A node's name with how it stands, for assistive technology: label, sub-label, state, problems. */
export function nodeName(node: MapNode, state: RecState | null, problems: readonly Problem[]): string {
  const parts = [node.sub ? `${node.label}, ${node.sub}` : node.label];
  if (node.ghost !== null) parts.push("Placeholder");
  const words = stateWords(state);
  if (words) parts.push(words);
  const trouble = problemWords(problems);
  if (trouble) parts.push(trouble);
  return `${parts.join(". ")}.`;
}

/** The settings a node shows as chips at a zoom: none in the story, the few that matter most in the design, all in the detail. */
export function chipsFor(node: MapNode, tier: ZoomTier): Rec[] {
  if (tier === "story") return [];
  const sorted = [...node.recs].sort((a, b) => CHIP_ORDER.indexOf(a.state) - CHIP_ORDER.indexOf(b.state));
  if (tier === "detail") return sorted;
  return sorted.filter((rec) => rec.state !== "locked").slice(0, DESIGN_CHIPS);
}

/** Whether a setting can be edited in the settings dialog. What the harness fixes has no panel. */
export function editable(path: string): boolean {
  return panelFor(path) !== undefined;
}

/** The first setting of a node that the settings dialog can open, or `null`. */
export function settingOf(node: MapNode): string | null {
  return node.settings.find(editable) ?? null;
}

/** Where the settings dialog opens for a problem: on its setting, or on the problems when no single field answers it. */
export function problemTarget(problem: Problem): { path: string } | { problemsOnly: true } {
  const path = problemPath(problem);
  return path !== undefined && editable(path) ? { path } : { problemsOnly: true };
}

/** A list as prose: "A", "A and B", "A, B and C". */
export function listed(names: readonly string[], joiner: "and" | "or" = "and"): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} ${joiner} ${names[names.length - 1]}`;
}

/**
 * The study in four sentences, the caption under the diagram: what is asked, what is compared, on which outcomes,
 * and which verdicts are within reach. Everything is read off the graph; nothing is promised that is not drawn.
 */
export function storyOf(graph: StudyGraph): { question: string; arms: string; outcomes: string; verdicts: string } {
  const study = graph.nodes.find((node) => node.id === "study");
  const compared = graph.lanes.filter((lane) => lane.compared);
  const deciding = graph.nodes.filter((node) => node.ref.type === "endpoint" && node.ref.role === "confirmatory" && node.hypotheses.length > 0);
  const stamps = graph.nodes.flatMap((node) => (node.ref.type === "verdict" ? node.ref.stamps : []));
  const findings = stamps.filter((stamp) => stamp.verdict !== "invalid" && stamp.verdict !== "unavailable");
  const unique = (labels: string[]) => [...new Set(labels)];
  const open = unique(findings.filter((stamp) => stamp.reachable).map((stamp) => stamp.label.toLowerCase()));
  const closed = unique(findings.filter((stamp) => !stamp.reachable).map((stamp) => stamp.label.toLowerCase())).filter((label) => !open.includes(label));
  return {
    question: study?.facts.story ?? "What the study asks.",
    arms: compared.length === 0 ? "No comparison is ticked yet."
      : compared.length === 1 ? `One comparison, ${compared[0]!.label}: the quantum arm against the seeded pseudo-random controls.`
        : `${compared.length} comparisons, each on its own: the quantum arm against the seeded pseudo-random controls.`,
    outcomes: graph.legacy ? "The outcomes are those of the fixed Study 1 plan."
      : deciding.length === 0 ? "No outcome decides the study yet."
        : `Decided on ${listed(unique(deciding.map((node) => node.label)))}.`,
    verdicts: graph.legacy ? "Tests, gates and verdicts are drawn once a design is declared."
      : stamps.length === 0 ? "No verdict can be drawn yet."
        : open.length === 0 ? "No finding is within reach as configured: every test could only come out invalid or unavailable."
        : `Verdicts within reach: ${listed(open)}.${closed.length > 0 ? ` Out of reach as configured: ${listed(closed)}.` : ""}`,
  };
}

/** The widest stretch of the world the opening view tries to show: from the question through to the measures. */
const HOME_SPAN = 1180;
const HOME_MIN_K = 0.8;
const HOME_PAD = 24;

/**
 * Where the camera starts: at the left edge, where the study's question is, at a zoom at which the numbers on the
 * nodes can be read. A graph small enough to be shown whole at that zoom is centred instead.
 */
export function homeView(bounds: Box, size: Size): View {
  const k = clampK(Math.min(1, Math.max(HOME_MIN_K, size.w / HOME_SPAN)));
  const fitsAcross = bounds.w * k + 2 * HOME_PAD <= size.w;
  const fitsDown = bounds.h * k + 2 * HOME_PAD <= size.h;
  return {
    x: fitsAcross ? (size.w - bounds.w * k) / 2 - bounds.x * k : HOME_PAD - bounds.x * k,
    y: fitsDown ? (size.h - bounds.h * k) / 2 - bounds.y * k : HOME_PAD - bounds.y * k,
    k,
  };
}

/** The view that puts a box in the middle of the viewport, large enough to read all of it. */
export function zoomToBox(box: Box, size: Size): View {
  const k = clampK(Math.min(2, Math.max(1.5, Math.min((size.w * 0.5) / box.w, (size.h * 0.5) / box.h))));
  return { x: size.w / 2 - (box.x + box.w / 2) * k, y: size.h / 2 - (box.y + box.h / 2) * k, k };
}

/**
 * The drawn node nearest to one that is no longer drawn, for a focus that has to go somewhere: the nearest
 * drawn node that holds it, else the drawn node whose box is closest to where it was, else the first.
 */
export function nearestDrawn(graph: StudyGraph, boxes: Readonly<Record<string, Box>>, order: readonly string[], id: string, was: Box | undefined): string | null {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  for (let node = byId.get(id); node !== undefined; node = node.parent === null ? undefined : byId.get(node.parent)) {
    if (boxes[node.id] !== undefined) return node.id;
  }
  if (was === undefined) return order[0] ?? null;
  const centre = (box: Box) => ({ x: box.x + box.w / 2, y: box.y + box.h / 2 });
  const from = centre(was);
  let best: { id: string; distance: number } | null = null;
  for (const other of order) {
    const at = centre(boxes[other]!);
    const distance = Math.hypot(at.x - from.x, at.y - from.y);
    if (best === null || distance < best.distance) best = { id: other, distance };
  }
  return best?.id ?? null;
}

/** The node the keyboard is on: the remembered one while it is drawn, else its nearest drawn ancestor, else the first. */
export function currentNode(graph: StudyGraph, order: readonly string[], wanted: string | null): string | null {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  let node = wanted === null ? undefined : byId.get(wanted);
  while (node !== undefined) {
    if (order.includes(node.id)) return node.id;
    node = node.parent === null ? undefined : byId.get(node.parent);
  }
  return order[0] ?? null;
}
