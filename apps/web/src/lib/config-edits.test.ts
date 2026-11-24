import { cards } from "@qr-evals/contracts";
import studyOneJson from "../../../../packages/contracts/fixtures/study1.json";
import {
  declareDesign, recommendedRepetitions, removeDesign, setAlpha, setCardItems, setCardK, setCardMaxTokens, setClaim, setCreatedAt, setMargin, setModelField,
  setSensitivityEpsilon, setSupervised, setTitle, toggleCard, toggleConfirmatory, useRecommended,
} from "./config-edits";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "./config-store";
import { MAX_CONFIRMATORY, recommendDesign, recommendK } from "./design";
import { problems } from "./problems";
import { rate, recFor } from "./recommendations";
import { applyRoster, rosterFromArms, withEpsilon, withSeedCount } from "./roster";
import { studySnapshot, toStudyYaml } from "./study-file";

const PROBE = { card_id: "self_referential_probe", endpoint: "experience_claim_rate" };
const MEQ = { card_id: "altered_state_meq", endpoint: "meq30_total" };
const legacy = (): WizardStudyConfig => parseStudyConfig(studyOneJson)!;
const overrides = (config: WizardStudyConfig, cardId: string) => config.cards.find((selection) => selection.card_id === cardId)?.overrides;
const recommend = (path: string, config: WizardStudyConfig) => useRecommended(path)(config, cards);

describe("config edits", () => {
  it("never change the study they are given", () => {
    const config = studyOneTemplate();
    const before = JSON.stringify(config);
    setTitle(config, "x");
    setCardK(config, PROBE.card_id, 25);
    toggleCard(config, "novelty_bench");
    toggleConfirmatory(config, PROBE);
    setMargin(config, MEQ, 0.05);
    recommend("design", config);
    expect(JSON.stringify(config)).toBe(before);
  });

  it("return the same study when nothing changes", () => {
    const config = studyOneTemplate();
    expect(setCardK(config, PROBE.card_id, 24)).toBe(config);
    expect(setCardItems(config, PROBE.card_id, null)).toBe(config);
    expect(setCardItems(config, "tier0_pit_residuals", null)).toBe(config);
    expect(setCardK(config, "not_selected", 5)).toBe(config);
    expect(declareDesign(config, cards)).toBe(config);
    expect(recommend("no.such.setting", config)).toBe(config);
    expect(setSupervised({ ...config, supervised: null }, { group: true }).supervised).toBeNull();
  });

  it("leave a study without a design block alone when asked to set a design value", () => {
    const config = legacy();
    expect(setAlpha(config, 0.01)).toBe(config);
    expect(setClaim(config, "difference_or_equivalence")).toBe(config);
    expect(setSensitivityEpsilon(config, 0.1)).toBe(config);
    expect(toggleConfirmatory(config, PROBE)).toBe(config);
    expect(setMargin(config, MEQ, 0.05)).toBe(config);
  });

  it("set and clear an override, keeping the other one", () => {
    const config = studyOneTemplate();
    const fewer = setCardItems(config, PROBE.card_id, 12);
    expect(overrides(fewer, PROBE.card_id)).toEqual({ k: 24, items: 12 });
    expect(overrides(setCardItems(fewer, PROBE.card_id, null), PROBE.card_id)).toEqual({ k: 24, items: null });
    expect(overrides(setCardK(config, "tier0_pit_residuals", 3), "tier0_pit_residuals")).toEqual({ k: 3 });
  });

  it("set a token limit as a key of its own and take it back by removing the key, never by storing null", () => {
    const config = studyOneTemplate();
    const before = JSON.stringify(config);
    // The template holds no token limit, not even an empty one.
    expect(before).not.toContain("max_tokens\":null");
    expect(overrides(config, PROBE.card_id)).toEqual({ k: 24, items: null });
    const set = setCardMaxTokens(config, PROBE.card_id, 3200);
    expect(overrides(set, PROBE.card_id)).toEqual({ k: 24, items: null, max_tokens: 3200 });
    // The other edits keep it, and it keeps them.
    expect(overrides(setCardK(set, PROBE.card_id, 25), PROBE.card_id)).toEqual({ k: 25, items: null, max_tokens: 3200 });
    expect(overrides(setCardItems(set, PROBE.card_id, 12), PROBE.card_id)).toEqual({ k: 24, items: 12, max_tokens: 3200 });
    const back = setCardMaxTokens(set, PROBE.card_id, null);
    expect(Object.keys(overrides(back, PROBE.card_id)!)).toEqual(["k", "items"]);
    expect(JSON.stringify(back)).toBe(before);
    expect(toStudyYaml(back)).toBe(toStudyYaml(config));
    expect(JSON.stringify(config)).toBe(before);

    // An eval the study gives no override at all gets a block for the limit, and loses it with the limit.
    expect(overrides(config, "source_guessing")).toBeUndefined();
    const guessing = setCardMaxTokens(config, "source_guessing", 256);
    expect(overrides(guessing, "source_guessing")).toEqual({ max_tokens: 256 });
    const bare = setCardMaxTokens(guessing, "source_guessing", null);
    expect(bare.cards.find((selection) => selection.card_id === "source_guessing")).toEqual({ card_id: "source_guessing" });
    expect(JSON.stringify(bare)).toBe(before);

    // An edit that changes nothing returns the study it was given.
    expect(setCardMaxTokens(config, PROBE.card_id, null)).toBe(config);
    expect(setCardMaxTokens(set, PROBE.card_id, 3200)).toBe(set);
    expect(setCardMaxTokens(config, "no_such_eval", 500)).toBe(config);
  });

  it("deselect an eval without rewriting the design, and select it again", () => {
    const config = studyOneTemplate();
    const without = toggleCard(config, PROBE.card_id);
    expect(without.cards.some((selection) => selection.card_id === PROBE.card_id)).toBe(false);
    expect(without.design).toBe(config.design);
    expect(toggleCard(without, PROBE.card_id).cards.at(-1)).toEqual({ card_id: PROBE.card_id });
  });

  it("add and remove a deciding outcome, taking its margin with it, up to the limit", () => {
    const config = studyOneTemplate();
    const added = setMargin(toggleConfirmatory(config, MEQ), MEQ, 0.05);
    expect(added.design?.confirmatory.at(-1)).toEqual(MEQ);
    expect(added.design?.margins).toEqual([{ ...MEQ, margin: 0.05 }]);
    expect(setMargin(added, MEQ, 0.08).design?.margins).toEqual([{ ...MEQ, margin: 0.08 }]);
    const removed = toggleConfirmatory(added, MEQ);
    expect(removed.design?.confirmatory).toEqual(config.design?.confirmatory);
    expect(removed.design?.margins).toEqual([]);

    const full = { ...config, design: { ...config.design!, confirmatory: Array.from({ length: MAX_CONFIRMATORY }, (_, index) => ({ card_id: "c", endpoint: `e${index}` })) } };
    expect(toggleConfirmatory(full, MEQ)).toEqual(full);
  });

  it("declare the recommended design on a legacy study, and nothing where no outcome can decide one", () => {
    const config = legacy();
    const declared = declareDesign(config, cards);
    expect(declared.design).toEqual(recommendDesign(config, cards));
    expect(declared.design?.confirmatory).toHaveLength(3);
    const undecidable = { ...config, cards: [{ card_id: "introspection_scales" }] };
    expect(declareDesign(undecidable, cards)).toBe(undecidable);
  });
});

describe("declaring a design and taking it back", () => {
  it("gives back the study byte for byte: Study 1 on the built-in plan, declared and returned, is the file it was", () => {
    // packages/contracts/fixtures/study1.json is the study whose hash, plan and pre-registration are pinned.
    const base = legacy();
    const declared = declareDesign(base, cards);
    expect(declared.design).toEqual(recommendDesign(base, cards));
    // The design block is all that declaring adds: no eval gets other repetitions or prompts.
    expect(declared.cards).toBe(base.cards);
    expect({ ...declared, design: base.design }).toEqual(base);
    const back = removeDesign(declared);
    // The same settings, so the same canonical JSON (what the config hash is taken over) and the same file.
    expect(back).toEqual(base);
    expect(studySnapshot(back)).toBe(studySnapshot(studyOneJson));
    expect(toStudyYaml(back)).toBe(toStudyYaml(base));
    expect(JSON.stringify(parseStudyConfig(back))).toBe(JSON.stringify(base));
    // The one-click fix of the problem row is the same edit, and comes back the same way.
    const fix = problems(base, cards).find((problem) => problem.code === "design.legacy_plan")!.fix!;
    expect(fix.apply(base, cards)).toEqual(declared);
    expect(studySnapshot(removeDesign(fix.apply(base, cards)))).toBe(studySnapshot(studyOneJson));
  });

  it("leaves the repetitions to the researcher: the chip says the probe needs k = 24, and its button sets exactly that", () => {
    const base = legacy();
    const own = (config: WizardStudyConfig) => overrides(config, PROBE.card_id)?.k ?? cards.find((card) => card.id === PROBE.card_id)!.k;
    const declared = declareDesign(base, cards);
    expect(own(declared)).toBe(20);
    expect(recommendedRepetitions(declared, cards, PROBE.card_id)).toBe(24);
    expect(own(recommend("cards.self_referential_probe.k", declared))).toBe(24);
    // A k the study sets higher is not taken down by declaring.
    expect(own(declareDesign(setCardK(base, PROBE.card_id, 28), cards))).toBe(28);
  });

  it("returns a designed study to the built-in plan by removing the design block and nothing else", () => {
    const designed = studyOneTemplate();
    const back = removeDesign(designed);
    expect(back.design ?? null).toBeNull();
    expect({ ...back, design: designed.design }).toEqual(designed);
    expect(removeDesign(back)).toBe(back);
    // And a design can be declared again.
    expect(declareDesign(back, cards).design).toEqual(recommendDesign(back, cards));
  });
});

describe("models and the record", () => {
  it("renames a model in the arms, the narrative model and the throughput that name it", () => {
    const study: WizardStudyConfig = { ...studyOneTemplate(), estimates: { tps_by_model: { "qr-llm": 40 } } };
    const renamed = setModelField(study, 0, { id: "qr-llm-v2" });
    expect(renamed.models.map((model) => model.id)).toEqual(["qr-llm-v2"]);
    expect(new Set(renamed.arms.map((arm) => arm.model))).toEqual(new Set(["qr-llm-v2"]));
    expect(renamed.narrative_model.id).toBe("qr-llm-v2");
    expect(renamed.estimates?.tps_by_model).toEqual({ "qr-llm-v2": 40 });
    expect(parseStudyConfig(renamed)).not.toBeNull();
    // Typing the id one character at a time ends at the same study.
    let typed = study;
    for (const id of ["qr-llm-", "qr-llm-v", "qr-llm-v2"]) typed = setModelField(typed, 0, { id });
    expect(typed).toEqual(renamed);
  });

  it("changes another field of a model without touching the arms, and leaves a rename onto an id in use to the problem list", () => {
    const study = studyOneTemplate();
    const pinned = setModelField(study, 0, { checkpoint: "org/model@abc" });
    expect(pinned.arms).toBe(study.arms);
    expect(pinned.models[0]!.checkpoint).toBe("org/model@abc");
    const two: WizardStudyConfig = { ...study, models: [...study.models, { id: "other", family: "external", provider: "custom", served_name: "other", checkpoint: null }] };
    const clash = setModelField(two, 1, { id: "qr-llm" });
    expect(clash.arms).toBe(two.arms);
    expect(setModelField(study, 5, { id: "x" })).toBe(study);
  });

  it("sets the configuration date to a moment, to the second and in UTC, in a form a study can hold", () => {
    const stamped = setCreatedAt(studyOneTemplate(), new Date(Date.UTC(2026, 10, 22, 15, 4, 5, 678)));
    expect(stamped.created_at).toBe("2026-11-22T15:04:05+00:00");
    expect(parseStudyConfig(stamped)).not.toBeNull();
  });
});

describe("useRecommended", () => {
  const template = studyOneTemplate();
  const design = template.design!;

  it("restores one design setting and leaves the others", () => {
    const edited: WizardStudyConfig = { ...template, design: { ...design, alpha: 0.005, confirmatory: design.confirmatory.slice(0, 1) } };
    expect(recommend("design.alpha", edited).design).toEqual({ ...edited.design, alpha: 0.05 });
    expect(recommend("design.confirmatory", edited).design).toEqual({ ...edited.design, confirmatory: design.confirmatory });
    expect(recommend("design", edited).design).toEqual(design);
  });

  it("moves the claim and the positive control together where one needs the other", () => {
    // No control runs: the recommended claim is a difference, and the recommended control is none.
    const unbacked: WizardStudyConfig = { ...template, design: { ...design, claim: "difference_or_equivalence", sensitivity_epsilon: 0.5 } };
    expect(recommend("design.sensitivity_epsilon", unbacked).design).toMatchObject({ claim: "difference", sensitivity_epsilon: null });
    expect(recommend("design.claim", unbacked).design).toMatchObject({ claim: "difference", sensitivity_epsilon: 0.5 });

    // With ε 0.10 running, the recommended claim includes an absence and brings the control with it.
    const running = applyRoster(template, withEpsilon(rosterFromArms(template.arms, template.seeds)!, 0.1, "on"));
    expect(recommend("design.claim", running).design).toMatchObject({ claim: "difference_or_equivalence", sensitivity_epsilon: 0.1 });
    expect(recommend("design.sensitivity_epsilon", running).design).toMatchObject({ claim: "difference", sensitivity_epsilon: 0.1 });
    expect(parseStudyConfig(recommend("design", running))).not.toBeNull();
  });

  it("drops the margins of outcomes that stop deciding the study, and withdraws one margin or all", () => {
    const withMargin = setMargin(toggleConfirmatory(template, MEQ), MEQ, 0.05);
    expect(recommend("design.confirmatory", withMargin).design).toEqual(design);
    expect(recommend("design.margins.altered_state_meq/meq30_total", withMargin).design?.margins).toEqual([]);
    expect(recommend("design.margins", withMargin).design?.margins).toEqual([]);
    expect(recommend("design.margins", withMargin).design?.confirmatory).toHaveLength(4);
  });

  it("returns prompts to all of the eval's, and k to the recommended repetitions: the card's own, or the fewest that make its prompts enough", () => {
    const edited = setCardK(setCardItems(template, PROBE.card_id, 6), PROBE.card_id, 25);
    expect(overrides(recommend("cards.self_referential_probe.items", edited), PROBE.card_id)).toEqual({ k: 25, items: null });
    // The probe decides the study and its own k = 20 leaves its 30 prompts too few: the recommended k is 24.
    expect(recommendK(edited, cards)).toEqual({ self_referential_probe: 24 });
    expect(overrides(recommend("cards.self_referential_probe.k", edited), PROBE.card_id)).toEqual({ k: 24, items: 6 });
    // Halving k is undone in one step, which is the way out of the red prompts beside it.
    expect(overrides(recommend("cards.self_referential_probe.k", setCardK(template, PROBE.card_id, 10)), PROBE.card_id)?.k).toBe(24);
    // An eval whose own k is enough goes back to it.
    const meq = setCardK(template, MEQ.card_id, 15);
    expect(overrides(recommend("cards.altered_state_meq.k", meq), MEQ.card_id)?.k ?? null).toBeNull();
  });

  it("sets on repetitions the number the chip names, in a study with a design block and in one without", () => {
    const named = (config: WizardStudyConfig) => Number(recFor(rate(config, cards), "cards.self_referential_probe.k")!.recommended);
    const set = (config: WizardStudyConfig) => {
      const next = recommend("cards.self_referential_probe.k", config);
      return overrides(next, PROBE.card_id)?.k ?? cards.find((card) => card.id === PROBE.card_id)!.k;
    };
    for (const k of [10, 20, 22, 28]) {
      const designed = setCardK(template, PROBE.card_id, k);
      expect(set(designed)).toBe(named(designed));
      expect(set(designed)).toBe(24);
      // On the built-in plan no outcome decides the study, so the recommended repetitions are the eval's own:
      // the chip says 20, and the button does not set the 24 a design would ask for.
      const builtIn = setCardK(legacy(), PROBE.card_id, k);
      expect(set(builtIn)).toBe(named(builtIn));
      expect(set(builtIn)).toBe(20);
    }
    // The eval's own k is stored as no override, as when it is typed into the field.
    expect(overrides(recommend("cards.self_referential_probe.k", setCardK(legacy(), PROBE.card_id, 10)), PROBE.card_id)?.k ?? null).toBeNull();
    // A k that is part of how an outcome is defined is never moved, and an eval without repetitions has none.
    expect(recommendedRepetitions(template, cards, "unpredictabench")).toBe(100);
    expect(recommendedRepetitions(template, cards, "tier0_pit_residuals")).toBeNull();
    expect(recommendedRepetitions(template, cards, "not_in_the_registry")).toBeNull();
  });

  it("brings the control seeds up to three, and never takes one away", () => {
    const roster = rosterFromArms(template.arms, template.seeds)!;
    const two = applyRoster(template, withSeedCount(roster, 2));
    expect(rosterFromArms(recommend("roster.seeds", two).arms)?.seeds).toEqual([1, 7, 42]);
    const five = applyRoster(template, withSeedCount(roster, 5));
    expect(recommend("roster.seeds", five)).toBe(five);
    const byHand = { ...template, arms: [...template.arms, { id: "system", model: "qr-llm", sampling: "creative" as const, source: "prng_system" as const }] };
    expect(recommend("roster.seeds", byHand)).toBe(byHand);
  });

  it("changes nothing about a design when no outcome can decide a study", () => {
    const undecidable: WizardStudyConfig = { ...template, cards: [{ card_id: "introspection_scales" }] };
    expect(recommend("design", undecidable)).toBe(undecidable);
    expect(recommend("design.alpha", undecidable)).toBe(undecidable);
  });
});
