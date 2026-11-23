import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api, ApiError } from "../../lib/api";
import { CONFIG_STORAGE_KEY, decodeShareConfig, encodeShareConfig, studyOneTemplate } from "../../lib/config-store";
import { FILE_MARK_STORAGE_KEY, STUDY_FILE_ACCEPT, fileMarkOf, readFileMark, studyFileName, toStudyYaml, writeFileMark } from "../../lib/study-file";
import { useStudy } from "../../routes/configure/StudyContext";
import { BarHarness, blobText, captureDownloads, fakeSavePicker, pinnedTemplate, resetBrowser, smallStudy, storeDraft, storedDraft } from "./study-test-kit";

vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => {
  resetBrowser();
  // The bar is tested without a server: the bundled eval registry and the study's own models stand in.
  vi.spyOn(api, "cards").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "models").mockRejectedValue(new TypeError("Failed to fetch"));
  // A save asks the server to read the written text back. No test here reaches a real server: by default it cannot be asked, and the page's own check answers.
  vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const bar = () => within(screen.getByRole("region", { name: "Study" }));
const fileState = () => document.querySelector<HTMLElement>(".study-bar .file-state");
const pill = () => document.querySelector<HTMLElement>(".study-bar .validity-pill");

/** Stands in for the views of the study: what they would show of it, and an edit made in one of them. */
function Probe() {
  const { config, updateConfig, view, step } = useStudy();
  return <div>
    <output data-testid="view">{view}</output>
    <output data-testid="step">{step.id}</output>
    <output data-testid="contact">{config.contact}</output>
    <button type="button" onClick={() => updateConfig({ ...config, contact: "lab@example.org" })}>Edit the contact</button>
    <button type="button" onClick={() => updateConfig({ ...config, title: "" })}>Clear the title</button>
    <output data-testid="title">{config.title}</output>
  </div>;
}

function renderBar(config = studyOneTemplate()) {
  storeDraft(config);
  return render(<BarHarness><Probe /></BarHarness>);
}

describe("study bar", () => {
  it("shows the title, how the study stands, the view, the file state and the file actions", () => {
    renderBar();
    expect(bar().getByRole("button", { name: `Edit the title of ${studyOneTemplate().title}` })).toBeInTheDocument();
    expect(pill()).toHaveTextContent("1 thing to fix");
    expect(within(bar().getByRole("radiogroup", { name: "Configuration view" })).getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Steps", "Map"]);
    expect(bar().getByRole("status")).toHaveTextContent("Not saved to a file yet");
    expect(bar().getByLabelText("Load study file")).toHaveAttribute("accept", STUDY_FILE_ACCEPT);
    expect(bar().getByLabelText("Load study file")).toHaveAttribute("type", "file");
    expect(bar().getByRole("button", { name: "Save study file" })).toBeEnabled();
    const gear = bar().getByRole("button", { name: "All settings" });
    expect(gear).toHaveClass("icon-button");
    // The gear is drawn here, not taken from an icon set.
    expect(gear.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(bar().getByRole("button", { name: "More" })).toHaveAttribute("aria-haspopup", "menu");
  });

  it("names a study without a title, so the bar never shows an empty button", () => {
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
    expect(bar().getByRole("button", { name: "Edit the title of Untitled study" })).toBeInTheDocument();
  });
});

describe("validity pill", () => {
  it("counts what blocks a launch, in words and with a glyph, and opens the settings on the problems", () => {
    renderBar();
    const blocked = pill()!;
    // Study 1 as it ships: a placeholder contact (blocks) and no expectations recorded (a warning).
    expect(blocked.tagName).toBe("BUTTON");
    expect(blocked).toHaveClass("validity-pill--fix");
    expect(blocked).toHaveTextContent("✕1 thing to fix");
    fireEvent.click(blocked);
    const dialog = within(screen.getByRole("dialog", { name: "Everything in this study" }));
    expect(dialog.getByRole("checkbox", { name: "Problems only" })).toBeChecked();
  });

  it("says Ready with what is left to check when nothing blocks", () => {
    renderBar({ ...smallStudy(), expectations: "" });
    expect(pill()).toHaveClass("validity-pill--check");
    expect(pill()).toHaveTextContent("!Ready · 1 to check");
    expect(pill()!.tagName).toBe("BUTTON");
  });

  it("says Ready, and is not a button, when the study has no problem", () => {
    renderBar(smallStudy());
    expect(pill()).toHaveClass("validity-pill--ready");
    expect(pill()).toHaveTextContent("✓Ready");
    expect(pill()!.tagName).toBe("SPAN");
  });

  it("follows the study as it is edited", () => {
    renderBar(pinnedTemplate());
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    expect(pill()).toHaveTextContent("Ready · 1 to check");
  });

  it("does not say plain Ready of the Study 1 template with its contact filled in: the document it would freeze opens with validity warnings", () => {
    renderBar({ ...studyOneTemplate(), expectations: "A difference on the probe." });
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    // No model checkpoint and no hashes of the session assets: two warnings, and the pill is not the check mark.
    expect(pill()).toHaveClass("validity-pill--check");
    expect(pill()).toHaveTextContent("!Ready · 2 to check");
    expect(pill()!.tagName).toBe("BUTTON");
  });
});

describe("view switch", () => {
  it("is a radio group that mirrors the view in the address without navigating", () => {
    renderBar();
    const [steps, map] = bar().getAllByRole("radio");
    expect(steps).toBeChecked();
    expect(map).not.toBeChecked();
    expect(steps).toHaveAttribute("tabindex", "0");
    expect(map).toHaveAttribute("tabindex", "-1");

    const entries = window.history.length;
    fireEvent.click(map!);
    expect(map).toBeChecked();
    expect(steps).not.toBeChecked();
    expect(screen.getByTestId("view")).toHaveTextContent("map");
    expect(window.location.search).toBe("?view=map");
    // The entry is replaced: the back button does not step through views.
    expect(window.history.length).toBe(entries);

    fireEvent.click(steps!);
    expect(window.location.search).toBe("");
    expect(screen.getByTestId("view")).toHaveTextContent("steps");
  });

  it("moves with the arrow keys and takes the focus with it", () => {
    renderBar();
    const [steps, map] = bar().getAllByRole("radio");
    steps!.focus();
    fireEvent.keyDown(steps!, { key: "ArrowRight" });
    expect(map).toBeChecked();
    expect(map).toHaveFocus();
    fireEvent.keyDown(map!, { key: "ArrowLeft" });
    expect(steps).toBeChecked();
    expect(steps).toHaveFocus();
  });

  it("opens on the map when the address asks for it, and keeps other parameters when it switches", () => {
    window.history.pushState({}, "", "/?view=map&open=card:unpredictabench");
    renderBar();
    expect(bar().getByRole("radio", { name: "Map" })).toBeChecked();
    fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
    expect(window.location.search).toBe("?open=card%3Aunpredictabench");
  });
});

describe("file state and saving", () => {
  it("saves the study as it stands, problems and all, and says where it went", async () => {
    const downloads = captureDownloads();
    const config = studyOneTemplate();
    renderBar(config);
    const name = studyFileName(config.title);
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    expect(downloads.names).toEqual([name]);
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml(config));
    // This browser has no save dialog, so the file went to the downloads and the page cannot follow it: the
    // state says "Downloaded as", never "Saved to". It is still not ready, and the state says that too.
    await waitFor(() => expect(fileState()).toHaveTextContent(`Downloaded as ${name} · 1 thing to fix`));
    expect(fileState()?.textContent).not.toContain("Saved to");
    expect(fileState()).toHaveAttribute("data-file-state", "saved");
    expect(fileState()).toHaveAttribute("role", "status");
    expect(readFileMark()).toEqual(fileMarkOf(name, config, "downloaded"));
  });

  it("reads Changed since the file after an edit, and Downloaded again after the next save", async () => {
    const downloads = captureDownloads();
    renderBar();
    const name = studyFileName(studyOneTemplate().title);
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(fileState()).toHaveAttribute("data-file-state", "saved"));
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    expect(fileState()).toHaveTextContent(`Changed since ${name}`);
    expect(fileState()).toHaveAttribute("data-file-state", "changed");
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    // The contact was the one thing that blocked; with it fixed nothing is left to say after the name.
    await waitFor(() => expect(fileState()?.textContent).toBe(`Downloaded as ${name}`));
    expect(downloads.blobs).toHaveLength(2);
  });

  it("writes the file itself where the browser has a save dialog, and says Saved to the name that was picked only once the file is closed", async () => {
    const downloads = captureDownloads();
    const picker = fakeSavePicker("pick", "my thesis study.study.yaml");
    const config = studyOneTemplate();
    renderBar(config);
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(fileState()).toHaveTextContent("Saved to my thesis study.study.yaml · 1 thing to fix"));
    expect(picker.asked).toEqual([expect.objectContaining({ suggestedName: studyFileName(config.title) })]);
    expect(picker.files.get("my thesis study.study.yaml")).toEqual({ written: [toStudyYaml(config)], closed: 1, failWrite: false });
    expect(readFileMark()).toEqual(fileMarkOf("my thesis study.study.yaml", config, "written"));
    expect(downloads.blobs).toEqual([]);

    // A later Save writes the same file without asking; "Save as" asks again.
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    expect(fileState()).toHaveTextContent("Changed since my thesis study.study.yaml");
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(fileState()?.textContent).toBe("Saved to my thesis study.study.yaml"));
    expect(picker.asked).toHaveLength(1);
    expect(picker.files.get("my thesis study.study.yaml")!.written).toHaveLength(2);
    expect(picker.files.get("my thesis study.study.yaml")!.written[1]).toContain('contact: "lab@example.org"');

    picker.state.pickedName = "a copy.study.yaml";
    fireEvent.click(bar().getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Save as…" }));
    await waitFor(() => expect(fileState()?.textContent).toBe("Saved to a copy.study.yaml"));
    expect(picker.asked).toHaveLength(2);
    // From now on Save goes to the copy.
    fireEvent.click(bar().getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Save" }));
    await waitFor(() => expect(picker.files.get("a copy.study.yaml")!.closed).toBe(2));
    expect(picker.asked).toHaveLength(2);
  });

  it("treats a save dialog that was closed as nothing happened, so unsaved work is still asked about", async () => {
    const downloads = captureDownloads();
    const picker = fakeSavePicker("cancel");
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    await waitFor(() => expect(picker.asked).toHaveLength(1));
    // Let the cancelled save settle.
    await Promise.resolve();
    await Promise.resolve();
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    expect(readFileMark()).toBeNull();
    expect(downloads.blobs).toEqual([]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // The study has work in no file: replacing it asks first, and "Save current study first" with the dialog
    // closed again keeps both the question and the study.
    fireEvent.click(bar().getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
    const dialog = within(screen.getByRole("dialog", { name: "Replace the study you are editing?" }));
    expect(dialog.getByText(/asks where to save it, writes the file, checks that the file will load back and then replaces the study\. If you close that dialog, or the file will not load back, nothing is replaced\./)).toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Save current study first" }));
    await waitFor(() => expect(picker.asked).toHaveLength(2));
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.getByRole("dialog", { name: "Replace the study you are editing?" })).toBeInTheDocument();
    expect(screen.getByTestId("contact")).toHaveTextContent("lab@example.org");

    // Picking a file this time saves and then replaces.
    picker.state.answer = "pick";
    fireEvent.click(dialog.getByRole("button", { name: "Save current study first" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Replace the study you are editing?" })).not.toBeInTheDocument());
    expect(picker.files.get("picked.study.yaml")!.written[0]).toContain('contact: "lab@example.org"');
    expect(screen.getByTestId("contact")).toHaveTextContent("REPLACE_WITH_STUDY_CONTACT");
  });

  it("does not replace a study on the strength of a file that will not load back, whoever says that it will not", async () => {
    const downloads = captureDownloads();
    renderBar({ ...pinnedTemplate(), contact: "lab@example.org" });
    fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
    const startOver = () => { fireEvent.click(bar().getByRole("button", { name: "More" })); fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" })); };
    const question = () => screen.queryByRole("dialog", { name: "Replace the study you are editing?" });
    startOver();
    // What the page's own check knows it says before anything is saved.
    expect(within(question()!).getByRole("alert")).toHaveTextContent("As it stands, a study file of this study will not load back: The setting title is empty. Fix that first, or the file that “Save current study first” writes replaces nothing.");

    // The file is written, as every save is. The server reads its text and refuses it, so the study stays and the question with it.
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all)", 400));
    fireEvent.click(within(question()!).getByRole("button", { name: "Save current study first" }));
    await waitFor(() => expect(within(question()!).getByRole("alert")).toHaveTextContent("The file was written, but it will not load back as it is, so nothing was replaced. The server read the same text and refuses it: not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all). The setting it names is title. Fix that in the study and save again."));
    expect(downloads.names).toEqual(["study.study.yaml"]);
    expect(screen.getByTestId("contact")).toHaveTextContent("lab@example.org");
    expect(readFileMark()).toMatchObject({ name: "study.study.yaml", loadsBack: false });

    // The same without the server: the page's own check is the one that answers.
    vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
    fireEvent.click(within(question()!).getByRole("button", { name: "Save current study first" }));
    await waitFor(() => expect(within(question()!).getByRole("alert")).toHaveTextContent("The file was written, but it will not load back as it is, so nothing was replaced. The setting title is empty. The server could not be asked, so this is the page's own check. Fix that and save again."));
    expect(downloads.names).toHaveLength(2);
    expect(screen.getByTestId("contact")).toHaveTextContent("lab@example.org");

    // "Cancel" keeps the study. It equals the file that was written, and that file is no way back to it: the
    // next replace asks again, and says why.
    fireEvent.click(within(question()!).getByRole("button", { name: "Cancel" }));
    expect(question()).toBeNull();
    expect(fileState()).toHaveTextContent("Downloaded as study.study.yaml · will not load back as it is · edits are not kept in this browser");
    startOver();
    expect(within(question()!).getByText(/The study you are editing was written to study\.study\.yaml, but that file will not load back as it is\./)).toBeInTheDocument();
    // "Replace" is the one answer that discards it, and it is the researcher's to give.
    fireEvent.click(within(question()!).getByRole("button", { name: "Replace" }));
    expect(question()).toBeNull();
    expect(screen.getByTestId("contact")).toHaveTextContent("REPLACE_WITH_STUDY_CONTACT");
  });

  it("replaces after Save current study first once the file is one that loads back", async () => {
    const downloads = captureDownloads();
    renderBar({ ...pinnedTemplate(), contact: "lab@example.org" });
    const parse = vi.spyOn(api, "parseConfig").mockResolvedValue({ config: { ...pinnedTemplate(), contact: "lab@example.org" }, config_hash: "c".repeat(64), unknown_cards: [] });
    fireEvent.click(bar().getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
    const question = () => screen.queryByRole("dialog", { name: "Replace the study you are editing?" });
    // Nothing is said beforehand about a study the page can hold.
    expect(within(question()!).queryByRole("alert")).toBeNull();
    fireEvent.click(within(question()!).getByRole("button", { name: "Save current study first" }));
    await waitFor(() => expect(question()).toBeNull());
    expect(parse).toHaveBeenCalledExactlyOnceWith({ text: await blobText(downloads.blobs[0]!) }, expect.any(AbortSignal));
    expect(screen.getByTestId("contact")).toHaveTextContent("REPLACE_WITH_STUDY_CONTACT");
  });

  describe("a save to the file of an earlier save, which opens no dialog", () => {
    const refusal = new ApiError("not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all)", 400);
    const overwrite = () => screen.queryByRole("dialog", { name: "Overwrite picked.study.yaml with a file that will not load?" });
    const file = (picker: ReturnType<typeof fakeSavePicker>) => picker.files.get("picked.study.yaml")!;
    /** A study that loads, saved once through the dialog, so the page holds the handle of a good file. */
    async function savedOnce() {
      captureDownloads();
      const picker = fakeSavePicker();
      const good = { ...pinnedTemplate(), contact: "first@example.org" };
      renderBar(good);
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      await waitFor(() => expect(fileState()?.textContent).toBe("Saved to picked.study.yaml"));
      expect(file(picker).written).toEqual([toStudyYaml(good)]);
      return { picker, good };
    }

    it("checks before it writes, and asks before a file that loads is overwritten by one that will not", async () => {
      const { picker, good } = await savedOnce();
      fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
      const parse = vi.spyOn(api, "parseConfig").mockRejectedValue(refusal);
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      const dialog = within(await screen.findByRole("dialog", { name: "Overwrite picked.study.yaml with a file that will not load?" }));
      // The server was asked about the text, and nothing has been written: the good file is whole.
      expect(parse).toHaveBeenCalledExactlyOnceWith({ text: toStudyYaml({ ...good, title: "" }) }, expect.any(AbortSignal));
      expect(file(picker)).toMatchObject({ written: [toStudyYaml(good)], closed: 1 });
      expect(picker.asked).toHaveLength(1);
      expect(dialog.getByText("Save writes to picked.study.yaml, the file of your last save, without asking where. Nothing has been written yet.")).toBeInTheDocument();
      expect(dialog.getByRole("alert")).toHaveTextContent("A file of the study as it stands will not load back. The server read the same text and refuses it: not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all). The setting it names is title. Fix that in the study and save again.");
      expect(dialog.getAllByRole("button").map((button) => button.textContent)).toEqual(["×", "Keep the file as it is", "Overwrite it"]);

      // Keeping the file writes nothing and says so; the study on screen is untouched.
      fireEvent.click(dialog.getByRole("button", { name: "Keep the file as it is" }));
      await waitFor(() => expect(overwrite()).toBeNull());
      expect(await screen.findByText(/^Nothing was written, so picked\.study\.yaml is as it was\. A file of the study as it stands will not load back\. The server read the same text and refuses it/)).toBeInTheDocument();
      expect(file(picker)).toMatchObject({ written: [toStudyYaml(good)], closed: 1 });
      expect(readFileMark()).toEqual(fileMarkOf("picked.study.yaml", good, "written"));
      expect(fileState()).toHaveTextContent("Changed since picked.study.yaml");
      expect(screen.getByTestId("title")).toHaveTextContent("");

      // Closing the question any other way is the same answer.
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      fireEvent.click(within(await screen.findByRole("dialog", { name: "Overwrite picked.study.yaml with a file that will not load?" })).getByRole("button", { name: "Close dialog" }));
      await waitFor(() => expect(overwrite()).toBeNull());
      expect(file(picker).written).toHaveLength(1);

      // Saying yes writes the file, and the page says what it now holds.
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      fireEvent.click(within(await screen.findByRole("dialog", { name: "Overwrite picked.study.yaml with a file that will not load?" })).getByRole("button", { name: "Overwrite it" }));
      await waitFor(() => expect(file(picker).written).toHaveLength(2));
      expect(file(picker).written[1]).toBe(toStudyYaml({ ...good, title: "" }));
      await waitFor(() => expect(fileState()).toHaveTextContent("Saved to picked.study.yaml · will not load back as it is"));
      expect(readFileMark()).toMatchObject({ name: "picked.study.yaml", loadsBack: false });

      // The file on disk no longer loads, so there is nothing good left to ask about: the next save is written as before.
      fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      await waitFor(() => expect(file(picker).written).toHaveLength(3));
      expect(overwrite()).toBeNull();
      expect(picker.asked).toHaveLength(1);
    });

    it("asks nothing when the text loads back, and nothing on the first save through the dialog, which is checked after the write", async () => {
      const { picker, good } = await savedOnce();
      const parse = vi.spyOn(api, "parseConfig").mockResolvedValue({ config: good, config_hash: "c".repeat(64), unknown_cards: [] });
      fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
      fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
      await waitFor(() => expect(file(picker).written).toHaveLength(2));
      expect(parse).toHaveBeenCalledTimes(1);
      expect(overwrite()).toBeNull();
      expect(await screen.findByText("Saved to picked.study.yaml. The server read the same text back and accepts it, so the file will load.")).toBeInTheDocument();

      // "Save as" opens the dialog, so it keeps today's order: written first, then said not to load.
      parse.mockRejectedValue(refusal);
      fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
      picker.state.pickedName = "another.study.yaml";
      fireEvent.click(bar().getByRole("button", { name: "More" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Save as…" }));
      await waitFor(() => expect(picker.files.get("another.study.yaml")?.closed).toBe(1));
      expect(screen.queryByRole("dialog")).toBeNull();
      await waitFor(() => expect(fileState()).toHaveTextContent("Saved to another.study.yaml · will not load back as it is"));
      expect(file(picker).written).toHaveLength(2);
    });

    it("says in the replace question that Save current study first writes to that file without asking, and leaves a file that loads alone", async () => {
      const { picker, good } = await savedOnce();
      fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
      vi.spyOn(api, "parseConfig").mockRejectedValue(refusal);
      fireEvent.click(bar().getByRole("button", { name: "More" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
      const question = () => within(screen.getByRole("dialog", { name: "Replace the study you are editing?" }));
      expect(question().getByText(/“Save current study first” does not ask where to save it: it checks that a file of the study will load back, writes it to picked\.study\.yaml, the file of your last save, and then replaces the study\. If the file would not load back, nothing is written and nothing is replaced\. If picked\.study\.yaml can no longer be written, it asks where to save instead\./)).toBeInTheDocument();
      expect(question().queryByText(/asks where to save it, writes the file/)).toBeNull();
      fireEvent.click(question().getByRole("button", { name: "Save current study first" }));
      await waitFor(() => expect(question().getByRole("alert")).toHaveTextContent("Nothing was written, so picked.study.yaml is as it was, and nothing was replaced. A file of the study as it stands will not load back. The server read the same text and refuses it:"));
      expect(file(picker)).toMatchObject({ written: [toStudyYaml(good)], closed: 1 });
      expect(picker.asked).toHaveLength(1);
      expect(overwrite()).toBeNull();
      expect(screen.getByTestId("contact")).toHaveTextContent("first@example.org");
    });

    it("writes to that file without asking and then replaces, when the study loads back", async () => {
      const { picker, good } = await savedOnce();
      fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
      vi.spyOn(api, "parseConfig").mockResolvedValue({ config: good, config_hash: "c".repeat(64), unknown_cards: [] });
      fireEvent.click(bar().getByRole("button", { name: "More" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
      fireEvent.click(within(screen.getByRole("dialog", { name: "Replace the study you are editing?" })).getByRole("button", { name: "Save current study first" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(file(picker).written).toHaveLength(2);
      expect(picker.asked).toHaveLength(1);
      expect(screen.getByTestId("contact")).toHaveTextContent("REPLACE_WITH_STUDY_CONTACT");
    });
  });

  it("says that nothing was saved when the picked file cannot be written", async () => {
    captureDownloads();
    const picker = fakeSavePicker();
    picker.handleFor("picked.study.yaml");
    picker.files.get("picked.study.yaml")!.failWrite = true;
    renderBar();
    fireEvent.click(bar().getByRole("button", { name: "Save study file" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The study could not be written to a file: The disk is full. Nothing was saved.");
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    expect(readFileMark()).toBeNull();
  });

  it("saves a study this page cannot even keep as a draft", async () => {
    const downloads = captureDownloads();
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
    const save = bar().getByRole("button", { name: "Save study file" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(downloads.names).toEqual(["study.study.yaml"]);
    expect(await blobText(downloads.blobs[0]!)).toContain('title: ""\n');
    await waitFor(() => expect(fileState()).toHaveTextContent("Downloaded as study.study.yaml"));
  });

  it("remembers the file across a visit, and reads the draft against it", () => {
    const config = smallStudy();
    storeDraft(config);
    writeFileMark(fileMarkOf("small.study.yaml", config));
    const first = render(<BarHarness><Probe /></BarHarness>);
    expect(fileState()).toHaveTextContent("Saved to small.study.yaml");
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    expect(fileState()?.textContent).toBe("Saved to small.study.yaml");
    first.unmount();

    storeDraft({ ...config, contact: "someone.else@example.org" });
    render(<BarHarness><Probe /></BarHarness>);
    expect(fileState()).toHaveTextContent("Changed since small.study.yaml");
  });

  it("forgets the file when the study arrived by a share link, which is no file's study", () => {
    writeFileMark(fileMarkOf("mine.study.yaml", smallStudy()));
    window.history.pushState({}, "", `/?c=${encodeShareConfig({ ...smallStudy(), title: "Sent by a colleague" }) ?? ""}`);
    render(<BarHarness><Probe /></BarHarness>);
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    expect(window.localStorage.getItem(FILE_MARK_STORAGE_KEY)).toBeNull();
  });
});

describe("a share link opened over a stored draft", () => {
  const colleague = () => ({ ...smallStudy(), title: "Sent by a colleague" });
  const openLink = () => window.history.pushState({}, "", `/?view=map&c=${encodeShareConfig(colleague()) ?? ""}`);
  const question = () => screen.queryByRole("dialog", { name: "Replace the study you are editing?" });

  it("takes the draft's place at once when the draft is saved to a file, and forgets that file", () => {
    storeDraft(smallStudy());
    writeFileMark(fileMarkOf("mine.study.yaml", smallStudy(), "written"));
    openLink();
    render(<BarHarness><Probe /></BarHarness>);
    expect(question()).not.toBeInTheDocument();
    expect(screen.getByTestId("title")).toHaveTextContent("Sent by a colleague");
    expect(storedDraft().title).toBe("Sent by a colleague");
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    expect(readFileMark()).toBeNull();
    expect(window.location.search).toBe("?view=map");
  });

  it("asks before replacing a draft with work that is in no file, and Cancel keeps the draft and its file", () => {
    const mine = { ...smallStudy(), expectations: "Three hours of edits." };
    storeDraft(mine);
    writeFileMark(fileMarkOf("mine.study.yaml", smallStudy(), "written"));
    openLink();
    render(<BarHarness><Probe /></BarHarness>);

    // The draft is on screen and in storage, untouched, while the question is open.
    const dialog = within(question()!);
    expect(dialog.getByText(/The study from the share link you opened is ready to take its place\. The study you are editing has changed since mine\.study\.yaml\./)).toBeInTheDocument();
    expect(screen.getByTestId("title")).toHaveTextContent("A small study");
    expect(storedDraft()).toEqual(JSON.parse(JSON.stringify(mine)));
    expect(window.location.search).toContain("c=");

    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    expect(question()).not.toBeInTheDocument();
    expect(screen.getByTestId("title")).toHaveTextContent("A small study");
    expect(storedDraft().expectations).toBe("Three hours of edits.");
    expect(fileState()).toHaveTextContent("Changed since mine.study.yaml");
    expect(readFileMark()?.name).toBe("mine.study.yaml");
    // The link is used up: a reload opens the draft and does not ask again.
    expect(window.location.search).toBe("?view=map");
    expect(document.querySelector("[data-report='note']")).toHaveTextContent("The share link was not opened. The study you were editing is unchanged.");
  });

  it("replaces the draft on Replace, with no file mark left over from the study it replaced", () => {
    storeDraft({ ...smallStudy(), expectations: "Three hours of edits." });
    writeFileMark(fileMarkOf("mine.study.yaml", smallStudy(), "written"));
    openLink();
    render(<BarHarness><Probe /></BarHarness>);
    fireEvent.click(within(question()!).getByRole("button", { name: "Replace" }));
    expect(question()).not.toBeInTheDocument();
    expect(screen.getByTestId("title")).toHaveTextContent("Sent by a colleague");
    expect(storedDraft().title).toBe("Sent by a colleague");
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    expect(readFileMark()).toBeNull();
    expect(window.location.search).toBe("?view=map");
    expect(document.querySelector("[data-report='note']")).toHaveTextContent("The study from the share link is loaded. It is not saved to a file yet.");
  });

  it("saves the draft first on Save current study first, and only then opens the link", async () => {
    const downloads = captureDownloads();
    storeDraft({ ...smallStudy(), expectations: "Three hours of edits." });
    openLink();
    render(<BarHarness><Probe /></BarHarness>);
    fireEvent.click(within(question()!).getByRole("button", { name: "Save current study first" }));
    await waitFor(() => expect(screen.getByTestId("title")).toHaveTextContent("Sent by a colleague"));
    expect(await blobText(downloads.blobs[0]!)).toContain('expectations: "Three hours of edits."');
    expect(readFileMark()).toBeNull();
  });

  it("opens without a question when the link holds the draft itself, and keeps the draft's file", () => {
    storeDraft(colleague());
    writeFileMark(fileMarkOf("theirs.study.yaml", colleague(), "loaded"));
    openLink();
    render(<BarHarness><Probe /></BarHarness>);
    expect(question()).not.toBeInTheDocument();
    expect(fileState()).toHaveTextContent("Saved to theirs.study.yaml");
    expect(window.location.search).toBe("?view=map");
  });
});

describe("another tab of the setup", () => {
  it("is followed: its edit shows here, and a replace asks about the study as it now stands", () => {
    const config = smallStudy();
    storeDraft(config);
    writeFileMark(fileMarkOf("small.study.yaml", config, "written"));
    render(<BarHarness><Probe /></BarHarness>);
    expect(fileState()).toHaveTextContent("Saved to small.study.yaml");

    // The other tab edits the study. It writes the draft; this tab hears of it through the storage event.
    const edited = { ...config, title: "Renamed in the other tab", expectations: "Typed in the other tab." };
    window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(edited));
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify(edited) }));
    expect(screen.getByTestId("title")).toHaveTextContent("Renamed in the other tab");
    expect(fileState()).toHaveTextContent("Changed since small.study.yaml");

    // An edit here is made on top of the other tab's, not on a copy from before it.
    fireEvent.click(screen.getByRole("button", { name: "Edit the contact" }));
    expect(storedDraft()).toMatchObject({ title: "Renamed in the other tab", expectations: "Typed in the other tab.", contact: "lab@example.org" });

    // Replacing the study now asks: the work of the other tab is in no file.
    fireEvent.click(bar().getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
    expect(screen.getByRole("dialog", { name: "Replace the study you are editing?" })).toBeInTheDocument();
  });

  it("is followed when it saves or loads a file, and is ignored when what it wrote is not a study", () => {
    const config = smallStudy();
    storeDraft(config);
    render(<BarHarness><Probe /></BarHarness>);
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    writeFileMark(fileMarkOf("from-the-other-tab.study.yaml", config, "written"));
    fireEvent(window, new StorageEvent("storage", { key: FILE_MARK_STORAGE_KEY, newValue: window.localStorage.getItem(FILE_MARK_STORAGE_KEY) }));
    expect(fileState()).toHaveTextContent("Saved to from-the-other-tab.study.yaml");

    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: "{not json" }));
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify({ title: "not a study" }) }));
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: null }));
    expect(screen.getByTestId("title")).toHaveTextContent("A small study");
  });
});

describe("More menu", () => {
  const toggle = () => bar().getByRole("button", { name: "More" });

  it("opens a menu with the template and the share link, and closes on Escape with the focus back on More", () => {
    renderBar();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    const menu = within(screen.getByRole("menu", { name: "More study actions" }));
    expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Load study file", "Save", "Save as…", "Start from Study 1 template", "Copy share link"]);
    // Without a save dialog in the browser, "Save as" cannot ask where: the menu says what it does instead.
    expect(menu.getByText("This browser saves a study file as a download; it decides where the file goes.")).toBeInTheDocument();
    fireEvent.keyDown(menu.getByRole("menuitem", { name: "Copy share link" }), { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(toggle()).toHaveFocus();
  });

  it("closes when the pointer goes down anywhere else", () => {
    renderBar();
    fireEvent.click(toggle());
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("moves through its items with the arrow keys", () => {
    renderBar();
    fireEvent.click(toggle());
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0]!, { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(items[1]!, { key: "ArrowUp" });
    fireEvent.keyDown(items[0]!, { key: "ArrowUp" });
    expect(items).toHaveLength(5);
    expect(items[4]).toHaveFocus();
  });

  it("copies a share link to the clipboard and puts nothing in the address bar", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const config = smallStudy();
    renderBar(config);
    fireEvent.click(toggle());
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy share link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const link = new URL(String(writeText.mock.calls[0]![0]));
    expect(link.origin).toBe(window.location.origin);
    expect(link.pathname).toBe("/");
    expect(decodeShareConfig(link.searchParams.get("c") ?? "")).toEqual(config);
    expect(await screen.findByText("Share link copied. It holds the study as it stands and none of the keys.")).toBeInTheDocument();
    // A link in the address bar would be applied again on reload, over every later edit.
    expect(window.location.search).toBe("");
    fireEvent.click(bar().getByRole("radio", { name: "Map" }));
    expect(window.location.search).toBe("?view=map");
    fireEvent.click(bar().getByRole("radio", { name: "Steps" }));
    expect(window.location.search).toBe("");
    expect(window.location.href).not.toContain("c=");
  });

  it("says so when the clipboard refuses", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    renderBar(smallStudy());
    fireEvent.click(toggle());
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy share link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The share link could not be copied. The browser refused access to the clipboard.");
  });

  it("does not offer a share link for a study that is not valid yet, and says why", () => {
    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Clear the title" }));
    fireEvent.click(toggle());
    expect(screen.getByRole("menuitem", { name: "Copy share link" })).toBeDisabled();
    expect(screen.getByText("This study is not valid yet, so it cannot be shared.")).toBeInTheDocument();
  });

  it("starts from the template at once when nothing would be lost", () => {
    const config = smallStudy();
    storeDraft(config);
    writeFileMark(fileMarkOf("small.study.yaml", config));
    render(<BarHarness><Probe /></BarHarness>);
    fireEvent.click(toggle());
    fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(bar().getByRole("button", { name: `Edit the title of ${studyOneTemplate().title}` })).toBeInTheDocument();
    // The template is no file's study.
    expect(fileState()).toHaveTextContent("Not saved to a file yet");
    expect(readFileMark()).toBeNull();
    expect(screen.getByText("The Study 1 template is loaded. It is not saved to a file yet.")).toBeInTheDocument();
  });

  it("asks before the template replaces work that is in no file, and Cancel keeps the study", () => {
    renderBar(smallStudy());
    fireEvent.click(toggle());
    fireEvent.click(screen.getByRole("menuitem", { name: "Start from Study 1 template" }));
    const ask = within(screen.getByRole("dialog", { name: "Replace the study you are editing?" }));
    expect(ask.getByText(/The Study 1 template is ready to take its place\. The study you are editing is not saved to a file\./)).toBeInTheDocument();
    fireEvent.click(ask.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(storedDraft().title).toBe("A small study");
  });
});

describe("title", () => {
  it("puts the focus on the title field of the view when it draws one", () => {
    storeDraft(smallStudy());
    render(<BarHarness><div className="field" data-path="title"><input aria-label="Title in the view" /></div></BarHarness>);
    fireEvent.click(bar().getByRole("button", { name: "Edit the title of A small study" }));
    expect(screen.getByLabelText("Title in the view")).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the settings on the title when the view draws none", () => {
    renderBar(smallStudy());
    fireEvent.click(bar().getByRole("button", { name: "Edit the title of A small study" }));
    const dialog = within(screen.getByRole("dialog", { name: "Everything in this study" }));
    expect(dialog.getByLabelText("Study title")).toHaveFocus();
  });
});
