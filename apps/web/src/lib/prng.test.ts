import { mulberry32, seedFromId } from "./prng";

describe("mulberry32", () => {
  it("yields the same sequence for the same seed, in [0, 1)", () => {
    const a = mulberry32(7), b = mulberry32(7);
    const first = Array.from({ length: 200 }, () => a());
    expect(Array.from({ length: 200 }, () => b())).toEqual(first);
    for (const value of first) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it("is pinned, so a stroke drawn today is the stroke drawn after any refactor", () => {
    const random = mulberry32(7);
    expect([random(), random(), random()].map((value) => value.toFixed(6))).toEqual(["0.011705", "0.061958", "0.976908"]);
  });

  it("gives different seeds different sequences", () => {
    expect(mulberry32(1)()).not.toBe(mulberry32(2)());
  });
});

describe("seedFromId", () => {
  it("is FNV-1a, 32-bit, on the published vectors", () => {
    expect(seedFromId("")).toBe(0x811c9dc5);
    expect(seedFromId("a")).toBe(0xe40c292c);
    expect(seedFromId("foobar")).toBe(0xbf9cf968);
  });

  it("is an unsigned 32-bit integer for any id, and separates the ids the map uses", () => {
    const ids = ["study", "arm:qrng_unsupervised", "deck:qr-llm/creative", "card:novelty_bench", "endpoint:novelty_bench/distinct", "test:h1", "gate:pos:qr-llm/creative", "verdict:h1", "ghost:evals", "Študie — 量子"];
    const seeds = ids.map(seedFromId);
    for (const seed of seeds) {
      expect(Number.isInteger(seed)).toBe(true);
      expect(seed).toBeGreaterThanOrEqual(0);
      expect(seed).toBeLessThanOrEqual(0xffffffff);
    }
    expect(new Set(seeds).size).toBe(ids.length);
    expect(seedFromId("card:novelty_bench")).toBe(seedFromId("card:novelty_bench"));
  });
});
