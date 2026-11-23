import { studyOneTemplate } from "../../lib/config-store";
import { visible } from "../../lib/map/graph";
import { layoutGraph } from "../../lib/map/layout";
import { build, fixtureCase } from "../../lib/map/test-fixtures";
import { DEFAULT_SIZE, zoomTier } from "../../lib/map/viewport";
import { REC_STATES } from "../../lib/recommendations";
import { edgeInk, nodeInk } from "./ink";
import {
  DESIGN_CHIPS, chipsFor, currentNode, homeView, listed, nearestDrawn, newMapMemory, nodeName, problemCounts, problemTarget, problemWords, settingOf,
  standingProblems, standingState, storyOf, zoomToBox,
} from "./model";

const NONE: ReadonlySet<string> = new Set();
const template = () => build(studyOneTemplate());
const node = (id: string) => {
  const found = template().graph.nodes.find((item) => item.id === id);
  if (!found) throw new Error(`no node ${id}`);
  return found;
};

describe("what a node shows", () => {
  it("shows no setting in the story, the few that matter in the design and all of them in the detail", () => {
    const { graph } = template();
    for (const item of graph.nodes) {
      expect(chipsFor(item, "story")).toEqual([]);
      const design = chipsFor(item, "design");
      expect(design.length).toBeLessThanOrEqual(DESIGN_CHIPS);
      expect(design.every((rec) => rec.state !== "locked")).toBe(true);
      expect(chipsFor(item, "detail").map((rec) => rec.path).sort()).toEqual(item.recs.map((rec) => rec.path).sort());
    }
  });

  it("names a node with how it stands, in the words of the legend", () => {
    const study = node("study");
    const name = nodeName(study, study.state, study.problems);
    expect(name.startsWith(study.label)).toBe(true);
    expect(name).toContain(REC_STATES[study.state!].legend);
    expect(name).toContain(problemWords(study.problems));
    expect(problemWords(study.problems)).toMatch(/blocks? the launch/);
    expect(problemWords([])).toBe("");
    // A placeholder says that it is one.
    const ghost = template().graph.nodes.find((item) => item.ghost !== null)!;
    expect(nodeName(ghost, null, [])).toContain("Placeholder");
  });

  it("answers for what is folded into a node", () => {
    const { graph } = build(fixtureCase("too_many_tests").config);
    const drawn = visible(graph, NONE, "design");
    for (const item of drawn.nodes) {
      const folded = drawn.folded[item.id];
      const state = standingState(item, folded);
      if (item.state !== null || (folded?.state ?? null) !== null) expect(state).not.toBeNull();
      expect(standingProblems(item, folded).length).toBe(item.problems.length + (folded?.problems.length ?? 0));
    }
  });

  it("opens only settings that have a panel, and sends a problem to its setting or to the list", () => {
    const { graph, problems } = template();
    for (const item of graph.nodes) {
      const setting = settingOf(item);
      if (setting !== null) expect(item.settings).toContain(setting);
    }
    // What the harness fixes has no panel, so a node with only that has nothing to open.
    expect(settingOf({ ...node("study"), settings: ["fixed.power"] })).toBeNull();
    for (const problem of problems) {
      const target = problemTarget(problem);
      expect("path" in target || target.problemsOnly === true).toBe(true);
    }
  });
});

describe("problems as counts", () => {
  it("counts what blocks the launch and what warns, as a node's badge marks them", () => {
    const error = { code: "a", severity: "error" as const, message: "A.", stepId: "design" as const, nodeId: "study" };
    const warning = { ...error, code: "b", severity: "warning" as const };
    expect(problemCounts([])).toBe("");
    expect(problemCounts([error])).toBe("✕ 1");
    expect(problemCounts([warning, warning])).toBe("! 2");
    expect(problemCounts([warning, error, warning, warning])).toBe("✕ 1  ! 3");
    expect(problemWords([warning, error, warning, warning])).toBe("1 problem blocks the launch, 3 warnings");
  });
});

describe("the caption", () => {
  it("says the study in four sentences read off the graph", () => {
    const story = storyOf(template().graph);
    expect(story.question).toBe("Does quantum sampling differ from seeded pseudo-random sampling?");
    expect(story.arms).toMatch(/^One comparison, .+: the quantum arm against the seeded pseudo-random controls\.$/);
    expect(story.outcomes).toMatch(/^Decided on .+ and .+\.$/);
    expect(story.verdicts).toContain("Verdicts within reach: ");
    // Study 1 claims a difference only, so "equivalent" is out of reach and the caption says so.
    expect(story.verdicts).toContain("Out of reach as configured: equivalent.");
  });

  it("says that no finding is within reach where every test could only come out invalid", () => {
    // Three prompts are too few for the seed-against-seed control to fail, on the only outcome that decides the study.
    const base = studyOneTemplate();
    const only = {
      ...base,
      cards: base.cards.map((selection) => (selection.card_id === "self_referential_probe" ? { ...selection, overrides: { ...selection.overrides, items: 3 } } : selection)),
      design: { ...base.design!, confirmatory: [{ card_id: "self_referential_probe", endpoint: "experience_claim_rate" }] },
    };
    const { graph } = build(only);
    const stamps = graph.nodes.flatMap((item) => (item.ref.type === "verdict" ? item.ref.stamps : []));
    expect(stamps.filter((stamp) => stamp.reachable).map((stamp) => stamp.verdict)).toEqual(["invalid", "unavailable"]);
    expect(storyOf(graph).verdicts).toBe("No finding is within reach as configured: every test could only come out invalid or unavailable.");
    // With the other outcomes in, their verdicts are within reach and the caption lists them.
    expect(storyOf(build(fixtureCase("control_below_floor").config).graph).verdicts).toContain("Verdicts within reach: ");
  });

  it("promises no test for a study without a design", () => {
    const legacy = storyOf(build({ ...studyOneTemplate(), design: null }).graph);
    expect(legacy.outcomes).toBe("The outcomes are those of the fixed Study 1 plan.");
    expect(legacy.verdicts).toBe("Tests, gates and verdicts are drawn once a design is declared.");
  });

  it("lists as prose", () => {
    expect(listed([])).toBe("");
    expect(listed(["a"])).toBe("a");
    expect(listed(["a", "b"])).toBe("a and b");
    expect(listed(["a", "b", "c"], "or")).toBe("a, b or c");
  });
});

describe("the camera's places", () => {
  it("starts at the question, at a zoom at which the design can be read", () => {
    const layout = layoutGraph(template().graph);
    for (const size of [DEFAULT_SIZE, { w: 1025, h: 720 }, { w: 700, h: 500 }]) {
      const view = homeView(layout.bounds, size);
      expect(zoomTier(view.k)).toBe("design");
      // The left edge of the world is just inside the viewport.
      expect(view.x + layout.bounds.x * view.k).toBeCloseTo(24);
    }
  });

  it("centres a graph small enough to be seen whole", () => {
    const view = homeView({ x: 0, y: 0, w: 400, h: 200 }, { w: 1400, h: 600 });
    expect(view).toEqual({ x: 500, y: 200, k: 1 });
  });

  it("zooms to a node large enough to read all of it", () => {
    const view = zoomToBox({ x: 640, y: 300, w: 260, h: 96 }, DEFAULT_SIZE);
    expect(zoomTier(view.k)).toBe("detail");
    expect(view.x + (640 + 130) * view.k).toBeCloseTo(DEFAULT_SIZE.w / 2);
    expect(view.y + (300 + 48) * view.k).toBeCloseTo(DEFAULT_SIZE.h / 2);
  });

  it("keeps the keyboard on a drawn node: the remembered one, its nearest drawn ancestor, or the first", () => {
    const { graph } = template();
    const order = layoutGraph(graph).order;
    expect(currentNode(graph, order, null)).toBe(order[0]);
    expect(currentNode(graph, order, "family")).toBe("family");
    // A seeded run is folded into its deck.
    expect(currentNode(graph, order, "arm:prng_seed_7")).toBe("deck:qr-llm/creative");
    expect(currentNode(graph, order, "no-such-node")).toBe(order[0]);
    expect(currentNode(graph, [], "study")).toBeNull();
  });

  it("finds the drawn node nearest to one that is no longer drawn", () => {
    const { graph } = template();
    const design = layoutGraph(graph);
    const story = layoutGraph(graph, NONE, "story");
    // A node that is still drawn is its own answer; one folded into a drawn node goes to that node.
    expect(nearestDrawn(graph, story.boxes, story.order, "test:H1", design.boxes["test:H1"])).toBe("test:H1");
    expect(nearestDrawn(graph, design.boxes, design.order, "arm:prng_seed_7", undefined)).toBe("deck:qr-llm/creative");
    // The Holm family has no holder: the nearest node by place is a test of its station.
    expect(story.boxes["family"]).toBeUndefined();
    expect(nearestDrawn(graph, story.boxes, story.order, "family", design.boxes["family"])).toBe("test:H1");
    // A gate goes to the verdict or the test beside it, not to the far end of the map.
    expect(["test:H1", "verdict:H1"]).toContain(nearestDrawn(graph, story.boxes, story.order, "gate:completeness", design.boxes["gate:completeness"]));
    // A node the graph no longer has, last seen at a place, goes to what is drawn nearest to that place.
    expect(nearestDrawn(graph, design.boxes, design.order, "group:gone", design.boxes["group:tier4"])).toBe("group:tier4");
    // Nothing known of where it was: the first node.
    expect(nearestDrawn(graph, design.boxes, design.order, "group:gone", undefined)).toBe(design.order[0]);
    expect(nearestDrawn(graph, {}, [], "study", undefined)).toBeNull();
  });

  it("starts with nothing remembered", () => {
    expect(newMapMemory()).toEqual({ view: null, expanded: [], mode: null, current: null, legendOpen: null, open: null });
  });
});

describe("ink", () => {
  it("draws every node of every kind the same way twice, with no stroke gone wrong", () => {
    const { graph } = build(fixtureCase("too_many_tests").config);
    const all = new Set(graph.nodes.map((item) => item.id));
    const layout = layoutGraph(graph, all, "detail");
    for (const item of graph.nodes) {
      const box = layout.boxes[item.id];
      if (!box) continue;
      const first = nodeInk(item, { w: box.w, h: box.h }, item.state);
      expect(first.length).toBeGreaterThan(0);
      expect(nodeInk(item, { w: box.w, h: box.h }, item.state)).toEqual(first);
      for (const stroke of first) {
        expect(stroke.d).not.toMatch(/NaN|undefined|Infinity/);
        expect(stroke.d.startsWith("M")).toBe(true);
      }
      // A box has paper under it, so a thread behind it does not show through.
      if (item.kind !== "family") expect(first.filter((stroke) => stroke.role === "fill")).toHaveLength(1);
    }
  });

  it("hatches a node in the measure of its state, and the quantum arm in its own ink", () => {
    const card = node("card:self_referential_probe");
    const size = { w: 260, h: 96 };
    expect(nodeInk(card, size, null).some((stroke) => stroke.role === "hatch")).toBe(false);
    const sparse = nodeInk(card, size, "unknown").find((stroke) => stroke.role === "hatch")!;
    const dense = nodeInk(card, size, "invalid").find((stroke) => stroke.role === "hatch")!;
    expect(dense.d.split("M").length).toBeGreaterThan(sparse.d.split("M").length);
    // A recommended value is neutral: it gets no hatching.
    expect(nodeInk(card, size, "recommended").some((stroke) => stroke.role === "hatch")).toBe(false);
    expect(nodeInk(node("arm:qrng_unsupervised"), { w: 220, h: 72 }, null).some((stroke) => stroke.role === "hatch")).toBe(true);
  });

  it("marks an eval with its prompts and repetitions, and an outcome with whether it decides", () => {
    const card = nodeInk(node("card:self_referential_probe"), { w: 260, h: 96 }, null);
    // 30 prompts and k = 20, capped at 12 across and 6 down: two arcs per dot.
    expect(card.find((stroke) => stroke.role === "dots")!.d.split("M").length - 1).toBe(12 * 6);
    const { graph } = template();
    const deciding = graph.nodes.find((item) => item.ref.type === "endpoint" && item.ref.role === "confirmatory")!;
    const exploring = graph.nodes.find((item) => item.ref.type === "endpoint" && item.ref.role === "exploratory")!;
    expect(nodeInk(deciding, { w: 220, h: 56 }, null).some((stroke) => stroke.role === "port")).toBe(true);
    expect(nodeInk(exploring, { w: 220, h: 56 }, null).some((stroke) => stroke.role === "port-open")).toBe(true);
  });

  it("leads a thread under what stands in its way, and straight across where nothing does", () => {
    const from = { x: 540, y: 400 }, to = { x: 1500, y: 300 };
    const straight = edgeInk("a>b", from, to, []);
    expect(straight.detour).toBe(false);
    expect(straight.head).not.toBe("");
    const wall = { x: 640, y: 0, w: 260, h: 700 };
    const led = edgeInk("a>b", from, to, [wall]);
    expect(led.detour).toBe(true);
    expect(edgeInk("a>b", from, to, [wall])).toEqual(led);
    // It runs under the wall: some stroke reaches below its foot.
    const ys = [...led.path.matchAll(/-?\d+\.\d (-?\d+\.\d)/g)].map((match) => Number(match[1]));
    expect(Math.max(...ys)).toBeGreaterThan(wall.y + wall.h);
    expect(led.path).not.toMatch(/NaN/);
  });
});
