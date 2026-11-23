import type { SampleSize } from "@qr-evals/contracts";
import fixture from "../../../../packages/contracts/fixtures/power.json";
import {
  EQUIVALENCE_SIDE_POWER, MARGIN_CAP_FRACTION, MARGIN_FLOOR_FRACTION, MARGINAL_POWER, MIN_COMPLETENESS, POWER, PRECISION,
  REFERENCE_EFFECT, controlFloor, equivalenceFloor, kNeeded, mdeStandardized, nFloor, powerAt, promptsNeeded, rate, sdBound, z,
  type Level, type RateArgs,
} from "./power";

/** The designed Study 1 probe: 30 prompts, k = 24 of 20..30, three told cells, three seeds, nine seed-pair tests. */
const PROBE: RateArgs = {
  level: "sample", range: [0, 1], margin: 0.05, n: 30, dataset_items: 30, k: 24, told_cells: 3, pooled_seeds: 3,
  alpha: 0.05, m: 3, m_e: 1, m_control: 9, k_default: 20, k_max: 30,
};

/** The mirror of one backend function, called with a fixture row's named arguments. */
const FUNCTIONS: Record<string, (args: Record<string, number | string>) => number | null> = {
  z: (a) => z(a.p as number),
  n_floor: (a) => nFloor(a.alpha as number, a.m as number),
  control_floor: (a) => controlFloor(a.alpha as number, a.m_control as number),
  mde_standardized: (a) => mdeStandardized(a.n as number, a.alpha as number, a.m as number),
  sd_bound: (a) => sdBound(a.lo as number, a.hi as number, a.level as Level, a.k as number, a.told_cells as number, a.pooled_seeds as number),
  power_at: (a) => powerAt(a.effect as number, a.sd as number, a.n as number, a.alpha as number, a.m as number),
  prompts_needed: (a) => promptsNeeded(a.effect_over_sd as number, a.alpha as number, a.m as number),
  equivalence_floor: (a) => equivalenceFloor(a.sd as number, a.n as number, a.alpha as number, a.m_e as number),
  k_needed: (a) => kNeeded(
    a.lo as number, a.hi as number, a.margin as number, a.n as number, a.told_cells as number, a.pooled_seeds as number,
    a.alpha as number, a.m as number, a.k_from as number, a.k_to as number,
  ),
};

const TOLERANCE = fixture.tolerance;

function close(actual: number, expected: number, what: string) {
  expect(Math.abs(actual - expected), `${what}: got ${actual}, the backend has ${expected}`).toBeLessThanOrEqual(TOLERANCE);
}

/** A fixture row's arguments as `rate` takes them: JSON has no tuple, so `range` arrives as a list. */
function rateArgs(args: (typeof fixture.rate)[number]["args"]): RateArgs {
  const range = args.range === null ? null : [args.range[0]!, args.range[1]!] as const;
  return { ...args, level: args.level as Level, range };
}

const ROUNDED = ["mde_standardized", "sd_bound", "mde_raw", "power_at_margin", "equivalence_floor"] as const;

/** The fields that are counts, flags and labels: these must match exactly. */
function exactFields(size: SampleSize): Record<string, unknown> {
  return Object.fromEntries(Object.entries(size).filter(([name]) => !(ROUNDED as readonly string[]).includes(name)));
}

function expectSampleSize(actual: SampleSize, expected: SampleSize, what: string) {
  for (const name of ROUNDED) {
    const want = expected[name];
    const got = actual[name];
    if (want === null || got === null) expect(got, `${what}: ${name}`).toBe(want);
    else close(got, want, `${what}: ${name}`);
  }
  expect(exactFields(actual), what).toEqual(exactFields(expected));
}

describe("power: parity with cards/power.py", () => {
  it("has the backend's constants", () => {
    expect({
      POWER, REFERENCE_EFFECT, MIN_COMPLETENESS, MARGIN_CAP_FRACTION, MARGIN_FLOOR_FRACTION, MARGINAL_POWER, EQUIVALENCE_SIDE_POWER,
    }).toEqual(fixture.constants);
    expect(PRECISION).toBe(fixture.precision);
    expect(TOLERANCE).toBe(1e-9);
  });

  it("z: the normal quantile, centre and both tails", () => {
    expect(fixture.z.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.z) close(z(args.p), value, `z(${args.p})`);
  });

  it("z refuses a probability outside (0, 1), as the backend does", () => {
    for (const p of [0, 1, -0.1, 1.5, Number.NaN]) expect(() => z(p)).toThrow(RangeError);
  });

  it("nFloor", () => {
    expect(fixture.n_floor.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.n_floor) expect(nFloor(args.alpha, args.m), JSON.stringify(args)).toBe(value);
  });

  it("controlFloor: one prompt above the exact bound of the seed-pair family", () => {
    expect(fixture.control_floor.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.control_floor) {
      expect(controlFloor(args.alpha, args.m_control), JSON.stringify(args)).toBe(value);
      expect(value).toBe(nFloor(args.alpha, args.m_control) + 1);
    }
  });

  it("kNeeded: the fewest repetitions, or none within the bounds", () => {
    const values = fixture.k_needed.map((row) => row.value);
    expect(values.some((value) => value === null)).toBe(true);
    expect(values.some((value) => value !== null)).toBe(true);
    for (const { args, value } of fixture.k_needed) {
      expect(
        kNeeded(args.lo, args.hi, args.margin, args.n, args.told_cells, args.pooled_seeds, args.alpha, args.m, args.k_from, args.k_to),
        JSON.stringify(args),
      ).toBe(value);
    }
  });

  it("agrees with values worked out by hand, not returned by the backend", () => {
    // Every other vector is what cards/power.py returned, so it shows only that the two sides agree. These
    // rows were derived in scripts/gen_design_fixtures.py from a printed normal table and arithmetic.
    expect(fixture.hand_derived.length).toBeGreaterThanOrEqual(15);
    const covered = new Set<string>();
    for (const row of fixture.hand_derived) {
      const mirror = FUNCTIONS[row.function];
      expect(mirror, row.function).toBeDefined();
      const got = mirror!(row.args as Record<string, number | string>);
      const what = `${row.function}(${JSON.stringify(row.args)})`;
      if ("tolerance" in row && typeof row.tolerance === "number") {
        expect(Math.abs((got as number) - row.value), `${what}: got ${got}, by hand ${row.value}`).toBeLessThanOrEqual(row.tolerance);
      } else {
        expect(got, what).toBe(row.value);
      }
      covered.add(row.function);
    }
    expect([...covered].sort()).toEqual(Object.keys(FUNCTIONS).sort());
  });

  it("mdeStandardized", () => {
    expect(fixture.mde_standardized.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.mde_standardized) close(mdeStandardized(args.n, args.alpha, args.m), value, JSON.stringify(args));
  });

  it("sdBound", () => {
    expect(fixture.sd_bound.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.sd_bound) {
      close(sdBound(args.lo, args.hi, args.level as Level, args.k, args.told_cells, args.pooled_seeds), value, JSON.stringify(args));
    }
  });

  it("sdBound refuses the token stream and an empty range", () => {
    expect(() => sdBound(0, 1, "token_stream", 20, 3, 3)).toThrow(/no per-prompt variance bound/);
    expect(() => sdBound(1, 1, "sample", 20, 3, 3)).toThrow(/hi > lo/);
  });

  it("powerAt", () => {
    expect(fixture.power_at.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.power_at) close(powerAt(args.effect, args.sd, args.n, args.alpha, args.m), value, JSON.stringify(args));
  });

  it("promptsNeeded", () => {
    expect(fixture.prompts_needed.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.prompts_needed) expect(promptsNeeded(args.effect_over_sd, args.alpha, args.m), JSON.stringify(args)).toBe(value);
  });

  it("equivalenceFloor", () => {
    expect(fixture.equivalence_floor.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.equivalence_floor) close(equivalenceFloor(args.sd, args.n, args.alpha, args.m_e), value, JSON.stringify(args));
  });

  it("rate: every case, numbers to 1e-9 and everything else exactly", () => {
    expect(fixture.rate.length).toBeGreaterThan(0);
    for (const { args, value } of fixture.rate) expectSampleSize(rate(rateArgs(args)), value as SampleSize, JSON.stringify(args));
  });

  it("rate reproduces the worked example: at k = 24 all 30 prompts are sufficient for ±0.05", () => {
    const size = rate(PROBE);
    expect(size).toMatchObject({
      rating: "sufficient", basis: "margin_worst_case", n_floor: 7, control_floor: 10, n_recommended: 30, reachable: true, k_recommended: 24,
    });
    expect(size.sd_bound).toBeCloseTo(1 / 12, 9);
    expect(size.mde_raw).toBeCloseTo(0.0492, 4);
    expect(size.power_at_margin).toBeCloseTo(0.8139, 4);
  });

  it("counts the told cells of a seeded run once: at the eval's own k = 20 the probe is marginal", () => {
    // The told cells of a seeded run share a seed. Taken for independent values they gave 0.0745, 90% power
    // and 24 prompts; counted once, 30 prompts are marginal and 35 would be needed, which the eval lacks.
    expect(sdBound(0, 1, "sample", 20, 3, 3)).toBeCloseTo(0.5 * Math.sqrt(1 / 60 + 1 / 60), 12);
    const size = rate({ ...PROBE, k: 20 });
    expect(size).toMatchObject({ rating: "marginal", n_recommended: 35, reachable: false, k_recommended: 24 });
    expect(size.sd_bound).toBeCloseTo(0.0913, 4);
    expect(size.power_at_margin).toBeCloseTo(0.7277, 4);
    // Without the told factor nothing changed.
    expect(sdBound(0, 1, "sample", 10, 1, 3)).toBeCloseTo(0.5 * Math.sqrt((1 + 1 / 3) / 10), 12);
  });

  it("refuses an effect too small to plan for, as the backend does", () => {
    expect(() => promptsNeeded(1e-160, 0.05, 3)).toThrow(/too small to plan for/);
    expect(promptsNeeded(1e-3, 0.05, 3)).toBe(10_469_115);
  });
});

describe("power: what a rating may say", () => {
  const levels: Level[] = ["sample", "prompt_group"];
  const ranges: (readonly [number, number] | null)[] = [null, [0, 1], [-1, 1]];
  const margins = [null, 0.02, 0.05, 0.2];
  const sizes = [0, 1, 6, 7, 30, 300, 5000];

  it("never returns sufficient without both a range and a margin", () => {
    let rated = 0;
    for (const level of levels) for (const range of ranges) for (const margin of margins) for (const n of sizes) {
      const size = rate({ ...PROBE, level, range, margin, n, dataset_items: 5000, k: 20 });
      if (range === null || margin === null) {
        expect(size.rating, JSON.stringify({ level, range, margin, n })).not.toBe("sufficient");
        expect(size.basis).toBe("standardized_reference");
        expect([size.sd_bound, size.mde_raw, size.power_at_margin, size.equivalence_floor]).toEqual([null, null, null, null]);
        rated++;
      }
    }
    expect(rated).toBeGreaterThan(0);
    // The guard above is not vacuous: with both, enough prompts do earn it.
    expect(rate({ ...PROBE, n: 300, dataset_items: 5000, k: 20 }).rating).toBe("sufficient");
  });

  it("never rates a test on the pooled token stream", () => {
    for (const range of ranges) for (const margin of margins) for (const n of sizes) {
      const size = rate({ ...PROBE, level: "token_stream", range, margin, n, k: null, told_cells: 1, k_default: null, k_max: null });
      expect(size).toEqual({
        n, n_floor: 7, control_floor: null, mde_standardized: null, sd_bound: null, mde_raw: null, power_at_margin: null,
        equivalence_floor: null, n_recommended: null, reachable: null, k_recommended: null, rating: "unknown", basis: "no_closed_form",
      });
    }
  });

  it("rates anything below the floor insufficient, whatever the margin", () => {
    const size = rate({ ...PROBE, margin: 0.1, n: 6 });
    expect(size.n_floor).toBe(7);
    expect(size.rating).toBe("insufficient");
  });

  it("rates anything below the control floor insufficient and never recommends it", () => {
    // Seven to nine prompts can reach significance, but no seed pair among nine could fail its control.
    for (const n of [7, 8, 9]) {
      const size = rate({ ...PROBE, margin: 0.1, n, k: 30 });
      expect(size).toMatchObject({ n_floor: 7, control_floor: 10, rating: "insufficient" });
      expect(size.n_recommended).toBeGreaterThanOrEqual(10);
    }
    expect(rate({ ...PROBE, margin: 0.1, n: 10, k: 30 })).toMatchObject({ rating: "sufficient", n_recommended: 10 });
    // A draft without a seed pair has no control floor.
    expect(rate({ ...PROBE, m_control: 0 }).control_floor).toBeNull();
  });
});
