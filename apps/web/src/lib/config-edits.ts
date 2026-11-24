import type {
  Arm, CardOverrides, CardSelection, Conditioning, DisplaySettings, EndpointRef, EstimateAssumptions, EvalCard, JudgeSpec, ModelRef,
  PilotConfig, StudyDesign,
  SupervisedConfig,
} from "@qr-evals/contracts";
import { resolveConditioning } from "./conditioning";
import type { WizardStudyConfig } from "./config-store";
import { MAX_CONFIRMATORY, metaFor, recommendDesign, recommendK } from "./design";
import { DEFAULT_JUDGE } from "./judge";
import { applyRoster, DEFAULT_SEEDS, rosterFromArms, withSeedCount } from "./roster";

// Every change the setup pages make to a study, as a pure function from one study to the next.
//
// The wizard steps, the settings page and the map all call these, so one edit means the same thing wherever
// it is made. None of them validates: a draft may be wrong while it is being edited, and `problems()` says how.
// An edit that changes nothing returns the study it was given.

type Alpha = NonNullable<StudyDesign["alpha"]>;
type Epsilon = NonNullable<StudyDesign["sensitivity_epsilon"]>;
type Claim = StudyDesign["claim"];

const sameRef = (a: EndpointRef, b: EndpointRef) => a.card_id === b.card_id && a.endpoint === b.endpoint;

/** The study with its design block changed, or untouched when it has none: a legacy study has nothing to set. */
function withDesign(config: WizardStudyConfig, change: (design: StudyDesign) => StudyDesign): WizardStudyConfig {
  const design = config.design;
  if (design === null || design === undefined) return config;
  return { ...config, design: change(design) };
}

// ── study ────────────────────────────────────────────────────────────────────

export function setTitle(config: WizardStudyConfig, title: string): WizardStudyConfig {
  return { ...config, title };
}

export function setAuthors(config: WizardStudyConfig, authors: string[]): WizardStudyConfig {
  return { ...config, authors };
}

export function setContact(config: WizardStudyConfig, contact: string): WizardStudyConfig {
  return { ...config, contact };
}

export function setExpectations(config: WizardStudyConfig, expectations: string): WizardStudyConfig {
  return { ...config, expectations };
}

// ── evals ────────────────────────────────────────────────────────────────────

/** Selects an eval, or deselects it. The design block is left alone: an outcome that lost its eval is a problem with a fix, not a silent rewrite. */
export function toggleCard(config: WizardStudyConfig, cardId: string): WizardStudyConfig {
  const selected = config.cards.some((selection) => selection.card_id === cardId);
  return { ...config, cards: selected ? config.cards.filter((selection) => selection.card_id !== cardId) : [...config.cards, { card_id: cardId }] };
}

function setOverride(config: WizardStudyConfig, cardId: string, key: "k" | "items", value: number | null): WizardStudyConfig {
  const current = config.cards.find((selection) => selection.card_id === cardId);
  // `null` is the card's own value, which a selection without the override already has.
  if (current === undefined || (current.overrides?.[key] ?? null) === value) return config;
  return {
    ...config,
    cards: config.cards.map((selection) => (selection === current ? { ...selection, overrides: { ...selection.overrides, [key]: value } } : selection)),
  };
}

/** Repetitions per prompt for one eval; `null` is the card's own k. */
export function setCardK(config: WizardStudyConfig, cardId: string, k: number | null): WizardStudyConfig {
  return setOverride(config, cardId, "k", k);
}

/** Prompts for one eval, taken from the start of its file; `null` is all of them. */
export function setCardItems(config: WizardStudyConfig, cardId: string, items: number | null): WizardStudyConfig {
  return setOverride(config, cardId, "items", items);
}

/**
 * The token limit of one eval's replies; `null` is the card's own.
 *
 * The card's own limit is stored as no key at all, never as `null`, and a selection left with no override
 * loses its `overrides` block: the server writes a study that sets no limit without the key, so a study whose
 * limit was set and taken back is the study it was before.
 */
export function setCardMaxTokens(config: WizardStudyConfig, cardId: string, maxTokens: number | null): WizardStudyConfig {
  const current = config.cards.find((selection) => selection.card_id === cardId);
  if (current === undefined || (current.overrides?.max_tokens ?? null) === maxTokens) return config;
  const without = <T extends object>(map: T, key: string) => Object.fromEntries(Object.entries(map).filter(([name]) => name !== key));
  const others: CardOverrides = without(current.overrides ?? {}, "max_tokens");
  const next: CardSelection = maxTokens !== null ? { ...current, overrides: { ...others, max_tokens: maxTokens } }
    : Object.keys(others).length > 0 ? { ...current, overrides: others }
      : without(current, "overrides") as unknown as CardSelection;
  return { ...config, cards: config.cards.map((selection) => (selection === current ? next : selection)) };
}

// ── design ───────────────────────────────────────────────────────────────────

export function setAlpha(config: WizardStudyConfig, alpha: Alpha): WizardStudyConfig {
  return withDesign(config, (design) => ({ ...design, alpha }));
}

export function setClaim(config: WizardStudyConfig, claim: Claim): WizardStudyConfig {
  return withDesign(config, (design) => ({ ...design, claim }));
}

export function setSensitivityEpsilon(config: WizardStudyConfig, epsilon: Epsilon | null): WizardStudyConfig {
  return withDesign(config, (design) => ({ ...design, sensitivity_epsilon: epsilon }));
}

/** Adds an outcome to those that decide the study, or removes it together with its declared margin. */
export function toggleConfirmatory(config: WizardStudyConfig, ref: EndpointRef): WizardStudyConfig {
  return withDesign(config, (design) => {
    if (design.confirmatory.some((existing) => sameRef(existing, ref))) {
      return {
        ...design,
        confirmatory: design.confirmatory.filter((existing) => !sameRef(existing, ref)),
        margins: (design.margins ?? []).filter((margin) => !sameRef(margin, ref)),
      };
    }
    if (design.confirmatory.length >= MAX_CONFIRMATORY) return design;
    return { ...design, confirmatory: [...design.confirmatory, { card_id: ref.card_id, endpoint: ref.endpoint }] };
  });
}

/** Declares the smallest difference that matters on an outcome; `null` withdraws the declaration. */
export function setMargin(config: WizardStudyConfig, ref: EndpointRef, margin: number | null): WizardStudyConfig {
  return withDesign(config, (design) => {
    const others = (design.margins ?? []).filter((existing) => !sameRef(existing, ref));
    return { ...design, margins: margin === null ? others : [...others, { card_id: ref.card_id, endpoint: ref.endpoint, margin }] };
  });
}

/**
 * Gives a study without a design block the recommended one. Nothing changes if it has one, or if no selected
 * outcome can decide a study.
 *
 * The design block is all that is added. Prompts and repetitions stay as they are, so that `removeDesign`
 * gives back the study that was there before, byte for byte: a study on the built-in plan is pinned by its
 * hash, and declaring a design to look at it must not cost that. Where an eval's repetitions leave its
 * prompts too few for an outcome that now decides the study, the sample-size chips say so and "Use
 * recommended" on the repetitions sets the number that is enough (`recommendedRepetitions`).
 */
export function declareDesign(config: WizardStudyConfig, cards: readonly EvalCard[]): WizardStudyConfig {
  if (config.design !== null && config.design !== undefined) return config;
  const design = recommendDesign(config, cards);
  return design === null ? config : { ...config, design };
}

/**
 * Takes the design block out of a study, which returns it to the built-in Study 1 plan. The claim, the
 * outcomes, the false-finding rate, the positive control and the margins go with it; nothing else changes.
 */
export function removeDesign(config: WizardStudyConfig): WizardStudyConfig {
  if (config.design === null || config.design === undefined) return config;
  return { ...config, design: null };
}

/**
 * The repetitions (k) the page recommends for one eval of this study, or `null` for an eval that takes none.
 *
 * It is the eval's own k, or, in a study with a design block, the fewest repetitions that make the eval's
 * prompts enough for an outcome that decides the study where its own k does not (`recommendK`). A k that is
 * part of how an outcome is defined is never moved. The chip beside the setting prints this number and "Use
 * recommended" sets it, so the two cannot disagree.
 */
export function recommendedRepetitions(config: WizardStudyConfig, cards: readonly EvalCard[], cardId: string): number | null {
  const card = cards.find((candidate) => candidate.id === cardId);
  if (card === undefined || card.k === null || card.k === undefined) return null;
  const [lo, hi] = card.k_bounds ?? [card.k, card.k];
  const locked = lo === hi || card.endpoints.primary.some((endpoint) => metaFor(card.id, endpoint).k_locked === true);
  // A study on the built-in plan has no outcome that decides it, so nothing asks for more than the eval's own k.
  if (locked || config.design === null || config.design === undefined) return card.k;
  return Math.max(card.k, recommendK(config, cards)[cardId] ?? 0);
}

/**
 * The edit that sets one setting to its recommended value, for a "Use recommended" button.
 *
 * `path` is a `Rec.path`: `design` (the whole block), `design.alpha`, `design.claim`,
 * `design.sensitivity_epsilon`, `design.confirmatory`, `design.margins`, `design.margins.{card}/{endpoint}`,
 * `cards.{id}.k` (the eval's own k, or the fewest repetitions that make its prompts enough for an outcome that
 * decides the study), `cards.{id}.items`, `roster.seeds`. The result is always a design the server would not
 * refuse for that setting: the claim and the positive control move together where one requires the other, and
 * margins of outcomes that are no longer confirmatory go with them. Any other path changes nothing.
 */
export function useRecommended(path: string): (config: WizardStudyConfig, cards: readonly EvalCard[]) => WizardStudyConfig {
  return (config, cards) => {
    const card = /^cards\.([^.]+)\.(k|items)$/.exec(path);
    // All of an eval's prompts is its recommended count, stored as no override.
    if (card && card[2] === "items") return setOverride(config, card[1]!, "items", null);
    if (card) {
      // The eval's own k is stored as no override, like a k typed into the field.
      const k = recommendedRepetitions(config, cards, card[1]!);
      const own = cards.find((candidate) => candidate.id === card[1])?.k ?? null;
      return setOverride(config, card[1]!, "k", k === own ? null : k);
    }
    if (path === "roster.seeds") {
      const roster = rosterFromArms(config.arms, config.seeds ?? DEFAULT_SEEDS);
      return roster === null ? config : applyRoster(config, withSeedCount(roster, Math.max(DEFAULT_SEEDS.length, roster.seeds.length)));
    }
    if (path !== "design" && !path.startsWith("design.")) return config;

    const margin = /^design\.margins\.([^/]+)\/(.+)$/.exec(path);
    if (margin) return setMargin(config, { card_id: margin[1]!, endpoint: margin[2]! }, null);
    if (path === "design.margins") return withDesign(config, (design) => ({ ...design, margins: [] }));

    const recommended = recommendDesign(config, cards);
    if (recommended === null) return config;
    if (path === "design") return { ...config, design: recommended };
    return withDesign(config, (design) => {
      switch (path) {
        case "design.alpha":
          return { ...design, alpha: recommended.alpha ?? 0.05 };
        case "design.claim":
          // The absence of an effect can only be claimed with a positive control, so the two arrive together.
          return recommended.claim === "difference_or_equivalence"
            ? { ...design, claim: recommended.claim, sensitivity_epsilon: design.sensitivity_epsilon ?? recommended.sensitivity_epsilon ?? null }
            : { ...design, claim: recommended.claim };
        case "design.sensitivity_epsilon": {
          const epsilon = recommended.sensitivity_epsilon ?? null;
          return { ...design, sensitivity_epsilon: epsilon, claim: epsilon === null ? "difference" : design.claim };
        }
        case "design.confirmatory":
          return {
            ...design,
            confirmatory: recommended.confirmatory,
            margins: (design.margins ?? []).filter((declared) => recommended.confirmatory.some((ref) => sameRef(ref, declared))),
          };
        default:
          return design;
      }
    });
  };
}

// ── instruments ──────────────────────────────────────────────────────────────

export function setJudge(config: WizardStudyConfig, patch: Partial<JudgeSpec>): WizardStudyConfig {
  return { ...config, judge: { ...DEFAULT_JUDGE, ...config.judge, ...patch } };
}

export function setNarrativeModel(config: WizardStudyConfig, model: ModelRef): WizardStudyConfig {
  return { ...config, narrative_model: model };
}

/** Changes the session settings. A study without supervised sessions has none to change. */
export function setSupervised(config: WizardStudyConfig, patch: Partial<SupervisedConfig>): WizardStudyConfig {
  const supervised = config.supervised;
  if (supervised === null || supervised === undefined) return config;
  return { ...config, supervised: { ...supervised, ...patch } };
}

export function setDisplaySettings(config: WizardStudyConfig, mode: "single" | "tiled", settings: DisplaySettings): WizardStudyConfig {
  return setSupervised(config, { settings_by_display: { ...config.supervised?.settings_by_display, [mode]: settings } });
}

// ── advanced ─────────────────────────────────────────────────────────────────

/** Changes the entropy conditioning. A study without the block gets one: what it ran under (raw), with the change. */
export function setConditioning(config: WizardStudyConfig, patch: Partial<Conditioning>): WizardStudyConfig {
  return { ...config, conditioning: { ...resolveConditioning(config.conditioning), ...patch } };
}

export function setEstimates(config: WizardStudyConfig, patch: Partial<EstimateAssumptions>): WizardStudyConfig {
  return { ...config, estimates: { ...config.estimates, ...patch } };
}

export function setPilot(config: WizardStudyConfig, patch: Partial<PilotConfig>): WizardStudyConfig {
  return { ...config, pilot: { ...config.pilot, ...patch } };
}

/** Sets the date the pre-registration prints as the configuration date, to the second and in UTC. */
export function setCreatedAt(config: WizardStudyConfig, when: Date): WizardStudyConfig {
  return { ...config, created_at: `${when.toISOString().slice(0, 19)}+00:00` };
}

export function setSeeds(config: WizardStudyConfig, seeds: number[]): WizardStudyConfig {
  return { ...config, seeds };
}

export function setModels(config: WizardStudyConfig, models: ModelRef[]): WizardStudyConfig {
  return { ...config, models };
}

/**
 * Changes one field of one model. A change of its id is followed by everything that names the model by it:
 * the arms that run on it, the narrative model, and its assumed throughput. Otherwise every arm would be
 * left on a model the study no longer lists.
 */
export function setModelField(config: WizardStudyConfig, index: number, patch: Partial<ModelRef>): WizardStudyConfig {
  const before = config.models[index];
  if (before === undefined) return config;
  const after = { ...before, ...patch };
  const next: WizardStudyConfig = { ...config, models: config.models.map((model, at) => (at === index ? after : model)) };
  // Only a rename moves the references, and only when no other model already has the old or the new id.
  const renamed = after.id !== before.id && !config.models.some((model, at) => at !== index && (model.id === before.id || model.id === after.id));
  if (!renamed) return next;
  next.arms = config.arms.map((arm) => (arm.model === before.id ? { ...arm, model: after.id } : arm));
  if (config.narrative_model.id === before.id) next.narrative_model = after;
  const assumed = config.estimates?.tps_by_model;
  if (assumed && Object.hasOwn(assumed, before.id)) {
    const { [before.id]: tps, ...rest } = assumed;
    next.estimates = { ...config.estimates, tps_by_model: tps === undefined ? rest : { ...rest, [after.id]: tps } };
  }
  return next;
}

/** Replaces the arm list as written, for the arm table. Nothing else follows the arms here; that is what the roster is for. */
export function setArms(config: WizardStudyConfig, arms: Arm[]): WizardStudyConfig {
  return { ...config, arms };
}
