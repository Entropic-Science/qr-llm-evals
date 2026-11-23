import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, vi } from "vitest";

import type * as ApiModule from "../lib/api";
import { CONFIG_STORAGE_KEY, decodeShareConfig, encodeShareConfig, rejectedNote, studyOneTemplate } from "../lib/config-store";
import { renderMarkdown } from "../lib/markdown";
import { normalizePreregMarkdown, preregMarkdownHash, verifiedPreregBytes } from "../lib/prereg";
import { Prereg, PreregRenderer, anchorPreregSections, preregSectionId } from "./Prereg";

const preregMock = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof ApiModule>();
  return { ...original, api: { ...original.api, prereg: preregMock } };
});

function memoryStorage(initialConfig?: ReturnType<typeof studyOneTemplate>): Storage {
  // An empty one is a browser in which nothing was ever saved.
  const values = new Map<string, string>();
  if (initialConfig) values.set(CONFIG_STORAGE_KEY, JSON.stringify(initialConfig));
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

/** What `config-store` says about a source it could not read. The words are that module's, so they are not repeated here. */
function loadNote(source: "storage" | "template", rejected: "share" | "storage"): string {
  const text = rejectedNote({ config: studyOneTemplate(), source, rejected });
  if (text === null) throw new Error("config-store has no note for a rejected source");
  return text;
}

/** The two documents the backend renders, byte for byte as its tests pin them. */
const golden = (name: string) => readFileSync(resolve(process.cwd(), "../../backend/tests/fixtures/prereg", name), "utf-8");
const legacyDocument = golden("golden.md");
const designedDocument = golden("golden_designed.md");

/** A document with the section titles of a designed pre-registration, small enough for role queries. */
const SHORT_DESIGNED = "# Study\n\n## Plain-language summary\n\ntext\n\n## Hypotheses\n\n**H1: Outcome, model · preset**\n\n## Decision rules\n\n### Study verdict\n\ntext\n\n## Sample size\n\ntext\n";

/** The links of a contents list as [title, href]. */
function linksOf(element: Element): [string, string][] {
  return [...element.querySelectorAll("a")].map((link) => [link.textContent ?? "", link.getAttribute("href") ?? ""]);
}

function published(markdown: string) {
  return { markdown, prereg_hash: "a".repeat(64), config_hash: "b".repeat(64) };
}

async function serverRenders(markdown: string) {
  preregMock.mockResolvedValue({ markdown, prereg_hash: await preregMarkdownHash(markdown), config_hash: "b".repeat(64), plan_hash: "c".repeat(64), plan: {} });
}

beforeAll(() => {
  if (!globalThis.crypto?.subtle) Object.defineProperty(globalThis, "crypto", { configurable: true, value: webcrypto });
});

beforeEach(() => {
  preregMock.mockReset();
  window.history.pushState({}, "", "/prereg");
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("preregistration bytes", () => {
  it("normalizes exported bytes so their browser SHA-256 equals the server hash", async () => {
    const serverMarkdown = "# Frozen\r\n\rMethods\rFinal\n";
    const normalized = normalizePreregMarkdown(serverMarkdown);
    const serverHash = await preregMarkdownHash(normalized);
    const bytes = await verifiedPreregBytes(serverMarkdown, serverHash);
    expect(new TextDecoder().decode(bytes)).toBe("# Frozen\n\nMethods\nFinal\n");
    expect(await preregMarkdownHash(new TextDecoder().decode(bytes))).toBe(serverHash);
  });
});

describe("preregistration document", () => {
  // The golden documents are large, so these two tests read the DOM directly:
  // role queries walk every element and are slow on a document of this size.
  it("renders a designed document and makes what decides the study easy to reach", () => {
    const { container } = render(<PreregRenderer document={published(designedDocument)} readOnly />);
    const contents = container.querySelector<HTMLElement>("nav.prereg-contents")!;
    expect(contents).toHaveAttribute("aria-label", "Sections of this document");
    const deciding = contents.querySelector<HTMLElement>(".prereg-contents__deciding")!;
    expect(deciding.querySelector(".eyebrow")).toHaveTextContent("What decides this study");
    expect(linksOf(deciding)).toEqual([
      ["Hypotheses", "#prereg-hypotheses"],
      ["Decision rules", "#prereg-decision-rules"],
      ["Sample size", "#prereg-sample-size"],
    ]);
    // every link of the contents lands on the heading it names
    const all = contents.querySelector("details")!;
    expect(all.open).toBe(false);
    expect(all.querySelector("summary")).toHaveTextContent("All 16 sections");
    const links = linksOf(all);
    expect(links.map(([title]) => title)).toEqual([
      "Plain-language summary", "Hypotheses", "Decision rules", "Sample size", "Arms and equivalence", "Models and exact settings",
      "Selected eval cards", "Statistical plan", "Budget and expected wall-clock", "Stopping and exclusion rules", "Result checks",
      "Researcher expectations", "Observer belief item", "Data sharing", "Validity limits", "Hashes and frozen inputs",
    ]);
    for (const [title, href] of links) {
      const target = container.querySelector(href);
      expect(target?.tagName).toBe("H2");
      expect(target?.textContent).toBe(title);
    }
    // the document itself: hypotheses, the decision matrix and the sample-size table
    const article = container.querySelector<HTMLElement>("article.prose.prereg-preview")!;
    const strong = [...article.querySelectorAll("strong")].map((element) => element.textContent);
    expect(strong).toContain("H3: Experience-claim rate, qr-llm · creative");
    expect(article.textContent).toContain("Worst-case variance, no prompt-by-source heterogeneity; true power may be lower.");
    expect(article.querySelectorAll("table").length).toBeGreaterThanOrEqual(2);
    const subsections = [...article.querySelectorAll("h3")];
    expect(subsections.map((heading) => heading.textContent)).toEqual(expect.arrayContaining(["Controls", "Study verdict"]));
    expect(subsections.every((heading) => !heading.id)).toBe(true);
  }, 20_000);

  it("renders a legacy document with a plain list of its sections", () => {
    const { container } = render(<PreregRenderer document={published(legacyDocument)} readOnly />);
    const contents = container.querySelector<HTMLElement>("nav.prereg-contents")!;
    expect(contents.querySelector(".prereg-contents__deciding")).toBeNull();
    expect(contents.textContent).not.toContain("What decides this study");
    const all = contents.querySelector("details")!;
    expect(all.open).toBe(true);
    expect(all.querySelector("summary")).toHaveTextContent("All 13 sections");
    expect(linksOf(all)).toContainEqual(["Hypotheses H0–H6", "#prereg-hypotheses-h0-h6"]);
    expect(container.querySelector("#prereg-hypotheses-h0-h6")?.tagName).toBe("H2");
    expect(container.querySelector("article.prose.prereg-preview h1")?.textContent).toContain("Prereg golden fixture");
    expect(container.querySelector("#prereg-decision-rules")).toBeNull();
  }, 20_000);

  it("scrolls to the section a link from another page names, once the document exists", () => {
    const scrolled: string[] = [];
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value(this: Element) { scrolled.push(this.id); } });
    try {
      window.history.pushState({}, "", "/studies/demo/prereg#prereg-decision-rules");
      const view = render(<PreregRenderer document={null} readOnly isRendering />);
      expect(scrolled).toEqual([]);
      view.rerender(<PreregRenderer document={published(SHORT_DESIGNED)} readOnly />);
      expect(scrolled).toEqual(["prereg-decision-rules"]);
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("still finds the section when the address escapes a character of its name", () => {
    const scrolled: string[] = [];
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value(this: Element) { scrolled.push(this.id); } });
    try {
      window.history.pushState({}, "", "/studies/demo/prereg#prereg-decision%2Drules");
      render(<PreregRenderer document={published(SHORT_DESIGNED)} readOnly />);
      expect(scrolled).toEqual(["prereg-decision-rules"]);
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  // decodeURIComponent throws on an escape that is cut short, and nothing above
  // this page catches an error thrown in an effect: the whole app would unmount.
  it.each(["#100%", "#%E0%A4", "#%", "#prereg-decision-rules%"])("shows the document when the address ends in the unreadable fragment %s", (hash) => {
    const scrolled: string[] = [];
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value(this: Element) { scrolled.push(this.id); } });
    try {
      window.history.pushState({}, "", `/studies/demo/prereg${hash}`);
      expect(window.location.hash).toBe(hash);
      const view = render(<PreregRenderer document={null} readOnly isRendering />);
      expect(() => view.rerender(<PreregRenderer document={published(SHORT_DESIGNED)} readOnly />)).not.toThrow();
      expect(screen.getByRole("heading", { level: 2, name: "Decision rules" })).toBeVisible();
      expect(screen.getByRole("navigation", { name: "Sections of this document" })).toBeVisible();
      // a fragment that names no section scrolls nowhere
      expect(scrolled).toEqual([]);
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("anchors only second-level headings, keeps ids unique and leaves the content as sanitised", () => {
    const html = renderMarkdown("# Title\n\n## Sample size\n\n### Sample size\n\n## Sample size\n\n## !!!\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(2))");
    const anchored = anchorPreregSections(html);
    expect(anchored.sections).toEqual([
      { id: "prereg-sample-size", title: "Sample size" },
      { id: "prereg-sample-size-2", title: "Sample size" },
      { id: "prereg-section", title: "!!!" },
    ]);
    expect(anchored.html).not.toContain("<script");
    expect(anchored.html).not.toContain('href="javascript:');
    expect(anchored.html).toContain('<h3>Sample size</h3>');
    expect(preregSectionId("Hypotheses H0–H6")).toBe("prereg-hypotheses-h0-h6");
    expect(preregSectionId("Decision rules")).toBe("prereg-decision-rules");
  });
});

describe("preregistration page", () => {
  it("shows the recorded expectations without editing them and points to the setup", () => {
    const config = studyOneTemplate();
    config.expectations = "No directional effect is expected.";
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(config) });
    render(<Prereg />);
    expect(screen.getByText("No directional effect is expected.")).toBeVisible();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText(/Expectations are written in the Design step of the setup, not on this page\./)).toBeVisible();
    expect(screen.getByRole("link", { name: /Open the setup/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("button", { name: "Render document" })).toBeEnabled();
  });

  it("renders a study without expectations and says what the document will state", async () => {
    const config = studyOneTemplate();
    config.expectations = "";
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(config) });
    await serverRenders("# Frozen protocol");
    render(<Prereg />);
    expect(screen.getByText("No expectations are recorded. The document will state that none were given.")).toBeVisible();
    // Nothing on this page claims that expectations gate the launch: they do not.
    expect(screen.queryByText(/required before/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Render document" }));
    await waitFor(() => expect(screen.getByText("Current with configuration")).toBeInTheDocument());
    const request = preregMock.mock.calls[0]?.[0] as { config: { expectations?: string }; expectations?: string };
    expect(request.expectations).toBe("");
    expect(request.config.expectations).toBe("");
  });

  it("renders the designed study it was given and lists what decides it", async () => {
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(studyOneTemplate()) });
    await serverRenders(SHORT_DESIGNED);
    render(<Prereg />);
    fireEvent.click(screen.getByRole("button", { name: "Render document" }));
    await waitFor(() => expect(screen.getByText("Current with configuration")).toBeInTheDocument());
    const contents = screen.getByRole("navigation", { name: "Sections of this document" });
    expect(within(contents).getByText("What decides this study")).toBeVisible();
    expect(within(contents).getAllByRole("link", { name: "Decision rules" })[0]).toHaveAttribute("href", "#prereg-decision-rules");
    const request = preregMock.mock.calls[0]?.[0] as { config: { design?: unknown; expectations?: string }; expectations?: string };
    expect(request.config.design).toBeTruthy();
    expect(request.config.expectations).toBe(request.expectations);
  });

  it("marks the rendered document stale when the study changes in another tab", async () => {
    const config = studyOneTemplate();
    config.expectations = "No directional effect is expected.";
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(config) });
    await serverRenders("# Frozen protocol");
    render(<Prereg />);
    fireEvent.click(screen.getByRole("button", { name: "Render document" }));
    await waitFor(() => expect(screen.getByText("Current with configuration")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Download .md" })).toBeEnabled();
    const edited = { ...config, expectations: "A changed prior expectation." };
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify(edited) }));
    expect(screen.getByText(/Stale · update required/i)).toBeInTheDocument();
    expect(screen.getByText("A changed prior expectation.")).toBeVisible();
    expect(screen.getByText("The study changed after this document was rendered. Update the document before downloading it.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Download .md" })).toBeDisabled();
    // a draft this version cannot read, or another key, changes nothing
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: "{not json" }));
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify({ title: "not a study" }) }));
    fireEvent(window, new StorageEvent("storage", { key: "another-key", newValue: JSON.stringify(config) }));
    expect(screen.getByText("A changed prior expectation.")).toBeVisible();
  });

  it("says that a share link could not be read, names the study on screen and waits before rendering it", async () => {
    const draft = studyOneTemplate();
    draft.title = "My own draft";
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(draft) });
    // what a mail client leaves of a link it cut short
    const whole = encodeShareConfig({ ...studyOneTemplate(), title: "A colleague's study" })!;
    window.history.pushState({}, "", `/prereg?c=${whole.slice(0, 200)}`);
    await serverRenders("# Frozen protocol");
    render(<Prereg />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(`${loadNote("storage", "share")} This page shows “My own draft”, not the study the link was made from. Ask for the link again, or continue with the study shown here.`);
    expect(alert).toHaveTextContent("The shared link could not be read");
    // nothing can be rendered or passed on as the shared study until the reader has said which study they mean
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Copy share link" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Render the document/ })).not.toBeInTheDocument();
    expect(screen.getByText("Choose “Continue with this study” above before rendering or sharing it.")).toBeVisible();
    expect(preregMock).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole("button", { name: "Continue with this study" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // what happened stays on the page after it was acknowledged
    expect(screen.getByRole("note", { name: "Which study this page shows" })).toHaveTextContent(`${loadNote("storage", "share")} This page shows “My own draft”.`);
    expect(screen.getByRole("button", { name: "Copy share link" })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Render the document/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Render document" }));
    await waitFor(() => expect(screen.getByText("Current with configuration")).toBeInTheDocument());
    expect((preregMock.mock.calls[0]?.[0] as { config: { title?: string } }).config.title).toBe("My own draft");
  });

  it("says which study is shown when neither the share link nor a saved draft could be read", () => {
    const template = studyOneTemplate();
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
    window.history.pushState({}, "", "/prereg?c=not-a-study");
    const first = render(<Prereg />);
    expect(screen.getByRole("alert")).toHaveTextContent(`${loadNote("template", "share")} This page shows “${template.title}”, not the study the link was made from.`);
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
    first.unmount();

    // a saved draft this version cannot read is said too, in its own words
    const storage = memoryStorage();
    storage.setItem(CONFIG_STORAGE_KEY, JSON.stringify({ title: "not a study" }));
    Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
    window.history.pushState({}, "", "/prereg");
    render(<Prereg />);
    expect(screen.getByRole("alert")).toHaveTextContent(`${loadNote("template", "storage")} This page shows “${template.title}”, not the draft you saved. Continue with the study shown here, or open the setup to build yours again.`);
    expect(screen.getByRole("alert")).toHaveTextContent("Your saved draft");
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
    // once the setup saves a draft this page can read, that draft is on screen and the notice has nothing left to say
    const rebuilt = { ...template, title: "Built again" };
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify(rebuilt) }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("note", { name: "Study on this page" })).toHaveTextContent("Built again");
    expect(screen.getByRole("note", { name: "Study on this page" })).toHaveTextContent("It is the draft saved in this browser.");
    expect(screen.getByRole("button", { name: "Render document" })).toBeEnabled();
  });

  it("names the study on screen and where it came from", () => {
    const draft = { ...studyOneTemplate(), title: "My own draft" };
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(draft) });
    const first = render(<Prereg />);
    expect(screen.getByRole("note", { name: "Study on this page" })).toHaveTextContent("Study on this pageMy own draftIt is the draft saved in this browser.");
    first.unmount();
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
    render(<Prereg />);
    expect(screen.getByRole("note", { name: "Study on this page" })).toHaveTextContent(`${studyOneTemplate().title}It is the Study 1 template.`);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the notice about a share link true when a draft saved in another tab takes the template's place", () => {
    const template = studyOneTemplate();
    const other = { ...template, title: "Draft from the other tab" };
    const arrives = () => fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify(other) }));
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() });
    window.history.pushState({}, "", "/prereg?c=not-a-study");
    const first = render(<Prereg />);
    expect(screen.getByRole("alert")).toHaveTextContent(`${loadNote("template", "share")} This page shows “${template.title}”, not the study the link was made from.`);

    // the setup in another tab saves a draft while the notice is up: the page takes it over, and the notice follows
    arrives();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(`${loadNote("storage", "share")} This page shows “Draft from the other tab”, not the study the link was made from. Ask for the link again, or continue with the study shown here.`);
    // it used to go on saying that the template was loaded, beside the title of the draft
    expect(alert).not.toHaveTextContent("template");
    expect(screen.getByRole("note", { name: "Study on this page" })).toHaveTextContent("Draft from the other tabIt is the draft saved in this browser.");
    // the reader still has to say which study they mean
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
    fireEvent.click(within(alert).getByRole("button", { name: "Continue with this study" }));
    expect(screen.getByRole("note", { name: "Which study this page shows" })).toHaveTextContent(`${loadNote("storage", "share")} This page shows “Draft from the other tab”.`);
    first.unmount();

    // the same when the draft arrives after the reader chose to continue with the template
    render(<Prereg />);
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Continue with this study" }));
    const kept = screen.getByRole("note", { name: "Which study this page shows" });
    expect(kept).toHaveTextContent(`${loadNote("template", "share")} This page shows “${template.title}”.`);
    arrives();
    expect(kept).toHaveTextContent(`${loadNote("storage", "share")} This page shows “Draft from the other tab”.`);
    expect(kept).not.toHaveTextContent("template");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says both when a share link and the saved draft could not be read", () => {
    const template = studyOneTemplate();
    const storage = memoryStorage();
    storage.setItem(CONFIG_STORAGE_KEY, JSON.stringify({ title: "not a study" }));
    Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
    window.history.pushState({}, "", "/prereg?c=not-a-study");
    render(<Prereg />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(`${loadNote("template", "storage")} The shared link could not be read either. This page shows “${template.title}”, not the draft you saved and not the study the link was made from. Ask for the link again, continue with the study shown here, or open the setup to build yours again.`);
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
    // a draft the setup saves is on screen from then on; the link is still not the study shown, so the notice stays for it
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify({ ...template, title: "Built again" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent(`${loadNote("storage", "share")} This page shows “Built again”, not the study the link was made from. Ask for the link again, or continue with the study shown here.`);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Your saved draft no longer matches");
    expect(screen.getByRole("button", { name: "Render document" })).toBeDisabled();
  });

  it("shows the study of a share link that can be read, without a notice and without touching the draft", async () => {
    const draft = { ...studyOneTemplate(), title: "My own draft", expectations: "Written by me." };
    const storage = memoryStorage(draft);
    Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
    const shared = { ...studyOneTemplate(), title: "A colleague's study", expectations: "Written by the colleague." };
    const encoded = encodeShareConfig(shared)!;
    window.history.pushState({}, "", `/prereg?c=${encoded}`);
    await serverRenders("# Frozen protocol");
    render(<Prereg />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("note", { name: "Which study this page shows" })).not.toBeInTheDocument();
    const study = screen.getByRole("note", { name: "Study on this page" });
    expect(study).toHaveTextContent("A colleague's study");
    expect(study).toHaveTextContent("It comes from the share link you opened. Your saved draft is not changed; the link below opens this study in the setup.");
    expect(screen.getByText("Written by the colleague.")).toBeVisible();
    // the setup is where a share link becomes the draft, so the link to it carries the study
    expect(screen.getByRole("link", { name: /Open the setup/ })).toHaveAttribute("href", `/?c=${encoded}`);
    // the reader's draft is still what is saved
    expect((JSON.parse(storage.getItem(CONFIG_STORAGE_KEY)!) as { title: string }).title).toBe("My own draft");

    // an edit of the reader's own draft in another tab does not take the place of the shared study
    fireEvent.click(screen.getByRole("button", { name: "Render document" }));
    await waitFor(() => expect(screen.getByText("Current with configuration")).toBeInTheDocument());
    fireEvent(window, new StorageEvent("storage", { key: CONFIG_STORAGE_KEY, newValue: JSON.stringify({ ...draft, expectations: "Edited by me." }) }));
    expect(screen.getByText("Written by the colleague.")).toBeVisible();
    expect(screen.queryByText("Edited by me.")).not.toBeInTheDocument();
    expect(screen.getByText("Current with configuration")).toBeInTheDocument();
    expect(study).toHaveTextContent("A colleague's study");
    expect((preregMock.mock.calls[0]?.[0] as { config: { title?: string } }).config.title).toBe("A colleague's study");
  });

  it("says so when the clipboard refuses the share link, and still leaves the address bar alone", async () => {
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(studyOneTemplate()) });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<Prereg />);
    fireEvent.click(screen.getByRole("button", { name: "Copy share link" }));
    const state = await screen.findByText("The share link could not be copied. The browser refused access to the clipboard.");
    expect(state).toHaveClass("prereg-action-state--error");
    expect(window.location.search).toBe("");
  });

  it("copies the share link without putting it back into the address bar", async () => {
    const config = studyOneTemplate();
    Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage(config) });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const replaceState = vi.spyOn(window.history, "replaceState");
    const pushState = vi.spyOn(window.history, "pushState");
    render(<Prereg />);
    fireEvent.click(screen.getByRole("button", { name: "Copy share link" }));
    expect(await screen.findByText("Share link copied")).toBeVisible();
    const copied = new URL(writeText.mock.calls[0]?.[0] as string);
    expect(copied.pathname).toBe("/prereg");
    expect(decodeShareConfig(copied.searchParams.get("c") ?? "")).toEqual(config);
    // Left in the address bar, a reload would show this copy of the study in
    // place of the draft and its later edits.
    expect(window.location.pathname).toBe("/prereg");
    expect(window.location.search).toBe("");
    expect(replaceState).not.toHaveBeenCalled();
    expect(pushState).not.toHaveBeenCalled();
  });
});
