import type { Arm, EvalCard, HypothesisPlan } from "@qr-evals/contracts";
import type { WizardStudyConfig } from "../config-store";
import {
  cardMargin, cellArms, compared, draftHypotheses, formatG, ineligibleReason, laneKey, metaFor, plannedTest,
  positiveControlShift, reachableVerdicts, sourceCells, type Cell, type Unreachable, type Verdict,
} from "../design";
import type { Problem, StepId } from "../problems";
import type { Rec, RecState } from "../recommendations";
import { observedArmInFixedPlan } from "./fixed-plan";
import type { ZoomTier } from "./viewport";

// The study as a graph: what the study map draws.
//
// Seven stations, left to right: the question, the arms, the prompts each arm answers, what is read off the
// answers, how a difference is tested, the checks before a conclusion, and the verdicts. One lane per
// (model, sampling) pair; the evals sit in one column across the lanes, because every arm answers the same
// prompts. Nothing here is drawn or measured: a node is a label, its facts, the settings and problems that sit
// on it, and its place in the hierarchy; `layout.ts` gives it a box.
//
// The graph only restates the study. Tests and verdicts are the hypotheses `draftHypotheses` generates; a
// study without a design block has none, and gets an invitation in their place rather than invented ones.
//
// Edges run from one station to a later one, in short hops: the question to the quantum arm and the seeded
// deck, those to every eval, an eval to its outcomes, an outcome to its test, a test through the negative
// control of its lane to its verdict. Each edge names the hypotheses it carries, because an eval is shared by
// every lane: that is what lets `lineage` follow one hypothesis through a shared node. The Holm family and
// the completeness gate apply to every test alike; their edges are `implied`, held but not drawn as lines.
//
// A node folds into its parent (a seeded run into the deck, an eval into its tier, an outcome into its eval,
// a verdict into its test) unless it is pinned: what decides the study is always in view.

export type StationId = "question" | "arms" | "prompts" | "measures" | "tests" | "gates" | "verdict";

export interface Station { id: StationId; label: string; sentence: string }

/** The stations in reading order, each with the one sentence the story zoom shows. */
export const STATIONS: readonly Station[] = [
  { id: "question", label: "Question", sentence: "What the study asks." },
  { id: "arms", label: "Arms", sentence: "What is compared: quantum against seeded pseudo-random." },
  { id: "prompts", label: "Prompts × repetitions", sentence: "What each arm is asked, and how many times." },
  { id: "measures", label: "Measures", sentence: "What is read off each answer." },
  { id: "tests", label: "Tests", sentence: "How a difference is tested." },
  { id: "gates", label: "Gates", sentence: "Checks that must pass before any conclusion." },
  { id: "verdict", label: "Verdict", sentence: "The answers the study can return." },
];

export type NodeKind =
  | "study" | "quantum_arm" | "supervised_arm" | "control_deck" | "seeded_arm" | "positive_control" | "other_arm"
  | "tier0_band" | "card" | "card_group" | "ghost_stack" | "endpoint" | "test" | "family" | "gate" | "verdict";

/** Whose ink a stroke is drawn in: the quantum arm, the seeded controls, the biased positive control, or neither. */
export type Tone = "quantum" | "control" | "positive" | "neutral";

/** One answer a hypothesis can end with, and whether this study can reach it. */
export interface VerdictStamp {
  verdict: Verdict;
  label: string;
  reachable: boolean;
  why: Unreachable | null;
  /** Why it cannot be reached, as a sentence; `null` when it can. */
  reason: string | null;
  /** The rows of the decision matrix that return this verdict, as their conditions. */
  rules: string[];
}

/** What a node stands for in the study, for the view that draws its particulars. */
export type NodeRef =
  | { type: "study"; claim: string | null; comparisons: number; tests: number }
  | { type: "arm"; armId: string; source: Arm["source"]; seed: number | null; epsilon: number | null; available: boolean; observed: boolean }
  | { type: "deck"; armIds: string[]; seeds: (number | null)[] }
  | { type: "tier0"; cardIds: string[] }
  | { type: "group"; tier: number; cardIds: string[] }
  | {
    type: "card"; cardId: string; known: boolean; selected: boolean; tier: number | null;
    /** Prompts the study runs, and how many the eval has. */
    prompts: number; datasetPrompts: number;
    k: number | null; toldCells: number; tokensPerArm: number; decides: boolean; judged: boolean;
  }
  | { type: "endpoint"; cardId: string; endpoint: string; role: "confirmatory" | "exploratory"; headline: boolean; margin: number | null; test: string | null }
  | { type: "test"; hypothesis: HypothesisPlan }
  | { type: "family"; tests: number; alpha: number | null }
  | { type: "gate"; gate: "negative" | "positive" | "completeness"; epsilon: number | null }
  | { type: "verdict"; hypothesis: HypothesisPlan; stamps: VerdictStamp[] }
  | { type: "ghost"; invites: "arms" | "evals" | "outcomes" | "design" };

export interface MapNode {
  /** Also the `nodeId` of every `Rec` and `Problem` that sits on the node. */
  id: string;
  kind: NodeKind;
  station: StationId;
  /** The lane key (`{model}/{sampling}`), or `null` for what all lanes share. */
  lane: string | null;
  /** The node this one folds into, or `null`. */
  parent: string | null;
  /** Drawn even while its parent is folded. Outcomes that decide the study are never hidden. */
  pinned: boolean;
  children: string[];
  /** Whether unfolding the node reveals anything: it has a child that is not pinned. */
  expandable: boolean;
  /** The coarsest zoom at which the node is drawn. */
  zoom: "story" | "design";
  label: string;
  sub: string;
  /** What the node says at each zoom: one sentence, the numbers, everything. */
  facts: { story: string; design: string[]; detail: string[] };
  tone: Tone;
  /** The worst state among `recs`, `locked` aside; `null` when nothing on the node is rated. */
  state: RecState | null;
  severity: "error" | "warning" | null;
  recs: Rec[];
  problems: Problem[];
  /** The settings edited on this node, as `Rec` paths and study key paths (`panelFor` reads both). */
  settings: string[];
  /** The hypotheses the node takes part in. */
  hypotheses: string[];
  stepId: StepId;
  /** The node's place in the flow as one sentence: for a hover note and `aria-describedby`. */
  describes: string;
  /** Why the node is a placeholder (nothing runs behind it), or `null`. */
  ghost: string | null;
  ref: NodeRef;
}

export type EdgeKind = "flow" | "measures" | "compares" | "governs" | "gates" | "decides";

export interface MapEdge {
  /** `{from}>{to}`. */
  id: string;
  from: string;
  to: string;
  kind: EdgeKind;
  tone: Tone;
  /**
   * Exploratory, switched off, or not in force. The link from a biased arm to its positive-control gate is
   * dashed only while the arm is switched off or no verdict waits for the gate.
   */
  dashed: boolean;
  /**
   * Holds without being a line of its own: one end is a rule over everything it touches (the Holm family over
   * the tests, the completeness gate between every test and its verdict). Lineage follows it; the layout gives
   * it no anchors.
   */
  implied: boolean;
  /** The hypotheses the edge carries. Empty for exploratory material. */
  hypotheses: string[];
}

export interface Lane {
  key: string;
  model: string;
  sampling: string;
  label: string;
  /** Whether the pair is a comparison: it has an available, unobserved quantum arm. */
  compared: boolean;
}

export interface StudyGraph {
  nodes: MapNode[];
  edges: MapEdge[];
  lanes: Lane[];
  /** Whether the study has no design block and runs on the fixed Study 1 plan. */
  legacy: boolean;
}

// ── copy ─────────────────────────────────────────────────────────────────────

/** Evals beyond this many fold into one node per tier, unless one of their outcomes decides the study. */
export const FOLD_ABOVE = 12;

export const LEGACY_BANNER = "This draft uses the fixed Study 1 plan. Tests, gates and verdicts are not drawn.";

/** The decision matrix, as `VERDICT_RULES` in `cards/design.py` states it (checked against `fixtures/design.json`). */
export const VERDICT_RULES: readonly { number: number; verdict: Verdict; condition: string }[] = [
  { number: 1, verdict: "unavailable", condition: "The comparison could not be computed." },
  { number: 2, verdict: "invalid", condition: "Fewer than 90% of the planned pairs were analysed." },
  { number: 3, verdict: "invalid", condition: "A seed-vs-seed control of the cell is missing, differs after Holm correction, or could not have failed." },
  { number: 4, verdict: "effect_within_margin", condition: "pD < alpha, a margin is registered, and pE < alpha." },
  { number: 5, verdict: "effect", condition: "pD < alpha." },
  { number: 6, verdict: "equivalent", condition: "The claim includes equivalence, a margin is registered, pE < alpha, and the positive control was detected." },
  { number: 7, verdict: "inconclusive", condition: "Otherwise." },
];

/** The verdicts in the order the map stamps them: what was found first, what went wrong last. */
export const VERDICT_ORDER: readonly Verdict[] = ["effect", "effect_within_margin", "equivalent", "inconclusive", "invalid", "unavailable"];

/** The same words the results page stamps a verdict with. */
export const VERDICT_LABELS: Readonly<Record<Verdict, string>> = {
  effect: "Effect",
  effect_within_margin: "Effect within margin",
  equivalent: "Equivalent",
  inconclusive: "Inconclusive",
  invalid: "Invalid",
  unavailable: "Unavailable",
};

/** Why a verdict is out of reach. The first four are `INCONCLUSIVE_REASON_TEXT` of `cards/design.py`. */
export const UNREACHABLE_TEXT: Readonly<Record<string, string>> = {
  equivalence_not_claimed: "the study registered no equivalence claim",
  equivalence_not_testable: "this test has no equivalence margin",
  no_margin: "no margin was registered for this outcome",
  positive_control_absent: "the positive control was not run",
  below_n_floor: "too few prompts to reach significance at any effect size",
  control_cannot_fail: "fewer prompts than the seed-against-seed control is asked for, so the hypothesis is invalid",
};

const TEST_LABELS: Readonly<Record<string, string>> = {
  paired_permutation: "Paired sign-flip test",
  ks_two_sample: "Two-sample KS test",
  mmd_hamming_permutation: "MMD permutation test",
};
const SAMPLING_LABELS: Readonly<Record<string, string>> = {
  creative: "Creative sampling",
  t1_minp: "Temperature 1 + min-p 0.10",
  greedy: "Greedy",
};
const OTHER_SOURCE_LABELS: Readonly<Record<string, string>> = {
  prng_system: "System pseudo-random",
  provider: "Provider's own sampling",
};
const LEVEL_TEXT: Readonly<Record<string, string>> = {
  sample: "one value per prompt",
  prompt_group: "one value per prompt group",
  token_stream: "the pooled tokens of the study's generations",
};
/** Which tokens a test on pooled tokens runs on (`_tier0_for` and `independent_streams` in the backend). */
export const POOLED_TOKENS = "The tokens of every eval that generates are pooled per side. Of an eval with told cells, only the told-nothing cell is used.";
/** What the audits of the token stream read. They do not read every token: see `POOLED_TOKENS`. */
export const TOKEN_AUDITS = "These audits read the tokens the study generates; they have no prompts of their own.";
const CLAIM_SUB: Readonly<Record<string, string>> = {
  difference: "claims a difference only",
  difference_or_equivalence: "claims a difference, or its absence within a margin",
};
const CLAIM_STORY: Readonly<Record<string, string>> = {
  difference: "Does quantum sampling differ from seeded pseudo-random sampling?",
  difference_or_equivalence: "Does quantum sampling differ from seeded pseudo-random sampling, or is any difference smaller than a stated margin?",
};
const ENVELOPE_PINS = "amplifier zscore_mean, calibration 0, prefetch off, 10,000 entropy bytes per token";
/** Whether an arm samples through the amplifier the harness pins (`models/envelope.py`): not a provider's own sampling, not greedy. */
const throughAmplifier = (arm: Arm) => arm.source !== "provider" && arm.sampling !== "greedy";
/** What holds of one arm's sampling envelope. Only the arms that are compared share it. */
function envelopeFact(arm: Arm): string {
  if (arm.source === "provider") return "The provider samples this arm itself: the sampling envelope the harness pins does not apply to it.";
  if (arm.sampling === "greedy") return "A greedy arm takes the most likely token and draws no randomness: the sampling envelope does not apply to it.";
  if (arm.source === "prng_epsilon") {
    return `It has the sampling envelope of the compared arms (${ENVELOPE_PINS}), with one change: the amplifier's reference mean is lowered by ε standard errors. That is the bias.`;
  }
  return `Its sampling envelope is the one every compared arm has: ${ENVELOPE_PINS}.`;
}
const PREVIEW = "These numbers are a preview; the registered ones are the server's.";

/** What the seed-against-seed control needs of one hypothesis, and whether the study gives it. */
function controlFloorFact(id: string, n: number, floor: number): string {
  return n < floor
    ? `With ${plural(n, "prompt")} it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed: it is asked for at least ${floor}. ${id} could only come out invalid.`
    : `The seed-against-seed control can fail from ${floor} prompts on; the study plans ${n}.`;
}

/** Worst first. `locked` is not a rating and never colours a node. */
const STATE_ORDER: readonly RecState[] = ["invalid", "insufficient", "marginal", "unknown", "custom", "sufficient", "recommended"];
const STATION_INDEX = new Map(STATIONS.map((station, index) => [station.id, index]));

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;
const sentence = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
const count = (value: number) => value.toLocaleString("en-US");
const unique = <T>(values: readonly T[]): T[] => [...new Set(values)];
const refKey = (cardId: string, endpoint: string) => `${cardId}/${endpoint}`;
const titleOf = (card: EvalCard) => (card as EvalCard & { title?: string }).title ?? card.id;
const isAvailable = (arm: Arm) => (arm.availability ?? "available") === "available";
const isObserved = (arm: Arm) => (arm.observer_mode ?? "unsupervised") === "supervised";
const sameLevel = (a: number | null | undefined, b: number | null | undefined) =>
  a !== null && a !== undefined && b !== null && b !== undefined && Math.abs(a - b) < 1e-12;

/** A list as prose: "A", "A and B", "A, B and C", "A, B, C and 4 more". */
function listed(names: readonly string[], joiner: "and" | "or" = "and"): string {
  const shown = names.length > 4 ? [...names.slice(0, 3), `${names.length - 3} more`] : [...names];
  if (shown.length <= 1) return shown[0] ?? "";
  return `${shown.slice(0, -1).join(", ")} ${joiner} ${shown.at(-1)}`;
}

/** The worst state in a list of states, `locked` aside. */
export function worstState(states: readonly (RecState | null)[]): RecState | null {
  return STATE_ORDER.find((state) => states.includes(state)) ?? null;
}

function worstSeverity(problems: readonly Problem[]): MapNode["severity"] {
  if (problems.some((problem) => problem.severity === "error")) return "error";
  return problems.length > 0 ? "warning" : null;
}

// ── building ─────────────────────────────────────────────────────────────────

type Draft = Pick<MapNode, "id" | "kind" | "station" | "label" | "sub" | "facts" | "stepId" | "ref">
  & Partial<Pick<MapNode, "lane" | "parent" | "pinned" | "zoom" | "tone" | "ghost" | "hypotheses" | "settings">>;

/**
 * The graph of a study.
 *
 * `problems` and `recs` are the lists the setup pages already hold (`problems()` and `rate()`); each sits on the
 * node its `nodeId` names. The inputs are read, never changed.
 */
export function buildStudyGraph(
  config: WizardStudyConfig, cards: readonly EvalCard[], problems: readonly Problem[], recs: readonly Rec[],
): StudyGraph {
  const registry = new Map(cards.map((card) => [card.id, card]));
  const design = config.design ?? null;
  const hypotheses = draftHypotheses(config, cards);
  const lanes = lanesOf(config);
  const hidsOfLane = (key: string) => hypotheses.filter((hypothesis) => laneKey([hypothesis.model, hypothesis.sampling]) === key).map((hypothesis) => hypothesis.id);
  const allHids = hypotheses.map((hypothesis) => hypothesis.id);
  const problemsAt = (id: string) => problems.filter((problem) => problem.nodeId === id);

  const nodes: MapNode[] = [];
  const byId = new Map<string, MapNode>();
  const add = (draft: Draft): MapNode | null => {
    // Ids are unique. A study with two arms of one id draws the first.
    if (byId.has(draft.id)) return null;
    const node: MapNode = {
      lane: null, parent: null, pinned: false, zoom: "design", tone: "neutral", ghost: null, hypotheses: [], settings: [],
      ...draft,
      children: [], expandable: false, state: null, severity: null, recs: [], problems: [], describes: "",
    };
    nodes.push(node);
    byId.set(node.id, node);
    return node;
  };

  const edges: MapEdge[] = [];
  const edgeById = new Map<string, MapEdge>();
  const link = (from: string, to: string, kind: EdgeKind, tone: Tone, hids: readonly string[], dashed = false, implied = false) => {
    if (!byId.has(from) || !byId.has(to) || from === to) return;
    const id = `${from}>${to}`;
    const known = edgeById.get(id);
    if (known) {
      known.hypotheses = unique([...known.hypotheses, ...hids]);
      known.dashed = known.dashed && dashed;
      known.implied = known.implied && implied;
      return;
    }
    const edge: MapEdge = { id, from, to, kind, tone, dashed, implied, hypotheses: [...hids] };
    edges.push(edge);
    edgeById.set(id, edge);
  };

  // ── 1. the question ──
  const comparisons = lanes.filter((lane) => lane.compared).length;
  const claim = design?.claim ?? null;
  const expectations = (config.expectations ?? "").trim();
  add({
    id: "study", kind: "study", station: "question", zoom: "story", stepId: "design",
    label: config.title.trim() || "Untitled study",
    sub: claim === null ? "fixed Study 1 plan" : CLAIM_SUB[claim] ?? claim,
    facts: {
      story: claim === null
        ? "Quantum sampling is compared with seeded pseudo-random sampling on the fixed Study 1 plan."
        : CLAIM_STORY[claim] ?? "What the study asks.",
      design: [
        comparisons === 1 ? "One pair of model and sampling preset is compared." : `${comparisons} pairs of model and sampling preset are compared, each on its own.`,
        design === null ? "No design is declared." : `${hypotheses.length} ${hypotheses.length === 1 ? "test decides" : "tests decide"} the study.`,
        expectations ? "Expectations are recorded." : "No expectations are recorded.",
      ],
      detail: [
        `Authors: ${listed(config.authors) || "none"}.`,
        `Contact: ${config.contact.trim() || "none"}.`,
        `The report is written by ${config.narrative_model.id}; it may only restate numbers from the tables.`,
        "Pilot runs are never confirmatory and do not set margins.",
      ],
    },
    hypotheses: allHids,
    settings: ["title", "expectations", "authors[]", "contact", "narrative_model"],
    ref: { type: "study", claim, comparisons, tests: hypotheses.length },
  });

  // ── 2. the arms ──
  const epsilon = design?.sensitivity_epsilon ?? null;
  /** Per lane: the nodes whose answers reach the evals, with the ink they are drawn in. */
  const feeders = new Map<string, { id: string; tone: Tone }[]>();
  /** The arms that do not sample through the pinned amplifier: what is said of the envelope is not said of them. */
  const outsideEnvelope = new Set(config.arms.filter((arm) => !throughAmplifier(arm)).map((arm) => `arm:${arm.id}`));
  for (const lane of lanes) {
    const cell: Cell = [lane.model, lane.sampling];
    const arms = cellArms(config, cell);
    const hids = hidsOfLane(lane.key);
    const feeds: { id: string; tone: Tone }[] = [];
    feeders.set(lane.key, feeds);
    const armNode = (arm: Arm, kind: NodeKind, draft: Pick<Draft, "label" | "sub" | "facts"> & Partial<Draft>) => add({
      id: `arm:${arm.id}`, kind, station: "arms", lane: lane.key, stepId: "compare", settings: ["arms"],
      ghost: isAvailable(arm) ? null : "Switched off: the arm is listed, not run.",
      ...draft,
      facts: {
        ...draft.facts,
        detail: [
          ...draft.facts.detail,
          `Arm ${arm.id} in ${lane.label}.`,
          ...(isAvailable(arm) ? [] : [arm.unavailable_reason ? `Switched off: ${arm.unavailable_reason}` : "Switched off."]),
          `The deterministic setting is ${arm.deterministic ? "on" : "off"}.`,
          envelopeFact(arm),
        ],
      },
      ref: { type: "arm", armId: arm.id, source: arm.source, seed: arm.study_seed ?? null, epsilon: arm.epsilon ?? null, available: isAvailable(arm), observed: isObserved(arm) },
    });

    for (const arm of arms.filter((item) => item.source === "qrng" && !isObserved(item))) {
      const node = armNode(arm, "quantum_arm", {
        label: "Quantum", sub: "fresh randomness for every token", tone: "quantum", zoom: "story", hypotheses: hids,
        facts: { story: "The quantum arm draws fresh quantum randomness for every token.", design: [`In ${lane.label}.`], detail: [] },
      });
      if (node && compared(arm, "qrng")) feeds.push({ id: node.id, tone: "quantum" });
    }
    for (const arm of arms.filter((item) => item.source === "qrng" && isObserved(item))) {
      // Observed against unobserved is exploratory in a designed study. The fixed Study 1 plan registers that
      // contrast, so on a study without a design the node says what that plan does with the arm.
      const fixed = design === null ? observedArmInFixedPlan(arm, arms) : null;
      armNode(arm, "supervised_arm", {
        label: "Observed quantum", sub: fixed?.sub ?? "exploratory, no verdict", tone: "quantum", settings: ["supervised", "supervised.display"],
        facts: {
          story: `An observed quantum arm. ${fixed?.story ?? "Its comparison is exploratory and gets no verdict."}`,
          design: [`In ${lane.label}.`, fixed?.design ?? "Compared with the unobserved quantum arm."],
          detail: [],
        },
      });
    }

    const seeded = arms.filter((item) => item.source === "prng_seeded");
    if (seeded.length > 0 || lane.compared) {
      const running = seeded.filter((arm) => compared(arm, "prng_seeded"));
      const seeds = unique(running.map((arm) => arm.study_seed ?? null));
      const pairs = (seeds.length * (seeds.length - 1)) / 2;
      const deckId = `deck:${lane.key}`;
      add({
        id: deckId, kind: "control_deck", station: "arms", lane: lane.key, stepId: "compare", tone: "control", zoom: "story", hypotheses: hids,
        label: "Seeded controls",
        sub: seeds.length === 0 ? "no seeded run" : `${plural(seeds.length, "seed")}: ${seeds.map((seed) => seed ?? "none").join(", ")}`,
        ghost: seeded.length === 0 ? "This comparison has no seeded control run." : null,
        facts: {
          story: `${plural(seeds.length, "seeded pseudo-random run")}. Their mean is the comparison side; pairs of them are the negative control.`,
          design: [`In ${lane.label}.`, `${plural(pairs, "seed pair")} for the negative control.`],
          detail: seeded.length > running.length ? [`${plural(seeded.length - running.length, "seeded arm")} of this comparison ${seeded.length - running.length === 1 ? "is" : "are"} switched off.`] : [],
        },
        ref: { type: "deck", armIds: seeded.map((arm) => arm.id), seeds: seeded.map((arm) => arm.study_seed ?? null) },
      });
      if (running.length > 0) feeds.push({ id: deckId, tone: "control" });
      for (const arm of seeded) {
        armNode(arm, "seeded_arm", {
          parent: deckId, tone: "control", hypotheses: compared(arm, "prng_seeded") ? hids : [],
          label: `Seed ${arm.study_seed ?? "none"}`, sub: "seeded pseudo-random run",
          facts: { story: "One seeded pseudo-random run of the control deck.", design: [`In ${lane.label}.`], detail: [] },
        });
      }
    }

    const biased = arms.filter((item) => item.source === "prng_epsilon")
      .sort((a, b) => (a.epsilon ?? 0) - (b.epsilon ?? 0));
    for (const arm of biased) {
      const level = arm.epsilon === null || arm.epsilon === undefined ? "ε not set" : `ε ${arm.epsilon.toFixed(2)}`;
      const named = sameLevel(arm.epsilon, epsilon);
      armNode(arm, "positive_control", {
        label: level, sub: isAvailable(arm) ? "biased on purpose" : "biased on purpose · planned, off", tone: "positive",
        facts: {
          story: "A deliberately biased pseudo-random arm. If the analysis cannot detect it, it cannot claim that nothing is there.",
          design: [
            named ? "The positive control this design has to detect." : "Not the positive control of this design.",
            `Runs on seed ${arm.study_seed ?? "none"}.`,
          ],
          detail: [],
        },
      });
    }
    for (const arm of arms.filter((item) => item.source === "prng_system" || item.source === "provider")) {
      armNode(arm, "other_arm", {
        label: OTHER_SOURCE_LABELS[arm.source] ?? arm.source, sub: "not part of a comparison",
        facts: { story: "This arm is run, but no comparison of the plan uses it.", design: [`In ${lane.label}.`], detail: [] },
      });
    }
  }
  if (lanes.length === 0) {
    add({
      id: "ghost:arms", kind: "ghost_stack", station: "arms", zoom: "story", stepId: "compare", settings: ["roster.cells"],
      label: "Tick a model and preset", sub: "nothing is compared yet", ghost: "No comparison is ticked.",
      facts: { story: "No comparison is ticked.", design: ["Each ticked pair of model and sampling preset is its own comparison."], detail: [] },
      ref: { type: "ghost", invites: "arms" },
    });
  }

  // ── 3. the prompts, and 4. what is read off the answers ──
  const selections = new Map(config.cards.map((selection) => [selection.card_id, selection]));
  const declared = (design?.confirmatory ?? []).map((ref) => refKey(ref.card_id, ref.endpoint));
  const deciding = new Set((design?.confirmatory ?? []).map((ref) => ref.card_id));
  const known = [...selections.keys()].flatMap((id) => registry.get(id) ?? []);
  const fold = selections.size > FOLD_ABOVE;
  const byTierThenId = (a: EvalCard, b: EvalCard) => a.tier - b.tier || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  // Evals that decide the study come first, in the order the design names them: the hypotheses are numbered in
  // that order, so an eval, its outcome, its test and its verdict line up.
  const named = (design?.confirmatory ?? []).map((ref) => ref.card_id);
  const rank = (card: EvalCard) => (deciding.has(card.id) ? named.indexOf(card.id) : named.length);
  const decidesFirst = (a: EvalCard, b: EvalCard) => rank(a) - rank(b) || byTierThenId(a, b);
  // A study without a design runs the fixed Study 1 plan, which has primary outcomes of its own that the map
  // does not draw. Nothing is called exploratory there: the map says the plan decides, and leaves it at that.
  const legacy = design === null;

  const cardNode = (card: EvalCard, parent: string | null, selected = true) => {
    const selection = selections.get(card.id);
    const prompts = selection?.overrides?.items ?? card.dataset.items;
    const k = selection?.overrides?.k ?? card.k ?? null;
    const toldCells = card.told_actual ? 3 : 1;
    const tokensPerArm = card.est_tokens_per_sample > 0 ? prompts * (k ?? 1) * toldCells * card.est_tokens_per_sample : 0;
    const decides = deciding.has(card.id);
    const judged = card.judge?.kind === "llm" && (card.judge.model ?? "study_judge") === "study_judge";
    const own = hypotheses.filter((hypothesis) => hypothesis.card_id === card.id);
    const outcomes = unique(own.map((hypothesis) => hypothesis.label));
    const sub = prompts > 0 ? (k === null ? plural(prompts, "prompt") : `${plural(prompts, "prompt")} × k = ${k}`)
      : card.tier === 0 ? "the generated tokens" : "no prompts of its own";
    // Where more repetitions would make the eval's prompts enough: `sample_size.k_recommended`, as the Design step states it.
    const moreK = k === null ? [] : unique(own.flatMap((hypothesis) => {
      const enough = hypothesis.sample_size.k_recommended ?? null;
      return enough !== null && enough > k ? [`${hypothesis.label}: with k = ${enough}, the ${plural(card.dataset.items, "prompt")} of the eval are enough.`] : [];
    }));
    add({
      id: `card:${card.id}`, kind: "card", station: "prompts", parent, pinned: decides && parent !== null, stepId: decides ? "design" : "measure",
      zoom: decides ? "story" : "design", hypotheses: own.map((hypothesis) => hypothesis.id),
      label: titleOf(card), sub: selected ? sub : "not selected",
      ghost: selected ? null : "This eval is not selected, but the design names one of its outcomes.",
      facts: {
        story: !selected ? "This eval is not selected, but the design names one of its outcomes."
          : outcomes.length > 0 ? `Decides the study on ${listed(outcomes)}.`
            : decides ? "Named as deciding the study, but its outcome cannot be tested as configured."
              : legacy ? "Analysed as the fixed Study 1 plan states. The map does not draw the tests of that plan."
                : "Measured for exploration: no outcome of this eval decides the study.",
        design: [
          ...(prompts > 0 ? [k === null ? `${plural(prompts, "prompt")} per arm.` : `${plural(prompts, "prompt")}, each answered ${k} times per arm.`] : [sentence(sub)]),
          ...(card.told_actual ? ["Every prompt runs in three cells: told quantum, told pseudo-random, told nothing."] : []),
          ...(tokensPerArm > 0 ? [`About ${count(tokensPerArm)} generated tokens per arm.`] : []),
          ...moreK,
        ],
        detail: [
          ...(prompts === 0 && card.tier === 0 ? [POOLED_TOKENS] : []),
          ...(prompts > 0 && prompts < card.dataset.items ? [`Uses the first ${prompts} of ${plural(card.dataset.items, "prompt")} in file order.`] : []),
          ...(k !== null ? ["Prompts are the unit of analysis. Repetitions sharpen each prompt's value, so fewer repetitions call for more prompts; they are never counted as prompts themselves."] : []),
          ...(judged ? ["Scored by the study's judge model."] : []),
        ],
      },
      settings: ["cards[].card_id", ...(judged ? ["judge.model"] : [])],
      ref: { type: "card", cardId: card.id, known: true, selected, tier: card.tier, prompts, datasetPrompts: card.dataset.items, k, toldCells, tokensPerArm, decides, judged },
    });
    endpointNodes(card);
  };

  /** The outcomes of a card: the ones the design names first, then its headline, then the rest. */
  const endpointNodes = (card: EvalCard) => {
    const named = declared.filter((ref) => ref.startsWith(`${card.id}/`)).map((ref) => ref.slice(card.id.length + 1));
    const margins = (design?.margins ?? []).filter((margin) => margin.card_id === card.id).map((margin) => margin.endpoint);
    for (const endpoint of unique([...named, ...card.endpoints.primary, ...(card.endpoints.secondary ?? []), ...margins])) {
      const id = `endpoint:${refKey(card.id, endpoint)}`;
      const meta = metaFor(card.id, endpoint);
      const confirmatory = named.includes(endpoint);
      const headline = card.endpoints.primary.includes(endpoint);
      const own = hypotheses.filter((hypothesis) => hypothesis.card_id === card.id && hypothesis.endpoint === endpoint);
      const why = confirmatory ? (selections.has(card.id) ? ineligibleReason(card, endpoint) : "the eval is not selected") : null;
      const margin = own[0]?.margin ?? (headline ? cardMargin(card, endpoint) : null);
      // The test named is the one a design would register. The fixed Study 1 plan has its own, which are not drawn.
      const test = !legacy && (headline || confirmatory) ? plannedTest(card, endpoint) : null;
      const place = headline ? "headline outcome" : "secondary outcome";
      add({
        id, kind: "endpoint", station: "measures", parent: `card:${card.id}`, stepId: "design",
        pinned: confirmatory || problemsAt(id).length > 0, zoom: confirmatory ? "story" : "design",
        hypotheses: own.map((hypothesis) => hypothesis.id),
        label: meta.label,
        sub: why !== null ? "named as deciding · cannot be tested"
          : confirmatory ? `decides the study · ${margin === null ? "no margin" : `±${formatG(margin)}`}`
            : legacy ? place : `${place} · exploratory`,
        facts: {
          story: why !== null ? `${meta.label} is named as deciding the study, but cannot be tested.`
            : confirmatory ? `${meta.label} decides the study.`
              : legacy ? `${meta.label} is analysed as the fixed Study 1 plan states.`
                : `${meta.label} is reported without a verdict.`,
          design: [
            ...(why !== null ? [`Cannot decide a study: ${why}.`] : []),
            ...(test !== null ? [`Test: ${TEST_LABELS[test] ?? test}.`] : []),
            ...(confirmatory && why === null ? [margin === null ? "No margin: 'equivalent' cannot be returned on this outcome." : `Smallest difference that matters: ±${formatG(margin)}.`] : []),
          ],
          detail: [
            `Unit of analysis: ${LEVEL_TEXT[meta.level] ?? meta.level}.`,
            ...(meta.range ? [`Values lie between ${formatG(meta.range[0])} and ${formatG(meta.range[1])}.`] : []),
            ...(confirmatory ? ["A margin is fixed now. It is never derived from a pilot."] : []),
          ],
        },
        settings: confirmatory ? ["design.confirmatory"] : [],
        ref: { type: "endpoint", cardId: card.id, endpoint, role: confirmatory ? "confirmatory" : "exploratory", headline, margin, test },
      });
    }
  };

  const tierZero = known.filter((card) => card.tier === 0).sort(decidesFirst);
  if (tierZero.length > 0) {
    const decide = tierZero.filter((card) => deciding.has(card.id)).length;
    add({
      id: "tier0", kind: "tier0_band", station: "prompts", stepId: "measure", zoom: decide > 0 ? "story" : "design",
      label: "Measured on the tokens", sub: plural(tierZero.length, "audit"),
      facts: {
        story: TOKEN_AUDITS,
        design: [
          decide > 0 ? `${decide} of ${plural(tierZero.length, "audit")} ${decide === 1 ? "decides" : "decide"} the study.`
            : legacy ? "The fixed Study 1 plan states which of them it tests." : "None of them decides the study.",
        ],
        detail: [sentence(listed(tierZero.map(titleOf))), POOLED_TOKENS],
      },
      ref: { type: "tier0", cardIds: tierZero.map((card) => card.id) },
    });
    for (const card of tierZero) cardNode(card, "tier0");
  }
  const others = known.filter((card) => card.tier !== 0);
  for (const card of others.filter((item) => deciding.has(item.id)).sort(decidesFirst)) cardNode(card, null);
  const exploratory = others.filter((card) => !deciding.has(card.id)).sort(byTierThenId);
  if (fold) {
    for (const tier of unique(exploratory.map((card) => card.tier))) {
      const members = exploratory.filter((card) => card.tier === tier);
      add({
        id: `group:tier${tier}`, kind: "card_group", station: "prompts", stepId: "measure",
        label: `Tier ${tier}`, sub: plural(members.length, "eval"),
        facts: {
          story: legacy ? `${plural(members.length, "eval")} of tier ${tier}.` : `${plural(members.length, "eval")} of tier ${tier}, measured for exploration.`,
          design: [legacy ? "The fixed Study 1 plan states how their outcomes are analysed." : "No outcome of these evals decides the study."],
          detail: [sentence(listed(members.map(titleOf)))],
        },
        ref: { type: "group", tier, cardIds: members.map((card) => card.id) },
      });
      for (const card of members) cardNode(card, `group:tier${tier}`);
    }
  } else {
    for (const card of exploratory) cardNode(card, null);
  }
  // An eval the registry does not have, and an eval the design names without selecting it.
  for (const id of selections.keys()) {
    if (registry.has(id)) continue;
    add({
      id: `card:${id}`, kind: "card", station: "prompts", stepId: "measure", label: id, sub: "not in the registry",
      ghost: "This eval is not in the registry.",
      facts: { story: "This eval is not in the registry.", design: ["Nothing can be planned for it."], detail: [] },
      settings: ["cards[].card_id"],
      ref: { type: "card", cardId: id, known: false, selected: true, tier: null, prompts: 0, datasetPrompts: 0, k: null, toldCells: 1, tokensPerArm: 0, decides: deciding.has(id), judged: false },
    });
  }
  const referenced = unique([...(design?.confirmatory ?? []), ...(design?.margins ?? [])].map((ref) => ref.card_id));
  for (const id of referenced) {
    if (selections.has(id)) continue;
    const card = registry.get(id);
    if (card) {
      cardNode(card, null, false);
      continue;
    }
    add({
      id: `card:${id}`, kind: "card", station: "prompts", stepId: "measure", label: id, sub: "not in the registry",
      ghost: "The design names this eval, but it is not in the registry.",
      facts: { story: "The design names this eval, but it is not in the registry.", design: ["Nothing can be planned for it."], detail: [] },
      ref: { type: "card", cardId: id, known: false, selected: false, tier: null, prompts: 0, datasetPrompts: 0, k: null, toldCells: 1, tokensPerArm: 0, decides: deciding.has(id), judged: false },
    });
    for (const endpoint of unique(declared.filter((ref) => ref.startsWith(`${id}/`)).map((ref) => ref.slice(id.length + 1)))) {
      add({
        id: `endpoint:${refKey(id, endpoint)}`, kind: "endpoint", station: "measures", parent: `card:${id}`, pinned: true, stepId: "design",
        label: endpoint, sub: "named as deciding · cannot be tested",
        facts: { story: `${endpoint} is named as deciding the study, but cannot be tested.`, design: ["Cannot decide a study: the eval is not in the registry."], detail: [] },
        settings: ["design.confirmatory"],
        ref: { type: "endpoint", cardId: id, endpoint, role: "confirmatory", headline: false, margin: null, test: null },
      });
    }
  }
  const unselected = cards.filter((card) => card.status === "implemented" && card.selectable && !selections.has(card.id)).length;
  if (selections.size === 0 || unselected > 0) {
    const none = selections.size === 0;
    add({
      id: "ghost:evals", kind: "ghost_stack", station: "prompts", stepId: "measure", zoom: none ? "story" : "design", settings: ["cards[].card_id"],
      label: none ? "Select an eval" : `+ ${unselected} more ${unselected === 1 ? "eval" : "evals"}`,
      sub: none ? "nothing is measured yet" : "not selected",
      ghost: none ? "No eval is selected." : `${plural(unselected, "eval")} of the registry ${unselected === 1 ? "is" : "are"} not selected.`,
      facts: {
        story: none ? "No eval is selected." : `${plural(unselected, "more eval")} can be selected.`,
        design: ["Every selected eval is answered by every arm."],
        detail: [],
      },
      ref: { type: "ghost", invites: "evals" },
    });
  }
  // A study in which no eval decides anything still shows its evals in the story, as they are folded.
  const evalRoots = nodes.filter((node) => node.station === "prompts" && node.parent === null);
  if (!evalRoots.some((node) => node.zoom === "story")) {
    for (const node of evalRoots) node.zoom = "story";
  }
  // The same for the outcomes: while none is in view, the station holds an invitation to unfold an eval.
  const inView = shownByExpansion({ nodes }, new Set());
  if (!nodes.some((node) => node.kind === "endpoint" && inView.has(node.id))) {
    const text = declared.length === 0 && !legacy ? "No outcome decides the study." : "The outcomes are folded into their evals.";
    add({
      id: "ghost:measures", kind: "ghost_stack", station: "measures", stepId: "measure", zoom: "story",
      label: "Outcomes", sub: "unfold an eval to see them", ghost: text,
      facts: { story: text, design: ["Each eval lists its outcomes when it is unfolded."], detail: [] },
      ref: { type: "ghost", invites: "outcomes" },
    });
  }

  // ── 5. tests, 6. gates, 7. verdicts ──
  if (design === null) {
    add({
      id: "family", kind: "family", station: "tests", stepId: "design", zoom: "story", settings: ["design"],
      label: "Fixed Study 1 plan", sub: "no design is declared", ghost: LEGACY_BANNER,
      facts: {
        story: LEGACY_BANNER,
        design: ["Declare a design to state what would count as a finding."],
        detail: ["No hypothesis is generated for a study without a design."],
      },
      ref: { type: "family", tests: 0, alpha: null },
    });
    add({
      id: "ghost:gates", kind: "ghost_stack", station: "gates", stepId: "design", zoom: "story", settings: ["design"],
      label: "Gates", sub: "drawn once a design is declared", ghost: "Gates are drawn once a design is declared.",
      facts: { story: "Gates are drawn once a design is declared.", design: [], detail: [] },
      ref: { type: "ghost", invites: "design" },
    });
  } else {
    const alpha = design.alpha ?? 0.05;
    const m = hypotheses.length;
    const power = recs.find((rec) => rec.path === "fixed.power")?.value ?? "80%";
    const completeness = recs.find((rec) => rec.path === "fixed.completeness")?.value ?? "≥ 90%";
    add({
      id: "family", kind: "family", station: "tests", stepId: "design", hypotheses: allHids,
      label: "Holm correction", sub: `m = ${m} · α ${formatG(alpha)} · power ${power}`,
      facts: {
        story: m === 0 ? "No test can be drawn yet." : `${m === 1 ? "The one test is" : `All ${m} tests are`} held to a false-finding rate of ${formatG(alpha)} together.`,
        design: [`Each test is run at α/${Math.max(1, m)}.`, "Every outcome that is added makes all of them harder to pass."],
        detail: [`Sample sizes are rated at ${power} power, with worst-case variance.`, PREVIEW],
      },
      ref: { type: "family", tests: m, alpha },
    });
    add({
      id: "gate:completeness", kind: "gate", station: "gates", stepId: "design", hypotheses: allHids,
      label: "Completeness", sub: `${completeness} of the planned pairs`,
      facts: {
        story: `At least ${completeness.replace(/^≥\s*/, "")} of the planned pairs have to be analysed.`,
        design: ["Below that an outcome is invalid, not inconclusive."],
        detail: ["The study stops at its planned size; only the first full run is confirmatory."],
      },
      ref: { type: "gate", gate: "completeness", epsilon: null },
    });

    for (const lane of lanes.filter((item) => item.compared)) {
      const own = hypotheses.filter((hypothesis) => laneKey([hypothesis.model, hypothesis.sampling]) === lane.key);
      const hids = own.map((hypothesis) => hypothesis.id);
      for (const hypothesis of own) {
        const size = hypothesis.sample_size;
        const evidence = recs.find((rec) => rec.path === `cards.${hypothesis.card_id}.items`);
        const pooled = size.basis === "no_closed_form";
        const floor = size.control_floor ?? null;
        add({
          id: `test:${hypothesis.id}`, kind: "test", station: "tests", lane: lane.key, stepId: "design", zoom: "story", hypotheses: [hypothesis.id],
          label: TEST_LABELS[hypothesis.test] ?? hypothesis.test,
          sub: `${hypothesis.id} · ${hypothesis.sided === "one_sided_upper" ? "one-sided" : "two-sided"}`,
          facts: {
            story: hypothesis.statement,
            design: [
              `Tests ${hypothesis.label}.`,
              pooled ? "Runs on the tokens the study generates, pooled per side." : `${plural(size.n, "prompt")} per arm.`,
              ...(hypothesis.margin === null ? [] : [`Margin ±${formatG(hypothesis.margin)}.`]),
            ],
            detail: [
              hypothesis.statement,
              ...(pooled ? [
                POOLED_TOKENS,
                `${count(hypothesis.planned_units)} generations per arm are planned on the evals whose prompt list is fixed before the run. Completeness is counted against that number.`,
              ] : []),
              ...(evidence ? [evidence.have, evidence.need, evidence.basis] : []),
              ...(floor === null ? [] : [controlFloorFact(hypothesis.id, size.n, floor)]),
              PREVIEW,
            ],
          },
          ref: { type: "test", hypothesis },
        });
      }

      const seeds = unique(cellArms(config, [lane.model, lane.sampling]).filter((arm) => compared(arm, "prng_seeded")).map((arm) => arm.study_seed ?? null)).length;
      const pairs = (seeds * (seeds - 1)) / 2;
      // With too few prompts it is not certain that a seed pair could fail, so the analysis does not count the
      // control as passed whatever the data: the control gate then makes the
      // hypothesis invalid (`sample_size.control_floor`, the reason `control_cannot_fail` of the verdicts).
      const floors = own.flatMap((hypothesis) => {
        const floor = hypothesis.sample_size.control_floor ?? null;
        return floor === null ? [] : [{ id: hypothesis.id, n: hypothesis.sample_size.n, floor }];
      });
      const cannotFail = floors.filter((item) => item.n < item.floor);
      add({
        id: `gate:neg:${lane.key}`, kind: "gate", station: "gates", lane: lane.key, stepId: "design", tone: "control", hypotheses: hids,
        label: "Negative control",
        sub: cannotFail.length > 0 ? `not counted as passed for ${listed(cannotFail.map((item) => item.id))}` : `${plural(pairs, "seed pair")} must not differ`,
        facts: {
          story: "Two seeds of the same pseudo-random generator must not differ.",
          design: [
            "If a pair differs, one is missing, or the control could not have failed, no conclusion is drawn in this comparison.",
            ...cannotFail.map((item) => controlFloorFact(item.id, item.n, item.floor)),
          ],
          detail: [
            "The seed pairs are corrected with Holm, in a family of their own.",
            ...floors.filter((item) => item.n >= item.floor).map((item) => `${item.id}: the control can fail from ${item.floor} prompts on; the study plans ${item.n}.`),
          ],
        },
        ref: { type: "gate", gate: "negative", epsilon: null },
      });
      // How far the biased arm has to move an outcome to be detected on it: the number the Design step's
      // `design.control_not_rated` warning states, for the same hypotheses (`positiveControlShift`).
      const pooledSeeds = cellArms(config, [lane.model, lane.sampling]).filter((arm) => compared(arm, "prng_seeded")).length;
      const shifts = design.claim !== "difference_or_equivalence" || epsilon === null ? [] : own.flatMap((hypothesis) => {
        if (reachableVerdicts(hypothesis, design.claim).equivalent !== null) return [];
        const k = selections.get(hypothesis.card_id)?.overrides?.k ?? registry.get(hypothesis.card_id)?.k ?? null;
        const shift = positiveControlShift(hypothesis, k, pooledSeeds, alpha);
        return shift === null ? [] : [`${hypothesis.id}: the biased arm has to move ${hypothesis.label} by at least ${shift.toFixed(3)} to be detected.`];
      });
      add({
        id: `gate:pos:${lane.key}`, kind: "gate", station: "gates", lane: lane.key, stepId: "design", tone: "positive",
        label: "Positive control", sub: epsilon === null ? "none" : `ε ${epsilon.toFixed(2)} must be detected`,
        ghost: epsilon === null ? "No positive control is set." : null,
        facts: {
          story: epsilon === null ? "No positive control: a null result is inconclusive." : `The ε ${epsilon.toFixed(2)} arm has to be detected.`,
          design: ["'Equivalent' is only returned on an outcome if this arm is detected on that outcome."],
          detail: [
            "A deliberately biased pseudo-random arm, on a seed of its own.",
            ...shifts,
            ...(shifts.length > 0 && epsilon !== null ? [`Whether a sampler bias of ε = ${epsilon.toFixed(2)} moves an outcome that far is not known before the run.`] : []),
          ],
        },
        ref: { type: "gate", gate: "positive", epsilon },
      });
    }
  }

  for (const hypothesis of design === null ? [] : hypotheses) {
    const reach = reachableVerdicts(hypothesis, design?.claim ?? "difference");
    const stamps = VERDICT_ORDER.map((verdict): VerdictStamp => {
      const why = reach[verdict];
      return {
        verdict, label: VERDICT_LABELS[verdict], reachable: why === null, why,
        reason: why === null ? null : sentence(UNREACHABLE_TEXT[why] ?? why),
        rules: VERDICT_RULES.filter((rule) => rule.verdict === verdict).map((rule) => rule.condition),
      };
    });
    const findings = stamps.filter((stamp) => stamp.verdict !== "invalid" && stamp.verdict !== "unavailable");
    const open = findings.filter((stamp) => stamp.reachable).map((stamp) => stamp.label);
    add({
      id: `verdict:${hypothesis.id}`, kind: "verdict", station: "verdict", lane: laneKey([hypothesis.model, hypothesis.sampling]),
      parent: `test:${hypothesis.id}`, pinned: true, stepId: "design", zoom: "story", hypotheses: [hypothesis.id],
      label: `Verdict of ${hypothesis.id}`, sub: open.length === 0 ? "no finding within reach" : open.join(" · ").toLowerCase(),
      facts: {
        story: open.length === 0 ? `${hypothesis.id} can only end as invalid or unavailable as configured.`
          : `${hypothesis.id} can end as ${listed(open.map((label) => label.toLowerCase()), "or")}.`,
        design: findings.filter((stamp) => !stamp.reachable).map((stamp) => `${stamp.label}: unreachable. ${stamp.reason}`),
        detail: [
          ...stamps.map((stamp) => `${stamp.label}: ${stamp.rules.join(" Or: ")}`),
          "The rules are tried in the order of the decision matrix; the first that holds gives the verdict.",
        ],
      },
      ref: { type: "verdict", hypothesis, stamps },
    });
  }
  if (!nodes.some((node) => node.kind === "verdict")) {
    const text = design === null ? "Verdicts are drawn once a design is declared." : "No hypothesis can be drawn yet.";
    add({
      id: "ghost:verdict", kind: "ghost_stack", station: "verdict", stepId: "design", zoom: "story", settings: design === null ? ["design"] : ["design.confirmatory"],
      label: "Verdicts", sub: design === null ? "drawn once a design is declared" : "no hypothesis yet", ghost: text,
      facts: { story: text, design: [], detail: [] },
      ref: { type: "ghost", invites: "design" },
    });
  }

  // ── edges ──
  for (const lane of lanes) {
    const hids = hidsOfLane(lane.key);
    for (const node of nodes.filter((item) => item.station === "arms" && item.lane === lane.key && item.parent === null)) {
      if (node.kind === "positive_control") continue;
      const exploratory = node.kind === "supervised_arm" || node.kind === "other_arm" || node.ghost !== null;
      link("study", node.id, "flow", node.tone, exploratory ? [] : hids, exploratory);
    }
    for (const feeder of feeders.get(lane.key) ?? []) {
      for (const card of known) {
        const serves = hypotheses.filter((hypothesis) => hypothesis.card_id === card.id && hids.includes(hypothesis.id)).map((hypothesis) => hypothesis.id);
        link(feeder.id, `card:${card.id}`, "flow", feeder.tone, serves, serves.length === 0);
      }
    }
  }
  for (const node of nodes.filter((item) => item.kind === "endpoint" && item.parent !== null)) {
    link(node.parent!, node.id, "measures", "neutral", node.hypotheses, node.ref.type === "endpoint" && node.ref.role === "exploratory");
  }
  if (design !== null) {
    for (const hypothesis of hypotheses) {
      const lane = laneKey([hypothesis.model, hypothesis.sampling]);
      const test = `test:${hypothesis.id}`, verdict = `verdict:${hypothesis.id}`, hid = [hypothesis.id];
      link(`endpoint:${refKey(hypothesis.card_id, hypothesis.endpoint)}`, test, "compares", "neutral", hid);
      // The thread of a hypothesis runs through the negative control of its lane. The Holm family and the
      // completeness gate are rules over every test: they hold, and are not lines of their own.
      link("family", test, "governs", "neutral", hid, false, true);
      link(test, `gate:neg:${lane}`, "gates", "neutral", hid);
      link(`gate:neg:${lane}`, verdict, "decides", "neutral", hid);
      link(test, "gate:completeness", "gates", "neutral", hid, false, true);
      link("gate:completeness", verdict, "decides", "neutral", hid, false, true);
      // The positive control only bears on 'equivalent', which needs the claim and a margin.
      if (design.claim === "difference_or_equivalence" && hypothesis.margin !== null) {
        link(`gate:pos:${lane}`, verdict, "decides", "positive", hid, hypothesis.positive_control === null);
      }
    }
    for (const lane of lanes.filter((item) => item.compared)) {
      const gate = `gate:pos:${lane.key}`;
      const hids = edges.filter((edge) => edge.from === gate).flatMap((edge) => edge.hypotheses);
      for (const arm of cellArms(config, [lane.model, lane.sampling])) {
        // In force while the arm runs and a verdict waits for the gate; the positive ink says whose line it is.
        if (arm.source === "prng_epsilon" && sameLevel(arm.epsilon, epsilon)) {
          link(`arm:${arm.id}`, gate, "gates", "positive", hids, !compared(arm, "prng_epsilon") || hids.length === 0);
        }
      }
      const node = byId.get(gate);
      if (node) node.hypotheses = unique(hids);
    }
  }

  // ── what sits on each node ──
  for (const node of nodes) {
    if (node.parent !== null) byId.get(node.parent)?.children.push(node.id);
  }
  const home = (id: string) => byId.get(id) ?? byId.get("study")!;
  for (const rec of recs) {
    // The pinned envelope is not said of an arm that does not sample through it.
    if (rec.path === "fixed.envelope" && outsideEnvelope.has(rec.nodeId)) continue;
    home(rec.nodeId).recs.push(rec);
  }
  for (const problem of problems) home(problem.nodeId).problems.push(problem);
  for (const node of nodes) {
    node.expandable = node.children.some((id) => !byId.get(id)!.pinned);
    node.state = worstState(node.recs.map((rec) => rec.state));
    node.severity = worstSeverity(node.problems);
    node.settings = unique([...node.recs.filter((rec) => rec.state !== "locked").map((rec) => rec.path), ...node.settings]);
    if (node.kind === "tier0_band") node.hypotheses = unique(node.children.flatMap((id) => byId.get(id)!.hypotheses));
    node.describes = describe(node, edges, byId);
  }

  // Reading order: station, then lane, then the order the nodes were made in (parents before children).
  const laneIndex = new Map(lanes.map((lane, index) => [lane.key, index + 1]));
  const made = new Map(nodes.map((node, index) => [node.id, index]));
  nodes.sort((a, b) => STATION_INDEX.get(a.station)! - STATION_INDEX.get(b.station)!
    || (laneIndex.get(a.lane ?? "") ?? 0) - (laneIndex.get(b.lane ?? "") ?? 0)
    || made.get(a.id)! - made.get(b.id)!);
  return { nodes, edges, lanes, legacy: design === null };
}

/** One lane per (model, sampling) pair: the comparisons first, in the plan's order, then pairs without a quantum arm. */
function lanesOf(config: WizardStudyConfig): Lane[] {
  const served = new Map(config.models.map((model) => [model.id, model.served_name]));
  const lane = (cell: Cell, isCompared: boolean): Lane => ({
    key: laneKey(cell), model: cell[0], sampling: cell[1], compared: isCompared,
    label: `${served.get(cell[0]) ?? cell[0]} · ${SAMPLING_LABELS[cell[1]] ?? cell[1]}`,
  });
  const lanes = sourceCells(config).map((cell) => lane(cell, true));
  const seen = new Set(lanes.map((item) => item.key));
  const rest: Lane[] = [];
  for (const arm of config.arms) {
    const cell: Cell = [arm.model, arm.sampling];
    if (seen.has(laneKey(cell))) continue;
    seen.add(laneKey(cell));
    rest.push(lane(cell, false));
  }
  rest.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  return [...lanes, ...rest];
}

/** A node's place in the flow, from its edges. */
function describe(node: MapNode, edges: readonly MapEdge[], byId: ReadonlyMap<string, MapNode>): string {
  const names = (ids: readonly string[]) => listed(unique(ids.map((id) => byId.get(id)?.label ?? id)));
  const from = edges.filter((edge) => edge.to === node.id && !edge.implied).map((edge) => edge.from);
  const to = edges.filter((edge) => edge.from === node.id && !edge.implied).map((edge) => edge.to);
  const parts: string[] = [];
  if (node.ghost !== null) parts.push(node.ghost);
  if (node.kind === "family" && node.ghost === null) parts.push("Applies to every test of the study.");
  if (node.id === "gate:completeness") parts.push("Applies to every test before its verdict.");
  if (node.kind === "seeded_arm") parts.push("One run of the seeded controls.");
  if (node.kind === "positive_control" && to.length === 0) parts.push("No gate of this design depends on it.");
  if (from.length > 0) parts.push(`Comes from ${names(from)}.`);
  if (to.length > 0) parts.push(`Leads to ${names(to)}.`);
  if (parts.length === 0 && node.children.length > 0) parts.push(`Holds ${names(node.children)}.`);
  if (parts.length === 0 && node.parent !== null) parts.push(`Part of ${byId.get(node.parent)?.label ?? node.parent}.`);
  if (parts.length === 0) parts.push(STATIONS[STATION_INDEX.get(node.station)!]!.sentence);
  return parts.join(" ");
}

// ── reading the graph ────────────────────────────────────────────────────────

export function nodeById(graph: Pick<StudyGraph, "nodes">, id: string): MapNode | undefined {
  return graph.nodes.find((node) => node.id === id);
}

/** What a drawn node, or a station, answers for without drawing it: how many nodes, their problems and their worst state. */
export interface Folded {
  count: number;
  problems: Problem[];
  state: RecState | null;
  severity: "error" | "warning" | null;
}

export interface Visible {
  /** The drawn nodes, in reading order. */
  nodes: MapNode[];
  /** The drawn edges. An edge to a folded node ends on the nearest drawn ancestor. */
  edges: MapEdge[];
  /**
   * Per drawn node that answers for others: what they carry, so a problem inside a folded node still shows.
   * At the story zoom that includes the nodes the story leaves out under it.
   */
  folded: Record<string, Folded>;
  /**
   * Per station: what the story zoom leaves out there and no drawn node holds (the Holm family, the gates, a
   * tier of evals that decide nothing). The station's heading shows it, so no problem is ever off the map.
   * Empty at the design and detail zooms, which draw every node that is not folded.
   */
  left: Partial<Record<StationId, Folded>>;
}

/**
 * The ids drawn at full density for a set of unfolded nodes: a root, or a child whose parent is drawn and
 * either unfolded or outranked by a pin. The invitation to open an eval gives way to the first outcome shown.
 */
export function shownByExpansion(graph: Pick<StudyGraph, "nodes">, expanded: ReadonlySet<string>): Set<string> {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const memo = new Map<string, boolean>();
  const shown = (node: MapNode): boolean => {
    const known = memo.get(node.id);
    if (known !== undefined) return known;
    memo.set(node.id, false); // a cycle, which the builder never makes, would end here
    const parent = node.parent === null ? undefined : byId.get(node.parent);
    const result = node.parent === null || (parent !== undefined && shown(parent) && (expanded.has(parent.id) || node.pinned));
    memo.set(node.id, result);
    return result;
  };
  const ids = new Set(graph.nodes.filter(shown).map((node) => node.id));
  if (graph.nodes.some((node) => node.kind === "endpoint" && ids.has(node.id))) ids.delete("ghost:measures");
  return ids;
}

/**
 * The nodes and edges drawn for a set of unfolded nodes at a zoom tier.
 *
 * `design` and `detail` draw the same nodes; they differ in how much each node says. `story` draws the
 * skeleton only (the question, the quantum arm and the seeded controls, what decides the study, its tests and
 * verdicts) and joins it across what it leaves out, so a test still reaches its verdict through the gates.
 */
export function visible(graph: StudyGraph, expanded: ReadonlySet<string>, tier: ZoomTier): Visible {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const shown = shownByExpansion(graph, expanded);
  const anchor = (id: string): string | null => {
    let node = byId.get(id);
    while (node !== undefined && !shown.has(node.id)) node = node.parent === null ? undefined : byId.get(node.parent);
    return node?.id ?? null;
  };

  let edges: MapEdge[] = [];
  const merge = (list: MapEdge[], edge: MapEdge) => {
    const known = list.find((item) => item.id === edge.id);
    if (known === undefined) {
      list.push(edge);
      return;
    }
    known.hypotheses = unique([...known.hypotheses, ...edge.hypotheses]);
    known.dashed = known.dashed && edge.dashed;
    known.implied = known.implied && edge.implied;
  };
  for (const edge of graph.edges) {
    const from = anchor(edge.from), to = anchor(edge.to);
    if (from === null || to === null || from === to) continue;
    merge(edges, { ...edge, id: `${from}>${to}`, from, to, hypotheses: [...edge.hypotheses] });
  }

  let drawn = shown;
  if (tier === "story") {
    drawn = new Set([...shown].filter((id) => byId.get(id)!.zoom === "story"));
    for (const id of shown) {
      if (drawn.has(id)) continue;
      const into = edges.filter((edge) => edge.to === id), out = edges.filter((edge) => edge.from === id);
      edges = edges.filter((edge) => edge.to !== id && edge.from !== id);
      for (const a of into) {
        for (const b of out) {
          const both = a.hypotheses.filter((hid) => b.hypotheses.includes(hid));
          // Joined only where one hypothesis runs through both halves, or neither half carries one.
          if (both.length === 0 && (a.hypotheses.length > 0 || b.hypotheses.length > 0)) continue;
          if (a.from === b.to) continue;
          merge(edges, { id: `${a.from}>${b.to}`, from: a.from, to: b.to, kind: b.kind, tone: b.tone, dashed: a.dashed || b.dashed, implied: a.implied || b.implied, hypotheses: both });
        }
      }
    }
  }

  const below = (node: MapNode): MapNode[] => node.children.flatMap((id) => { const child = byId.get(id)!; return [child, ...below(child)]; });
  // A drawn child answers for what is folded into it; only the children that are not drawn fold into the node.
  const hidden = (node: MapNode): MapNode[] => node.children.flatMap((id) => (shown.has(id) ? [] : [byId.get(id)!, ...below(byId.get(id)!)]));
  const within = new Map<string, MapNode[]>();
  const leftOut = new Map<StationId, MapNode[]>();
  for (const id of drawn) {
    const inside = hidden(byId.get(id)!);
    if (inside.length > 0) within.set(id, inside);
  }
  // What the story leaves out (the Holm family, the gates, a tier of evals that decide nothing) is still part
  // of the study: its state and its problems go to the nearest drawn node that holds it, and where none does,
  // to its station.
  for (const id of shown) {
    if (drawn.has(id)) continue;
    const node = byId.get(id)!;
    const carried = [node, ...hidden(node)];
    let holder = node.parent === null ? undefined : byId.get(node.parent);
    while (holder !== undefined && !drawn.has(holder.id)) holder = holder.parent === null ? undefined : byId.get(holder.parent);
    if (holder !== undefined) within.set(holder.id, [...(within.get(holder.id) ?? []), ...carried]);
    else leftOut.set(node.station, [...(leftOut.get(node.station) ?? []), ...carried]);
  }
  const carry = (inside: readonly MapNode[]): Folded => {
    const problems = inside.flatMap((node) => node.problems);
    return { count: inside.length, problems, state: worstState(inside.map((node) => node.state)), severity: worstSeverity(problems) };
  };
  const folded: Record<string, Folded> = {};
  for (const [id, inside] of within) folded[id] = carry(inside);
  const left: Partial<Record<StationId, Folded>> = {};
  for (const [station, inside] of leftOut) left[station] = carry(inside);
  return { nodes: graph.nodes.filter((node) => drawn.has(node.id)), edges, folded, left };
}

export interface Lineage {
  upstream: { nodes: string[]; edges: string[] };
  downstream: { nodes: string[]; edges: string[] };
}

/**
 * What a node depends on and what depends on it: the node and edge ids up and down the flow.
 *
 * An eval is shared by every comparison and by several hypotheses, so the walk follows the node's own
 * hypotheses: an edge that carries hypotheses is taken only if it carries one of them, and a node that sits in
 * a lane never leads into another lane. An edge that carries none (exploratory material) is always taken.
 * Implied edges are followed like any other, so the Holm family is upstream of every test and the completeness
 * gate sits between every test and its verdict. A node that folds others of its own station is walked together
 * with them.
 *
 * Works on the whole graph and on what `visible` returns, whose edges end on drawn nodes.
 */
export function lineage(graph: Pick<StudyGraph, "nodes" | "edges">, nodeId: string): Lineage {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const start = byId.get(nodeId);
  const result = { upstream: { nodes: new Set<string>(), edges: new Set<string>() }, downstream: { nodes: new Set<string>(), edges: new Set<string>() } };
  if (start === undefined) return { upstream: { nodes: [], edges: [] }, downstream: { nodes: [], edges: [] } };

  const walk = (from: MapNode, direction: "upstream" | "downstream") => {
    const carried = from.hypotheses.length > 0 ? new Set(from.hypotheses) : null;
    const found = result[direction];
    const queue = [from.id];
    const seen = new Set(queue);
    while (queue.length > 0) {
      const current = queue.shift()!;
      for (const edge of graph.edges) {
        if ((direction === "downstream" ? edge.from : edge.to) !== current) continue;
        const next = byId.get(direction === "downstream" ? edge.to : edge.from);
        if (next === undefined) continue;
        if (carried !== null && edge.hypotheses.length > 0 && !edge.hypotheses.some((hid) => carried.has(hid))) continue;
        if (from.lane !== null && next.lane !== null && next.lane !== from.lane) continue;
        found.edges.add(edge.id);
        if (seen.has(next.id)) continue;
        seen.add(next.id);
        found.nodes.add(next.id);
        queue.push(next.id);
      }
    }
  };

  // A node that folds others of its own station (the seeded deck, the token band, a tier of evals) stands for
  // them: its lineage is theirs too.
  const within = (node: MapNode): MapNode[] => node.children.flatMap((id) => {
    const child = byId.get(id);
    return child === undefined || child.station !== node.station ? [] : [child, ...within(child)];
  });
  const starts = [start, ...within(start)];

  for (const node of starts) {
    walk(node, "upstream");
    walk(node, "downstream");
  }
  for (const node of starts) {
    result.downstream.nodes.delete(node.id);
    result.upstream.nodes.delete(node.id);
  }

  const inOrder = (ids: Set<string>, all: readonly { id: string }[]) => all.filter((item) => ids.has(item.id)).map((item) => item.id);
  return {
    upstream: { nodes: inOrder(result.upstream.nodes, graph.nodes), edges: inOrder(result.upstream.edges, graph.edges) },
    downstream: { nodes: inOrder(result.downstream.nodes, graph.nodes), edges: inOrder(result.downstream.edges, graph.edges) },
  };
}
