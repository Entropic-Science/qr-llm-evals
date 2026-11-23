import { studyOneTemplate } from "../config-store";
import { STATIONS, visible, type StudyGraph } from "./graph";
import {
  COLUMN_W, GAP, GHOST_H, HEADER_GAP, HEADER_H, LANE_GAP, LANE_LABEL_H, NODE_SIZE, OPEN_SIZE, STATION_X, layoutGraph, sizeOf,
  type MapLayout,
} from "./layout";
import { FIXTURE_CASES, build, expandableIds, fixtureCase } from "./test-fixtures";
import type { Box, ZoomTier } from "./viewport";

const NONE: ReadonlySet<string> = new Set();
const TIERS: readonly ZoomTier[] = ["story", "design", "detail"];
const LANE = "qr-llm/creative";

const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const inside = (inner: Box, outer: Box) =>
  inner.x >= outer.x && inner.y >= outer.y && inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h;
const box = (layout: MapLayout, id: string): Box => {
  const found = layout.boxes[id];
  if (!found) throw new Error(`the layout has no box for ${id}`);
  return found;
};

/** What must hold for any graph, any set of unfolded nodes and any tier. */
function expectSound(graph: StudyGraph, expanded: ReadonlySet<string>, tier: ZoomTier, name: string) {
  const layout = layoutGraph(graph, expanded, tier);
  const drawn = visible(graph, expanded, tier);
  const where = `${name} at ${tier} with ${expanded.size} unfolded`;

  // Every drawn node has a box of its own size in its own column, and nothing else has one.
  expect(layout.order, `${where}: order`).toEqual(drawn.nodes.map((node) => node.id));
  expect(Object.keys(layout.boxes).sort(), `${where}: boxes`).toEqual([...layout.order].sort());
  for (const node of drawn.nodes) {
    const size = sizeOf(node), at = box(layout, node.id);
    expect([at.x, at.w, at.h], `${where}: ${node.id}`).toEqual([STATION_X[node.station], size.w, size.h]);
    expect(Number.isFinite(at.y) && at.y >= 0, `${where}: ${node.id} is below the headers`).toBe(true);
    expect(inside(at, layout.bounds), `${where}: ${node.id} is inside the bounds`).toBe(true);
  }

  // No two boxes overlap, and no box touches a station header.
  const ids = layout.order;
  const clashes: string[] = [];
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      if (overlaps(box(layout, ids[i]!), box(layout, ids[j]!))) clashes.push(`${ids[i]} overlaps ${ids[j]}`);
    }
  }
  expect(clashes, `${where}: overlapping boxes`).toEqual([]);
  expect(layout.stations.map((station) => station.id)).toEqual(STATIONS.map((station) => station.id));
  for (const station of layout.stations) {
    expect(station.header).toEqual({ x: STATION_X[station.id], y: -(HEADER_H + HEADER_GAP), w: COLUMN_W[station.id], h: HEADER_H });
    expect(inside(station.header, layout.bounds), `${where}: header ${station.id}`).toBe(true);
  }

  // Lanes are stacked bands that do not meet, and a lane's nodes are inside its band.
  expect(layout.lanes.map((lane) => lane.key), `${where}: lanes`).toEqual(graph.lanes.map((lane) => lane.key));
  layout.lanes.forEach((lane, index) => {
    const next = layout.lanes[index + 1];
    if (next) expect(next.band.y - (lane.band.y + lane.band.h), `${where}: gap under ${lane.key}`).toBe(LANE_GAP);
    expect(inside(lane.band, layout.bounds), `${where}: band ${lane.key}`).toBe(true);
    expect(inside(lane.labelBox, lane.band)).toBe(true);
    expect(lane.labelBox.h).toBe(LANE_LABEL_H);
    for (const segment of lane.segments) expect(inside(segment, lane.band)).toBe(true);
  });
  for (const node of drawn.nodes) {
    const at = box(layout, node.id);
    const lane = layout.lanes.find((item) => item.key === node.lane);
    if (lane) {
      expect(lane.segments.some((segment) => inside(at, segment)), `${where}: ${node.id} is inside a segment of its lane`).toBe(true);
      expect(overlaps(at, lane.labelBox), `${where}: ${node.id} keeps clear of the lane label`).toBe(false);
    } else if (node.station !== "question" && node.station !== "prompts" && node.station !== "measures") {
      // What all lanes share stands above the first lane.
      for (const band of layout.lanes) expect(overlaps(at, band.band), `${where}: ${node.id} is above the lanes`).toBe(false);
    }
  }

  // Edges leave the right-middle of one box and enter the left-middle of the next.
  // An implied edge is no line and has no anchors.
  const lines = drawn.edges.filter((edge) => !edge.implied);
  expect(Object.keys(layout.edges).sort(), `${where}: edges`).toEqual(lines.map((edge) => edge.id).sort());
  for (const edge of lines) {
    const from = box(layout, edge.from), to = box(layout, edge.to);
    expect(layout.edges[edge.id], `${where}: ${edge.id}`).toEqual({
      from: { x: from.x + from.w, y: from.y + from.h / 2 }, to: { x: to.x, y: to.y + to.h / 2 },
    });
    expect(layout.edges[edge.id]!.to.x, `${where}: ${edge.id} runs left to right`).toBeGreaterThan(layout.edges[edge.id]!.from.x);
  }

  // The arrow keys only lead to drawn nodes, in the direction they say.
  const centre = (id: string) => ({ x: box(layout, id).x + box(layout, id).w / 2, y: box(layout, id).y + box(layout, id).h / 2 });
  expect(Object.keys(layout.neighbors).sort()).toEqual([...layout.order].sort());
  for (const id of ids) {
    const near = layout.neighbors[id]!;
    if (near.left) expect(centre(near.left).x).toBeLessThan(centre(id).x);
    if (near.right) expect(centre(near.right).x).toBeGreaterThan(centre(id).x);
    if (near.up) expect(centre(near.up).y).toBeLessThan(centre(id).y);
    if (near.down) expect(centre(near.down).y).toBeGreaterThan(centre(id).y);
    if (ids.length > 1) expect(Object.keys(near).length, `${where}: ${id} has a neighbour`).toBeGreaterThan(0);
  }
  return layout;
}

describe("map layout: constants", () => {
  it("places the stations and sizes the nodes as the spec gives them", () => {
    expect(STATION_X).toEqual({ question: 0, arms: 320, prompts: 640, measures: 960, tests: 1240, gates: 1500, verdict: 1740 });
    expect(NODE_SIZE.study).toEqual({ w: 260, h: 160 });
    expect(NODE_SIZE.quantum_arm).toEqual({ w: 220, h: 72 });
    expect(NODE_SIZE.control_deck).toEqual({ w: 220, h: 88 });
    expect(NODE_SIZE.card).toEqual({ w: 260, h: 96 });
    expect(NODE_SIZE.card_group).toEqual({ w: 260, h: 56 });
    expect(NODE_SIZE.tier0_band).toEqual({ w: 260, h: 56 });
    expect(NODE_SIZE.endpoint).toEqual({ w: 220, h: 56 });
    expect(NODE_SIZE.test).toEqual({ w: 200, h: 64 });
    expect(NODE_SIZE.gate).toEqual({ w: 180, h: 56 });
    expect(NODE_SIZE.verdict).toEqual({ w: 200, h: 56 });
    expect([GAP, LANE_GAP]).toEqual([16, 48]);
    expect(OPEN_SIZE).toEqual({ card: { w: 560, h: 420 }, other: { w: 360, h: 240 } });
    expect(sizeOf({ kind: "ghost_stack", station: "gates" })).toEqual({ w: 180, h: GHOST_H });
  });

  it("keeps a gutter between every two columns", () => {
    STATIONS.forEach((station, index) => {
      const next = STATIONS[index + 1];
      if (next) expect(STATION_X[next.id] - (STATION_X[station.id] + COLUMN_W[station.id])).toBeGreaterThanOrEqual(60);
    });
    for (const [kind, size] of Object.entries(NODE_SIZE)) expect(size.w, kind).toBeGreaterThan(0);
  });
});

describe("map layout: the Study 1 template", () => {
  const { graph } = build(studyOneTemplate());
  const layout = layoutGraph(graph);

  it("is sound at every tier, folded and unfolded", () => {
    for (const tier of TIERS) {
      expectSound(graph, NONE, tier, "template");
      expectSound(graph, new Set(expandableIds(graph)), tier, "template");
    }
  });

  it("stacks the question and the evals from the top, and the lane under what all lanes share", () => {
    expect(box(layout, "study")).toEqual({ x: 0, y: 0, w: 260, h: 160 });
    expect(box(layout, "tier0")).toEqual({ x: 640, y: 0, w: 260, h: 56 });
    expect(box(layout, "card:tier0_pit_residuals")).toEqual({ x: 640, y: 72, w: 260, h: 96 });
    expect(box(layout, "card:tier0_mmd_hamming").y).toBe(72 + 96 + GAP);
    expect(box(layout, "family")).toEqual({ x: 1240, y: 0, w: 200, h: 56 });
    expect(box(layout, "gate:completeness")).toEqual({ x: 1500, y: 0, w: 180, h: 56 });
    const lane = layout.lanes[0]!;
    expect(lane).toMatchObject({ key: LANE, label: "qr-llm · Creative sampling" });
    expect(lane.band.y).toBe(56 + LANE_GAP);
    expect(box(layout, "arm:qrng_unsupervised").y).toBe(lane.band.y + LANE_LABEL_H);
    expect(box(layout, "test:H1").y).toBe(lane.band.y + LANE_LABEL_H);
    expect(box(layout, `gate:neg:${LANE}`).y).toBe(lane.band.y + LANE_LABEL_H);
    expect(box(layout, `gate:pos:${LANE}`).y).toBe(lane.band.y + LANE_LABEL_H + 56 + GAP);
  });

  it("stands an outcome level with its eval and a verdict level with its test", () => {
    expect(box(layout, "endpoint:self_referential_probe/experience_claim_rate").y).toBe(box(layout, "card:self_referential_probe").y);
    expect(box(layout, "endpoint:tier0_pit_residuals/pit_ks_two_sample").y).toBe(box(layout, "card:tier0_pit_residuals").y);
    for (const hid of ["H1", "H2", "H3"]) expect(box(layout, `verdict:${hid}`).y).toBe(box(layout, `test:${hid}`).y);
    expect(box(layout, "test:H2").y - box(layout, "test:H1").y).toBe(64 + GAP);
  });

  it("makes room beside an unfolded eval for all its outcomes", () => {
    const open = layoutGraph(graph, new Set(["card:self_referential_probe"]));
    const card = box(open, "card:self_referential_probe");
    const outcomes = ["experience_claim_rate", "semantic_entropy", "vendi_embed", "told_main_effect"].map((name) => box(open, `endpoint:self_referential_probe/${name}`));
    expect(outcomes.map((item) => item.y - card.y)).toEqual([0, 72, 144, 216]);
    // The next row starts under the taller of the eval and its outcomes.
    expect(box(open, "group:tier1").y).toBe(card.y + 4 * 56 + 3 * GAP + GAP);
    expect(box(layout, "group:tier1").y).toBe(box(layout, "card:self_referential_probe").y + 96 + GAP);
  });

  it("gives the same numbers for the same input", () => {
    for (const tier of TIERS) {
      for (const expanded of [NONE, new Set(["tier0", `deck:${LANE}`]), new Set(expandableIds(graph))]) {
        const again = layoutGraph(build(structuredClone(studyOneTemplate())).graph, new Set(expanded), tier);
        expect(JSON.stringify(layoutGraph(graph, expanded, tier))).toBe(JSON.stringify(again));
      }
    }
    expect(layoutGraph(graph)).toEqual(layoutGraph(graph, NONE, "design"));
  });

  it("does not depend on the order in which nodes were unfolded", () => {
    const ids = expandableIds(graph);
    expect(layoutGraph(graph, new Set(ids))).toEqual(layoutGraph(graph, new Set([...ids].reverse())));
  });

  it("covers every box, header and lane with its bounds", () => {
    expect(layout.bounds.x).toBe(0);
    expect(layout.bounds.y).toBe(-(HEADER_H + HEADER_GAP));
    expect(layout.bounds.x + layout.bounds.w).toBe(layout.lanes[0]!.band.x + layout.lanes[0]!.band.w);
    const lowest = Math.max(...Object.values(layout.boxes).map((item) => item.y + item.h), layout.lanes[0]!.band.y + layout.lanes[0]!.band.h);
    expect(layout.bounds.y + layout.bounds.h).toBe(lowest);
  });

  it("leads the arrow keys along the flow", () => {
    expect(layout.neighbors["study"]).toMatchObject({ right: "arm:qrng_unsupervised" });
    expect(layout.neighbors["card:self_referential_probe"]).toMatchObject({ right: "endpoint:self_referential_probe/experience_claim_rate", up: "card:tier0_mmd_hamming", down: "group:tier1" });
    expect(layout.neighbors["test:H1"]).toMatchObject({ down: "test:H2", right: `gate:neg:${LANE}` });
    expect(layout.neighbors["verdict:H2"]).toMatchObject({ up: "verdict:H1", down: "verdict:H3" });
  });
});

describe("map layout: what the story zoom draws", () => {
  it("does not grow with the evals that are unfolded, which the story leaves out", () => {
    const { graph } = build(studyOneTemplate());
    const folded = layoutGraph(graph);
    const unfolded = layoutGraph(graph, new Set(["group:tier1", "group:tier4"]));
    expect(unfolded.bounds.h).toBeGreaterThan(folded.bounds.h * 2);
    expect(unfolded.storyBounds).toEqual(folded.storyBounds);
    expect(folded.storyBounds.y).toBe(-(HEADER_H + HEADER_GAP));
  });
});

describe("map layout: zoom tiers", () => {
  it.each(FIXTURE_CASES.map((item) => [item.name, item] as const))("%s: a tier decides what gets a box, never where the box is", (_name, item) => {
    const { graph } = build(item.config);
    for (const expanded of [NONE, new Set(expandableIds(graph))]) {
      const design = layoutGraph(graph, expanded, "design");
      const story = layoutGraph(graph, expanded, "story");
      expect(layoutGraph(graph, expanded, "detail")).toEqual(design);
      expect(story.bounds).toEqual(design.bounds);
      // What "Fit" shows of the story is the same at every tier too, and holds exactly what the story draws.
      expect(story.storyBounds).toEqual(design.storyBounds);
      expect(inside(story.storyBounds, story.bounds)).toBe(true);
      for (const id of story.order) expect(inside(story.boxes[id]!, story.storyBounds), id).toBe(true);
      for (const station of story.stations) expect(inside(station.header, story.storyBounds), station.id).toBe(true);
      for (const lane of story.lanes) expect(inside(lane.band, story.storyBounds), lane.key).toBe(true);
      expect(story.lanes).toEqual(design.lanes);
      expect(story.stations).toEqual(design.stations);
      expect(story.order.length).toBeLessThanOrEqual(design.order.length);
      for (const id of story.order) expect(story.boxes[id], id).toEqual(design.boxes[id]);
    }
  });
});

describe("map layout: every study of the design fixture", () => {
  it.each(FIXTURE_CASES.map((item) => [item.name, item] as const))("%s: no two boxes overlap, folded or unfolded, at any tier", (name, item) => {
    const { graph } = build(item.config);
    const all = expandableIds(graph);
    for (const tier of TIERS) {
      expectSound(graph, NONE, tier, name);
      expectSound(graph, new Set(all), tier, name);
    }
    // One at a time, and every other one, at the tier that draws everything.
    for (const id of all) expectSound(graph, new Set([id]), "design", name);
    expectSound(graph, new Set(all.filter((_, index) => index % 2 === 0)), "design", name);
  }, 30_000);

  it("gives each comparison a lane of its own height", () => {
    const { graph } = build(fixtureCase("two_cells").config);
    const layout = layoutGraph(graph);
    const [first, second] = layout.lanes;
    expect(layout.lanes.map((lane) => lane.label)).toEqual(["qr-llm · Creative sampling", "qr-llm · Temperature 1 + min-p 0.10"]);
    expect(second!.band.y).toBe(first!.band.y + first!.band.h + LANE_GAP);
    // The first lane has an observed arm and a planned positive control; the second does not.
    expect(first!.band.h).toBeGreaterThan(second!.band.h);
    expect(box(layout, "test:H2").y).toBe(second!.band.y + LANE_LABEL_H);
    expect(box(layout, "arm:qrng_unsupervised__qr_llm__t1_minp").y).toBe(second!.band.y + LANE_LABEL_H);
  });

  it("lays out a study with no arm and no eval", () => {
    const config = structuredClone(studyOneTemplate());
    config.arms = [];
    config.cards = [];
    // The design still names three evals; they are drawn as placeholders above the invitation.
    const designed = build(config).graph;
    for (const tier of TIERS) expectSound(designed, NONE, tier, "empty, designed");
    expect(box(layoutGraph(designed), "ghost:evals").y).toBe(3 * (96 + GAP));
    config.design = null;
    const { graph } = build(config);
    const layout = expectSound(graph, NONE, "design", "empty");
    expect(layout.lanes).toEqual([]);
    expect(box(layout, "ghost:arms")).toEqual({ x: 320, y: 0, w: 220, h: GHOST_H });
    expect(box(layout, "ghost:evals")).toEqual({ x: 640, y: 0, w: 260, h: GHOST_H });
    expect(layout.order).toEqual(["study", "ghost:arms", "ghost:evals", "ghost:measures", "family", "ghost:gates", "ghost:verdict"]);
    for (const tier of TIERS) expectSound(graph, NONE, tier, "empty");
  });
});

describe("map layout: unfolding a node", () => {
  const cases = [
    ["template", build(studyOneTemplate()).graph] as const,
    ...["study1_legacy", "two_cells", "equivalence_claim", "draft_not_eligible", "too_many_tests"].map((name) => [name, build(fixtureCase(name).config).graph] as const),
  ];

  it.each(cases)("%s: moves only what is below it, and only downward", (name, graph) => {
    const before = layoutGraph(graph);
    for (const id of expandableIds(graph)) {
      // Unfold it where it is drawn: with its ancestors unfolded.
      const ancestors = new Set<string>();
      for (let node = graph.nodes.find((item) => item.id === id); node?.parent; node = graph.nodes.find((item) => item.id === node!.parent)) ancestors.add(node.parent);
      const closed = ancestors.size === 0 ? before : layoutGraph(graph, ancestors);
      const open = layoutGraph(graph, new Set([...ancestors, id]));
      const self = box(closed, id);
      expect(box(open, id), `${name}: ${id} stays where it is`).toEqual(self);
      expect(open.order.some((other) => closed.boxes[other] === undefined), `${name}: unfolding ${id} draws something new`).toBe(true);
      for (const other of closed.order) {
        const was = box(closed, other), now = open.boxes[other];
        // The invitation to open an eval gives way to the outcomes; everything else stays drawn.
        if (now === undefined) {
          expect(other, `${name}: unfolding ${id} hides nothing but the invitation`).toBe("ghost:measures");
          continue;
        }
        expect([now.x, now.w, now.h], `${name}: ${other} keeps its column and size when ${id} unfolds`).toEqual([was.x, was.w, was.h]);
        expect(now.y, `${name}: ${other} never moves up when ${id} unfolds`).toBeGreaterThanOrEqual(was.y);
        if (now.y !== was.y) expect(was.y, `${name}: ${other} moved, so it was below ${id}`).toBeGreaterThan(self.y);
      }
      // Whatever moved, moved by one amount per stack: nothing is reordered.
      const order = (layout: MapLayout) => layout.order.filter((other) => closed.boxes[other] !== undefined && open.boxes[other] !== undefined);
      expect(order(open), `${name}: unfolding ${id} keeps the reading order`).toEqual(order(closed));
    }
  }, 30_000);

  it("leaves the other lane's tests where they are when a deck unfolds in a lane that is not the tallest stack", () => {
    const { graph } = build(fixtureCase("two_cells").config);
    const closed = layoutGraph(graph);
    const open = layoutGraph(graph, new Set(["deck:qr-llm/t1_minp"]));
    // The second lane grows; everything in the first lane and in the eval column is untouched.
    for (const id of closed.order.filter((other) => graph.nodes.find((node) => node.id === other)!.lane !== "qr-llm/t1_minp")) {
      expect(open.boxes[id], id).toEqual(closed.boxes[id]);
    }
    expect(open.lanes[0]).toEqual(closed.lanes[0]);
    expect(open.lanes[1]!.band.y).toBe(closed.lanes[1]!.band.y);
    expect(box(open, "arm:prng_seed_1__qr_llm__t1_minp").y).toBe(box(open, "deck:qr-llm/t1_minp").y + 88 + GAP);
  });

  it("puts what was folded back exactly where it was", () => {
    const { graph } = build(studyOneTemplate());
    const before = layoutGraph(graph);
    layoutGraph(graph, new Set(expandableIds(graph)));
    expect(layoutGraph(graph)).toEqual(before);
  });
});
