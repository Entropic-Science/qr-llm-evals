import type { Arm, ModelRef, SupervisedConfig } from "@qr-evals/contracts";
import type { WizardStudyConfig } from "./config-store";
import { DEFAULT_DISPLAY_SETTINGS } from "./supervised-settings";

// A study's arms, seen as roles.
//
// The setup pages do not edit arms one by one. They edit a roster: which (model, sampling preset) pairs are
// compared, how many seeded control runs each gets, which biased positive controls are planned, and whether an
// observed quantum arm is added. `applyRoster` writes the arms a roster stands for; `rosterFromArms` reads a
// roster back from arms, and returns `null` for an arm set that is not one roster written out, so a study
// written by hand is never reshaped by a guess. Such a study is edited in the arm table instead.

export type RosterSampling = "creative" | "t1_minp";
export interface RosterCell { model: string; sampling: RosterSampling }
export interface RosterEpsilon { level: number; available: boolean }
export interface Roster {
  /** The comparisons. Each is its own; pairs are never pooled. */
  cells: RosterCell[];
  /** Seeds of the seeded pseudo-random control runs, the same in every comparison. */
  seeds: number[];
  /** Planned positive controls. An unavailable one is planned and switched off. */
  epsilons: RosterEpsilon[];
  /** Whether every comparison also gets an observed quantum arm. */
  supervised: boolean;
  deterministic: boolean;
}

export const ROSTER_SAMPLINGS: readonly RosterSampling[] = ["creative", "t1_minp"];
/** The registered grid of the biased-sampler positive control (`EPSILON_LEVELS` in `models/schema.py`). */
export const EPSILON_LEVELS: readonly number[] = [0.01, 0.03, 0.10, 0.50];
export const DEFAULT_SEEDS: readonly number[] = [1, 7, 42];
export const MIN_CONTROL_SEEDS = 2;
export const MAX_CONTROL_SEEDS = 5;
/** Biased arms run on the smallest seed from here up that no control run uses. */
export const EPSILON_SEED_FLOOR = 1001;
/** An arm id is at most this long (`ArmId` in `models/schema.py`). */
const MAX_ID_LENGTH = 64;
/** The server refuses an unavailable arm without a reason. */
const SWITCHED_OFF_REASON = "Planned positive control, switched off in the study setup.";

const observerMode = (arm: Arm) => arm.observer_mode ?? "unsupervised";
const isAvailable = (arm: Arm) => (arm.availability ?? "available") === "available";
const deterministic = (arm: Arm) => arm.deterministic ?? false;
const seedOf = (arm: Arm) => arm.study_seed ?? null;
const onGrid = (level: number | null | undefined): level is number =>
  level !== null && level !== undefined && EPSILON_LEVELS.some((known) => Math.abs(known - level) < 1e-12);
const sameLevel = (a: number | null | undefined, b: number) => a !== null && a !== undefined && Math.abs(a - b) < 1e-12;
const isRosterSampling = (sampling: string): sampling is RosterSampling => (ROSTER_SAMPLINGS as readonly string[]).includes(sampling);

function unique<T>(values: readonly T[]): T[] {
  return [...new Set(values)];
}

/** The seed the biased arms run on: never a control seed, so a positive control has a random stream of its own. */
export function epsilonSeedFor(controlSeeds: readonly number[]): number {
  let seed = EPSILON_SEED_FLOOR;
  while (controlSeeds.includes(seed)) seed += 1;
  return seed;
}

interface CellRoles { seeds: number[]; epsilons: RosterEpsilon[]; epsilonSeed: number | null; supervised: boolean }

/** One comparison's arms as roles, or `null` if they are not the roles of a roster. */
function rolesOf(arms: readonly Arm[]): CellRoles | null {
  const roles: CellRoles = { seeds: [], epsilons: [], epsilonSeed: null, supervised: false };
  let quantum = 0;
  for (const arm of arms) {
    const seed = seedOf(arm);
    if (arm.source === "qrng") {
      // A quantum arm that is switched off has no role: a roster always runs it.
      if (!isAvailable(arm) || seed !== null) return null;
      if (observerMode(arm) === "supervised") {
        if (roles.supervised) return null;
        roles.supervised = true;
      } else {
        quantum += 1;
      }
    } else if (arm.source === "prng_seeded") {
      if (!isAvailable(arm) || observerMode(arm) !== "unsupervised" || seed === null || roles.seeds.includes(seed)) return null;
      roles.seeds.push(seed);
    } else if (arm.source === "prng_epsilon") {
      const level = arm.epsilon;
      if (observerMode(arm) !== "unsupervised" || seed === null || !onGrid(level)) return null;
      if (roles.epsilons.some((planned) => sameLevel(planned.level, level))) return null;
      // Every biased arm of a roster runs on one seed.
      if (roles.epsilonSeed !== null && roles.epsilonSeed !== seed) return null;
      roles.epsilonSeed = seed;
      roles.epsilons.push({ level, available: isAvailable(arm) });
    } else {
      return null;
    }
  }
  return quantum === 1 ? roles : null;
}

function cellsOf(arms: readonly Arm[]): Map<string, { cell: RosterCell; arms: Arm[] }> | null {
  const cells = new Map<string, { cell: RosterCell; arms: Arm[] }>();
  for (const arm of arms) {
    if (!isRosterSampling(arm.sampling)) return null;
    const key = JSON.stringify([arm.model, arm.sampling]);
    const entry = cells.get(key) ?? { cell: { model: arm.model, sampling: arm.sampling }, arms: [] };
    entry.arms.push(arm);
    cells.set(key, entry);
  }
  return cells;
}

/** The roles a study without arms falls back to: its seed list, and what the page remembers of its last comparisons. */
export type RosterMemory = Pick<Roster, "seeds"> & Partial<Pick<Roster, "epsilons" | "supervised" | "deterministic">>;

/**
 * The roster these arms are, or `null` if they are not one.
 *
 * Arms are a roster when every comparison has the same roles: one quantum arm, the same control seeds, the
 * same positive controls on one seed of their own, an observed quantum arm in all comparisons or in none, and
 * one determinism setting throughout. Arm ids and the reasons of switched-off arms are free. `memory` is what a
 * study without arms falls back to, so that unticking the last comparison forgets neither its control seeds nor,
 * where the page still knows them, its positive controls, its observed arm and its determinism. A seed list
 * alone is the memory of a study that was opened without arms: its other roles are the defaults.
 */
export function rosterFromArms(arms: readonly Arm[], memory: readonly number[] | RosterMemory = DEFAULT_SEEDS): Roster | null {
  if (arms.length === 0) {
    const kept: RosterMemory = Array.isArray(memory) ? { seeds: [...(memory as readonly number[])] } : memory as RosterMemory;
    return {
      cells: [],
      seeds: [...kept.seeds],
      epsilons: (kept.epsilons ?? []).map((epsilon) => ({ ...epsilon })),
      supervised: kept.supervised ?? false,
      deterministic: kept.deterministic ?? true,
    };
  }
  const cells = cellsOf(arms);
  if (cells === null) return null;
  const first = deterministic(arms[0]!);
  if (arms.some((arm) => deterministic(arm) !== first)) return null;

  let shared: CellRoles | null = null;
  for (const entry of cells.values()) {
    const roles = rolesOf(entry.arms);
    if (roles === null) return null;
    if (shared !== null && JSON.stringify(shared) !== JSON.stringify(roles)) return null;
    shared = roles;
  }
  if (shared === null) return null;
  return {
    cells: [...cells.values()].map((entry) => entry.cell),
    seeds: shared.seeds,
    epsilons: shared.epsilons,
    supervised: shared.supervised,
    deterministic: first,
  };
}

/**
 * The roster closest to arms that are not one, for "Rebuild from roles".
 *
 * This is the guess `rosterFromArms` refuses to make, so it only runs when the researcher asks for it: the
 * comparisons that have a quantum arm, every control seed and every biased level found anywhere, an observed
 * arm if any comparison has one.
 */
export function nearestRoster(arms: readonly Arm[], seeds: readonly number[] | RosterMemory = DEFAULT_SEEDS): Roster {
  const exact = rosterFromArms(arms, seeds);
  if (exact !== null) return exact;
  const cellOf = (arm: Arm): RosterCell[] => (isRosterSampling(arm.sampling) ? [{ model: arm.model, sampling: arm.sampling }] : []);
  const distinct = (cells: RosterCell[]) => unique(cells.map((cell) => JSON.stringify(cell))).map((text) => JSON.parse(text) as RosterCell);
  const quantum = distinct(arms.filter((arm) => arm.source === "qrng").flatMap(cellOf));
  const found = unique(arms.filter((arm) => arm.source === "prng_seeded").flatMap((arm) => seedOf(arm) ?? [])).slice(0, MAX_CONTROL_SEEDS);
  const levels = EPSILON_LEVELS.filter((level) => arms.some((arm) => arm.source === "prng_epsilon" && sameLevel(arm.epsilon, level)));
  return withSeedCount({
    cells: quantum.length > 0 ? quantum : distinct(arms.flatMap(cellOf)),
    seeds: found,
    epsilons: levels.map((level) => ({
      level,
      available: arms.some((arm) => arm.source === "prng_epsilon" && sameLevel(arm.epsilon, level) && isAvailable(arm)),
    })),
    supervised: arms.some((arm) => observerMode(arm) === "supervised"),
    deterministic: arms.every(deterministic),
  }, Math.max(MIN_CONTROL_SEEDS, found.length));
}

// ── roster edits ─────────────────────────────────────────────────────────────

/** The roster with `count` control seeds: the last are dropped, or the Study 1 seeds and then the next free integers are added. */
export function withSeedCount(roster: Roster, count: number): Roster {
  const seeds = unique(roster.seeds).slice(0, Math.max(0, count));
  const add = (candidate: number) => { if (seeds.length < count && !seeds.includes(candidate)) seeds.push(candidate); };
  DEFAULT_SEEDS.forEach(add);
  for (let next = 2; seeds.length < count; next += 1) add(next);
  return { ...roster, seeds };
}

/** The roster with one comparison ticked or unticked. */
export function withCell(roster: Roster, cell: RosterCell, ticked: boolean): Roster {
  const others = roster.cells.filter((existing) => existing.model !== cell.model || existing.sampling !== cell.sampling);
  return { ...roster, cells: ticked ? [...others, cell] : others };
}

export type EpsilonState = "absent" | "off" | "on";

export function epsilonState(roster: Roster, level: number): EpsilonState {
  const planned = roster.epsilons.find((epsilon) => sameLevel(epsilon.level, level));
  return planned === undefined ? "absent" : planned.available ? "on" : "off";
}

/** The roster with one positive control absent, planned and switched off, or on. Levels stay in grid order. */
export function withEpsilon(roster: Roster, level: number, state: EpsilonState): Roster {
  const others = roster.epsilons.filter((epsilon) => !sameLevel(epsilon.level, level));
  const next = state === "absent" ? others : [...others, { level, available: state === "on" }];
  return { ...roster, epsilons: next.sort((a, b) => a.level - b.level) };
}

// ── arms from a roster ───────────────────────────────────────────────────────

interface Wanted {
  source: Arm["source"];
  seed: number | null;
  epsilon: number | null;
  observer: NonNullable<Arm["observer_mode"]>;
  available: boolean;
  id: string;
}

const slug = (text: string) => text.toLowerCase().replace(/[^a-z0-9_]/g, "_");
const epsilonId = (level: number) => `prng_epsilon_${String(Math.round(level * 100)).padStart(3, "0")}`;

function wantedArms(roster: Roster, epsilonSeed: number): Wanted[] {
  const base = { seed: null, epsilon: null, observer: "unsupervised", available: true } as const;
  return [
    ...(roster.supervised ? [{ ...base, source: "qrng", observer: "supervised", id: "qrng_supervised" } as const] : []),
    { ...base, source: "qrng", id: "qrng_unsupervised" },
    ...roster.seeds.map((seed): Wanted => ({ ...base, source: "prng_seeded", seed, id: `prng_seed_${seed}` })),
    ...roster.epsilons.map(({ level, available }): Wanted => ({ ...base, source: "prng_epsilon", seed: epsilonSeed, epsilon: level, available, id: epsilonId(level) })),
  ];
}

function normalised(roster: Roster): Roster {
  const cells = unique(roster.cells.map((cell) => JSON.stringify([cell.model, cell.sampling])))
    .map((text) => JSON.parse(text) as [string, RosterSampling])
    .map(([model, sampling]) => ({ model, sampling }));
  const epsilons = roster.epsilons.filter((epsilon, index) => onGrid(epsilon.level) && roster.epsilons.findIndex((other) => sameLevel(other.level, epsilon.level)) === index);
  return { ...roster, cells, seeds: unique(roster.seeds), epsilons: epsilons.map(({ level, available }) => ({ level, available })) };
}

const sameRoster = (a: Roster, b: Roster) => JSON.stringify(normalised(a)) === JSON.stringify(normalised(b));

function defaultSupervised(): SupervisedConfig {
  return {
    display: "single",
    settings_by_display: { single: { ...DEFAULT_DISPLAY_SETTINGS.single }, tiled: { ...DEFAULT_DISPLAY_SETTINGS.tiled } },
    intention_timer_min: 3,
    refocus_s: 30,
    group: false,
    target_pool_hash: null,
    questionnaires_hash: null,
  };
}

/**
 * The study with its arms rewritten as the roster's: comparisons × roles.
 *
 * A roster the study's arms already are changes nothing, and the same object comes back: a study that was only
 * looked at keeps its bytes, biased arms on a control seed included. Otherwise every arm is regenerated. An
 * existing arm in the same role (model, sampling, source, seed, ε, observer mode) is reused as it stands, with
 * its id and its reason; a biased arm on another seed is moved to the roster's seed for biased arms, which is
 * never a control seed. One comparison uses the plain ids (`qrng_unsupervised`, `prng_seed_7`,
 * `prng_epsilon_010`); with more, a new arm's id ends in `__{model}__{sampling}`.
 *
 * The models, the seed list, the supervised block and the narrative model follow the arms: a model no
 * comparison uses is dropped, the supervised block is `null` without an observed arm, and a narrative model
 * that was dropped is replaced by the first model left. A comparison naming a model found neither in the study
 * nor in `modelOptions` is left out, since no arm can be written for it.
 */
export function applyRoster(config: WizardStudyConfig, roster: Roster, modelOptions: readonly ModelRef[] = []): WizardStudyConfig {
  const current = rosterFromArms(config.arms, config.seeds ?? DEFAULT_SEEDS);
  if (current !== null && sameRoster(current, roster)) return config;

  const known = new Map<string, ModelRef>();
  for (const model of [...modelOptions, ...config.models]) known.set(model.id, model);
  const wanted = normalised(roster);
  // A comparison the study already runs stays, also when its model is missing from the model list: that is a
  // problem the study lists, and no reason to delete its arms on the next edit of another role.
  const cells = wanted.cells.filter((cell) => known.has(cell.model) || config.arms.some((arm) => arm.model === cell.model));
  const epsilonSeed = epsilonSeedFor(wanted.seeds);

  const taken = new Set<Arm>();
  const take = (match: (arm: Arm) => boolean): Arm | undefined => {
    const found = config.arms.find((arm) => !taken.has(arm) && match(arm));
    if (found !== undefined) taken.add(found);
    return found;
  };

  // First the arms that stay, so that a new arm never takes the id of one that is kept.
  const slots = cells.flatMap((cell) => wantedArms(wanted, epsilonSeed).map((role) => {
    const inRole = (arm: Arm) => arm.model === cell.model && arm.sampling === cell.sampling && arm.source === role.source
      && observerMode(arm) === role.observer && (role.epsilon === null ? (arm.epsilon ?? null) === null : sameLevel(arm.epsilon, role.epsilon));
    const existing = take((arm) => inRole(arm) && seedOf(arm) === role.seed) ?? (role.source === "prng_epsilon" ? take(inRole) : undefined);
    return { cell, role, existing };
  }));
  const ids = new Set(slots.flatMap(({ existing }) => existing?.id ?? []));
  const suffixed = cells.length > 1;
  const newId = (cell: RosterCell, base: string): string => {
    const full = (suffixed ? `${base}__${slug(cell.model)}__${slug(cell.sampling)}` : base).slice(0, MAX_ID_LENGTH);
    let id = full;
    for (let n = 2; ids.has(id); n += 1) id = `${full.slice(0, MAX_ID_LENGTH - `_${n}`.length)}_${n}`;
    ids.add(id);
    return id;
  };

  const arms = slots.map(({ cell, role, existing }): Arm => {
    const availability = role.available ? "available" : "unavailable";
    if (existing === undefined) {
      return {
        id: newId(cell, role.id),
        model: cell.model,
        sampling: cell.sampling,
        source: role.source,
        study_seed: role.seed,
        epsilon: role.epsilon,
        deterministic: wanted.deterministic,
        observer_mode: role.observer,
        availability,
        unavailable_reason: role.available ? null : SWITCHED_OFF_REASON,
      };
    }
    const unchanged = deterministic(existing) === wanted.deterministic && seedOf(existing) === role.seed
      && isAvailable(existing) === role.available && (role.available || Boolean(existing.unavailable_reason));
    if (unchanged) return existing;
    return {
      ...existing,
      study_seed: role.seed,
      deterministic: wanted.deterministic,
      availability,
      unavailable_reason: role.available ? null : existing.unavailable_reason || SWITCHED_OFF_REASON,
    };
  });

  const used = unique(cells.map((cell) => cell.model));
  const kept = config.models.filter((model) => used.includes(model.id));
  const models = [...kept, ...used.filter((id) => !kept.some((model) => model.id === id)).flatMap((id) => known.get(id) ?? [])];
  const seeds = unique([...wanted.seeds, ...arms.flatMap((arm) => seedOf(arm) ?? [])]);
  const next: WizardStudyConfig = {
    ...config,
    arms,
    // A study needs a model and a seed list even while it has no comparison.
    models: models.length > 0 ? models : config.models,
    seeds: seeds.length > 0 ? seeds : config.seeds ?? [...DEFAULT_SEEDS],
    supervised: wanted.supervised ? config.supervised ?? defaultSupervised() : null,
  };
  if (models.length > 0 && !models.some((model) => model.id === config.narrative_model.id)) next.narrative_model = models[0]!;
  return next;
}
