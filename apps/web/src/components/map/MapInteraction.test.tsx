import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, vi } from "vitest";
import { api } from "../../lib/api";
import { studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import { lineage, visible } from "../../lib/map/graph";
import { layoutGraph } from "../../lib/map/layout";
import { buildNotes, layoutNotes } from "../../lib/map/notes";
import { POPOVER_PAD } from "../../lib/map/popover";
import { build } from "../../lib/map/test-fixtures";
import { STEPS } from "../../routes/configure/steps";
import { StudyProvider, useStudy } from "../../routes/configure/StudyContext";
import { resetBrowser, storeDraft, storedDraft } from "../study/study-test-kit";
import { newMapMemory } from "./model";
import { StudyMap } from "./StudyMap";

vi.setConfig({ testTimeout: 30_000 });

const LANE = "qr-llm/creative";
const DECK = `deck:${LANE}`;
const PROBE = "card:self_referential_probe";
const NONE: ReadonlySet<string> = new Set();
/** The size the map takes for its viewport where nothing is laid out. */
const VIEW = { w: 1200, h: 720 };

beforeEach(() => {
  resetBrowser();
  // No server: the bundled eval registry and the study's own models stand in, and no prompt can be read.
  vi.spyOn(api, "cards").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "models").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "prereg").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "cardItems").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function stubMedia(matching: (query: string) => boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: matching(query), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
}

/** Stands in for the setup shell: it holds the map's memory and prints what the settings dialog was asked to open on. */
function Shell() {
  const memory = useRef(newMapMemory());
  const { settings, config } = useStudy();
  return <div>
    <output data-testid="settings">{settings === null ? "closed" : JSON.stringify({ path: settings.path ?? null, problemsOnly: settings.problemsOnly === true })}</output>
    <output data-testid="alpha">{String(config.design?.alpha ?? "none")}</output>
    <StudyMap memory={memory} />
  </div>;
}

function renderMap(config: WizardStudyConfig = studyOneTemplate()) {
  storeDraft(config);
  return render(<StudyProvider steps={STEPS}><Shell /></StudyProvider>);
}

const map = () => document.querySelector<HTMLElement>(".study-map")!;
const world = () => document.querySelector<HTMLElement>(".map-world")!;
const viewport = () => document.querySelector<HTMLElement>(".map-viewport")!;
const nodeEl = (id: string) => document.querySelector<HTMLElement>(`.map-node[data-node="${id}"]`);
const inkEl = (id: string) => document.querySelector<SVGGElement>(`.map-ink__node[data-ink="${id}"]`);
const noteEl = (id: string) => document.querySelector<HTMLButtonElement>(`.map-marginal[data-note="${id}"]`);
const title = (id: string) => nodeEl(id)!.querySelector<HTMLButtonElement>(".map-node__title")!;
const chip = (id: string, path: string) => nodeEl(id)!.querySelector<HTMLButtonElement>(`.map-node__chips .rec[data-rec="${path}"]`)!;
const popovers = () => [...document.querySelectorAll<HTMLElement>(".map-popover")];
const popover = () => document.querySelector<HTMLElement>(".map-popover");
const asked = () => screen.getByTestId("settings").textContent;
const said = () => document.querySelector(".map-status")!.textContent;
const key = (element: Element, name: string, more: Partial<KeyboardEventInit> = {}) => fireEvent.keyDown(element, { key: name, ...more });
const camera = () => ({
  x: parseFloat(world().style.getPropertyValue("--map-x")),
  y: parseFloat(world().style.getPropertyValue("--map-y")),
  k: parseFloat(world().style.getPropertyValue("--map-k")),
});
const px = (element: HTMLElement | SVGElement, name: string) => parseFloat(element.style.getPropertyValue(name) || "0");

/** Frames asked for and not yet drawn: the tests draw them when they choose. */
function holdFrames() {
  const waiting = new Map<number, FrameRequestCallback>();
  let next = 0;
  const request = vi.fn((callback: FrameRequestCallback) => { waiting.set(++next, callback); return next; });
  vi.stubGlobal("requestAnimationFrame", request);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { waiting.delete(id); });
  return {
    request,
    draw: () => act(() => { const due = [...waiting.values()]; waiting.clear(); for (const callback of due) callback(0); }),
  };
}

/** Where a point of the world is on screen, in the viewport's own px. */
function onScreen(point: { x: number; y: number }) {
  const view = camera();
  return { clientX: view.x + view.k * point.x, clientY: view.y + view.k * point.y };
}

describe("study map: the popover of a node", () => {
  it("opens on Enter with the fields of the node, and an edit changes the map and the stored draft at once", () => {
    renderMap();
    const stamp = () => nodeEl("verdict:H3")!.querySelector("[data-verdict='equivalent']")!;
    expect(stamp()).toHaveClass("is-unreachable");

    act(() => title("family").focus());
    fireEvent.click(title("family"));
    const sheet = screen.getByRole("dialog", { name: "Holm correction" });
    expect(sheet).toHaveClass("map-popover", "map-sheet--family");
    expect(sheet).toHaveAttribute("data-sheet", "family");
    // Hand-drawn: its outline is a pencilled path, not a box border.
    expect(sheet.querySelector(".map-popover__outline path")!.getAttribute("d")).toMatch(/^M/);
    // The same field the Design step and the settings dialog draw, with ids of its own.
    const alpha = within(sheet).getByRole("combobox", { name: /^False-finding rate \(α\)/ });
    expect(alpha.closest("[data-path='design.alpha']")).toHaveAttribute("id", "map-field-design-alpha");
    expect(within(sheet).getByRole("group", { name: /^Outcomes that decide the study/ })).toBeInTheDocument();
    // What the harness fixes is stated with its reason, not offered for editing.
    const fixed = within(sheet).getByLabelText("Set by the harness on Holm correction");
    expect(fixed.querySelectorAll(".rec--locked").length).toBeGreaterThanOrEqual(3);
    expect(fixed.querySelector("input, select")).toBeNull();

    expect(nodeEl("family")).toHaveAttribute("data-state", "recommended");
    expect(nodeEl(PROBE)).toHaveAttribute("data-state", "sufficient");
    fireEvent.change(alpha, { target: { value: "0.005" } });
    // The study, the draft in storage, the node, its chip, the eval the stricter rate weakens, and the note at the station.
    expect(screen.getByTestId("alpha")).toHaveTextContent("0.005");
    expect(storedDraft().design?.alpha).toBe(0.005);
    expect(nodeEl("family")).toHaveAttribute("data-state", "custom");
    expect(chip("family", "design.alpha")).toHaveClass("rec--custom");
    expect(nodeEl(PROBE)).toHaveAttribute("data-state", "marginal");
    expect(inkEl(PROBE)).toHaveClass("ink--marginal");
    expect(noteEl("note:alpha")).toHaveTextContent("α = 0.005");
    expect(noteEl("note:alpha")).toHaveClass("is-custom");
    // The popover stays open on the same field, and the edit is said.
    expect(screen.getByRole("dialog", { name: "Holm correction" })).toBeInTheDocument();
    expect(within(sheet).getByRole("combobox", { name: /^False-finding rate \(α\)/ })).toHaveValue("0.005");
    expect(said()).toMatch(/^False-finding rate \(α\): α = 0\.005\. Your choice\./);

    // The field's own way back.
    fireEvent.click(within(sheet).getByRole("button", { name: "Use recommended" }));
    expect(storedDraft().design?.alpha).toBe(0.05);
    expect(nodeEl(PROBE)).toHaveAttribute("data-state", "sufficient");
  });

  it("edits the prompts of an eval from the map, shows the problem that raises and fixes it in one press", () => {
    renderMap();
    fireEvent.click(chip(PROBE, "cards.self_referential_probe.items"));
    const sheet = popover()!;
    const prompts = within(sheet).getByRole("spinbutton", { name: "Prompts for self_referential_probe" });
    // The chip opened the popover on its own field.
    expect(document.activeElement).toBe(prompts);
    expect(sheet.querySelector(".map-sheet__problems")).toBeNull();

    fireEvent.change(prompts, { target: { value: "6" } });
    expect(storedDraft().cards?.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.items).toBe(6);
    expect(nodeEl(PROBE)).toHaveClass("is-insufficient", "has-error");
    expect(chip(PROBE, "cards.self_referential_probe.items")).toHaveClass("rec--insufficient");
    expect(chip(PROBE, "cards.self_referential_probe.items")).toHaveTextContent("↓");
    const problem = sheet.querySelector(".map-sheet__problem[data-code='design.below_floor']")!;
    expect(problem).toHaveTextContent("Blocks launch");
    expect(said()).toMatch(/^Prompts: 6 prompts\. Too few prompts\./);

    fireEvent.click(within(problem as HTMLElement).getByRole("button", { name: "Use recommended" }));
    expect(storedDraft().cards?.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.items ?? null).toBeNull();
    expect(nodeEl(PROBE)).toHaveClass("is-sufficient");
    expect(sheet.querySelector(".map-sheet__problems")).toBeNull();
  });

  it("draws the fields that belong to each kind of node", () => {
    renderMap();
    const fields = (id: string) => {
      const opener = nodeEl(id)!.querySelector<HTMLButtonElement>(".map-node__tool[aria-label^='Details of']") ?? title(id);
      fireEvent.click(opener);
      const sheet = popover()!;
      expect(sheet, id).toHaveAttribute("data-sheet", id);
      // The fields themselves: a field may name the parts inside it with paths of their own.
      return [...sheet.querySelectorAll<HTMLElement>(".map-fields .field[data-path]")].map((field) => field.dataset.path);
    };
    expect(fields("study")).toEqual(["title", "design.claim", "expectations"]);
    expect(fields("arm:qrng_unsupervised")).toEqual(["roster.cells", "roster.quantum", "roster.deterministic"]);
    expect(fields("arm:qrng_supervised")).toEqual(["roster.supervised"]);
    expect(fields(DECK)).toEqual(["roster.seeds", "roster.seed_values"]);
    expect(fields("tier0")).toEqual(["cards[].card_id"]);
    expect(fields("test:H3")).toEqual(["cards.self_referential_probe.items", "cards.self_referential_probe.k"]);
    expect(fields(`gate:neg:${LANE}`)).toEqual(["roster.seeds"]);
    expect(fields(`gate:pos:${LANE}`)).toEqual(["design.sensitivity_epsilon", "roster.epsilons"]);
    expect(fields("verdict:H3")).toEqual(["design.claim", "design.sensitivity_epsilon"]);
    // Completeness is the harness's: stated, with nothing to set and no way into the settings.
    expect(fields("gate:completeness")).toEqual([]);
    expect(within(popover()!).queryByRole("button", { name: "Open in all settings" })).not.toBeInTheDocument();
    expect(popover()!.querySelector(".map-sheet__settings .rec--locked")).toBeInTheDocument();
  });

  it("shows one popover at a time", () => {
    renderMap();
    fireEvent.click(title("family"));
    expect(popovers().map((sheet) => sheet.dataset.sheet)).toEqual(["family"]);
    fireEvent.click(title("verdict:H1"));
    expect(popovers().map((sheet) => sheet.dataset.sheet)).toEqual(["verdict:H1"]);
    expect(nodeEl("family")).not.toHaveClass("is-open");
    fireEvent.click(chip(DECK, "roster.seeds"));
    expect(popovers().map((sheet) => sheet.dataset.sheet)).toEqual([DECK]);
    fireEvent.click(noteEl("note:alpha")!);
    expect(popovers().map((sheet) => sheet.dataset.sheet)).toEqual(["note:alpha"]);
    expect(window.location.search).toBe("?open=note%3Aalpha");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("closes on Escape and gives the focus back to the node, from the close button and from inside a field", () => {
    renderMap();
    act(() => title("family").focus());
    key(title("family"), "Enter");
    fireEvent.click(title("family"));
    expect(document.activeElement).toBe(within(popover()!).getByRole("button", { name: "Close the details of Holm correction" }));
    key(document.activeElement!, "Escape");
    expect(popover()).toBeNull();
    expect(document.activeElement).toBe(title("family"));

    fireEvent.click(chip(DECK, "roster.seeds"));
    const seeds = within(popover()!).getByRole("spinbutton", { name: "Control seeds" });
    expect(document.activeElement).toBe(seeds);
    key(seeds, "Escape");
    expect(popover()).toBeNull();
    expect(document.activeElement).toBe(title(DECK));
    expect(window.location.search).toBe("");
  });

  it("leads to the same setting in the settings dialog", () => {
    renderMap();
    fireEvent.click(chip(DECK, "roster.seeds"));
    expect(asked()).toBe("closed");
    fireEvent.click(within(popover()!).getByRole("button", { name: "Open in all settings" }));
    expect(asked()).toBe(JSON.stringify({ path: "roster.seeds", problemsOnly: false }));
  });

  it("hangs under its node, in the world, and never leaves the viewport while the map is moved", () => {
    renderMap();
    fireEvent.click(title("family"));
    const sheet = popover()!;
    const { graph } = build(studyOneTemplate());
    const box = layoutGraph(graph).boxes["family"]!;
    expect(world().contains(sheet)).toBe(true);
    expect(sheet).toHaveAttribute("data-side", "below");
    expect(sheet.style.transform).toContain(`calc(${box.x}px + var(--pop-dx, 0px))`);
    expect(sheet.style.transform).toContain(`calc(${box.y + box.h + 20}px + var(--pop-dy, 0px))`);
    // Brought into view when it opened: it sits where it hangs.
    expect(px(sheet, "--pop-dx")).toBe(0);
    expect(px(sheet, "--pop-dy")).toBe(0);
    expect(sheet).not.toHaveClass("is-moved");
    const width = parseFloat(sheet.style.width.replace("min(", ""));
    const height = parseFloat(sheet.style.maxHeight.replace("min(", ""));
    const edges = () => {
      const view = camera();
      const scale = view.k * px(sheet, "--pop-s");
      const left = view.x + view.k * (box.x + px(sheet, "--pop-dx"));
      const top = view.y + view.k * (box.y + box.h + 20 + px(sheet, "--pop-dy"));
      return { left, top, right: left + Math.min(width, px(sheet, "--pop-max-w")) * scale, bottom: top + Math.min(height, px(sheet, "--pop-max-h")) * scale };
    };

    // Carried far off every side by the keyboard, and zoomed: it stops at the edge of the viewport.
    for (const [arrow, presses] of [["ArrowRight", 30], ["ArrowDown", 30], ["ArrowLeft", 60], ["ArrowUp", 60]] as const) {
      for (let press = 0; press < presses; press++) key(viewport(), arrow, { shiftKey: true });
      const at = edges();
      expect(at.left, arrow).toBeGreaterThanOrEqual(POPOVER_PAD - 0.1);
      expect(at.top, arrow).toBeGreaterThanOrEqual(POPOVER_PAD - 0.1);
      expect(at.right, arrow).toBeLessThanOrEqual(VIEW.w - POPOVER_PAD + 0.1);
      expect(at.bottom, arrow).toBeLessThanOrEqual(VIEW.h - POPOVER_PAD + 0.1);
      expect(sheet).toHaveClass("is-moved");
    }
    for (let press = 0; press < 4; press++) key(viewport(), "+");
    expect(camera().k).toBeGreaterThan(2);
    // It does not grow past its reading size.
    expect(px(sheet, "--pop-s") * camera().k).toBeCloseTo(1, 3);
    expect(edges().right).toBeLessThanOrEqual(VIEW.w - POPOVER_PAD + 0.1);
    expect(edges().bottom).toBeLessThanOrEqual(VIEW.h - POPOVER_PAD + 0.1);
  });

  it("opens an eval to its prompts as it unfolds, and puts them away as it folds", async () => {
    vi.mocked(api.cardItems).mockResolvedValue({
      card_id: "self_referential_probe", listable: true, source: "task_module", reason: null, total: 2, offset: 0, limit: 50, told: "told_nothing",
      items: [0, 1].map((index) => ({ index, prompt_id: `probe_${index}`, messages: [{ role: "user", content: `Probe ${index + 1}.` }], truncated: false })),
    });
    renderMap();
    fireEvent.click(title(PROBE));
    expect(title(PROBE)).toHaveAttribute("aria-expanded", "true");
    const sheet = popover()!;
    expect(sheet).toHaveAttribute("data-sheet", PROBE);
    const list = await within(sheet).findByRole("listbox", { name: "Prompts of T4-a Self-referential experience probe" });
    expect(within(list).getAllByRole("option")).toHaveLength(2);
    expect(within(sheet).getByRole("article")).toHaveAccessibleName("Prompt 1");
    expect(vi.mocked(api.cardItems)).toHaveBeenCalledWith("self_referential_probe", { offset: 0, limit: 50, told: "told_nothing" }, expect.anything());

    // From a prompt on to the hypothesis it feeds: that node's popover takes this one's place.
    fireEvent.click(within(sheet).getByRole("button", { name: "H3" }));
    expect(popovers().map((open) => open.dataset.sheet)).toEqual(["test:H3"]);
    // And back, from the test to the prompts of its eval.
    fireEvent.click(within(popover()!).getByRole("button", { name: "Read the prompts of T4-a Self-referential experience probe" }));
    expect(popovers().map((open) => open.dataset.sheet)).toEqual([PROBE]);

    fireEvent.click(title(PROBE));
    expect(title(PROBE)).toHaveAttribute("aria-expanded", "false");
    expect(popover()).toBeNull();
  });

  it("says that prompts need the run service where there is none, and still draws the map and the fields", async () => {
    renderMap();
    fireEvent.click(title(PROBE));
    const sheet = popover()!;
    expect(await within(sheet).findByRole("alert")).toHaveTextContent("Prompts need the run service. It is not reachable.");
    expect(within(sheet).getByRole("spinbutton", { name: "Prompts for self_referential_probe" })).toHaveValue(30);
    expect(document.querySelectorAll(".map-node").length).toBeGreaterThan(20);
  });
});

describe("study map: after a jump from a popover", () => {
  const zoomOut = () => fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
  /** Opens the eval's prompts, follows a prompt to the hypothesis it feeds, and closes that popover. */
  async function jumpToH3() {
    vi.mocked(api.cardItems).mockResolvedValue({
      card_id: "self_referential_probe", listable: true, source: "task_module", reason: null, total: 2, offset: 0, limit: 50, told: "told_nothing",
      items: [0, 1].map((index) => ({ index, prompt_id: `probe_${index}`, messages: [{ role: "user", content: `Probe ${index + 1}.` }], truncated: false })),
    });
    renderMap();
    fireEvent.click(title(PROBE));
    fireEvent.click(await within(popover()!).findByRole("button", { name: "H3" }));
    expect(popovers().map((open) => open.dataset.sheet)).toEqual(["test:H3"]);
    fireEvent.click(within(popover()!).getByRole("button", { name: /^Close the details of/ }));
    expect(popover()).toBeNull();
  }

  // One drawing of the map for all of it: these are the moves a reader makes one after the other.
  it("brings the node into view once, and then leaves the camera to the reader", async () => {
    await jumpToH3();
    // The jump itself: the test is on screen, at reading size.
    const at = camera();
    const box = layoutGraph(build(studyOneTemplate()).graph, new Set([PROBE]))!.boxes["test:H3"]!;
    expect(at.k).toBeGreaterThanOrEqual(1);
    expect(at.x + box.x * at.k).toBeGreaterThanOrEqual(0);
    expect(at.x + (box.x + box.w) * at.k).toBeLessThanOrEqual(VIEW.w);

    // Folding the map leaves the camera where it was put. It used to go back to the jumped-to node at every
    // change of the layout: a fold, an edit, a change of zoom tier.
    for (let press = 0; press < 12; press++) key(viewport(), "ArrowLeft", { shiftKey: true });
    const away = camera();
    fireEvent.click(screen.getByRole("button", { name: "Fold all" }));
    expect(camera()).toEqual(away);

    // So does an edit made elsewhere on the map.
    fireEvent.click(title("family"));
    const opened = camera();
    fireEvent.change(within(popover()!).getByRole("combobox", { name: /^False-finding rate \(α\)/ }), { target: { value: "0.01" } });
    expect(screen.getByTestId("alpha")).toHaveTextContent("0.01");
    expect(camera()).toEqual(opened);
    fireEvent.click(within(popover()!).getByRole("button", { name: /^Close the details of/ }));

    // And the map can be zoomed out to its story: each press zooms out, across the tier.
    const seen: number[] = [];
    for (let press = 0; press < 3; press++) { zoomOut(); seen.push(camera().k); }
    expect(seen[0]!).toBeLessThan(opened.k);
    expect(seen[1]!).toBeLessThan(seen[0]!);
    expect(seen[2]!).toBeLessThan(seen[1]!);
    expect(seen[2]!).toBeLessThan(0.55);
    expect(map()).toHaveAttribute("data-zoom", "story");
  }, 90_000);
});

describe("study map: a popover whose node an edit takes away", () => {
  it("stays open on an outcome that is unticked in it, by unfolding the eval the outcome folds into", () => {
    renderMap();
    const outcome = "endpoint:self_referential_probe/experience_claim_rate";
    expect(title(PROBE)).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(title(outcome));
    const sheet = () => popover()!;
    expect(sheet()).toHaveAttribute("data-sheet", outcome);
    const tick = () => within(within(sheet()).getByRole("group", { name: /^Outcomes that decide the study/ })).getByRole("checkbox", { name: /^Experience-claim rate/ });
    act(() => tick().focus());
    fireEvent.click(tick());
    // The outcome no longer decides the study, so it is no longer pinned in view. The popover in which that was
    // just set is still there, on the same outcome, with the focus where it was.
    expect(storedDraft().design?.confirmatory.map((ref) => ref.endpoint)).not.toContain("experience_claim_rate");
    expect(popovers().map((open) => open.dataset.sheet)).toEqual([outcome]);
    expect(nodeEl(outcome)).toBeInTheDocument();
    expect(title(PROBE)).toHaveAttribute("aria-expanded", "true");
    expect(tick()).not.toBeChecked();
    expect(document.activeElement).toBe(tick());
    expect(window.location.search).toBe(`?open=${encodeURIComponent(outcome)}`);
    // And it can be ticked again from there.
    fireEvent.click(tick());
    expect(storedDraft().design?.confirmatory.map((ref) => ref.endpoint)).toContain("experience_claim_rate");
  });

  it("is closed properly when the edit removes its node from the study", () => {
    renderMap();
    fireEvent.click(nodeEl("group:tier4")!.querySelector<HTMLButtonElement>("button[aria-label^='Details of']")!);
    expect(popover()).toHaveAttribute("data-sheet", "group:tier4");
    expect(window.location.search).toBe("?open=group%3Atier4");
    // Evals are unticked in the tier's own checklist until the study has few enough for the tiers not to be grouped.
    let unticked = 0;
    while (popover() !== null && unticked < 20) {
      const ticked = [...popover()!.querySelectorAll<HTMLInputElement>("section[aria-label='Tier 4'] input[type=checkbox]")].filter((box) => box.checked && !box.disabled);
      if (ticked.length === 0) break;
      act(() => ticked[0]!.focus());
      fireEvent.click(ticked[0]!);
      unticked += 1;
    }
    expect(nodeEl("group:tier4")).toBeNull();
    expect(popover()).toBeNull();
    // Nothing is left open unseen: the address is clean, it is said, and the focus is on a node of the map.
    expect(window.location.search).toBe("");
    expect(said()).toBe("The details of Tier 4 were closed: it is no longer part of the study.");
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toHaveClass("map-node__title");
    expect(document.activeElement!.closest<HTMLElement>(".map-node")!.dataset.station).toBe("prompts");
  });

  it("is closed with the node when the reader folds it away, and does not come back when the node is unfolded", async () => {
    renderMap();
    fireEvent.click(title(DECK));
    fireEvent.click(title("arm:prng_seed_7"));
    expect(popover()).toHaveAttribute("data-sheet", "arm:prng_seed_7");
    fireEvent.click(screen.getByRole("button", { name: "Fold all" }));
    expect(nodeEl("arm:prng_seed_7")).toBeNull();
    expect(popover()).toBeNull();
    expect(window.location.search).toBe("");
    expect(title(DECK)).toHaveAttribute("aria-expanded", "false");
    // The popover took the focus with it, and the "Fold all" button is gone too: the focus is back on the diagram.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(document.activeElement).toHaveClass("map-node__title");

    fireEvent.click(title(DECK));
    expect(nodeEl("arm:prng_seed_7")).toBeInTheDocument();
    expect(popover()).toBeNull();

    // The same when the node that holds it is folded by itself.
    fireEvent.click(title("arm:prng_seed_7"));
    expect(popover()).toHaveAttribute("data-sheet", "arm:prng_seed_7");
    key(title(DECK), "Escape");
    key(title(DECK), "Escape");
    expect(title(DECK)).toHaveAttribute("aria-expanded", "false");
    expect(popover()).toBeNull();
    expect(window.location.search).toBe("");
  });

  it("is closed properly when the zoom leaves its node out, and does not come back with the node", () => {
    renderMap();
    act(() => title("family").focus());
    fireEvent.click(title("family"));
    expect(popover()).toHaveAttribute("data-sheet", "family");
    for (let press = 0; press < 3; press++) fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(map()).toHaveAttribute("data-zoom", "story");
    expect(popover()).toBeNull();
    expect(window.location.search).toBe("");
    expect(said()).toBe("The details of Holm correction were closed: it is not drawn at this zoom.");
    for (let press = 0; press < 3; press++) fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(nodeEl("family")).toBeInTheDocument();
    expect(popover()).toBeNull();
  });
});

describe("study map: marginal notes", () => {
  it("pencils the settings that are no node of their own beside their station or node, with their state", () => {
    renderMap();
    const config = studyOneTemplate();
    const { graph, problems, recs } = build(config);
    const notes = buildNotes(graph, config, recs, problems);
    const boxes = layoutNotes(notes, layoutGraph(graph));
    expect([...document.querySelectorAll<HTMLElement>(".map-marginal")].map((note) => note.dataset.note)).toEqual(notes.filter((note) => boxes[note.id] !== undefined).map((note) => note.id));
    // In the world, so they move with the view; each where the layout says.
    for (const [id, box] of Object.entries(boxes)) {
      expect(world().contains(noteEl(id)), id).toBe(true);
      expect(noteEl(id)!.style.transform, id).toBe(`translate(${box.x}px, ${box.y}px)`);
    }
    expect(noteEl("note:alpha")).toHaveAttribute("data-anchor", "tests");
    expect(noteEl("note:gates")).toHaveAttribute("data-anchor", "gates");
    expect(noteEl("note:narrative")).toHaveAttribute("data-anchor", "verdict");
    expect(noteEl("note:people")).toHaveAttribute("data-anchor", "study");
    expect(noteEl("note:envelope:arm:qrng_unsupervised")).toHaveAttribute("data-anchor", "arm:qrng_unsupervised");
    expect(noteEl("note:sessions:arm:qrng_supervised")).toHaveAttribute("data-anchor", "arm:qrng_supervised");
    expect(noteEl(`note:judge:${PROBE}`)).toHaveAttribute("data-anchor", PROBE);

    // The state is a class, a glyph in a bordered pip, and words.
    const alpha = noteEl("note:alpha")!;
    expect(alpha).toHaveClass("is-recommended");
    expect(alpha.querySelector(".map-pip")).toHaveClass("rec--recommended");
    expect(alpha.querySelector(".map-pip")).toHaveTextContent("=");
    expect(alpha).toHaveAccessibleName("False-finding rate and correction: α = 0.05 · Holm · m = 3. Recommended value.");
    expect(noteEl("note:gates")).toHaveClass("is-locked");
    expect(noteEl("note:gates")!.querySelector(".map-pip")).toHaveClass("rec--locked");
    expect(noteEl("note:gates")).toHaveAccessibleName("Power and completeness: power 80% · completeness ≥ 90%. Set by the harness.");
    // A problem on a note's setting is a glyph and words as well.
    const people = noteEl("note:people")!;
    expect(people).toHaveClass("has-error");
    expect(people.querySelector(".map-marginal__problem--error")).toHaveTextContent("✕");
    expect(people.getAttribute("aria-label")).toContain("Replace the placeholder contact.");
  });

  it("opens the same popover as a node, with the fields of the note, and gives the focus back to the note", () => {
    renderMap();
    const people = noteEl("note:people")!;
    act(() => people.focus());
    fireEvent.click(people);
    expect(people).toHaveAttribute("aria-expanded", "true");
    const sheet = screen.getByRole("dialog", { name: "Authors and contact" });
    expect(sheet).toHaveClass("map-popover", "map-sheet--note-people");
    expect(world().contains(sheet)).toBe(true);
    expect(sheet.querySelector("[data-code='review.contact_placeholder']")).toHaveTextContent("Blocks launch");
    const contact = within(sheet).getByRole("textbox", { name: /^Contact/ });
    expect(contact.closest(".field")).toHaveAttribute("id", "map-field-contact");

    fireEvent.change(contact, { target: { value: "lab@example.org" } });
    expect(storedDraft().contact).toBe("lab@example.org");
    expect(people).toHaveTextContent("lab@example.org");
    expect(people).not.toHaveClass("has-error");
    // The error is gone from the study's node; its warnings stay. (The count was pinned at one before the setup
    // gained warnings about the pre-registration.)
    const left = build({ ...studyOneTemplate(), contact: "lab@example.org" }).problems.filter((problem) => problem.nodeId === "study");
    expect(left.every((problem) => problem.severity === "warning")).toBe(true);
    expect(left.length).toBeGreaterThanOrEqual(1);
    expect(nodeEl("study")!.querySelector(".map-node__problems")).toHaveTextContent(`!${left.length}`);
    expect(sheet.querySelector(".map-sheet__problems")).toBeNull();

    key(contact, "Escape");
    expect(popover()).toBeNull();
    expect(document.activeElement).toBe(noteEl("note:people"));
  });

  it("opens the judge on a judged eval, the sessions on the observed arm and the narrative at the verdicts", () => {
    renderMap();
    const fields = (id: string) => {
      fireEvent.click(noteEl(id)!);
      expect(popover()).toHaveAttribute("data-sheet", id);
      return [...popover()!.querySelectorAll<HTMLElement>(".map-fields .field[data-path]")].map((field) => field.dataset.path);
    };
    expect(fields(`note:judge:${PROBE}`)).toEqual(["judge.provider", "judge.model", "judge.max_tokens", "judge.thinking", "judge.temperature"]);
    expect(fields("note:sessions:arm:qrng_supervised")).toEqual(["supervised.display", "supervised.intention_timer_min", "supervised.refocus_s", "supervised.group", "supervised.settings_by_display.single"]);
    expect(fields("note:narrative")).toEqual(["narrative_model"]);
    expect(fields("note:alpha")).toEqual(["design.alpha"]);
    // What the harness fixes has no field: its popover states it, padlocked, with the reason.
    expect(fields("note:gates")).toEqual([]);
    expect([...popover()!.querySelectorAll(".map-sheet__settings dt")].map((term) => term.textContent)).toEqual(["Power", "Completeness"]);
    expect(fields("note:envelope:arm:qrng_unsupervised")).toEqual([]);
    expect(popover()!.querySelector(".map-sheet__settings")).toHaveTextContent("Amplifier zscore_mean, calibration 0, prefetch off.");

    // The session's timer, edited on the arm's note, is the study's.
    fireEvent.click(noteEl("note:sessions:arm:qrng_supervised")!);
    fireEvent.change(within(popover()!).getByRole("spinbutton", { name: /^Intention timer/ }), { target: { value: "5" } });
    expect(storedDraft().supervised?.intention_timer_min).toBe(5);
    expect(noteEl("note:sessions:arm:qrng_supervised")).toHaveTextContent("5 min timer");
  });

  it("is in the tab order with the node it hangs on, and at a station always", () => {
    renderMap();
    expect(noteEl("note:alpha")).toHaveAttribute("tabindex", "0");
    expect(noteEl(`note:judge:${PROBE}`)).toHaveAttribute("tabindex", "-1");
    act(() => title(PROBE).focus());
    expect(noteEl(`note:judge:${PROBE}`)).toHaveAttribute("tabindex", "0");
  });
});

describe("study map: lineage", () => {
  it("lights everything upstream and downstream of the node under the pointer and quiets the rest", () => {
    renderMap();
    const { graph } = build(studyOneTemplate());
    const drawn = visible(graph, NONE, "design");
    const line = lineage(drawn, "test:H3");
    expect(world()).not.toHaveClass("has-lineage");

    fireEvent.pointerEnter(nodeEl("test:H3")!);
    expect(world()).toHaveClass("has-lineage");
    expect(world()).toHaveAttribute("data-lineage", "test:H3");
    expect(nodeEl("test:H3")).toHaveClass("is-lit");
    for (const id of line.upstream.nodes) expect(nodeEl(id), id).toHaveClass("is-upstream");
    for (const id of line.downstream.nodes) expect(nodeEl(id), id).toHaveClass("is-downstream");
    expect(line.upstream.nodes).toEqual(expect.arrayContaining(["study", "arm:qrng_unsupervised", DECK, PROBE, "family"]));
    expect(line.downstream.nodes).toEqual(expect.arrayContaining(["verdict:H3"]));
    const lit = new Set(["test:H3", ...line.upstream.nodes, ...line.downstream.nodes]);
    for (const node of drawn.nodes) {
      expect(nodeEl(node.id)!.classList.contains("is-quiet"), node.id).toBe(!lit.has(node.id));
      // The ink follows its node.
      expect(inkEl(node.id)!.classList.contains("is-quiet"), node.id).toBe(!lit.has(node.id));
    }
    expect(nodeEl("test:H1")).toHaveClass("is-quiet");
    expect(nodeEl("verdict:H1")).toHaveClass("is-quiet");
    // The threads of the lineage are lit; the others are quiet.
    const edges = new Set([...line.upstream.edges, ...line.downstream.edges]);
    for (const edge of document.querySelectorAll<SVGGElement>(".map-edge")) {
      const mine = edges.has(edge.getAttribute("data-edge")!);
      expect(edge.classList.contains("is-lit"), edge.getAttribute("data-edge")!).toBe(mine);
      expect(edge.classList.contains("is-quiet")).toBe(!mine);
    }
    expect(document.querySelector(".map-edge[data-from='test:H3'].is-lit")).toBeInTheDocument();
    // A note follows what it hangs on.
    expect(noteEl(`note:judge:${PROBE}`)).toHaveClass("is-upstream");
    expect(noteEl("note:sessions:arm:qrng_supervised")).toHaveClass("is-quiet");

    fireEvent.pointerLeave(nodeEl("test:H3")!);
    expect(world()).not.toHaveClass("has-lineage");
    expect(world()).not.toHaveAttribute("data-lineage");
    expect(document.querySelectorAll(".is-quiet, .is-lit, .is-upstream, .is-downstream")).toHaveLength(0);
  });

  it("follows the keyboard from node to node, and keeps the lineage through an edit", () => {
    renderMap();
    act(() => title("study").focus());
    // A node that only holds the focus is not lit; one the keyboard moved to is.
    expect(world()).not.toHaveClass("has-lineage");
    key(title("study"), "End");
    const last = document.activeElement!.closest<HTMLElement>("[data-node]")!.dataset.node!;
    expect(world()).toHaveAttribute("data-lineage", last);
    expect(nodeEl(last)).toHaveClass("is-lit");
    expect(nodeEl("study")).toHaveClass("is-upstream");
    key(document.activeElement!, "Home");
    expect(world()).toHaveAttribute("data-lineage", "study");
    expect(nodeEl("study")).toHaveClass("is-lit");

    // The pointer takes it over; when it leaves, the keyboard's node is lit again.
    fireEvent.pointerEnter(nodeEl("verdict:H1")!);
    expect(world()).toHaveAttribute("data-lineage", "verdict:H1");
    expect(nodeEl("test:H1")).toHaveClass("is-upstream");
    fireEvent.pointerLeave(nodeEl("verdict:H1")!);
    expect(world()).toHaveAttribute("data-lineage", "study");

    // An open popover holds the lineage of its node, and an edit that redraws the nodes does not lose it.
    act(() => (document.activeElement as HTMLElement).blur());
    expect(world()).not.toHaveClass("has-lineage");
    fireEvent.click(chip(PROBE, "cards.self_referential_probe.items"));
    expect(world()).toHaveAttribute("data-lineage", PROBE);
    expect(nodeEl("test:H3")).toHaveClass("is-downstream");
    expect(nodeEl("test:H1")).toHaveClass("is-quiet");
    fireEvent.change(within(popover()!).getByRole("spinbutton", { name: "Prompts for self_referential_probe" }), { target: { value: "6" } });
    expect(nodeEl(PROBE)).toHaveClass("is-insufficient", "is-lit");
    expect(nodeEl("test:H3")).toHaveClass("is-downstream");
    expect(nodeEl("test:H1")).toHaveClass("is-quiet");
  });

  it("shows the rule that leads to a verdict on its stamp, and the sentence of a node beside it", () => {
    renderMap();
    const stamps = [...nodeEl("verdict:H3")!.querySelectorAll<HTMLElement>(".map-stamp")];
    expect(stamps).toHaveLength(6);
    const rule = (verdict: string) => {
      const stamp = stamps.find((candidate) => candidate.dataset.stamp === verdict)!;
      const button = stamp.querySelector<HTMLButtonElement>(".map-stamp__button")!;
      const tip = stamp.querySelector<HTMLElement>(".map-stamp__rule")!;
      expect(tip).toHaveAttribute("role", "tooltip");
      expect(button.getAttribute("aria-describedby")).toBe(tip.id);
      return tip;
    };
    expect(rule("effect")).toHaveTextContent("EffectReturned when: pD < alpha.");
    expect(rule("equivalent")).toHaveTextContent("Equivalent: unreachable");
    expect(rule("equivalent")).toHaveTextContent("The study registered no equivalence claim.");
    expect(rule("equivalent")).toHaveTextContent("The claim includes equivalence, a margin is registered, pE < alpha, and the positive control was detected.");
    expect(rule("invalid")).toHaveTextContent("Returned when one of these holds: Fewer than 90% of the planned pairs were analysed. Or: A seed-vs-seed control");
    // A press on a stamp opens the verdict, which has every rule.
    fireEvent.click(stamps[0]!.querySelector("button")!);
    expect(popover()).toHaveAttribute("data-sheet", "verdict:H3");

    const { graph } = build(studyOneTemplate());
    const note = nodeEl("family")!.querySelector(".map-node__note")!;
    expect(note).toHaveTextContent(graph.nodes.find((node) => node.id === "family")!.describes);
    expect(note).toHaveAttribute("aria-hidden", "true");
  });
});

describe("study map: the pointer", () => {
  it("leans the nodes near the pointer toward it and the notes away, in one frame, and settles them when it leaves", () => {
    const frames = holdFrames();
    renderMap();
    frames.draw();
    frames.request.mockClear();
    const { graph, problems, recs } = build(studyOneTemplate());
    const layout = layoutGraph(graph);
    const box = layout.boxes[DECK]!;
    const leaning = () => [...world().querySelectorAll<HTMLElement>(".map-node, .map-marginal")].filter((element) => element.style.getPropertyValue("--near-x") !== "");

    // Just right of the seeded deck, level with its middle.
    fireEvent.pointerMove(viewport(), { ...onScreen({ x: box.x + box.w + 30, y: box.y + box.h / 2 }), pointerType: "mouse" });
    fireEvent.pointerMove(viewport(), { ...onScreen({ x: box.x + box.w + 30, y: box.y + box.h / 2 }), pointerType: "mouse" });
    // Many moves, one frame; nothing is written before it.
    expect(frames.request).toHaveBeenCalledTimes(1);
    expect(leaning()).toHaveLength(0);
    frames.draw();
    const deck = nodeEl(DECK)!;
    expect(px(deck, "--near-x")).toBeGreaterThan(0);
    expect(px(deck, "--near-x")).toBeLessThanOrEqual(2.5);
    expect(Math.abs(px(deck, "--near-y"))).toBeLessThan(0.01);
    // The ink moves with its node.
    expect(inkEl(DECK)!.style.getPropertyValue("--near-x")).toBe(deck.style.getPropertyValue("--near-x"));
    // Only what is near answers: the verdicts across the map do not.
    expect(leaning().every((element) => !element.matches("[data-station='verdict']"))).toBe(true);
    expect(nodeEl("verdict:H1")!.style.getPropertyValue("--near-x")).toBe("");
    // Nothing loops: the frame asked for no other.
    expect(frames.request).toHaveBeenCalledTimes(1);

    // A note shies the other way.
    const notes = layoutNotes(buildNotes(graph, studyOneTemplate(), recs, problems), layout);
    const noteBox = notes["note:people"]!;
    fireEvent.pointerMove(viewport(), { ...onScreen({ x: noteBox.x + noteBox.w + 30, y: noteBox.y + noteBox.h / 2 }), pointerType: "mouse" });
    frames.draw();
    expect(px(noteEl("note:people")!, "--near-x")).toBeLessThan(0);
    expect(noteEl("note:people")!.style.getPropertyValue("--near-r")).toMatch(/^-.*deg$/);
    // The deck is out of reach now and has settled.
    expect(deck.style.getPropertyValue("--near-x")).toBe("");

    fireEvent.pointerLeave(viewport());
    frames.draw();
    expect(leaning()).toHaveLength(0);
    expect(world().querySelectorAll("[style*='--near']")).toHaveLength(0);
  });

  it("does not answer a finger", () => {
    const frames = holdFrames();
    renderMap();
    frames.draw();
    frames.request.mockClear();
    const box = layoutGraph(build(studyOneTemplate()).graph).boxes[DECK]!;
    fireEvent.pointerMove(viewport(), { ...onScreen({ x: box.x + box.w + 30, y: box.y + box.h / 2 }), pointerType: "touch" });
    frames.draw();
    expect(frames.request).not.toHaveBeenCalled();
    expect(world().querySelectorAll("[style*='--near']")).toHaveLength(0);
  });

  it("has no pointer motion at all under reduced motion", () => {
    stubMedia((query) => query.includes("prefers-reduced-motion"));
    const frames = holdFrames();
    const listening = vi.spyOn(HTMLElement.prototype, "addEventListener");
    renderMap();
    frames.draw();
    frames.request.mockClear();
    expect(map()).not.toHaveClass("is-animated");
    // No listener of its own for the pointer's moves is added to the viewport (React listens at the root of the page, not there).
    const onViewport = listening.mock.calls.filter((_, call) => listening.mock.contexts[call] === viewport()).map(([type]) => type);
    expect(onViewport).toContain("wheel");
    expect(onViewport).not.toContain("pointermove");
    expect(onViewport).not.toContain("pointerleave");
    const box = layoutGraph(build(studyOneTemplate()).graph).boxes[DECK]!;
    fireEvent.pointerMove(viewport(), { ...onScreen({ x: box.x + box.w + 30, y: box.y + box.h / 2 }), pointerType: "mouse" });
    frames.draw();
    expect(frames.request).not.toHaveBeenCalled();
    expect(world().querySelectorAll("[style*='--near']")).toHaveLength(0);
    // The lineage is not motion: it still answers.
    fireEvent.pointerEnter(nodeEl("test:H3")!);
    expect(world()).toHaveClass("has-lineage");
  });
});

describe("study map: the outline", () => {
  it("opens the same settings under an entry, one at a time, and edits the study from there", async () => {
    stubMedia((query) => query.includes("max-width: 760px"));
    renderMap();
    expect(map()).toHaveAttribute("data-mode", "outline");
    const entry = (id: string) => document.querySelector<HTMLElement>(`.map-outline__node[data-node="${id}"]`)!;
    const toggle = (id: string) => entry(id).querySelector<HTMLButtonElement>(":scope > .map-outline__row .map-outline__toggle")!;
    const opened = () => [...document.querySelectorAll<HTMLElement>(".map-sheet--inline")].map((sheet) => sheet.dataset.sheet);

    expect(toggle("family")).toHaveAccessibleName("Open Holm correction");
    fireEvent.click(toggle("family"));
    expect(toggle("family")).toHaveAttribute("aria-expanded", "true");
    expect(opened()).toEqual(["family"]);
    fireEvent.change(within(entry("family")).getByRole("combobox", { name: /^False-finding rate \(α\)/ }), { target: { value: "0.01" } });
    expect(storedDraft().design?.alpha).toBe(0.01);
    expect(entry("family")).toHaveAttribute("data-state", "custom");

    // A station's notes are listed at the station, a node's on its entry; each opens its own settings.
    const station = document.querySelector<HTMLElement>(".map-outline__station[data-station='tests']")!;
    const alpha = station.querySelector<HTMLButtonElement>(".map-outline__note[data-note='note:alpha']")!;
    expect(alpha).toHaveTextContent("α = 0.01");
    fireEvent.click(alpha);
    expect(opened()).toEqual(["note:alpha"]);
    expect(alpha).toHaveAttribute("aria-expanded", "true");
    const judge = entry(PROBE).querySelector<HTMLButtonElement>(`.map-outline__note[data-note="note:judge:${PROBE}"]`)!;
    fireEvent.click(judge);
    expect(opened()).toEqual([`note:judge:${PROBE}`]);
    expect(within(entry(PROBE)).getByRole("combobox", { name: /^Judge model/ })).toBeInTheDocument();

    // Escape puts them away and returns to what opened them.
    act(() => within(entry(PROBE)).getByRole("combobox", { name: /^Judge model/ }).focus());
    key(document.activeElement!, "Escape");
    expect(opened()).toEqual([]);
    expect(document.activeElement).toBe(judge);

    // An eval opens to its prompts here too; without the run service that is said.
    fireEvent.click(toggle(PROBE));
    expect(opened()).toEqual([PROBE]);
    await waitFor(() => expect(within(entry(PROBE)).getByRole("alert")).toHaveTextContent("Prompts need the run service. It is not reachable."));
    fireEvent.click(toggle(PROBE));
    expect(opened()).toEqual([]);
  });

  it("leaves a list the reader unfolded as it is when an entry in it is closed", () => {
    stubMedia((query) => query.includes("max-width: 760px"));
    renderMap();
    const entry = (id: string) => document.querySelector<HTMLElement>(`.map-outline__node[data-node="${id}"]`)!;
    const toggle = (id: string) => entry(id).querySelector<HTMLButtonElement>(":scope > .map-outline__row .map-outline__toggle")!;
    const fold = () => entry("group:tier4").querySelector<HTMLDetailsElement>(":scope > details.map-outline__fold")!;
    /** Folds or unfolds a list as a press on its summary does. */
    const set = (details: HTMLDetailsElement, open: boolean) => act(() => { details.open = open; fireEvent(details, new Event("toggle")); });
    const inside = "card:introspection_scales";

    // A long list starts folded.
    expect(fold().querySelector("summary")).toHaveTextContent(/^\d+ parts of Tier 4$/);
    expect(fold().open).toBe(false);
    set(fold(), true);
    fireEvent.click(toggle(inside));
    expect(fold().open).toBe(true);
    expect(entry(inside).querySelector(".map-sheet--inline")).toBeInTheDocument();
    // Closing the entry used to fold the whole list shut under the reader.
    fireEvent.click(toggle(inside));
    expect(entry(inside).querySelector(".map-sheet--inline")).toBeNull();
    expect(fold().open).toBe(true);
    // Escape from inside an entry returns the focus to its button, which is still on the page.
    fireEvent.click(toggle(inside));
    key(entry(inside).querySelector(".map-sheet--inline")!, "Escape");
    expect(fold().open).toBe(true);
    expect(document.activeElement).toBe(toggle(inside));

    // The reader's own fold is kept, and opening an entry that is in the list unfolds it.
    set(fold(), false);
    expect(fold().open).toBe(false);
    act(() => toggle(inside).click());
    expect(fold().open).toBe(true);

    // The same for the outcomes listed aside at their station.
    const aside = document.querySelector<HTMLDetailsElement>(".map-outline__station[data-station='measures'] > details.map-outline__fold")!;
    expect(aside.querySelector("summary")).toHaveTextContent(/^\d+ more, reported without a verdict$/);
    set(aside, true);
    const outcome = aside.querySelector<HTMLElement>(".map-outline__node")!.dataset.node!;
    fireEvent.click(toggle(outcome));
    fireEvent.click(toggle(outcome));
    expect(aside.open).toBe(true);
  });

  it("says nothing of verdicts in the outline of a study on the fixed Study 1 plan", () => {
    stubMedia((query) => query.includes("max-width: 760px"));
    renderMap({ ...studyOneTemplate(), design: null });
    const aside = document.querySelector<HTMLDetailsElement>(".map-outline__station[data-station='measures'] > details.map-outline__fold")!;
    expect(aside.querySelector("summary")).toHaveTextContent(/^\d+ more$/);
    expect(document.querySelector(".map-outline")!.textContent).not.toMatch(/without a verdict|for exploration|· exploratory/);
  });
});
