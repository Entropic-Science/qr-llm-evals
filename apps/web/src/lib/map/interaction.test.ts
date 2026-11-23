import type { CardItem, CardItemsResponse } from "@qr-evals/contracts";
import { studyOneTemplate } from "../config-store";
import { layoutGraph } from "./layout";
import { NODE_NOTE_H, STATION_NOTE_H, STATION_NOTE_REACH, boundsWithNotes, buildNotes, isNoteId, layoutNotes } from "./notes";
import { LEAN_MAX, POPOVER_GAP, POPOVER_PAD, keepInView, lean, popoverBox, unionBox } from "./popover";
import {
  PROMPT_ROWS_MOUNTED, answersText, cutLength, failureSentence, firstLine, inStudy, nextOffset, promptLineage, roleLabel, rowWindow,
  sharedLead, sourceSentence,
} from "./prompts";
import { build, fixtureCase } from "./test-fixtures";

const LANE = "qr-llm/creative";
const item = (index: number, content: string, more: Partial<CardItem> = {}): CardItem => ({
  index, prompt_id: `p${index}`, messages: [{ role: "user", content }], truncated: false, ...more,
});
const page = (offset: number, count: number, total: number, more: Partial<CardItemsResponse> = {}): CardItemsResponse => ({
  card_id: "c", listable: true, source: "task_module", reason: null, total, offset, limit: 50, told: null,
  items: Array.from({ length: count }, (_, at) => item(offset + at, `Prompt ${offset + at}`)), ...more,
});

describe("marginal notes", () => {
  const config = studyOneTemplate();
  const { graph, problems, recs } = build(config);
  const notes = buildNotes(graph, config, recs, problems);
  const byId = (id: string) => notes.find((note) => note.id === id)!;

  it("puts each setting that is not a node of its own where it belongs", () => {
    expect(notes.every((note) => isNoteId(note.id))).toBe(true);
    expect(new Set(notes.map((note) => note.id)).size).toBe(notes.length);
    expect(graph.nodes.some((node) => isNoteId(node.id))).toBe(false);

    // Alpha and the Holm family at the tests; power and completeness at the gates; the narrative at the verdicts.
    expect(byId("note:alpha").anchor).toEqual({ type: "station", station: "tests" });
    expect(byId("note:alpha").text).toBe("α = 0.05 · Holm · m = 3");
    expect(byId("note:alpha").state).toBe("recommended");
    expect(byId("note:alpha").settings).toEqual(["design.alpha"]);
    expect(byId("note:gates").anchor).toEqual({ type: "station", station: "gates" });
    expect(byId("note:gates").text).toBe("power 80% · completeness ≥ 90%");
    // What the harness fixes is said as locked, and has nothing to edit.
    expect(byId("note:gates").state).toBe("locked");
    expect(byId("note:gates").settings).toEqual([]);
    expect(byId("note:narrative").anchor).toEqual({ type: "station", station: "verdict" });
    expect(byId("note:narrative").text).toBe(`Narrative written by ${config.narrative_model.served_name}`);

    // Authors and contact on the study; its placeholder contact is the study's own problem.
    expect(byId("note:people").anchor).toEqual({ type: "node", node: "study" });
    expect(byId("note:people").severity).toBe("error");
    expect(byId("note:people").problems.map((problem) => problem.code)).toEqual(["review.contact_placeholder"]);

    // The envelope on the quantum arm, the sessions on the observed arm, the judge on every eval it scores.
    expect(byId("note:envelope:arm:qrng_unsupervised").state).toBe("locked");
    expect(byId("note:envelope:arm:qrng_unsupervised").recs.map((rec) => rec.path)).toEqual(["fixed.envelope"]);
    // It is said of the arms that are compared: a provider's own arm and a greedy arm are outside the envelope,
    // and a biased arm bends it on purpose.
    expect(byId("note:envelope:arm:qrng_unsupervised").text).toBe("Same envelope in the compared arms");
    expect(byId("note:envelope:arm:qrng_unsupervised").story).toBe("The harness pins the sampling envelope of the quantum arm and the seeded controls, so the compared arms differ only in their entropy source and its seed.");
    for (const note of notes.filter((item) => item.kind === "envelope")) expect(`${note.text} ${note.story}`, note.id).not.toMatch(/every arm/);
    const sessions = notes.filter((note) => note.kind === "sessions");
    expect(sessions).toHaveLength(1);
    expect(graph.nodes.find((node) => sessions[0]!.anchor.type === "node" && node.id === sessions[0]!.anchor.node)?.kind).toBe("supervised_arm");
    expect(sessions[0]!.text).toMatch(/^Sessions: (single stream|four-stream tile), [0-9]+ min timer$/);
    const judged = graph.nodes.filter((node) => node.ref.type === "card" && node.ref.judged && node.ref.selected).map((node) => `note:judge:${node.id}`);
    expect(notes.filter((note) => note.kind === "judge").map((note) => note.id)).toEqual(judged);
  });

  it("has no alpha or gate note on a study without a design, and none for sessions without an observed arm", () => {
    const legacy = { ...config, design: null };
    const built = build(legacy);
    const kinds = buildNotes(built.graph, legacy, built.recs, built.problems).map((note) => note.kind);
    expect(kinds).not.toContain("alpha");
    expect(kinds).not.toContain("gates");
    expect(kinds).toContain("narrative");

    const unobserved = { ...config, supervised: null, arms: config.arms.filter((arm) => arm.observer_mode !== "supervised") };
    const plain = build(unobserved);
    expect(buildNotes(plain.graph, unobserved, plain.recs, plain.problems).map((note) => note.kind)).not.toContain("sessions");
  });

  it("hangs a station's note over its heading and a node's note under the node, clear of every node", () => {
    const layout = layoutGraph(graph);
    const boxes = layoutNotes(notes, layout);
    const tests = layout.stations.find((station) => station.id === "tests")!.header;
    expect(boxes["note:alpha"]).toEqual({ x: tests.x, y: tests.y - STATION_NOTE_H - 4, w: tests.w + STATION_NOTE_REACH, h: STATION_NOTE_H });
    const arm = layout.boxes["arm:qrng_unsupervised"]!;
    expect(boxes["note:envelope:arm:qrng_unsupervised"]).toEqual({ x: arm.x + 8, y: arm.y + arm.h + 1, w: arm.w - 16, h: NODE_NOTE_H });

    const overlaps = (a: { x: number; y: number; w: number; h: number }, b: typeof a) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    for (const [id, box] of Object.entries(boxes)) {
      for (const [nodeId, node] of Object.entries(layout.boxes)) expect(overlaps(box, node), `${id} over ${nodeId}`).toBe(false);
      for (const station of layout.stations) expect(overlaps(box, station.header), `${id} over the ${station.id} heading`).toBe(false);
    }
    // The notes are part of what "Fit" shows.
    const bounds = boundsWithNotes(layout.bounds, boxes);
    expect(bounds.y).toBeLessThan(layout.bounds.y);
    for (const box of Object.values(boxes)) expect(box.y).toBeGreaterThanOrEqual(bounds.y);
  });

  it("draws a node's note only while the node is drawn, and only the stations' in the story", () => {
    const folded = layoutGraph(graph);
    const judge = notes.find((note) => note.kind === "judge");
    if (judge && judge.anchor.type === "node") {
      const without = { ...folded, boxes: Object.fromEntries(Object.entries(folded.boxes).filter(([id]) => judge.anchor.type === "node" && id !== judge.anchor.node)) };
      expect(layoutNotes(notes, without)[judge.id]).toBeUndefined();
    }
    const story = layoutNotes(notes, layoutGraph(graph, new Set(), "story"), "story");
    expect(Object.keys(story).sort()).toEqual(["note:alpha", "note:gates", "note:narrative"]);
  });

  it("gives every lane its own envelope note", () => {
    const two = fixtureCase("two_cells").config;
    const built = build(two);
    const envelopes = buildNotes(built.graph, two, built.recs, built.problems).filter((note) => note.kind === "envelope");
    expect(envelopes).toHaveLength(built.graph.nodes.filter((node) => node.kind === "quantum_arm").length);
    expect(envelopes.length).toBeGreaterThanOrEqual(2);
  });
});

describe("popover geometry", () => {
  const node = { x: 640, y: 0, w: 260, h: 96 };
  const size = { w: 620, h: 560 };

  it("hangs a popover under what it is about, or beside it where the viewport is too short", () => {
    expect(popoverBox(node, size, { w: 1200, h: 720 })).toEqual({ side: "below", box: { x: 640, y: 96 + POPOVER_GAP, w: 620, h: 560 } });
    expect(popoverBox(node, size, { w: 1200, h: 600 })).toEqual({ side: "right", box: { x: 900 + POPOVER_GAP, y: 0, w: 620, h: 560 } });
    // Neither fits: under it, and `keepInView` does the rest.
    expect(popoverBox(node, size, { w: 700, h: 500 }).side).toBe("below");
    expect(unionBox(node, { x: 640, y: 106, w: 620, h: 560 })).toEqual({ x: 640, y: 0, w: 620, h: 666 });
  });

  it("keeps a popover inside the viewport wherever the map is carried", () => {
    const viewport = { w: 1200, h: 720 };
    const box = { x: 640, y: 106, w: 620, h: 560 };
    const inside = (view: { x: number; y: number; k: number }, height = 400) => {
      const fit = keepInView(view, box, height, viewport);
      const onScreen = view.k * fit.scale;
      const left = view.x + view.k * (box.x + fit.dx), top = view.y + view.k * (box.y + fit.dy);
      return { fit, left, top, right: left + Math.min(box.w, fit.maxW) * onScreen, bottom: top + Math.min(height, fit.maxH) * onScreen };
    };
    // In view already: it stays where it hangs.
    const still = inside({ x: -300, y: 40, k: 1 });
    expect(still.fit).toMatchObject({ dx: 0, dy: 0, scale: 1, moved: false });
    // Carried off every edge in turn, at several zooms: it stops at the edge.
    for (const k of [0.5, 1, 1.6, 2.5]) {
      for (const view of [{ x: 900, y: 0, k }, { x: -3000, y: 0, k }, { x: 0, y: 900, k }, { x: 0, y: -3000, k }, { x: -2400, y: -2400, k }]) {
        const at = inside(view);
        expect(at.left, JSON.stringify(view)).toBeGreaterThanOrEqual(POPOVER_PAD - 0.01);
        expect(at.top).toBeGreaterThanOrEqual(POPOVER_PAD - 0.01);
        expect(at.right).toBeLessThanOrEqual(viewport.w - POPOVER_PAD + 0.01);
        expect(at.bottom).toBeLessThanOrEqual(viewport.h - POPOVER_PAD + 0.01);
        expect(at.fit.moved).toBe(true);
      }
    }
    // It grows with the map up to its reading size and no further.
    expect(keepInView({ x: 0, y: 0, k: 2 }, box, 400, viewport).scale).toBe(0.5);
    expect(keepInView({ x: 0, y: 0, k: 0.5 }, box, 400, viewport).scale).toBe(1);
    // A viewport smaller than the popover caps its size at what there is.
    const small = keepInView({ x: 0, y: 0, k: 1 }, box, 560, { w: 400, h: 300 });
    expect(small.maxW).toBe(400 - 2 * POPOVER_PAD);
    expect(small.maxH).toBe(300 - 2 * POPOVER_PAD);
  });

  it("leans a box toward a near pointer, by at most a few px, and not at all beyond its reach", () => {
    const box = { x: 100, y: 100, w: 200, h: 60 };
    expect(lean({ x: 600, y: 600 }, box, 90)).toEqual({ x: 0, y: 0 });
    expect(lean({ x: 200, y: 130 }, box, 90)).toEqual({ x: 0, y: 0 });
    const right = lean({ x: 330, y: 130 }, box, 90);
    expect(right.x).toBeGreaterThan(0);
    expect(right.y).toBe(0);
    const above = lean({ x: 200, y: 60 }, box, 90);
    expect(above.y).toBeLessThan(0);
    // Nearer pulls harder; on the box hardest; never past the limit.
    expect(lean({ x: 310, y: 130 }, box, 90).x).toBeGreaterThan(lean({ x: 370, y: 130 }, box, 90).x);
    for (const point of [{ x: 300, y: 160 }, { x: 100, y: 100 }, { x: 389, y: 130 }, { x: 250, y: 249 }]) {
      const by = lean(point, box, 90);
      expect(Math.hypot(by.x, by.y)).toBeLessThanOrEqual(LEAN_MAX * Math.SQRT2);
      expect(Math.abs(by.x)).toBeLessThanOrEqual(LEAN_MAX);
    }
    // A note shies away: the same pull, the other way.
    const shy = lean({ x: 330, y: 130 }, box, 90, -LEAN_MAX);
    expect(shy.x).toBeCloseTo(-right.x);
    expect(lean({ x: 330, y: 130 }, box, 0)).toEqual({ x: 0, y: 0 });
  });
});

describe("prompts", () => {
  it("says in a sentence why an eval's prompts are not listed, and what went wrong with a request", () => {
    expect(sourceSentence("derived_at_run", null)).toBe("The prompts are built from this study's own generations at run time.");
    expect(sourceSentence("remote_dataset", null)).toBe("The prompts are downloaded at run time from the pinned revision of a remote dataset.");
    expect(sourceSentence("inspect_task", null)).toBe("The prompts are inside the Inspect task and cannot be read here.");
    expect(sourceSentence("no_prompts", null)).toBe("This measure reads the tokens the study generates; it has no prompts of its own.");
    expect(sourceSentence("error", "TaskModuleError: <repo>/evals/x/task.py has no build_items.")).toBe("The prompts could not be read: TaskModuleError: <repo>/evals/x/task.py has no build_items.");
    expect(sourceSentence("error", null)).toBe("The prompts could not be read: the task module gave no reason.");

    expect(failureSentence(null)).toBe("Prompts need the run service. It is not reachable.");
    expect(failureSentence({ status: 404, message: "Unknown card" })).toBe("The run service does not know this eval, so its prompts cannot be listed.");
    expect(failureSentence({ status: 502, message: "bad" })).toBe("Prompts need the run service. It answered with an error.");
    expect(failureSentence({ status: 400, message: "limit must be at most 100." })).toBe("The run service refused the request for the prompts: limit must be at most 100.");
  });

  it("reads a prompt's first line, its turns and where it was cut", () => {
    expect(firstLine({ messages: [{ role: "system", content: "Be brief." }, { role: "user", content: "\n\n  What is it like?  \nSecond line." }] })).toBe("What is it like?");
    expect(firstLine({ messages: [{ role: "system", content: "Only a system turn." }] })).toBe("Only a system turn.");
    expect(firstLine({ messages: [] })).toBe("");
    // A preamble every prompt opens with is left out of the rows; a prompt always keeps a line of its own.
    const told = ["First question?", "Second question?"].map((question, index) => item(index, ["Please answer as you normally would.", "", question].join(String.fromCharCode(10))));
    expect(sharedLead(told)).toBe(1);
    expect(firstLine(told[1]!, sharedLead(told))).toBe("Second question?");
    expect(sharedLead([item(0, "Same."), item(1, "Same.")])).toBe(0);
    expect(sharedLead([told[0]!])).toBe(0);
    expect(firstLine(item(0, "Only line."), 3)).toBe("Only line.");
    expect(roleLabel("user")).toBe("User");
    expect(roleLabel("developer")).toBe("Developer");
    expect(roleLabel(undefined)).toBe("Message");
    expect(cutLength(item(0, "short"))).toBeNull();
    // The cut turn is as long as the limit, counted in characters and not in UTF-16 units.
    expect(cutLength(item(0, "x".repeat(6000), { truncated: true, messages: [{ role: "system", content: "s" }, { role: "user", content: "x".repeat(6000) }] }))).toBe(6000);
    expect(cutLength(item(0, "", { truncated: true, messages: [{ role: "user", content: "😀".repeat(20) }] }))).toBe(20);
  });

  it("knows which prompts the study runs, where the next page starts and which rows to mount", () => {
    expect(inStudy(0, 20)).toBe(true);
    expect(inStudy(19, 20)).toBe(true);
    expect(inStudy(20, 20)).toBe(false);
    expect(nextOffset(page(0, 50, 4000))).toBe(50);
    expect(nextOffset(page(3950, 50, 4000))).toBeUndefined();
    expect(nextOffset(page(0, 30, 30))).toBeUndefined();
    expect(nextOffset(page(0, 0, 0, { listable: false, source: "no_prompts" }))).toBeUndefined();

    expect(rowWindow(0, 50)).toEqual({ first: 0, last: PROMPT_ROWS_MOUNTED });
    expect(rowWindow(0, 12)).toEqual({ first: 0, last: 12 });
    const deep = rowWindow(28 * 300, 4000);
    expect(deep.last - deep.first).toBe(PROMPT_ROWS_MOUNTED);
    expect(deep.first).toBeLessThanOrEqual(300);
    expect(deep.last).toBeGreaterThan(306);
    // The end of the list still fills the window.
    expect(rowWindow(28 * 3999, 4000)).toEqual({ first: 4000 - PROMPT_ROWS_MOUNTED, last: 4000 });
  });

  it("follows a prompt from the arms that answer it to the hypothesis it feeds", () => {
    const config = studyOneTemplate();
    const { graph } = build(config);
    const probe = promptLineage(graph, "self_referential_probe");
    expect(probe.runs).toBe(true);
    expect(probe.arms.count).toBe(config.arms.filter((arm) => (arm.availability ?? "available") === "available").length);
    expect(probe.arms.parts[0]).toBe("the quantum arm");
    expect(probe.arms.parts).toContain("3 seeded controls");
    expect(probe.toldCells).toBe(3);
    expect(probe.outcomes[0]).toMatchObject({ decides: true });
    expect(probe.hypotheses.map((hypothesis) => hypothesis.id)).toEqual(graph.nodes.flatMap((node) => (node.ref.type === "test" && node.ref.hypothesis.card_id === "self_referential_probe" ? [node.ref.hypothesis.id] : [])));
    expect(probe.hypotheses[0]!.nodeId).toBe(`test:${probe.hypotheses[0]!.id}`);
    expect(graph.nodes.some((node) => node.id === `gate:neg:${LANE}`)).toBe(true);

    // An eval that decides nothing feeds no hypothesis; one the study does not run is said not to run.
    const other = graph.nodes.find((node) => node.ref.type === "card" && node.ref.selected && !node.ref.decides && node.ref.known);
    if (other?.ref.type === "card") expect(promptLineage(graph, other.ref.cardId).hypotheses).toEqual([]);
    expect(promptLineage(graph, "no_such_card")).toMatchObject({ runs: false, outcomes: [], hypotheses: [] });
    // A study on the fixed Study 1 plan says so: what is not drawn is not called undecided.
    expect(probe.legacy).toBe(false);
    expect(promptLineage(build(fixtureCase("study1_legacy").config).graph, "self_referential_probe")).toMatchObject({ legacy: true, hypotheses: [] });
    // What the fixed plan does with the outcomes is said only of a study that runs it.
    expect(probe.fixedPlan).toEqual([]);
    const fixed = build(fixtureCase("study1_legacy").config).graph;
    expect(promptLineage(fixed, "self_referential_probe").fixedPlan).toEqual([expect.stringMatching(/^All \d+ outcomes are reported as exploratory by the fixed Study 1 plan, with no significance threshold\.$/)]);
    expect(promptLineage(fixed, "opinion_quantum_sampling").fixedPlan).toEqual([
      expect.stringMatching(/ is a secondary outcome of the fixed Study 1 plan: it is tested with Benjamini–Hochberg correction at 0\.05 across the secondary outcomes of tier 4\.$/),
      expect.stringMatching(/ reported as exploratory by the fixed Study 1 plan, with no significance threshold\.$/),
    ]);
    expect(promptLineage(fixed, "novelty_bench").fixedPlan[0]).toMatch(/ is a primary outcome of the fixed Study 1 plan: it is tested with Holm correction at α = 0\.05 across the primary outcomes of that plan\.$/);

    expect(answersText(20, 1)).toBe("20 times");
    expect(answersText(null, 1)).toBe("once");
    expect(answersText(20, 3)).toBe("20 times in each of the 3 told cells");
  });
});
