import type { PreregRenderResponse } from "@qr-evals/contracts";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { api, ApiError } from "../../lib/api";
import { CONFIG_STORAGE_KEY, decodeShareConfig, studyOneTemplate, type WizardStudyConfig } from "../../lib/config-store";
import { clearSession } from "../../lib/session";
import { pinnedTemplate } from "../../components/study/study-test-kit";
import { Configure } from "./index";
import { SERVER_RECORD_DEBOUNCE_MS } from "./ServerRecord";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: memoryStorage() });
  window.history.pushState({}, "", "/");
  clearSession();
  // The page works from its bundled registry; only the calls a test names reach its stand-in for the server.
  vi.spyOn(api, "cards").mockRejectedValue(new TypeError("Failed to fetch"));
  vi.spyOn(api, "models").mockRejectedValue(new TypeError("Failed to fetch"));
});
afterEach(() => { vi.useRealTimers(); cleanup(); vi.restoreAllMocks(); });

const rail = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}: `) });
const open = (label: string) => fireEvent.click(rail(label));
const saved = () => JSON.parse(window.localStorage.getItem(CONFIG_STORAGE_KEY) ?? "{}") as Partial<WizardStudyConfig>;
const problem = (code: string) => document.querySelector<HTMLElement>(`.problem[data-code="${code}"]`);
const launch = () => screen.getByRole("button", { name: "Launch run" });
const fixContact = () => fireEvent.change(screen.getByLabelText("Contact"), { target: { value: "lab@example.org" } });

function answer(letter: string): PreregRenderResponse {
  return {
    markdown: "# Pre-registration\n", config_hash: `c${letter}`.repeat(32), plan_hash: `d${letter}`.repeat(32), prereg_hash: `e${letter}`.repeat(32),
    plan: {} as PreregRenderResponse["plan"],
  };
}
const refusal = (message: string) => new ApiError(message, 400, null, "invalid_request_error", "config");

describe("server hashes", () => {
  it("shows the config, plan and pre-registration hashes the server returns", async () => {
    const prereg = vi.spyOn(api, "prereg").mockResolvedValue(answer("1"));
    render(<Configure />);
    open("Review & launch");
    // Until the server answers, nothing is shown as a hash: the browser computes none of its own.
    expect(screen.getAllByText("calculating…")).toHaveLength(3);
    const ledger = within(screen.getByRole("region", { name: "Server hashes" }));
    expect(await ledger.findByText(answer("1").config_hash)).toBeInTheDocument();
    expect(ledger.getByText("Canonical config · SHA-256").nextElementSibling).toHaveTextContent(answer("1").config_hash);
    expect(ledger.getByText("Statistical plan · SHA-256").nextElementSibling).toHaveTextContent(answer("1").plan_hash);
    expect(ledger.getByText("Pre-registration draft · SHA-256").nextElementSibling).toHaveTextContent(answer("1").prereg_hash);
    expect(ledger.getByText(/^Computed by the run service for the study as it stands\. Showing them registers nothing/)).toBeInTheDocument();
    expect(prereg).toHaveBeenCalledTimes(1);
    expect(prereg).toHaveBeenCalledWith({ config: expect.objectContaining({ title: studyOneTemplate().title, contact: "REPLACE_WITH_STUDY_CONTACT" }) });
    // Hashes are not a problem and not an alert: the placeholder contact is still the one thing that blocks.
    expect(problem("review.server_rejected")).toBeNull();
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: 1 to fix");
  });

  it("drops the hashes at the next edit and asks again for the study as it then stands", async () => {
    const prereg = vi.spyOn(api, "prereg").mockResolvedValueOnce(answer("1")).mockResolvedValue(answer("2"));
    render(<Configure />);
    open("Review & launch");
    expect(await screen.findByText(answer("1").plan_hash)).toBeInTheDocument();

    fixContact();
    // The old hashes were of another study.
    expect(screen.queryByText(answer("1").plan_hash)).not.toBeInTheDocument();
    expect(screen.getAllByText("calculating…")).toHaveLength(3);
    expect(await screen.findByText(answer("2").plan_hash)).toBeInTheDocument();
    expect(prereg).toHaveBeenCalledTimes(2);
    expect(prereg).toHaveBeenLastCalledWith({ config: expect.objectContaining({ contact: "lab@example.org" }) });
  });

  it("asks only while Review is open, and only once the study has been left alone for a moment", async () => {
    vi.useFakeTimers();
    const prereg = vi.spyOn(api, "prereg").mockResolvedValue(answer("1"));
    render(<Configure />);
    await act(async () => { await vi.advanceTimersByTimeAsync(SERVER_RECORD_DEBOUNCE_MS * 5); });
    expect(prereg).not.toHaveBeenCalled();

    open("Review & launch");
    await act(async () => { await vi.advanceTimersByTimeAsync(SERVER_RECORD_DEBOUNCE_MS - 1); });
    expect(prereg).not.toHaveBeenCalled();
    // Typing restarts the wait: three keystrokes are one question.
    for (const title of ["S", "St", "Stu"]) {
      fireEvent.change(screen.getByLabelText("Study title"), { target: { value: title } });
      await act(async () => { await vi.advanceTimersByTimeAsync(SERVER_RECORD_DEBOUNCE_MS - 1); });
    }
    expect(prereg).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(prereg).toHaveBeenCalledTimes(1);
    expect(prereg).toHaveBeenCalledWith({ config: expect.objectContaining({ title: "Stu" }) });
    expect(screen.getByText(answer("1").config_hash)).toBeInTheDocument();

    // Leaving the step and editing there asks nothing; coming back asks for the study as it is now.
    open("Compare");
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Study" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(SERVER_RECORD_DEBOUNCE_MS * 5); });
    expect(prereg).toHaveBeenCalledTimes(1);
    open("Review & launch");
    await act(async () => { await vi.advanceTimersByTimeAsync(SERVER_RECORD_DEBOUNCE_MS); });
    expect(prereg).toHaveBeenCalledTimes(2);
    expect(prereg).toHaveBeenLastCalledWith({ config: expect.objectContaining({ title: "Study" }) });
  });

  it("does not ask for a study the browser cannot serialise", async () => {
    vi.useFakeTimers();
    const prereg = vi.spyOn(api, "prereg").mockResolvedValue(answer("1"));
    render(<Configure />);
    // An empty title is not a study this page can hold.
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "" } });
    open("Review & launch");
    await act(async () => { await vi.advanceTimersByTimeAsync(SERVER_RECORD_DEBOUNCE_MS * 5); });
    expect(prereg).not.toHaveBeenCalled();
    expect(screen.getByText("This draft is not a valid study yet, so there is nothing to hash.")).toBeInTheDocument();
    expect(screen.getAllByText("not rendered")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Copy share link" })).toBeDisabled();
  });

  it("says so when the run service cannot be reached, without blocking anything, and asks again on request", async () => {
    const prereg = vi.spyOn(api, "prereg").mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(answer("3"));
    render(<Configure />);
    open("Review & launch");
    fixContact();
    expect(await screen.findByText("Hashes need the run service. It is not reachable.")).toBeInTheDocument();
    expect(problem("review.server_rejected")).toBeNull();
    expect(launch()).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Ask again" }));
    expect(await screen.findByText(answer("3").config_hash)).toBeInTheDocument();
    expect(prereg).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Ask again" })).not.toBeInTheDocument();
  });

  it("does not take an answer that is not about the study for a refusal", async () => {
    vi.spyOn(api, "prereg").mockRejectedValue(new ApiError("too many requests from this address; retry in 3s", 429));
    render(<Configure />);
    open("Review & launch");
    fixContact();
    expect(await screen.findByText("The run service gave no hashes: too many requests from this address; retry in 3s.")).toBeInTheDocument();
    expect(problem("review.server_rejected")).toBeNull();
    expect(launch()).toBeEnabled();
  });
});

describe("a study the server refuses", () => {
  const message = "design.confirmatory: tier0_mmd_hamming/mmd_hamming is declared twice";

  it("becomes a blocking problem with the server's message word for word", async () => {
    vi.spyOn(api, "prereg").mockRejectedValue(refusal(message));
    render(<Configure />);
    open("Review & launch");
    fixContact();
    expect(launch()).toBeEnabled();

    const row = (await screen.findByText(message)).closest("li")!;
    expect(row).toHaveAttribute("data-code", "review.server_rejected");
    expect(row).toHaveClass("problem--error");
    expect(within(row).getByText("Blocks launch")).toBeInTheDocument();
    expect(within(row).getByText(/^This is the run service's own message/)).toBeInTheDocument();
    // It is listed under Review, counted on the rail, and stops the launch and the pre-registration.
    expect(row.closest("[data-step-group]")).toHaveAttribute("data-step-group", "review");
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: 1 to fix");
    expect(launch()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate pre-registration" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Generate pre-registration" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("1 problem above blocks the pre-registration and the launch. Warnings do not.");
    expect(screen.getAllByText("not rendered")).toHaveLength(3);
    expect(screen.getByText(/^The run service refused this study, so it has no hashes\./)).toBeInTheDocument();
  });

  it("stays while the study is unchanged, and is withdrawn at the next edit", async () => {
    const prereg = vi.spyOn(api, "prereg").mockRejectedValueOnce(refusal(message)).mockResolvedValue(answer("4"));
    render(<Configure />);
    open("Review & launch");
    fixContact();
    await screen.findByText(message);

    // Walking through the steps changes nothing about the study, so the refusal stands.
    open("Compare");
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: 1 to fix");
    open("Review & launch");
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(prereg).toHaveBeenCalledTimes(1);

    // The refusal was about the study as it was. An edit withdraws it at once and the server is asked again.
    fireEvent.change(screen.getByLabelText("Study title"), { target: { value: "Declared once" } });
    expect(screen.queryByText(message)).not.toBeInTheDocument();
    expect(launch()).toBeEnabled();
    expect(await screen.findByText(answer("4").config_hash)).toBeInTheDocument();
    expect(prereg).toHaveBeenCalledTimes(2);
    expect(rail("Review & launch")).toHaveAccessibleName("Review & launch: clear");
    expect(screen.getByRole("link", { name: "Generate pre-registration" })).toHaveAttribute("href", expect.stringMatching(/^\/prereg\?c=/));
  });
});

describe("problems of the study", () => {
  beforeEach(() => { vi.spyOn(api, "prereg").mockRejectedValue(new TypeError("Failed to fetch")); });
  /** The template with its pins in place, as the stored draft: the tests that count problems count their own. */
  const openPinned = () => { window.localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(pinnedTemplate())); return render(<Configure />); };

  it("are listed under their step, in step order, with what blocks said in words", () => {
    openPinned();
    open("Measure");
    fireEvent.change(screen.getByLabelText("Prompts for creative_diversity"), { target: { value: "100000" } });
    open("Review & launch");
    const groups = within(screen.getByRole("list", { name: "Problems in this study" }));
    expect(groups.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["Measure 1 to fix", "Design 1 to check", "Review & launch 1 to fix"]);
    expect(within(groups.getByRole("list", { name: "Problems on Measure" })).getByText(/prompts must be between 1 and/)).toBeInTheDocument();
    expect(within(groups.getByRole("list", { name: "Problems on Design" })).getByText("No expectations recorded.")).toBeInTheDocument();
    expect(within(groups.getByRole("list", { name: "Problems on Review & launch" })).getByText("Replace the placeholder contact.")).toBeInTheDocument();
    expect(screen.getByText('2 problems block the launch and the pre-registration, and are marked "Blocks launch". 1 warning does not stop a launch; it weakens what the study can show.')).toBeInTheDocument();
    expect(document.querySelectorAll(".problem--error .problem__severity")).toHaveLength(2);
    expect(within(problem("design.no_expectations")!).getByText("Warning")).toBeInTheDocument();
    expect(launch()).toBeDisabled();
  });

  it("opens a problem's step with the focus on its setting", () => {
    render(<Configure />);
    open("Review & launch");
    const expectations = problem("design.no_expectations")!;
    const link = within(expectations).getByRole("button", { name: "Open Design" });
    // Buttons of the same name are told apart by the message they belong to.
    expect(link).toHaveAccessibleDescription("No expectations recorded.");
    fireEvent.click(link);
    expect(rail("Design")).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText("What do you expect to see?")).toHaveFocus();
  });

  it("offers a problem's one-click fix where it has one", async () => {
    render(<Configure />);
    open("Design");
    fireEvent.change(within(screen.getByRole("table", { name: "Prompts and repetitions of the outcomes that decide the study" })).getByLabelText("Prompts for self_referential_probe"), { target: { value: "6" } });
    open("Review & launch");
    const row = problem("design.below_floor")!;
    expect(within(row).getByText("Blocks launch")).toBeInTheDocument();
    expect(row.closest("[data-step-group]")).toHaveAttribute("data-step-group", "design");
    // A problem without a fix offers none.
    expect(within(problem("review.contact_placeholder")!).queryByRole("button", { name: "Use recommended" })).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Use recommended" }));
    expect(problem("design.below_floor")).toBeNull();
    await waitFor(() => expect(saved().cards?.find((selection) => selection.card_id === "self_referential_probe")?.overrides?.items ?? 30).toBe(30));
  });

  it("does not call the Study 1 template ready while the document it would freeze opens with validity warnings", async () => {
    // The server's catalogue knows the checkpoint of the model; the template ships without it.
    vi.mocked(api.models).mockResolvedValue({ models: [{ id: "qr-llm", family: "qr_llm", provider: "qr_server", served_name: "qr-llm", checkpoint: "rdtand/Qwen3.8-27B@5.5bit" }] } as Awaited<ReturnType<typeof api.models>>);
    render(<Configure />);
    open("Design");
    fireEvent.change(screen.getByLabelText("What do you expect to see?"), { target: { value: "No difference between the arms." } });
    open("Review & launch");
    fixContact();
    // Nothing blocks, and two things are said: no checkpoint, no hashes of the session assets.
    expect(launch()).toBeEnabled();
    expect(screen.queryByText("Nothing in this study stands in the way of a launch.")).not.toBeInTheDocument();
    expect(screen.getByText("2 warnings do not stop a launch; they weaken what the study can show.")).toBeInTheDocument();
    expect(problem("compare.checkpoint_unpinned")).toHaveTextContent("The model qr-llm has no checkpoint pinned, so the pre-registration will open with a validity warning that its exact revision is not frozen.");
    expect(problem("instruments.assets_unpinned")).toHaveTextContent("This page cannot fill the hashes in; they can only be written into a study file by hand.");
    expect(rail("Compare")).toHaveAccessibleName("Compare: 1 to check");
    expect(document.querySelector(".study-bar .validity-pill")).toHaveTextContent("Ready · 2 to check");

    // The checkpoint is set in the model table, which only the settings dialog draws: the row leads there.
    fireEvent.click(within(problem("compare.checkpoint_unpinned")!).getByRole("button", { name: "Show the setting" }));
    const dialog = within(screen.getByRole("dialog", { name: "Everything in this study" }));
    expect(dialog.getByLabelText("Checkpoint")).toHaveFocus();
    fireEvent.click(dialog.getByRole("button", { name: "Done" }));

    // And one press pins the checkpoint the server has for that model.
    fireEvent.click(await within(problem("compare.checkpoint_unpinned")!).findByRole("button", { name: "Pin the server's checkpoint" }));
    expect(problem("compare.checkpoint_unpinned")).toBeNull();
    await waitFor(() => expect(saved().models?.[0]?.checkpoint).toBe("rdtand/Qwen3.8-27B@5.5bit"));
    expect(screen.getByText("1 warning does not stop a launch; it weakens what the study can show.")).toBeInTheDocument();
  });

  it("lets a warning through and holds the launch for an error", () => {
    openPinned();
    open("Review & launch");
    expect(launch()).toBeDisabled();
    expect(screen.getByText('1 problem blocks the launch and the pre-registration, and is marked "Blocks launch". 1 warning does not stop a launch; it weakens what the study can show.')).toBeInTheDocument();
    fixContact();
    // What is left is a warning: no expectations recorded.
    expect(problem("design.no_expectations")).not.toBeNull();
    expect(screen.getByText("1 warning does not stop a launch; it weakens what the study can show.")).toBeInTheDocument();
    expect(launch()).toBeEnabled();
    fireEvent.click(within(problem("design.no_expectations")!).getByRole("button", { name: "Open Design" }));
    fireEvent.change(screen.getByLabelText("What do you expect to see?"), { target: { value: "No difference between the arms." } });
    open("Review & launch");
    expect(screen.getByText("Nothing in this study stands in the way of a launch.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Problems in this study" })).not.toBeInTheDocument();
  });
});

describe("people, share link and keys", () => {
  beforeEach(() => { vi.spyOn(api, "prereg").mockRejectedValue(new TypeError("Failed to fetch")); });

  it("edits the authors and the contact on this step", async () => {
    render(<Configure />);
    open("Review & launch");
    fireEvent.click(within(problem("review.contact_placeholder")!).getByRole("button", { name: "Show the setting" }));
    expect(screen.getByLabelText("Contact")).toHaveFocus();
    fireEvent.change(screen.getByLabelText("Authors"), { target: { value: "Ada Lovelace\nAlan Turing" } });
    fixContact();
    await waitFor(() => expect(saved()).toMatchObject({ authors: ["Ada Lovelace", "Alan Turing"], contact: "lab@example.org" }));
    expect(problem("review.contact_placeholder")).toBeNull();
  });

  it("copies a share link that opens the setup with the study and holds no key", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<Configure />);
    open("Review & launch");
    fireEvent.change(screen.getByLabelText("Anthropic judge key"), { target: { value: "sk-ant-secret" } });
    fireEvent.change(screen.getByLabelText(/^External provider key/), { target: { value: "sk-external-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Copy share link" }));
    expect(await screen.findByText("Share link copied. It holds the study as it stands and none of the keys.")).toBeInTheDocument();

    const link = new URL(writeText.mock.calls[0]![0]);
    expect(link.pathname).toBe("/");
    const shared = decodeShareConfig(link.searchParams.get("c") ?? "");
    expect(shared?.title).toBe(studyOneTemplate().title);
    expect(JSON.stringify(shared)).not.toContain("secret");
    expect(link.toString()).not.toContain("secret");
    // Neither the draft in storage nor the address bar holds a key, or the link.
    expect(window.localStorage.getItem(CONFIG_STORAGE_KEY)).not.toContain("secret");
    expect(window.location.search).toBe("");
    // The note is about the link that was copied: it goes when the study changes.
    fixContact();
    expect(screen.queryByText(/^Share link copied/)).not.toBeInTheDocument();
  });

  it("says so when the browser refuses the clipboard", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<Configure />);
    open("Review & launch");
    fireEvent.click(screen.getByRole("button", { name: "Copy share link" }));
    expect(await screen.findByText("The share link could not be copied. The browser refused access to the clipboard.")).toBeInTheDocument();
  });

  it("links to the pre-registration document once nothing blocks", () => {
    render(<Configure />);
    open("Review & launch");
    fixContact();
    const link = screen.getByRole("link", { name: "Generate pre-registration" });
    const encoded = new URL(link.getAttribute("href") ?? "", window.location.origin).searchParams.get("c") ?? "";
    expect(decodeShareConfig(encoded)?.contact).toBe("lab@example.org");
  });
});
