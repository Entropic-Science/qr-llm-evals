import type { Arm, ModelRef } from "@qr-evals/contracts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "./config-store";
import {
  DEFAULT_SEEDS, EPSILON_SEED_FLOOR, applyRoster, epsilonSeedFor, epsilonState, nearestRoster, rosterFromArms, withCell,
  withEpsilon, withSeedCount, type Roster,
} from "./roster";

const studies = resolve(process.cwd(), "../../studies");

/**
 * The `arms:` and `seeds:` of a study YAML, read from the file itself so the test follows the study.
 *
 * The web app has no YAML parser (the server reads study files). Both studies write their arms as a block
 * list of flat maps, which is all this reads; anything else in that block fails the test rather than being
 * skipped.
 */
function armsOf(path: string): { arms: Arm[]; seeds: number[] } {
  const lines = readFileSync(resolve(studies, path), "utf8").split(/\r?\n/);
  const start = lines.indexOf("arms:");
  expect(start, `${path} has an arms block`).toBeGreaterThanOrEqual(0);
  const scalar = (text: string): string | number | boolean | null => {
    if (text === "null") return null;
    if (text === "true" || text === "false") return text === "true";
    return /^-?\d+(\.\d+)?$/.test(text) ? Number(text) : text;
  };
  const arms: Record<string, unknown>[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^\S/.test(line)) break;
    if (line.trim() === "" || line.trim().startsWith("#")) continue;
    const entry = /^ {2}(- | {2})([a-z_]+): (.+)$/.exec(line);
    if (!entry) throw new Error(`${path}: cannot read the arm line "${line}"`);
    if (entry[1] === "- ") arms.push({});
    arms.at(-1)![entry[2]!] = scalar(entry[3]!);
  }
  const seeds = /^seeds: \[([\d, ]+)\]$/m.exec(lines.join("\n"));
  if (!seeds) throw new Error(`${path} has no inline seeds list`);
  return { arms: arms as unknown as Arm[], seeds: seeds[1]!.split(",").map((seed) => Number(seed.trim())) };
}

/** A study with the YAML's arms as the file writes them, and as the server hands them back (every key, defaults filled). */
function studiesWith(path: string): { written: WizardStudyConfig; served: WizardStudyConfig } {
  const { arms, seeds } = armsOf(path);
  const supervised = arms.some((arm) => arm.observer_mode === "supervised");
  const base = studyOneTemplate();
  const written: WizardStudyConfig = { ...base, design: null, arms, seeds, supervised: supervised ? base.supervised ?? null : null };
  const served = parseStudyConfig(written);
  if (!served) throw new Error(`${path}: the arms do not make a study`);
  return { written, served };
}

const QR: ModelRef = { id: "qr-llm", family: "qr_llm", provider: "qr_server", served_name: "qr-llm", checkpoint: null };
const QR2: ModelRef = { id: "qr-llm:v2", family: "qr_llm", provider: "qr_server", served_name: "qr-llm-v2", checkpoint: null };

function rosterOf(config: WizardStudyConfig): Roster {
  const roster = rosterFromArms(config.arms, config.seeds);
  if (roster === null) throw new Error("the arms are not a roster");
  return roster;
}

describe("rosterFromArms then applyRoster", () => {
  it.each(["study1.yaml", "thesis_prompt_or_physics/study.yaml"])("round-trips the arms of studies/%s byte for byte", (path) => {
    for (const config of Object.values(studiesWith(path))) {
      const before = JSON.stringify(config);
      const result = applyRoster(config, rosterOf(config));
      expect(JSON.stringify(result)).toBe(before);
      expect(result).toBe(config);
    }
  });

  it("reads Study 1 as one comparison with three seeds, three planned controls and an observed arm", () => {
    expect(rosterOf(studiesWith("study1.yaml").served)).toEqual({
      cells: [{ model: "qr-llm", sampling: "creative" }],
      seeds: [1, 7, 42],
      epsilons: [{ level: 0.01, available: false }, { level: 0.03, available: false }, { level: 0.1, available: false }],
      supervised: true,
      deterministic: true,
    });
  });

  it("reads the thesis study as one comparison with two running controls and no observed arm", () => {
    expect(rosterOf(studiesWith("thesis_prompt_or_physics/study.yaml").served)).toEqual({
      cells: [{ model: "qr-llm", sampling: "creative" }],
      seeds: [1, 7, 42],
      epsilons: [{ level: 0.1, available: true }, { level: 0.5, available: true }],
      supervised: false,
      deterministic: true,
    });
  });

  it("keeps the arms an edit does not touch, with their own ids", () => {
    const { served } = studiesWith("thesis_prompt_or_physics/study.yaml");
    const result = applyRoster(served, withSeedCount(rosterOf(served), 4));
    // The thesis names its quantum arm `qrng` and its biased arms `prng_bent_…`; those names are the study's.
    expect(result.arms.map((arm) => arm.id)).toEqual(["qrng", "prng_seed_1", "prng_seed_7", "prng_seed_42", "prng_seed_2", "prng_bent_010", "prng_bent_050"]);
    for (const id of ["qrng", "prng_seed_1", "prng_seed_7", "prng_seed_42"]) {
      expect(result.arms.find((arm) => arm.id === id)).toBe(served.arms.find((arm) => arm.id === id));
    }
    expect(rosterOf(result).seeds).toEqual([1, 7, 42, 2]);
  });

  it("rebuilds the template's own arms from its roster alone", () => {
    const template = studyOneTemplate();
    const rebuilt = applyRoster({ ...template, arms: [] }, rosterOf(template));
    expect(rebuilt.arms.map((arm) => arm.id)).toEqual(template.arms.map((arm) => arm.id));
    // A switched-off arm made from scratch has the page's reason, not the template's.
    const comparable = (arms: Arm[]) => arms.map((arm) => ({ ...arm, unavailable_reason: arm.unavailable_reason ? "a reason" : null }));
    expect(comparable(rebuilt.arms)).toEqual(comparable(template.arms));
    expect(rebuilt.seeds).toEqual(template.seeds);
    expect(parseStudyConfig(rebuilt)).not.toBeNull();
  });
});

describe("rosterFromArms", () => {
  const template = () => studyOneTemplate();
  const without = (config: WizardStudyConfig, id: string) => config.arms.filter((arm) => arm.id !== id);
  const changed = (config: WizardStudyConfig, id: string, patch: Partial<Arm>) => config.arms.map((arm) => (arm.id === id ? { ...arm, ...patch } : arm));

  it("returns null rather than guess at arms that are not one roster", () => {
    const config = template();
    const second = (arm: Arm): Arm => ({ ...arm, id: `${arm.id}_b`, sampling: "t1_minp" });
    const cases: Record<string, Arm[]> = {
      "no quantum arm": without(config, "qrng_unsupervised"),
      "a quantum arm that is switched off": changed(config, "qrng_unsupervised", { availability: "unavailable", unavailable_reason: "down" }),
      "a control arm that is switched off": changed(config, "prng_seed_7", { availability: "unavailable", unavailable_reason: "down" }),
      "two control arms on one seed": changed(config, "prng_seed_7", { study_seed: 1 }),
      "an observed control arm": changed(config, "prng_seed_7", { observer_mode: "supervised" }),
      "greedy sampling": changed(config, "prng_seed_7", { sampling: "greedy" }),
      "a provider arm": [...config.arms, { id: "provider", model: "qr-llm", sampling: "creative", source: "provider" }],
      "a system pseudo-random arm": [...config.arms, { id: "system", model: "qr-llm", sampling: "creative", source: "prng_system" }],
      "biased arms on different seeds": changed(config, "prng_epsilon_003", { study_seed: 2002 }),
      "a biased level off the registered grid": changed(config, "prng_epsilon_003", { epsilon: 0.2 }),
      "the same biased level twice": changed(config, "prng_epsilon_003", { epsilon: 0.01 }),
      "mixed determinism": changed(config, "prng_seed_7", { deterministic: false }),
      "comparisons with different seeds": [...config.arms, ...config.arms.map(second).map((arm) => (arm.id === "prng_seed_7_b" ? { ...arm, study_seed: 8 } : arm))],
      "an observed arm in one comparison only": [...config.arms, ...config.arms.filter((arm) => arm.observer_mode !== "supervised").map(second)],
    };
    for (const [name, arms] of Object.entries(cases)) expect(rosterFromArms(arms), name).toBeNull();
  });

  it("reads two comparisons with the same roles as one roster", () => {
    const config = template();
    const arms = [...config.arms, ...config.arms.map((arm): Arm => ({ ...arm, id: `${arm.id}_b`, sampling: "t1_minp" }))];
    expect(rosterFromArms(arms)?.cells).toEqual([{ model: "qr-llm", sampling: "creative" }, { model: "qr-llm", sampling: "t1_minp" }]);
  });

  it("reads a study without arms as an empty roster that remembers its seeds", () => {
    expect(rosterFromArms([], [3, 4])).toEqual({ cells: [], seeds: [3, 4], epsilons: [], supervised: false, deterministic: true });
    expect(rosterFromArms([])?.seeds).toEqual([...DEFAULT_SEEDS]);
  });

  it("gives a study without arms the roles it is told the comparisons last had, and the defaults for those it is not told", () => {
    const remembered = { seeds: [3, 4], epsilons: [{ level: 0.03, available: false }, { level: 0.1, available: true }], supervised: true, deterministic: false };
    expect(rosterFromArms([], remembered)).toEqual({ cells: [], ...remembered });
    // The memory is copied, so an edit of the roster does not reach back into it.
    expect(rosterFromArms([], remembered)!.epsilons[0]).not.toBe(remembered.epsilons[0]);
    expect(rosterFromArms([], { seeds: [9] })).toEqual({ cells: [], seeds: [9], epsilons: [], supervised: false, deterministic: true });
    // Arms speak for themselves: a memory is only for a study that has none.
    const template = studyOneTemplate();
    expect(rosterFromArms(template.arms, remembered)).toEqual(rosterFromArms(template.arms, template.seeds));
  });

  it("unticks the last comparison and ticks one again without losing a role, given the roster from before", () => {
    const template = studyOneTemplate();
    const study: WizardStudyConfig = { ...template, supervised: { ...template.supervised!, intention_timer_min: 5 } };
    const before = rosterFromArms(study.arms, study.seeds)!;
    const cell = before.cells[0]!;
    const emptied = applyRoster(study, withCell(before, cell, false));
    expect(emptied.arms).toEqual([]);
    // Read back with what the page remembers, the next tick is applied to the same roles.
    const memory = { seeds: emptied.seeds!, epsilons: before.epsilons, supervised: before.supervised, deterministic: before.deterministic };
    const again = applyRoster(emptied, withCell(rosterFromArms(emptied.arms, memory)!, cell, true));
    expect(again.arms.map((arm) => arm.id)).toEqual(study.arms.map((arm) => arm.id));
    expect(rosterFromArms(again.arms, again.seeds)).toEqual(before);
    expect(again.supervised?.intention_timer_min).toBe(5);
    expect(again.seeds).toEqual([1, 7, 42, 1001]);
    // Read back from the seed list alone, as before this was remembered, the roles would be gone.
    const forgotten = applyRoster(emptied, withCell(rosterFromArms(emptied.arms, emptied.seeds)!, cell, true));
    expect(forgotten.arms).toHaveLength(4);
    expect(forgotten.supervised).toBeNull();
  });
});

describe("applyRoster", () => {
  it("never puts a biased arm on a control seed", () => {
    expect(epsilonSeedFor([1, 7, 42])).toBe(EPSILON_SEED_FLOOR);
    expect(epsilonSeedFor([1001, 1002, 7])).toBe(1003);

    // Study 1 as written has its biased arms on seed 42, a control seed. Any edit moves them off it.
    const { served } = studiesWith("study1.yaml");
    const edited = applyRoster(served, withEpsilon(rosterOf(served), 0.1, "on"));
    const control = edited.arms.filter((arm) => arm.source === "prng_seeded").map((arm) => arm.study_seed);
    const biased = edited.arms.filter((arm) => arm.source === "prng_epsilon");
    expect(biased).toHaveLength(3);
    for (const arm of biased) {
      expect(arm.study_seed).toBe(1001);
      expect(control).not.toContain(arm.study_seed);
    }
    expect(edited.seeds).toEqual([1, 7, 42, 1001]);
    // The arms keep their ids and the reasons of the ones still switched off.
    expect(biased.map((arm) => arm.id)).toEqual(["prng_epsilon_001", "prng_epsilon_003", "prng_epsilon_010"]);
    expect(biased.map((arm) => arm.availability)).toEqual(["unavailable", "unavailable", "available"]);
    expect(biased[0]!.unavailable_reason).toBe(served.arms.find((arm) => arm.id === "prng_epsilon_001")!.unavailable_reason);
    expect(biased[2]!.unavailable_reason).toBeNull();

    // With control seeds on 1001 and 1002 the biased arms take the next free one.
    const crowded = applyRoster(edited, { ...rosterOf(edited), seeds: [1001, 1002] });
    expect(crowded.arms.filter((arm) => arm.source === "prng_epsilon").map((arm) => arm.study_seed)).toEqual([1003, 1003, 1003]);
    expect(parseStudyConfig(crowded)).not.toBeNull();
  });

  it("gives every arm of every comparison an id of its own", () => {
    const template = studyOneTemplate();
    const roster = rosterOf(template);
    const cells = [
      { model: "qr-llm", sampling: "creative" }, { model: "qr-llm", sampling: "t1_minp" },
      { model: "qr-llm:v2", sampling: "creative" }, { model: "qr-llm:v2", sampling: "t1_minp" },
    ] as const;
    const result = applyRoster(template, { ...roster, cells: [...cells] }, [QR, QR2]);
    const ids = result.arms.map((arm) => arm.id);
    expect(result.arms).toHaveLength(cells.length * 8);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z0-9_]{1,64}$/);
    // The first comparison keeps the plain ids it had; the others are named after their model and preset.
    expect(ids).toContain("qrng_unsupervised");
    expect(ids).toContain("qrng_unsupervised__qr_llm__t1_minp");
    expect(ids).toContain("prng_epsilon_010__qr_llm_v2__creative");
    expect(result.models.map((model) => model.id)).toEqual(["qr-llm", "qr-llm:v2"]);
    expect(rosterFromArms(result.arms)?.cells).toEqual(cells);
    expect(parseStudyConfig(result)).not.toBeNull();
  });

  it("keeps ids apart when a long model name is cut to fit", () => {
    const long: ModelRef = { ...QR, id: `qr-${"x".repeat(80)}`, served_name: "long" };
    const template = studyOneTemplate();
    const result = applyRoster(template, { ...rosterOf(template), cells: [{ model: long.id, sampling: "creative" }, { model: long.id, sampling: "t1_minp" }] }, [long]);
    const ids = result.arms.map((arm) => arm.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(Math.max(...ids.map((id) => id.length))).toBeLessThanOrEqual(64);
  });

  it("drops the supervised block with the observed arm, and brings a default one back with it", () => {
    const template = studyOneTemplate();
    const off = applyRoster(template, { ...rosterOf(template), supervised: false });
    expect(off.supervised).toBeNull();
    expect(off.arms.some((arm) => arm.observer_mode === "supervised")).toBe(false);
    const on = applyRoster(off, { ...rosterOf(off), supervised: true });
    expect(on.arms[0]).toMatchObject({ id: "qrng_supervised", source: "qrng", observer_mode: "supervised" });
    expect(on.supervised).toMatchObject({ display: "single", intention_timer_min: 3, group: false });
    expect(parseStudyConfig(on)).not.toBeNull();
  });

  it("drops a model no comparison uses and repairs the narrative model", () => {
    const template = { ...studyOneTemplate(), models: [QR, QR2], narrative_model: QR2 };
    const moved = applyRoster(template, { ...rosterOf(template), supervised: false });
    expect(moved.models).toEqual([QR]);
    expect(moved.narrative_model).toEqual(QR);
    // A comparison the study already runs keeps its arms when its model has dropped out of the model list: the
    // next edit of another role must not delete them.
    const orphaned: WizardStudyConfig = { ...template, models: template.models.map((model) => ({ ...model, id: "qr-llm-v2" })) };
    const edited = applyRoster(orphaned, withSeedCount(rosterOf(orphaned), 4));
    expect(edited.arms).toHaveLength(orphaned.arms.length + 1);
    expect(new Set(edited.arms.map((arm) => arm.model))).toEqual(new Set(["qr-llm"]));
    // A comparison on a model nobody knows cannot be written as arms.
    const unknown = applyRoster(template, withCell(rosterOf(template), { model: "nobody", sampling: "creative" }, true));
    expect(unknown.arms.some((arm) => arm.model === "nobody")).toBe(false);
  });

  it("leaves a study without comparisons with its models and seeds", () => {
    const template = studyOneTemplate();
    const empty = applyRoster(template, { ...rosterOf(template), cells: [] });
    expect(empty.arms).toEqual([]);
    expect(empty.models).toEqual(template.models);
    expect(empty.seeds).toEqual([1, 7, 42]);
    expect(rosterFromArms(empty.arms, empty.seeds)?.seeds).toEqual([1, 7, 42]);
  });

  it("applies determinism to every arm", () => {
    const template = studyOneTemplate();
    const result = applyRoster(template, { ...rosterOf(template), deterministic: false });
    expect(result.arms.every((arm) => arm.deterministic === false)).toBe(true);
    expect(result.arms.map((arm) => arm.id)).toEqual(template.arms.map((arm) => arm.id));
  });
});

describe("roster edits", () => {
  const roster = (): Roster => ({ cells: [{ model: "qr-llm", sampling: "creative" }], seeds: [1, 7, 42], epsilons: [], supervised: false, deterministic: true });

  it("adds and drops control seeds without repeating one", () => {
    expect(withSeedCount(roster(), 2).seeds).toEqual([1, 7]);
    expect(withSeedCount(roster(), 5).seeds).toEqual([1, 7, 42, 2, 3]);
    expect(withSeedCount({ ...roster(), seeds: [5] }, 3).seeds).toEqual([5, 1, 7]);
  });

  it("cycles a positive control through absent, planned and on, in grid order", () => {
    const planned = withEpsilon(withEpsilon(roster(), 0.5, "on"), 0.03, "off");
    expect(planned.epsilons).toEqual([{ level: 0.03, available: false }, { level: 0.5, available: true }]);
    expect(epsilonState(planned, 0.03)).toBe("off");
    expect(epsilonState(planned, 0.5)).toBe("on");
    expect(epsilonState(planned, 0.1)).toBe("absent");
    expect(withEpsilon(planned, 0.5, "absent").epsilons).toEqual([{ level: 0.03, available: false }]);
  });

  it("ticks and unticks a comparison", () => {
    const two = withCell(roster(), { model: "qr-llm", sampling: "t1_minp" }, true);
    expect(two.cells).toHaveLength(2);
    expect(withCell(two, { model: "qr-llm", sampling: "creative" }, false).cells).toEqual([{ model: "qr-llm", sampling: "t1_minp" }]);
  });
});

describe("nearestRoster", () => {
  it("is the roster itself for arms that are one", () => {
    const template = studyOneTemplate();
    expect(nearestRoster(template.arms, template.seeds)).toEqual(rosterFromArms(template.arms, template.seeds));
  });

  it("makes a runnable roster of hand-written arms", () => {
    const template = studyOneTemplate();
    const arms: Arm[] = [
      ...template.arms.filter((arm) => arm.id !== "prng_seed_7" && arm.id !== "prng_seed_42"),
      { id: "provider", model: "gpt", sampling: "greedy", source: "provider" },
    ];
    expect(rosterFromArms(arms)).toBeNull();
    const roster = nearestRoster(arms, template.seeds);
    expect(roster.cells).toEqual([{ model: "qr-llm", sampling: "creative" }]);
    expect(roster.seeds).toEqual([1, 7]);
    expect(roster.supervised).toBe(true);
    expect(roster.epsilons.map((epsilon) => epsilon.level)).toEqual([0.01, 0.03, 0.1]);
    const rebuilt = applyRoster({ ...template, arms }, roster);
    expect(rosterFromArms(rebuilt.arms)).not.toBeNull();
    expect(parseStudyConfig(rebuilt)).not.toBeNull();
  });
});
