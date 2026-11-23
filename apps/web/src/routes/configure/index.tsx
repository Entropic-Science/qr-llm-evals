import { useEffect, useRef, useState, type DragEvent } from "react";
import { createPortal } from "react-dom";
import { StudyMap, newMapMemory } from "../../components/map";
import { AllSettings } from "../../components/study/AllSettings";
import { LoadReport } from "../../components/study/LoadReport";
import { titlePanel } from "../../components/study/panels/study";
import { ReplaceConfirm } from "../../components/study/ReplaceConfirm";
import { StudyBar } from "../../components/study/StudyBar";
import { DraftBoundary } from "./DraftBoundary";
import { EstimateSidebar } from "./EstimateSidebar";
import { focusField, locateField } from "./focus";
import { LaunchProvider } from "./Launch";
import { ServerRecordProvider } from "./ServerRecord";
import { stepHeadingId } from "./StepFrame";
import { StepRail } from "./StepRail";
import { STEPS } from "./steps";
import { StudyProvider, useStudy } from "./StudyContext";

// The setup page: one study, held by <StudyProvider>, and the views of it.
//
// The shell knows the steps only as a list (`steps.ts`) and keeps no state of any step. What a view needs of
// the study it reads from `useStudy()`, which this module re-exports for the views that are not steps: the
// study bar and the settings dialog here, and the map in its slot.

export { useStudy } from "./StudyContext";
export type { FileReport, SettingsTarget, SetupView, StudyValue } from "./StudyContext";

/** The id of the element the map is drawn in while the map view is on screen. */
export const STUDY_MAP_SLOT_ID = "study-map-slot";

/** The step on screen with the way to the steps before and after it. */
function StepBody() {
  const { steps, step, stepIndex, goTo, problemsByStep, config, loadTemplate } = useStudy();
  const previous = steps[stepIndex - 1];
  const next = steps[stepIndex + 1];
  const blocking = problemsByStep[step.id].filter((problem) => problem.severity === "error").length;
  return <main className="wizard-workbench">
    <DraftBoundary resetKey={config} onLoadTemplate={loadTemplate}>
      <step.Component step={step} />
    </DraftBoundary>
    <footer className="wizard-actions">
      <button className="button button--secondary" type="button" disabled={previous === undefined} onClick={() => { if (previous) goTo({ stepId: previous.id }); }}>Previous</button>
      {next ? <button className="button button--primary" type="button" onClick={() => goTo({ stepId: next.id })}>Continue</button> : null}
    </footer>
    {next && blocking > 0
      ? <p className="wizard-actions__note">{blocking === 1 ? "1 problem on this step blocks" : `${blocking} problems on this step block`} the launch. You can continue and come back to {blocking === 1 ? "it" : "them"}.</p>
      : null}
  </main>;
}

/** Hands a text to the browser as a download. Used for the one thing the page cannot open: a draft it could not read. */
function downloadText(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** Whether what is being dragged over the page is a file from outside it, and not text or a part of the page. */
function carriesFiles(event: DragEvent): boolean {
  return [...event.dataTransfer.types].includes("Files");
}

function SetupShell() {
  const { step, view, pendingFocus, clearPendingFocus, panelProps, loadNote, unreadableDraft, loadFile, config, loadTemplate } = useStudy();
  const shell = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  // The map is drawn only while it is on screen; where it was looked at is kept here, so it comes back as it was left.
  const mapMemory = useRef(newMapMemory());
  // How many elements of the shell a dragged file is over: the outline stays while it is over any of them.
  const [dragDepth, setDragDepth] = useState(0);

  // A `goTo` is acted on once its step is on the page: the focus goes to the setting it names, or to the step's heading.
  useEffect(() => {
    if (pendingFocus === null || pendingFocus.stepId !== step.id || view !== "steps") return;
    const drawn = shell.current?.querySelector<HTMLElement>(`[data-step="${step.id}"]`);
    if (!drawn) return;
    const field = pendingFocus.path === undefined ? null : locateField(drawn, pendingFocus.path);
    const heading = drawn.querySelector<HTMLElement>(`[id="${stepHeadingId(step.id)}"]`);
    if (field) focusField(field);
    else if (heading) focusField(heading, "start");
    clearPendingFocus();
  }, [pendingFocus, step.id, view, clearPendingFocus]);

  // The bar sticks under the site header, and the rail and the estimate under the bar. Both heights change with
  // the width of the window (the header's links and the bar's actions wrap), so they are measured, not assumed.
  useEffect(() => {
    const measured = bar.current;
    const host = shell.current;
    if (!measured || !host || typeof ResizeObserver === "undefined") return;
    const header = document.querySelector<HTMLElement>(".site-header");
    const observer = new ResizeObserver(() => {
      host.style.setProperty("--study-bar-height", `${Math.ceil(measured.getBoundingClientRect().height)}px`);
      if (header) host.style.setProperty("--site-header-height", `${Math.floor(header.getBoundingClientRect().height)}px`);
    });
    observer.observe(measured);
    if (header) observer.observe(header);
    return () => observer.disconnect();
  }, []);

  // The wheel zooms the diagram, so the page cannot be scrolled from on it. Choosing the map therefore brings the
  // study bar up under the site header, where the map below it is exactly as tall as the rest of the window.
  // A window scrolled further down (the reader came from the foot of a long step) is brought back up to the same
  // place: the bar sticks, so its own position no longer says where the map's toolbar is, and whatever follows the
  // bar does, having slid up behind it. Scrolling away afterwards is the reader's own move and is left alone.
  // The move is a jump, not a glide: the whole view has just changed, and a glide is cut short by the map's
  // own first drawing.
  useEffect(() => {
    if (view !== "map") return;
    const dock = bar.current;
    if (!dock) return;
    const header = document.querySelector<HTMLElement>(".site-header");
    const under = header ? header.getBoundingClientRect().bottom : 0;
    const place = dock.getBoundingClientRect();
    const above = place.top - under;
    if (above > 1) { window.scrollBy({ top: above, behavior: "auto" }); return; }
    const next = dock.nextElementSibling;
    const behind = next ? next.getBoundingClientRect().top - place.bottom : 0;
    if (behind < -1) window.scrollBy({ top: behind, behavior: "auto" });
  }, [view]);

  function onDrop(event: DragEvent) {
    if (!carriesFiles(event)) return;
    // Without this the browser would leave the page to show the file.
    event.preventDefault();
    setDragDepth(0);
    // Exactly one file is a study; `loadFile` says so when more were dropped, and changes nothing.
    void loadFile([...event.dataTransfer.files]);
  }

  return <div
    className={`setup-shell configure-page${dragDepth > 0 ? " is-drop-target" : ""}`}
    ref={shell}
    data-view={view}
    onDragEnter={(event) => { if (carriesFiles(event)) { event.preventDefault(); setDragDepth((depth) => depth + 1); } }}
    onDragOver={(event) => { if (carriesFiles(event)) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } }}
    onDragLeave={(event) => { if (carriesFiles(event)) setDragDepth((depth) => Math.max(0, depth - 1)); }}
    onDrop={onDrop}
  >
    <header className="configure-header setup-header">
      <div className="setup-header__study">
        <div className="page-heading"><p className="eyebrow">Study configuration · editable protocol</p><h1>Build the comparison.</h1></div>
        <titlePanel.Component {...panelProps} />
      </div>
    </header>
    <div className="study-bar-dock" ref={bar}><StudyBar /></div>
    {loadNote ? <p className="prereg-error" role="alert">
      {loadNote}
      {unreadableDraft !== null ? <>{" "}<button type="button" className="text-button" onClick={() => downloadText("unreadable-study-draft.json", unreadableDraft)}>Download the draft that could not be read</button></> : null}
    </p> : null}
    <LoadReport />
    {/* The page animates in, which would pin a fixed element to the page instead of the window: the hint is drawn on the body. */}
    {dragDepth > 0 ? createPortal(<p className="drop-hint" aria-hidden="true">Drop one study file to load it.</p>, document.body) : null}
    {view === "map"
      ? <div className="wizard-layout wizard-layout--map">
        {/* The map view is drawn into this element; the study bar above it and the estimate beside it stay. */}
        <div id={STUDY_MAP_SLOT_ID} className="study-map-slot">
          <DraftBoundary resetKey={config} onLoadTemplate={loadTemplate}>
            <StudyMap memory={mapMemory} />
          </DraftBoundary>
        </div>
        <EstimateSidebar />
      </div>
      : <div className="wizard-layout">
        <StepRail />
        <StepBody />
        <EstimateSidebar />
      </div>}
    <AllSettings />
    <ReplaceConfirm />
  </div>;
}

export function Configure() {
  return <StudyProvider steps={STEPS}>
    <ServerRecordProvider>
      <LaunchProvider>
        <SetupShell />
      </LaunchProvider>
    </ServerRecordProvider>
  </StudyProvider>;
}
