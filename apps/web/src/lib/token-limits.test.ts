import { cards, tokenLimits } from "@qr-evals/contracts";
import { effectiveLimit, limitStands, tokenLimitFor } from "./token-limits";

const card = (id: string) => {
  const found = cards.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no card ${id}`);
  return found;
};

describe("token limits", () => {
  it("reads the limits of an eval from the contracts package, and has none for an eval that generates nothing", () => {
    expect(tokenLimitFor("self_referential_probe")).toEqual(tokenLimits.self_referential_probe);
    expect(tokenLimitFor("self_referential_probe")).toEqual({ standard: 800, safe: 3200, min: 400, max: 64000 });
    expect(tokenLimitFor("target_hit_ranking")).toBeUndefined();
    expect(tokenLimitFor("no_such_eval")).toBeUndefined();
    // A name every object has is not an eval.
    expect(tokenLimitFor("constructor")).toBeUndefined();
    expect(tokenLimitFor("toString")).toBeUndefined();
  });

  it("has the card's own limit as the standard of every eval it lists", () => {
    for (const [id, limit] of Object.entries(tokenLimits)) {
      expect(card(id).max_tokens, id).toBe(limit.standard);
      expect(limit.min <= limit.standard && limit.standard <= limit.safe && limit.safe <= limit.max, id).toBe(true);
    }
  });

  it("gives the limit a reply runs with: the study's, or the card's own", () => {
    const probe = card("self_referential_probe");
    expect(effectiveLimit(probe, undefined)).toBe(800);
    expect(effectiveLimit(probe, { card_id: probe.id })).toBe(800);
    expect(effectiveLimit(probe, { card_id: probe.id, overrides: { k: 24 } })).toBe(800);
    expect(effectiveLimit(probe, { card_id: probe.id, overrides: { max_tokens: null } })).toBe(800);
    expect(effectiveLimit(probe, { card_id: probe.id, overrides: { max_tokens: 3200 } })).toBe(3200);
  });

  it("lets a limit stand only as a whole number within the bounds of an eval that has them", () => {
    expect(limitStands("self_referential_probe", 400)).toBe(true);
    expect(limitStands("self_referential_probe", 64000)).toBe(true);
    expect(limitStands("self_referential_probe", 399)).toBe(false);
    expect(limitStands("self_referential_probe", 64001)).toBe(false);
    expect(limitStands("self_referential_probe", 800.5)).toBe(false);
    expect(limitStands("target_hit_ranking", 500)).toBe(false);
  });
});
