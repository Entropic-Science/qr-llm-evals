import { describe, expect, it } from "vitest";
import { setConditioning } from "./config-edits";
import { HASHED_DEFAULT, conditioningProblems, rawBytesPerToken, readRatio, resolveConditioning } from "./conditioning";
import { studyOneTemplate } from "./config-store";
import { problems } from "./problems";

describe("entropy conditioning", () => {
  it("starts a new study hashed with the standard parameters", () => {
    expect(studyOneTemplate().conditioning).toEqual({ mode: "hash_truncate", hash: "sha256", in_bytes: 128, keep_bytes: 16 });
    expect(HASHED_DEFAULT).toEqual(studyOneTemplate().conditioning);
  });

  it("reads a study without the block as raw", () => {
    expect(resolveConditioning(undefined).mode).toBe("raw");
    expect(resolveConditioning(null).mode).toBe("raw");
    expect(rawBytesPerToken(undefined)).toBe(10_000);
    expect(conditioningProblems(undefined)).toEqual([]);
  });

  it("reads 80,000 raw bytes for a hashed token at the standard parameters", () => {
    expect(readRatio(HASHED_DEFAULT)).toBe(8);
    expect(readRatio({ ...HASHED_DEFAULT, in_bytes: 40 })).toBe(2.5);
    expect(readRatio(undefined)).toBe(1);
    expect(rawBytesPerToken(HASHED_DEFAULT)).toBe(80_000);
    expect(rawBytesPerToken({ ...HASHED_DEFAULT, in_bytes: 64, keep_bytes: 8 })).toBe(80_000);
    expect(rawBytesPerToken({ ...HASHED_DEFAULT, hash: "sha512", in_bytes: 256, keep_bytes: 32 })).toBe(80_128);
  });

  it("names what the server would refuse", () => {
    expect(conditioningProblems(HASHED_DEFAULT)).toEqual([]);
    expect(conditioningProblems({ ...HASHED_DEFAULT, keep_bytes: 17 })).toEqual([{
      message: "Serving more than half of each digest may compromise the integrity of the signal; keep at most 16 bytes of a SHA-256 digest.",
      path: "conditioning.keep_bytes",
    }]);
    expect(conditioningProblems({ ...HASHED_DEFAULT, hash: "sha512", in_bytes: 128, keep_bytes: 33 })[0]?.message).toContain("keep at most 32 bytes of a SHA-512 digest");
    expect(conditioningProblems({ ...HASHED_DEFAULT, keep_bytes: 16 })).toEqual([]);
    expect(conditioningProblems({ ...HASHED_DEFAULT, keep_bytes: 0 }).map((problem) => problem.path)).toEqual(["conditioning.keep_bytes"]);
    expect(conditioningProblems({ ...HASHED_DEFAULT, in_bytes: 31 }).map((problem) => problem.path)).toEqual(["conditioning.in_bytes"]);
    expect(conditioningProblems({ ...HASHED_DEFAULT, hash: "sha512", keep_bytes: 32, in_bytes: 64 })).toEqual([]);
    // Raw ignores the parameters.
    expect(conditioningProblems({ mode: "raw", hash: "sha256", in_bytes: 1, keep_bytes: 99 })).toEqual([]);
  });

  it("edits one setting and keeps the rest, and gives a study without the block one", () => {
    const template = studyOneTemplate();
    expect(setConditioning(template, { keep_bytes: 8 }).conditioning).toEqual({ ...HASHED_DEFAULT, keep_bytes: 8 });
    const legacy = { ...template, conditioning: null };
    expect(setConditioning(legacy, { mode: "hash_truncate" }).conditioning).toEqual(HASHED_DEFAULT);
    expect(setConditioning(legacy, { hash: "sha512" }).conditioning).toEqual({ ...HASHED_DEFAULT, mode: "raw", hash: "sha512" });
  });

  it("lists a refused parameter among the study's problems, on the compare step", () => {
    const bad = setConditioning(studyOneTemplate(), { keep_bytes: 20 });
    const found = problems(bad, []).filter((problem) => problem.code === "compare.conditioning");
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({ severity: "error", stepId: "compare", path: "conditioning.keep_bytes" });
    expect(problems(studyOneTemplate(), []).some((problem) => problem.code === "compare.conditioning")).toBe(false);
  });
});
