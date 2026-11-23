import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, vi } from "vitest";
import { api } from "../../lib/api";
import { studyOneTemplate } from "../../lib/config-store";
import { STEPS } from "../../routes/configure/steps";
import { StudyProvider } from "../../routes/configure/StudyContext";
import { resetBrowser, storeDraft } from "../study/study-test-kit";
import { RULES_KEY } from "./Inspector";
import { tabStops } from "./MapSheet";
import { newMapMemory } from "./model";
import { StudyMap } from "./StudyMap";

// What the keyboard needs of the map beyond moving from node to node: a popover that keeps the Tab key, a camera
// that follows the focus to a note, and rules whose symbols are said in words. These live in a file of their own
// so the long interaction file does not grow: its tests run back to back without a pause, and a worker that goes
// a minute without one loses touch with the test runner.

vi.setConfig({ testTimeout: 30_000 });

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

/** Stands in for the setup shell: it holds the map's memory. */
function Shell() {
  const memory = useRef(newMapMemory());
  return <StudyMap memory={memory} />;
}

function renderMap() {
  storeDraft(studyOneTemplate());
  return render(<StudyProvider steps={STEPS}><Shell /></StudyProvider>);
}

const world = () => document.querySelector<HTMLElement>(".map-world")!;
const nodeEl = (id: string) => document.querySelector<HTMLElement>(`.map-node[data-node="${id}"]`);
const noteEl = (id: string) => document.querySelector<HTMLButtonElement>(`.map-marginal[data-note="${id}"]`);
const title = (id: string) => nodeEl(id)!.querySelector<HTMLButtonElement>(".map-node__title")!;
const popover = () => document.querySelector<HTMLElement>(".map-popover");
const camera = () => ({
  x: parseFloat(world().style.getPropertyValue("--map-x")),
  y: parseFloat(world().style.getPropertyValue("--map-y")),
  k: parseFloat(world().style.getPropertyValue("--map-k")),
});

describe("study map: the keyboard in a popover", () => {
  it("keeps the Tab key inside while it is open, in both directions", () => {
    renderMap();
    act(() => title("family").focus());
    fireEvent.click(title("family"));
    const sheet = popover()!;
    const stops = tabStops(sheet);
    expect(stops.length).toBeGreaterThan(3);
    // The close button comes first; nothing disabled, hidden or folded away is a stop.
    expect(stops[0]).toBe(within(sheet).getByRole("button", { name: "Close the details of Holm correction" }));
    for (const stop of stops) {
      expect(stop).not.toBeDisabled();
      expect(stop.closest("details:not([open])") === null || stop.tagName === "SUMMARY").toBe(true);
    }
    const last = stops[stops.length - 1]!;
    act(() => last.focus());
    expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(stops[0]);
    expect(fireEvent.keyDown(stops[0]!, { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(last);
    // In between the browser moves the focus itself.
    act(() => stops[1]!.focus());
    expect(fireEvent.keyDown(stops[1]!, { key: "Tab" })).toBe(true);
    expect(popover()).toBe(sheet);
  });

  it("explains the symbols of the decision rules on a verdict", () => {
    renderMap();
    act(() => title("verdict:H3").focus());
    fireEvent.click(title("verdict:H3"));
    const sheet = popover()!;
    expect(sheet.querySelector(".map-sheet__rules")).toHaveTextContent("pD < alpha.");
    expect(sheet.querySelector(".map-sheet__key")).toHaveTextContent(RULES_KEY);
    expect(RULES_KEY).toContain("pD is the p-value of the test for a difference");
    expect(RULES_KEY).toContain("alpha is the false-finding rate");
  });
});

describe("study map: the keyboard and the camera", () => {
  it("is brought into view when the keyboard reaches it, and left where it is under the mouse", () => {
    renderMap();
    const gates = noteEl("note:gates")!;
    const box = gates.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/)!;
    const left = Number(box[1]);
    const shown = () => { const view = camera(); const x = view.x + left * view.k; return x >= 0 && x <= VIEW.w; };
    // At the opening view the note of the Gates station lies beyond the right edge.
    expect(shown()).toBe(false);
    // A press of the mouse on it moves nothing: the map must not slide under the pointer.
    fireEvent.keyDown(document.body, { key: "Tab" });
    fireEvent.pointerDown(gates, { pointerType: "mouse", button: 0 });
    const before = camera();
    act(() => gates.focus());
    expect(camera()).toEqual(before);
    act(() => gates.blur());
    // The Tab key does, even pressed outside the diagram: the focus is never on a note that cannot be seen.
    fireEvent.keyDown(document.body, { key: "Tab" });
    act(() => gates.focus());
    expect(shown()).toBe(true);
    // And on a node the same way: the keyboard arriving from the toolbar finds its node in view.
    const far = "verdict:H3";
    fireEvent.pointerDown(document.body, { pointerType: "mouse", button: 0 });
    act(() => title("study").focus());
    const start = camera();
    fireEvent.keyDown(document.body, { key: "Tab" });
    act(() => title(far).focus());
    expect(camera()).not.toEqual(start);
  });
});
