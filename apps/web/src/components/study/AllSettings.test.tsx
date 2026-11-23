import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api, ApiError } from "../../lib/api";
import { studyOneTemplate } from "../../lib/config-store";
import { PANELS, SECTIONS } from "../../lib/panels";
import { studyFileName, toStudyYaml } from "../../lib/study-file";
import { useStudy, type SettingsTarget } from "../../routes/configure/StudyContext";
import { BarHarness, blobText, captureDownloads, pinnedTemplate, resetBrowser, smallStudy, storeDraft, storedDraft } from "./study-test-kit";

// A dialog of every panel is a large page for jsdom; elements are found by selector where a role query would walk all of it.
vi.setConfig({ testTimeout: 60_000 });

beforeEach(() => {
  resetBrowser();
  vi.spyOn(api, "cards").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "models").mockRejectedValue(new TypeError("Failed to fetch"));
  // A save asks the server to read the written text back. No test here reaches a real server: by default it cannot be asked, and the page's own check answers.
  vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const gear = () => screen.getByRole("button", { name: "All settings" });
const dialog = () => document.querySelector<HTMLElement>(".modal--page");
const inDialog = () => within(dialog()!);
/** The panels that are drawn and not hidden by a filter, by id, in page order. */
const shownPanels = () => [...dialog()!.querySelectorAll<HTMLElement>("[data-panel]")].filter((panel) => !panel.hidden && !panel.closest("section")?.hidden).map((panel) => panel.dataset.panel);
const shownSections = () => [...dialog()!.querySelectorAll<HTMLElement>(".all-settings__section")].filter((section) => !section.hidden).map((section) => section.querySelector("h3")?.textContent);
const filter = () => dialog()!.querySelector<HTMLInputElement>("input[type='search']")!;
const problemsOnly = () => dialog()!.querySelector<HTMLInputElement>(".all-settings__toggle input")!;
const search = (text: string) => fireEvent.change(filter(), { target: { value: text } });

/** Opens the settings the way another part of the page would: on a section, a setting, or the problems. */
function Opener({ target, label }: { target: SettingsTarget; label: string }) {
  const { openSettings } = useStudy();
  return <button type="button" onClick={() => openSettings(target)}>{label}</button>;
}

function renderSettings(config = smallStudy(), opener?: { target: SettingsTarget; label: string }) {
  storeDraft(config);
  return render(<BarHarness>{opener ? <Opener {...opener} /> : null}</BarHarness>);
}

describe("all settings", () => {
  it("opens from the gear as a full-height dialog with every panel, in registry order, under its section", () => {
    renderSettings();
    expect(dialog()).toBeNull();
    fireEvent.click(gear());
    expect(dialog()).toHaveAttribute("role", "dialog");
    expect(dialog()).toHaveAttribute("aria-modal", "true");
    expect(dialog()).toHaveAccessibleName("Everything in this study");
    expect(dialog()!.querySelector(".eyebrow")).toHaveTextContent("Study settings");
    // Every panel of the registry, the advanced ones included, in the registry's own order.
    expect([...dialog()!.querySelectorAll<HTMLElement>("[data-panel]")].map((panel) => panel.dataset.panel)).toEqual(PANELS.map((panel) => panel.id));
    expect(shownPanels()).toEqual(PANELS.map((panel) => panel.id));
    expect(PANELS.some((panel) => panel.advanced)).toBe(true);
    for (const panel of PANELS) {
      const drawn = dialog()!.querySelector<HTMLElement>(`[data-panel="${panel.id}"]`)!;
      expect(drawn.classList.contains("is-advanced")).toBe(panel.advanced === true);
      // Each sits under the heading of its own section.
      expect(drawn.closest("section")?.querySelector("h3")?.textContent).toBe(SECTIONS.find((section) => section.id === panel.section)?.label);
    }
    expect(shownSections()).toEqual(["Study", "Models & arms", "Evals & repetitions", "Design & analysis", "Supervised sessions", "Judge & narrative", "Advanced"]);
  });

  it("lists the sections as links, marks the one in view, and jumps to one", () => {
    renderSettings();
    fireEvent.click(gear());
    const nav = inDialog().getByRole("navigation", { name: "Settings sections" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.querySelector(".all-settings__nav-label")?.textContent)).toEqual(SECTIONS.map((section) => section.label));
    expect(links[0]).toHaveAttribute("aria-current", "true");
    expect(links[0]).toHaveAttribute("href", "#settings-section-study");
    fireEvent.click(links[3]!);
    expect(links[3]).toHaveAttribute("aria-current", "true");
    expect(links[0]).not.toHaveAttribute("aria-current");
    expect(dialog()!.querySelector("#settings-section-design h3")).toHaveFocus();
    // A link inside the dialog is not a navigation of the page.
    expect(window.location.hash).toBe("");
  });

  it("keeps its element ids apart from those of the page behind it", () => {
    renderSettings();
    fireEvent.click(gear());
    const fields = [...dialog()!.querySelectorAll<HTMLElement>(".field[id], .settings-panel[id]")];
    expect(fields.length).toBeGreaterThan(PANELS.length);
    for (const field of fields) expect(field.id.startsWith("settings-")).toBe(true);
    expect(dialog()!.querySelector("#settings-panel-title")).not.toBeNull();
    expect(dialog()!.querySelector("#settings-field-title")).not.toBeNull();
    const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("applies an edit to the shared study at once: there is nothing to apply", async () => {
    renderSettings();
    fireEvent.click(gear());
    fireEvent.change(inDialog().getByLabelText("Contact"), { target: { value: "new.contact@example.org" } });
    await waitFor(() => expect(storedDraft().contact).toBe("new.contact@example.org"));
    fireEvent.change(inDialog().getByLabelText("Study title"), { target: { value: "Renamed in the settings" } });
    // The study bar behind the dialog shows the same study.
    expect(document.querySelector(".study-bar__title")).toHaveTextContent("Renamed in the settings");
    expect(storedDraft().title).toBe("Renamed in the settings");
  });

  it("closes on Done, on Escape and on the backdrop, and gives the focus back to the gear", () => {
    renderSettings(smallStudy(), { target: { section: "judge" }, label: "Open on the judge" });
    fireEvent.click(gear());
    fireEvent.click(inDialog().getByRole("button", { name: "Done" }));
    expect(dialog()).toBeNull();
    expect(gear()).toHaveFocus();

    fireEvent.click(gear());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(dialog()).toBeNull();
    expect(gear()).toHaveFocus();

    // Opened from somewhere else, it still returns to the gear: that is where the settings live.
    fireEvent.click(screen.getByRole("button", { name: "Open on the judge" }));
    fireEvent.mouseDown(dialog()!.parentElement!);
    expect(dialog()).toBeNull();
    expect(gear()).toHaveFocus();
  });

  it("does not let an edit inside it take the focus away from what is being typed", () => {
    renderSettings();
    fireEvent.click(gear());
    const contact = inDialog().getByLabelText("Contact");
    contact.focus();
    fireEvent.change(contact, { target: { value: "a" } });
    fireEvent.change(contact, { target: { value: "ab" } });
    expect(contact).toHaveFocus();
  });
});

describe("opening on a place", () => {
  it("lands on a section", () => {
    renderSettings(smallStudy(), { target: { section: "advanced" }, label: "Open on Advanced" });
    fireEvent.click(screen.getByRole("button", { name: "Open on Advanced" }));
    expect(dialog()!.querySelector("#settings-section-advanced h3")).toHaveFocus();
    expect(within(inDialog().getByRole("navigation", { name: "Settings sections" })).getByRole("link", { current: true })).toHaveTextContent("Advanced");
  });

  it("lands on a setting named by its study key path", () => {
    renderSettings(smallStudy(), { target: { path: "design.alpha" }, label: "Open on alpha" });
    fireEvent.click(screen.getByRole("button", { name: "Open on alpha" }));
    expect(dialog()!.querySelector("#settings-field-design-alpha select")).toHaveFocus();
    expect(document.activeElement).toHaveAccessibleName(/^False-finding rate/);
    expect(within(inDialog().getByRole("navigation", { name: "Settings sections" })).getByRole("link", { current: true })).toHaveTextContent("Design & analysis");
  });

  it("lands on a setting named by a recommendation path, as a chip or a map node names it", () => {
    renderSettings(smallStudy(), { target: { path: "cards.tier0_pit_residuals.items" }, label: "Open on the prompts" });
    fireEvent.click(screen.getByRole("button", { name: "Open on the prompts" }));
    const focused = document.activeElement as HTMLElement;
    expect(dialog()!.contains(focused)).toBe(true);
    expect(focused.closest("[data-panel]")).toHaveAttribute("data-panel", "prompts");
    expect(focused.closest("tr")?.textContent).toContain("tier0_pit_residuals");
  });

  it("shows only the problems when asked to, and a later request starts afresh", () => {
    renderSettings(studyOneTemplate(), { target: { path: "title" }, label: "Open on the title" });
    fireEvent.click(document.querySelector<HTMLElement>(".study-bar .validity-pill")!);
    expect(problemsOnly()).toBeChecked();
    search("contact");
    fireEvent.click(inDialog().getByRole("button", { name: "Done" }));

    fireEvent.click(screen.getByRole("button", { name: "Open on the title" }));
    expect(problemsOnly()).not.toBeChecked();
    expect(filter()).toHaveValue("");
    expect(inDialog().getByLabelText("Study title")).toHaveFocus();
  });
});

describe("filter", () => {
  it("finds panels by their name, by the label of a field, and by a key of the study", () => {
    renderSettings();
    fireEvent.click(gear());
    expect(filter()).toHaveAttribute("aria-label", "Filter settings");

    // A panel's name.
    search("Margins");
    expect(shownPanels()).toContain("margins");
    expect(shownPanels()).not.toContain("title");
    expect(shownPanels()).not.toContain("estimates");

    // The label of a field, which only the drawn panel knows.
    search("reading speed");
    expect(shownPanels()).toEqual(["estimates"]);
    expect(shownSections()).toEqual(["Advanced"]);

    // A key path, as it is written in a study file.
    search("pilot.prompts");
    expect(shownPanels()).toEqual(["pilot"]);
    search("design.alpha");
    expect(shownPanels()).toEqual(["alpha"]);

    // Case and surrounding spaces do not matter.
    search("  FALSE-FINDING  ");
    expect(shownPanels()).toEqual(["alpha"]);

    search("");
    expect(shownPanels()).toEqual(PANELS.map((panel) => panel.id));
  });

  it("hides the sections it empties from the list of sections", () => {
    renderSettings();
    fireEvent.click(gear());
    search("reading speed");
    const items = [...dialog()!.querySelectorAll<HTMLElement>(".all-settings__nav li")];
    expect(items.filter((item) => !item.hidden).map((item) => item.querySelector(".all-settings__nav-label")?.textContent)).toEqual(["Advanced"]);
  });

  it("says so when nothing matches", () => {
    renderSettings();
    fireEvent.click(gear());
    search("no such setting");
    expect(shownPanels()).toEqual([]);
    expect(dialog()!.querySelector(".all-settings__empty")).toHaveTextContent('No settings match "no such setting".');
    search("title");
    expect(dialog()!.querySelector(".all-settings__empty")).toBeNull();
  });

  it("is cleared by the first Escape; the second closes the dialog", () => {
    renderSettings();
    fireEvent.click(gear());
    search("margins");
    fireEvent.keyDown(filter(), { key: "Escape" });
    expect(dialog()).not.toBeNull();
    expect(filter()).toHaveValue("");
    expect(shownPanels()).toEqual(PANELS.map((panel) => panel.id));
    fireEvent.keyDown(filter(), { key: "Escape" });
    expect(dialog()).toBeNull();
  });
});

describe("problems", () => {
  it("lists each problem at the top of its section, as an alert, in the words used everywhere else", () => {
    renderSettings(pinnedTemplate());
    fireEvent.click(gear());
    const study = dialog()!.querySelector<HTMLElement>("#settings-section-study")!;
    const alert = study.querySelector<HTMLElement>("[role='alert']")!;
    // The list comes before the section's panels.
    expect(alert.compareDocumentPosition(study.querySelector("[data-panel]")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const contact = within(alert).getByText("Replace the placeholder contact.").closest("li")!;
    expect(contact).toHaveAttribute("data-code", "review.contact_placeholder");
    expect(contact).toHaveClass("problem--error");
    expect(within(contact).getByText("Blocks launch")).toBeInTheDocument();

    const design = dialog()!.querySelector<HTMLElement>("#settings-section-design")!;
    const warning = within(design.querySelector<HTMLElement>("[role='alert']")!).getByText("No expectations recorded.").closest("li")!;
    expect(warning).toHaveClass("problem--warning");
    expect(within(warning).getByText("Warning")).toBeInTheDocument();
    // A section without problems has no list.
    expect(dialog()!.querySelector("#settings-section-advanced [role='alert']")).toBeNull();
  });

  it("says how each section stands in the list of sections, in words and with a glyph", () => {
    renderSettings(pinnedTemplate());
    fireEvent.click(gear());
    const state = (label: string) => [...dialog()!.querySelectorAll<HTMLElement>(".all-settings__nav a")].find((link) => link.querySelector(".all-settings__nav-label")?.textContent === label)!;
    expect(state("Study")).toHaveClass("has-errors");
    expect(state("Study").querySelector(".all-settings__nav-state")).toHaveTextContent("✕ 1 to fix");
    expect(state("Design & analysis")).toHaveClass("has-warnings");
    expect(state("Design & analysis").querySelector(".all-settings__nav-state")).toHaveTextContent("! 1 to check");
    expect(state("Advanced")).toHaveClass("is-clear");
    expect(state("Advanced").querySelector(".all-settings__nav-state")).toBeNull();
  });

  it("links a problem to its field, and the list goes when the problem is fixed", () => {
    renderSettings(pinnedTemplate());
    fireEvent.click(gear());
    const study = dialog()!.querySelector<HTMLElement>("#settings-section-study")!;
    fireEvent.click(within(study.querySelector<HTMLElement>("[role='alert']")!).getByRole("button", { name: "Show the setting" }));
    const contact = within(study).getByLabelText("Contact");
    expect(contact).toHaveFocus();
    fireEvent.change(contact, { target: { value: "lab@example.org" } });
    expect(study.querySelector("[role='alert']")).toBeNull();
    expect(document.querySelector(".study-bar .validity-pill")).toHaveTextContent("Ready · 1 to check");
  });

  it("warns, under the section that holds them, about the pins the Study 1 template ships without, and leads to the checkpoint", () => {
    renderSettings(studyOneTemplate());
    fireEvent.click(gear());
    const advanced = dialog()!.querySelector<HTMLElement>("#settings-section-advanced")!;
    const rows = [...advanced.querySelectorAll<HTMLElement>("[role='alert'] .problem")];
    expect(rows.map((row) => row.dataset.code)).toEqual(["compare.checkpoint_unpinned", "instruments.assets_unpinned"]);
    expect(rows.every((row) => row.classList.contains("problem--warning"))).toBe(true);
    // Offline there is no server checkpoint to offer, so the row leads to the field and offers no fix.
    expect(within(rows[0]!).queryByRole("button", { name: "Pin the server's checkpoint" })).toBeNull();
    fireEvent.click(within(rows[0]!).getByRole("button", { name: "Show the setting" }));
    const checkpoint = within(advanced).getByLabelText("Checkpoint");
    expect(checkpoint).toHaveFocus();
    // The placeholder is an example of the form, not a promise that something will fill it in.
    expect(checkpoint).toHaveAttribute("placeholder", "org/model@revision");
    fireEvent.change(checkpoint, { target: { value: "org/qr-llm@0123abc" } });
    expect([...advanced.querySelectorAll<HTMLElement>("[role='alert'] .problem")].map((row) => row.dataset.code)).toEqual(["instruments.assets_unpinned"]);
    // The hashes cannot be set here, and the page says so instead of "not frozen yet".
    expect(advanced.textContent).not.toMatch(/not frozen yet|frozen at readiness/);
    expect(within(advanced).getAllByText(/This page cannot set it\. It can be written into a study file by hand/)).toHaveLength(2);
  });

  it("offers a problem's one-click fix", () => {
    const template = studyOneTemplate();
    renderSettings({ ...template, design: null });
    fireEvent.click(gear());
    const design = dialog()!.querySelector<HTMLElement>("#settings-section-design")!;
    const row = within(design.querySelector<HTMLElement>("[role='alert']")!).getByText("This draft uses the fixed Study 1 plan.").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Declare a design" }));
    expect(within(design).queryByText("This draft uses the fixed Study 1 plan.")).toBeNull();
    expect(storedDraft().design).toMatchObject({ claim: "difference", alpha: 0.05 });
  });

  it("shows only the panels with a problem when Problems only is ticked", () => {
    renderSettings(pinnedTemplate());
    fireEvent.click(gear());
    fireEvent.click(problemsOnly());
    expect(shownPanels()).toEqual(["people", "expectations"]);
    expect(shownSections()).toEqual(["Study", "Design & analysis"]);
    // The problem lists stay; they are what the filter is about.
    expect(dialog()!.querySelectorAll("[role='alert'] .problem")).toHaveLength(2);
    fireEvent.click(problemsOnly());
    expect(shownPanels()).toEqual(PANELS.map((panel) => panel.id));
  });

  it("keeps a panel on screen while its problem is being fixed, and lets go of it when the box is unticked", () => {
    renderSettings(pinnedTemplate());
    fireEvent.click(gear());
    fireEvent.click(problemsOnly());
    const contact = inDialog().getByLabelText("Contact");
    contact.focus();
    fireEvent.change(contact, { target: { value: "lab@example.org" } });
    // The problem is gone; the field that fixed it is still there, with the focus.
    expect(dialog()!.querySelector("#settings-section-study [role='alert']")).toBeNull();
    expect(shownPanels()).toEqual(["people", "expectations"]);
    expect(contact).toHaveFocus();
    fireEvent.click(problemsOnly());
    fireEvent.click(problemsOnly());
    expect(shownPanels()).toEqual(["expectations"]);
    // Opened again on the problems, it starts from the problems there are now.
    fireEvent.click(inDialog().getByRole("button", { name: "Done" }));
    fireEvent.click(document.querySelector<HTMLElement>(".study-bar .validity-pill")!);
    expect(shownPanels()).toEqual(["expectations"]);
  });

  it("combines Problems only with the filter", () => {
    renderSettings(pinnedTemplate());
    fireEvent.click(gear());
    fireEvent.click(problemsOnly());
    search("contact");
    expect(shownPanels()).toEqual(["people"]);
    search("expect");
    expect(shownPanels()).toEqual(["expectations"]);
  });

  it("says that a study has no problems instead of showing an empty dialog", () => {
    renderSettings(smallStudy());
    fireEvent.click(gear());
    fireEvent.click(problemsOnly());
    expect(shownPanels()).toEqual([]);
    expect(dialog()!.querySelector(".all-settings__empty")).toHaveTextContent('This study has no problems. Untick "Problems only" to see every setting.');
  });

  it("keeps Problems only ticked when it shows the setting of a problem", () => {
    renderSettings(pinnedTemplate(), { target: { problemsOnly: true }, label: "Open on the problems" });
    fireEvent.click(screen.getByRole("button", { name: "Open on the problems" }));
    expect(shownPanels()).toEqual(["people", "expectations"]);
    const study = dialog()!.querySelector<HTMLElement>("#settings-section-study")!;
    fireEvent.click(within(study.querySelector<HTMLElement>("[role='alert']")!).getByRole("button", { name: "Show the setting" }));
    // Its panel has a problem, so the filter stays as it was.
    expect(problemsOnly()).toBeChecked();
    expect(within(study).getByLabelText("Contact")).toHaveFocus();
  });
});

describe("footer", () => {
  it("has the file state, Load, Save and Done", () => {
    renderSettings(studyOneTemplate());
    fireEvent.click(gear());
    const footer = within(dialog()!.querySelector<HTMLElement>(".modal__footer")!);
    expect(footer.getByRole("status")).toHaveTextContent("Not saved to a file yet");
    expect(footer.getByLabelText("Load study file")).toHaveAttribute("type", "file");
    expect(footer.getByRole("button", { name: "Save study file" })).toBeEnabled();
    expect(footer.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("saves the study as edited in the dialog, and both file states follow", async () => {
    const downloads = captureDownloads();
    renderSettings(studyOneTemplate());
    fireEvent.click(gear());
    fireEvent.change(inDialog().getByLabelText("Contact"), { target: { value: "lab@example.org" } });
    const footer = within(dialog()!.querySelector<HTMLElement>(".modal__footer")!);
    fireEvent.click(footer.getByRole("button", { name: "Save study file" }));
    const expected = { ...studyOneTemplate(), contact: "lab@example.org" };
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml(expected));
    const name = studyFileName(expected.title);
    // The test browser has no save dialog, so the file is a download and both states say so.
    await waitFor(() => expect(footer.getByRole("status")).toHaveTextContent(`Downloaded as ${name}`));
    expect(document.querySelector(".study-bar .file-state")).toHaveTextContent(`Downloaded as ${name}`);
    fireEvent.change(inDialog().getByLabelText("Contact"), { target: { value: "other@example.org" } });
    expect(footer.getByRole("status")).toHaveTextContent(`Changed since ${name}`);
  });

  it("loads a file from the footer and reports it inside the dialog, which stays open", async () => {
    const loaded = { ...smallStudy(), title: "Loaded in the dialog" };
    vi.spyOn(api, "parseConfig").mockResolvedValue({ config: loaded, config_hash: "c".repeat(64), unknown_cards: [] });
    // The study on screen is the untouched template, so nothing is asked.
    renderSettings(studyOneTemplate());
    fireEvent.click(gear());
    const footer = within(dialog()!.querySelector<HTMLElement>(".modal__footer")!);
    fireEvent.change(footer.getByLabelText("Load study file"), { target: { files: [new File(["x"], "small.study.yaml")] } });
    await waitFor(() => expect(dialog()!.querySelector(".load-report[data-report='loaded']")).not.toBeNull(), { timeout: 20_000 });
    expect(dialog()!.querySelector(".load-report")).toHaveTextContent("Loaded small.study.yaml: 7 arms, 4 evals, 2 hypotheses.");
    expect(inDialog().getByLabelText("Study title")).toHaveValue("Loaded in the dialog");
    expect(footer.getByRole("status")).toHaveTextContent("Saved to small.study.yaml");
  });

  it("leaves the study alone when the file loaded from the footer is refused", async () => {
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("the file does not hold a study configuration", 400));
    renderSettings(smallStudy());
    fireEvent.click(gear());
    const before = JSON.stringify(storedDraft());
    const footer = within(dialog()!.querySelector<HTMLElement>(".modal__footer")!);
    fireEvent.change(footer.getByLabelText("Load study file"), { target: { files: [new File(["- a list"], "list.yaml")] } });
    await waitFor(() => expect(dialog()!.querySelector(".load-report[data-report='failed']")).not.toBeNull(), { timeout: 20_000 });
    expect(dialog()!.querySelector(".load-report")).toHaveTextContent('"list.yaml" was not loaded: the file does not hold a study configuration');
    expect(JSON.stringify(storedDraft())).toBe(before);
    expect(inDialog().getByLabelText("Study title")).toHaveValue("A small study");
  });
});
