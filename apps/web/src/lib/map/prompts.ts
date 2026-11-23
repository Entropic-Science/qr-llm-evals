import type { CardItem, CardItemsResponse } from "@qr-evals/contracts";
import { fixedPlanSentences, fixedPlanUse } from "./fixed-plan";
import type { MapNode, StudyGraph } from "./graph";

// The prompts of an eval, as the study map lists them: what a page of them is, which of them the study runs,
// what to say when they cannot be listed, and what happens to one of them on its way to a verdict.
// Pure: the list and the pane only draw what this says.

/** Prompts asked for at a time. The route allows 100. */
export const PROMPT_PAGE = 50;
/** The height of a row of the list, in its own px: the rows are windowed, so every row is this tall. */
export const PROMPT_ROW_H = 28;
/** The most rows the list keeps mounted, whatever the eval holds. */
export const PROMPT_ROWS_MOUNTED = 40;

export type Told = NonNullable<CardItemsResponse["told"]>;
export type PromptSource = CardItemsResponse["source"];

/** The told cells of a told/actual eval, in the order they are offered. `says` is what the model is told. */
export const TOLD_CELLS: readonly { value: Told; label: string; says: string }[] = [
  { value: "told_nothing", label: "Told nothing", says: "The prompt says nothing about how the answer is sampled." },
  { value: "told_qrng", label: "Told quantum", says: "The prompt says the answer is sampled from a hardware quantum random number generator." },
  { value: "told_prng", label: "Told pseudo-random", says: "The prompt says the answer is sampled from a seeded pseudo-random number generator." },
];
export const DEFAULT_TOLD: Told = "told_nothing";

/** Why the prompts of an eval are not listed. It is a normal state of the eval, not a failure of the page. */
export function sourceSentence(source: PromptSource, reason: string | null): string {
  switch (source) {
    case "derived_at_run": return "The prompts are built from this study's own generations at run time.";
    case "remote_dataset": return "The prompts are downloaded at run time from the pinned revision of a remote dataset.";
    case "inspect_task": return "The prompts are inside the Inspect task and cannot be read here.";
    case "no_prompts": return "This measure reads the tokens the study generates; it has no prompts of its own.";
    case "error": return `The prompts could not be read: ${(reason ?? "the task module gave no reason").replace(/\.$/, "")}.`;
    default: return "The prompts are listed below.";
  }
}

/** What to say when the request for the prompts failed. `status` is the HTTP status, absent when nothing answered. */
export function failureSentence(error: { status?: number; message?: string } | null | undefined): string {
  const status = error?.status;
  if (status === undefined) return "Prompts need the run service. It is not reachable.";
  if (status === 404) return "The run service does not know this eval, so its prompts cannot be listed.";
  if (status >= 500) return "Prompts need the run service. It answered with an error.";
  return `The run service refused the request for the prompts: ${(error?.message ?? `status ${status}`).replace(/\.$/, "")}.`;
}

const ROLE_LABELS: Readonly<Record<string, string>> = { system: "System", user: "User", assistant: "Assistant", tool: "Tool" };

/** A message's role, as a heading. */
export function roleLabel(role: string | undefined): string {
  if (!role) return "Message";
  return ROLE_LABELS[role] ?? `${role.charAt(0).toUpperCase()}${role.slice(1)}`;
}

/** The turns of a prompt, each with its role and its text. */
export function turnsOf(item: Pick<CardItem, "messages">): { role: string; content: string }[] {
  return item.messages.map((message) => ({ role: message["role"] ?? "", content: message["content"] ?? "" }));
}

/** The lines with words of what the user says in a prompt, failing that of all its turns. */
function spokenLines(item: Pick<CardItem, "messages">): string[] {
  const turns = turnsOf(item);
  const lines = (from: typeof turns) => from.flatMap((turn) => turn.content.split(/\r?\n/).map((part) => part.trim()).filter((part) => part !== ""));
  const spoken = lines(turns.filter((turn) => turn.role === "user"));
  return spoken.length > 0 ? spoken : lines(turns);
}

/**
 * How many opening lines every one of these prompts shares (a preamble all of them start with). Such lines
 * tell no prompt from another, so the list starts each row after them. 0 for fewer than two prompts.
 */
export function sharedLead(items: readonly Pick<CardItem, "messages">[]): number {
  if (items.length < 2) return 0;
  const all = items.map(spokenLines);
  let shared = 0;
  // A prompt keeps at least one line of its own.
  while (all.every((lines) => lines.length > shared + 1 && lines[shared] === all[0]![shared])) shared += 1;
  return shared;
}

/**
 * The first line of a prompt for its row in the list: the first line with words of what the user says,
 * failing that of any turn. `skip` leaves out that many opening lines (see `sharedLead`).
 */
export function firstLine(item: Pick<CardItem, "messages">, skip = 0): string {
  const lines = spokenLines(item);
  return lines[Math.min(skip, Math.max(0, lines.length - 1))] ?? "";
}

/**
 * The length a truncated prompt was cut at, in characters: a turn that was cut is exactly as long as the
 * service's limit and no turn is longer, so the longest turn says what the limit is. `null` when nothing was cut.
 */
export function cutLength(item: Pick<CardItem, "messages" | "truncated">): number | null {
  if (!item.truncated) return null;
  // Counted in code points, as the service counts them.
  return Math.max(0, ...turnsOf(item).map((turn) => [...turn.content].length));
}

/** Whether the study runs a prompt: the runner takes the first `chosen` of an eval, in the eval's own order. */
export function inStudy(index: number, chosen: number): boolean {
  return index < chosen;
}

/** Where the next page starts, or `undefined` when the last one was the end (or the eval lists nothing). */
export function nextOffset(page: Pick<CardItemsResponse, "listable" | "offset" | "total" | "items">): number | undefined {
  if (!page.listable || page.items.length === 0) return undefined;
  const next = page.offset + page.items.length;
  return next < page.total ? next : undefined;
}

/**
 * The rows of a windowed list to mount for a scroll position: at most `mounted` of the `count` that are loaded,
 * with a few above the first one in view so that scrolling back does not show a gap.
 */
export function rowWindow(scrollTop: number, count: number, rowHeight = PROMPT_ROW_H, mounted = PROMPT_ROWS_MOUNTED): { first: number; last: number } {
  const overscan = 6;
  const top = Math.max(0, Math.floor(Math.max(0, scrollTop) / rowHeight) - overscan);
  const first = Math.max(0, Math.min(top, count - mounted));
  return { first, last: Math.min(count, first + mounted) };
}

/** How a prompt of an eval goes on from being asked to a verdict. */
export interface PromptLineage {
  /** Whether the eval is one the study runs. */
  runs: boolean;
  /** The arms that answer the prompt, by kind: "the quantum arm", "3 seeded controls", … */
  arms: { count: number; parts: string[] };
  /** Answers per arm: repetitions times told cells. */
  k: number | null;
  toldCells: number;
  /** What is computed from the answers: the eval's outcomes, those that decide the study first. */
  outcomes: { id: string; label: string; decides: boolean }[];
  /** The hypotheses the answers feed, with the test of each. */
  hypotheses: { id: string; test: string; statement: string; nodeId: string }[];
  /**
   * Whether the study runs the fixed Study 1 plan. The map does not draw that plan, so nothing is said about
   * which outcomes decide; `fixedPlan` says what the plan does with them.
   */
  legacy: boolean;
  /**
   * On the fixed Study 1 plan: what that plan does with the eval's outcomes, a sentence per kind of use (tested
   * as a primary outcome, tested as a secondary one, or reported as exploratory). Empty in a designed study.
   */
  fixedPlan: string[];
}

const plural = (count: number, one: string, many: string) => (count === 1 ? `the ${one}` : `${count} ${many}`);
/** A hypothesis's statement without its own id in front ("H3: on …" is shown beside "H3" already). */
function unprefixed(statement: string, id: string): string {
  const rest = statement.startsWith(`${id}:`) ? statement.slice(id.length + 1).trim() : statement;
  return `${rest.charAt(0).toUpperCase()}${rest.slice(1)}`;
}

/**
 * What happens to a prompt of the eval `cardId`: which arms answer it and how often, what is read off the
 * answers, and which hypotheses that feeds. Read off the graph, so it says only what the map draws; of a study on
 * the fixed Study 1 plan, which the map does not draw, it adds what that plan does with the outcomes.
 */
export function promptLineage(graph: Pick<StudyGraph, "nodes"> & Partial<Pick<StudyGraph, "legacy">>, cardId: string): PromptLineage {
  const card = graph.nodes.find((node) => node.id === `card:${cardId}`);
  const ref = card?.ref.type === "card" ? card.ref : null;
  const running = (node: MapNode) => node.ref.type === "arm" && node.ref.available;
  const arms = graph.nodes.filter(running);
  const of = (kind: MapNode["kind"]) => arms.filter((node) => node.kind === kind).length;
  const parts: string[] = [];
  if (of("quantum_arm") > 0) parts.push(plural(of("quantum_arm"), "quantum arm", "quantum arms"));
  if (of("seeded_arm") > 0) parts.push(plural(of("seeded_arm"), "seeded control", "seeded controls"));
  if (of("positive_control") > 0) parts.push(plural(of("positive_control"), "biased positive control", "biased positive controls"));
  if (of("supervised_arm") > 0) parts.push(plural(of("supervised_arm"), "observed quantum arm", "observed quantum arms"));
  if (of("other_arm") > 0) parts.push(plural(of("other_arm"), "other arm", "other arms"));

  const endpoints = graph.nodes.filter((node) => node.ref.type === "endpoint" && node.ref.cardId === cardId);
  const outcomes = endpoints
    .map((node) => ({ id: node.id, label: node.label, decides: node.ref.type === "endpoint" && node.ref.role === "confirmatory" && node.hypotheses.length > 0 }))
    .sort((a, b) => Number(b.decides) - Number(a.decides));
  const hypotheses = graph.nodes.flatMap((node) => (
    node.ref.type === "test" && node.ref.hypothesis.card_id === cardId
      ? [{ id: node.ref.hypothesis.id, test: node.label, statement: unprefixed(node.ref.hypothesis.statement, node.ref.hypothesis.id), nodeId: node.id }]
      : []
  ));
  const legacy = graph.legacy === true;
  const tier = ref?.tier ?? null;
  const uses = endpoints.flatMap((node) => (node.ref.type === "endpoint" ? [{ label: node.label, use: fixedPlanUse(cardId, node.ref.endpoint, tier) }] : []));
  return {
    runs: ref !== null && ref.selected,
    arms: { count: arms.length, parts },
    k: ref?.k ?? null,
    toldCells: ref?.toldCells ?? 1,
    outcomes,
    hypotheses,
    legacy,
    fixedPlan: legacy ? fixedPlanSentences(uses, tier) : [],
  };
}

/** "20 times", "once", "20 times in each of the 3 told cells". */
export function answersText(k: number | null, toldCells: number): string {
  const times = k === null || k === 1 ? "once" : `${k} times`;
  return toldCells > 1 ? `${times} in each of the ${toldCells} told cells` : times;
}
