import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import studyOneJson from "../../../../../packages/contracts/fixtures/study1.json";
import { api, ApiError } from "../../lib/api";
import { CONFIG_STORAGE_KEY, encodeShareConfig, parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import { FILE_MARK_STORAGE_KEY, toStudyYaml } from "../../lib/study-file";
import { UNREADABLE_STORAGE_KEY } from "../../lib/config-store";
import { rosterFromArms } from "../../lib/roster";
import { clearSession, setSession } from "../../lib/session";
import { authResponse } from "../../lib/session-fixture";
import { pinnedTemplate } from "../../components/study/study-test-kit";
import { Configure } from "./index";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

// Each step of Study 1 is a large page for jsdom, and the gate runs beside other suites: the default five seconds is too tight.
vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() }); window.history.pushState({}, "", "/"); clearSession();
  // The Review step asks the run service for the study's hashes. No test here is about them (Review.test.tsx is), and none may reach a real server.
  vi.spyOn(api, "prereg").mockRejectedValue(new TypeError("Failed to fetch"));
  // A save asks the server to read the written text back. No test here reaches a real server: by default it cannot be asked, and the page's own check answers.
  vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const STEP_LABELS = ["Compare", "Measure", "Design", "Instruments", "Review & launch"];
/** For a wait on something that follows a redraw of the whole page: beside other suites that takes more than the default second. */
const SLOW = { timeout: 10_000 };

/** The rail button of a step, whatever state it is in. */
const rail = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}: `) });
const open = (label: string) => fireEvent.click(rail(label));
/** The heading of the step on screen. */
const heading = () => document.querySelector<HTMLElement>(".wizard-step h2");
const saved = () => JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) ?? "{}") as Partial<WizardStudyConfig>;
/** The settings panels on screen, by id, in page order. */
const panels = (container: HTMLElement) => [...container.querySelectorAll(".wizard-step .settings-panel")].map((panel) => panel.id.replace(/^panel-/, ""));
/** The report under the study bar, once it says what `data-report` names ("loaded", "failed", "note"). The file state in the bar is a status too, so the report is found by what it is. */
const report = async (kind: string) => await waitFor(() => {
  const found = document.querySelector<HTMLElement>(`.setup-shell > .load-report[data-report="${kind}"]`);
  if (!found) throw new Error(`No "${kind}" report under the study bar.`);
  return found;
}, { timeout: 10_000 });
/** The note of the launch on the Review step: a status of its own, beside the bar's file state. */
const launchNote = () => document.querySelector<HTMLElement>(".wizard-step .validation-note[role='status']");
/** The study bar, and an item of its "More" menu. */
const bar = () => within(screen.getByRole("region", { name: "Study" }));
const more = (item: string) => { fireEvent.click(bar().getByRole("button", { name: "More" })); fireEvent.click(screen.getByRole("menuitem", { name: item })); };
/** The file state shown in the study bar. */
const fileState = () => document.querySelector<HTMLElement>(".study-bar .file-state");
/** The settings dialog, open. */
const settings = () => screen.getByRole("dialog", { name: "Everything in this study" });

/** Study 1 as a loaded legacy file: no design block. */
function legacy(): WizardStudyConfig {
  const parsed = parseStudyConfig(studyOneJson);
  if (!parsed) throw new Error("study1.json does not match StudyConfig.");
  return parsed;
}

/**
 * A study with nothing for the Instruments step: the token-level evals and one eval with prompts of its own,
 * none of which a judge scores, and no supervised sessions. The eval with prompts gives the token-level
 * outcomes replies to be tested on; without it the study would plan no data for them.
 */
function bare(): WizardStudyConfig {
  const template = studyOneTemplate();
  const tokenLevel = (cardId: string) => cardId.startsWith("tier0_") || cardId === "introspection_scales";
  return {
    ...template,
    supervised: null,
    arms: template.arms.filter((arm) => arm.observer_mode !== "supervised"),
    cards: template.cards.filter((selection) => tokenLevel(selection.card_id)),
    design: template.design ? { ...template.design, confirmatory: template.design.confirmatory.filter((ref) => tokenLevel(ref.card_id)) } : null,
  };
}

function renderWith(config: WizardStudyConfig) {
  window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config));
  return render(<Configure />);
}

describe("setup steps", () => {
  it("offers five steps, numbered from their place, and opens on the first", () => {
    render(<Configure />);
    const steps = within(screen.getByRole("navigation", { name: "Setup steps" })).getAllByRole("button");
    expect(steps.map((step) => step.querySelector(".step-rail__label")?.textContent)).toEqual(STEP_LABELS);
    expect(screen.getByText("01 / 05")).toBeInTheDocument();
    expect(rail("Compare")).toHaveAttribute("aria-current", "step");
    expect(heading()).toHaveTextContent("What is compared");
    expect(screen.getByText("Protocol 01")).toBeInTheDocument();
    expect(document.querySelector(".setup-shell")).not.toBeNull();
  });

  it("draws each step from its panels and walks through all five with Continue", () => {
    const { container } = render(<Configure />);
    expect(panels(container)).toEqual(["comparison", "conditioning"]);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(heading()).toHaveTextContent("What is measured");
    expect(heading()).toHaveFocus();
    expect(panels(container)).toEqual(["evals", "prompts"]);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(heading()).toHaveTextContent("What would count as a finding");
    expect(panels(container)).toEqual(["claim", "outcomes", "alpha", "prompts", "positive-control", "margins", "expectations"]);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(heading()).toHaveTextContent("Judge and sessions");
    expect(panels(container)).toEqual(["judge", "sessions"]);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(heading()).toHaveTextContent("Review and launch");
    expect(screen.getByText("Protocol 05")).toBeInTheDocument();
    expect(panels(container)).toEqual(["people"]);
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(heading()).toHaveTextContent("Judge and sessions");
    expect(heading()).toHaveFocus();
  });

  it("keeps the study title in the header and saves an edit to it", async () => {
    render(<Configure />);
    const title = screen.getByLabelText("Study title");
    expect(title).toHaveValue(studyOneTemplate().title);
    fireEvent.change(title, { target: { value: "Prompt or Physics?" } });
    open("Review & launch");
    expect(screen.getByLabelText("Study title")).toHaveValue("Prompt or Physics?");
    await waitFor(() => expect(saved().title).toBe("Prompt or Physics?"));
  });
});

describe("step rail", () => {
  it("says how each step stands in words and in a class, not in colour alone", () => {
    render(<Configure />);
    // Study 1 as it ships: no expectations recorded yet, no model checkpoint and no hashes of the session
    // assets (three warnings, each on its step), and a placeholder contact (an error).
    expect(rail("Compare")).toHaveAccessibleName("Compare: 1 to check");
    expect(rail("Compare")).toHaveClass("has-warnings", "is-current");
    expect(rail("Measure")).toHaveAccessibleName("Measure: clear");
    expect(rail("Measure")).toHaveClass("is-clear");
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to check");
    expect(rail("Design")).toHaveClass("has-warnings");
    expect(rail("Design")).not.toHaveClass("is-current");
    expect(rail("Instruments")).toHaveAccessibleName("Instruments: 1 to check");
    expect(rail("Instruments")).toHaveClass("has-warnings");
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: 1 to fix");
    expect(rail("Review & launch")).toHaveClass("has-errors");
    // The same words are on the button for a reader who sees it.
    expect(within(rail("Design")).getByText("1 to check")).toBeInTheDocument();
    expect(within(rail("Review & launch")).getByText("1 to fix")).toBeInTheDocument();
  });

  it("clears a step when its problems are dealt with", () => {
    render(<Configure />);
    open("Design");
    fireEvent.change(screen.getByLabelText("What do you expect to see?"), { target: { value: "No difference between the arms." } });
    expect(rail("Design")).toHaveAccessibleName("Design: clear");
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: clear");
    expect(rail("Review & launch")).toHaveClass("is-clear");
  });

  it("never blocks the way through the steps: a step with an error can be left by Continue and by the rail", () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" }));
    // A study without an arm is not one the browser stores: that is the second thing to check here, beside the unpinned checkpoint.
    expect(rail("Compare")).toHaveAccessibleName("Compare: 1 to fix, 2 to check");
    expect(rail("Compare")).toHaveClass("has-errors");
    const listed = within(screen.getByRole("list", { name: "Problems on this step" }));
    expect(listed.getByText("Tick at least one model and sampling preset.")).toBeInTheDocument();
    expect(listed.getByText(/^This browser does not store the draft while the setting arms is outside what a study can hold\./)).toBeInTheDocument();
    expect(listed.getByText("Blocks launch")).toBeInTheDocument();
    expect(screen.getByText("1 problem on this step blocks the launch. You can continue and come back to it.")).toBeInTheDocument();

    const next = screen.getByRole("button", { name: "Continue" });
    expect(next).toBeEnabled();
    fireEvent.click(next);
    expect(heading()).toHaveTextContent("What is measured");
    open("Review & launch");
    expect(heading()).toHaveTextContent("Review and launch");
    // Only the launch is blocked.
    expect(screen.getByRole("button", { name: "Launch run" })).toBeDisabled();
  });
});

describe("problems lead to their step", () => {
  it("lists a problem under the step it belongs to and opens that step on its setting", () => {
    render(<Configure />);
    fireEvent.click(screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" }));
    open("Review & launch");
    const row = screen.getByText("Tick at least one model and sampling preset.").closest("li");
    if (!row) throw new Error("The problem is not a list item.");
    expect(row).toHaveAttribute("data-code", "compare.no_cell");
    expect(within(row).getByText("· Compare")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Open Compare" }));
    expect(heading()).toHaveTextContent("What is compared");
    expect(rail("Compare")).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" })).toHaveFocus();
  });

  it("counts a prompt count out of bounds on Measure and points at that eval's row", () => {
    render(<Configure />);
    open("Measure");
    fireEvent.change(screen.getByLabelText("Prompts for creative_diversity"), { target: { value: "100000" } });
    expect(rail("Measure")).toHaveAccessibleName("Measure: 1 to fix");
    const listed = within(screen.getByRole("list", { name: "Problems on this step" }));
    expect(listed.getByText(/prompts must be between 1 and/)).toBeInTheDocument();
    fireEvent.click(listed.getByRole("button", { name: "Show the setting" }));
    expect(screen.getByLabelText("Prompts for creative_diversity")).toHaveFocus();
    // The table it sits in was folded away; it is unfolded to show it.
    expect(screen.getByLabelText("Prompts for creative_diversity").closest("details")).toHaveAttribute("open");
  });

  it("counts a missing judge model on Instruments and a placeholder contact on Review", () => {
    render(<Configure />);
    open("Instruments");
    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "__custom__" } });
    // The empty judge model blocks, and while it is empty the draft is not stored: with the unpinned session assets, two things to check.
    expect(rail("Instruments")).toHaveAccessibleName("Instruments: 1 to fix, 2 to check");
    expect(within(screen.getByRole("list", { name: "Problems on this step" })).getByText("Choose a judge model.")).toBeInTheDocument();

    open("Review & launch");
    const all = within(screen.getByRole("list", { name: "Problems in this study" }));
    // Two rows lead to Instruments: the missing judge model, and the notice that the draft is not stored while it is missing.
    expect(within(all.getByText("Choose a judge model.").closest("li")!).getByRole("button", { name: "Open Instruments" })).toBeInTheDocument();
    expect(all.getAllByRole("button", { name: "Open Instruments" })).toHaveLength(2);
    const contact = all.getByText("Replace the placeholder contact.").closest("li");
    if (!contact) throw new Error("The problem is not a list item.");
    fireEvent.click(within(contact).getByRole("button", { name: "Show the setting" }));
    expect(screen.getByLabelText("Contact")).toHaveFocus();
  });

  it("offers a problem's one-click fix on Review", () => {
    renderWith(legacy());
    open("Review & launch");
    const row = screen.getByText("This draft uses the fixed Study 1 plan.").closest("li");
    if (!row) throw new Error("The problem is not a list item.");
    expect(within(row).getByText("Warning")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Declare a design" }));
    expect(screen.queryByText("This draft uses the fixed Study 1 plan.")).not.toBeInTheDocument();
    expect(saved().design).toMatchObject({ claim: "difference", alpha: 0.05 });
  });
});

describe("Instruments step", () => {
  it("is offered only when the study has a judged eval or supervised sessions", () => {
    const { container } = renderWith(bare());
    const labels = () => within(screen.getByRole("navigation", { name: "Setup steps" })).getAllByRole("button").map((step) => step.querySelector(".step-rail__label")?.textContent);
    expect(labels()).toEqual(["Compare", "Measure", "Design", "Review & launch"]);
    expect(screen.getByText("01 / 04")).toBeInTheDocument();
    // The steps after it close up: Review is the fourth of four.
    open("Review & launch");
    expect(screen.getByText("Protocol 04")).toBeInTheDocument();

    open("Compare");
    fireEvent.click(screen.getByRole("checkbox", { name: "Supervised sessions" }));
    expect(labels()).toEqual(STEP_LABELS);
    open("Instruments");
    // No eval is scored by the judge, so only the sessions are set here.
    expect(panels(container)).toEqual(["sessions"]);
    expect(screen.getByLabelText("Display")).toBeInTheDocument();

    open("Compare");
    fireEvent.click(screen.getByRole("checkbox", { name: "Supervised sessions" }));
    expect(labels()).toEqual(["Compare", "Measure", "Design", "Review & launch"]);
  });

  it("starts the judge on the current Haiku and saves the settings with the study", async () => {
    render(<Configure />);
    open("Instruments");
    expect(screen.getByLabelText("Judge model")).toHaveValue("claude-haiku-5-5");
    expect(screen.getByLabelText("Longest answer, in tokens")).toHaveValue(1024);
    expect(screen.getByLabelText("Extended thinking")).toHaveValue("disabled");
    expect(screen.getByLabelText("Temperature")).toHaveValue("unset");
    expect(screen.queryByLabelText("Temperature value (0 to 1)")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "claude-sonnet-5-5" } });
    fireEvent.change(screen.getByLabelText("Extended thinking"), { target: { value: "model_default" } });
    fireEvent.change(screen.getByLabelText("Longest answer, in tokens"), { target: { value: "4096" } });
    fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "set" } });
    fireEvent.change(screen.getByLabelText("Temperature value (0 to 1)"), { target: { value: "0.3" } });
    await waitFor(() => expect(saved().judge).toMatchObject({ model: "claude-sonnet-5-5", thinking: "model_default", max_tokens: 4096, temperature: 0.3 }));

    fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("Exact model id"), { target: { value: "claude-sonnet-4-6" } });
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toContain("claude-sonnet-4-6"));
  });
});

describe("a study without a design block", () => {
  it("loads from a file and shows the built-in plan with the way to declare a design", async () => {
    const study = legacy();
    vi.spyOn(api, "parseConfig").mockResolvedValue({ config: study, config_hash: "c".repeat(64), unknown_cards: [] });
    const { container } = render(<Configure />);
    open("Design");
    fireEvent.change(screen.getByLabelText("Load study file"), { target: { files: [new File(["title: Study 1\n"], "study1.yaml", { type: "text/yaml" })] } });
    // The Design step is on screen while the file loads; the report is found by its own mark, not by a role query over that page.
    expect(await report("loaded")).toHaveTextContent(`Loaded study1.yaml: ${study.arms.length} arms, ${study.cards.length} evals. No design block: the analysis plan will be the built-in Study 1 plan until you add one on the Design step.`);
    // A loaded study starts at the first step.
    expect(rail("Compare")).toHaveAttribute("aria-current", "step");
    await waitFor(() => expect(saved().design ?? null).toBeNull());
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to check");
    expect(within(screen.getByLabelText("Evidence preview")).getByText("This draft uses the fixed Study 1 plan, so no sample size is rated here.")).toBeInTheDocument();

    open("Design");
    expect(panels(container)).toEqual(["claim", "expectations"]);
    expect(within(screen.getByRole("list", { name: "Problems on this step" })).getByText("This draft uses the fixed Study 1 plan.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Declare a design" }));
    expect(panels(container)).toEqual(["claim", "outcomes", "alpha", "prompts", "positive-control", "margins", "expectations"]);
    expect(within(screen.getByLabelText("Evidence preview")).getByText("Hypotheses")).toBeInTheDocument();
    await waitFor(() => expect(saved().design).toMatchObject({ claim: "difference" }));
  });
});

describe("study file and draft", () => {
  it("loads a study file through the server and reports what it holds", async () => {
    const thesis = { ...studyOneTemplate(), title: "Prompt or Physics?" };
    const parseConfig = vi.spyOn(api, "parseConfig").mockResolvedValue({ config: thesis, config_hash: "c".repeat(64), unknown_cards: [] });
    render(<Configure />);
    const file = new File(["title: Prompt or Physics?\n"], "study.yaml", { type: "text/yaml" });
    fireEvent.change(screen.getByLabelText("Load study file"), { target: { files: [file] } });
    expect(await report("loaded")).toHaveTextContent(`Loaded study.yaml: ${thesis.arms.length} arms, ${thesis.cards.length} evals, 3 hypotheses.`);
    expect(parseConfig).toHaveBeenCalledWith({ text: "title: Prompt or Physics?\n" });
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toContain("Prompt or Physics?"));
  });

  it("says why a study file was not loaded and keeps the current study", async () => {
    // A refusal is the server's answer to the file (an HTTP 400), as the API client reports one.
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: arms: Field required (3 problem(s) in all)", 400));
    render(<Configure />);
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toBeNull());
    const before = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    fireEvent.change(screen.getByLabelText("Load study file"), { target: { files: [new File(["x"], "broken.yaml")] } });
    const failed = await report("failed");
    expect(failed).toHaveAttribute("role", "alert");
    expect(failed).toHaveTextContent('"broken.yaml" was not loaded: not a valid study configuration: arms: Field required');
    expect(failed).toHaveTextContent("The setting it names is arms. The study you are editing was not changed.");
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(before);
  });

  it("does not load a study the server read but this page cannot hold", async () => {
    const fromNewerServer = { ...studyOneTemplate(), title: "From a newer server", a_field_this_page_does_not_know: true };
    vi.spyOn(api, "parseConfig").mockResolvedValue({ config: fromNewerServer, config_hash: "c".repeat(64), unknown_cards: [] });
    render(<Configure />);
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toBeNull());
    const before = window.localStorage.getItem(CONFIG_STORAGE_KEY);
    fireEvent.change(screen.getByLabelText("Load study file"), { target: { files: [new File(["x"], "newer.yaml")] } });
    expect(await report("failed")).toHaveTextContent('"newer.yaml" was read by the server but this page is out of date. Reload the page and try again.');
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toBe(before);
    expect(before).not.toContain("From a newer server");
  });

  it("puts the Study 1 template in place of the study and returns to the first step", async () => {
    renderWith({ ...bare(), title: "A small study" });
    open("Review & launch");
    more("Start from Study 1 template");
    // The study on screen is in no file, so the page asks before it is replaced.
    const ask = within(screen.getByRole("dialog", { name: "Replace the study you are editing?" }));
    expect(screen.getByLabelText("Study title")).toHaveValue("A small study");
    fireEvent.click(ask.getByRole("button", { name: "Replace" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(rail("Compare")).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText("Study title")).toHaveValue(studyOneTemplate().title);
    expect(rail("Instruments")).toBeInTheDocument();
    await waitFor(() => expect(saved().title).toBe(studyOneTemplate().title));
  });

  it("says so when the saved draft could not be read and the template took its place", async () => {
    window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify({ title: "A draft from another version" }));
    render(<Configure />);
    expect(screen.getByRole("alert")).toHaveTextContent("Your saved draft no longer matches this version and was replaced by the Study 1 template.");
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toContain(studyOneTemplate().title));
  });

  it("opens a share link once: the address bar loses it and the study becomes the draft", async () => {
    const shared = { ...studyOneTemplate(), title: "Sent by a colleague" };
    window.history.pushState({}, "", `/?c=${encodeShareConfig(shared) ?? ""}`);
    render(<Configure />);
    expect(window.location.search).toBe("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    open("Review & launch");
    // The title is in the study bar as well; the ledger of the Review step is what is read here.
    expect(within(document.querySelector<HTMLElement>(".review-ledger")!).getByText("Sent by a colleague")).toBeInTheDocument();
    await waitFor(() => expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).toContain("Sent by a colleague"));
  });

  it("names the setting that keeps a draft from being stored, as an error that leads to it and blocks the launch", () => {
    renderWith({ ...pinnedTemplate(), contact: "lab@example.org", expectations: "No difference." });
    expect(document.querySelector(".study-bar .validity-pill")).toHaveTextContent("Ready");
    // A judge answer of 32 tokens is outside what a study can hold: the page keeps it on screen and cannot store it.
    open("Instruments");
    fireEvent.change(screen.getByLabelText("Longest answer, in tokens"), { target: { value: "32" } });
    expect(saved().judge?.max_tokens).toBe(1024);
    // It is said at once, on the step of the setting, in the rail and in the study bar: not only on the last step.
    expect(rail("Instruments")).toHaveAccessibleName("Instruments: 1 to fix, 1 to check");
    const onStep = within(screen.getByRole("list", { name: "Problems on this step" }));
    const row = onStep.getByText("The setting judge.max_tokens must be at least 64.").closest("li")!;
    expect(row).toHaveAttribute("data-code", "draft.invalid");
    expect(row).toHaveClass("problem--error");
    expect(document.querySelector(".study-bar .validity-pill")).toHaveTextContent("1 thing to fix");
    // That the draft is not being stored meanwhile is said right beside it, with what is lost and how it ends,
    // and in the bar's file state, which is on screen on every step and over the map.
    const unkept = onStep.getByText("This browser does not store the draft while the setting judge.max_tokens is outside what a study can hold. Every edit made since then is lost when the page is closed or reloaded. Fix that setting and the draft is stored again. A study file saved in the meantime will not load back.").closest("li")!;
    expect(unkept).toHaveAttribute("data-code", "draft.not_saved");
    expect(unkept).toHaveClass("problem--warning");
    expect(fileState()).toHaveTextContent("Not saved to a file yet · edits are not kept in this browser");
    expect(fileState()).toHaveAttribute("data-draft-stored", "false");
    // Edits made meanwhile are indeed not stored, which is why this must not pass for ready or for kept.
    open("Design");
    fireEvent.change(screen.getByLabelText("What do you expect to see?"), { target: { value: "Typed while the draft could not be stored." } });
    expect(saved().expectations).toBe("No difference.");
    expect(fileState()).toHaveTextContent("Not saved to a file yet · edits are not kept in this browser");

    open("Review & launch");
    expect(screen.getByRole("button", { name: "Launch run" })).toBeDisabled();
    expect(screen.queryByText("This draft is not valid yet and was not saved.")).not.toBeInTheDocument();
    fireEvent.click(within(document.querySelector<HTMLElement>('.problem[data-code="draft.invalid"]')!).getByRole("button", { name: "Open Instruments" }));
    expect(screen.getByLabelText("Longest answer, in tokens")).toHaveFocus();
    fireEvent.change(screen.getByLabelText("Longest answer, in tokens"), { target: { value: "2048" } });
    expect(rail("Instruments")).toHaveAccessibleName("Instruments: clear");
    // With the setting repaired the whole study is stored, the edit made meanwhile included, and nothing says otherwise any more.
    expect(saved()).toMatchObject({ expectations: "Typed while the draft could not be stored.", judge: { max_tokens: 2048 } });
    expect(fileState()?.textContent).toBe("Not saved to a file yet");
    expect(fileState()).toHaveAttribute("data-draft-stored", "true");
    expect(document.querySelector('.problem[data-code="draft.not_saved"]')).toBeNull();
    open("Review & launch");
    expect(screen.getByRole("button", { name: "Launch run" })).toBeEnabled();
  });

  it("shows a setting that only the settings dialog draws there: an empty title leads to the title field", () => {
    renderWith({ ...pinnedTemplate(), contact: "lab@example.org", expectations: "No difference." });
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "" } });
    // The empty title blocks, and the draft is not stored while it is empty.
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: 1 to fix, 1 to check");
    open("Review & launch");
    const row = screen.getByText("The setting title is empty.").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Show the setting" }));
    const title = within(settings()).getByLabelText("Study title");
    expect(title).toHaveFocus();
    fireEvent.change(title, { target: { value: "Named again" } });
    expect(screen.queryByText("The setting title is empty.")).not.toBeInTheDocument();
  });

  it("says that the browser did not store a draft it could have stored, as a warning with what to do about it", () => {
    Object.defineProperty(window, "localStorage", { configurable: true, value: { ...memoryStorage(), setItem: () => { throw new DOMException("The quota has been exceeded.", "QuotaExceededError"); } } });
    render(<Configure />);
    open("Review & launch");
    const row = screen.getByText("This browser did not store the draft, so closing the page loses it. Save a study file to keep it.").closest("li")!;
    expect(row).toHaveAttribute("data-code", "draft.not_saved");
    expect(row).toHaveClass("problem--warning");
    expect(screen.queryByText(/not valid yet/)).not.toBeInTheDocument();
    // The bar says it too, in words that are true of a full or blocked storage as of a draft the page cannot hold.
    expect(fileState()).toHaveTextContent("Not saved to a file yet · edits are not kept in this browser");
  });

  it("draws the setup, with a way to save, where the browser refuses access to its storage altogether", () => {
    // With all site data blocked, reading `window.localStorage` or `window.sessionStorage` is itself what throws.
    const refuse = { configurable: true, get: () => { throw new DOMException("The operation is insecure.", "SecurityError"); } };
    Object.defineProperty(window, "localStorage", refuse);
    Object.defineProperty(window, "sessionStorage", refuse);
    render(<Configure />);
    expect(heading()).toHaveTextContent("What is compared");
    expect(screen.getByLabelText("Study title")).toHaveValue(studyOneTemplate().title);
    expect(bar().getByRole("button", { name: "Save study file" })).toBeEnabled();
    expect(bar().getByLabelText("Load study file")).toBeInTheDocument();
    // An edit works, and the page says that it is not kept.
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Edited without storage" } });
    open("Review & launch");
    expect(screen.getByText("This browser did not store the draft, so closing the page loses it. Save a study file to keep it.")).toBeInTheDocument();
  });

  it("keeps a stored draft it cannot read, and hands it back as a download, when the template takes its place", () => {
    const downloads = captureDownloads();
    const unreadable = JSON.stringify({ ...studyOneTemplate(), title: "Work of a week", a_setting_of_another_version: true });
    window.localStorage.setItem(CONFIG_STORAGE_KEY, unreadable);
    render(<Configure />);
    const note = screen.getByText(/Your saved draft no longer matches this version and was replaced by the Study 1 template\. The draft that could not be read is kept in this browser\./);
    // The template is the draft now, and the text that was there is still in the browser.
    expect(saved().title).toBe(studyOneTemplate().title);
    expect(window.localStorage.getItem(UNREADABLE_STORAGE_KEY)).toBe(unreadable);
    fireEvent.click(within(note.closest("p")!).getByRole("button", { name: "Download the draft that could not be read" }));
    expect(downloads.names).toEqual(["unreadable-study-draft.json"]);
  });
});

describe("unticking the last comparison", () => {
  it("keeps the roles for the next one: positive controls, the observed arm with its session settings, and determinism", async () => {
    const template = pinnedTemplate();
    // A study with settings of its own: a session timer, and arms that are not asked to be replayable.
    renderWith({ ...template, arms: template.arms.map((arm) => ({ ...arm, deterministic: false })), supervised: { ...template.supervised!, intention_timer_min: 5 } });
    const creative = () => screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" });
    fireEvent.click(creative());
    await waitFor(() => expect(creative()).not.toBeChecked(), SLOW);
    // The roles are still shown while no comparison is ticked.
    expect(screen.getByRole("checkbox", { name: "Supervised sessions" })).toBeChecked();
    expect(screen.getByRole("button", { name: /^ε 0\.10: planned, off/ })).toBeInTheDocument();

    // Ticking the other preset (the natural way to switch) gives it the same eight roles.
    fireEvent.click(screen.getByRole("checkbox", { name: "qr-llm · Temperature 1 + min-p 0.10" }));
    await waitFor(() => expect(saved().arms).toHaveLength(8), SLOW);
    const roster = rosterFromArms(saved().arms!, saved().seeds!)!;
    expect(roster).toMatchObject({
      cells: [{ model: "qr-llm", sampling: "t1_minp" }], seeds: [1, 7, 42], supervised: true, deterministic: false,
      epsilons: [{ level: 0.01, available: false }, { level: 0.03, available: false }, { level: 0.1, available: false }],
    });
    expect(saved().supervised?.intention_timer_min).toBe(5);
    expect(saved().seeds).toEqual([1, 7, 42, 1001]);
    // Nothing was asked that was not asked before: no briefing about determinism for arms that were never replayable.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(rail("Compare")).toHaveAccessibleName("Compare: clear");
  });

  it("does not carry the roles of one study into another that takes its place", async () => {
    // The page opens on the template, which has an observed arm. The study loaded over it has none.
    const incoming: WizardStudyConfig = { ...bare(), title: "A study without an observed arm", contact: "lab@example.org" };
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(incoming));
    render(<Configure />);
    expect(screen.getByRole("checkbox", { name: "Supervised sessions" })).toBeChecked();
    pick(new File(["x"], "unobserved.yaml"));
    await report("loaded");
    const creative = () => screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" });
    fireEvent.click(creative());
    await waitFor(() => expect(creative()).not.toBeChecked(), SLOW);
    // What is remembered is the loaded study's roles, not the template's.
    expect(screen.getByRole("checkbox", { name: "Supervised sessions" })).not.toBeChecked();
    fireEvent.click(creative());
    await waitFor(() => expect(saved().arms).toHaveLength(incoming.arms.length), SLOW);
    expect(saved().arms?.some((arm) => arm.observer_mode === "supervised")).toBe(false);
    expect(saved().supervised ?? null).toBeNull();
  });
});

describe("the kind of run the launch dialog starts on", () => {
  const openGate = () => {
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Launch run" }));
    return within(screen.getByRole("dialog", { name: "Readiness before generation" }));
  };
  const untickPreselect = () => {
    fireEvent.click(bar().getByRole("button", { name: "All settings" }));
    fireEvent.click(within(settings()).getByLabelText("Preselect a pilot run in the launch dialog"));
    fireEvent.click(within(settings()).getByRole("button", { name: "Done" }));
  };

  it("follows the pilot setting as it stands when the dialog opens, not as it stood when the page loaded", () => {
    setSession(authResponse());
    renderWith({ ...pinnedTemplate(), contact: "lab@example.org" });
    let gate = openGate();
    expect(gate.getByRole("radio", { name: /^Pilot:/ })).toBeChecked();
    fireEvent.click(gate.getByRole("button", { name: "Cancel" }));

    untickPreselect();
    expect(saved().pilot?.enabled).toBe(false);
    gate = openGate();
    expect(gate.getByRole("radio", { name: /^Full run:/ })).toBeChecked();
    // Both kinds are still on offer: the setting chooses the start, not the choice.
    fireEvent.click(gate.getByRole("radio", { name: /^Pilot:/ }));
    expect(gate.getByRole("radio", { name: /^Pilot:/ })).toBeChecked();
    fireEvent.click(gate.getByRole("button", { name: "Cancel" }));
    // A choice made in a dialog that was cancelled does not stick; the study's setting decides again.
    expect(openGate().getByRole("radio", { name: /^Full run:/ })).toBeChecked();
  });

  it("follows a study that takes the place of the one the page loaded with", async () => {
    setSession(authResponse());
    const template = pinnedTemplate();
    renderWith({ ...template, contact: "lab@example.org", pilot: { ...template.pilot, enabled: false } });
    const incoming = { ...template, contact: "lab@example.org", title: "A study that starts with a pilot", pilot: { ...template.pilot, enabled: true } };
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(incoming));
    pick(new File(["x"], "pilot.yaml"));
    // The draft on screen is in no file, so the page asks before the loaded study takes its place.
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Replace the study you are editing?" }, SLOW)).getByRole("button", { name: "Replace" }));
    await report("loaded");
    expect(openGate().getByRole("radio", { name: /^Pilot:/ })).toBeChecked();
  });

  it("starts a study that says nothing about a pilot on one, as its checkbox shows and the server's default has it", () => {
    setSession(authResponse());
    const silent: Partial<WizardStudyConfig> = { ...pinnedTemplate(), contact: "lab@example.org" };
    delete silent.pilot;
    renderWith(silent as WizardStudyConfig);
    fireEvent.click(bar().getByRole("button", { name: "All settings" }));
    expect(within(settings()).getByLabelText("Preselect a pilot run in the launch dialog")).toBeChecked();
    fireEvent.click(within(settings()).getByRole("button", { name: "Done" }));
    expect(openGate().getByRole("radio", { name: /^Pilot:/ })).toBeChecked();
  });
});

describe("Review and launch", () => {
  it("keeps the keys for the visit and never writes them to localStorage", async () => {
    render(<Configure />);
    open("Review & launch");
    expect(screen.queryByLabelText("api.entropic.science launch key")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=/");
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-secret" } });
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    await waitFor(() => expect(saved().contact).toBe("lab@example.org"));
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain("secret");
    // Leaving the step and coming back does not lose what was typed.
    open("Compare");
    open("Review & launch");
    expect(screen.getByLabelText("Anthropic judge key")).toHaveValue("sk-ant-secret");
  });

  it("blocks the pre-registration and the launch while any step has an error, and says how many", () => {
    render(<Configure />);
    open("Review & launch");
    expect(screen.getByRole("button", { name: "Launch run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate pre-registration" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Generate pre-registration" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("1 problem above blocks the pre-registration and the launch. Warnings do not.");

    // The warning that is left (no expectations recorded) does not block.
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    expect(screen.getByRole("button", { name: "Launch run" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Generate pre-registration" })).toHaveAttribute("href", expect.stringMatching(/^\/prereg\?c=/));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("No expectations recorded.")).toBeInTheDocument();
  });

  it("refuses to open the launch gate while signed out", () => {
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    fireEvent.click(screen.getByRole("button", { name: "Launch run" }));
    expect(launchNote()).toHaveTextContent("Sign in before launching; the link is under Access keys on this step.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("will not open the launch gate for a judged study without the Anthropic key", () => {
    setSession(authResponse());
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    fireEvent.click(screen.getByRole("button", { name: "Launch run" }));
    expect(launchNote()).toHaveTextContent("runs on your own Anthropic key. Add the key under Access keys, then launch.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Launch run" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("tests the judge with the typed key and the chosen settings", async () => {
    const session = setSession(authResponse());
    const judgeCheck = vi.spyOn(api, "judgeCheck").mockResolvedValue({ ok: false, status: 401, model: null, detail: "invalid x-api-key" });
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    expect(screen.getByText(/Required\. This study has evals scored by the judge/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test the judge" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-typed" } });
    fireEvent.click(screen.getByRole("button", { name: "Test the judge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The judge call failed (401): invalid x-api-key");
    expect(judgeCheck).toHaveBeenCalledWith({ judge: expect.objectContaining({ model: "claude-haiku-5-5", thinking: "disabled", temperature: null }), api_key: "sk-ant-typed" }, session.token);

    judgeCheck.mockResolvedValue({ ok: true, status: 200, model: "claude-haiku-5-5", detail: "The judge answered." });
    fireEvent.click(screen.getByRole("button", { name: "Test the judge" }));
    expect(await screen.findByText("claude-haiku-5-5 answered. The judge answered.")).toBeInTheDocument();
  });

  it("names no step by number anywhere in its copy", () => {
    const { container } = render(<Configure />);
    for (const label of STEP_LABELS) {
      open(label);
      expect(container.textContent).not.toMatch(/\bstep \d/i);
    }
  });
});

describe("estimate sidebar", () => {
  it("shows the evidence preview beside the cost: hypotheses, each outcome's sample size, the Holm families", () => {
    render(<Configure />);
    const evidence = within(screen.getByLabelText("Evidence preview"));
    expect(evidence.getByText("Evidence · preview")).toBeInTheDocument();
    expect(evidence.getByText("Hypotheses").nextElementSibling).toHaveTextContent("3 (3 outcomes × 1 comparison)");
    const outcomes = within(evidence.getByRole("list", { name: "Sample size per outcome" })).getAllByRole("listitem");
    expect(outcomes.map((item) => item.firstElementChild?.textContent)).toEqual(["Token sampling residuals (KS)", "Token sequence divergence (MMD)", "Experience-claim rate"]);
    // Pooled token tests have no closed-form power: never rated as enough. The probe is rated, with its count.
    expect(within(outcomes[0]!).getByRole("button")).toHaveClass("rec--unknown");
    expect(within(outcomes[1]!).getByRole("button")).toHaveClass("rec--unknown");
    expect(within(outcomes[2]!).getByRole("button")).toHaveClass("rec--sufficient");
    expect(within(outcomes[2]!).getByRole("button")).toHaveTextContent("30 prompts");
    expect(evidence.getByText("Tests for a difference").nextElementSibling).toHaveTextContent("m = 3");
    expect(evidence.getByText("Negative-control tests").nextElementSibling).toHaveTextContent("m = 9");
    expect(evidence.getByText("Computed in this browser as a preview. The registered numbers are the server's.")).toBeInTheDocument();
    // The cost readout is still there.
    expect(within(screen.getByLabelText("Live study estimates")).getByText("Per arm")).toBeInTheDocument();
  });

  it("follows an edit and opens the setting behind a chip", () => {
    render(<Configure />);
    open("Design");
    fireEvent.change(within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" })).getByLabelText("Prompts for self_referential_probe"), { target: { value: "6" } });
    const chip = within(within(screen.getByLabelText("Evidence preview")).getByRole("list", { name: "Sample size per outcome" })).getAllByRole("button")[2]!;
    expect(chip).toHaveClass("rec--insufficient");
    expect(chip).toHaveTextContent("6 prompts");

    open("Compare");
    fireEvent.click(within(within(screen.getByLabelText("Evidence preview")).getByRole("list", { name: "Sample size per outcome" })).getAllByRole("button")[2]!);
    expect(rail("Design")).toHaveAttribute("aria-current", "step");
    // The label now also says which prompts are used, so it is matched by how it starts.
    expect(within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" })).getByLabelText(/^Prompts for self_referential_probe/)).toHaveFocus();
  });

  it("says which model has no throughput assumption instead of failing", () => {
    const template = studyOneTemplate();
    renderWith({ ...template, estimates: { ...template.estimates, tps_by_model: { "another-model": 100 } } });
    expect(within(screen.getByLabelText("Live study estimates")).getByText("No throughput assumption for qr-llm.")).toBeInTheDocument();
    expect(screen.getByLabelText("Evidence preview")).toBeInTheDocument();
  });
});

describe("determinism briefing", () => {
  it("opens once when an arm is asked to be replayable", () => {
    render(<Configure />);
    const deterministic = screen.getByRole("checkbox", { name: "Deterministic" });
    fireEvent.click(deterministic);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(deterministic);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "I understand" }));
    expect(window.sessionStorage.getItem("qr-evals.determinism-briefing.acknowledged")).toBe("yes");
    fireEvent.click(deterministic);
    fireEvent.click(deterministic);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

/** Stands in for the browser's download, which jsdom does not have: the Blobs handed over and the names they were to be saved under. */
function captureDownloads() {
  const blobs: Blob[] = [];
  const names: string[] = [];
  Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: (blob: Blob) => { blobs.push(blob); return `blob:study-${blobs.length}`; } });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: () => undefined });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) { names.push(this.download); });
  return { blobs, names };
}

function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("The blob could not be read."));
    reader.readAsText(blob);
  });
}

/** The study in storage, as text: what a failed load must leave exactly as it was. */
const draftText = () => window.localStorage.getItem(CONFIG_STORAGE_KEY);
const pick = (file: File | File[]) => fireEvent.change(bar().getByLabelText("Load study file"), { target: { files: Array.isArray(file) ? file : [file] } });
const answer = (config: WizardStudyConfig, unknown_cards: string[] = []) => ({ config, config_hash: "c".repeat(64), unknown_cards });

describe("study bar on every view", () => {
  it("keeps Save and the gear on every step, and Save is never disabled", () => {
    render(<Configure />);
    for (const label of STEP_LABELS) {
      open(label);
      expect(bar().getByRole("button", { name: "Save study file" })).toBeEnabled();
      expect(bar().getByRole("button", { name: "All settings" })).toBeEnabled();
      expect(bar().getByLabelText("Load study file")).toBeInTheDocument();
      expect(fileState()).toHaveAttribute("role", "status");
    }
    // With an error on screen it is still enabled: a draft is saved as it is.
    open("Compare");
    fireEvent.click(screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" }));
    expect(rail("Compare")).toHaveClass("has-errors");
    expect(bar().getByRole("button", { name: "Save study file" })).toBeEnabled();
  });

  it("brings the bar up under the site header when the map is chosen, and not when it is there already", () => {
    // The wheel zooms the diagram, so the page cannot be scrolled from on it: the map has to arrive in the window.
    const header = document.createElement("header");
    header.className = "site-header";
    document.body.append(header);
    const scrollBy = vi.fn();
    vi.stubGlobal("scrollBy", scrollBy);
    let dockTop = 294;
    let bodyTop = 380;
    const rect = (top: number, height: number) => ({ top, bottom: top + height, left: 0, right: 800, width: 800, height, x: 0, y: top, toJSON: () => ({}) });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("site-header")) return rect(0, 65);
      if (this.classList.contains("study-bar-dock")) return rect(dockTop, 70);
      if (this.classList.contains("wizard-layout")) return rect(bodyTop, 600);
      return rect(0, 0);
    });
    try {
      render(<Configure />);
      expect(scrollBy).not.toHaveBeenCalled();
      fireEvent.click(bar().getByRole("radio", { name: "Map" }));
      expect(scrollBy).toHaveBeenCalledTimes(1);
      expect(scrollBy).toHaveBeenCalledWith({ top: 229, behavior: "auto" });
      // Already under the header: nothing moves.
      fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
      dockTop = 65;
      bodyTop = 151;
      fireEvent.click(bar().getByRole("radio", { name: "Map" }));
      expect(scrollBy).toHaveBeenCalledTimes(1);
      // Scrolled to the foot of a long step: the bar sticks under the header while the page has slid up behind
      // it. The page comes back down until what follows the bar starts right under it.
      fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
      bodyTop = -2400;
      fireEvent.click(bar().getByRole("radio", { name: "Map" }));
      expect(scrollBy).toHaveBeenCalledTimes(2);
      expect(scrollBy).toHaveBeenLastCalledWith({ top: -2535, behavior: "auto" });
    } finally {
      header.remove();
      vi.unstubAllGlobals();
    }
  });

  it("stays over the map view, which takes the place of the steps without a reload or a lost edit", async () => {
    const { container } = render(<Configure />);
    open("Measure");
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Edited before the map" } });
    fireEvent.click(bar().getByRole("radio", { name: "Map" }));
    expect(window.location.search).toBe("?view=map");
    expect(container.querySelector("#study-map-slot")).toBeInTheDocument();
    expect(container.querySelector("#study-map-slot .study-map")).toBeInTheDocument();
    expect(container.querySelector(".wizard-step")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Setup steps" })).not.toBeInTheDocument();
    // The bar, the title and the estimate are the same over both views.
    expect(bar().getByRole("button", { name: "Save study file" })).toBeEnabled();
    expect(bar().getByRole("button", { name: "All settings" })).toBeEnabled();
    expect(screen.getByLabelText("Study title")).toHaveValue("Edited before the map");
    expect(screen.getByLabelText("Live study estimates")).toBeInTheDocument();

    fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
    expect(window.location.search).toBe("");
    expect(container.querySelector("#study-map-slot")).toBeNull();
    // The step that was on screen is on screen again, with the edit.
    expect(rail("Measure")).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText("Study title")).toHaveValue("Edited before the map");
    await waitFor(() => expect(saved().title).toBe("Edited before the map"));
  });

  it("opens on the map when the address says so, and a link to a setting leads back to the steps", () => {
    window.history.pushState({}, "", "/?view=map");
    const { container } = render(<Configure />);
    expect(container.querySelector("#study-map-slot")).toBeInTheDocument();
    expect(container.querySelector(".setup-shell")).toHaveAttribute("data-view", "map");
    // A chip of the estimate names a setting of a step: the steps come back with the focus on it.
    fireEvent.click(within(within(screen.getByLabelText("Evidence preview")).getByRole("list", { name: "Sample size per outcome" })).getAllByRole("button")[2]!);
    expect(window.location.search).toBe("");
    expect(rail("Design")).toHaveAttribute("aria-current", "step");
    expect(within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" })).getByLabelText(/^Prompts for self_referential_probe/)).toHaveFocus();
  });

  it("never puts a share link back into the address bar after one was copied from the bar", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const shared = { ...studyOneTemplate(), title: "Sent by a colleague", contact: "lab@example.org" };
    window.history.pushState({}, "", `/?c=${encodeShareConfig(shared) ?? ""}`);
    render(<Configure />);
    expect(window.location.search).toBe("");
    more("Copy share link");
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(String(writeText.mock.calls[0]![0])).toContain("?c=");
    expect(await report("note")).toHaveTextContent("Share link copied.");
    // Nothing done on the page afterwards writes it to the address: not an edit, a step, a view, the settings or a save.
    captureDownloads();
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Edited after sharing" } });
    for (const label of STEP_LABELS) { open(label); expect(window.location.href).not.toContain("c="); }
    fireEvent.click(bar().getByRole("radio", { name: "Map" }));
    expect(window.location.search).toBe("?view=map");
    fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
    fireEvent.click(bar().getByRole("button", { name: "All settings" }));
    fireEvent.click(within(settings()).getByRole("button", { name: "Done" }));
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    expect(window.location.search).toBe("");
    expect(window.location.href).not.toContain("c=");
  });
});

describe("saving the study file", () => {
  it("writes a Blob whose text is toStudyYaml of the study on screen, edits included", async () => {
    const downloads = captureDownloads();
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    const expected = { ...studyOneTemplate(), contact: "lab@example.org" };
    expect(downloads.blobs).toHaveLength(1);
    expect(downloads.blobs[0]!.type).toBe("application/yaml;charset=utf-8");
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml(expected));
    expect(downloads.names).toEqual(["study-1-quantum-randomness-and-supervised-observation.study.yaml"]);
    // jsdom has no save dialog, like Firefox and Safari: the file is a download, and the state does not claim more.
    await waitFor(() => expect(fileState()).toHaveTextContent("Downloaded as study-1-quantum-randomness-and-supervised-observation.study.yaml"), SLOW);
    expect(JSON.parse(window.localStorage.getItem(FILE_MARK_STORAGE_KEY) ?? "null")).toMatchObject({ name: "study-1-quantum-randomness-and-supervised-observation.study.yaml" });
  });

  it("saves a draft with problems as it stands, and the file state says what is left to fix", async () => {
    const downloads = captureDownloads();
    render(<Configure />);
    fireEvent.click(screen.getByRole("checkbox", { name: "qr-llm · Creative sampling" }));
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    expect(downloads.blobs).toHaveLength(1);
    const text = await blobText(downloads.blobs[0]!);
    expect(text).toContain("arms: []\n");
    // The file is written as the study stands. It is not a file that loads back, and the page says so where it
    // confirms the save: in the file state and in the report under the bar.
    await waitFor(() => expect(fileState()).toHaveTextContent(/^Downloaded as .+\.study\.yaml · will not load back as it is · edits are not kept in this browser$/), SLOW);
    const note = await report("note");
    expect(note).toHaveAttribute("role", "alert");
    expect(note).toHaveTextContent(/Handed to the browser as a download named .+\.study\.yaml\. This file will not load back as it is\. The setting arms is empty: it needs at least one entry\. The server could not be asked, so this is the page's own check\. Fix that and save again\./);
  });

  it("says after a save that the file will load back, on the server's word when it can be asked and on the page's own when it cannot", async () => {
    captureDownloads();
    const study = { ...pinnedTemplate(), contact: "lab@example.org", expectations: "No difference." };
    renderWith(study);
    // The server cannot be asked (the default of these tests): the page's own check answers, and says that it did.
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    const own = await report("note");
    expect(own).toHaveAttribute("role", "status");
    expect(own).toHaveTextContent(/^✓Handed to the browser as a download named .+\.study\.yaml\. The server could not be asked to read it back\. This page's own check found nothing in it that stops a study file from loading\./);
    expect(fileState()?.textContent).toMatch(/^Downloaded as .+\.study\.yaml$/);

    // The server reads the very text that was written, through the route a load uses.
    const parse = vi.spyOn(api, "parseConfig").mockResolvedValue(answer(study));
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(document.querySelector(".setup-shell > .load-report")).toHaveTextContent("The server read the same text back and accepts it, so the file will load."), SLOW);
    expect(parse).toHaveBeenCalledExactlyOnceWith({ text: toStudyYaml(study) }, expect.any(AbortSignal));
    expect(document.querySelector(".setup-shell > .load-report")).toHaveAttribute("role", "status");
  });

  it("takes the server's refusal of the written text for what it is: the file is there, and it will not load back", async () => {
    captureDownloads();
    renderWith({ ...pinnedTemplate(), contact: "lab@example.org", expectations: "No difference." });
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "" } });
    // What the server answers to a study file with an empty title.
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all)", 400));
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    const note = await report("note");
    expect(note).toHaveAttribute("role", "alert");
    expect(note).toHaveTextContent("Handed to the browser as a download named study.study.yaml. This file will not load back as it is. The server read the same text and refuses it: not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all). The setting it names is title. Fix that in the study and save again.");
    expect(fileState()).toHaveTextContent("Downloaded as study.study.yaml · will not load back as it is · edits are not kept in this browser");
    // The mark keeps it, so a later visit and the question before a replace know it too.
    expect(JSON.parse(window.localStorage.getItem(FILE_MARK_STORAGE_KEY) ?? "{}")).toMatchObject({ name: "study.study.yaml", loadsBack: false });

    // Fixed and saved again, the file is one that loads, and the page says that instead.
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Named again" } });
    expect(fileState()).toHaveTextContent("Changed since study.study.yaml");
    vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(fileState()?.textContent).toBe("Downloaded as named-again.study.yaml"), SLOW);
    expect(JSON.parse(window.localStorage.getItem(FILE_MARK_STORAGE_KEY) ?? "{}")).not.toHaveProperty("loadsBack");
  });

  it("never writes a key typed on the page into the file", async () => {
    const downloads = captureDownloads();
    setSession(authResponse());
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-typed-judge-key" } });
    fireEvent.change(screen.getByLabelText(/^External provider key/), { target: { value: "sk-external-typed-key" } });
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(fileState()).toHaveAttribute("data-file-state", "saved"), SLOW);
    // And from the settings dialog, opened over the step that holds the keys.
    fireEvent.click(bar().getByRole("button", { name: "All settings" }));
    fireEvent.click(within(settings()).getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(downloads.blobs).toHaveLength(2), SLOW);
    for (const blob of downloads.blobs) {
      const text = await blobText(blob);
      expect(text).toBe(toStudyYaml({ ...studyOneTemplate(), contact: "lab@example.org" }));
      expect(text).not.toContain("sk-ant-typed-judge-key");
      expect(text).not.toContain("sk-external-typed-key");
    }
    expect(window.localStorage.getItem(FILE_MARK_STORAGE_KEY)).not.toContain("typed");
    // Each save asked the server to read its text back. What was sent is the text of the file, so no key went with it.
    const sent = vi.mocked(api.parseConfig).mock.calls.map(([body]) => body.text);
    expect(sent).toEqual([toStudyYaml({ ...studyOneTemplate(), contact: "lab@example.org" }), toStudyYaml({ ...studyOneTemplate(), contact: "lab@example.org" })]);
    // The keys are still on the page for the launch.
    fireEvent.click(within(settings()).getByRole("button", { name: "Done" }));
    expect(screen.getByLabelText("Anthropic judge key")).toHaveValue("sk-ant-typed-judge-key");
  });

  it("round-trips: the saved text is what the server is sent on load, and the study comes back saved", async () => {
    const downloads = captureDownloads();
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    const edited = { ...studyOneTemplate(), contact: "lab@example.org" };
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    const text = await blobText(downloads.blobs[0]!);
    const name = downloads.names[0]!;
    await waitFor(() => expect(fileState()).toHaveAttribute("data-file-state", "saved"), SLOW);

    fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "changed.since@example.org" } });
    expect(fileState()).toHaveTextContent(`Changed since ${name}`);
    await waitFor(() => expect(saved().contact).toBe("changed.since@example.org"));

    // The server's side is pinned in the backend's round-trip test; here it answers with the study the text holds.
    const parseConfig = vi.spyOn(api, "parseConfig").mockResolvedValue(answer(edited));
    pick(new File([downloads.blobs[0]!], name));
    // The study has changed since its file, so the page asks first.
    const ask = within(await screen.findByRole("dialog", { name: "Replace the study you are editing?" }, { timeout: 10_000 }));
    expect(saved().contact).toBe("changed.since@example.org");
    fireEvent.click(ask.getByRole("button", { name: "Replace" }));
    expect(parseConfig).toHaveBeenCalledExactlyOnceWith({ text });
    expect(await report("loaded")).toHaveTextContent(`Loaded ${name}: ${edited.arms.length} arms, ${edited.cards.length} evals, 3 hypotheses.`);
    await waitFor(() => expect(saved()).toEqual(parseStudyConfig(edited)));
    expect(fileState()?.textContent).toBe(`Saved to ${name}`);
    expect(fileState()).toHaveAttribute("data-file-state", "saved");
  });
});

describe("loading a study file that was saved with problems", () => {
  it("loads it, and shows what the server would refuse it for in the server's own words, in plain sight", async () => {
    // The Study 1 template with the eval of a deciding outcome unticked, as the page saves it without complaint.
    const template = pinnedTemplate();
    const draft = { ...template, contact: "lab@example.org", cards: template.cards.filter((selection) => selection.card_id !== "tier0_pit_residuals") };
    const said = ["design.confirmatory names card 'tier0_pit_residuals', which is not selected"];
    vi.spyOn(api, "parseConfig").mockResolvedValue({ ...answer(draft), problems: said } as Awaited<ReturnType<typeof api.parseConfig>>);
    render(<Configure />);
    pick(new File(["x"], "draft.study.yaml"));
    const loaded = await report("loaded");
    // The study is on screen and stored: the file the app wrote is a file the app reads.
    expect(screen.getByLabelText("Study title")).toHaveValue(template.title);
    await waitFor(() => expect(saved().cards).toHaveLength(template.cards.length - 1), SLOW);
    expect(fileState()).toHaveTextContent("Saved to draft.study.yaml · 1 thing to fix");
    expect(loaded).toHaveAttribute("role", "alert");
    expect(loaded).toHaveTextContent("The study was loaded as it is. The server read the file and would refuse this study at launch for the reason below, in its own words:");
    // The message is on the page as it is, not behind a fold: nothing has to be opened to read it.
    const message = within(loaded).getByText("design.confirmatory names card 'tier0_pit_residuals', which is not selected");
    expect(message.closest("details")).toBeNull();
    expect(message.tagName).toBe("LI");
    // This one the page has a check of its own for, so it is also listed under its step, with the way to fix it.
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to fix, 1 to check");
    open("Design");
    expect(within(screen.getByRole("list", { name: "Problems on this step" })).getByText("Token sampling residuals (KS) can no longer decide a study: the eval is not selected.")).toBeInTheDocument();
  });

  it("shows a problem only the server knows of, for which the page lists nothing under any step", async () => {
    // Two margins for one outcome: the server refuses that at launch, and the page has no check of its own for it.
    const study = { ...pinnedTemplate(), contact: "lab@example.org", expectations: "No difference." };
    const said = ["design.margins: altered_state_meq/meq30_total is declared twice", "a second thing only the server checks"];
    vi.spyOn(api, "parseConfig").mockResolvedValue({ ...answer(study), problems: said } as Awaited<ReturnType<typeof api.parseConfig>>);
    render(<Configure />);
    pick(new File(["x"], "twice.study.yaml"));
    const loaded = await report("loaded");
    // The page itself finds nothing to fix in this study.
    expect(document.querySelector(".study-bar .validity-pill")).toHaveTextContent("Ready");
    expect(loaded.querySelector(".load-report__problems")).toBeNull();
    // So the report does not send the reader to the steps. It is an alert, and both messages are in it to be read.
    expect(loaded).toHaveAttribute("role", "alert");
    expect(loaded).toHaveTextContent("The study was loaded as it is. The server read the file and would refuse this study at launch for the 2 reasons below, in its own words:");
    expect(loaded).not.toHaveTextContent(/listed under the steps/);
    for (const message of said) expect(within(loaded).getByText(message)).toBeVisible();
    expect(loaded.querySelector("details")).toBeNull();
  });

  it("says nothing of the kind for a file an older server read, which sends no list", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...pinnedTemplate(), contact: "lab@example.org", expectations: "No difference." }));
    render(<Configure />);
    pick(new File(["x"], "clean.study.yaml"));
    const loaded = await report("loaded");
    expect(loaded).not.toHaveTextContent("the server would refuse");
    expect(loaded.querySelector("[data-server-problems]")).toBeNull();
  });
});

describe("loading a study file", () => {
  it("returns the steps to the first one, records the file, and leaves the view where it is", async () => {
    const thesis = { ...studyOneTemplate(), title: "Prompt or Physics?" };
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(thesis));
    render(<Configure />);
    open("Review & launch");
    pick(new File(["x"], "thesis.study.yaml"));
    const loaded = await report("loaded");
    expect(rail("Compare")).toHaveAttribute("aria-current", "step");
    expect(fileState()).toHaveTextContent("Saved to thesis.study.yaml · 1 thing to fix");
    expect(JSON.parse(window.localStorage.getItem(FILE_MARK_STORAGE_KEY) ?? "null")).toMatchObject({ name: "thesis.study.yaml" });
    // The loaded study has problems, so the report is an alert and leads to them.
    expect(loaded).toHaveAttribute("role", "alert");
    // The contact blocks; the expectations and the two pins the template ships without are to check.
    expect(loaded).toHaveTextContent("1 thing to fix, 3 to check.");
    fireEvent.click(within(loaded).getByRole("button", { name: "Open settings" }));
    expect(within(settings()).getByRole("checkbox", { name: "Problems only" })).toBeChecked();
    fireEvent.click(within(settings()).getByRole("button", { name: "Done" }));

    // Loaded while the map is on screen, the map stays.
    fireEvent.click(bar().getByRole("radio", { name: "Map" }));
    pick(new File(["x"], "thesis.study.yaml"));
    await waitFor(() => expect(vi.mocked(api.parseConfig)).toHaveBeenCalledTimes(2));
    await report("loaded");
    expect(window.location.search).toBe("?view=map");
    expect(document.querySelector("#study-map-slot")).toBeInTheDocument();
  });

  it("reports a study without problems as a status, and can be dismissed", async () => {
    const clean = { ...bare(), contact: "lab@example.org", expectations: "No difference.", models: bare().models.map((model) => ({ ...model, checkpoint: "org/qr-llm@0123abc" })) };
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(clean));
    render(<Configure />);
    pick(new File(["x"], "clean.yaml"));
    const loaded = await report("loaded");
    expect(loaded).toHaveAttribute("role", "status");
    expect(loaded).toHaveTextContent(`Loaded clean.yaml: ${clean.arms.length} arms, 4 evals, 2 hypotheses.`);
    expect(within(loaded).queryByRole("button", { name: "Open settings" })).not.toBeInTheDocument();
    fireEvent.click(within(loaded).getByRole("button", { name: "Dismiss" }));
    expect(document.querySelector(".load-report")).toBeNull();
  });

  it("names the evals the server does not have", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(studyOneTemplate(), ["made_up_eval", "another_one"]));
    render(<Configure />);
    pick(new File(["x"], "odd.yaml"));
    const loaded = await report("loaded");
    expect(loaded).toHaveAttribute("role", "alert");
    expect(loaded).toHaveTextContent("It names evals this server does not have: made_up_eval, another_one.");
  });

  it("asks before replacing unsaved work: Cancel keeps it, Replace discards it, Save current study first saves and then replaces", async () => {
    const downloads = captureDownloads();
    const incoming = { ...bare(), title: "The incoming study" };
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(incoming));
    render(<Configure />);
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "My unsaved work" } });
    await waitFor(() => expect(saved().title).toBe("My unsaved work"));
    const question = async () => within(await screen.findByRole("dialog", { name: "Replace the study you are editing?" }, { timeout: 10_000 }));

    // Cancel: nothing is loaded and nothing is lost.
    pick(new File(["x"], "incoming.yaml"));
    let ask = await question();
    expect(ask.getAllByRole("button").map((button) => button.textContent)).toEqual(["×", "Save current study first", "Replace", "Cancel"]);
    expect(ask.getByText(/"incoming\.yaml" is ready to take its place\. The study you are editing is not saved to a file\./)).toBeInTheDocument();
    fireEvent.click(ask.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Study title")).toHaveValue("My unsaved work");
    expect(saved().title).toBe("My unsaved work");
    expect(document.querySelector(".load-report")).toBeNull();

    // Escape is Cancel.
    pick(new File(["x"], "incoming.yaml"));
    await question();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(saved().title).toBe("My unsaved work");

    // Save current study first: the work goes to a file, then the incoming study takes its place.
    pick(new File(["x"], "incoming.yaml"));
    ask = await question();
    fireEvent.click(ask.getByRole("button", { name: "Save current study first" }));
    expect(downloads.names).toEqual(["my-unsaved-work.study.yaml"]);
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml({ ...studyOneTemplate(), title: "My unsaved work" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), SLOW);
    expect(screen.getByLabelText("Study title")).toHaveValue("The incoming study");
    expect(fileState()).toHaveTextContent("Saved to incoming.yaml");
    await waitFor(() => expect(saved().title).toBe("The incoming study"));

    // Replace: the edit made since the load is discarded.
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Edited again" } });
    pick(new File(["x"], "incoming.yaml"));
    ask = await question();
    expect(ask.getByText(/The study you are editing has changed since incoming\.yaml\./)).toBeInTheDocument();
    fireEvent.click(ask.getByRole("button", { name: "Replace" }));
    expect(screen.getByLabelText("Study title")).toHaveValue("The incoming study");
    expect(downloads.blobs).toHaveLength(1);
  });

  it("does not ask when nothing would be lost: the untouched template, or a study equal to its file", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...bare(), title: "First" }));
    render(<Configure />);
    pick(new File(["x"], "first.yaml"));
    await report("loaded");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    vi.mocked(api.parseConfig).mockResolvedValue(answer({ ...bare(), title: "Second" }));
    pick(new File(["x"], "second.yaml"));
    await waitFor(() => expect(screen.getByLabelText("Study title")).toHaveValue("Second"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fileState()).toHaveTextContent("Saved to second.yaml");
  });

  describe("a file that fails leaves the study, the draft and the file state untouched", () => {
    const big = new File(["x".repeat(200_001)], "big.yaml");
    const cases: [string, File | File[], (() => void) | null, string][] = [
      ["a file larger than the server reads", big, null, '"big.yaml" is larger than a study file can be (200 kB).'],
      ["an empty file", new File([], "empty.yaml"), null, '"empty.yaml" is empty.'],
      ["two files at once", [new File(["a"], "a.yaml"), new File(["b"], "b.yaml")], null, "A study is one file, and 2 were given. Nothing was changed."],
      ["a file the server refuses", new File(["x"], "refused.yaml"), () => { vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: arms.0.sampling: Input should be 'creative' or 't1_minp' (2 problem(s) in all)", 400)); }, "\"refused.yaml\" was not loaded: not a valid study configuration: arms.0.sampling: Input should be 'creative' or 't1_minp' (2 problem(s) in all)"],
      ["a file that is not YAML", new File(["{"], "torn.yaml"), () => { vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("the file is not valid YAML or JSON: while parsing a flow node", 400)); }, '"torn.yaml" was not loaded: the file is not valid YAML or JSON: while parsing a flow node'],
      ["a study this page cannot hold", new File(["x"], "newer.yaml"), () => { vi.spyOn(api, "parseConfig").mockResolvedValue({ config: { ...studyOneTemplate(), from_the_future: true } as unknown as WizardStudyConfig, config_hash: "c".repeat(64), unknown_cards: [] }); }, '"newer.yaml" was read by the server but this page is out of date. Reload the page and try again.'],
      ["a YAML file while the server cannot be reached", new File(["title: x\n"], "offline.yaml"), () => { vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch")); }, '"offline.yaml" needs the server to be read, and it could not be reached. Nothing was changed.'],
      ["an incomplete JSON file while the server cannot be reached", new File(['{"title":"x"}'], "partial.json"), () => { vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch")); }, '"partial.json" could not be checked because the server could not be reached, and on its own it is not a complete study. Nothing was changed.'],
      ["a file that cannot be read", new File(["x"], "locked.yaml"), () => { vi.spyOn(FileReader.prototype, "readAsText").mockImplementation(function fail(this: FileReader) { this.onerror?.(new ProgressEvent("error") as ProgressEvent<FileReader>); }); }, '"locked.yaml" was not loaded: the file could not be read.'],
    ];

    it.each(cases)("%s", async (_what, file, arrange, message) => {
      captureDownloads();
      render(<Configure />);
      // A study with edits and a file of its own: any of it would show if a failed load touched it.
      open("Review & launch");
      fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      await waitFor(() => expect(fileState()).toHaveAttribute("data-file-state", "saved"), SLOW);
      fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Work in progress" } });
      await waitFor(() => expect(saved().title).toBe("Work in progress"));
      const before = { draft: draftText(), mark: window.localStorage.getItem(FILE_MARK_STORAGE_KEY), state: fileState()?.textContent };
      expect(before.state).toMatch(/^Changed since /);

      arrange?.();
      pick(file);
      const failed = await report("failed");
      expect(failed).toHaveAttribute("role", "alert");
      expect(failed).toHaveTextContent(message);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(draftText()).toBe(before.draft);
      expect(window.localStorage.getItem(FILE_MARK_STORAGE_KEY)).toBe(before.mark);
      expect(fileState()?.textContent).toBe(before.state);
      expect(screen.getByLabelText("Study title")).toHaveValue("Work in progress");
      expect(screen.getByLabelText("Contact")).toHaveValue("lab@example.org");
      // The step on screen stays too: only a study that loads returns the steps to the first.
      expect(rail("Review & launch")).toHaveAttribute("aria-current", "step");
    });

    it("says which setting the server's refusal is about", async () => {
      vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: arms.0.sampling: Input should be 'creative' or 't1_minp' (2 problem(s) in all)", 400));
      render(<Configure />);
      pick(new File(["x"], "refused.yaml"));
      expect(await report("failed")).toHaveTextContent("The setting it names is arms[0].sampling. The study you are editing was not changed.");
    });
  });

  it("loads a JSON study without the server when the server cannot be reached, and says so", async () => {
    vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
    const offline = { ...bare(), title: "Loaded offline" };
    render(<Configure />);
    pick(new File([JSON.stringify(offline)], "offline.json", { type: "application/json" }));
    const loaded = await report("loaded");
    expect(loaded).toHaveTextContent("Loaded offline.json: ");
    expect(loaded).toHaveTextContent("Loaded without the server's checks.");
    expect(screen.getByLabelText("Study title")).toHaveValue("Loaded offline");
    expect(fileState()).toHaveTextContent("Saved to offline.json");
  });
});

describe("dropping a study file on the setup", () => {
  const shell = () => document.querySelector<HTMLElement>(".setup-shell")!;
  const transfer = (files: File[], types = ["Files"]) => ({ dataTransfer: { files, types, dropEffect: "none" } });

  it("outlines the setup while a file is over it and loads exactly one dropped file", async () => {
    const dropped = { ...bare(), title: "Dropped on the page" };
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(dropped));
    render(<Configure />);
    const file = new File(["x"], "dropped.study.yaml");
    expect(shell()).not.toHaveClass("is-drop-target");
    fireEvent.dragEnter(shell(), transfer([file]));
    expect(shell()).toHaveClass("is-drop-target");
    // Moving from one element of the page to another inside it does not drop the outline.
    fireEvent.dragEnter(screen.getByLabelText("Study title"), transfer([file]));
    fireEvent.dragLeave(shell(), transfer([file]));
    expect(shell()).toHaveClass("is-drop-target");
    fireEvent.dragLeave(screen.getByLabelText("Study title"), transfer([file]));
    expect(shell()).not.toHaveClass("is-drop-target");

    fireEvent.dragEnter(shell(), transfer([file]));
    fireEvent.drop(shell(), transfer([file]));
    expect(shell()).not.toHaveClass("is-drop-target");
    expect(await report("loaded")).toHaveTextContent("Loaded dropped.study.yaml: ");
    expect(screen.getByLabelText("Study title")).toHaveValue("Dropped on the page");
    expect(fileState()).toHaveTextContent("Saved to dropped.study.yaml");
  });

  it("loads nothing when more than one file is dropped, and says why", async () => {
    const parseConfig = vi.spyOn(api, "parseConfig");
    render(<Configure />);
    await waitFor(() => expect(draftText()).not.toBeNull());
    const before = draftText();
    fireEvent.drop(shell(), transfer([new File(["a"], "a.yaml"), new File(["b"], "b.yaml")]));
    expect(await report("failed")).toHaveTextContent("A study is one file, and 2 were given. Nothing was changed.");
    expect(parseConfig).not.toHaveBeenCalled();
    expect(draftText()).toBe(before);
  });

  it("ignores a drag that carries no file, such as selected text", () => {
    const parseConfig = vi.spyOn(api, "parseConfig");
    render(<Configure />);
    fireEvent.dragEnter(shell(), transfer([], ["text/plain"]));
    expect(shell()).not.toHaveClass("is-drop-target");
    fireEvent.drop(shell(), transfer([], ["text/plain"]));
    expect(document.querySelector(".load-report")).toBeNull();
    expect(parseConfig).not.toHaveBeenCalled();
  });
});

describe("all settings over a step", () => {
  it("is reached from the gear on every step, with ids that do not collide with the step behind it", () => {
    render(<Configure />);
    for (const label of ["Compare", "Design", "Review & launch"]) {
      open(label);
      fireEvent.click(bar().getByRole("button", { name: "All settings" }));
      const ids = [...document.querySelectorAll("[id]")].map((element) => element.id).filter((id) => id !== "");
      const twice = ids.filter((id, index) => ids.indexOf(id) !== index);
      expect(twice).toEqual([]);
      fireEvent.click(within(settings()).getByRole("button", { name: "Done" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(bar().getByRole("button", { name: "All settings" })).toHaveFocus();
      // The step behind it is where it was.
      expect(rail(label)).toHaveAttribute("aria-current", "step");
    }
  });

  it("changes the step behind it and the stored draft with an edit, as the edit is made", async () => {
    render(<Configure />);
    open("Design");
    const stepField = () => document.querySelector<HTMLTextAreaElement>(".wizard-step [data-path='expectations'] textarea")!;
    expect(stepField()).toHaveValue("");
    expect(rail("Design")).toHaveAccessibleName("Design: 1 to check");
    fireEvent.click(bar().getByRole("button", { name: "All settings" }));
    const inSettings = settings().querySelector<HTMLTextAreaElement>("[data-path='expectations'] textarea")!;
    expect(inSettings).not.toBe(stepField());
    expect(stepField().closest(".field")).toHaveAttribute("id", "field-expectations");
    expect(inSettings.closest(".field")).toHaveAttribute("id", "settings-field-expectations");
    fireEvent.change(inSettings, { target: { value: "No difference between the arms." } });
    // The dialog is still open; the step, the rail and the pill behind it already show the edit.
    expect(settings()).toBeInTheDocument();
    expect(stepField()).toHaveValue("No difference between the arms.");
    expect(rail("Design")).toHaveAccessibleName("Design: clear");
    expect(document.querySelector(".study-bar .validity-pill")).toHaveTextContent("1 thing to fix");
    await waitFor(() => expect(saved().expectations).toBe("No difference between the arms."));

    // An advanced setting, which no step shows, is edited here and lands in the same draft.
    fireEvent.change(settings().querySelector<HTMLInputElement>("[data-path='estimates.reading_tps'] input")!, { target: { value: "12" } });
    await waitFor(() => expect(saved().estimates?.reading_tps).toBe(12));
  });

  it("filters by a search and by Problems only", () => {
    render(<Configure />);
    fireEvent.click(bar().getByRole("button", { name: "All settings" }));
    const shown = () => [...settings().querySelectorAll<HTMLElement>("[data-panel]")].filter((panel) => !panel.hidden && !panel.closest("section")?.hidden).map((panel) => panel.dataset.panel);
    expect(shown()).toHaveLength(22);
    fireEvent.change(within(settings()).getByRole("searchbox", { name: "Filter settings" }), { target: { value: "narrative" } });
    expect(shown()).toEqual(["narrative"]);
    fireEvent.change(within(settings()).getByRole("searchbox", { name: "Filter settings" }), { target: { value: "no such thing" } });
    expect(shown()).toEqual([]);
    expect(within(settings()).getByText('No settings match "no such thing".')).toBeInTheDocument();
    fireEvent.change(within(settings()).getByRole("searchbox", { name: "Filter settings" }), { target: { value: "" } });
    fireEvent.click(within(settings()).getByRole("checkbox", { name: "Problems only" }));
    // Study 1 as it ships: the placeholder contact, the missing expectations, and the two pins it has none of
    // (the model's checkpoint in the model table, the hashes of the session assets among the recorded values).
    expect(shown()).toEqual(["people", "expectations", "models", "record"]);
  });

  it("opens on the problems from the validity pill", () => {
    render(<Configure />);
    fireEvent.click(document.querySelector<HTMLElement>(".study-bar .validity-pill")!);
    expect(within(settings()).getByRole("checkbox", { name: "Problems only" })).toBeChecked();
    expect(within(settings()).getByText("Replace the placeholder contact.")).toBeInTheDocument();
  });
});
