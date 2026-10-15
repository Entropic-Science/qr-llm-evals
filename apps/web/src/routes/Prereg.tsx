import type { PreregRenderResponse } from "@qr-evals/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "wouter";

import { ExpectationsEditor } from "../components/ExpectationsEditor";
import { HashBadge } from "../components/HashBadge";
import { Markdown } from "../components/Markdown";
import { ApiError } from "../lib/api";
import { encodeShareConfig, loadConfig, saveConfig, type WizardStudyConfig } from "../lib/config-store";
import { preregDownloadName, preregMarkdownHash, renderPreregistration, verifiedPreregBlob } from "../lib/prereg";

const RENDER_DEBOUNCE_MS = 250;

interface PublishedPrereg {
  markdown: string;
  prereg_hash: string;
  config_hash: string;
}

interface PreregRendererProps {
  document: PublishedPrereg | null;
  error?: string | null;
  isRendering?: boolean;
  isStale?: boolean;
  readOnly?: boolean;
  onRender?: () => void;
}

/** Shared document surface; future published-study routes can use readOnly mode. */
export function PreregRenderer({ document, error, isRendering = false, isStale = false, readOnly = false, onRender }: PreregRendererProps) {
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
    {!document && !isRendering && <div className="prereg-empty"><span className="prereg-empty__index">00</span><p>The frozen Markdown preview will appear here after expectations are recorded and the server renders the statistical plan.</p>{!readOnly && onRender && <button type="button" className="text-link prereg-empty__action" onClick={onRender}>Render the document <span aria-hidden="true">→</span></button>}</div>}
    {isRendering && !document && <div className="prereg-loading" role="status"><span />Verifying configuration and composing the statistical plan…</div>}
    {document && <Markdown source={document.markdown} className="prereg-preview" />}
  </section>;
}

export function Prereg() {
  const [config, setConfig] = useState<WizardStudyConfig>(() => loadConfig());
  const [document, setDocument] = useState<PreregRenderResponse | null>(null);
  const [renderedInput, setRenderedInput] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState(false);
  const [validationAttempted, setValidationAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloadState, setDownloadState] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<string | null>(null);
  const renderTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expectations = config.expectations ?? "";
  const inputFingerprint = useMemo(() => JSON.stringify(config), [config]);
  const isStale = document !== null && renderedInput !== inputFingerprint;
  const canRender = expectations.trim().length > 0;

  useEffect(() => () => { if (renderTimer.current) clearTimeout(renderTimer.current); }, []);

  const updateExpectations = useCallback((value: string) => {
    setConfig((current) => {
      const next = { ...current, expectations: value };
      saveConfig(next);
      return next;
    });
    setError(null);
    setDownloadState(null);
  }, []);

  const requestRender = useCallback(() => {
    setValidationAttempted(true);
    setError(null);
    setDownloadState(null);
    if (!canRender) return;
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
  }, [canRender, config]);

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
      const shareUrl = new URL("/prereg", window.location.origin);
      shareUrl.searchParams.set("c", encoded);
      await navigator.clipboard.writeText(shareUrl.toString());
      window.history.replaceState({}, "", `${shareUrl.pathname}${shareUrl.search}`);
      setCopyState("Share link copied");
    } catch {
      setCopyState("Could not copy the share link");
    }
  }, [config]);

  return <div className="prereg-page">
    <header className="prereg-header">
      <div>
        <p className="eyebrow">Protocol checkpoint · freeze before launch</p>
        <h1>Fix the method before seeing the result.</h1>
        <p>This page turns the current machine configuration and your prior expectations into a verifiable, publishable record.</p>
      </div>
      <dl className="prereg-stage"><div><dt>Stage</dt><dd>Pre-registration</dd></div><div><dt>State</dt><dd>{isStale ? "Document stale" : document ? "Hash verified" : "Draft"}</dd></div></dl>
    </header>

    <div className="prereg-layout">
      <aside className="prereg-controls" aria-label="Preregistration controls">
        <ExpectationsEditor value={expectations} onChange={updateExpectations} showError={validationAttempted || !canRender} />
        <div className="prereg-actions">
          <button className="button button--primary" type="button" disabled={isRendering || !canRender} onClick={requestRender}>{document ? "Update document" : "Render document"}</button>
          <button className="button button--secondary" type="button" disabled={!document || isStale || isRendering} onClick={() => void download()}>Download .md</button>
          <button className="button button--secondary" type="button" onClick={() => void copyShareLink()}>Copy share link</button>
          <Link className="text-link" href="/">Return to configuration <span aria-hidden="true">←</span></Link>
        </div>
        {!canRender && <p className="validation-note" role="alert">Expectations are required before this study can be preregistered or launched.</p>}
        {downloadState && <p className={downloadState.startsWith("Download refused") ? "prereg-action-state prereg-action-state--error" : "prereg-action-state"} role="status">{downloadState}</p>}
        {copyState && <p className="prereg-action-state" role="status">{copyState}</p>}
        <dl className="hash-ledger">
          <HashBadge label="Prereg Markdown · SHA-256" hash={document?.prereg_hash} pending={isRendering && !document} />
          <HashBadge label="Canonical config · SHA-256" hash={document?.config_hash} pending={isRendering && !document} />
        </dl>
        <p className="hash-ledger__note">The first digest verifies the exact UTF-8, LF-normalized Markdown bytes. The second identifies the canonical study configuration embedded in that record.</p>
      </aside>

      <PreregRenderer document={document} error={error} isRendering={isRendering} isStale={isStale} onRender={requestRender} />
    </div>
  </div>;
}
