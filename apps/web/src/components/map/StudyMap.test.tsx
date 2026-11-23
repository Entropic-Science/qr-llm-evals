import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, vi } from "vitest";
import { api } from "../../lib/api";
import { studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import { STATIONS, visible, type MapNode } from "../../lib/map/graph";
import { layoutGraph } from "../../lib/map/layout";
import { buildNotes, layoutNotes } from "../../lib/map/notes";
import { build, fixtureCase } from "../../lib/map/test-fixtures";
import { STORY_FIT_K } from "../../lib/map/viewport";
import { REC_STATES, type RecState } from "../../lib/recommendations";
import { Configure } from "../../routes/configure";
import { STEPS } from "../../routes/configure/steps";
import { StudyProvider, useStudy } from "../../routes/configure/StudyContext";
import { captureDownloads, resetBrowser, storeDraft, storedDraft } from "../study/study-test-kit";
import { newMapMemory, problemWords } from "./model";
import { StudyMap, type StudyMapProps } from "./StudyMap";

vi.setConfig({ testTimeout: 30_000 });

const LANE = "qr-llm/creative";
const DECK = `deck:${LANE}`;
const NONE: ReadonlySet<string> = new Set();

beforeEach(() => {
  resetBrowser();
  // No server: the bundled eval registry and the study's own models stand in.
  vi.spyOn(api, "cards").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "models").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "prereg").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "cardItems").mockRejectedValue(new TypeError("Failed to fetch"));
  // A save asks the server to read the written text back; here it cannot be asked.
  vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

/** Answers the media queries the map asks: whether motion is reduced, and how wide the window is. */
function stubMedia(matching: (query: string) => boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: matching(query), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
}

/** Stands in for the setup shell: it holds the map's memory, can take the map off screen, and can edit the study. */
function Shell(props: StudyMapProps) {
  const memory = useRef(newMapMemory());
  const [shown, setShown] = useState(true);
  const { config, updateConfig, settings } = useStudy();
  return <div>
    {/* What the settings dialog was asked to open on. The dialog itself is drawn once, in the test of the setup page. */}
    <output data-testid="settings">{settings === null ? "closed" : JSON.stringify({ path: settings.path ?? null, problemsOnly: settings.problemsOnly === true })}</output>
    <button type="button" onClick={() => setShown((now) => !now)}>Switch the view</button>
    <button type="button" onClick={() => updateConfig({ ...config, title: "Renamed on the way" })}>Rename the study</button>
    {shown ? <StudyMap memory={memory} {...props} /> : <p>The steps are on screen.</p>}
  </div>;
}

function renderMap(config: WizardStudyConfig = studyOneTemplate(), props: StudyMapProps = {}) {
  storeDraft(config);
  return render(<StudyProvider steps={STEPS}><Shell {...props} /></StudyProvider>);
}

const map = () => document.querySelector<HTMLElement>(".study-map")!;
const world = () => document.querySelector<HTMLElement>(".map-world")!;
const viewport = () => document.querySelector<HTMLElement>(".map-viewport")!;
const nodeEl = (id: string) => document.querySelector<HTMLElement>(`.map-node[data-node="${id}"]`);
const title = (id: string) => nodeEl(id)!.querySelector<HTMLButtonElement>(".map-node__title")!;
const drawnIds = () => [...document.querySelectorAll<HTMLElement>(".map-node")].map((element) => element.dataset.node!);
const camera = () => ({
  x: parseFloat(world().style.getPropertyValue("--map-x")),
  y: parseFloat(world().style.getPropertyValue("--map-y")),
  k: parseFloat(world().style.getPropertyValue("--map-k")),
});
const toolbar = () => within(screen.getByRole("toolbar", { name: "Map controls" }));
const asked = () => screen.getByTestId("settings").textContent;
const key = (element: Element, name: string, more: Partial<KeyboardEventInit> = {}) => fireEvent.keyDown(element, { key: name, ...more });
const focus = (element: HTMLElement) => act(() => element.focus());

describe("study map: the diagram", () => {
  it("draws the Study 1 template with its seven stations, every node of the design and its ink", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const expected = visible(graph, NONE, "design").nodes.map((node) => node.id);

    expect(map()).toHaveAttribute("data-zoom", "design");
    expect(map()).toHaveAttribute("data-mode", "canvas");
    expect([...document.querySelectorAll(".map-station")].map((station) => station.getAttribute("data-station"))).toEqual(STATIONS.map((station) => station.id));
    expect([...document.querySelectorAll(".map-station__label")].map((label) => label.textContent?.replace(/^\d/, ""))).toEqual(STATIONS.map((station) => station.label));
    expect([...document.querySelectorAll(".map-station__sentence")].map((sentence) => sentence.textContent)).toEqual(STATIONS.map((station) => station.sentence));

    // The nodes are in reading order, which is also the order of the DOM.
    expect(drawnIds()).toEqual(expected);
    for (const id of ["study", "arm:qrng_unsupervised", DECK, "tier0", "card:self_referential_probe", "test:H1", "test:H3", "family", `gate:neg:${LANE}`, `gate:pos:${LANE}`, "gate:completeness", "verdict:H1", "verdict:H3"]) {
      expect(nodeEl(id), id).toBeInTheDocument();
    }

    // One group of strokes per node, under the nodes, in the same world.
    const ink = document.querySelector(".map-world > svg.map-ink")!;
    expect(ink).toHaveAttribute("aria-hidden", "true");
    expect([...ink.querySelectorAll(".map-ink__node")].map((group) => group.getAttribute("data-ink")).sort()).toEqual([...expected].sort());
    expect(ink.querySelectorAll(".map-ink__node .ink-outline").length).toBeGreaterThanOrEqual(expected.length);
    for (const path of ink.querySelectorAll("path")) expect(path.getAttribute("d")).not.toMatch(/NaN|undefined/);
    // Threads with arrowheads; the Holm family as a brace beside the tests; completeness as a rule over the lanes.
    const layout = layoutGraph(graph);
    expect(ink.querySelectorAll(".map-edge")).toHaveLength(Object.keys(layout.edges).length);
    for (const edge of ink.querySelectorAll(".map-edge")) expect(edge.querySelector(".map-edge__head")!.getAttribute("d")).not.toBe("");
    expect(ink.querySelector("[data-ink='family-brace']")).toBeInTheDocument();
    expect(ink.querySelector("[data-ink='completeness-rule']")).toBeInTheDocument();
    expect(ink.querySelectorAll(".map-lane-band")).toHaveLength(2);
    expect(document.querySelector(".map-lane")).toHaveTextContent("qr-llm · Creative sampling");

    // The caption says the study in sentences.
    expect(document.querySelector(".map-caption")).toHaveTextContent("Does quantum sampling differ from seeded pseudo-random sampling?");
    expect(document.querySelector(".map-caption")).toHaveTextContent("Out of reach as configured: equivalent.");
  });

  it("places every node where the layout says, under one transform with the ink", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const layout = layoutGraph(graph);
    for (const [id, box] of Object.entries(layout.boxes)) {
      const element = nodeEl(id)!;
      expect(element.style.transform, id).toBe(`translate(${box.x}px, ${box.y}px)`);
      expect(element.style.width).toBe(`${box.w}px`);
      expect(element.style.height).toBe(`${box.h}px`);
      expect(document.querySelector<SVGGElement>(`.map-ink__node[data-ink="${id}"]`)!.style.transform).toBe(`translate(${box.x}px, ${box.y}px)`);
    }
    expect(world().contains(document.querySelector(".map-ink"))).toBe(true);
    expect(world().contains(document.querySelector(".map-nodes"))).toBe(true);
    expect(camera().k).toBeGreaterThan(0);
  });

  it("colours every node and every value by the state of its settings, with a glyph and a border as well", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const drawn = visible(graph, NONE, "design");
    const seen = new Set<RecState>();
    for (const node of drawn.nodes) {
      const element = nodeEl(node.id)!;
      const state = element.dataset.state as RecState | "none";
      const pip = element.querySelector(".map-node__pip");
      if (state === "none") {
        expect(element).toHaveClass("is-unrated");
        expect(pip, node.id).toBeNull();
        continue;
      }
      seen.add(state);
      // The class (which the hatching behind follows), the pip's chip class with its border, and its glyph.
      expect(element).toHaveClass(`is-${state}`);
      expect(pip).toHaveClass(REC_STATES[state].className);
      const glyph = REC_STATES[state].glyph;
      if (glyph !== null) expect(pip).toHaveTextContent(glyph);
      expect(document.querySelector(`.map-ink__node[data-ink="${node.id}"]`)).toHaveClass(`ink--${state}`);
      // And in words, for who cannot see either.
      expect(title(node.id).getAttribute("aria-label")).toContain(REC_STATES[state].legend);
    }
    expect(seen.size).toBeGreaterThanOrEqual(3);

    // A chip is the chip of the setup steps: same class, same glyph, same spoken name.
    for (const chip of document.querySelectorAll<HTMLElement>(".map-node__chips .rec")) {
      const state = chip.dataset.state as RecState;
      expect(chip).toHaveClass(REC_STATES[state].className);
      expect(chip.getAttribute("aria-label")).toContain(REC_STATES[state].legend);
    }
    // The pooled-token tests have no closed-form power: never a check mark.
    for (const id of ["card:tier0_pit_residuals", "card:tier0_mmd_hamming"]) {
      expect(nodeEl(id)).toHaveClass("is-unknown");
      expect(nodeEl(id)!.querySelector(".rec--sufficient")).toBeNull();
      expect(nodeEl(id)!.querySelector(".map-node__chips .rec--unknown")).toHaveTextContent("?");
    }
    // Study 1 claims a difference only: "equivalent" is stamped as out of reach, struck and said so.
    const equivalent = nodeEl("verdict:H3")!.querySelector("[data-verdict='equivalent']")!;
    expect(equivalent).toHaveClass("is-unreachable");
    expect(equivalent).toHaveTextContent("(unreachable)");
    expect(nodeEl("verdict:H3")!.querySelector("[data-verdict='effect']")).toHaveClass("is-reachable");
  });

  it("badges a node with its problems, and the badge opens the settings", () => {
    renderMap();
    const study = nodeEl("study")!;
    expect(study).toHaveClass("has-error");
    expect(document.querySelector(".map-ink__node[data-ink='study']")).toHaveClass("ink--error");
    const badge = study.querySelector<HTMLButtonElement>(".map-node__problems")!;
    expect(badge).toHaveClass("map-node__problems--error");
    // The badge counts the problems the setup puts on the study: the placeholder contact, which blocks the launch,
    // and its warnings. (The count was pinned at two before the setup gained warnings about the pre-registration.)
    const own = build(studyOneTemplate()).problems.filter((problem) => problem.nodeId === "study");
    expect(own.filter((problem) => problem.severity === "error").map((problem) => problem.code)).toEqual(["review.contact_placeholder"]);
    expect(own.length).toBeGreaterThanOrEqual(2);
    expect(badge).toHaveTextContent(`✕${own.length}`);
    expect(problemWords(own)).toMatch(/^1 problem blocks the launch, \d+ warnings?$/);
    expect(badge.getAttribute("aria-label")).toBe(`${problemWords(own)} on ${studyOneTemplate().title}. Show them in the settings.`);
    expect(document.querySelector(".map-station[data-station='question'] .map-station__problems--error")).toHaveTextContent(problemWords(own));

    // Several problems lead to the list of them.
    expect(asked()).toBe("closed");
    fireEvent.click(badge);
    expect(asked()).toBe(JSON.stringify({ path: null, problemsOnly: true }));
  });

  it("opens a setting from its chip and from the node's own button", () => {
    renderMap();
    const seeds = nodeEl(DECK)!.querySelector<HTMLButtonElement>(".map-node__chips .rec[data-rec='roster.seeds']")!;
    expect(seeds.getAttribute("aria-label")).toMatch(/^Control seeds: 3 seeds\. Recommended value\./);
    // The chip opens the node's own popover on the field of that setting; the settings dialog stays shut.
    fireEvent.click(seeds);
    expect(asked()).toBe("closed");
    const popover = document.querySelector<HTMLElement>(`.map-sheet[data-sheet="${DECK}"]`)!;
    expect(popover).toHaveAttribute("role", "dialog");
    expect(document.activeElement).toBe(within(popover).getByRole("spinbutton", { name: "Control seeds" }));

    const open = nodeEl("card:self_referential_probe")!.querySelector<HTMLButtonElement>(".map-node__tool[data-setting]")!;
    expect(open).toHaveAccessibleName("Open the settings of T4-a Self-referential experience probe");
    fireEvent.click(open);
    const opened = JSON.stringify({ path: open.dataset.setting, problemsOnly: false });
    expect(asked()).toBe(opened);
    // A setting the harness fixes has nowhere to open: its chip shows its own note instead.
    for (let press = 0; press < 2; press++) key(title("study"), "+");
    const locked = nodeEl("arm:qrng_unsupervised")!.querySelector<HTMLButtonElement>(".rec--locked")!;
    fireEvent.click(locked);
    expect(locked).toHaveAttribute("aria-expanded", "true");
    expect(asked()).toBe(opened);
  });

  it("unfolds a node in place and folds it back, moving only what is below it", () => {
    renderMap();
    const before = Object.fromEntries(drawnIds().map((id) => [id, nodeEl(id)!.style.transform]));
    expect(title(DECK)).toHaveAttribute("aria-expanded", "false");
    expect(nodeEl(DECK)!.querySelector(".map-node__fold")).toHaveTextContent("+3");
    expect(nodeEl("arm:prng_seed_7")).toBeNull();

    fireEvent.click(title(DECK));
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");
    expect(nodeEl(DECK)).toHaveClass("is-expanded");
    for (const id of ["arm:prng_seed_1", "arm:prng_seed_7", "arm:prng_seed_42"]) expect(nodeEl(id)).toBeInTheDocument();
    expect(document.querySelector(".map-status")).toHaveTextContent("Seeded controls unfolded: 3 parts.");
    // The deck stays where it was; the arm under it has moved down; the question has not moved at all.
    expect(nodeEl(DECK)!.style.transform).toBe(before[DECK]);
    expect(nodeEl("study")!.style.transform).toBe(before["study"]);
    expect(nodeEl("arm:prng_epsilon_001")!.style.transform).not.toBe(before["arm:prng_epsilon_001"]);

    fireEvent.click(title(DECK));
    expect(title(DECK)).toHaveAttribute("aria-expanded", "false");
    expect(nodeEl("arm:prng_seed_7")).toBeNull();
    expect(Object.fromEntries(drawnIds().map((id) => [id, nodeEl(id)!.style.transform]))).toEqual(before);
  });

  it("unfolds an eval into its outcomes and a tier into its evals", () => {
    renderMap();
    const card = "card:self_referential_probe";
    const hidden = build(studyOneTemplate()).graph.nodes.filter((node) => node.parent === card && !node.pinned).map((node) => node.id);
    expect(hidden.length).toBeGreaterThan(0);
    for (const id of hidden) expect(nodeEl(id)).toBeNull();
    fireEvent.click(title(card));
    for (const id of hidden) expect(nodeEl(id)).toHaveAttribute("data-kind", "endpoint");

    const group = drawnIds().find((id) => id.startsWith("group:tier"))!;
    const count = drawnIds().length;
    fireEvent.click(title(group));
    expect(drawnIds().length).toBeGreaterThan(count);
    expect(toolbar().getByRole("button", { name: "Fold all" })).toBeInTheDocument();
    fireEvent.click(toolbar().getByRole("button", { name: "Fold all" }));
    expect(drawnIds()).toHaveLength(visible(build(studyOneTemplate()).graph, NONE, "design").nodes.length);
  });

  it("is walked with the keyboard: one tab stop, arrows between neighbours, Enter and Escape", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const layout = layoutGraph(graph);
    const stops = () => [...document.querySelectorAll<HTMLButtonElement>(".map-node__title")].filter((button) => button.tabIndex === 0);
    expect(stops().map((button) => button.closest<HTMLElement>("[data-node]")!.dataset.node)).toEqual(["study"]);
    // Every node says what it is and how it stands.
    for (const button of document.querySelectorAll(".map-node__title")) expect(button.getAttribute("aria-label")).toMatch(/\S.*\.$/);
    for (const button of document.querySelectorAll(".map-node__title")) expect(document.getElementById(button.getAttribute("aria-describedby")!)!.textContent).not.toBe("");

    focus(title("study"));
    let at = "study";
    for (const [arrow, side] of [["ArrowRight", "right"], ["ArrowDown", "down"], ["ArrowDown", "down"], ["ArrowRight", "right"], ["ArrowUp", "up"], ["ArrowLeft", "left"]] as const) {
      const next = layout.neighbors[at]?.[side];
      key(document.activeElement!, arrow);
      if (next !== undefined) at = next;
      expect(document.activeElement, `${arrow} from ${at}`).toBe(title(at));
      expect(stops()).toEqual([title(at)]);
      // The chips and buttons of the node the keyboard is on are reachable with Tab; the others are not.
      for (const control of document.querySelectorAll<HTMLElement>(".map-node button.rec, .map-node__tool, .map-node__problems")) {
        expect(control.tabIndex).toBe(control.closest("[data-node]") === nodeEl(at) ? 0 : -1);
      }
    }
    key(document.activeElement!, "End");
    expect(document.activeElement).toBe(title(layout.order[layout.order.length - 1]!));
    // Past the last node an arrow stays put rather than losing the focus.
    key(document.activeElement!, "ArrowRight");
    expect(document.activeElement).toBe(title(layout.order[layout.order.length - 1]!));
    key(document.activeElement!, "Home");
    expect(document.activeElement).toBe(title("study"));

    // Enter unfolds a node that holds others (a button's own click); Escape folds it.
    focus(title(DECK));
    fireEvent.click(title(DECK));
    expect(nodeEl("arm:prng_seed_1")).toBeInTheDocument();
    key(title(DECK), "Escape");
    expect(nodeEl("arm:prng_seed_1")).toBeNull();
    expect(title(DECK)).toHaveAttribute("aria-expanded", "false");
    // Escape on a node that was unfolded out of another folds it back in and returns to its parent.
    fireEvent.click(title(DECK));
    focus(title("arm:prng_seed_7"));
    key(title("arm:prng_seed_7"), "Escape");
    expect(nodeEl("arm:prng_seed_7")).toBeNull();
    expect(document.activeElement).toBe(title(DECK));
  });

  it("pans with Shift and an arrow, zooms with plus and minus, and fits with 0", () => {
    renderMap();
    const start = camera();
    focus(title("study"));
    key(title("study"), "ArrowRight", { shiftKey: true });
    expect(camera().x).toBeLessThan(start.x);
    expect(document.activeElement).toBe(title("study"));
    key(title("study"), "ArrowDown", { shiftKey: true });
    expect(camera().y).toBeLessThan(start.y);
    // On the paper itself the arrows pan without Shift.
    const before = camera();
    key(viewport(), "ArrowLeft");
    expect(camera().x).toBeGreaterThan(before.x);

    key(title("study"), "+");
    expect(camera().k).toBeGreaterThan(start.k);
    key(title("study"), "-");
    key(title("study"), "-");
    expect(camera().k).toBeLessThan(start.k);
    // 0 shows the whole study, whatever the zoom was.
    key(title("study"), "0");
    const whole = camera();
    key(title("study"), "+");
    key(title("study"), "0");
    expect(camera()).toEqual(whole);
    // Far enough out, the map tells the story only, and says so.
    for (let press = 0; press < 3; press++) key(title("study"), "-");
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(document.querySelector(".map-status")).toHaveTextContent("The map shows the story of the study.");
  });

  it("zooms from the toolbar, about the pointer with the wheel, and to a node on a double-click", async () => {
    renderMap();
    const start = camera();
    expect(toolbar().getByRole("button", { name: "Zoom in" })).toBeInTheDocument();
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    expect(camera().k).toBeCloseTo(start.k * 1.25, 3);
    expect(document.querySelector(".map-toolbar__zoom")).toHaveTextContent(`${Math.round(start.k * 125)}%`);
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom out" }));
    expect(camera().k).toBeCloseTo(start.k, 3);
    fireEvent.click(toolbar().getByRole("button", { name: "Fit" }));
    expect(camera().k).toBeLessThan(start.k);
    fireEvent.click(toolbar().getByRole("button", { name: "Start" }));
    expect(camera()).toEqual(start);

    // The wheel keeps the point under the pointer where it is.
    const point = { x: 300, y: 200 };
    const under = { x: (point.x - start.x) / start.k, y: (point.y - start.y) / start.k };
    fireEvent.wheel(viewport(), { deltaY: -200, clientX: point.x, clientY: point.y });
    await waitFor(() => expect(camera().k).toBeGreaterThan(start.k));
    expect(under.x * camera().k + camera().x).toBeCloseTo(point.x, 0);
    expect(under.y * camera().k + camera().y).toBeCloseTo(point.y, 0);

    fireEvent.doubleClick(nodeEl("card:self_referential_probe")!);
    expect(camera().k).toBeGreaterThanOrEqual(1.5);
    expect(map()).toHaveAttribute("data-zoom", "detail");
  });

  it("pans when the paper or a node is dragged, without the drag counting as a click, and zooms on a pinch", async () => {
    renderMap();
    const start = camera();
    // A press that does not travel is a click: the deck unfolds.
    fireEvent.pointerDown(title(DECK), { pointerId: 1, pointerType: "mouse", button: 0, clientX: 400, clientY: 300 });
    fireEvent.pointerUp(title(DECK), { pointerId: 1, pointerType: "mouse", clientX: 401, clientY: 300 });
    fireEvent.click(title(DECK));
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");
    expect(camera()).toEqual(start);

    // A press that travels is a drag: the map follows the pointer, and the click that ends it is swallowed.
    fireEvent.pointerDown(title(DECK), { pointerId: 2, pointerType: "mouse", button: 0, clientX: 400, clientY: 300 });
    fireEvent.pointerMove(title(DECK), { pointerId: 2, pointerType: "mouse", clientX: 340, clientY: 330 });
    fireEvent.pointerMove(title(DECK), { pointerId: 2, pointerType: "mouse", clientX: 300, clientY: 350 });
    expect(viewport()).toHaveClass("is-panning");
    fireEvent.pointerUp(title(DECK), { pointerId: 2, pointerType: "mouse", clientX: 300, clientY: 350 });
    fireEvent.click(title(DECK));
    expect(viewport()).not.toHaveClass("is-panning");
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(camera().x).toBeCloseTo(start.x - 100, 1));
    expect(camera().y).toBeCloseTo(start.y + 50, 1);
    expect(camera().k).toBe(start.k);

    // Two fingers moving apart zoom in.
    fireEvent.pointerDown(viewport(), { pointerId: 3, pointerType: "touch", clientX: 300, clientY: 300 });
    fireEvent.pointerDown(viewport(), { pointerId: 4, pointerType: "touch", clientX: 400, clientY: 300 });
    fireEvent.pointerMove(viewport(), { pointerId: 4, pointerType: "touch", clientX: 400, clientY: 300 });
    fireEvent.pointerMove(viewport(), { pointerId: 4, pointerType: "touch", clientX: 500, clientY: 300 });
    fireEvent.pointerUp(viewport(), { pointerId: 4, pointerType: "touch", clientX: 500, clientY: 300 });
    fireEvent.pointerUp(viewport(), { pointerId: 3, pointerType: "touch", clientX: 300, clientY: 300 });
    await waitFor(() => expect(camera().k).toBeCloseTo(start.k * 2, 2));
  });

  it("shows the sentence of each node in the story, its numbers in the design and everything in the detail", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const card = graph.nodes.find((node) => node.id === "card:self_referential_probe")!;
    const element = () => nodeEl(card.id)!;
    // Design: the numbers.
    expect(element().querySelector(".map-node__sub")).toHaveTextContent("30 prompts × k = 24");
    expect([...element().querySelectorAll(".map-node__chips .rec")].map((chip) => chip.textContent)).toEqual(expect.arrayContaining(["✓30 prompts"]));
    expect(element().querySelector(".map-node__facts")).toBeNull();
    expect(nodeEl("family")!.querySelector(".map-node__sub")).toHaveTextContent("m = 3 · α 0.05");

    // Detail: every fact and every setting of the node, the ones the harness fixes too.
    key(title("study"), "+");
    key(title("study"), "+");
    expect(map()).toHaveAttribute("data-zoom", "detail");
    expect(element().querySelector(".map-node__facts")).toHaveTextContent([...card.facts.design, ...card.facts.detail].join(" "));
    expect(element().querySelectorAll(".map-node__chips .rec")).toHaveLength(card.recs.length);
    const arm = graph.nodes.find((node) => node.id === "arm:qrng_unsupervised")!;
    expect(nodeEl(arm.id)!.querySelectorAll(".map-node__chips .rec--locked")).toHaveLength(arm.recs.filter((rec) => rec.state === "locked").length);

    // Story: the skeleton of the study, each node with one sentence and no chip.
    for (let press = 0; press < 5; press++) key(title("study"), "-");
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(drawnIds()).toEqual(visible(graph, NONE, "story").nodes.map((node) => node.id));
    expect(element().querySelector(".map-node__story")).toHaveTextContent(card.facts.story);
    expect(nodeEl("verdict:H1")!.querySelector(".map-node__story")).toHaveTextContent("H1 can end as effect or inconclusive.");
    // A name is cut where its box ends at this zoom, so a verdict's starts with the number that tells it from the next.
    expect(nodeEl("verdict:H1")!.querySelector(".map-node__label")).toHaveTextContent("H1 verdict");
    expect(title("verdict:H1").getAttribute("aria-label")).toMatch(/^Verdict of H1, /);
    expect(document.querySelector(".map-node__chips")).toBeNull();
    // The gates are not part of the story; the thread of each test still reaches its verdict.
    expect(nodeEl(`gate:neg:${LANE}`)).toBeNull();
    expect(document.querySelector(".map-edge[data-from='test:H1'][data-to='verdict:H1']")).toBeInTheDocument();
  });
});

describe("study map: an open node", () => {
  it("opens a node on a sheet with its hypothesis card, mirrors it in the address and gives the focus back", () => {
    const opened: (string | null)[] = [];
    renderMap(studyOneTemplate(), { onOpenNode: (node) => opened.push(node?.id ?? null) });
    focus(title("test:H3"));
    fireEvent.click(title("test:H3"));
    const sheet = screen.getByRole("dialog", { name: "Paired sign-flip test" });
    expect(sheet).toHaveAttribute("data-sheet", "test:H3");
    expect(window.location.search).toBe("?open=test%3AH3");
    expect(map()).toHaveClass("has-open");
    expect(nodeEl("test:H3")).toHaveClass("is-open");
    expect(document.activeElement).toBe(within(sheet).getByRole("button", { name: "Close the details of Paired sign-flip test" }));
    // The Design step's own card and stamps.
    const card = sheet.querySelector(".hypothesis-card[data-hypothesis='H3']")!;
    expect(card).toHaveTextContent("Experience-claim rate");
    expect(card.querySelector(".verdict-stamp--equivalent")).toHaveClass("is-unreachable");
    expect(opened).toEqual(["test:H3"]);

    key(document.activeElement!, "Escape");
    expect(screen.queryByRole("dialog", { name: "Paired sign-flip test" })).not.toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(document.activeElement).toBe(title("test:H3"));
    expect(opened).toEqual(["test:H3", null]);
  });

  it("states the decision rules on a verdict's sheet, and a problem's fix on the node that has it", () => {
    renderMap();
    fireEvent.click(title("verdict:H1"));
    const verdict = screen.getByRole("dialog", { name: "Verdict of H1" });
    expect(within(verdict).getByRole("list", { name: "Verdicts H1 cannot reach" })).toHaveTextContent("Equivalent: unreachable.");
    expect(verdict.querySelector(".map-sheet__rules")).toHaveTextContent("pD < alpha.");
    fireEvent.click(within(verdict).getByRole("button", { name: "Close the details of Verdict of H1" }));

    // The question's node is not unfoldable, so its own click opens it.
    fireEvent.click(title("study"));
    const study = screen.getByRole("dialog", { name: studyOneTemplate().title });
    const problems = within(study).getByRole("list", { name: `Problems on ${studyOneTemplate().title}` });
    // Every problem the setup puts on the study, and no other.
    const own = build(studyOneTemplate()).problems.filter((problem) => problem.nodeId === "study");
    expect(within(problems).getAllByRole("listitem").map((item) => item.getAttribute("data-code"))).toEqual(own.map((problem) => problem.code));
    expect(problems.querySelector("[data-code='review.contact_placeholder']")).toHaveTextContent("Blocks launch");
    expect(within(study).getByRole("button", { name: "Open in all settings" })).toBeInTheDocument();
    expect(within(study).getByRole("button", { name: "Go to the Design step" })).toBeInTheDocument();
  });

  it("opens the details of a node that unfolds from its own button, and draws what the mount adds", () => {
    renderMap(studyOneTemplate(), { inspector: (node: MapNode) => <p>More about {node.id}.</p> });
    const card = "card:self_referential_probe";
    fireEvent.click(nodeEl(card)!.querySelector(".map-node__tool[aria-label^='Details of']")!);
    const sheet = screen.getByRole("dialog", { name: "T4-a Self-referential experience probe" });
    expect(sheet).toHaveClass("map-sheet--card");
    expect(within(sheet).getByRole("heading", { name: "Outcomes" })).toBeInTheDocument();
    expect(sheet.querySelector(`[data-inspector="${card}"]`)).toHaveTextContent(`More about ${card}.`);
    // A click on the paper puts the sheet away.
    fireEvent.click(viewport());
    expect(screen.queryByRole("dialog", { name: "T4-a Self-referential experience probe" })).not.toBeInTheDocument();
  });

  it("opens the node the address names, unfolding what holds it", () => {
    window.history.replaceState({}, "", "/?view=map&open=arm%3Aprng_seed_7");
    renderMap();
    expect(nodeEl("arm:prng_seed_7")).toHaveClass("is-open");
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "Seed 7" })).toBeInTheDocument();
  });

  it("tells whoever mounts it which node the pointer is over", () => {
    const hovered: (string | null)[] = [];
    renderMap(studyOneTemplate(), { onHoverNode: (node) => hovered.push(node?.id ?? null) });
    fireEvent.pointerEnter(nodeEl("family")!);
    fireEvent.pointerLeave(nodeEl("family")!);
    expect(hovered).toEqual(["family", null]);
  });
});

describe("study map: what it keeps", () => {
  it("keeps the view and the unfolded nodes when a setting changes", () => {
    renderMap();
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    key(viewport(), "ArrowRight");
    fireEvent.click(title(DECK));
    const before = camera();
    expect(nodeEl("study")!.querySelector(".map-node__label")).toHaveTextContent(studyOneTemplate().title);

    fireEvent.click(screen.getByRole("button", { name: "Rename the study" }));
    expect(nodeEl("study")!.querySelector(".map-node__label")).toHaveTextContent("Renamed on the way");
    expect(storedDraft().title).toBe("Renamed on the way");
    expect(camera()).toEqual(before);
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");
    expect(nodeEl("arm:prng_seed_7")).toBeInTheDocument();
  });

  it("comes back as it was left after the steps were on screen", () => {
    renderMap();
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    key(viewport(), "ArrowDown");
    fireEvent.click(title(DECK));
    focus(title("arm:prng_seed_42"));
    const before = camera();
    expect(map()).toHaveAttribute("data-zoom", "detail");

    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(document.querySelector(".study-map")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(camera()).toEqual(before);
    expect(map()).toHaveAttribute("data-zoom", "detail");
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");
    expect(title("arm:prng_seed_42").tabIndex).toBe(0);
  });

  it("stays under the study bar in the setup, with Save and the gear, across both views", async () => {
    const downloads = captureDownloads();
    storeDraft(studyOneTemplate());
    const { container } = render(<Configure />);
    const bar = () => within(screen.getByRole("region", { name: "Study" }));
    fireEvent.click(bar().getByRole("radio", { name: "Map" }));
    expect(window.location.search).toBe("?view=map");
    const slot = container.querySelector("#study-map-slot")!;
    expect(slot.querySelector(".study-map")).toBeInTheDocument();
    expect(container.querySelectorAll(".map-station")).toHaveLength(7);
    // The estimate stays beside the map, and the bar above it.
    expect(screen.getByLabelText("Live study estimates")).toBeInTheDocument();
    expect(bar().getByRole("button", { name: "Save study file" })).toBeEnabled();
    expect(bar().getByRole("button", { name: "All settings" })).toBeEnabled();
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    expect(downloads.names).toHaveLength(1);

    // A node's own settings button opens the settings dialog on its setting, over the map.
    fireEvent.click(nodeEl(DECK)!.querySelector<HTMLButtonElement>(".map-node__tool[data-setting='roster.seeds']")!);
    const dialog = await screen.findByRole("dialog", { name: "Everything in this study" });
    expect(dialog.querySelector("#settings-section-arms")).toBeInTheDocument();
    expect(container.querySelector(".study-map")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Everything in this study" })).not.toBeInTheDocument());
    // The validity pill is the bar's own button over the map as over the steps.
    expect(container.querySelector("button.validity-pill")).toBeInTheDocument();

    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    fireEvent.click(title(DECK));
    const before = camera();
    fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
    expect(container.querySelector(".study-map")).toBeNull();
    fireEvent.click(bar().getByRole("radio", { name: "Map" }));
    expect(camera()).toEqual(before);
    expect(title(DECK)).toHaveAttribute("aria-expanded", "true");

    // "Review & launch" leaves the map for the last step.
    fireEvent.click(toolbar().getByRole("button", { name: "Review & launch" }));
    await waitFor(() => expect(container.querySelector("[data-step='review']")).toBeInTheDocument());
    expect(container.querySelector(".study-map")).toBeNull();
  });
});

/** Gives the diagram a size, as a browser would: jsdom lays nothing out, and the map then assumes 1200 × 720. */
function sizeViewport(w: number, h: number) {
  const measure = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (!this.classList.contains("map-viewport")) return measure.call(this);
    return { x: 0, y: 0, left: 0, top: 0, right: w, bottom: h, width: w, height: h, toJSON: () => ({}) } as DOMRect;
  });
}

describe("study map: Fit", () => {
  /** Where a box of the world is on screen under the camera. */
  const onScreen = (box: { x: number; y: number; w: number; h: number }) => {
    const view = camera();
    return { left: view.x + box.x * view.k, top: view.y + box.y * view.k, right: view.x + (box.x + box.w) * view.k, bottom: view.y + (box.y + box.h) * view.k };
  };

  it("shows the whole story with groups unfolded: the question, the headings and every drawn node are on screen", () => {
    // The diagram of a 1440-wide window.
    const size = { w: 1024, h: 560 };
    sizeViewport(size.w, size.h);
    const config = studyOneTemplate();
    renderMap(config);
    const built = build(config);
    const unfolded = built.graph.nodes.filter((node) => node.kind === "card_group" || node.kind === "control_deck" || node.kind === "tier0_band").map((node) => node.id);
    expect(unfolded.length).toBeGreaterThanOrEqual(4);
    for (const id of unfolded) fireEvent.click(title(id));
    for (const id of unfolded) expect(title(id), id).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(toolbar().getByRole("button", { name: "Fit" }));
    // Everything at full density is far too tall for the smallest zoom, so the map is at the story zoom.
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(camera().k).toBeGreaterThan(0.35);
    expect(camera().k).toBeLessThanOrEqual(STORY_FIT_K);
    const layout = layoutGraph(built.graph, new Set(unfolded), "story");
    expect(drawnIds()).toEqual(layout.order);
    const notes = layoutNotes(buildNotes(built.graph, config, built.recs, built.problems), layout, "story");
    const drawn = { ...layout.boxes, ...Object.fromEntries(layout.stations.map((station) => [`heading of ${station.id}`, station.header])), ...notes };
    expect(Object.keys(drawn)).toEqual(expect.arrayContaining(["study", "heading of question", "heading of verdict", "note:narrative"]));
    for (const [id, box] of Object.entries(drawn)) {
      const at = onScreen(box);
      expect(at.left, id).toBeGreaterThanOrEqual(-0.01);
      expect(at.top, id).toBeGreaterThanOrEqual(-0.01);
      expect(at.right, id).toBeLessThanOrEqual(size.w + 0.01);
      expect(at.bottom, id).toBeLessThanOrEqual(size.h + 0.01);
    }

    // The same view from any zoom, and from the key.
    const whole = camera();
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    expect(map()).toHaveAttribute("data-zoom", "design");
    key(viewport(), "0");
    expect(camera()).toEqual(whole);
  });

  it("shows everything, at a zoom that draws it, where that fits", () => {
    sizeViewport(2400, 1300);
    renderMap();
    fireEvent.click(toolbar().getByRole("button", { name: "Fit" }));
    expect(map()).toHaveAttribute("data-zoom", "design");
    const layout = layoutGraph(build(studyOneTemplate()).graph);
    for (const [id, box] of Object.entries(layout.boxes)) {
      const at = onScreen(box);
      expect(at.left >= 0 && at.top >= 0 && at.right <= 2400 && at.bottom <= 1300, id).toBe(true);
    }
  });
});

describe("study map: the story zoom", () => {
  const station = (id: string) => document.querySelector<HTMLElement>(`.map-station[data-station="${id}"]`)!;
  const toStory = () => { for (let press = 0; press < 3; press++) fireEvent.click(toolbar().getByRole("button", { name: "Zoom out" })); };

  it("marks at its station a blocking problem on a node the story leaves out, with the way to it", () => {
    renderMap(fixtureCase("too_many_tests").config);
    // At the design zoom the Holm family is drawn and carries the problem itself.
    expect(nodeEl("family")).toHaveClass("has-error");
    expect(station("tests").querySelector(".map-station__problems--error")).toHaveTextContent("1 problem blocks the launch");

    toStory();
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(nodeEl("family")).toBeNull();
    // The family is not drawn, and the launch is still blocked: the station says so.
    const mark = station("tests").querySelector<HTMLButtonElement>("button.map-station__problems--error")!;
    expect(mark).toHaveTextContent("1 problem blocks the launch");
    // In words for the design zoom and as counts for the story zoom, where the words are wider than the station.
    expect(mark.querySelector(".map-station__words")).toHaveTextContent(/^✕ 1 problem blocks the launch/);
    expect(mark.querySelector(".map-station__counts")!.textContent).toMatch(/^✕ 1( {2}! \d+)?$/);
    expect(mark).toHaveAccessibleName(/^1 problem blocks the launch.* at Tests\. Show (it|them) in the settings\.$/);
    expect(mark.title).toContain("need more permutations than the harness runs");
    expect(station("tests").querySelector(".map-pip")).toHaveAttribute("data-state", "invalid");
    fireEvent.click(mark);
    expect(asked()).not.toBe("closed");
  });

  it("marks the problems of the gates at the gates station", () => {
    renderMap(fixtureCase("control_switched_off").config);
    toStory();
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(document.querySelector(".map-node[data-kind='gate']")).toBeNull();
    expect(station("gates").querySelector(".map-station__problems--error")).toHaveTextContent("1 problem blocks the launch");
    // Every problem of the study is on the map: on a node, or at a station.
    const { graph, problems } = build(fixtureCase("control_switched_off").config);
    const story = visible(graph, NONE, "story");
    const marked = [...document.querySelectorAll<HTMLElement>(".map-node__problems, .map-station__problems")].flatMap((mark) => (mark.title ? mark.title.split("\n") : []));
    for (const problem of problems) expect(marked, problem.code).toContain(problem.message);
    expect(Object.keys(story.left)).toContain("gates");
  });

  it("moves the focus to the nearest drawn node when the zoom leaves the focused one out", () => {
    renderMap();
    focus(title("family"));
    expect(document.activeElement).toBe(title("family"));
    key(document.activeElement!, "-");
    key(document.activeElement!, "-");
    expect(map()).toHaveAttribute("data-zoom", "design");
    expect(document.activeElement).toBe(title("family"));
    key(document.activeElement!, "-");
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(nodeEl("family")).toBeNull();
    // The Holm family stands beside the first test: that is where the keyboard is now, not on nothing.
    expect(document.activeElement).toBe(title("test:H1"));
    expect(title("test:H1").tabIndex).toBe(0);
    // The keyboard goes on from there.
    key(document.activeElement!, "+");
    expect(map()).toHaveAttribute("data-zoom", "design");
    expect(document.activeElement).toBe(title("test:H1"));
  });

  it("leaves the focus alone when it is not on the map", () => {
    renderMap();
    focus(title("family"));
    const zoomOut = toolbar().getByRole("button", { name: "Zoom out" });
    focus(zoomOut);
    toStory();
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(document.activeElement).toBe(zoomOut);
  });
});

describe("study map: the open node and the address", () => {
  it("takes the open node out of the address when the map goes off screen, and opens it again with the map", () => {
    renderMap();
    fireEvent.click(title("test:H3"));
    expect(window.location.search).toBe("?open=test%3AH3");

    // The steps are on screen: the address names no node of a map that is not there.
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(document.querySelector(".study-map")).toBeNull();
    expect(window.location.search).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(screen.getByRole("dialog", { name: "Paired sign-flip test" })).toBeInTheDocument();
    expect(window.location.search).toBe("?open=test%3AH3");

    // A node that was closed stays closed.
    key(document.activeElement!, "Escape");
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("keeps the other parameters of the address", () => {
    window.history.replaceState({}, "", "/?view=map");
    renderMap();
    fireEvent.click(title("test:H3"));
    expect(window.location.search).toBe("?view=map&open=test%3AH3");
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(window.location.search).toBe("?view=map");
  });
});

describe("study map: motion", () => {
  it("animates the layout and glides the camera where motion is welcome", () => {
    renderMap();
    expect(map()).toHaveClass("is-animated");
    expect(document.querySelectorAll(".map-ink .ink-draws").length).toBeGreaterThan(0);
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    expect(world()).toHaveClass("is-gliding");
  });

  it("has no motion class at all under reduced motion", () => {
    stubMedia((query) => query.includes("prefers-reduced-motion"));
    renderMap();
    expect(map()).not.toHaveClass("is-animated");
    fireEvent.click(toolbar().getByRole("button", { name: "Zoom in" }));
    fireEvent.click(toolbar().getByRole("button", { name: "Fit" }));
    key(viewport(), "ArrowRight");
    fireEvent.click(title(DECK));
    fireEvent.doubleClick(nodeEl("study")!);
    expect(world()).not.toHaveClass("is-gliding");
    expect(document.querySelector(".is-gliding, .is-animated")).toBeNull();
    // The diagram is all there: nothing waits on an animation to be drawn.
    expect(nodeEl("arm:prng_seed_7")).toBeInTheDocument();
  });
});

describe("study map: the outline", () => {
  it("lists every node of the graph once, with the state it has in the diagram and the same way into the settings", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const inDiagram = Object.fromEntries(drawnIds().map((id) => [id, nodeEl(id)!.dataset.state]));
    fireEvent.click(toolbar().getByRole("button", { name: "Outline" }));
    expect(map()).toHaveAttribute("data-mode", "outline");
    expect(toolbar().getByRole("button", { name: "Outline" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".map-viewport")).toBeNull();
    expect(toolbar().queryByRole("button", { name: "Zoom in" })).not.toBeInTheDocument();

    const entries = [...document.querySelectorAll<HTMLElement>(".map-outline__node")];
    expect(entries.map((entry) => entry.dataset.node).sort()).toEqual(graph.nodes.map((node) => node.id).sort());
    // Every node the diagram drew has an entry, in the state the diagram showed for its own settings.
    for (const node of graph.nodes) {
      const entry = document.querySelector<HTMLElement>(`.map-outline__node[data-node="${node.id}"]`)!;
      expect(entry.dataset.state).toBe(node.state ?? "none");
      if (node.state !== null) {
        expect(entry.querySelector(".map-outline__row .map-pip")).toHaveClass(REC_STATES[node.state].className);
        expect(entry.querySelector(".map-outline__row .sr-only")).toHaveTextContent(REC_STATES[node.state].legend);
      }
      const chips = [...entry.querySelectorAll<HTMLElement>(":scope > .map-outline__chips .rec")].map((chip) => chip.dataset.rec);
      expect(chips.sort()).toEqual(node.recs.map((rec) => rec.path).sort());
    }
    for (const [id, state] of Object.entries(inDiagram)) {
      const node = graph.nodes.find((item) => item.id === id)!;
      // A folded node of the diagram answers for what is inside it; the outline shows each on its own entry.
      if (node.children.length === 0) expect(document.querySelector<HTMLElement>(`.map-outline__node[data-node="${id}"]`)!.dataset.state).toBe(state);
    }
    // By station and by comparison, a seeded run nested under its deck.
    expect([...document.querySelectorAll(".map-outline__station > h3")].map((heading) => heading.textContent)).toEqual(STATIONS.map((station) => station.label));
    expect(document.querySelector(`.map-outline__node[data-node="${DECK}"] .map-outline__node[data-node="arm:prng_seed_7"]`)).toBeInTheDocument();
    expect(document.querySelector(".map-outline__lane > h4")).toHaveTextContent("qr-llm · Creative sampling");
    expect(document.querySelector(".map-outline__node[data-node='study'] [data-code='review.contact_placeholder']")).toHaveTextContent("Blocks launch");

    fireEvent.click(document.querySelector<HTMLButtonElement>(`.map-outline__node[data-node="${DECK}"] > .map-outline__row .map-outline__open`)!);
    expect(asked()).toBe(JSON.stringify({ path: "roster.seeds", problemsOnly: false }));
    // A chip opens the settings of its node under the entry, on that chip's field; the dialog is not asked for again.
    fireEvent.click(document.querySelector<HTMLButtonElement>(".map-outline__node[data-node='card:self_referential_probe'] > .map-outline__chips .rec[data-rec='cards.self_referential_probe.k']")!);
    expect(asked()).toBe(JSON.stringify({ path: "roster.seeds", problemsOnly: false }));
    const opened = document.querySelector<HTMLElement>(".map-outline__node[data-node='card:self_referential_probe'] > .map-sheet--inline")!;
    expect(opened).toHaveAttribute("data-sheet", "card:self_referential_probe");
    expect(document.activeElement).toBe(within(opened).getByRole("spinbutton", { name: "Repetitions (k) for self_referential_probe" }));
    fireEvent.click(within(document.querySelector<HTMLElement>(".map-outline__node[data-node='study'] [data-code='review.contact_placeholder']")!).getByRole("button", { name: "Show in all settings" }));
    expect(asked()).toBe(JSON.stringify({ path: "contact", problemsOnly: false }));
  });

  it("is what a narrow window gets, with the diagram one press away", () => {
    stubMedia((query) => query.includes("max-width: 760px"));
    renderMap();
    expect(map()).toHaveAttribute("data-mode", "outline");
    expect(document.querySelector(".map-outline__title")).toHaveTextContent("The study, station by station");
    fireEvent.click(toolbar().getByRole("button", { name: "Diagram" }));
    expect(map()).toHaveAttribute("data-mode", "canvas");
    expect(document.querySelectorAll(".map-station")).toHaveLength(7);
    // A narrow window starts with the legend folded.
    expect(document.querySelector<HTMLDetailsElement>(".map-legend")!.open).toBe(false);
  });

  it("can be reached past the diagram from the keyboard", async () => {
    renderMap();
    fireEvent.click(screen.getByRole("button", { name: "Skip the map: open the outline" }));
    expect(map()).toHaveAttribute("data-mode", "outline");
    await waitFor(() => expect(document.activeElement).toBe(document.querySelector(".map-outline__title")));
  });
});

describe("study map: legend, and studies that are not Study 1", () => {
  it("explains the eight states with their glyphs, and folds away", () => {
    renderMap();
    const legend = document.querySelector<HTMLDetailsElement>(".map-legend")!;
    expect(legend.querySelector("summary")).toHaveTextContent("Legend");
    const states = [...legend.querySelectorAll<HTMLElement>("li[data-state]")];
    expect(states.map((item) => item.dataset.state)).toEqual(Object.keys(REC_STATES));
    for (const item of states) {
      const state = item.dataset.state as RecState;
      expect(item.querySelector(".rec")).toHaveClass(REC_STATES[state].className);
      expect(item).toHaveTextContent(REC_STATES[state].legend);
      expect(item.querySelector(".rec__glyph")).toBeInTheDocument();
    }
    expect(legend).toHaveTextContent("Quantum arm");
    expect(legend).toHaveTextContent("Exploratory, switched off, or not in force");
    // It is a strip over the diagram, not a box on it: it cannot cover a node or a popover.
    expect(viewport().contains(legend)).toBe(false);
    expect(legend.compareDocumentPosition(viewport()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(map().contains(legend)).toBe(true);
    act(() => { legend.open = !legend.open; legend.dispatchEvent(new Event("toggle")); });
    const folded = legend.open;
    // The choice outlives a visit to the steps.
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch the view" }));
    expect(document.querySelector<HTMLDetailsElement>(".map-legend")!.open).toBe(folded);
    // The outline uses the same marks, so it keeps the legend.
    fireEvent.click(toolbar().getByRole("button", { name: "Outline" }));
    expect(map()).toHaveAttribute("data-mode", "outline");
    expect(document.querySelector<HTMLDetailsElement>(".map-legend")!.open).toBe(folded);
  });

  it("starts with the legend unfolded only where the window can spare its height", () => {
    stubMedia((query) => query === "(min-width: 1280px) and (min-height: 960px)");
    renderMap();
    expect(document.querySelector<HTMLDetailsElement>(".map-legend")!.open).toBe(true);
    cleanup();
    // Wide but short: the diagram keeps the height.
    stubMedia((query) => query === "(min-width: 1280px)");
    renderMap();
    expect(document.querySelector<HTMLDetailsElement>(".map-legend")!.open).toBe(false);
  });

  it("draws a study without a design as placeholders and offers to declare one", () => {
    renderMap({ ...studyOneTemplate(), design: null });
    const banner = document.querySelector(".map-banner")!;
    expect(banner).toHaveTextContent("This draft uses the fixed Study 1 plan. Tests, gates and verdicts are not drawn.");
    expect(document.querySelector(".map-node--test")).toBeNull();
    expect(document.querySelector(".map-node--verdict")).toBeNull();
    expect(nodeEl("ghost:gates")).toHaveClass("is-ghost");
    expect(document.querySelector(".map-ink__node[data-ink='ghost:gates']")).toHaveClass("ink--ghost");
    expect(document.querySelector("[data-ink='family-brace']")).toBeNull();
    expect(title("ghost:verdict").getAttribute("aria-label")).toContain("Placeholder");

    fireEvent.click(within(banner as HTMLElement).getByRole("button", { name: "Declare a design" }));
    expect(document.querySelector(".map-banner")).toBeNull();
    expect(nodeEl("test:H1")).toBeInTheDocument();
    expect(storedDraft().design).toBeTruthy();
  });

  it("draws one lane per comparison, and leads the positive control to its gate around the evals", () => {
    renderMap(fixtureCase("equivalence_claim").config);
    const config = fixtureCase("equivalence_claim").config;
    const { graph } = build(config);
    const layout = layoutGraph(graph);
    expect(document.querySelectorAll(".map-lane:not(.map-lane--again)")).toHaveLength(graph.lanes.length);
    expect(drawnIds()).toEqual(layout.order);
    const long = [...document.querySelectorAll<SVGGElement>(".map-edge")].filter((edge) => edge.dataset.to?.startsWith("gate:pos:"));
    expect(long.length).toBeGreaterThan(0);
    for (const edge of long) {
      expect(edge).toHaveClass("map-edge--positive");
      expect(edge).toHaveClass("is-detour");
    }
    // Nothing else is led around anything.
    expect(document.querySelectorAll(".map-edge.is-detour")).toHaveLength(long.length);
  });

  it("draws every study of the design fixtures in both forms without a missing node", () => {
    for (const name of ["two_cells", "control_switched_off"]) {
      const found = fixtureCase(name).config;
      const view = renderMap(found);
      const { graph } = build(found);
      expect(drawnIds(), name).toEqual(layoutGraph(graph).order);
      fireEvent.click(toolbar().getByRole("button", { name: "Outline" }));
      expect(document.querySelectorAll(".map-outline__node"), name).toHaveLength(graph.nodes.length);
      view.unmount();
      cleanup();
      resetBrowser();
    }
  });
});
