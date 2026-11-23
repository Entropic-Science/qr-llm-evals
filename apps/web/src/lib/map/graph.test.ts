import { cards, selectableCards } from "@qr-evals/contracts";
import fixture from "../../../../../packages/contracts/fixtures/design.json";
import { studyOneTemplate, type WizardStudyConfig } from "../config-store";
import {
  FOLD_ABOVE, LEGACY_BANNER, POOLED_TOKENS, STATIONS, TOKEN_AUDITS, UNREACHABLE_TEXT, VERDICT_LABELS, VERDICT_ORDER, VERDICT_RULES, buildStudyGraph,
  lineage, nodeById, shownByExpansion, visible, type MapNode, type StudyGraph,
} from "./graph";
import { problems as problemsOf } from "../problems";
import { rate } from "../recommendations";
import { FIXTURE_CASES, build, expandableIds, fixtureCase } from "./test-fixtures";

const NONE: ReadonlySet<string> = new Set();
const LANE = "qr-llm/creative";
const OTHER_LANE = "qr-llm/t1_minp";

const node = (graph: StudyGraph, id: string): MapNode => {
  const found = nodeById(graph, id);
  if (!found) throw new Error(`the graph has no node ${id}`);
  return found;
};
const ids = (nodes: readonly MapNode[]) => nodes.map((item) => item.id);
const stationIndex = (item: MapNode) => STATIONS.findIndex((station) => station.id === item.station);

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const item of Object.values(value)) deepFreeze(item);
  }
  return value;
}

/** What must hold for any study, however broken. */
function expectWellFormed(graph: StudyGraph, name: string) {
  const all = ids(graph.nodes);
  expect(new Set(all).size, `${name}: node ids are unique`).toBe(all.length);
  expect(new Set(graph.edges.map((edge) => edge.id)).size, `${name}: edge ids are unique`).toBe(graph.edges.length);
  const laneKeys = graph.lanes.map((lane) => lane.key);
  for (const item of graph.nodes) {
    expect(item.label.trim(), `${name}: ${item.id} has a label`).not.toBe("");
    expect(item.facts.story.trim(), `${name}: ${item.id} has a story sentence`).not.toBe("");
    if (item.lane !== null) expect(laneKeys, `${name}: ${item.id} sits in a lane of the graph`).toContain(item.lane);
    if (item.parent !== null) expect(node(graph, item.parent).children, `${name}: ${item.id} is a child of its parent`).toContain(item.id);
    expect(item.expandable, `${name}: ${item.id} is expandable iff it has an unpinned child`)
      .toBe(item.children.some((child) => !node(graph, child).pinned));
  }
  for (const edge of graph.edges) {
    const from = node(graph, edge.from), to = node(graph, edge.to);
    expect(edge.id).toBe(`${edge.from}>${edge.to}`);
    if (edge.implied) {
      // Only the two rules over every test hold without a line of their own.
      expect([edge.from, edge.to].some((id) => id === "family" || id === "gate:completeness"), `${name}: ${edge.id} is implied by a rule`).toBe(true);
      expect(stationIndex(from), `${name}: ${edge.id} never runs backwards`).toBeLessThanOrEqual(stationIndex(to));
    } else {
      expect(stationIndex(from), `${name}: ${edge.id} runs left to right`).toBeLessThan(stationIndex(to));
    }
    if (from.lane !== null && to.lane !== null) expect(to.lane, `${name}: ${edge.id} stays in its lane`).toBe(from.lane);
  }
  // Reading order: by station, and a parent before its children.
  const order = graph.nodes.map(stationIndex);
  expect([...order].sort((a, b) => a - b), `${name}: nodes are in station order`).toEqual(order);
  for (const item of graph.nodes) {
    if (item.parent !== null) expect(all.indexOf(item.parent), `${name}: ${item.parent} comes before ${item.id}`).toBeLessThan(all.indexOf(item.id));
  }
  // A positive-control arm leads to its gate and nowhere else.
  for (const item of graph.nodes.filter((entry) => entry.kind === "positive_control")) {
    const touching = graph.edges.filter((edge) => edge.from === item.id || edge.to === item.id);
    for (const edge of touching) expect(edge.to, `${name}: ${item.id} only reaches its gate`).toBe(`gate:pos:${item.lane}`);
  }
  // A test compares one outcome, and both sides of its own comparison reach it.
  for (const test of graph.nodes.filter((entry) => entry.kind === "test")) {
    const compares = graph.edges.filter((edge) => edge.to === test.id && edge.kind === "compares");
    expect(compares.length, `${name}: ${test.id} compares one outcome`).toBe(1);
    expect(graph.edges.filter((edge) => edge.to === test.id && edge.kind === "governs").map((edge) => [edge.from, edge.implied]), `${name}: ${test.id} is under the family`)
      .toEqual([["family", true]]);
    // Its thread runs through the negative control of its lane to its verdict.
    const verdict = test.id.replace("test:", "verdict:");
    expect(graph.edges.filter((edge) => edge.from === test.id && !edge.implied).map((edge) => edge.to), `${name}: ${test.id} thread`).toEqual([`gate:neg:${test.lane}`]);
    expect(graph.edges.some((edge) => edge.id === `gate:neg:${test.lane}>${verdict}` && !edge.implied), `${name}: ${verdict} thread`).toBe(true);
    const up = lineage(graph, test.id).upstream.nodes.map((id) => node(graph, id));
    expect(up.filter((entry) => entry.kind === "quantum_arm").map((entry) => entry.lane), `${name}: ${test.id} quantum side`).toEqual([test.lane]);
    expect(up.filter((entry) => entry.kind === "control_deck").map((entry) => entry.id), `${name}: ${test.id} control side`).toEqual([`deck:${test.lane}`]);
    expect(nodeById(graph, test.id.replace("test:", "verdict:")), `${name}: ${test.id} has a verdict`).toBeDefined();
  }
}

describe("study graph: the Study 1 template", () => {
  const config = studyOneTemplate();
  const { graph, problems, recs } = build(config);

  it("is well formed", () => expectWellFormed(graph, "template"));

  it("has seven stations, each with something drawn", () => {
    expect(STATIONS.map((station) => station.label)).toEqual(["Question", "Arms", "Prompts × repetitions", "Measures", "Tests", "Gates", "Verdict"]);
    const drawn = visible(graph, NONE, "design").nodes;
    for (const station of STATIONS) expect(drawn.filter((item) => item.station === station.id).length, station.id).toBeGreaterThan(0);
  });

  it("has one lane for the one comparison", () => {
    expect(graph.legacy).toBe(false);
    expect(graph.lanes).toEqual([{ key: LANE, model: "qr-llm", sampling: "creative", label: "qr-llm · Creative sampling", compared: true }]);
  });

  it("draws the study, every arm and the seeded deck", () => {
    expect(node(graph, "study")).toMatchObject({ kind: "study", station: "question", lane: null, label: config.title, sub: "claims a difference only" });
    expect(node(graph, "arm:qrng_unsupervised")).toMatchObject({ kind: "quantum_arm", station: "arms", lane: LANE, tone: "quantum", ghost: null });
    expect(node(graph, "arm:qrng_supervised")).toMatchObject({ kind: "supervised_arm", lane: LANE, sub: "exploratory, no verdict" });
    const deck = node(graph, `deck:${LANE}`);
    expect(deck).toMatchObject({ kind: "control_deck", tone: "control", sub: "3 seeds: 1, 7, 42", expandable: true });
    expect(deck.children).toEqual(["arm:prng_seed_1", "arm:prng_seed_7", "arm:prng_seed_42"]);
    for (const id of deck.children) expect(node(graph, id)).toMatchObject({ kind: "seeded_arm", parent: deck.id, pinned: false });
    for (const arm of config.arms) expect(nodeById(graph, `arm:${arm.id}`), arm.id).toBeDefined();
  });

  it("marks the planned positive controls as switched off, with no edge", () => {
    for (const id of ["arm:prng_epsilon_001", "arm:prng_epsilon_003", "arm:prng_epsilon_010"]) {
      const arm = node(graph, id);
      expect(arm).toMatchObject({ kind: "positive_control", tone: "positive" });
      expect(arm.ghost).toBe("Switched off: the arm is listed, not run.");
      expect(arm.sub).toContain("planned, off");
      expect(graph.edges.filter((edge) => edge.from === id || edge.to === id)).toEqual([]);
    }
  });

  it("draws every selected eval, with the deciding ones always in view", () => {
    for (const selection of config.cards) expect(nodeById(graph, `card:${selection.card_id}`), selection.card_id).toBeDefined();
    const band = node(graph, "tier0");
    expect(band).toMatchObject({ kind: "tier0_band", label: "Measured on the tokens", sub: "3 audits", expandable: true });
    expect(band.children).toEqual(["card:tier0_pit_residuals", "card:tier0_mmd_hamming", "card:tier0_logprob_endpoints"]);
    expect(node(graph, "card:tier0_pit_residuals")).toMatchObject({ parent: "tier0", pinned: true, sub: "the generated tokens" });
    expect(node(graph, "card:tier0_logprob_endpoints")).toMatchObject({ parent: "tier0", pinned: false });
    expect(node(graph, "card:self_referential_probe")).toMatchObject({ parent: null, sub: "30 prompts × k = 24", stepId: "design" });
    expect(node(graph, "card:self_referential_probe").ref).toMatchObject({ type: "card", prompts: 30, datasetPrompts: 30, k: 24, toldCells: 3, tokensPerArm: 864_000, decides: true, judged: true });
    // Eighteen evals are more than twelve: the rest fold into their tiers.
    expect(node(graph, "group:tier1")).toMatchObject({ kind: "card_group", label: "Tier 1", sub: "4 evals", expandable: true });
    expect(node(graph, "card:novelty_bench")).toMatchObject({ parent: "group:tier1", pinned: false, stepId: "measure" });
    expect(node(graph, "ghost:evals")).toMatchObject({ kind: "ghost_stack", label: `+ ${selectableCards.length - config.cards.filter((item) => selectableCards.some((card) => card.id === item.card_id)).length} more evals` });
    expect(nodeById(graph, "ghost:measures")).toBeUndefined();
  });

  it("pins the outcomes that decide the study and folds the rest into their eval", () => {
    const claim = node(graph, "endpoint:self_referential_probe/experience_claim_rate");
    expect(claim).toMatchObject({ kind: "endpoint", station: "measures", parent: "card:self_referential_probe", pinned: true, label: "Experience-claim rate", sub: "decides the study · ±0.05" });
    expect(claim.ref).toMatchObject({ role: "confirmatory", headline: true, margin: 0.05, test: "paired_permutation" });
    expect(node(graph, "endpoint:tier0_pit_residuals/pit_ks_two_sample")).toMatchObject({ pinned: true, sub: "decides the study · no margin" });
    const other = node(graph, "endpoint:self_referential_probe/semantic_entropy");
    expect(other).toMatchObject({ pinned: false, sub: "secondary outcome · exploratory" });
    expect(other.ref).toMatchObject({ role: "exploratory", headline: false });
    expect(node(graph, "card:self_referential_probe").expandable).toBe(true);
  });

  it("draws one test and one verdict per generated hypothesis, and nothing it was not given", () => {
    const tests = graph.nodes.filter((item) => item.kind === "test");
    expect(ids(tests)).toEqual(["test:H1", "test:H2", "test:H3"]);
    expect(tests.map((item) => item.label)).toEqual(["Two-sample KS test", "MMD permutation test", "Paired sign-flip test"]);
    expect(tests.map((item) => item.sub)).toEqual(["H1 · two-sided", "H2 · one-sided", "H3 · two-sided"]);
    expect(node(graph, "test:H3").facts.story).toMatch(/^H3: on Experience-claim rate/);
    expect(ids(graph.nodes.filter((item) => item.kind === "verdict"))).toEqual(["verdict:H1", "verdict:H2", "verdict:H3"]);
    expect(node(graph, "verdict:H3")).toMatchObject({ parent: "test:H3", pinned: true, lane: LANE });
    expect(node(graph, "test:H3").expandable).toBe(false);
  });

  it("draws the Holm family, the gates of the lane and the completeness gate", () => {
    expect(node(graph, "family")).toMatchObject({ kind: "family", station: "tests", lane: null, label: "Holm correction", sub: "m = 3 · α 0.05 · power 80%", ghost: null });
    expect(node(graph, `gate:neg:${LANE}`)).toMatchObject({ kind: "gate", station: "gates", lane: LANE, sub: "3 seed pairs must not differ" });
    expect(node(graph, `gate:pos:${LANE}`)).toMatchObject({ kind: "gate", lane: LANE, sub: "none", ghost: "No positive control is set." });
    expect(node(graph, "gate:completeness")).toMatchObject({ kind: "gate", lane: null, sub: "≥ 90% of the planned pairs" });
  });

  it("stamps what each hypothesis can end as, and why not the rest", () => {
    const stamps = (id: string) => {
      const ref = node(graph, id).ref;
      if (ref.type !== "verdict") throw new Error(`${id} is not a verdict`);
      return Object.fromEntries(ref.stamps.map((stamp) => [stamp.verdict, stamp]));
    };
    const probe = stamps("verdict:H3");
    expect(Object.keys(probe)).toEqual([...VERDICT_ORDER]);
    expect(probe.effect).toMatchObject({ label: "Effect", reachable: true, reason: null });
    expect(probe.effect_within_margin).toMatchObject({ reachable: true });
    expect(probe.equivalent).toMatchObject({ label: "Equivalent", reachable: false, why: "equivalence_not_claimed", reason: "The study registered no equivalence claim." });
    expect(probe.invalid!.rules).toHaveLength(2);
    expect(stamps("verdict:H1").effect_within_margin).toMatchObject({ reachable: false, reason: "This test has no equivalence margin." });
    expect(node(graph, "verdict:H3").sub).toBe("effect · effect within margin · inconclusive");
    expect(node(graph, "verdict:H3").facts.design).toEqual(["Equivalent: unreachable. The study registered no equivalence claim."]);
  });

  it("puts every rating and every problem on the node it names", () => {
    for (const rec of recs) expect(node(graph, rec.nodeId).recs, rec.path).toContain(rec);
    for (const problem of problems) expect(node(graph, problem.nodeId).problems, problem.code).toContain(problem);
    expect(graph.nodes.flatMap((item) => item.recs)).toHaveLength(recs.length);
    expect(graph.nodes.flatMap((item) => item.problems)).toHaveLength(problems.length);
  });

  it("rates a node by the worst of its settings, and never by a locked one", () => {
    expect(node(graph, "card:self_referential_probe").state).toBe("sufficient");
    expect(node(graph, "card:tier0_pit_residuals").state).toBe("unknown");
    expect(node(graph, "card:tier0_mmd_hamming").state).toBe("unknown");
    expect(node(graph, "arm:qrng_unsupervised")).toMatchObject({ state: null });
    expect(node(graph, "arm:qrng_unsupervised").recs.map((rec) => rec.state)).toEqual(["locked"]);
    expect(node(graph, "study")).toMatchObject({ severity: "error" });
    expect(node(graph, "study").problems.map((problem) => problem.code)).toContain("review.contact_placeholder");
    expect(node(graph, "family").severity).toBeNull();
  });

  it("names the settings that are edited on a node", () => {
    expect(node(graph, "study").settings).toEqual(["design.claim", "title", "expectations", "authors[]", "contact", "narrative_model"]);
    expect(node(graph, "card:self_referential_probe").settings).toEqual(
      ["cards.self_referential_probe.items", "cards.self_referential_probe.k", "cards[].card_id", "judge.model"]);
    expect(node(graph, `deck:${LANE}`).settings).toEqual(["roster.seeds"]);
    expect(node(graph, "family").settings).toEqual(["design.confirmatory", "design.alpha"]);
    expect(node(graph, `gate:pos:${LANE}`).settings).toEqual(["design.sensitivity_epsilon"]);
    // A setting the harness fixes is on the node as a rating, not as something to edit.
    expect(node(graph, "test:H1").settings).toEqual([]);
    expect(node(graph, "test:H1").recs.map((rec) => rec.path)).toEqual(["fixed.test.tier0_pit_residuals/pit_ks_two_sample"]);
  });

  it("tags every edge with the hypotheses it carries", () => {
    const edge = (id: string) => {
      const found = graph.edges.find((item) => item.id === id);
      if (!found) throw new Error(`the graph has no edge ${id}`);
      return found;
    };
    expect(edge("study>arm:qrng_unsupervised")).toMatchObject({ kind: "flow", tone: "quantum", dashed: false, hypotheses: ["H1", "H2", "H3"] });
    expect(edge(`study>deck:${LANE}`)).toMatchObject({ kind: "flow", tone: "control", hypotheses: ["H1", "H2", "H3"] });
    expect(edge("study>arm:qrng_supervised")).toMatchObject({ dashed: true, hypotheses: [] });
    expect(edge("arm:qrng_unsupervised>card:self_referential_probe")).toMatchObject({ kind: "flow", tone: "quantum", dashed: false, hypotheses: ["H3"] });
    expect(edge(`deck:${LANE}>card:novelty_bench`)).toMatchObject({ kind: "flow", tone: "control", dashed: true, hypotheses: [] });
    expect(edge("card:self_referential_probe>endpoint:self_referential_probe/experience_claim_rate")).toMatchObject({ kind: "measures", dashed: false, hypotheses: ["H3"] });
    expect(edge("card:self_referential_probe>endpoint:self_referential_probe/semantic_entropy")).toMatchObject({ kind: "measures", dashed: true, hypotheses: [] });
    expect(edge("endpoint:self_referential_probe/experience_claim_rate>test:H3")).toMatchObject({ kind: "compares", hypotheses: ["H3"] });
    expect(edge(`test:H3>gate:neg:${LANE}`)).toMatchObject({ kind: "gates", implied: false, hypotheses: ["H3"] });
    expect(edge(`gate:neg:${LANE}>verdict:H3`)).toMatchObject({ kind: "decides", implied: false, hypotheses: ["H3"] });
    // The rules over every test hold without a line of their own.
    expect(edge("family>test:H3")).toMatchObject({ kind: "governs", implied: true, hypotheses: ["H3"] });
    expect(edge("test:H3>gate:completeness")).toMatchObject({ kind: "gates", implied: true, hypotheses: ["H3"] });
    expect(edge("gate:completeness>verdict:H3")).toMatchObject({ kind: "decides", implied: true, hypotheses: ["H3"] });
    expect(graph.edges.filter((item) => item.implied)).toHaveLength(9);
    // No equivalence is claimed, so no verdict waits for the positive control.
    expect(graph.edges.filter((item) => item.from === `gate:pos:${LANE}`)).toEqual([]);
  });

  it("describes every node in a sentence", () => {
    expect(node(graph, "test:H3").describes).toBe("Comes from Experience-claim rate. Leads to Negative control.");
    expect(node(graph, "gate:completeness").describes).toBe("Applies to every test before its verdict.");
    expect(node(graph, "verdict:H3").describes).toBe("Comes from Negative control.");
    expect(node(graph, "family").describes).toBe("Applies to every test of the study.");
    expect(node(graph, "arm:prng_epsilon_010").describes).toBe("Switched off: the arm is listed, not run. No gate of this design depends on it.");
    expect(node(graph, "group:tier1").describes).toMatch(/^Holds Creative diversity/);
    expect(node(graph, "arm:prng_seed_7").describes).toBe("One run of the seeded controls.");
    for (const item of graph.nodes) expect(item.describes, item.id).toMatch(/\.$/);
  });

  it("reads its inputs without changing them, and gives the same graph every time", () => {
    const frozen = deepFreeze(structuredClone(config));
    const first = build(frozen);
    const again = build(structuredClone(config));
    expect(JSON.stringify(first.graph)).toBe(JSON.stringify(again.graph));
    expect(JSON.stringify(first.graph)).toBe(JSON.stringify(graph));
    // Frozen lists too: the graph holds the ratings and problems, it does not rewrite them.
    expect(() => buildStudyGraph(frozen, cards, deepFreeze([...problems]), deepFreeze(recs.map((rec) => ({ ...rec }))))).not.toThrow();
  });
});

describe("study graph: a study without a design block", () => {
  const { graph, problems } = build(fixtureCase("study1_legacy").config);

  it("is well formed", () => expectWellFormed(graph, "legacy"));

  it("invents no test, gate or verdict", () => {
    expect(graph.legacy).toBe(true);
    expect(graph.nodes.filter((item) => item.kind === "test" || item.kind === "verdict" || item.kind === "gate")).toEqual([]);
    expect(graph.edges.filter((edge) => edge.kind === "compares" || edge.kind === "gates" || edge.kind === "decides")).toEqual([]);
    expect(graph.nodes.every((item) => item.hypotheses.length === 0)).toBe(true);
  });

  it("invites a design where the tests would be", () => {
    const family = node(graph, "family");
    expect(family).toMatchObject({ kind: "family", station: "tests", label: "Fixed Study 1 plan", ghost: LEGACY_BANNER, severity: "warning", settings: ["design"] });
    expect(LEGACY_BANNER).toBe("This draft uses the fixed Study 1 plan. Tests, gates and verdicts are not drawn.");
    expect(problems.map((problem) => problem.code)).toContain("design.legacy_plan");
    expect(family.problems.map((problem) => [problem.code, problem.fix?.label])).toEqual([["design.legacy_plan", "Declare a design"]]);
    expect(node(graph, "study").sub).toBe("fixed Study 1 plan");
  });

  it("still draws the arms and the evals, and a placeholder in every empty station", () => {
    expect(node(graph, "arm:qrng_unsupervised").kind).toBe("quantum_arm");
    expect(node(graph, `deck:${LANE}`).children).toHaveLength(3);
    expect(node(graph, "tier0").children).toHaveLength(3);
    for (const id of ["ghost:measures", "ghost:gates", "ghost:verdict"]) expect(node(graph, id)).toMatchObject({ kind: "ghost_stack", zoom: "story" });
    for (const tier of ["story", "design", "detail"] as const) {
      const drawn = visible(graph, NONE, tier).nodes;
      for (const station of STATIONS) expect(drawn.filter((item) => item.station === station.id).length, `${tier} ${station.id}`).toBeGreaterThan(0);
    }
    // With no outcome pinned, every eval folds: the token band and one node per tier.
    expect(ids(visible(graph, NONE, "design").nodes.filter((item) => item.station === "prompts")))
      .toEqual(["tier0", "group:tier1", "group:tier4", "ghost:evals"]);
  });
});

describe("study graph: two comparisons", () => {
  const { graph } = build(fixtureCase("two_cells").config);

  it("is well formed", () => expectWellFormed(graph, "two_cells"));

  it("has one lane per comparison, each with its own arms, tests, gates and verdicts", () => {
    expect(graph.lanes.map((lane) => [lane.key, lane.label, lane.compared])).toEqual([
      [LANE, "qr-llm · Creative sampling", true], [OTHER_LANE, "qr-llm · Temperature 1 + min-p 0.10", true],
    ]);
    const inLane = (lane: string, kind: MapNode["kind"]) => ids(graph.nodes.filter((item) => item.lane === lane && item.kind === kind));
    expect(inLane(LANE, "test")).toEqual(["test:H1", "test:H3", "test:H5"]);
    expect(inLane(OTHER_LANE, "test")).toEqual(["test:H2", "test:H4", "test:H6"]);
    expect(inLane(OTHER_LANE, "quantum_arm")).toEqual(["arm:qrng_unsupervised__qr_llm__t1_minp"]);
    expect(inLane(OTHER_LANE, "control_deck")).toEqual([`deck:${OTHER_LANE}`]);
    expect(inLane(OTHER_LANE, "gate")).toEqual([`gate:neg:${OTHER_LANE}`, `gate:pos:${OTHER_LANE}`]);
    expect(inLane(OTHER_LANE, "verdict")).toEqual(["verdict:H2", "verdict:H4", "verdict:H6"]);
    expect(node(graph, `gate:neg:${OTHER_LANE}`).sub).toBe("1 seed pair must not differ");
    expect(node(graph, "family").sub).toBe("m = 6 · α 0.05 · power 80%");
  });

  it("shares the evals between the lanes and keeps the hypotheses apart", () => {
    const probe = node(graph, "card:self_referential_probe");
    expect(probe.lane).toBeNull();
    expect(probe.hypotheses).toEqual(["H5", "H6"]);
    expect(graph.edges.find((edge) => edge.id === "arm:qrng_unsupervised>card:self_referential_probe")?.hypotheses).toEqual(["H5"]);
    expect(graph.edges.find((edge) => edge.id === "arm:qrng_unsupervised__qr_llm__t1_minp>card:self_referential_probe")?.hypotheses).toEqual(["H6"]);
    expect(graph.edges.find((edge) => edge.id === "card:self_referential_probe>endpoint:self_referential_probe/experience_claim_rate")?.hypotheses).toEqual(["H5", "H6"]);
  });

  it("follows one hypothesis through a shared eval without leaking into the other lane", () => {
    const { upstream, downstream } = lineage(graph, "test:H6");
    expect(upstream.nodes).toEqual([
      "study", "arm:qrng_unsupervised__qr_llm__t1_minp", `deck:${OTHER_LANE}`, "card:self_referential_probe",
      "endpoint:self_referential_probe/experience_claim_rate", "family",
    ]);
    expect(downstream.nodes).toEqual(["gate:completeness", `gate:neg:${OTHER_LANE}`, "verdict:H6"]);
    // The eval itself belongs to both.
    const shared = lineage(graph, "card:self_referential_probe");
    expect(shared.upstream.nodes).toEqual(expect.arrayContaining(["arm:qrng_unsupervised", "arm:qrng_unsupervised__qr_llm__t1_minp", `deck:${LANE}`, `deck:${OTHER_LANE}`]));
    expect(shared.downstream.nodes).toEqual(expect.arrayContaining(["test:H5", "test:H6", "verdict:H5", "verdict:H6"]));
    expect(shared.downstream.nodes).not.toContain("test:H1");
    // An arm leads to every eval, and to the tests of its own lane only.
    const arm = lineage(graph, "arm:qrng_unsupervised");
    expect(arm.downstream.nodes).toEqual(expect.arrayContaining(["card:novelty_bench", "test:H1", "test:H3", "test:H5", "verdict:H5", "gate:completeness"]));
    expect(arm.downstream.nodes.filter((id) => node(graph, id).lane === OTHER_LANE)).toEqual([]);
  });
});

describe("study graph: an equivalence claim with a positive control", () => {
  const { graph } = build(fixtureCase("equivalence_claim").config);

  it("is well formed", () => expectWellFormed(graph, "equivalence_claim"));

  it("links the ε arm the design names to the positive gate, and the gate to the verdicts it bears on", () => {
    expect(node(graph, "study").sub).toBe("claims a difference, or its absence within a margin");
    expect(node(graph, "arm:prng_epsilon_010")).toMatchObject({ kind: "positive_control", ghost: null, sub: "biased on purpose" });
    expect(node(graph, "arm:prng_epsilon_010").facts.design[0]).toBe("The positive control this design has to detect.");
    const gate = `gate:pos:${LANE}`;
    expect(node(graph, gate)).toMatchObject({ sub: "ε 0.10 must be detected", ghost: null, hypotheses: ["H3", "H4"] });
    expect(graph.edges.filter((edge) => edge.from === "arm:prng_epsilon_010")).toEqual([
      // In force: the arm runs and two verdicts wait for the gate. This line was pinned as dashed, which the
      // legend reads as "exploratory, switched off, or not in force".
      { id: `arm:prng_epsilon_010>${gate}`, from: "arm:prng_epsilon_010", to: gate, kind: "gates", tone: "positive", dashed: false, implied: false, hypotheses: ["H3", "H4"] },
    ]);
    // The other planned ε arm is not this design's control.
    expect(graph.edges.filter((edge) => edge.from === "arm:prng_epsilon_003")).toEqual([]);
    // Only an outcome with a margin can be found equivalent, so only its verdict waits for the control.
    expect(graph.edges.filter((edge) => edge.from === gate).map((edge) => [edge.to, edge.kind, edge.dashed])).toEqual([
      ["verdict:H3", "decides", false], ["verdict:H4", "decides", false],
    ]);
  });

  it("stamps 'equivalent' as reachable where there is a margin", () => {
    const reachable = (id: string) => {
      const ref = node(graph, id).ref;
      return ref.type === "verdict" ? ref.stamps.find((stamp) => stamp.verdict === "equivalent")?.reachable : undefined;
    };
    expect(reachable("verdict:H3")).toBe(true);
    expect(reachable("verdict:H4")).toBe(true);
    expect(reachable("verdict:H1")).toBe(false);
    expect(node(graph, "verdict:H3").sub).toBe("effect · effect within margin · equivalent · inconclusive");
  });

  it("includes the positive control in the lineage of a verdict that waits for it", () => {
    expect(lineage(graph, "verdict:H3").upstream.nodes).toEqual(expect.arrayContaining(["arm:prng_epsilon_010", `gate:pos:${LANE}`, "test:H3"]));
    expect(lineage(graph, "verdict:H1").upstream.nodes).not.toContain(`gate:pos:${LANE}`);
    expect(lineage(graph, "arm:prng_epsilon_010").downstream.nodes).toEqual([`gate:pos:${LANE}`, "verdict:H3", "verdict:H4"]);
  });

  it("dashes the way from a control that is switched off", () => {
    const off = build(fixtureCase("control_switched_off").config).graph;
    expect(node(off, "arm:prng_epsilon_003").ghost).not.toBeNull();
    expect(off.edges.find((edge) => edge.id === `gate:pos:${LANE}>verdict:H2`)).toMatchObject({ dashed: true });
    expect(node(off, `gate:pos:${LANE}`)).toMatchObject({ severity: "error", state: "invalid" });
  });
});

describe("study graph: a study with problems", () => {
  it.each(FIXTURE_CASES.map((item) => [item.name, item] as const))("%s: every problem and rating has its node", (name, item) => {
    const { graph, problems, recs } = build(item.config);
    expectWellFormed(graph, name);
    for (const problem of problems) expect(node(graph, problem.nodeId).problems, `${problem.code} on ${problem.nodeId}`).toContain(problem);
    for (const rec of recs) expect(node(graph, rec.nodeId).recs, `${rec.path} on ${rec.nodeId}`).toContain(rec);
    // The backend's own list names the same nodes.
    for (const problem of item.backendProblems) expect(nodeById(graph, problem.node_id), `${problem.code} on ${problem.node_id}`).toBeDefined();
  });

  it("keeps an outcome that cannot decide the study in view, with its problem", () => {
    const { graph } = build(fixtureCase("draft_not_eligible").config);
    const outcome = node(graph, "endpoint:unpredictabench/ks100_pass_rate");
    expect(outcome).toMatchObject({ pinned: true, severity: "error", sub: "named as deciding · cannot be tested" });
    expect(outcome.facts.story).toBe("KS-100 pass rate is named as deciding the study, but cannot be tested.");
    expect(outcome.facts.design[0]).toBe("Cannot decide a study: it is scored per prompt group, which is not computed yet.");
    expect(outcome.hypotheses).toEqual([]);
    // No test is drawn for it: the only hypothesis is the one the plan can generate.
    expect(ids(graph.nodes.filter((item) => item.kind === "test"))).toEqual(["test:H1"]);
    expect(ids(visible(graph, NONE, "design").nodes)).toContain(outcome.id);
  });

  it("shows a problem inside a folded node on the node it folds into", () => {
    const { graph, problems } = build(fixtureCase("draft_not_eligible").config);
    // The margin is declared on an outcome that does not decide the study; its eval sits in the tier 4 group.
    const hidden = "endpoint:self_referential_probe/experience_claim_rate";
    // What the setup puts on that outcome: the refused margin, and whatever else it finds wrong with it. (The
    // list was pinned to the one code before the setup also said that the margin has no outcome to go with.)
    const own = problems.filter((problem) => problem.nodeId === hidden).map((problem) => problem.code);
    expect(own).toContain("design.margin_not_allowed");
    expect(node(graph, hidden).problems.map((problem) => problem.code)).toEqual(own);
    const folded = visible(graph, NONE, "design");
    expect(ids(folded.nodes)).not.toContain(hidden);
    expect(folded.folded["group:tier4"]).toMatchObject({ severity: "error" });
    expect(folded.folded["group:tier4"]!.problems.map((problem) => problem.code)).toEqual(own);
    const open = visible(graph, new Set(["group:tier4"]), "design");
    expect(ids(open.nodes)).toContain(hidden);
    expect(open.folded["group:tier4"]).toBeUndefined();
    // Its eval is drawn now, and answers for the outcomes still folded into it.
    expect(open.folded["card:self_referential_probe"]).toMatchObject({ count: 3, severity: null });
  });

  it("draws an eval the design names without selecting it, as a placeholder", () => {
    const config = structuredClone(studyOneTemplate());
    config.cards = config.cards.filter((selection) => selection.card_id !== "self_referential_probe");
    const { graph, problems } = build(config);
    expectWellFormed(graph, "unselected");
    expect(problems.find((problem) => problem.code === "design.not_eligible")?.nodeId).toBe("endpoint:self_referential_probe/experience_claim_rate");
    expect(node(graph, "card:self_referential_probe")).toMatchObject({ sub: "not selected", ghost: "This eval is not selected, but the design names one of its outcomes." });
    expect(node(graph, "endpoint:self_referential_probe/experience_claim_rate")).toMatchObject({ pinned: true, severity: "error" });
    expect(graph.edges.filter((edge) => edge.to === "card:self_referential_probe")).toEqual([]);
  });

  it("draws an eval and an outcome the registry does not have", () => {
    const config = structuredClone(studyOneTemplate());
    config.cards.push({ card_id: "no_such_eval" });
    config.design!.confirmatory.push({ card_id: "also_missing", endpoint: "made_up" });
    const { graph, problems } = build(config);
    expectWellFormed(graph, "unknown");
    for (const problem of problems) expect(node(graph, problem.nodeId).problems).toContain(problem);
    expect(node(graph, "card:no_such_eval")).toMatchObject({ ghost: "This eval is not in the registry." });
    expect(node(graph, "endpoint:also_missing/made_up")).toMatchObject({ parent: "card:also_missing", pinned: true, severity: "error" });
    // An unknown eval stops the plan: no hypothesis is generated, so none is drawn.
    expect(graph.nodes.filter((item) => item.kind === "test")).toEqual([]);
    expect(node(graph, "ghost:verdict")).toMatchObject({ sub: "no hypothesis yet" });
  });

  it("draws placeholders for a study with no arm and no eval", () => {
    const config = structuredClone(studyOneTemplate());
    config.arms = [];
    config.cards = [];
    const { graph, problems } = build(config);
    expectWellFormed(graph, "empty");
    expect(graph.lanes).toEqual([]);
    expect(node(graph, "ghost:arms")).toMatchObject({ label: "Tick a model and preset", station: "arms" });
    expect(node(graph, "ghost:evals")).toMatchObject({ label: "Select an eval", severity: "error" });
    for (const problem of problems) expect(node(graph, problem.nodeId).problems).toContain(problem);
    expect(node(graph, "study").problems.map((problem) => problem.code)).toContain("compare.no_cell");
    for (const station of STATIONS) expect(graph.nodes.some((item) => item.station === station.id), station.id).toBe(true);
  });

  it("draws arms that belong to no comparison in a lane of their own", () => {
    const config = structuredClone(studyOneTemplate());
    config.arms.push({ id: "provider_default", model: "qr-llm", sampling: "greedy", source: "provider" });
    const { graph } = build(config);
    expectWellFormed(graph, "hand-written");
    expect(graph.lanes.map((lane) => [lane.key, lane.compared])).toEqual([[LANE, true], ["qr-llm/greedy", false]]);
    expect(node(graph, "arm:provider_default")).toMatchObject({ kind: "other_arm", lane: "qr-llm/greedy", label: "Provider's own sampling", sub: "not part of a comparison" });
    expect(graph.edges.filter((edge) => edge.from === "arm:provider_default")).toEqual([]);
    expect(nodeById(graph, "gate:neg:qr-llm/greedy")).toBeUndefined();
  });

  it("puts a problem that names no node on the study", () => {
    const config = studyOneTemplate();
    const stray = { code: "review.server_rejected", severity: "error", message: "Refused.", stepId: "review", nodeId: "nowhere" } as const;
    const graph = buildStudyGraph(config, cards, [stray], []);
    expect(node(graph, "study").problems).toEqual([stray]);
  });
});

describe("study graph: folding", () => {
  const withEvals = (count: number): WizardStudyConfig => {
    const config = structuredClone(studyOneTemplate());
    config.design = null;
    config.cards = selectableCards.filter((card) => card.tier !== 0).slice(0, count).map((card) => ({ card_id: card.id }));
    return config;
  };

  it("draws every eval on its own up to twelve, and folds them by tier beyond that", () => {
    expect(FOLD_ABOVE).toBe(12);
    const twelve = build(withEvals(12)).graph;
    expect(twelve.nodes.filter((item) => item.kind === "card_group")).toEqual([]);
    expect(twelve.nodes.filter((item) => item.kind === "card").every((item) => item.parent === null)).toBe(true);
    const thirteen = build(withEvals(13)).graph;
    const groups = thirteen.nodes.filter((item) => item.kind === "card_group");
    expect(groups.length).toBeGreaterThan(0);
    expect(thirteen.nodes.filter((item) => item.kind === "card").every((item) => item.parent?.startsWith("group:tier"))).toBe(true);
    expect(groups.flatMap((group) => group.children)).toHaveLength(13);
  });

  it("never folds an eval whose outcome decides the study", () => {
    const { graph } = build(studyOneTemplate());
    expect(node(graph, "card:self_referential_probe").parent).toBeNull();
    for (const id of ["card:tier0_pit_residuals", "card:tier0_mmd_hamming"]) expect(shownByExpansion(graph, NONE).has(id), id).toBe(true);
  });
});

describe("study graph: what is drawn", () => {
  const { graph } = build(studyOneTemplate());

  it("draws the roots and what is pinned when nothing is unfolded", () => {
    expect(ids(visible(graph, NONE, "design").nodes)).toEqual([
      "study",
      "arm:qrng_unsupervised", "arm:qrng_supervised", `deck:${LANE}`, "arm:prng_epsilon_001", "arm:prng_epsilon_003", "arm:prng_epsilon_010",
      "tier0", "card:tier0_pit_residuals", "card:tier0_mmd_hamming", "card:self_referential_probe", "group:tier1", "group:tier4", "ghost:evals",
      "endpoint:tier0_pit_residuals/pit_ks_two_sample", "endpoint:tier0_mmd_hamming/mmd_hamming", "endpoint:self_referential_probe/experience_claim_rate",
      "family", "test:H1", "test:H2", "test:H3",
      "gate:completeness", `gate:neg:${LANE}`, `gate:pos:${LANE}`,
      "verdict:H1", "verdict:H2", "verdict:H3",
    ]);
  });

  it("draws the same nodes and edges at design and detail", () => {
    for (const expanded of [NONE, new Set(expandableIds(graph))]) {
      expect(visible(graph, expanded, "detail")).toEqual(visible(graph, expanded, "design"));
    }
  });

  it("draws only the skeleton at story, joined across what it leaves out", () => {
    const story = visible(graph, NONE, "story");
    expect(ids(story.nodes)).toEqual([
      "study", "arm:qrng_unsupervised", `deck:${LANE}`,
      "tier0", "card:tier0_pit_residuals", "card:tier0_mmd_hamming", "card:self_referential_probe",
      "endpoint:tier0_pit_residuals/pit_ks_two_sample", "endpoint:tier0_mmd_hamming/mmd_hamming", "endpoint:self_referential_probe/experience_claim_rate",
      "test:H1", "test:H2", "test:H3", "verdict:H1", "verdict:H2", "verdict:H3",
    ]);
    const design = ids(visible(graph, NONE, "design").nodes);
    for (const id of ids(story.nodes)) expect(design).toContain(id);
    // The gates are not drawn, so each test reaches its verdict directly.
    expect(story.edges.filter((edge) => edge.from.startsWith("test:"))).toEqual([
      { id: "test:H1>verdict:H1", from: "test:H1", to: "verdict:H1", kind: "decides", tone: "neutral", dashed: false, implied: false, hypotheses: ["H1"] },
      { id: "test:H2>verdict:H2", from: "test:H2", to: "verdict:H2", kind: "decides", tone: "neutral", dashed: false, implied: false, hypotheses: ["H2"] },
      { id: "test:H3>verdict:H3", from: "test:H3", to: "verdict:H3", kind: "decides", tone: "neutral", dashed: false, implied: false, hypotheses: ["H3"] },
    ]);
  });

  it.each(["story", "design", "detail"] as const)("only draws edges between drawn nodes at %s", (tier) => {
    for (const expanded of [NONE, new Set([`deck:${LANE}`]), new Set(expandableIds(graph))]) {
      const drawn = visible(graph, expanded, tier);
      const shown = new Set(ids(drawn.nodes));
      expect(new Set(drawn.edges.map((edge) => edge.id)).size).toBe(drawn.edges.length);
      for (const edge of drawn.edges) {
        expect(shown.has(edge.from), edge.id).toBe(true);
        expect(shown.has(edge.to), edge.id).toBe(true);
        expect(edge.id).toBe(`${edge.from}>${edge.to}`);
      }
    }
  });

  it("opens a node's children when it is unfolded, and nothing else", () => {
    const before = ids(visible(graph, NONE, "design").nodes);
    const deck = ids(visible(graph, new Set([`deck:${LANE}`]), "design").nodes);
    expect(deck.filter((id) => !before.includes(id))).toEqual(["arm:prng_seed_1", "arm:prng_seed_7", "arm:prng_seed_42"]);
    const card = ids(visible(graph, new Set(["card:self_referential_probe"]), "design").nodes);
    expect(card.filter((id) => !before.includes(id))).toEqual([
      "endpoint:self_referential_probe/semantic_entropy", "endpoint:self_referential_probe/vendi_embed", "endpoint:self_referential_probe/told_main_effect",
    ]);
    const band = ids(visible(graph, new Set(["tier0"]), "design").nodes);
    expect(band.filter((id) => !before.includes(id))).toEqual(["card:tier0_logprob_endpoints"]);
    // A node inside a folded node stays folded, whatever is said about it.
    expect(ids(visible(graph, new Set(["card:novelty_bench"]), "design").nodes)).toEqual(before);
    const nested = ids(visible(graph, new Set(["group:tier1", "card:novelty_bench"]), "design").nodes);
    expect(nested).toEqual(expect.arrayContaining(["card:novelty_bench", "endpoint:novelty_bench/distinct_k", "endpoint:novelty_bench/utility_k"]));
  });

  it("ends an edge to a folded node on the node it folds into", () => {
    const folded = visible(graph, NONE, "design");
    expect(folded.edges.find((edge) => edge.id === "arm:qrng_unsupervised>group:tier1")).toMatchObject({ kind: "flow", dashed: true, hypotheses: [] });
    expect(folded.edges.filter((edge) => edge.to === "group:tier1")).toHaveLength(2);
    expect(folded.edges.some((edge) => edge.to === "card:novelty_bench")).toBe(false);
    const open = visible(graph, new Set(["group:tier1"]), "design");
    expect(open.edges.some((edge) => edge.to === "group:tier1")).toBe(false);
    expect(open.edges.filter((edge) => edge.to === "card:novelty_bench").map((edge) => edge.from)).toEqual(["arm:qrng_unsupervised", `deck:${LANE}`]);
    // The graph's own edges are left as they were.
    expect(graph.edges.some((edge) => edge.to === "group:tier1")).toBe(false);
  });

  it("counts what is folded into a node", () => {
    const folded = visible(graph, NONE, "design").folded;
    expect(folded[`deck:${LANE}`]).toMatchObject({ count: 3, state: null, severity: null });
    expect(folded["group:tier1"]).toMatchObject({ state: "recommended", severity: null });
    expect(folded["study"]).toBeUndefined();
    expect(visible(graph, new Set([`deck:${LANE}`]), "design").folded[`deck:${LANE}`]).toBeUndefined();
  });

  it("lets the invitation to open an eval give way to the first outcome", () => {
    const legacy = build(fixtureCase("fallback_first_eligible").config).graph;
    expect(ids(visible(legacy, NONE, "design").nodes)).toContain("ghost:measures");
    const eval0 = legacy.nodes.find((item) => item.kind === "card" && item.expandable)!;
    const open = ids(visible(legacy, new Set([eval0.id]), "design").nodes);
    expect(open).not.toContain("ghost:measures");
    expect(open.some((id) => id.startsWith("endpoint:"))).toBe(true);
  });
});

describe("study graph: lineage", () => {
  const { graph } = build(studyOneTemplate());

  it("follows a test up to the question and down to its verdict", () => {
    expect(lineage(graph, "test:H3")).toEqual({
      upstream: {
        nodes: ["study", "arm:qrng_unsupervised", `deck:${LANE}`, "card:self_referential_probe", "endpoint:self_referential_probe/experience_claim_rate", "family"],
        edges: [
          "study>arm:qrng_unsupervised", `study>deck:${LANE}`, "arm:qrng_unsupervised>card:self_referential_probe", `deck:${LANE}>card:self_referential_probe`,
          "card:self_referential_probe>endpoint:self_referential_probe/experience_claim_rate", "endpoint:self_referential_probe/experience_claim_rate>test:H3",
          "family>test:H3",
        ],
      },
      downstream: {
        nodes: ["gate:completeness", `gate:neg:${LANE}`, "verdict:H3"],
        edges: [`test:H3>gate:neg:${LANE}`, `gate:neg:${LANE}>verdict:H3`, "test:H3>gate:completeness", "gate:completeness>verdict:H3"],
      },
    });
  });

  it("keeps the hypotheses of one lane apart at a shared gate", () => {
    const gate = lineage(graph, "gate:completeness");
    expect(gate.upstream.nodes).toEqual(expect.arrayContaining(["test:H1", "test:H2", "test:H3", "study"]));
    expect(gate.downstream.nodes).toEqual(["verdict:H1", "verdict:H2", "verdict:H3"]);
    expect(lineage(graph, "verdict:H1").upstream.nodes).not.toContain("test:H3");
    expect(lineage(graph, "verdict:H1").upstream.nodes).not.toContain("card:self_referential_probe");
  });

  it("puts everything downstream of the question, and nothing upstream", () => {
    const study = lineage(graph, "study");
    expect(study.upstream).toEqual({ nodes: [], edges: [] });
    expect(study.downstream.nodes).toEqual(expect.arrayContaining(["arm:qrng_supervised", "card:novelty_bench", "test:H1", "verdict:H3"]));
    // Everything but the family, which nothing leads to, and its hold over the tests.
    expect(study.downstream.nodes).not.toContain("family");
    expect(study.downstream.edges).toHaveLength(graph.edges.filter((edge) => edge.from !== "family").length);
  });

  it("gives an exploratory eval its arms and its outcomes, and no test", () => {
    const novelty = lineage(graph, "card:novelty_bench");
    expect(novelty.upstream.nodes).toEqual(["study", "arm:qrng_unsupervised", `deck:${LANE}`]);
    expect(novelty.downstream.nodes).toEqual(["endpoint:novelty_bench/distinct_k", "endpoint:novelty_bench/utility_k", "endpoint:novelty_bench/vendi_embed"]);
  });

  it("puts every test under the Holm family", () => {
    const family = lineage(graph, "family");
    expect(family.upstream).toEqual({ nodes: [], edges: [] });
    expect(family.downstream.nodes).toEqual(["test:H1", "test:H2", "test:H3", "gate:completeness", `gate:neg:${LANE}`, "verdict:H1", "verdict:H2", "verdict:H3"]);
    for (const id of ["test:H1", "verdict:H2", "gate:completeness"]) expect(lineage(graph, id).upstream.nodes, id).toContain("family");
    expect(family.downstream.edges).toEqual(expect.arrayContaining(["family>test:H1", "family>test:H2", "family>test:H3"]));
    // It stands beside what leads to a test, not after it.
    for (const id of ["study", "card:self_referential_probe", "arm:qrng_unsupervised", "card:novelty_bench"]) {
      const { upstream, downstream } = lineage(graph, id);
      expect([...upstream.nodes, ...downstream.nodes], id).not.toContain("family");
    }
  });

  it("walks a node that folds others together with them", () => {
    const band = lineage(graph, "tier0");
    expect(band.upstream.nodes).toEqual(expect.arrayContaining(["study", "arm:qrng_unsupervised", `deck:${LANE}`]));
    expect(band.downstream.nodes).toEqual(expect.arrayContaining(["test:H1", "test:H2", "verdict:H1", "verdict:H2"]));
    expect(band.downstream.nodes).not.toContain("card:tier0_pit_residuals");
    expect(band.downstream.nodes).not.toContain("test:H3");
  });

  it("works on what is drawn, whose edges end on drawn nodes", () => {
    const drawn = visible(graph, NONE, "design");
    const group = lineage(drawn, "group:tier1");
    expect(group.upstream.nodes).toEqual(["study", "arm:qrng_unsupervised", `deck:${LANE}`]);
    expect(group.upstream.edges).toEqual(["study>arm:qrng_unsupervised", `study>deck:${LANE}`, "arm:qrng_unsupervised>group:tier1", `deck:${LANE}>group:tier1`]);
    const story = lineage(visible(graph, NONE, "story"), "verdict:H3");
    expect(story.upstream.nodes).toEqual([
      "study", "arm:qrng_unsupervised", `deck:${LANE}`, "card:self_referential_probe", "endpoint:self_referential_probe/experience_claim_rate", "test:H3",
    ]);
    const edgeIds = new Set(drawn.edges.map((edge) => edge.id));
    for (const id of [...group.upstream.edges, ...group.downstream.edges]) expect(edgeIds.has(id), id).toBe(true);
  });

  it("is empty for a node the graph does not have", () => {
    expect(lineage(graph, "card:nope")).toEqual({ upstream: { nodes: [], edges: [] }, downstream: { nodes: [], edges: [] } });
  });
});

describe("study graph: the link from a biased arm to its positive-control gate", () => {
  const link = (name: string, arm: string) => {
    const { graph } = build(fixtureCase(name).config);
    return graph.edges.find((edge) => edge.from === arm && edge.to === `gate:pos:${LANE}`);
  };

  it("is a solid line while the control is in force", () => {
    expect(link("equivalence_claim", "arm:prng_epsilon_010")).toMatchObject({ dashed: false, tone: "positive", hypotheses: ["H3", "H4"] });
  });

  it("is dashed while the arm is switched off", () => {
    expect(link("control_switched_off", "arm:prng_epsilon_003")).toMatchObject({ dashed: true });
  });

  it("is dashed while no verdict waits for the gate", () => {
    // The arm runs, but the study claims a difference only: 'equivalent' is not on offer, so the gate decides nothing.
    const base = fixtureCase("equivalence_claim").config;
    const { graph } = build({ ...base, design: { ...base.design!, claim: "difference" } });
    const edge = graph.edges.find((item) => item.from === "arm:prng_epsilon_010");
    expect(edge).toMatchObject({ to: `gate:pos:${LANE}`, dashed: true, hypotheses: [] });
  });
});

describe("study graph: what the story zoom leaves out", () => {
  const codes = (problems: readonly { code: string }[]) => problems.map((problem) => problem.code);
  /** Every problem the story zoom shows: on a drawn node, in what a drawn node answers for, or at a station. */
  const shownAtStory = (graph: StudyGraph, expanded: ReadonlySet<string> = NONE) => {
    const story = visible(graph, expanded, "story");
    return [
      ...story.nodes.flatMap((item) => item.problems),
      ...Object.values(story.folded).flatMap((item) => item.problems),
      ...Object.values(story.left).flatMap((item) => item?.problems ?? []),
    ];
  };

  it("hands a problem on the Holm family to the tests station", () => {
    const { graph } = build(fixtureCase("too_many_tests").config);
    const story = visible(graph, NONE, "story");
    expect(ids(story.nodes)).not.toContain("family");
    expect(story.left.tests).toMatchObject({ severity: "error", state: "invalid" });
    expect(codes(story.left.tests!.problems)).toEqual(["design.too_many_tests"]);
  });

  it("hands the problems of the positive-control gates to the gates station", () => {
    const off = visible(build(fixtureCase("control_switched_off").config).graph, NONE, "story");
    expect(off.left.gates).toMatchObject({ severity: "error" });
    expect(codes(off.left.gates!.problems)).toEqual(["design.epsilon_off"]);
    const draft = visible(build(fixtureCase("draft_epsilon_and_seeds").config).graph, NONE, "story");
    expect(codes(draft.left.gates!.problems)).toEqual(["design.epsilon_off", "design.epsilon_seed_shared", "design.epsilon_missing"]);
  });

  it("hands a problem on an eval that decides nothing to the eval's station, with what is folded into it", () => {
    const base = studyOneTemplate();
    const config: WizardStudyConfig = { ...base, cards: base.cards.map((selection) => (selection.card_id === "novelty_bench" ? { ...selection, overrides: { items: 5000 } } : selection)) };
    const { graph } = build(config);
    expect(codes(node(graph, "card:novelty_bench").problems)).toEqual(["measure.out_of_bounds"]);
    // Folded into its tier, which the story leaves out.
    expect(codes(visible(graph, NONE, "story").left.prompts!.problems)).toContain("measure.out_of_bounds");
    // Unfolded, it is itself left out.
    expect(codes(visible(graph, new Set(["group:tier1"]), "story").left.prompts!.problems)).toContain("measure.out_of_bounds");
  });

  it("hands what the story leaves out under a drawn node to that node", () => {
    const { graph } = build(fixtureCase("draft_not_eligible").config);
    const story = visible(graph, new Set(expandableIds(graph)), "story");
    // An outcome with a refused margin, on an eval that decides nothing: neither is drawn, and both are shown
    // at full density, so nothing used to answer for them.
    expect(codes(shownAtStory(graph, new Set(expandableIds(graph))))).toContain("design.margin_not_allowed");
    for (const [id, carried] of Object.entries(story.folded)) {
      expect(ids(story.nodes), id).toContain(id);
      expect(carried.count, id).toBeGreaterThan(0);
    }
  });

  it.each(FIXTURE_CASES.map((item) => [item.name, item] as const))("%s: shows every problem at the story zoom, each once, folded or unfolded", (_name, item) => {
    const { graph, problems } = build(item.config);
    const sorted = (list: readonly { code: string; message: string }[]) => list.map((problem) => `${problem.code}|${problem.message}`).sort();
    for (const expanded of [NONE, new Set(expandableIds(graph))]) {
      expect(sorted(shownAtStory(graph, expanded))).toEqual(sorted(problems));
    }
  });

  it("leaves nothing out at the design and detail zooms", () => {
    const { graph } = build(fixtureCase("too_many_tests").config);
    expect(visible(graph, NONE, "design").left).toEqual({});
    expect(visible(graph, new Set(expandableIds(graph)), "detail").left).toEqual({});
  });
});

describe("study graph: a study on the fixed Study 1 plan", () => {
  const { graph } = build(fixtureCase("study1_legacy").config);
  const said = graph.nodes.filter((item) => item.station === "prompts" || item.station === "measures")
    .flatMap((item) => [item.sub, item.facts.story, ...item.facts.design, ...item.facts.detail]);

  it("calls no eval and no outcome exploratory: the fixed plan has primary outcomes the map does not draw", () => {
    expect(graph.legacy).toBe(true);
    for (const text of said) {
      expect(text).not.toMatch(/exploratory|for exploration|without a verdict|decides the study|[Nn]one of them decides/);
    }
  });

  it("says instead that the fixed plan states how each is analysed", () => {
    expect(node(graph, "card:tier0_pit_residuals").facts.story).toBe("Analysed as the fixed Study 1 plan states. The map does not draw the tests of that plan.");
    expect(node(graph, "card:unpredictabench").facts.story).toBe("Analysed as the fixed Study 1 plan states. The map does not draw the tests of that plan.");
    const outcome = node(graph, "endpoint:tier0_pit_residuals/pit_ks_two_sample");
    expect(outcome.sub).toBe("headline outcome");
    expect(outcome.facts.story).toBe("Token sampling residuals (KS) is analysed as the fixed Study 1 plan states.");
    // No test is named for it: the test a design would register is not the fixed plan's to state.
    expect(outcome.facts.design).toEqual([]);
    expect(node(graph, "tier0").facts.design).toEqual(["The fixed Study 1 plan states which of them it tests."]);
    expect(node(graph, "group:tier4").facts).toMatchObject({ story: "11 evals of tier 4.", design: ["The fixed Study 1 plan states how their outcomes are analysed."] });
    expect(node(graph, "ghost:measures").facts.story).toBe("The outcomes are folded into their evals.");
  });

  it("still calls them exploratory in a designed study, where it is true", () => {
    const designed = build(studyOneTemplate()).graph;
    expect(node(designed, "card:novelty_bench").facts.story).toBe("Measured for exploration: no outcome of this eval decides the study.");
    expect(node(designed, "group:tier1").facts.design).toEqual(["No outcome of these evals decides the study."]);
  });

  it("says that the fixed plan tests the observed quantum arm, which a designed study does not", () => {
    // The fixed plan registers observed against unobserved in a Holm family of its own; only a designed study leaves it exploratory.
    const observed = node(graph, "arm:qrng_supervised");
    expect(observed).toMatchObject({ kind: "supervised_arm", label: "Observed quantum", sub: "tested against unobserved quantum" });
    expect(observed.facts.story).toBe("An observed quantum arm. The fixed Study 1 plan tests it against the unobserved quantum arm on the primary outcomes of that plan, corrected with Holm in a family of their own.");
    expect(observed.facts.design).toEqual(["In qr-llm · Creative sampling.", "Compared with the unobserved quantum arm."]);
    for (const text of [observed.sub, observed.facts.story, ...observed.facts.design]) expect(text).not.toMatch(/exploratory|verdict/);

    const designed = node(build(studyOneTemplate()).graph, "arm:qrng_supervised");
    expect(designed.sub).toBe("exploratory, no verdict");
    expect(designed.facts.story).toBe("An observed quantum arm. Its comparison is exploratory and gets no verdict.");
  });

  it("says what the fixed plan does with an observed arm that has no unobserved quantum arm beside it", () => {
    const base = fixtureCase("study1_legacy").config;
    const without = build({ ...base, arms: base.arms.filter((arm) => arm.id !== "qrng_unsupervised") }).graph;
    expect(node(without, "arm:qrng_supervised")).toMatchObject({ sub: "tested against the seeded controls" });
    expect(node(without, "arm:qrng_supervised").facts.design).toEqual(["In qr-llm · Creative sampling.", "Compared with the seeded controls, pooled."]);
    const off = build({ ...base, arms: base.arms.map((arm) => (arm.id === "qrng_supervised" ? { ...arm, availability: "unavailable" as const } : arm)) }).graph;
    expect(node(off, "arm:qrng_supervised")).toMatchObject({ sub: "switched off, not compared", ghost: "Switched off: the arm is listed, not run." });
  });
});

describe("study graph: what is said of tokens and of the sampling envelope", () => {
  const config = studyOneTemplate();
  const { graph } = build(config);

  it("does not say that a pooled test reads every generated token", () => {
    // The quantum arm does draw fresh randomness for every token; what is measured is not every token.
    for (const item of graph.nodes.filter((entry) => entry.station !== "arms")) {
      for (const text of [item.label, item.sub, item.facts.story, ...item.facts.design, ...item.facts.detail]) {
        expect(text, item.id).not.toMatch(/every (generated )?token\b/i);
      }
    }
    expect(node(graph, "tier0").facts.story).toBe(TOKEN_AUDITS);
  });

  it("says which tokens a pooled test runs on, and what its completeness is counted on", () => {
    const ks = node(graph, "test:H1");
    expect(ks.facts.design).toContain("Runs on the tokens the study generates, pooled per side.");
    expect(ks.facts.detail).toContain(POOLED_TOKENS);
    expect(POOLED_TOKENS).toBe("The tokens of every eval that generates are pooled per side. Of an eval with told cells, only the told-nothing cell is used.");
    const hypothesis = ks.ref.type === "test" ? ks.ref.hypothesis : null;
    expect(ks.facts.detail).toContain(`${hypothesis!.planned_units.toLocaleString("en-US")} generations per arm are planned on the evals whose prompt list is fixed before the run. Completeness is counted against that number.`);
    expect(node(graph, "tier0").facts.detail).toContain(POOLED_TOKENS);
    // A paired test says nothing of pooled tokens.
    expect(node(graph, "test:H3").facts.detail).not.toContain(POOLED_TOKENS);
  });

  it("says of each arm what holds of its own envelope", () => {
    const last = (id: string, from: StudyGraph = graph) => node(from, id).facts.detail.at(-1);
    expect(last("arm:qrng_unsupervised")).toBe("Its sampling envelope is the one every compared arm has: amplifier zscore_mean, calibration 0, prefetch off.");
    expect(last("arm:prng_seed_7")).toBe("Its sampling envelope is the one every compared arm has: amplifier zscore_mean, calibration 0, prefetch off.");
    expect(last("arm:prng_epsilon_010")).toBe("It has the sampling envelope of the compared arms (amplifier zscore_mean, calibration 0, prefetch off), with one change: the amplifier's reference mean is lowered by ε standard errors. That is the bias.");
    for (const item of graph.nodes) expect(item.facts.detail.join(" "), item.id).not.toContain("same in every arm");

    // An arm a provider samples itself, and a greedy arm, are outside the envelope: the pinned setting is not put on them.
    const outside: WizardStudyConfig = {
      ...config,
      arms: [
        ...config.arms,
        { ...config.arms[0]!, id: "provider_arm", source: "provider", study_seed: null },
        { ...config.arms.find((arm) => arm.source === "prng_seeded")!, id: "greedy_arm", sampling: "greedy" },
      ],
    };
    const built = build(outside);
    // Whether or not the list of settings names the envelope for such an arm, the map does not put it on its node.
    const withEnvelope = [...built.recs, ...["arm:provider_arm", "arm:greedy_arm"].map((nodeId) => ({ ...built.recs.find((rec) => rec.path === "fixed.envelope")!, nodeId }))];
    const forced = buildStudyGraph(outside, cards, built.problems, withEnvelope);
    for (const id of ["arm:provider_arm", "arm:greedy_arm"]) expect(node(forced, id).recs.map((rec) => rec.path), id).not.toContain("fixed.envelope");
    expect(node(forced, "arm:qrng_unsupervised").recs.map((rec) => rec.path)).toContain("fixed.envelope");
    expect(last("arm:provider_arm", built.graph)).toBe("The provider samples this arm itself: the sampling envelope the harness pins does not apply to it.");
    expect(last("arm:greedy_arm", built.graph)).toBe("A greedy arm takes the most likely token and draws no randomness: the sampling envelope does not apply to it.");
    for (const id of ["arm:provider_arm", "arm:greedy_arm"]) expect(node(built.graph, id).recs.map((rec) => rec.path), id).not.toContain("fixed.envelope");
    expect(node(built.graph, "arm:qrng_unsupervised").recs.map((rec) => rec.path)).toContain("fixed.envelope");
  });
});

describe("study graph: what the design says of sample sizes and controls", () => {
  it("puts the repetitions of an eval on its node with the state the setup gives them", () => {
    for (const name of ["study1_designed", "fewer_repetitions", "control_below_floor"]) {
      const config = fixtureCase(name).config;
      const { graph } = build(config);
      const expected = rate(config, cards, problemsOf(config, cards)).filter((rec) => rec.nodeId === "card:self_referential_probe");
      expect(node(graph, "card:self_referential_probe").recs, name).toEqual(expected);
      expect(expected.map((rec) => rec.path), name).toEqual(["cards.self_referential_probe.items", "cards.self_referential_probe.k"]);
    }
    const short = build(fixtureCase("fewer_repetitions").config).graph;
    const probe = node(short, "card:self_referential_probe");
    const k = probe.recs.find((rec) => rec.path === "cards.self_referential_probe.k")!;
    expect(k.value).toBe("k = 10");
    expect(k.state).not.toBe("recommended");
    // The eval says the repetitions that would make its prompts enough: the number the recommendation names.
    expect(Number(k.recommended)).toBeGreaterThan(10);
    expect(probe.facts.design).toContain(`Experience-claim rate: with k = ${k.recommended}, the 30 prompts of the eval are enough.`);
    // With the recommended repetitions there is nothing to add.
    const template = node(build(studyOneTemplate()).graph, "card:self_referential_probe");
    expect(template.recs.find((rec) => rec.path === "cards.self_referential_probe.k")).toMatchObject({ value: "k = 24", state: "recommended" });
    expect(template.facts.design.join(" ")).not.toContain("with k =");
  });

  it("says on the test and on the negative control from how many prompts the control can fail", () => {
    const { graph } = build(studyOneTemplate());
    const test = node(graph, "test:H3");
    const floor = test.ref.type === "test" ? test.ref.hypothesis.sample_size.control_floor : null;
    expect(floor).toBe(10);
    expect(test.facts.detail).toContain("The seed-against-seed control can fail from 10 prompts on; the study plans 30.");
    const gate = node(graph, `gate:neg:${LANE}`);
    expect(gate.sub).toBe("3 seed pairs must not differ");
    expect(gate.facts.design).toEqual(["If a pair differs, one is missing, or the control could not have failed, no conclusion is drawn in this comparison."]);
    expect(gate.facts.detail).toContain("H3: the control can fail from 10 prompts on; the study plans 30.");
    // A pooled test has no such floor.
    expect(node(graph, "test:H1").facts.detail.join(" ")).not.toContain("seed-against-seed");
  });

  it("shows a control that is not certain to be able to fail on the eval, the test, the gate and the verdict", () => {
    const { graph, problems } = build(fixtureCase("control_below_floor").config);
    const sentence = "With 8 prompts it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed: it is asked for at least 10. H3 could only come out invalid.";
    expect(problems.map((problem) => [problem.code, problem.nodeId])).toContainEqual(["design.control_below_floor", "card:self_referential_probe"]);
    expect(node(graph, "card:self_referential_probe")).toMatchObject({ severity: "error", state: "insufficient" });
    expect(node(graph, "test:H3").facts.detail).toContain(sentence);
    const gate = node(graph, `gate:neg:${LANE}`);
    expect(gate.sub).toBe("not counted as passed for H3");
    // Below the floor a pair that differs as much as it can often still fails (cards/power.py), so no text says it cannot.
    expect(JSON.stringify(graph)).not.toMatch(/cannot fail|able to fail/);
    expect(gate.facts.design).toContain(sentence);
    const verdict = node(graph, "verdict:H3");
    expect(verdict.sub).toBe("no finding within reach");
    expect(verdict.facts.story).toBe("H3 can only end as invalid or unavailable as configured.");
    const stamps = verdict.ref.type === "verdict" ? verdict.ref.stamps : [];
    expect(stamps.filter((stamp) => stamp.why === "control_cannot_fail").map((stamp) => stamp.verdict)).toEqual(["effect", "effect_within_margin", "inconclusive"]);
    expect(stamps.find((stamp) => stamp.verdict === "effect")!.reason).toBe("Fewer prompts than the seed-against-seed control is asked for, so the hypothesis is invalid.");
    expect(stamps.filter((stamp) => stamp.reachable).map((stamp) => stamp.verdict)).toEqual(["invalid", "unavailable"]);
    // The rule that makes it invalid is the third row of the decision matrix.
    expect(stamps.find((stamp) => stamp.verdict === "invalid")!.rules).toContain("A seed-vs-seed control of the cell is missing, differs after Holm correction, or could not have failed.");
  });

  it("says on the positive-control gate how far the biased arm has to move each outcome, as the warning does", () => {
    const { graph, problems } = build(fixtureCase("equivalence_claim").config);
    const gate = node(graph, `gate:pos:${LANE}`);
    const warnings = problems.filter((problem) => problem.code === "design.control_not_rated");
    expect(gate.problems).toEqual(warnings);
    expect(warnings).toHaveLength(2);
    const shifts = gate.facts.detail.filter((fact) => /has to move/.test(fact));
    expect(shifts).toHaveLength(2);
    // The same number in the fact and in the problem the Design step shows.
    for (const [index, fact] of shifts.entries()) {
      const shift = /by at least (\d\.\d{3}) to be detected/.exec(fact)![1]!;
      expect(warnings[index]!.message).toContain(`move it by at least ${shift}.`);
    }
    expect(gate.facts.detail.at(-1)).toBe("Whether a sampler bias of ε = 0.10 moves an outcome that far is not known before the run.");
    expect(gate.facts.design).toEqual(["'Equivalent' is only returned on an outcome if this arm is detected on that outcome."]);
    // Where 'equivalent' is not on offer, nothing is said of a shift.
    expect(node(build(studyOneTemplate()).graph, `gate:pos:${LANE}`).facts.detail).toEqual(["A deliberately biased pseudo-random arm, on a seed of its own."]);
  });

  it("draws the new problems of the design on the nodes the setup names", () => {
    const equivalence = build(fixtureCase("equivalence_claim").config).graph;
    expect(node(equivalence, "family").problems.map((problem) => problem.code)).toContain("design.study_absence_unreachable");
    expect(node(equivalence, `gate:pos:${LANE}`).problems.map((problem) => problem.code)).toContain("design.control_not_rated");
    const floor = build(fixtureCase("below_floor").config).graph;
    expect(node(floor, "card:self_referential_probe").problems.map((problem) => problem.code)).toEqual(["design.below_floor", "design.control_below_floor"]);
  });
});

describe("study graph: copy that mirrors the backend", () => {
  it("states the decision matrix as the backend does", () => {
    expect(VERDICT_RULES).toEqual(fixture.verdict_rules.map((rule) => ({ number: rule.number, verdict: rule.verdict, condition: rule.condition })));
  });

  it("gives the backend's reason for every verdict out of reach", () => {
    const backend = fixture.inconclusive_reason_text as Record<string, string>;
    for (const [reason, text] of Object.entries(UNREACHABLE_TEXT)) {
      if (reason in backend) expect(text, reason).toBe(backend[reason]);
    }
    // Every reason the fixture's studies run into has words.
    for (const item of fixture.cases) {
      for (const verdicts of Object.values(item.reachable_verdicts as Record<string, Record<string, string | null>>)) {
        for (const why of Object.values(verdicts)) {
          if (why !== null) expect(UNREACHABLE_TEXT[why], why).toBeDefined();
        }
      }
    }
    expect(Object.keys(VERDICT_LABELS).sort()).toEqual([...VERDICT_ORDER].sort());
  });
});
