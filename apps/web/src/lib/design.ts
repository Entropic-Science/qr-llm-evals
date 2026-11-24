import {
  endpointMeta, type Arm, type CardSelection, type Contrast, type EndpointMeta, type EvalCard, type FamilySizes,
  type HypothesisPlan, type StudyConfig, type StudyDesign,
} from "@qr-evals/contracts";
import { MARGIN_CAP_FRACTION, MARGIN_FLOOR_FRACTION, PRECISION, mdeStandardized, rate, sdBound } from "./power";
import { limitStands } from "./token-limits";

// From a study's design block to its hypotheses, in the browser.
//
// A mirror of `backend/src/qr_llm_evals/cards/design.py` (`eligible`, `recommend`, `recommend_k`,
// `draft_hypotheses`, `plan_permutations`, `positive_control_shift`, `reachable_verdicts` and the helpers they
// stand on), checked against `packages/contracts/fixtures/design.json`.
// The setup pages need these synchronously and offline; the hypotheses a study registers are always the
// server's `plan.design`, so what this module drafts is shown as a preview.
//
// The backend works on a dumped config in which every arm key is present. A config held by the browser may
// leave out the keys that have defaults, so arms are read through `observerMode` and `availability`.

export type Verdict = "unavailable" | "invalid" | "effect" | "effect_within_margin" | "equivalent" | "inconclusive";
export type Reason =
  | "not_produced" | "incomplete" | "negative_control" | "negative_control_cannot_fail" | "equivalence_not_claimed"
  | "equivalence_not_testable" | "no_margin" | "interval_exceeds_margin" | "positive_control_absent" | "positive_control_failed";
/** Why a registered hypothesis cannot resolve to a verdict. */
export type Unreachable = Reason | "below_n_floor" | "control_cannot_fail";
/** A (model, sampling) pair: one comparison. Cells are never pooled. */
export type Cell = readonly [model: string, sampling: string];
type Ref = readonly [cardId: string, endpoint: string];

/** The tests a hypothesis can be registered on: the three the analysis resolves to a verdict. */
export const HYPOTHESIS_TESTS: readonly string[] = ["paired_permutation", "ks_two_sample", "mmd_hamming_permutation"];
/** The only test with a paired difference, and so the only one a margin applies to. */
export const PAIRED_TEST = "paired_permutation";
/** A study registers at most this many confirmatory endpoints. */
export const MAX_CONFIRMATORY = 8;
export const MIN_PERMUTATIONS = 10_000;
/** More permutations than this per test do not finish in a reasonable analysis run. */
export const MAX_PERMUTATIONS = 100_000;
/** Permutations per unit of `m / alpha` for `m` difference tests: the smallest p a difference test can return is then a twentieth of its corrected level. */
export const PERMUTATIONS_PER_LEVEL = 20;
/**
 * Permutations per unit of `F / alpha` for `F` seed-pair control tests: the smallest p a seed pair can return is
 * then half its corrected level. A control is only asked whether it falls below its level, so it needs room
 * under the level and not the resolution a difference test is read at.
 */
export const CONTROL_PERMUTATIONS_PER_LEVEL = 2;
/** The positive control a study gets when it has a choice. */
export const PREFERRED_EPSILON = 0.10;

// The two tables of `cards/statistical_plan.py` that name an endpoint's test. They are not part of the
// generated contracts; the `ineligible_reason` vectors of design.json print the test of every headline
// endpoint, so a row that drifts from the backend fails the parity test.
const PRIMARY_TESTS: readonly (readonly [cardId: string, endpoint: string, test: string])[] = [
  ["tier0_pit_residuals", "pit_ks_two_sample", "ks_two_sample"],
  ["tier0_mmd_hamming", "mmd_hamming", "mmd_hamming_permutation"],
  ["unpredictabench", "ks100_pass_rate", "paired_permutation"],
  ["novelty_bench", "distinct_k", "paired_permutation"],
];
const SPECIAL_TESTS: readonly (readonly [cardId: string, endpoint: string, test: string])[] = [
  ["tier0_pit_residuals", "pit_ks_one_sample", "ks_uniform"],
  ["tier0_pit_residuals", "pit_anderson_darling", "anderson_darling_uniform"],
  ["tier0_pit_residuals", "pit_lag1_autocorr", "lag1_autocorrelation"],
  ["tier0_pit_residuals", "pit_first_token_ks", "first_token_ks_uniform"],
  ["tier0_pit_residuals", "u_uniformity", "ks_uniform"],
  ["tier0_pit_residuals", "u_vs_r_agreement", "u_vs_residual_agreement"],
  ["tier0_mmd_hamming", "mmd_hamming_seed_vs_seed", "mmd_hamming_permutation"],
  ["stream_deviation_windows", "stouffer_z_supervised_vs_unsupervised", "stouffer_z_windows"],
  ["stream_deviation_windows", "window_permutation_p", "window_permutation"],
  ["stream_deviation_windows", "cumulative_deviation", "descriptive_cumulative_deviation"],
  ["target_hit_ranking", "rank1_hit_rate", "blinded_rank_hit_vs_chance"],
  ["target_hit_ranking", "mean_rank", "blinded_rank_vs_chance"],
  ["self_reported_coincidence", "observer_wcs_score", "descriptive_by_belief"],
];
const key = (cardId: string, endpoint: string) => `${cardId}/${endpoint}`;
const NAMED_TESTS = new Map([...SPECIAL_TESTS, ...PRIMARY_TESTS].map(([cardId, endpoint, test]) => [key(cardId, endpoint), test]));

function byId(cards: readonly EvalCard[]): Map<string, EvalCard> {
  return new Map(cards.map((card) => [card.id, card]));
}

/** Python orders strings by code point; JavaScript's `<` orders them by UTF-16 unit. */
function byCodePoint(a: string, b: string): number {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const difference = x[i]!.codePointAt(0)! - y[i]!.codePointAt(0)!;
    if (difference !== 0) return difference;
  }
  return x.length - y.length;
}

/** Python's `format(value, "g")`: six significant digits, trailing zeros dropped. */
export function formatG(value: number): string {
  if (value === 0 || !Number.isFinite(value)) return String(value);
  const [mantissa, exponentText] = value.toExponential(5).split("e") as [string, string];
  const exponent = Number(exponentText);
  const trimmed = (text: string) => (text.includes(".") ? text.replace(/\.?0+$/, "") : text);
  if (exponent < -4 || exponent >= 6) return `${trimmed(mantissa)}e${exponent < 0 ? "-" : "+"}${String(Math.abs(exponent)).padStart(2, "0")}`;
  return trimmed(value.toFixed(Math.max(0, 5 - exponent)));
}

// ── endpoints ────────────────────────────────────────────────────────────────

/** The registered facts about an endpoint, or the cautious default for one without a row. */
export function metaFor(cardId: string, endpoint: string): EndpointMeta {
  return endpointMeta[key(cardId, endpoint)] ?? { label: endpoint, scale: "statistic", level: "sample", range: null, executable: false };
}

/** The test the plan names for an endpoint, as the built-in plan resolves it. */
export function plannedTest(card: EvalCard, endpoint: string): string {
  return NAMED_TESTS.get(key(card.id, endpoint)) ?? (card.paired_stat === "paired_binomial" ? "paired_binomial" : PAIRED_TEST);
}

/** Why an endpoint cannot decide a study, as the end of a sentence; `null` if it can. */
export function ineligibleReason(card: EvalCard | undefined, endpoint: string): string | null {
  if (card === undefined) return "the eval is not in the registry";
  if (card.status !== "implemented") return "the eval is not implemented";
  if (!card.endpoints.primary.includes(endpoint)) return "it is not the eval's headline outcome";
  const test = plannedTest(card, endpoint);
  if (!HYPOTHESIS_TESTS.includes(test)) return `its test (${test}) does not resolve to a verdict`;
  const meta = metaFor(card.id, endpoint);
  if (!meta.executable) {
    return meta.level === "prompt_group"
      ? "it is scored per prompt group, which is not computed yet"
      : "it is not computed from single samples yet";
  }
  return null;
}

/** Whether a confirmatory hypothesis may be registered on `endpoint`. */
export function eligible(card: EvalCard, endpoint: string): boolean {
  return ineligibleReason(card, endpoint) === null;
}

function statesSesoi(card: EvalCard, endpoint: string): boolean {
  return card.sesoi !== undefined && Object.hasOwn(card.sesoi, endpoint);
}

/** The card's SESOI, where the endpoint's test can use it as an equivalence margin. */
export function cardMargin(card: EvalCard, endpoint: string): number | null {
  if (plannedTest(card, endpoint) !== PAIRED_TEST) return null;
  const sesoi = statesSesoi(card, endpoint) ? card.sesoi?.[endpoint] : undefined;
  return sesoi !== undefined && sesoi > 0 ? sesoi : null;
}

/** Why `endpoint` cannot take a declared margin of any size; `null` if one within its limits can stand. */
export function noMarginReason(card: EvalCard, endpoint: string): string | null {
  if (plannedTest(card, endpoint) !== PAIRED_TEST) return "has no paired difference a margin could apply to";
  if (statesSesoi(card, endpoint)) return "takes its margin from the eval card";
  if (metaFor(card.id, endpoint).range === null) return "has no fixed range to state a margin against";
  return null;
}

/**
 * Why a declared margin cannot stand on `endpoint`; `null` if it can.
 *
 * The test comes first: a card may state a SESOI for an endpoint whose test has no paired difference (the KS
 * residual test does), and such an endpoint has no margin at all, whatever the card says.
 */
export function marginRefusal(card: EvalCard, endpoint: string, margin: number): string | null {
  const never = noMarginReason(card, endpoint);
  if (never !== null) return never;
  const bounds = metaFor(card.id, endpoint).range!;
  const width = bounds[1] - bounds[0];
  const cap = MARGIN_CAP_FRACTION * width;
  if (margin > cap + 1e-12) return `cannot take a margin above ${formatG(cap)} (${(MARGIN_CAP_FRACTION * 100).toFixed(0)}% of its range)`;
  const least = MARGIN_FLOOR_FRACTION * width;
  if (margin < least - 1e-12) return `cannot take a margin below ${formatG(least)} (${(MARGIN_FLOOR_FRACTION * 100).toFixed(1)}% of its range)`;
  return null;
}

// ── arms ─────────────────────────────────────────────────────────────────────

const observerMode = (arm: Arm) => arm.observer_mode ?? "unsupervised";
const availability = (arm: Arm) => arm.availability ?? "available";
const inCell = (arm: Arm, cell: Cell) => arm.model === cell[0] && arm.sampling === cell[1];

/** Whether an arm takes part in a comparison as `source`: unsupervised and available. */
export function compared(arm: Arm, source: Arm["source"]): boolean {
  return arm.source === source && observerMode(arm) === "unsupervised" && availability(arm) === "available";
}

function same(a: number | null | undefined, b: number | null | undefined): boolean {
  return a !== null && a !== undefined && b !== null && b !== undefined && Math.abs(a - b) < 1e-12;
}

function sortedCells(arms: readonly Arm[]): Cell[] {
  const cells = new Map<string, Cell>();
  for (const arm of arms) cells.set(JSON.stringify([arm.model, arm.sampling]), [arm.model, arm.sampling]);
  return [...cells.values()].sort((a, b) => byCodePoint(a[0], b[0]) || byCodePoint(a[1], b[1]));
}

/** The (model, sampling) pairs with an available unsupervised quantum arm, sorted. */
export function sourceCells(config: StudyConfig): Cell[] {
  return sortedCells(config.arms.filter((arm) => compared(arm, "qrng")));
}

/** Every arm of one comparison, whatever its role or availability. */
export function cellArms(config: StudyConfig, cell: Cell): Arm[] {
  return config.arms.filter((arm) => inCell(arm, cell));
}

/** The biased arms of a comparison at one ε, available or not. */
export function epsilonArms(config: StudyConfig, cell: Cell, epsilon: number): Arm[] {
  return cellArms(config, cell).filter((arm) => arm.source === "prng_epsilon" && same(arm.epsilon, epsilon));
}

/** The seeds of a comparison's seeded arms, available or not. */
export function controlSeeds(config: StudyConfig, cell: Cell): (number | null)[] {
  return cellArms(config, cell).filter((arm) => arm.source === "prng_seeded").map((arm) => arm.study_seed ?? null);
}

/** A comparison as prose names it. */
export const cellText = (cell: Cell) => `${cell[0]} · ${cell[1]}`;
/** A comparison as node ids name it: the lane key of the study map. */
export const laneKey = (cell: Cell) => `${cell[0]}/${cell[1]}`;

/** The positive control every cell can run: ε 0.10 if it can, else the largest. */
function recommendedEpsilon(config: StudyConfig): number | null {
  const cells = sourceCells(config);
  if (cells.length === 0) return null;

  const usable = (level: number) => cells.every((cell) => {
    const arms = epsilonArms(config, cell, level);
    const arm = arms[0];
    if (arms.length !== 1 || arm === undefined || !compared(arm, "prng_epsilon")) return false;
    return !controlSeeds(config, cell).includes(arm.study_seed ?? null);
  });

  const levels = [...new Set(config.arms.filter((arm) => arm.source === "prng_epsilon" && arm.epsilon).map((arm) => arm.epsilon as number))].sort((a, b) => b - a);
  const candidates = levels.filter(usable);
  return candidates.find((level) => same(level, PREFERRED_EPSILON)) ?? candidates[0] ?? null;
}

// ── the recommended design ───────────────────────────────────────────────────

/**
 * The design block a study gets by default, or `null` if nothing can decide it.
 *
 * Confirmatory are the built-in primaries that are selected and eligible, then every selected eligible
 * headline endpoint whose card states a SESOI. The absence of an effect is claimed only where it can be shown:
 * a positive control every cell runs, and a margin on at least one endpoint.
 */
export function recommendDesign(config: StudyConfig, cards: readonly EvalCard[]): StudyDesign | null {
  const registry = byId(cards);
  const selected = config.cards.flatMap((selection) => registry.get(selection.card_id) ?? []);
  const chosen = byId(selected);
  let refs: Ref[] = PRIMARY_TESTS
    .filter(([cardId, endpoint]) => { const card = chosen.get(cardId); return card !== undefined && eligible(card, endpoint); })
    .map(([cardId, endpoint]): Ref => [cardId, endpoint]);
  const listed = (cardId: string, endpoint: string) => refs.some((ref) => ref[0] === cardId && ref[1] === endpoint);
  for (const card of selected) {
    for (const endpoint of card.endpoints.primary) {
      if (statesSesoi(card, endpoint) && eligible(card, endpoint) && !listed(card.id, endpoint)) refs.push([card.id, endpoint]);
    }
  }
  if (refs.length === 0) {
    const first = selected.flatMap((card) => card.endpoints.primary.filter((endpoint) => eligible(card, endpoint)).map((endpoint): Ref => [card.id, endpoint]))[0];
    if (first === undefined) return null;
    refs = [first];
  }
  refs = refs.slice(0, MAX_CONFIRMATORY);
  const epsilon = recommendedEpsilon(config);
  const hasMargin = refs.some(([cardId, endpoint]) => cardMargin(chosen.get(cardId)!, endpoint) !== null);
  return {
    version: 1,
    claim: epsilon !== null && hasMargin ? "difference_or_equivalence" : "difference",
    confirmatory: refs.map(([cardId, endpoint]) => ({ card_id: cardId, endpoint })),
    alpha: 0.05,
    // The level is the arm's own ε, as the backend returns it. Arms carry ε on the registered grid.
    sensitivity_epsilon: epsilon as NonNullable<StudyDesign["sensitivity_epsilon"]> | null,
    margins: [],
  };
}

/**
 * The repetitions (k) a study gets by default where an eval's own k is too few, by card id.
 *
 * Where all the prompts of an eval are too few for the margin of an outcome that decides the study, this names
 * the fewest repetitions, within the eval's bounds, at which they are enough (`sample_size.k_recommended`). An
 * eval that is not in the result keeps its own k: either that is enough, or no k within its bounds is. The
 * hypotheses are those of the declared design, or of the recommended one when the study declares none. The
 * answer does not depend on the study's k and prompt overrides, so applying it does not change it.
 */
export function recommendK(config: StudyConfig, cards: readonly EvalCard[]): Record<string, number> {
  const design = config.design ?? recommendDesign(config, cards);
  if (design === null) return {};
  const registry = byId(cards);
  const out: Record<string, number> = {};
  for (const hypothesis of draftHypotheses({ ...config, design }, cards)) {
    const k = hypothesis.sample_size.k_recommended ?? null;
    const own = registry.get(hypothesis.card_id)?.k ?? null;
    if (k !== null && own !== null && k > own) out[hypothesis.card_id] = Math.max(out[hypothesis.card_id] ?? 0, k);
  }
  return out;
}

// ── contrasts ────────────────────────────────────────────────────────────────

/**
 * The comparisons of the statistical plan, in the plan's order: per cell, quantum against the seeded runs,
 * every pair of seeds, every biased arm against the seeded runs, supervised against unsupervised. A hypothesis
 * points at them by index. Different models, sampling presets and unavailable arms are never pooled.
 */
export function planContrasts(config: StudyConfig): Contrast[] {
  const contrasts: Contrast[] = [];
  for (const cell of sortedCells(config.arms)) {
    const group = cellArms(config, cell).filter((arm) => availability(arm) === "available");
    const prng = group.filter((arm) => arm.source === "prng_seeded" && observerMode(arm) === "unsupervised");
    const qrng = group.filter((arm) => arm.source === "qrng");
    const baseline = qrng.filter((arm) => observerMode(arm) === "unsupervised");
    const add = (kind: Contrast["kind"], a: string[], b: string[], epsilon: number | null = null) => {
      if (a.length > 0 && b.length > 0) contrasts.push({ kind, model: cell[0], sampling: cell[1], a, b, epsilon });
    };
    const seeded = prng.map((arm) => arm.id);
    for (const arm of baseline.length > 0 ? baseline : qrng) add("source", [arm.id], seeded);
    prng.forEach((a, i) => {
      for (const b of prng.slice(i + 1)) {
        if ((a.study_seed ?? null) !== (b.study_seed ?? null)) add("seed_vs_seed", [a.id], [b.id]);
      }
    });
    for (const arm of group) {
      if (arm.source === "prng_epsilon") add("epsilon", [arm.id], seeded, arm.epsilon ?? null);
    }
    for (const arm of qrng) {
      if (observerMode(arm) === "supervised") for (const b of baseline) add("supervised", [arm.id], [b.id]);
    }
  }
  return contrasts;
}

// ── hypotheses ───────────────────────────────────────────────────────────────

// The subtraction absorbs the float error of the division (1000 / 0.01 is a hair above 100 000), which would
// otherwise round an exact count up.
const perLevel = (factor: number, tests: number, alpha: number) => Math.ceil((factor * tests) / alpha - 1e-9);

/** Permutations per test for `m` difference tests at level `alpha`, before the cap. */
export function plannedPermutations(m: number, alpha: number): number {
  return Math.max(MIN_PERMUTATIONS, perLevel(PERMUTATIONS_PER_LEVEL, m, alpha));
}

/** The fewest permutations the plan gives `mControl` seed-pair control tests. */
export function controlPermutations(mControl: number, alpha: number): number {
  return perLevel(CONTROL_PERMUTATIONS_PER_LEVEL, mControl, alpha);
}

/**
 * Permutations per test of a plan: what its difference tests and its controls each need.
 *
 * Both are read off permutation p-values, each corrected within its own Holm family, and a test with `P`
 * permutations returns no p-value below `1 / (P + 1)`. A difference test is given a resolution of a twentieth
 * of its corrected level. A seed-against-seed pair is only asked whether it falls below `alpha / F`, so its
 * smallest p-value is put at half that level and no lower: sized like a difference test, the seed pairs of a
 * study with five control seeds would ask for ten times the permutations of its hypotheses and put ordinary
 * designs over the cap. The count is the larger of the two. The controls decide it only from six control
 * seeds on (15 pairs for each hypothesis); sized on the difference tests alone, a study with seven would have
 * controls that cannot fail.
 */
export function planPermutations(families: FamilySizes, alpha: number): number {
  return Math.max(plannedPermutations(families.difference, alpha), controlPermutations(families.negative_control, alpha));
}

/** How many tests each Holm family corrects for. */
export function familySizes(hypotheses: readonly HypothesisPlan[]): FamilySizes {
  return {
    difference: hypotheses.length,
    equivalence: hypotheses.filter((hypothesis) => hypothesis.margin !== null).length,
    negative_control: hypotheses.reduce((total, hypothesis) => total + hypothesis.negative_controls.length, 0),
  };
}

/** A card with `told_actual` runs every prompt in three cells: told quantum, told pseudo-random, told nothing. */
const TOLD_CELLS = 3;
const toldCells = (card: EvalCard) => (card.told_actual ? TOLD_CELLS : 1);

/** A selection's k and prompts after its overrides, unchecked. */
function effective(card: EvalCard, selection: CardSelection): [k: number | null, items: number] {
  return [selection.overrides?.k ?? card.k ?? null, selection.overrides?.items ?? card.dataset.items];
}

/** Whether the built-in plan could be built from the selected evals: every card known, every override in bounds. */
function selectionsStand(config: StudyConfig, registry: ReadonlyMap<string, EvalCard>): boolean {
  return config.cards.every((selection) => {
    const card = registry.get(selection.card_id);
    if (card === undefined) return false;
    const k = selection.overrides?.k ?? null;
    if (k !== null) {
      if (card.k === null || card.k === undefined || !Number.isInteger(k)) return false;
      const [lo, hi] = card.k_bounds ?? [card.k, card.k];
      if (!(lo <= k && k <= hi)) return false;
    }
    const items = selection.overrides?.items ?? null;
    if (items !== null && !(Number.isInteger(items) && 1 <= items && items <= card.dataset.items)) return false;
    // A token limit on an eval that generates nothing, or one outside the eval's bounds, is refused too.
    const maxTokens = selection.overrides?.max_tokens ?? null;
    return maxTokens === null || limitStands(card.id, maxTokens);
  });
}

/**
 * Whether an eval's replies are counted as planned data of a token-stream test (`in_planned_stream`): it runs
 * (implemented, generating) on a prompt list fixed before the run. An eval derived from another eval's replies
 * asks as many prompts as those replies allow, so its item count is an expectation and it is not counted.
 */
export function inPlannedStream(card: EvalCard): boolean {
  return (
    card.est_tokens_per_sample > 0 && card.status === 'implemented' && !card.dataset.source.startsWith('derived:')
  );
}

/** Replies one arm is planned to generate on the evals with a fixed prompt list. */
function generationsPerArm(config: StudyConfig, registry: ReadonlyMap<string, EvalCard>): number {
  let total = 0;
  for (const selection of config.cards) {
    const card = registry.get(selection.card_id);
    if (card === undefined || !inPlannedStream(card)) continue;
    const [k, items] = effective(card, selection);
    total += items * (k !== null ? k : 1) * toldCells(card);
  }
  return total;
}

/** The declared confirmatory endpoints that can be tested, in declared order. */
function confirmatory(design: StudyDesign, config: StudyConfig, registry: ReadonlyMap<string, EvalCard>): Ref[] {
  const selected = new Set(config.cards.map((selection) => selection.card_id));
  const refs: Ref[] = [];
  for (const { card_id: cardId, endpoint } of design.confirmatory) {
    const card = registry.get(cardId);
    const listed = refs.some((ref) => ref[0] === cardId && ref[1] === endpoint);
    if (card !== undefined && selected.has(cardId) && eligible(card, endpoint) && !listed) refs.push([cardId, endpoint]);
  }
  return refs;
}

type Margin = readonly [margin: number | null, source: HypothesisPlan["margin_source"]];

/** An endpoint's margin: the card SESOI first, then a declared margin that can stand. */
function marginOf(design: StudyDesign, card: EvalCard, endpoint: string): Margin {
  const sesoi = cardMargin(card, endpoint);
  if (sesoi !== null) return [sesoi, "card_sesoi"];
  // The last declaration of an endpoint is the one that counts.
  const declared = (design.margins ?? []).filter((margin) => margin.card_id === card.id && margin.endpoint === endpoint).at(-1);
  if (declared !== undefined && marginRefusal(card, endpoint, declared.margin) === null) return [declared.margin, "declared"];
  return [null, "none"];
}

/**
 * The hypotheses a draft would register, without the refusals of the server's plan.
 *
 * A confirmatory endpoint that cannot be tested and a declared margin that cannot stand are left out rather
 * than refused, so a draft that is being edited still shows the rest. Empty for a study without a design
 * block, and while the selected evals themselves are not valid (an unknown card, an override out of bounds).
 */
export function draftHypotheses(config: StudyConfig, cards: readonly EvalCard[]): HypothesisPlan[] {
  const design = config.design;
  if (design === null || design === undefined) return [];
  const registry = byId(cards);
  if (!selectionsStand(config, registry)) return [];
  const contrasts = planContrasts(config);

  const alpha = design.alpha ?? 0.05;
  const epsilon = design.sensitivity_epsilon ?? null;
  const refs = confirmatory(design, config, registry);
  const margins = refs.map(([cardId, endpoint]) => marginOf(design, registry.get(cardId)!, endpoint));
  const selections = new Map(config.cards.map((selection) => [selection.card_id, selection]));

  const index = (kind: Contrast["kind"], cell: Cell) => contrasts.flatMap((contrast, i) => (
    contrast.kind === kind && contrast.model === cell[0] && contrast.sampling === cell[1] ? [i] : []
  ));

  // A cell is tested only where there is something to compare the quantum arm with.
  const cells = sourceCells(config).filter((cell) => index("source", cell).length > 0);
  const m = refs.length * cells.length;
  const mE = margins.filter(([margin]) => margin !== null).length * cells.length;
  // Every seed pair of every hypothesis: the family the negative control is corrected over.
  const mControl = refs.length * cells.reduce((total, cell) => total + index("seed_vs_seed", cell).length, 0);
  const generations = generationsPerArm(config, registry);

  const hypotheses: HypothesisPlan[] = [];
  refs.forEach(([cardId, endpoint], position) => {
    const card = registry.get(cardId)!;
    const meta = metaFor(cardId, endpoint);
    const test = plannedTest(card, endpoint) as HypothesisPlan["test"];
    const [margin, marginSource] = margins[position]!;
    const [k, items] = effective(card, selections.get(cardId)!);
    const pooled = meta.level === "token_stream";
    const units = pooled ? generations : items;
    const pairs = pooled ? generations : items * (k !== null ? k : 1) * toldCells(card);
    // The most repetitions a study may set: the card's upper bound, or its own k where it has no bounds or
    // the endpoint is defined for that k only.
    const own = card.k ?? null;
    const kMax = meta.k_locked || card.k_bounds === null || card.k_bounds === undefined ? own : card.k_bounds[1];
    for (const cell of cells) {
      const source = index("source", cell)[0]!;
      const seeds = contrasts[source]!.b.length;
      const positive = index("epsilon", cell).filter((i) => same(contrasts[i]!.epsilon, epsilon));
      const number = hypotheses.length + 1;
      const comparedWith = pooled ? "the pooled tokens of" : "the per-prompt mean of";
      hypotheses.push({
        id: `H${number}`,
        card_id: cardId,
        endpoint,
        label: meta.label,
        model: cell[0],
        sampling: cell[1],
        test,
        sided: test === "mmd_hamming_permutation" ? "one_sided_upper" : "two_sided",
        contrast: source,
        negative_controls: index("seed_vs_seed", cell),
        positive_control: positive[0] ?? null,
        margin,
        margin_source: marginSource,
        planned_units: units,
        planned_pairs: pairs,
        sample_size: rate({
          level: meta.level,
          range: meta.range,
          margin,
          n: units,
          dataset_items: card.dataset.items,
          k,
          told_cells: toldCells(card),
          pooled_seeds: seeds,
          alpha,
          m,
          m_e: mE,
          m_control: mControl,
          k_default: own,
          k_max: kMax,
        }),
        statement: `H${number}: on ${meta.label} (${cardId}), quantum sampling differs from ${comparedWith} ${seeds} seeded pseudo-random runs (${cellText(cell)}).`,
      });
    }
  });
  return hypotheses;
}

// ── verdicts a registered hypothesis can reach ───────────────────────────────

/**
 * Every verdict, with `null` if the hypothesis can resolve to it, else why not.
 *
 * What is ruled out is known at registration: a test without a paired difference has no margin, a study that
 * registered no equivalence claim or no positive control cannot return `equivalent`, and too few prompts
 * cannot reach significance at any effect size. With fewer prompts than the seed-against-seed control is asked
 * for (`control_floor`), the control gate makes the hypothesis invalid, so no finding is within reach; that is
 * the reason given wherever nothing else rules the verdict out.
 */
export function reachableVerdicts(hypothesis: HypothesisPlan, claim: string): Record<Verdict, Unreachable | null> {
  const size = hypothesis.sample_size;
  const belowFloor = size.basis !== "no_closed_form" && size.n < size.n_floor;
  const controlFloor = size.control_floor ?? null;
  const noControl: Unreachable | null = controlFloor !== null && size.n < controlFloor ? "control_cannot_fail" : null;
  // Why the hypothesis has no margin to test against, if it has none. The reasons come in the order the
  // decision matrix gives them.
  const marginless: Unreachable | null =
    hypothesis.test !== PAIRED_TEST ? "equivalence_not_testable" : hypothesis.margin === null ? "no_margin" : null;
  const equivalent: Unreachable | null =
    claim !== "difference_or_equivalence" ? "equivalence_not_claimed"
      : marginless !== null ? marginless
        : hypothesis.positive_control === null ? "positive_control_absent"
          : null;
  return {
    unavailable: null,
    invalid: null,
    effect: belowFloor ? "below_n_floor" : noControl,
    effect_within_margin: marginless !== null ? marginless : belowFloor ? "below_n_floor" : noControl,
    equivalent: equivalent !== null ? equivalent : noControl,
    inconclusive: noControl,
  };
}

/**
 * How far the biased arm must move the outcome to be detected with `POWER`; `null` where the outcome has no
 * variance bound.
 *
 * `equivalent` needs the positive control detected on the hypothesis's own outcome at the uncorrected level.
 * This is the smallest shift that comparison detects under the worst-case variance: the biased arm is one
 * seeded run, so its told cells count once, against `pooledSeeds` seeded runs. It is a property of the test
 * only. How far a sampler bias of ε moves a behavioural outcome is not known before a run, so whether the
 * control will be detected is not rated.
 */
export function positiveControlShift(hypothesis: HypothesisPlan, k: number | null, pooledSeeds: number, alpha: number): number | null {
  const meta = metaFor(hypothesis.card_id, hypothesis.endpoint);
  const n = hypothesis.sample_size.n;
  if (hypothesis.test !== PAIRED_TEST || meta.range === null || n < 1 || pooledSeeds < 1) return null;
  const sd = sdBound(meta.range[0], meta.range[1], meta.level, k !== null ? k : 1, 1, pooledSeeds);
  return Number((mdeStandardized(n, alpha, 1) * sd).toFixed(PRECISION));
}
