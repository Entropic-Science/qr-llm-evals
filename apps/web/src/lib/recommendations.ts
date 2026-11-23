import type { Arm, EvalCard, HypothesisPlan, SampleSize } from "@qr-evals/contracts";
import { recommendedRepetitions } from "./config-edits";
import type { WizardStudyConfig } from "./config-store";
import {
  cardMargin, cellArms, compared, draftHypotheses, familySizes, formatG, laneKey, marginRefusal, metaFor, planPermutations,
  recommendDesign, sourceCells, type Cell,
} from "./design";
import { MARGIN_CAP_FRACTION, MARGIN_FLOOR_FRACTION, MIN_COMPLETENESS, POWER, REFERENCE_EFFECT } from "./power";
import { problems, type Problem, type StepId } from "./problems";
import { DEFAULT_SEEDS } from "./roster";

// How every setting of a study compares with what is recommended, as one list.
//
// A `Rec` is a setting, its value and a state. The wizard and the map draw the same list with the same chip
// (`RecChip`), so a setting can never be green in one view and grey in the other. Green (`sufficient`) is
// reserved for a sample size that `power.ts` computed to be enough for a stated margin; a value that merely
// equals its default is `recommended`, which is neutral. Every number here is the browser's preview: the
// registered ones are the server's `plan.design`.

export type RecState = "sufficient" | "marginal" | "insufficient" | "unknown" | "recommended" | "custom" | "invalid" | "locked";

export interface Rec {
  /** The setting: `cards.{id}.items`, `cards.{id}.k`, `roster.seeds`, `design.alpha`, `design.margins.{card}/{endpoint}`, `fixed.power`, … */
  path: string;
  label: string;
  value: string;
  /** The recommended value as text, or `null` where there is none to offer. */
  recommended: string | null;
  state: RecState;
  /** The three lines of the tooltip: what the study has, what it needs, and on what basis that is said. */
  have: string;
  need: string;
  basis: string;
  stepId: StepId;
  nodeId: string;
}

export interface RecStateDef {
  /** The chip's modifier class. Ink, wash and border colours are in `styles/tokens.css`. */
  className: string;
  /** One character, or `null` for the padlock `RecChip` draws. */
  glyph: string | null;
  border: "solid" | "dashed" | "dotted" | "hairline";
  legend: string;
}

/** The eight states, in legend order. Shape (glyph, border) carries the state as well as colour does. */
export const REC_STATES: Readonly<Record<RecState, RecStateDef>> = {
  sufficient: { className: "rec--sufficient", glyph: "✓", border: "solid", legend: "Enough for the stated margin" },
  marginal: { className: "rec--marginal", glyph: "≈", border: "solid", legend: "Close, or no raw-unit margin" },
  insufficient: { className: "rec--insufficient", glyph: "↓", border: "dashed", legend: "Too few prompts" },
  unknown: { className: "rec--unknown", glyph: "?", border: "dotted", legend: "No closed-form power" },
  recommended: { className: "rec--recommended", glyph: "=", border: "solid", legend: "Recommended value" },
  custom: { className: "rec--custom", glyph: "≠", border: "solid", legend: "Your choice" },
  invalid: { className: "rec--invalid", glyph: "✕", border: "solid", legend: "Not runnable" },
  locked: { className: "rec--locked", glyph: null, border: "hairline", legend: "Set by the harness" },
};

/** Worst first. A card with several hypotheses is rated by its worst one. */
const RATING_ORDER: readonly SampleSize["rating"][] = ["insufficient", "marginal", "unknown", "sufficient"];

const CLAIM_TEXT: Record<string, string> = {
  difference: "A difference only",
  difference_or_equivalence: "A difference, or its absence within a margin",
};
const TEST_TEXT: Record<HypothesisPlan["test"], string> = {
  paired_permutation: "Paired sign-flip test",
  ks_two_sample: "Two-sample KS test",
  mmd_hamming_permutation: "MMD permutation test",
};
const POOLED_TEST: Record<string, string> = { ks_two_sample: "a KS test", mmd_hamming_permutation: "an MMD test" };
const ENVELOPE = "Amplifier zscore_mean, calibration 0, prefetch off.";

// Not rounded to a whole percent: 1 − α at α = 0.005 is 99.5%, and "100%" would contradict the registered plan.
const percent = (fraction: number) => `${Number((fraction * 100).toFixed(6))}%`;
const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;
const epsilonText = (epsilon: number | null | undefined) => (epsilon === null || epsilon === undefined ? "None" : `ε ${epsilon.toFixed(2)}`);
const refKey = (ref: { card_id: string; endpoint: string }) => `${ref.card_id}/${ref.endpoint}`;
const sameRefs = (a: readonly { card_id: string; endpoint: string }[], b: readonly { card_id: string; endpoint: string }[]) =>
  a.length === b.length && a.every((ref, index) => refKey(ref) === refKey(b[index]!));

/** The three tooltip lines for a sample size. `m` is the number of confirmatory tests; `prompts` is how many the eval has. */
function evidence(hypothesis: HypothesisPlan, m: number, alpha: number, prompts: number): Pick<Rec, "have" | "need" | "basis"> {
  const size = hypothesis.sample_size;
  if (size.basis === "no_closed_form") {
    // Nothing here promises a rating from elsewhere: a test on pooled tokens has no margin, so the positive
    // control, which only 'equivalent' consults, never bears on it.
    return {
      have: `No closed-form power for ${POOLED_TEST[hypothesis.test] ?? "this test"} on pooled tokens.`,
      need: "Its sample size is not rated.",
      basis: "This outcome has no margin, so it cannot return 'equivalent'.",
    };
  }
  const holm = `${TEST_TEXT[hypothesis.test]}, Holm over ${plural(m, "test")} (α′ = ${(alpha / Math.max(1, m)).toFixed(4)})`;
  const basis = size.basis === "margin_worst_case" ? `${holm}, worst-case variance.` : "No raw-unit margin for this outcome.";
  const short = size.reachable === false ? `; this eval has ${prompts}.` : ".";
  if (size.n < size.n_floor) {
    return { have: `${plural(size.n, "prompt")} cannot reach significance at any effect size.`, need: `At least ${size.n_floor} are needed${short}`, basis };
  }
  const controlFloor = size.control_floor ?? null;
  if (controlFloor !== null && size.n < controlFloor) {
    return {
      have: `With ${plural(size.n, "prompt")} it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed and the outcome could only come out invalid.`,
      need: `At least ${controlFloor} are needed${short}`,
      basis,
    };
  }
  // Where the eval has no more prompts, repetitions are the way to make the ones it has enough.
  const kRecommended = size.k_recommended ?? null;
  const byK = size.reachable === false && size.rating !== "sufficient"
    ? (kRecommended !== null ? ` With k = ${kRecommended}, they are enough.` : " No number of repetitions the eval allows makes them enough.")
    : "";
  if (size.basis === "margin_worst_case" && size.mde_raw !== null && hypothesis.margin !== null) {
    return {
      have: `${plural(size.n, "prompt")} detect ≥ ${size.mde_raw.toFixed(3)} at ${percent(POWER)} power.`,
      need: `±${formatG(hypothesis.margin)} needs ≥ ${size.n_recommended}${short}${byK}`,
      basis,
    };
  }
  return {
    have: `${plural(size.n, "prompt")} detect ≥ ${(size.mde_standardized ?? 0).toFixed(2)} SD.`,
    need: `${REFERENCE_EFFECT.toFixed(2)} SD needs ≥ ${size.n_recommended}${short}`,
    basis,
  };
}

/** One rec per selected eval for its prompts, and one for its k where it has one. */
function cardRecs(config: WizardStudyConfig, cards: readonly EvalCard[], registry: ReadonlyMap<string, EvalCard>, hypotheses: readonly HypothesisPlan[]): Rec[] {
  const recs: Rec[] = [];
  const confirmatory = new Set((config.design?.confirmatory ?? []).map((ref) => ref.card_id));
  const alpha = config.design?.alpha ?? 0.05;
  for (const selection of config.cards) {
    const card = registry.get(selection.card_id);
    if (card === undefined) continue;
    const nodeId = `card:${card.id}`;
    const decides = confirmatory.has(card.id);
    const stepId: StepId = decides ? "design" : "measure";
    const prompts = card.dataset.items;
    const items = selection.overrides?.items ?? prompts;
    const own = hypotheses.filter((hypothesis) => hypothesis.card_id === card.id);
    const worst = [...own].sort((a, b) => RATING_ORDER.indexOf(a.sample_size.rating) - RATING_ORDER.indexOf(b.sample_size.rating))[0];

    if (worst !== undefined) {
      const pooled = worst.sample_size.basis === "no_closed_form";
      recs.push({
        // A test on the token stream has no prompts of its own: it reads the tokens the study generates.
        path: `cards.${card.id}.items`, label: "Prompts", value: pooled ? "generated tokens" : plural(items, "prompt"),
        recommended: pooled ? null : String(prompts), state: worst.sample_size.rating,
        ...evidence(worst, hypotheses.length, alpha, prompts), stepId, nodeId,
      });
    } else if (prompts > 0) {
      const inBounds = Number.isInteger(items) && items >= 1 && items <= prompts;
      recs.push({
        path: `cards.${card.id}.items`, label: "Prompts", value: plural(items, "prompt"), recommended: String(prompts),
        state: !inBounds ? "invalid" : items === prompts ? "recommended" : "custom",
        have: items === prompts ? `All ${plural(prompts, "prompt")} of this eval.` : `${items} of ${plural(prompts, "prompt")}.`,
        need: !inBounds ? `Between 1 and ${prompts}.` : items === prompts ? "Nothing to add." : `Uses the first ${items} prompts in file order.`,
        basis: decides ? "Its outcome cannot be tested as configured; not rated." : "No outcome of this eval decides the study; not rated.",
        stepId, nodeId,
      });
    }

    if (card.k === null || card.k === undefined) continue;
    const k = selection.overrides?.k ?? card.k;
    const [lo, hi] = card.k_bounds ?? [card.k, card.k];
    const locked = lo === hi || card.endpoints.primary.some((endpoint) => metaFor(card.id, endpoint).k_locked === true);
    const allowed = locked ? k === card.k : Number.isInteger(k) && lo <= k && k <= hi;
    // The number "Use recommended" sets: the eval's own k, or the fewest repetitions that make its prompts
    // enough for an outcome that decides the study.
    const recommendedK = recommendedRepetitions(config, cards, card.id) ?? card.k;
    // Fewer repetitions than that leave the eval's prompts short of enough for such an outcome. How short is
    // the outcome's own rating, the one the prompts beside this chip show and the problem list goes by: the
    // chip takes it over, so the two chips and the list say the same thing about the same setting.
    const rating = worst?.sample_size.rating;
    const short = allowed && !locked && k < recommendedK && recommendedK > card.k && (rating === "insufficient" || rating === "marginal") ? rating : null;
    const need = !allowed ? `Between ${lo} and ${hi}.`
      : short !== null ? `With k = ${recommendedK}, the ${plural(prompts, "prompt")} of this eval are enough for its margin.`
        : decides ? "Fewer repetitions call for more prompts; more repetitions call for fewer."
          : "Fewer repetitions make each prompt's value less exact.";
    recs.push({
      path: `cards.${card.id}.k`, label: "Repetitions (k)", value: `k = ${k}`, recommended: String(recommendedK),
      state: !allowed ? "invalid" : locked ? "locked" : k === recommendedK ? "recommended" : short ?? "custom",
      ...(locked
        ? { have: `k = ${card.k} is part of how this outcome is defined.`, need: "Not editable.", basis: "Same in every arm." }
        : { have: `Each prompt's value is the mean of ${k} repetitions.`, need, basis: "The unit of analysis is the prompt; the sample-size rating takes k into account." }),
      stepId, nodeId,
    });
  }
  return recs;
}

function seedRecs(config: WizardStudyConfig, cells: readonly Cell[]): Rec[] {
  const rec = (count: number, nodeId: string): Rec => ({
    path: "roster.seeds", label: "Control seeds", value: plural(count, "seed"), recommended: String(DEFAULT_SEEDS.length),
    state: count < 2 ? "invalid" : count === 2 ? "custom" : "recommended",
    have: `${plural(count, "seeded pseudo-random run")} per comparison.`,
    need: count < 2 ? "Each comparison needs at least 2." : `${plural((count * (count - 1)) / 2, "seed pair")} for the negative control.`,
    basis: "Their mean is the comparison side; pairs of them are the negative control.",
    stepId: "compare", nodeId,
  });
  if (cells.length === 0) return [rec(0, "study")];
  return cells.map((cell) => {
    const seeds = cellArms(config, cell).filter((arm) => compared(arm, "prng_seeded")).map((arm) => arm.study_seed ?? null);
    return rec(new Set(seeds).size, `deck:${laneKey(cell)}`);
  });
}

function designRecs(config: WizardStudyConfig, cards: readonly EvalCard[], registry: ReadonlyMap<string, EvalCard>, cells: readonly Cell[], hypotheses: readonly HypothesisPlan[], all: readonly Problem[]): Rec[] {
  const design = config.design;
  if (design === null || design === undefined) return [];
  const recommended = recommendDesign(config, cards);
  const failing = (...codes: string[]) => all.some((problem) => problem.severity === "error" && codes.includes(problem.code));
  // An error wins over everything: a value the study cannot run with is not "recommended", whatever it equals.
  const state = (codes: string[], matches: boolean): RecState => (failing(...codes) ? "invalid" : matches ? "recommended" : "custom");

  const alpha = design.alpha ?? 0.05;
  const epsilon = design.sensitivity_epsilon ?? null;
  const m = hypotheses.length;
  const recs: Rec[] = [
    {
      path: "design.claim", label: "What would count as a finding", value: CLAIM_TEXT[design.claim] ?? design.claim,
      recommended: recommended === null ? null : CLAIM_TEXT[recommended.claim] ?? recommended.claim,
      state: state(["design.equivalence_needs_control"], recommended !== null && recommended.claim === design.claim),
      have: design.claim === "difference" ? "A null result will be inconclusive." : "A null result within the margin can be reported as an absence.",
      need: "An absence needs a margin and a positive control that is detected.",
      basis: "Fixed at registration.",
      stepId: "design", nodeId: "study",
    },
    {
      path: "design.confirmatory", label: "Outcomes that decide the study", value: `m = ${m}`,
      recommended: recommended === null ? null : plural(recommended.confirmatory.length, "outcome"),
      state: state(["design.no_outcome", "design.not_eligible", "design.too_many_tests"], recommended !== null && sameRefs(recommended.confirmatory, design.confirmatory)),
      have: `${plural(design.confirmatory.length, "outcome")} in ${plural(cells.length, "comparison")}: ${plural(m, "test")}.`,
      need: `Each is tested at α/${Math.max(1, m)}.`,
      basis: "Every outcome you add makes all of them harder to pass.",
      stepId: "design", nodeId: "family",
    },
    {
      path: "design.alpha", label: "False-finding rate (α)", value: `α = ${formatG(alpha)}`, recommended: recommended === null ? null : formatG(recommended.alpha ?? 0.05),
      state: state(["design.too_many_tests"], recommended !== null && (recommended.alpha ?? 0.05) === alpha),
      have: "Across all confirmatory tests together.",
      need: "It can only be made stricter.",
      basis: `Holm over ${plural(m, "test")}; the confidence level is ${percent(1 - alpha)}.`,
      stepId: "design", nodeId: "family",
    },
  ];

  const control: Omit<Rec, "nodeId"> = {
    path: "design.sensitivity_epsilon", label: "Positive control that must be detected", value: epsilonText(epsilon),
    recommended: recommended === null ? null : epsilonText(recommended.sensitivity_epsilon),
    state: state(
      ["design.epsilon_missing", "design.epsilon_off", "design.epsilon_seed_shared", "design.equivalence_needs_control"],
      recommended !== null && (recommended.sensitivity_epsilon ?? null) === epsilon,
    ),
    have: epsilon === null ? "No positive control: a null result is inconclusive." : `The ε = ${epsilon.toFixed(2)} arm has to be detected.`,
    need: "'Equivalent' is only returned if this arm is detected.",
    basis: "A deliberately biased pseudo-random arm, on a seed of its own.",
    stepId: "design",
  };
  recs.push(...(cells.length === 0 ? [{ ...control, nodeId: "family" }] : cells.map((cell) => ({ ...control, nodeId: `gate:pos:${laneKey(cell)}` }))));

  for (const ref of design.confirmatory) {
    const card = registry.get(ref.card_id);
    if (card === undefined) continue;
    const sesoi = cardMargin(card, ref.endpoint);
    const declared = (design.margins ?? []).filter((margin) => refKey(margin) === refKey(ref)).at(-1);
    const bounds = metaFor(card.id, ref.endpoint).range;
    const cap = bounds === null ? null : MARGIN_CAP_FRACTION * (bounds[1] - bounds[0]);
    const refused = declared !== undefined && marginRefusal(card, ref.endpoint, declared.margin) !== null;
    const base = { path: `design.margins.${refKey(ref)}`, label: "Smallest difference that matters", recommended: null, stepId: "design" as const, nodeId: `endpoint:${refKey(ref)}` };
    const fixedNow = "Fixed now. Never derived from a pilot.";
    if (refused) {
      recs.push({ ...base, value: `±${formatG(declared.margin)}`, state: "invalid", have: "This outcome cannot take a declared margin.", need: "Remove the declaration.", basis: fixedNow });
    } else if (sesoi !== null) {
      recs.push({ ...base, value: `±${formatG(sesoi)}`, state: "locked", have: `±${formatG(sesoi)}, stated by the eval card.`, need: "Not editable.", basis: fixedNow });
    } else if (declared !== undefined) {
      recs.push({
        ...base, value: `±${formatG(declared.margin)}`, state: "custom", have: `±${formatG(declared.margin)}, declared for this study.`,
        need: cap === null || bounds === null ? "Stated in the outcome's own units."
          : `Between ±${formatG(MARGIN_FLOOR_FRACTION * (bounds[1] - bounds[0]))} and ±${formatG(cap)} (${percent(MARGIN_FLOOR_FRACTION)} to ${percent(MARGIN_CAP_FRACTION)} of its range).`,
        basis: fixedNow,
      });
    } else {
      recs.push({
        ...base, value: "none", state: "unknown", have: "No margin: 'equivalent' cannot be returned on this outcome.",
        need: cap === null ? "This outcome cannot take one." : `One can be declared, up to ±${formatG(cap)}.`, basis: fixedNow,
      });
    }
  }

  // What the harness fixes once the design is declared. Nothing here is a choice.
  const fixed = (path: string, label: string, value: string, nodeId: string, have: string, basis: string): Rec => ({
    path, label, value, recommended: null, state: "locked", have, need: "Not editable.", basis, stepId: "design", nodeId,
  });
  for (const hypothesis of hypotheses) {
    recs.push(fixed(`fixed.test.${hypothesis.card_id}/${hypothesis.endpoint}`, "Test", TEST_TEXT[hypothesis.test], `test:${hypothesis.id}`,
      hypothesis.sided === "one_sided_upper" ? "One-sided: only a larger divergence counts." : "Two-sided.", "Set by the outcome, not by the study."));
  }
  recs.push(
    fixed("fixed.correction", "Correction", `Holm · m = ${m}`, "family", "Holm across outcomes × comparisons.", "Members that produce no data keep their slot."),
    fixed("fixed.ci_level", "Confidence level", percent(1 - alpha), "family", "1 − α.", "Follows the false-finding rate."),
    fixed("fixed.power", "Power", percent(POWER), "family", "Sample sizes are rated at this power.", "Worst-case variance; true power may be lower."),
    fixed("fixed.permutations", "Permutations", String(planPermutations(familySizes(hypotheses), alpha)), "family", "Per test, with analysis seed 42.", "The largest of three numbers: 10 000, 20 × the difference tests ÷ α, and 2 × the seed-pair controls ÷ α."),
    fixed("fixed.completeness", "Completeness", `≥ ${percent(MIN_COMPLETENESS)}`, "gate:completeness", "Share of planned pairs that must be analysed.", "Below it the outcome is invalid, not inconclusive."),
  );
  return recs;
}

/** Whether an arm samples through the harness's amplifier: a provider samples its own way, and greedy decoding draws nothing. */
export function throughAmplifier(arm: Arm): boolean {
  return arm.source !== "provider" && arm.sampling !== "greedy";
}

/**
 * What the harness fixes about the arms themselves: one quantum arm per comparison, and one sampling envelope
 * for every arm that samples through its amplifier. An arm that does not (a provider's own sampling, greedy
 * decoding) gets no such line, and the biased arm's says what is bent in it on purpose.
 */
function armRecs(arms: readonly Arm[]): Rec[] {
  const locked = { recommended: null, state: "locked", need: "Not editable.", stepId: "compare" } as const;
  return [
    { ...locked, path: "roster.quantum", label: "Quantum arm", value: "1 per comparison", have: "One arm per comparison draws fresh quantum randomness for every token.", basis: "A comparison has exactly one.", nodeId: "study" },
    ...arms.filter(throughAmplifier).map((arm): Rec => ({
      ...locked, path: "fixed.envelope", label: "Sampling envelope", value: "pinned",
      have: arm.source === "prng_epsilon"
        ? "The same amplifier as the other arms, with its reference mean lowered by ε standard errors on purpose."
        : "The same in every arm that samples through the amplifier; such arms differ only in the entropy source and the seed.",
      basis: ENVELOPE, nodeId: `arm:${arm.id}`,
    })),
  ];
}

/**
 * Every setting of the study with its state, for both views.
 *
 * `known` is the study's problem list when the caller already has it; a design setting is `invalid` when an
 * error in it names that setting.
 */
export function rate(config: WizardStudyConfig, cards: readonly EvalCard[], known?: readonly Problem[]): Rec[] {
  const registry = new Map(cards.map((card) => [card.id, card]));
  const cells = sourceCells(config);
  const hypotheses = draftHypotheses(config, cards);
  const all = known ?? problems(config, cards);
  return [
    ...seedRecs(config, cells),
    ...cardRecs(config, cards, registry, hypotheses),
    ...designRecs(config, cards, registry, cells, hypotheses, all),
    ...armRecs(config.arms),
  ];
}

/** The first rec for a setting, or `undefined`. Settings that sit on several nodes (one per comparison) share a state. */
export function recFor(recs: readonly Rec[], path: string): Rec | undefined {
  return recs.find((rec) => rec.path === path);
}
