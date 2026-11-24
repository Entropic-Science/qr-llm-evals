import { StudyConfigSchema, type Arm, type EvalCard, type ModelRef } from "@qr-evals/contracts";
import { declareDesign, setCardK, setCardItems, setCardMaxTokens, setModels, setSeeds, toggleCard, toggleConfirmatory, useRecommended } from "./config-edits";
import { conditioningProblems } from "./conditioning";
import type { WizardStudyConfig } from "./config-store";
import {
  MAX_PERMUTATIONS, cellArms, cellText, compared, controlSeeds, draftHypotheses, epsilonArms, familySizes, formatG,
  ineligibleReason, laneKey, marginRefusal, metaFor, planPermutations, positiveControlShift, reachableVerdicts,
  recommendDesign, sourceCells,
} from "./design";
import { needsStudyJudge } from "./judge";
import { REFERENCE_EFFECT } from "./power";
import { DEFAULT_SEEDS, EPSILON_LEVELS, rosterFromArms } from "./roster";
import { TOKEN_LIMIT_PATH, tokenLimitFor } from "./token-limits";

// What stops a draft from being registered, and what weakens it.
//
// One list for every view: the wizard rail counts it per step, the settings page lists it per section, the
// map draws it on the node it names. The part that can be told from the study and the registry mirrors
// `problems()` in `backend/src/qr_llm_evals/cards/design.py` word for word and is checked against
// `packages/contracts/fixtures/design.json`; the rest is what only the page knows or only the page says: a
// setting outside what a study can hold, the cross-references the server checks when it reads a study, pins
// the pre-registration will warn about, hand-written arms, a missing judge, a placeholder contact, a draft
// the browser did not store, a study the server refused.

export type StepId = "compare" | "measure" | "design" | "instruments" | "review";

export interface ProblemFix {
  label: string;
  apply: (config: WizardStudyConfig, cards: readonly EvalCard[]) => WizardStudyConfig;
}

export interface Problem {
  code: string;
  severity: "error" | "warning";
  message: string;
  stepId: StepId;
  /** The node of the study map the problem sits on (`study`, `card:{id}`, `endpoint:{card}/{endpoint}`, `family`, …). */
  nodeId: string;
  fix?: ProblemFix;
  /**
   * The setting the problem is about, as a study key path with list items as `[]` (`judge.max_tokens`,
   * `arms[].id`), where the code alone does not say which setting it is.
   */
  path?: string;
}

/** What the page knows and the study does not say. */
export interface ProblemContext {
  /** Whether the draft was written to this browser's storage. Left out, nothing is said about it. */
  saved?: boolean;
  /** The message of the server's last refusal of this study, if it refused it. */
  serverError?: string | null;
  /** The models the server offers, with the checkpoints it has pinned for them. */
  modelOptions?: readonly ModelRef[];
}

/**
 * The codes decided from the study and the registry alone: the ones the backend reports too, in the order it
 * reports them (`PROBLEM_CODES` in `cards/design.py`, written to `design.json` as `problem_codes`).
 */
export const REGISTRY_CODES: readonly string[] = [
  "compare.no_cell", "compare.too_few_seeds", "measure.no_eval", "measure.out_of_bounds", "design.legacy_plan",
  "design.no_outcome", "design.not_eligible", "design.equivalence_needs_control", "design.epsilon_missing",
  "design.epsilon_off", "design.epsilon_seed_shared", "design.margin_not_allowed", "design.no_planned_data",
  "design.below_floor", "design.control_below_floor", "design.insufficient", "design.control_not_rated",
  "design.margin_below_floor", "design.too_many_tests", "design.equivalent_unreachable",
  "design.study_absence_unreachable", "design.no_expectations",
];

/** The contact a template ships with, and any other `REPLACE_WITH_…` left in its place. */
const PLACEHOLDER = /REPLACE_WITH/;

const cardTitle = (card: EvalCard) => (card as EvalCard & { title?: string }).title ?? card.id;
const recommended = (path: string): ProblemFix => ({ label: "Use recommended", apply: useRecommended(path) });
const plural = (count: number, noun: string, many = `${noun}s`) => `${count} ${count === 1 ? noun : many}`;

interface SchemaIssue {
  code?: string;
  path: readonly PropertyKey[];
  message: string;
  origin?: string;
  minimum?: number | bigint;
  maximum?: number | bigint;
  inclusive?: boolean;
  pattern?: string;
  keys?: readonly string[];
  errors?: readonly (readonly SchemaIssue[])[];
}

/** `arms[0].id` for the page's words, and `arms[].id` for finding the field. */
function issuePaths(path: readonly PropertyKey[]): { exact: string; generic: string } {
  let exact = "";
  let generic = "";
  for (const part of path) {
    if (typeof part === "number") { exact += `[${part}]`; generic += "[]"; continue; }
    const dot = exact === "" ? "" : ".";
    exact += `${dot}${String(part)}`;
    generic += `${dot}${String(part)}`;
  }
  return { exact, generic };
}

/**
 * The issues of a study the schema refuses, one per setting. A block that may also be left out (`design`,
 * `supervised`) is refused as a whole with the reasons inside it; those reasons are what is reported.
 */
function schemaIssues(issues: readonly SchemaIssue[], prefix: readonly PropertyKey[] = []): { path: readonly PropertyKey[]; issue: SchemaIssue }[] {
  return issues.flatMap((issue) => {
    const path = [...prefix, ...issue.path];
    if (issue.code !== "invalid_union" || issue.errors === undefined) return [{ path, issue }];
    // The branch "left out" fails only because the block is there; the other branch says what is wrong in it.
    const told = issue.errors.filter((branch) => !branch.every((inner) => inner.path.length === 0 && (inner.code === "invalid_type" || inner.code === "invalid_value")));
    return told.length === 1 ? schemaIssues(told[0]!, path) : [{ path, issue }];
  });
}

/** What is wrong with one setting, as the end of a sentence that starts with its name. */
function issueText(issue: SchemaIssue): string {
  const bound = (value: number | bigint | undefined) => String(value ?? "");
  switch (issue.code) {
    case "too_small":
      if (issue.origin === "string") return issue.minimum === 1 ? "is empty" : `needs at least ${bound(issue.minimum)} characters`;
      if (issue.origin === "array") return issue.minimum === 1 ? "is empty: it needs at least one entry" : `needs at least ${bound(issue.minimum)} entries`;
      return issue.inclusive === false ? `must be more than ${bound(issue.minimum)}` : `must be at least ${bound(issue.minimum)}`;
    case "too_big":
      if (issue.origin === "string") return `can have at most ${bound(issue.maximum)} characters`;
      if (issue.origin === "array") return `can have at most ${bound(issue.maximum)} entries`;
      if (Number(issue.maximum) >= Number.MAX_SAFE_INTEGER) return `is larger than ${Number.MAX_SAFE_INTEGER}, the largest whole number this page can keep exactly`;
      return issue.inclusive === false ? `must be less than ${bound(issue.maximum)}` : `must be at most ${bound(issue.maximum)}`;
    case "invalid_format":
      return issue.pattern === "/^[a-z0-9_]+$/" ? "may hold only lowercase letters, digits and underscores" : "is not written in the form it needs";
    case "invalid_type":
      return "is missing, or is not the kind of value it has to be";
    case "invalid_value":
    case "invalid_union":
      return "is not one of the values it can take";
    default:
      return `is not valid (${issue.message})`;
  }
}

/** The step a setting is edited on, by the block it is in. */
function stepOf(path: readonly PropertyKey[]): StepId {
  switch (path[0]) {
    case "arms": case "models": case "seeds": return "compare";
    case "cards": return "measure";
    case "design": case "expectations": return "design";
    case "judge": case "supervised": case "narrative_model": return "instruments";
    default: return "review";
  }
}

/** At most this many settings are named for a study the schema refuses; the rest are counted. */
const MAX_SCHEMA_PROBLEMS = 8;

/** One setting of a study that is outside what a study can hold. */
export interface RefusedSetting {
  /** The setting as the page writes it (`arms[0].id`), or "" for the study as a whole. */
  exact: string;
  /** The same with list items as `[]` (`arms[].id`): the path of the field that edits it. */
  generic: string;
  stepId: StepId;
  /** What is wrong with it, as a sentence. */
  message: string;
}

/**
 * The settings that keep a draft from being a study this page can hold, in the order the schema names them;
 * empty for a study it can hold. Such a draft is not stored by the browser, the server refuses it, and a study
 * file written from it does not load back.
 */
export function refusedSettings(config: unknown): RefusedSetting[] {
  const parsed = StudyConfigSchema.safeParse(config);
  if (parsed.success) return [];
  return schemaIssues(parsed.error.issues as readonly SchemaIssue[]).map(({ path, issue }) => {
    const { exact, generic } = issuePaths(path);
    const message = issue.code === "unrecognized_keys"
      ? `${exact === "" ? "The study" : `The setting ${exact}`} holds ${plural(issue.keys?.length ?? 1, "key")} this page does not know: ${(issue.keys ?? []).join(", ")}.`
      : `The setting ${exact || "of the study"} ${issueText(issue)}.`;
    return { exact, generic, stepId: stepOf(path), message };
  });
}

/**
 * The refused settings another problem already names in its own words, by the path the schema gives them:
 * the code of that problem, and the field that edits the setting.
 */
const NAMED_ELSEWHERE: Readonly<Record<string, { code: string; field: string }>> = {
  arms: { code: "compare.no_cell", field: "roster.cells" },
  cards: { code: "measure.no_eval", field: "cards[].card_id" },
  contact: { code: "review.contact_placeholder", field: "contact" },
  "judge.model": { code: "instruments.judge", field: "judge.model" },
};

/** "a", "a and b", "a, b and c", and from the fourth on "a, b, c and 2 more". */
function nameList(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  const shown = names.slice(0, 3);
  if (names.length > 3) return `${shown.join(", ")} and ${names.length - 3} more`;
  return `${shown.slice(0, -1).join(", ")} and ${shown.at(-1)!}`;
}

export function problems(config: WizardStudyConfig, cards: readonly EvalCard[], context: ProblemContext = {}): Problem[] {
  const out: Problem[] = [];
  const seen = new Set<string>();
  const add: Add = (code, severity, message, nodeId, fix, stepId, path) => {
    const key = JSON.stringify([code, message, nodeId]);
    if (seen.has(key)) return;
    seen.add(key);
    const problem: Problem = { code, severity, message, stepId: stepId ?? (code.split(".", 1)[0] as StepId), nodeId };
    if (fix !== undefined) problem.fix = fix;
    if (path !== undefined) problem.path = path;
    out.push(problem);
  };
  const registry = new Map(cards.map((card) => [card.id, card]));
  const designed = config.design !== null && config.design !== undefined;

  // ── what is compared ──
  // The server asks for a comparison and its two control seeds only of a study with a design block. A study
  // on the built-in plan runs without them, so there they are said and do not block; a study with no arm at
  // all is refused whatever its plan.
  const comparison: Problem["severity"] = designed || config.arms.length === 0 ? "error" : "warning";
  const cells = sourceCells(config);
  if (cells.length === 0) add("compare.no_cell", comparison, "Tick at least one model and sampling preset.", "study");
  for (const cell of cells) {
    const seeds = cellArms(config, cell).filter((arm) => compared(arm, "prng_seeded")).map((arm) => arm.study_seed ?? null);
    if (new Set(seeds).size < 2) add("compare.too_few_seeds", comparison, "Each comparison needs at least 2 control seeds.", `deck:${laneKey(cell)}`);
  }
  if (config.arms.length > 0 && rosterFromArms(config.arms, config.seeds ?? DEFAULT_SEEDS) === null) {
    add("compare.custom_arms", "warning", "These arms were written by hand. Rebuild from roles to edit them here.", "study");
  }
  references(config, cells, designed, add);

  // ── what is measured ──
  if (config.cards.length === 0) add("measure.no_eval", "error", "Select at least one eval.", "ghost:evals");
  for (const selection of config.cards) {
    const card = registry.get(selection.card_id);
    if (card === undefined) continue;
    const k = selection.overrides?.k ?? null;
    const items = selection.overrides?.items ?? null;
    const [lo, hi] = card.k !== null && card.k !== undefined ? card.k_bounds ?? [card.k, card.k] : [0, 0];
    const noK = k !== null && (card.k === null || card.k === undefined);
    const badK = k !== null && !noK && !(lo <= k && k <= hi);
    const badItems = items !== null && !(1 <= items && items <= card.dataset.items);
    const maxTokens = selection.overrides?.max_tokens ?? null;
    const limit = tokenLimitFor(card.id);
    const noLimit = maxTokens !== null && limit === undefined;
    const badLimit = maxTokens !== null && limit !== undefined && !(limit.min <= maxTokens && maxTokens <= limit.max);
    // The way back from values the eval cannot run with is the eval's own: one press for whichever are off.
    const off = [badK || noK, badItems, badLimit || noLimit].filter(Boolean).length;
    const own: ProblemFix = {
      label: off > 1 ? "Use the card's values" : "Use the card's value",
      apply: (study) => {
        const withK = badK || noK ? setCardK(study, card.id, null) : study;
        const withItems = badItems ? setCardItems(withK, card.id, null) : withK;
        return badLimit || noLimit ? setCardMaxTokens(withItems, card.id, null) : withItems;
      },
    };
    if (badK) add("measure.out_of_bounds", "error", `${cardTitle(card)}: k must be between ${lo} and ${hi}.`, `card:${card.id}`, own);
    if (noK) add("measure.k_not_taken", "error", `${cardTitle(card)}: this eval takes no repetitions, so it cannot be given a k.`, `card:${card.id}`, own);
    if (badItems) add("measure.out_of_bounds", "error", `${cardTitle(card)}: prompts must be between 1 and ${card.dataset.items}.`, `card:${card.id}`, own);
    if (badLimit) add("measure.out_of_bounds", "error", `${cardTitle(card)}: the token limit must be between ${limit.min} and ${limit.max}.`, `card:${card.id}`, own, undefined, TOKEN_LIMIT_PATH);
    if (noLimit) add("measure.limit_not_taken", "error", `${cardTitle(card)}: this eval generates nothing, so it cannot be given a token limit.`, `card:${card.id}`, own, undefined, TOKEN_LIMIT_PATH);
  }
  for (const selection of config.cards) {
    if (registry.has(selection.card_id)) continue;
    add("measure.unknown_eval", "error", `The eval ${selection.card_id} is not in this server's registry, so the study cannot be planned with it.`, "ghost:evals",
      { label: "Remove this eval", apply: (study) => toggleCard(study, selection.card_id) }, "measure", "cards[].card_id");
  }

  design(config, cards, registry, cells, add);

  // ── what only the page knows ──
  if (needsStudyJudge(config, cards) && !(config.judge.model ?? "").trim()) {
    const judged = config.cards.map((selection) => registry.get(selection.card_id))
      .find((card) => card?.judge?.kind === "llm" && (card.judge.model ?? "study_judge") === "study_judge");
    add("instruments.judge", "error", "Choose a judge model.", judged ? `card:${judged.id}` : "study");
  }
  if (!config.contact.trim() || PLACEHOLDER.test(config.contact)) {
    add("review.contact_placeholder", "error", "Replace the placeholder contact.", "study");
  }
  if (context.serverError) add("review.server_rejected", "error", context.serverError, "study");
  pins(config, context.modelOptions ?? [], add);

  // ── a setting outside what a study can hold ──
  // The page keeps such a draft on screen and cannot store it, and the server would refuse it. Each setting is
  // named, so it can be found; one that an earlier problem already names is not said twice.
  const raised = new Set(out.map((problem) => problem.code));
  const covered = (path: string) => { const other = NAMED_ELSEWHERE[path]; return other !== undefined && raised.has(other.code); };
  const refused = refusedSettings(config);
  const named = refused.filter(({ generic }) => !covered(generic));
  for (const { generic, stepId, message } of named.slice(0, MAX_SCHEMA_PROBLEMS)) {
    add("draft.invalid", "error", message, "study", undefined, stepId, generic === "" ? undefined : generic);
  }
  if (named.length > MAX_SCHEMA_PROBLEMS) {
    add("draft.invalid", "error", `${plural(named.length - MAX_SCHEMA_PROBLEMS, "more setting")} cannot be kept as they are. They are named here once the ones above are fixed.`, "study", undefined, "review");
  }
  if (context.saved === false) notStored(refused, covered, add);
  return out;
}

/**
 * The draft is on screen and not in the browser's storage. There are two reasons, and what helps differs.
 * A study the page can hold was not stored because the storage is full or blocked for this site: a study file
 * keeps it. A draft with a setting outside what a study can hold is never stored, so the browser still has
 * the study as it was before that setting: fixing the setting stores it again, and a study file would not
 * load back. That one is listed on the step of the first such setting, beside the problem that names it.
 */
function notStored(refused: readonly RefusedSetting[], covered: (path: string) => boolean, add: Add): void {
  const first = refused[0];
  if (first === undefined) {
    add("draft.not_saved", "warning", "This browser did not store the draft, so closing the page loses it. Save a study file to keep it.", "study", undefined, "review");
    return;
  }
  const names = [...new Set(refused.map((setting) => setting.exact).filter((exact) => exact !== ""))];
  const what = names.length === 0 ? "it holds something a study cannot hold"
    : `the ${names.length === 1 ? "setting" : "settings"} ${nameList(names)} ${names.length === 1 ? "is" : "are"} outside what a study can hold`;
  const field = covered(first.generic) ? NAMED_ELSEWHERE[first.generic]?.field : first.generic === "" ? undefined : first.generic;
  add("draft.not_saved", "warning",
    `This browser does not store the draft while ${what}. Every edit made since then is lost when the page is closed or reloaded. Fix ${names.length > 1 ? "those settings" : names.length === 1 ? "that setting" : "it"} and the draft is stored again. A study file saved in the meantime will not load back.`,
    "study", undefined, first.stepId, field);
}

type Add = (code: string, severity: Problem["severity"], message: string, nodeId: string, fix?: ProblemFix, stepId?: StepId, path?: string) => void;

/**
 * What one arm says about itself that cannot all be true: the server reads such an arm as no arm at all, so
 * it refuses the study at launch and the study file on load (`Arm._check_combination` in `models/schema.py`).
 * The arm table lets every field be set on its own, so each of these can be typed.
 */
export function armContradictions(arm: Arm): { message: string; path: string }[] {
  const out: { message: string; path: string }[] = [];
  const seeded = arm.source === "prng_seeded" || arm.source === "prng_epsilon";
  const kind = arm.source === "prng_epsilon" ? "biased" : "seeded";
  const seed = arm.study_seed ?? null;
  const epsilon = arm.epsilon ?? null;
  if (seeded && seed === null) out.push({ message: `Arm ${arm.id} is a ${kind} pseudo-random arm and has no seed. Give it one.`, path: "arms[].study_seed" });
  if (!seeded && seed !== null) out.push({ message: `Arm ${arm.id} has the seed ${seed}, and only a seeded or a biased pseudo-random arm runs on a seed. Clear the seed, or change the source.`, path: "arms[].study_seed" });
  if (arm.source === "prng_epsilon" && (epsilon === null || !EPSILON_LEVELS.some((level) => Math.abs(level - epsilon) < 1e-12))) {
    out.push({ message: `Arm ${arm.id} is a biased pseudo-random arm and needs a bias ε of ${EPSILON_LEVELS.slice(0, -1).map((level) => level.toFixed(2)).join(", ")} or ${EPSILON_LEVELS.at(-1)!.toFixed(2)}.`, path: "arms[].epsilon" });
  }
  if (arm.source !== "prng_epsilon" && epsilon !== null) out.push({ message: `Arm ${arm.id} has a bias ε, and only a biased pseudo-random arm has one. Set the bias to None, or change the source.`, path: "arms[].epsilon" });
  if (arm.sampling === "greedy" && arm.source === "qrng") out.push({ message: `Arm ${arm.id} decodes greedily, which draws no randomness, so its source cannot be Quantum.`, path: "arms[].sampling" });
  if (arm.availability === "unavailable" && !arm.unavailable_reason) out.push({ message: `Arm ${arm.id} is switched off and gives no reason. An arm that is listed and does not run has to say why.`, path: "arms[].unavailable_reason" });
  return out;
}

/**
 * Why a study file written from this draft would not load back, as far as the page can tell on its own: the
 * settings outside what a study can hold, then the arms that contradict themselves. Empty when the page knows
 * of nothing. The server's reading of the written text is the authority; this is what answers without it.
 */
export function loadRefusals(config: unknown): string[] {
  const settings = refusedSettings(config).map((setting) => setting.message);
  // A draft may be anything while it is edited: only what is shaped like an arm is asked about itself.
  const listed: unknown = (config as { arms?: unknown } | null)?.arms;
  const arms = (Array.isArray(listed) ? listed : []).filter((arm): arm is Arm => arm !== null && typeof arm === "object");
  return [...settings, ...arms.flatMap((arm) => armContradictions(arm).map((found) => found.message))];
}

/**
 * The cross-references the server checks when it reads a study, and that the setup can break: an id used
 * twice, an arm that contradicts itself, an arm on a model or a seed the study does not list, an observed arm
 * without session settings, and,
 * for a study with a design block, a comparison whose roles are filled twice. None of them is reported by the
 * registry part, and each would otherwise show only as the server's refusal on the last step.
 */
function references(config: WizardStudyConfig, cells: ReturnType<typeof sourceCells>, designed: boolean, add: Add): void {
  const twice = <T,>(values: readonly T[]) => [...new Set(values.filter((value, index) => values.indexOf(value) !== index))];
  for (const id of twice(config.arms.map((arm) => arm.id))) add("compare.duplicate_arm", "error", `Two arms have the id ${id}. Every arm needs an id of its own.`, `arm:${id}`, undefined, "compare", "arms[].id");
  for (const id of twice(config.models.map((model) => model.id))) add("compare.duplicate_model", "error", `Two models have the id ${id}. Every model needs an id of its own.`, "study", undefined, "compare", "models[].id");
  for (const id of twice(config.cards.map((selection) => selection.card_id))) add("measure.duplicate_eval", "error", `The eval ${id} is selected twice.`, `card:${id}`, undefined, "measure", "cards[].card_id");
  // A study that names no seed list runs on the default one: the server fills it in, and so does the rest of the page.
  const listed = config.seeds ?? DEFAULT_SEEDS;
  for (const seed of twice(listed)) {
    add("compare.duplicate_seed", "error", `The seed list names ${seed} twice. Each seed is listed once.`, "study",
      { label: "Remove the repeat", apply: (study) => setSeeds(study, [...new Set(study.seeds ?? DEFAULT_SEEDS)]) }, "compare", "seeds[]");
  }
  for (const { message, path } of conditioningProblems(config.conditioning)) add("compare.conditioning", "error", message, "study", undefined, "compare", path);
  const models = new Set(config.models.map((model) => model.id));
  for (const arm of config.arms) {
    const node = `arm:${arm.id}`;
    for (const { message, path } of armContradictions(arm)) add("compare.arm_contradiction", "error", message, node, undefined, "compare", path);
    if (!models.has(arm.model)) {
      add("compare.unknown_model", "error", `Arm ${arm.id} names the model ${arm.model || "(none)"}, which is not in the study's model list.`, node, undefined, "compare", "arms[].model");
    }
    const seed = arm.study_seed ?? null;
    if (seed !== null && !listed.includes(seed)) {
      add("compare.seed_not_listed", "error", `Arm ${arm.id} runs on seed ${seed}, which is not in the study's seed list.`, node,
        { label: "Add the seed to the list", apply: (study) => setSeeds(study, [...new Set([...(study.seeds ?? DEFAULT_SEEDS), seed])]) }, "compare", "seeds[]");
    }
    if (arm.observer_mode === "supervised" && (config.supervised === null || config.supervised === undefined)) {
      add("compare.observed_without_sessions", "error", `Arm ${arm.id} is observed, but the study has no settings for supervised sessions.`, node, undefined, "compare", "arms[].observer_mode");
    }
  }
  if (!designed) return;

  for (const cell of cells) {
    const arms = cellArms(config, cell);
    const lane = `deck:${laneKey(cell)}`;
    const quantum = arms.filter((arm) => compared(arm, "qrng"));
    if (quantum.length > 1) {
      add("compare.quantum_arms", "error", `${cellText(cell)} has ${quantum.length} quantum arms that run unobserved (${quantum.map((arm) => arm.id).join(", ")}). A comparison has exactly one.`, lane, undefined, "compare", "arms[].source");
    }
    const seeds = arms.filter((arm) => compared(arm, "prng_seeded")).map((arm) => arm.study_seed ?? null);
    for (const seed of twice(seeds)) {
      add("compare.repeated_control_seed", "error", `Two control runs of ${cellText(cell)} use the seed ${seed ?? "none"}. Each control run needs a seed of its own.`, lane, undefined, "compare", "arms[].study_seed");
    }
    const epsilon = config.design?.sensitivity_epsilon ?? null;
    if (epsilon !== null && epsilonArms(config, cell, epsilon).length > 1) {
      add("design.epsilon_repeated", "error", `${cellText(cell)} has ${epsilonArms(config, cell, epsilon).length} arms at ε = ${epsilon.toFixed(2)}. The positive control of a comparison is one arm.`, `gate:pos:${laneKey(cell)}`, undefined, "design", "arms[].epsilon");
    }
  }
  const block = config.design;
  if (block === null || block === undefined) return;
  const key = (ref: { card_id: string; endpoint: string }) => `${ref.card_id}/${ref.endpoint}`;
  const deciding = block.confirmatory.map(key);
  for (const ref of twice(deciding)) {
    const [cardId = "", endpoint = ""] = ref.split("/");
    add("design.duplicate_outcome", "error", `${metaFor(cardId, endpoint).label} is listed twice among the outcomes that decide the study.`, `endpoint:${ref}`, undefined, "design", "design.confirmatory");
  }
  for (const declared of block.margins ?? []) {
    if (deciding.includes(key(declared))) continue;
    add("design.margin_without_outcome", "error", `A margin is declared for ${metaFor(declared.card_id, declared.endpoint).label}, which is not an outcome that decides the study.`,
      `endpoint:${key(declared)}`, { label: "Remove the margin", apply: useRecommended(`design.margins.${key(declared)}`) }, "design", "design.margins");
  }
}

/**
 * What the pre-registration will warn about at its head, whatever else is right: a model without a pinned
 * checkpoint, session assets without their hashes, a judge that is sent a temperature. They do not block a
 * launch, and the study is not "ready" without a word about them: the frozen document would say it for the
 * researcher, after the only run that can be confirmatory.
 */
function pins(config: WizardStudyConfig, modelOptions: readonly ModelRef[], add: Add): void {
  for (const model of config.models) {
    if (model.checkpoint) continue;
    const offered = modelOptions.find((option) => option.id === model.id)?.checkpoint ?? null;
    add("compare.checkpoint_unpinned", "warning",
      `The model ${model.served_name || model.id} has no checkpoint pinned, so the pre-registration will open with a validity warning that its exact revision is not frozen.`,
      "study",
      offered ? { label: "Pin the server's checkpoint", apply: (study) => setModels(study, study.models.map((entry) => (entry.id === model.id && !entry.checkpoint ? { ...entry, checkpoint: offered } : entry))) } : undefined,
      "compare", "models[].checkpoint");
  }
  const sessions = config.supervised;
  if (sessions !== null && sessions !== undefined && (!sessions.questionnaires_hash || !sessions.target_pool_hash)) {
    add("instruments.assets_unpinned", "warning",
      "The questionnaires and the target pool of the supervised sessions are not pinned by their hashes, so the pre-registration will open with a validity warning about each. This page cannot fill the hashes in; they can only be written into a study file by hand.",
      "study", undefined, "instruments", "supervised.questionnaires_hash");
  }
  const temperature = config.judge.temperature ?? null;
  if (temperature !== null && temperature !== 0) {
    add("instruments.judge_temperature", "warning",
      `The judge is sent a temperature of ${formatG(temperature)}, so its scores vary between repeated calls on the same reply. The pre-registration says so in a validity warning.`,
      "study", undefined, "instruments", "judge.temperature");
  }
}

/** The problems of the design block, in the backend's order. */
function design(
  config: WizardStudyConfig, cards: readonly EvalCard[], registry: ReadonlyMap<string, EvalCard>,
  cells: ReturnType<typeof sourceCells>, add: Add,
): void {
  const block = config.design;
  // What the study would be given by default. A problem offers it only where it differs from what is there.
  const alternative = recommendDesign(config, cards);
  if (block === null || block === undefined) {
    add("design.legacy_plan", "warning", "This draft uses the fixed Study 1 plan.", "family",
      alternative === null ? undefined : { label: "Declare a design", apply: declareDesign });
    return;
  }
  const available = <T>(fix: T): T | undefined => (alternative === null ? undefined : fix);

  if (block.confirmatory.length === 0) {
    add("design.no_outcome", "error", "Choose at least one outcome that decides the study.", "family", available(recommended("design.confirmatory")));
  }
  const selections = new Map(config.cards.map((selection) => [selection.card_id, selection]));
  for (const { card_id: cardId, endpoint } of block.confirmatory) {
    const card = registry.get(cardId);
    const meta = metaFor(cardId, endpoint);
    const why = selections.has(cardId) ? ineligibleReason(card, endpoint) : "the eval is not selected";
    if (card === undefined || why !== null) {
      // The fix takes this one outcome out. The other outcomes the researcher chose stay as they are.
      add("design.not_eligible", "error", `${meta.label} can no longer decide a study: ${why}.`, `endpoint:${cardId}/${endpoint}`,
        { label: "Remove this outcome", apply: (study) => toggleConfirmatory(study, { card_id: cardId, endpoint }) });
      continue;
    }
    const override = selections.get(cardId)?.overrides?.k ?? null;
    if (meta.k_locked && override !== null && override !== card.k) {
      add("measure.out_of_bounds", "error", `${cardTitle(card)}: k must be between ${card.k} and ${card.k}.`, `card:${cardId}`,
        { label: "Use the card's value", apply: (study) => setCardK(study, cardId, null) });
    }
  }

  const epsilon = block.sensitivity_epsilon ?? null;
  if (epsilon === null) {
    if (block.claim === "difference_or_equivalence") {
      add("design.equivalence_needs_control", "error", "'Absence within a margin' needs a positive control.", "study", available(recommended("design.claim")));
    }
  } else {
    // The button says what it does: another level takes the place of this one, or the control goes, and with
    // it the claim that an absence can be shown.
    const other = alternative?.sensitivity_epsilon ?? null;
    const fix = available<ProblemFix>({
      label: other !== null ? `Use ε = ${other.toFixed(2)} instead`
        : block.claim === "difference_or_equivalence" ? "Drop the positive control and claim a difference only" : "Use no positive control",
      apply: useRecommended("design.sensitivity_epsilon"),
    });
    for (const cell of cells) {
      const arms = epsilonArms(config, cell, epsilon);
      const node = `gate:pos:${laneKey(cell)}`;
      if (arms.length === 0) {
        add("design.epsilon_missing", "error", `No ε = ${epsilon.toFixed(2)} arm in ${cellText(cell)}. Add it in Compare, or choose 'None'.`, node, fix);
        continue;
      }
      if (!arms.some((arm) => compared(arm, "prng_epsilon"))) {
        add("design.epsilon_off", "error", `The ε = ${epsilon.toFixed(2)} arm is switched off in ${cellText(cell)}.`, node, fix);
      }
      const seeds = controlSeeds(config, cell);
      if (arms.some((arm) => seeds.includes(arm.study_seed ?? null))) {
        add("design.epsilon_seed_shared", "error", "The ε arm shares a seed with a control arm, which overstates sensitivity.", node, fix);
      }
    }
  }

  for (const declared of block.margins ?? []) {
    const card = registry.get(declared.card_id);
    if (card === undefined) continue;
    const why = marginRefusal(card, declared.endpoint, declared.margin);
    if (why !== null) {
      const ref = `${declared.card_id}/${declared.endpoint}`;
      add("design.margin_not_allowed", "error", `${metaFor(declared.card_id, declared.endpoint).label} ${why}.`, `endpoint:${ref}`, recommended(`design.margins.${ref}`));
    }
  }

  const hypotheses = draftHypotheses(config, cards);
  const alpha = block.alpha ?? 0.05;
  const claimsAbsence = block.claim === "difference_or_equivalence";
  for (const hypothesis of hypotheses) {
    const size = hypothesis.sample_size;
    if (size.basis === "no_closed_form") {
      // A test on pooled tokens counts its planned data on the selected evals with a prompt list of their own
      // (`inPlannedStream`). With none of them the hypothesis registers no amount of data, and the
      // completeness gate has nothing to hold the run to.
      if (hypothesis.planned_units === 0) {
        add("design.no_planned_data", "error",
          `${hypothesis.label} is tested on the tokens of the replies the study generates, and no selected eval generates replies to a prompt list of its own. The study therefore plans no data for this outcome, and the analysis cannot check how much of the planned data it used. Select an eval that has its own prompt list.`,
          `endpoint:${hypothesis.card_id}/${hypothesis.endpoint}`);
      }
      continue;
    }
    const card = registry.get(hypothesis.card_id);
    const k = selections.get(hypothesis.card_id)?.overrides?.k ?? card?.k ?? null;
    // More prompts are the recommended alternative only while the eval has more than the study uses.
    const morePrompts = card !== undefined && card.dataset.items > size.n ? recommended(`cards.${hypothesis.card_id}.items`) : undefined;
    const below = size.n < size.n_floor;
    const controlFloor = size.control_floor ?? null;
    const uncontrolled = controlFloor !== null && size.n < controlFloor;
    if (below) {
      add("design.below_floor", "error",
        `${hypothesis.label}: ${size.n} prompts cannot reach significance at any effect size. At least ${size.n_floor} are needed.`,
        `card:${hypothesis.card_id}`, card !== undefined && card.dataset.items >= size.n_floor ? morePrompts : undefined);
    }
    if (uncontrolled) {
      add("design.control_below_floor", "error",
        `${hypothesis.label}: with ${size.n} prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the outcome could only come out invalid. At least ${controlFloor} prompts are needed.`,
        `card:${hypothesis.card_id}`, card !== undefined && card.dataset.items >= controlFloor ? morePrompts : undefined);
    }
    if (!below && !uncontrolled && size.rating === "insufficient") {
      const [detects, target] = size.mde_raw !== null && hypothesis.margin !== null
        ? [size.mde_raw.toFixed(3), `±${formatG(hypothesis.margin)}`]
        : [`${(size.mde_standardized ?? 0).toFixed(2)} SD`, `${REFERENCE_EFFECT.toFixed(2)} SD`];
      // Repetitions are the way out where the eval has no more prompts.
      const kRecommended = size.k_recommended ?? null;
      const moreK = size.reachable === false && kRecommended !== null && kRecommended !== k && card !== undefined
        ? ` With k = ${kRecommended}, the ${card.dataset.items} prompts of the eval are enough.`
        : "";
      add("design.insufficient", "warning",
        `${hypothesis.label}: ${size.n} prompts detect ≥ ${detects}; ${target} needs ${size.n_recommended}.${moreK}`,
        // The button sets the number its label and the sentence before it name, whatever else the eval's other outcomes ask for.
        `card:${hypothesis.card_id}`, morePrompts ?? (moreK === "" || kRecommended === null || card === undefined ? undefined
          : { label: `Use k = ${kRecommended}`, apply: (study) => setCardK(study, hypothesis.card_id, kRecommended === card.k ? null : kRecommended) }));
    }
    // 'Equivalent' also needs the biased arm detected on this outcome, and nothing rates that: the size of
    // its effect is unknown before a run.
    if (claimsAbsence && epsilon !== null && reachableVerdicts(hypothesis, block.claim).equivalent === null) {
      const cell = [hypothesis.model, hypothesis.sampling] as const;
      const seeds = cellArms(config, cell).filter((arm) => compared(arm, "prng_seeded")).length;
      const shift = positiveControlShift(hypothesis, k, seeds, alpha);
      const needs = shift === null ? "the biased arm to be detected on the same outcome" : `the biased arm to move it by at least ${shift.toFixed(3)}`;
      add("design.control_not_rated", "warning",
        `'Equivalent' on ${hypothesis.label} needs ${needs}. Whether a sampler bias of ε = ${epsilon.toFixed(2)} does that is not known before the run.`,
        `gate:pos:${laneKey(cell)}`);
    }
    // A margin is only confirmed by an equivalence claim; without one the floor has nothing to warn about.
    if (block.claim === "difference_or_equivalence" && hypothesis.margin !== null && size.equivalence_floor !== null && hypothesis.margin < size.equivalence_floor) {
      add("design.margin_below_floor", "warning",
        `±${formatG(hypothesis.margin)} cannot be confirmed with ${size.n} prompts; the smallest confirmable margin is ±${size.equivalence_floor.toFixed(3)}.`,
        `endpoint:${hypothesis.card_id}/${hypothesis.endpoint}`, morePrompts);
    }
  }
  const families = familySizes(hypotheses);
  if (hypotheses.length > 0 && planPermutations(families, alpha) > MAX_PERMUTATIONS) {
    // The mildest level first: it asks for the fewest permutations and removes nothing from the study.
    const fix = alpha !== 0.05 && planPermutations(families, 0.05) <= MAX_PERMUTATIONS ? recommended("design.alpha")
      : alternative !== null && alternative.confirmatory.length < block.confirmatory.length ? recommended("design.confirmatory")
        : undefined;
    add("design.too_many_tests", "error",
      `${families.difference} tests with ${families.negative_control} seed-pair controls at α = ${formatG(alpha)} need more permutations than the harness runs. Remove outcomes, comparisons or control seeds.`,
      "family", fix);
  }
  const notEquivalent = hypotheses.filter((hypothesis) => reachableVerdicts(hypothesis, block.claim).equivalent !== null).map((hypothesis) => hypothesis.id);
  if (hypotheses.length > 0 && claimsAbsence && notEquivalent.length === hypotheses.length) {
    const fix = alternative === null ? undefined
      : alternative.claim !== block.claim ? recommended("design.claim")
        : (alternative.sensitivity_epsilon ?? null) !== epsilon ? recommended("design.sensitivity_epsilon")
          : undefined;
    add("design.equivalent_unreachable", "warning", "No outcome can return 'equivalent' as configured.", "family", fix);
  } else if (claimsAbsence && notEquivalent.length > 0) {
    // The study verdict is 'no effect within margins' only when every hypothesis is equivalent, and these cannot be.
    add("design.study_absence_unreachable", "warning",
      `The study as a whole cannot conclude 'no effect within margins': ${notEquivalent.join(", ")} cannot return 'equivalent'. An outcome that has a margin can still be reported as equivalent on its own.`,
      "family");
  }
  if (!(config.expectations ?? "").trim()) add("design.no_expectations", "warning", "No expectations recorded.", "study");
}

/** The problems that belong to one step. */
export function problemsFor(stepId: StepId, all: readonly Problem[]): Problem[] {
  return all.filter((problem) => problem.stepId === stepId);
}

/** Whether anything in the list stops the study from being registered. */
export function hasErrors(all: readonly Problem[]): boolean {
  return all.some((problem) => problem.severity === "error");
}
