import type { CardItem, CardItemsResponse } from "@qr-evals/contracts";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { ApiError, api } from "../../lib/api";
import { studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import type { MapNode } from "../../lib/map/graph";
import { PROMPT_ROWS_MOUNTED, PROMPT_ROW_H } from "../../lib/map/prompts";
import { build, fixtureCase } from "../../lib/map/test-fixtures";
import { PromptDeck } from "./Prompts";
import { PromptQueries } from "./usePrompts";

vi.setConfig({ testTimeout: 30_000 });

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

type Told = CardItemsResponse["told"];
const NEWLINE = String.fromCharCode(10);

/** One prompt as the service lists it. */
function item(index: number, more: Partial<CardItem> = {}): CardItem {
  return { index, prompt_id: `prompt_${String(index).padStart(4, "0")}`, messages: [{ role: "user", content: `Question ${index + 1}?${NEWLINE}Reply with only A or B.` }], truncated: false, ...more };
}

/** A service that lists `total` prompts, a page at a time, and remembers what it was asked. */
function listing(total: number, make: (index: number, told: Told) => CardItem = (index) => item(index), hasTold = false) {
  const asked: { cardId: string; offset: number; limit: number; told: Told }[] = [];
  vi.spyOn(api, "cardItems").mockImplementation(async (cardId, page = {}) => {
    const offset = page.offset ?? 0, limit = page.limit ?? 50, told = page.told ?? null;
    asked.push({ cardId, offset, limit, told });
    const count = Math.max(0, Math.min(limit, total - offset));
    return {
      card_id: cardId, listable: true, source: "task_module", reason: null, total, offset, limit,
      told: hasTold ? told ?? "told_nothing" : null,
      items: Array.from({ length: count }, (_, at) => make(offset + at, told)),
    };
  });
  return asked;
}

/** A service that says the prompts of every eval cannot be listed, for a reason. */
function unlisted(source: CardItemsResponse["source"], reason: string | null = null) {
  return vi.spyOn(api, "cardItems").mockImplementation(async (cardId, page = {}) => ({
    card_id: cardId, listable: false, source, reason, total: 0, offset: page.offset ?? 0, limit: page.limit ?? 50, told: null, items: [],
  }));
}

function cardNode(cardId: string, config: WizardStudyConfig = studyOneTemplate()) {
  const { graph } = build(config);
  const node = graph.nodes.find((candidate) => candidate.id === `card:${cardId}`);
  if (!node) throw new Error(`the study has no eval ${cardId}`);
  return { graph, node };
}

function renderDeck(cardId: string, config?: WizardStudyConfig, onOpen?: (id: string) => void) {
  const { graph, node } = cardNode(cardId, config);
  render(<PromptQueries><PromptDeck graph={graph} node={node} onOpen={onOpen} /></PromptQueries>);
  return { graph, node };
}

const deck = () => document.querySelector<HTMLElement>(".prompt-deck")!;
const rows = () => [...document.querySelectorAll<HTMLElement>(".prompt-row")];
const list = (node: MapNode) => screen.findByRole("listbox", { name: `Prompts of ${node.label}` });
/** Scrolls the list as a wheel would: jsdom lays nothing out, so the position is set by hand. */
function scrollTo(element: HTMLElement, top: number) {
  Object.defineProperty(element, "scrollTop", { configurable: true, writable: true, value: top });
  fireEvent.scroll(element);
}
/** A study that runs only the first `items` prompts of an eval. */
function withPrompts(cardId: string, items: number): WizardStudyConfig {
  const config = studyOneTemplate();
  return { ...config, cards: config.cards.map((selection) => (selection.card_id === cardId ? { ...selection, overrides: { ...selection.overrides, items } } : selection)) };
}

describe("prompt list", () => {
  it("lists an eval's prompts with their place, id and first line, and shows the picked one in full", async () => {
    const asked = listing(30);
    const { node } = renderDeck("creative_diversity");
    expect(deck()).toHaveAttribute("data-prompts", "loading");
    expect(within(deck()).getByRole("status")).toHaveTextContent("Reading the prompts from the run service.");

    const box = await list(node);
    expect(deck()).toHaveAttribute("data-prompts", "listed");
    expect(asked).toEqual([{ cardId: "creative_diversity", offset: 0, limit: 50, told: null }]);
    expect(rows()).toHaveLength(30);
    expect(rows()[0]).toHaveTextContent("1");
    expect(rows()[0]!.querySelector(".prompt-row__id")).toHaveTextContent("prompt_0000");
    expect(rows()[0]!.querySelector(".prompt-row__line")).toHaveTextContent("Question 1?");
    expect(document.querySelector(".prompt-list__count")).toHaveTextContent("All 30 prompts are listed.");

    // The first prompt is picked to begin with; another is picked by a press, or with the arrow keys.
    const pane = () => screen.getByRole("article");
    expect(pane()).toHaveAccessibleName("Prompt 1");
    expect(rows()[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.click(rows()[2]!);
    expect(pane()).toHaveAccessibleName("Prompt 3");
    expect(within(pane()).getByRole("heading", { name: "User" })).toBeInTheDocument();
    expect(pane().querySelector(".prompt-turn__text")!.textContent).toBe(`Question 3?${NEWLINE}Reply with only A or B.`);
    expect(pane().querySelector("code")).toHaveTextContent("prompt_0002");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(pane()).toHaveAccessibleName("Prompt 4");
    expect(box).toHaveAttribute("aria-activedescendant", rows()[3]!.id);
    fireEvent.keyDown(box, { key: "Home" });
    expect(pane()).toHaveAccessibleName("Prompt 1");
    expect(pane().querySelector(".prompt-pane__cut")).toBeNull();
  });

  it("mounts at most forty rows of four thousand, and reads the next page when the end comes near", async () => {
    const asked = listing(4000);
    const { node } = renderDeck("thurstonian_self_outcomes");
    const box = await list(node);
    // One page is read, not two: nothing is asked for until the list is scrolled.
    expect(asked.map((request) => request.offset)).toEqual([0]);
    expect(rows().length).toBeLessThanOrEqual(PROMPT_ROWS_MOUNTED);
    expect(document.querySelector<HTMLElement>(".prompt-list__rows")!.style.height).toBe(`${50 * PROMPT_ROW_H}px`);
    expect(document.querySelector(".prompt-list__count")).toHaveTextContent("50 of 4,000 prompts are listed so far.");

    scrollTo(box, 45 * PROMPT_ROW_H);
    await waitFor(() => expect(asked.map((request) => request.offset)).toEqual([0, 50]));
    await waitFor(() => expect(document.querySelector(".prompt-list__count")).toHaveTextContent("100 of 4,000 prompts are listed so far."));
    expect(rows().length).toBeLessThanOrEqual(PROMPT_ROWS_MOUNTED);
    // The rows in view are the ones around the scroll position, each at its own place.
    const indices = rows().map((row) => Number(row.dataset.index));
    expect(indices).toContain(45);
    expect(indices).not.toContain(0);
    for (const row of rows()) expect(row.style.top).toBe(`${Number(row.dataset.index) * PROMPT_ROW_H}px`);

    // The button reads on for who does not scroll.
    fireEvent.click(screen.getByRole("button", { name: "Read more" }));
    await waitFor(() => expect(asked.map((request) => request.offset)).toEqual([0, 50, 100]));
    expect(asked.every((request) => request.limit === 50)).toBe(true);
  });

  it("asks once for a next page that fails, and again only when the reader asks", async () => {
    // The first page is read; every later one fails after a moment, as a service that went away would.
    const asked: number[] = [];
    let fails = true;
    vi.spyOn(api, "cardItems").mockImplementation(async (cardId, page = {}) => {
      const offset = page.offset ?? 0, limit = page.limit ?? 50;
      asked.push(offset);
      if (offset > 0 && fails) {
        await new Promise((resolve) => setTimeout(resolve, 10));
        throw new ApiError("upstream error", 503, "unavailable");
      }
      return { card_id: cardId, listable: true, source: "task_module", reason: null, total: 4000, offset, limit, told: null, items: Array.from({ length: limit }, (_, at) => item(offset + at)) };
    });
    const { node } = renderDeck("thurstonian_self_outcomes");
    const box = await list(node);
    expect(asked).toEqual([0]);

    scrollTo(box, 45 * PROMPT_ROW_H);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Prompts need the run service. It answered with an error.");
    // The list stays scrolled to its end. It used to ask again after every failure, for as long as it stayed there.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    expect(asked).toEqual([0, 50]);
    // What was read is still listed, and the alert says where to ask again: it has no button of its own to do it.
    expect(rows().length).toBeGreaterThan(0);
    expect(document.querySelector(".prompt-list__count")).toHaveTextContent("50 of 4,000 prompts are listed so far.");
    expect(alert).toHaveTextContent("The prompts read so far are still listed. The button under the list asks for the next ones again.");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();

    // One press, one request. It fails again, and again nothing is asked behind the reader's back.
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(asked).toEqual([0, 50, 50]));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    expect(asked).toEqual([0, 50, 50]);

    // Once the service answers, the press reads the page, and reading on by scrolling works again.
    fails = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(document.querySelector(".prompt-list__count")).toHaveTextContent("100 of 4,000 prompts are listed so far."));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read more" })).toBeInTheDocument();
    scrollTo(box, 95 * PROMPT_ROW_H);
    await waitFor(() => expect(asked).toEqual([0, 50, 50, 50, 100]));
  });

  it("marks the prompts the study runs and those beyond its chosen count", async () => {
    listing(30);
    const { node } = renderDeck("creative_diversity", withPrompts("creative_diversity", 20));
    await list(node);
    expect(document.querySelector(".prompt-deck__summary")).toHaveTextContent("The study runs the first 20 of 30 prompts, in the eval's own order, each answered 10 times by every arm.");
    const inside = rows().filter((row) => row.dataset.inStudy === "true");
    const beyond = rows().filter((row) => row.dataset.inStudy === "false");
    expect(inside.map((row) => Number(row.dataset.index))).toEqual(Array.from({ length: 20 }, (_, index) => index));
    expect(beyond.map((row) => Number(row.dataset.index))).toEqual(Array.from({ length: 10 }, (_, index) => 20 + index));
    // Said three ways: the class (a struck line), the words on the row, and the line drawn where the study stops.
    for (const row of inside) { expect(row).toHaveClass("is-in"); expect(row.querySelector(".prompt-row__out")).toBeNull(); }
    for (const row of beyond) { expect(row).toHaveClass("is-beyond"); expect(row.querySelector(".prompt-row__out")).toHaveTextContent("not in this study"); }
    expect(rows().filter((row) => row.classList.contains("is-first-beyond")).map((row) => Number(row.dataset.index))).toEqual([20]);

    const pane = () => screen.getByRole("article");
    expect(pane().querySelector(".prompt-pane__standing")).toHaveTextContent("In this study");
    expect(pane().querySelector(".prompt-strip")).toBeInTheDocument();
    fireEvent.click(beyond[0]!);
    expect(pane()).toHaveClass("is-beyond");
    expect(pane().querySelector(".prompt-pane__standing")).toHaveTextContent("Not in this study");
    expect(pane().querySelector(".prompt-strip")).toBeNull();
    expect(pane().querySelector(".prompt-strip__none")).toHaveTextContent("The study stops before this prompt: it runs the first 20 in the eval's own order.");
  });

  it("switches the told cell of a told/actual eval, and asks for that cell", async () => {
    const said: Record<string, string> = { told_nothing: "Answer as you normally would.", told_qrng: "Your tokens are sampled from a quantum generator.", told_prng: "Your tokens are sampled from a seeded generator." };
    const asked = listing(30, (index, told) => item(index, { messages: [{ role: "user", content: `${said[told ?? "told_nothing"]}${NEWLINE}${NEWLINE}Probe ${index + 1}.` }] }), true);
    const { node } = renderDeck("self_referential_probe");
    await list(node);
    const cells = within(screen.getByRole("radiogroup", { name: "Told cell" }));
    expect(cells.getAllByRole("radio").map((radio) => radio.parentElement!.textContent)).toEqual(["Told nothing", "Told quantum", "Told pseudo-random"]);
    expect(cells.getByRole("radio", { name: "Told nothing" })).toBeChecked();
    expect(asked).toEqual([{ cardId: "self_referential_probe", offset: 0, limit: 50, told: "told_nothing" }]);
    expect(screen.getByRole("article").querySelector(".prompt-turn__text")).toHaveTextContent("Answer as you normally would.");
    // A preamble every prompt opens with is left out of the rows, so they can be told apart.
    expect(rows()[1]!.querySelector(".prompt-row__line")).toHaveTextContent("Probe 2.");
    expect(document.querySelector(".prompt-deck__summary")).toHaveTextContent("each answered 24 times in each of the 3 told cells by every arm.");

    fireEvent.click(rows()[4]!);
    fireEvent.click(cells.getByRole("radio", { name: "Told quantum" }));
    await waitFor(() => expect(screen.getByRole("article").querySelector(".prompt-turn__text")).toHaveTextContent("Your tokens are sampled from a quantum generator."));
    expect(asked.at(-1)).toEqual({ cardId: "self_referential_probe", offset: 0, limit: 50, told: "told_qrng" });
    expect(document.querySelector(".prompt-told__cell.is-on")).toHaveTextContent("Told quantum");
    expect(document.querySelector(".prompt-told__says")).toHaveTextContent("The prompt says the answer is sampled from a hardware quantum random number generator.");
    // The prompt that was picked stays picked across the cells.
    expect(screen.getByRole("article")).toHaveAccessibleName("Prompt 5");

    // An eval without told cells has no switch.
    cleanup();
    listing(30);
    const plain = renderDeck("creative_diversity");
    await list(plain.node);
    expect(screen.queryByRole("radiogroup", { name: "Told cell" })).not.toBeInTheDocument();
  });

  it("says where a prompt was cut", async () => {
    listing(3, (index) => (index === 1 ? item(index, { truncated: true, messages: [{ role: "system", content: "Short." }, { role: "user", content: "x".repeat(6000) }] }) : item(index)));
    const { node } = renderDeck("creative_diversity");
    await list(node);
    expect(document.querySelector(".prompt-pane__cut")).toBeNull();
    fireEvent.click(rows()[1]!);
    expect(screen.getByRole("note")).toHaveTextContent("This preview is cut at 6,000 characters per message. The run sends the full text.");
    expect(within(screen.getByRole("article")).getAllByRole("heading").map((heading) => heading.textContent)).toEqual(["System", "User"]);
  });

  it("follows a prompt to the arms that answer it, the outcome read off the answers and the hypothesis that feeds", async () => {
    listing(30, undefined, true);
    const opened: string[] = [];
    const { node, graph } = renderDeck("self_referential_probe", undefined, (id) => opened.push(id));
    await list(node);
    const strip = screen.getByRole("list", { name: "What happens to this prompt" });
    const steps = within(strip).getAllByRole("listitem");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toHaveTextContent("Asked");
    expect(steps[0]).toHaveTextContent("Each of the 5 arms answers it 24 times in each of the 3 told cells: the quantum arm, 3 seeded controls and the observed quantum arm.");
    expect(steps[1]).toHaveTextContent("Read off");
    expect(steps[1]).toHaveTextContent("Experience-claim rate, which decides the study.");
    expect(steps[1]).toHaveTextContent("reported without a verdict.");
    const test = graph.nodes.find((candidate) => candidate.ref.type === "test" && candidate.ref.hypothesis.card_id === "self_referential_probe")!;
    const hypothesis = test.ref.type === "test" ? test.ref.hypothesis : null;
    expect(steps[2]).toHaveTextContent(`Feeds${hypothesis!.id}`);
    // The hypothesis is a way on to its test.
    fireEvent.click(within(steps[2]!).getByRole("button", { name: hypothesis!.id }));
    expect(opened).toEqual([test.id]);

    // An eval that decides nothing says so instead of naming a hypothesis.
    cleanup();
    listing(30);
    const plain = renderDeck("creative_diversity");
    await list(plain.node);
    expect(within(screen.getByRole("list", { name: "What happens to this prompt" })).getAllByRole("listitem")[2]).toHaveTextContent("No hypothesis: no outcome of this eval decides the study.");
  });

  it("does not call the outcomes of a study on the fixed Study 1 plan undecided", async () => {
    listing(30);
    const { node } = renderDeck("creative_diversity", fixtureCase("study1_legacy").config);
    await list(node);
    const steps = within(screen.getByRole("list", { name: "What happens to this prompt" })).getAllByRole("listitem");
    // The fixed plan is not drawn on the map: nothing is said of verdicts, and the strip says what that plan does with the outcomes.
    expect(steps[1]!.textContent).not.toMatch(/without a verdict|decides/);
    expect(steps[2]!.textContent).toBe("FeedsAll 7 outcomes are secondary outcomes of the fixed Study 1 plan: they are tested with Benjamini–Hochberg correction at 0.05 across the secondary outcomes of tier 1. The map does not draw that plan.");
    expect(steps[2]!.textContent).not.toMatch(/No hypothesis/);
  });

  it("promises no test of the fixed Study 1 plan to a Tier 4 eval that plan only reports", async () => {
    const legacy = fixtureCase("study1_legacy").config;
    const feeds = () => within(screen.getByRole("list", { name: "What happens to this prompt" })).getAllByRole("listitem")[2]!.textContent ?? "";
    listing(30);
    await list(renderDeck("introspection_scales", legacy).node);
    // the strip used to say of every eval that its prompts feed "the tests of the fixed Study 1 plan"
    expect(feeds()).toMatch(/^FeedsAll \d+ outcomes are reported as exploratory by the fixed Study 1 plan, with no significance threshold\. The map does not draw that plan\.$/);
    expect(feeds()).not.toMatch(/\btests?\b|\btested\b/);

    // an eval of which the plan tests one outcome and reports the others says which is which
    cleanup();
    listing(30);
    const { graph, node } = renderDeck("source_guessing", legacy);
    await list(node);
    const label = (endpoint: string) => graph.nodes.find((candidate) => candidate.id === `endpoint:source_guessing/${endpoint}`)!.label;
    expect(feeds()).toContain(`Feeds${label("guess_accuracy")} is a secondary outcome of the fixed Study 1 plan: it is tested with Benjamini–Hochberg correction at 0.05 across the secondary outcomes of tier 4. `);
    expect(feeds()).toMatch(/ are reported as exploratory by the fixed Study 1 plan, with no significance threshold\. The map does not draw that plan\.$/);
    expect(feeds().split("are reported as exploratory")[0]).toContain(label("d_prime"));
    expect(feeds().split(" is a secondary outcome")[0]).not.toContain(label("d_prime"));

    // one of the plan's four primary outcomes is said to be one
    cleanup();
    listing(30);
    const bench = renderDeck("unpredictabench", legacy);
    await list(bench.node);
    const ks = bench.graph.nodes.find((candidate) => candidate.id === "endpoint:unpredictabench/ks100_pass_rate")!.label;
    expect(feeds()).toContain(`Feeds${ks} is a primary outcome of the fixed Study 1 plan: it is tested with Holm correction at α = 0.05 across the primary outcomes of that plan.`);
  });
});

describe("prompts that cannot be listed", () => {
  const cases: [CardItemsResponse["source"], string | null, string][] = [
    ["derived_at_run", null, "The prompts are built from this study's own generations at run time."],
    ["remote_dataset", null, "The prompts are downloaded at run time from the pinned revision of a remote dataset."],
    ["inspect_task", null, "The prompts are inside the Inspect task and cannot be read here."],
    ["no_prompts", null, "This measure reads the tokens the study generates; it has no prompts of its own."],
    ["error", "TaskModuleError: <repo>/evals/creative_diversity/task.py has no build_items.", "The prompts could not be read: TaskModuleError: <repo>/evals/creative_diversity/task.py has no build_items."],
  ];

  it.each(cases)("says in a sentence why, for the source %s", async (source, reason, sentence) => {
    unlisted(source, reason);
    renderDeck("creative_diversity");
    await waitFor(() => expect(deck()).toHaveAttribute("data-prompts", "unlisted"));
    expect(deck()).toHaveAttribute("data-source", source);
    expect(deck().querySelector(`.prompt-deck__state--${source}`)!.textContent).toBe(sentence);
    // It is a state of the eval, not a failure: no list, no alert, nothing left reading.
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("says that the run service is not reachable, and tries again when asked", async () => {
    const call = vi.spyOn(api, "cardItems").mockRejectedValue(new TypeError("Failed to fetch"));
    const { node } = renderDeck("creative_diversity");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Prompts need the run service. It is not reachable.");
    expect(deck()).toHaveAttribute("data-prompts", "failed");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    // It is not asked again behind the reader's back.
    expect(call).toHaveBeenCalledTimes(1);

    listing(12);
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await list(node);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(rows()).toHaveLength(12);
  });

  it("tells a service that does not know the eval from one that refused the request", async () => {
    vi.spyOn(api, "cardItems").mockRejectedValue(new ApiError("Unknown card", 404, "not_found", null, "card_id"));
    renderDeck("creative_diversity");
    expect(await screen.findByRole("alert")).toHaveTextContent("The run service does not know this eval, so its prompts cannot be listed.");
    cleanup();
    vi.spyOn(api, "cardItems").mockRejectedValue(new ApiError("The run service answered with a prompt list this page cannot read.", 502, "contract_mismatch"));
    renderDeck("creative_diversity");
    expect(await screen.findByRole("alert")).toHaveTextContent("Prompts need the run service. It answered with an error.");
  });

  it("does not ask for the prompts of an eval the registry does not have", () => {
    const call = vi.spyOn(api, "cardItems");
    const config = studyOneTemplate();
    renderDeck("not_in_the_registry", { ...config, cards: [...config.cards, { card_id: "not_in_the_registry" }] });
    expect(deck()).toHaveAttribute("data-prompts", "unknown");
    expect(deck()).toHaveTextContent("This eval is not in the registry of this page, so its prompts cannot be listed.");
    expect(call).not.toHaveBeenCalled();
  });
});

describe("api.cardItems", () => {
  const answer = (body: unknown, status = 200) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })));
  const page: CardItemsResponse = { card_id: "creative_diversity", listable: true, source: "task_module", reason: null, total: 1, offset: 0, limit: 50, told: null, items: [item(0)] };

  it("asks the card-items route for a page and returns what the contract describes", async () => {
    answer(page);
    await expect(api.cardItems("creative_diversity")).resolves.toEqual(page);
    const first = String(vi.mocked(fetch).mock.calls[0]![0]);
    expect(first).toMatch(/\/api\/cards\/creative_diversity\/items\?offset=0&limit=50$/);

    await api.cardItems("self referential/probe", { offset: 50, limit: 25, told: "told_qrng" });
    const second = String(vi.mocked(fetch).mock.calls[1]![0]);
    expect(second).toMatch(/\/api\/cards\/self%20referential%2Fprobe\/items\?offset=50&limit=25&told=told_qrng$/);
  });

  it("refuses an answer that is not a page of prompts, and passes a refusal of the service on", async () => {
    answer({ ...page, source: "somewhere_else" });
    await expect(api.cardItems("creative_diversity")).rejects.toMatchObject({ name: "ApiError", status: 502, code: "contract_mismatch" });
    answer({ card_id: "creative_diversity", items: "none" });
    await expect(api.cardItems("creative_diversity")).rejects.toBeInstanceOf(ApiError);
    answer({ error: { message: "limit must be at most 100.", param: "query.limit" } }, 400);
    await expect(api.cardItems("creative_diversity", { limit: 500 })).rejects.toMatchObject({ status: 400, message: "limit must be at most 100.", param: "query.limit" });
  });
});
