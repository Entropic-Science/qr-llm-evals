import type { PreregRenderResponse } from "@qr-evals/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "wouter";

import { HashBadge } from "../components/HashBadge";
import { ApiError } from "../lib/api";
import { CONFIG_STORAGE_KEY, encodeShareConfig, loadConfig, parseStudyConfig, rejectedNote, type LoadedConfig, type WizardStudyConfig } from "../lib/config-store";
import { renderMarkdown } from "../lib/markdown";
import { preregDownloadName, preregMarkdownHash, renderPreregistration, verifiedPreregBlob } from "../lib/prereg";
import "../styles/results.css";

const RENDER_DEBOUNCE_MS = 250;

/** The sections of a designed pre-registration that say what decides the study. */
const DECIDING_SECTIONS: readonly string[] = ["Hypotheses", "Decision rules", "Sample size"];

interface PublishedPrereg {
  markdown: string;
  prereg_hash: string;
  config_hash: string;
}

export interface PreregSection {
  id: string;
  title: string;
}

/** The anchor of a section of the pre-registration, from its H2 title. */
export function preregSectionId(title: string): string {
  const slug = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `prereg-${slug || "section"}`;
}

/**
 * Give every H2 of the rendered (already sanitised) document an anchor and
 * list them. Both document kinds are handled alike: the legacy one has
 * "Hypotheses H0–H6", the designed one "Hypotheses", "Decision rules" and
 * "Sample size".
 */
export function anchorPreregSections(html: string): { html: string; sections: PreregSection[] } {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const sections: PreregSection[] = [];
  const used = new Set<string>();
  for (const heading of parsed.body.querySelectorAll("h2")) {
    const title = heading.textContent?.trim() ?? "";
    if (!title) continue;
    const base = preregSectionId(title);
    let id = base;
    for (let n = 2; used.has(id); n += 1) id = `${base}-${n}`;
    used.add(id);
    heading.id = id;
    sections.push({ id, title });
  }
  return { html: parsed.body.innerHTML, sections };
}

/**
 * The element the address names after `#`. A fragment that cannot be decoded
 * (an escape cut short, as in a mangled link) is taken as written: it names
 * no section, and it must not throw inside an effect, where nothing would
 * catch it.
 */
function hashTarget(): string {
  const raw = window.location.hash.slice(1);
  try { return decodeURIComponent(raw); } catch { return raw; }
}

/** What this page could not read when it opened. The two can fail together. */
interface Unread {
  /** The address carries a share link, and the study on the page is not the one it was made from. */
  link: boolean;
  /** A saved draft was found that this version cannot read, and no readable one has arrived since. */
  draft: boolean;
}

/**
 * What a reader is told when a share link or a saved draft could not be read
 * and this page therefore shows another study than the one they asked for.
 * `source` is where the study on the page comes from now, not where it came
 * from when the page opened: a draft saved in another tab takes the place of
 * the template, and the note has to name what is on screen.
 */
function loadNoteFor(unread: Unread, source: LoadedConfig["source"], config: WizardStudyConfig): string | null {
  if (unread.draft) {
    const note = rejectedNote({ config, source: "template", rejected: "storage" });
    return note !== null && unread.link ? `${note} The shared link could not be read either.` : note;
  }
  return unread.link ? rejectedNote({ config, source, rejected: "share" }) : null;
}

/** Which study the page shows in place of the one that could not be read, and what the reader can do about it. */
function shownInstead(unread: Unread, title: string): { shows: string; instead: string } {
  const shows = `This page shows ${title.trim() ? `“${title.trim()}”` : "a study without a title"}`;
  if (unread.draft && unread.link) {
    return { shows, instead: "not the draft you saved and not the study the link was made from. Ask for the link again, continue with the study shown here, or open the setup to build yours again." };
  }
  return unread.link
    ? { shows, instead: "not the study the link was made from. Ask for the link again, or continue with the study shown here." }
    : { shows, instead: "not the draft you saved. Continue with the study shown here, or open the setup to build yours again." };
}

/** Where the study on the draft page came from, in a sentence. */
const SOURCE_TEXT: Readonly<Record<LoadedConfig["source"], string>> = {
  share: "It comes from the share link you opened. Your saved draft is not changed; the link below opens this study in the setup.",
  storage: "It is the draft saved in this browser.",
  template: "It is the Study 1 template.",
};

function SectionLink({ section }: { section: PreregSection }) {
  return <li><a href={`#${section.id}`}>{section.title}</a></li>;
}

function PreregContents({ sections }: { sections: readonly PreregSection[] }) {
  if (sections.length === 0) return null;
  // Only a designed document has decision rules; a legacy one gets the plain list.
  const designed = sections.some((section) => section.title === "Decision rules");
  const deciding = designed ? DECIDING_SECTIONS.flatMap((title) => sections.filter((section) => section.title === title)) : [];
  return <nav className="prereg-contents" aria-label="Sections of this document">
    {deciding.length > 0 && <div className="prereg-contents__deciding">
      <p className="eyebrow">What decides this study</p>
      <ul>{deciding.map((section) => <SectionLink key={section.id} section={section} />)}</ul>
    </div>}
    <details open={!designed}>
      <summary>All {sections.length} sections</summary>
      <ol>{sections.map((section) => <SectionLink key={section.id} section={section} />)}</ol>
    </details>
  </nav>;
}

interface PreregRendererProps {
  document: PublishedPrereg | null;
  error?: string | null;
  isRendering?: boolean;
  isStale?: boolean;
  readOnly?: boolean;
  onRender?: () => void;
}

/** Shared document surface: the draft page and the published study page both use it. */
export function PreregRenderer({ document, error, isRendering = false, isStale = false, readOnly = false, onRender }: PreregRendererProps) {
  const markdown = document?.markdown ?? null;
  const rendered = useMemo(() => (markdown === null ? null : anchorPreregSections(renderMarkdown(markdown))), [markdown]);

  // A link from elsewhere (the results page links to the decision rules)
  // arrives before the document exists, so the browser cannot scroll to it.
  useEffect(() => {
    if (!rendered) return;
    const target = hashTarget();
    if (!rendered.sections.some((section) => section.id === target)) return;
    window.document.getElementById(target)?.scrollIntoView?.();
  }, [rendered]);

  return <section className="prereg-document" aria-labelledby="prereg-preview-title" aria-busy={isRendering}>
    <div className="prereg-document__bar">
      <div>
        <p className="eyebrow">Server-authored record</p>
        <h2 id="prereg-preview-title">Preregistration document</h2>
      </div>
      {!readOnly && <span className={`render-state ${isStale ? "render-state--stale" : document ? "render-state--current" : ""}`} role="status">
        {isRendering ? "Rendering…" : isStale ? "Stale · update required" : document ? "Current with configuration" : "Awaiting first render"}
      </span>}
    </div>
    {error && <p className="prereg-error" role="alert">{error}</p>}
    {!document && !isRendering && <div className="prereg-empty"><span className="prereg-empty__index">00</span><p>The Markdown preview will appear here once the server has rendered the statistical plan of this study.</p>{!readOnly && onRender && <button type="button" className="text-link prereg-empty__action" onClick={onRender}>Render the document <span aria-hidden="true">→</span></button>}</div>}
    {isRendering && !document && <div className="prereg-loading" role="status"><span />Verifying configuration and composing the statistical plan…</div>}
    {rendered && <>
      <PreregContents sections={rendered.sections} />
      <article className="prose prereg-preview" dangerouslySetInnerHTML={{ __html: rendered.html }} />
    </>}
  </section>;
}

export function Prereg() {
  // Where the study on this page came from. "Copy share link" makes links to
  // this page, so this is where a link that was cut short or made by another
  // version lands: the page then shows the reader's own draft or the template,
  // and has to say so before anything is rendered from it.
  const [loaded] = useState<LoadedConfig>(() => loadConfig());
  const [config, setConfig] = useState<WizardStudyConfig>(loaded.config);
  // A share link is only shown here; the setup is what makes it the draft.
  const [source, setSource] = useState<LoadedConfig["source"]>(loaded.source);
  // `loadConfig` reports one rejected source, and a lost draft takes precedence there. A share link that could
  // not be read next to it is still news: the address carries one, and the study on the page is not its study.
  const [linkUnread] = useState(() => loaded.source !== "share" && Boolean(new URLSearchParams(window.location.search).get("c")));
  // A draft that could not be read stops being the news once a readable one arrives from the setup.
  const [draftArrived, setDraftArrived] = useState(false);
  const unread = useMemo<Unread>(() => ({ link: linkUnread, draft: loaded.rejected === "storage" && !draftArrived }), [draftArrived, linkUnread, loaded]);
  // Said of the study that is on the page now: `source` and `config` change when another tab saves a draft.
  const loadNote = loadNoteFor(unread, source, config);
  const [acknowledged, setAcknowledged] = useState(false);
  const awaitingReader = loadNote !== null && !acknowledged;
  const shown = shownInstead(unread, config.title);
  const setupHref = useMemo(() => {
    const encoded = source === "share" ? encodeShareConfig(config) : null;
    return encoded === null ? "/" : `/?c=${encoded}`;
  }, [config, source]);
  const [document, setDocument] = useState<PreregRenderResponse | null>(null);
  const [renderedInput, setRenderedInput] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadState, setDownloadState] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<{ text: string; failed: boolean } | null>(null);
  const renderTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expectations = (config.expectations ?? "").trim();
  const inputFingerprint = useMemo(() => JSON.stringify(config), [config]);
  const isStale = document !== null && renderedInput !== inputFingerprint;

  useEffect(() => () => { if (renderTimer.current) clearTimeout(renderTimer.current); }, []);

  // The study is edited in the setup, not here. When the draft changes in
  // another tab, take it over so that a rendered document is marked stale
  // instead of passing for the current study. The study of a share link is
  // not the draft: an edit of the draft must not take its place on this page.
  useEffect(() => {
    if (source === "share") return;
    const onStorage = (event: StorageEvent) => {
      if (event.key !== CONFIG_STORAGE_KEY || event.newValue === null) return;
      let stored: unknown;
      try { stored = JSON.parse(event.newValue); } catch { return; }
      const next = parseStudyConfig(stored);
      if (next === null) return;
      setConfig(next);
      setSource("storage");
      setDraftArrived(true);
      setDownloadState(null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [source]);

  const requestRender = useCallback(() => {
    setError(null);
    setDownloadState(null);
    if (renderTimer.current) clearTimeout(renderTimer.current);
    setIsRendering(true);
    const submittedConfig = structuredClone(config);
    const submittedFingerprint = JSON.stringify(submittedConfig);
    renderTimer.current = setTimeout(() => {
      renderTimer.current = null;
      void renderPreregistration({ config: submittedConfig, expectations: submittedConfig.expectations ?? "" })
        .then(async (response) => {
          const browserHash = await preregMarkdownHash(response.markdown);
          if (browserHash !== response.prereg_hash.toLowerCase()) {
            throw new Error("The server response did not match its preregistration hash. Nothing was frozen.");
          }
          setDocument(response);
          setRenderedInput(submittedFingerprint);
        })
        .catch((reason: unknown) => {
          setError(reason instanceof ApiError || reason instanceof Error ? reason.message : "The preregistration could not be rendered.");
        })
        .finally(() => setIsRendering(false));
    }, RENDER_DEBOUNCE_MS);
  }, [config]);

  const download = useCallback(async () => {
    if (!document) return;
    setDownloadState("Verifying exact bytes…");
    try {
      const blob = await verifiedPreregBlob(document.markdown, document.prereg_hash);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = preregDownloadName(config.title);
      anchor.click();
      URL.revokeObjectURL(url);
      setDownloadState("Hash verified · Markdown downloaded");
    } catch (reason: unknown) {
      setDownloadState(reason instanceof Error ? `Download refused · ${reason.message}` : "Download refused · hash verification failed");
    }
  }, [config.title, document]);

  const copyShareLink = useCallback(async () => {
    try {
      const encoded = encodeShareConfig(config);
      if (encoded === null) { setCopyState({ text: "This study is not valid yet, so it has no share link.", failed: true }); return; }
      const shareUrl = new URL("/prereg", window.location.origin);
      shareUrl.searchParams.set("c", encoded);
      // The link goes to the clipboard only. Left in the address bar, a
      // reload would show this copy of the study in place of the draft and
      // its later edits.
      await navigator.clipboard.writeText(shareUrl.toString());
      setCopyState({ text: "Share link copied", failed: false });
    } catch {
      setCopyState({ text: "The share link could not be copied. The browser refused access to the clipboard.", failed: true });
    }
  }, [config]);

  return <div className="prereg-page">
    <header className="prereg-header">
      <div>
        <div className="page-heading"><p className="eyebrow">Protocol checkpoint · freeze before launch</p>
        <h1>Fix the method before seeing the result.</h1></div>
        <p>This page turns the current study configuration, with your prior expectations, into a verifiable, publishable record.</p>
      </div>
      <dl className="prereg-stage"><div><dt>Stage</dt><dd>Pre-registration</dd></div><div><dt>State</dt><dd>{isStale ? "Document stale" : document ? "Hash verified" : "Draft"}</dd></div></dl>
    </header>

    {awaitingReader && <section className="prereg-load-note" role="alert">
      <p><strong>{loadNote}</strong> {shown.shows}, {shown.instead}</p>
      <button className="button button--secondary" type="button" onClick={() => setAcknowledged(true)}>Continue with this study</button>
    </section>}
    {loadNote !== null && acknowledged && <p className="validation-note prereg-load-note__kept" role="note" aria-label="Which study this page shows">{loadNote} {shown.shows}.</p>}

    <div className="prereg-layout">
      <aside className="prereg-controls" aria-label="Preregistration controls">
        <p className="prereg-study" role="note" aria-label="Study on this page">
          <span className="eyebrow">Study on this page</span>
          <strong>{config.title.trim() || "A study without a title"}</strong>
          <span>{SOURCE_TEXT[source]}</span>
        </p>
        <section className="expectations-editor prereg-expectations" aria-labelledby="prereg-expectations-title">
          <div className="expectations-editor__heading">
            <div>
              <p className="eyebrow">Frozen with the document</p>
              <h2 id="prereg-expectations-title">Researcher expectations</h2>
            </div>
          </div>
          {expectations
            ? <blockquote className="prereg-expectations__text">{expectations}</blockquote>
            : <p className="validation-note" role="note">No expectations are recorded. The document will state that none were given.</p>}
          <p>Expectations are written in the Design step of the setup, not on this page. They are context for a reader and are <strong>not treated as evidence</strong>.</p>
          <Link className="text-link" href={setupHref}>Open the setup <span aria-hidden="true">→</span></Link>
        </section>
        <div className="prereg-actions">
          <button className="button button--primary" type="button" disabled={isRendering || awaitingReader} onClick={requestRender}>{document ? "Update document" : "Render document"}</button>
          <button className="button button--secondary" type="button" disabled={!document || isStale || isRendering} onClick={() => void download()}>Download .md</button>
          <button className="button button--secondary" type="button" disabled={awaitingReader} onClick={() => void copyShareLink()}>Copy share link</button>
        </div>
        {awaitingReader && <p className="validation-note" role="note">Choose “Continue with this study” above before rendering or sharing it.</p>}
        {isStale && <p className="validation-note" role="status">The study changed after this document was rendered. Update the document before downloading it.</p>}
        {downloadState && <p className={downloadState.startsWith("Download refused") ? "prereg-action-state prereg-action-state--error" : "prereg-action-state"} role="status">{downloadState}</p>}
        {copyState && <p className={copyState.failed ? "prereg-action-state prereg-action-state--error" : "prereg-action-state"} role="status">{copyState.text}</p>}
        <dl className="hash-ledger">
          <HashBadge label="Prereg Markdown · SHA-256" hash={document?.prereg_hash} pending={isRendering && !document} />
          <HashBadge label="Canonical config · SHA-256" hash={document?.config_hash} pending={isRendering && !document} />
        </dl>
        <p className="hash-ledger__note">The first digest verifies the exact UTF-8, LF-normalized Markdown bytes. The second identifies the canonical study configuration embedded in that record.</p>
      </aside>

      <PreregRenderer document={document} error={error} isRendering={isRendering} isStale={isStale} {...(awaitingReader ? {} : { onRender: requestRender })} />
    </div>
  </div>;
}
