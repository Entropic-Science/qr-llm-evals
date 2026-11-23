import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { encodeShareConfig } from "../../lib/config-store";
import { STUDY_FILE_ACCEPT } from "../../lib/study-file";
import { focusField } from "../../routes/configure/focus";
import { useStudy, type SetupView } from "../../routes/configure/StudyContext";
import { fileStateText, standingOf, standingText } from "./standing";

// The study bar: what the study is called, how it stands, which view of it is on screen, and its file.
//
// It is the same bar over the steps and over the map, and it stays under the site header while the page
// scrolls, so saving the study to a file is one click away from anywhere in the setup.

const VIEWS: readonly { id: SetupView; label: string }[] = [{ id: "steps", label: "Steps" }, { id: "map", label: "Map" }];

/** A gear, drawn here so the app needs no icon set. */
function GearIcon() {
  return <svg className="study-bar__gear-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <circle cx="12" cy="12" r="2.6" />
    <circle cx="12" cy="12" r="6.4" />
    {/* Eight teeth around the rim. */}
    <path strokeWidth="2.4" d="M12 2.6v3M12 18.4v3M2.6 12h3M18.4 12h3M5.35 5.35l2.12 2.12M16.53 16.53l2.12 2.12M5.35 18.65l2.12-2.12M16.53 7.47l2.12-2.12" />
  </svg>;
}

/** How the study stands, as a pill. With problems it is a button that opens the settings on them. */
function ValidityPill() {
  const { problems, openSettings } = useStudy();
  const standing = standingOf(problems);
  const text = standingText(standing);
  if (problems.length === 0) return <span className="validity-pill validity-pill--ready"><span aria-hidden="true">✓</span>{text}</span>;
  // The state is in the words and the glyph; the colour and the edge only repeat it.
  const blocked = standing.errors > 0;
  return <button type="button" className={`validity-pill validity-pill--${blocked ? "fix" : "check"}`} onClick={() => openSettings({ problemsOnly: true })}>
    <span aria-hidden="true">{blocked ? "✕" : "!"}</span>{text}<span className="sr-only">. Show {problems.length === 1 ? "it" : "them"} in the settings.</span>
  </button>;
}

/** Steps or Map: a radio group, because exactly one view is on screen. */
function ViewSwitch() {
  const { view, setView } = useStudy();
  const buttons = useRef<Partial<Record<SetupView, HTMLButtonElement | null>>>({});
  function onKeyDown(event: KeyboardEvent) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const other: SetupView = view === "steps" ? "map" : "steps";
    setView(other);
    buttons.current[other]?.focus();
  }
  return <div className="view-switch" role="radiogroup" aria-label="Configuration view" onKeyDown={onKeyDown}>
    {VIEWS.map(({ id, label }) => <button
      key={id}
      ref={(element) => { buttons.current[id] = element; }}
      type="button"
      role="radio"
      aria-checked={view === id}
      tabIndex={view === id ? 0 : -1}
      className={view === id ? "is-current" : undefined}
      onClick={() => setView(id)}
    >{label}</button>)}
  </div>;
}

/** Where the study stands against its file. It is a live region: a save or a first edit after one is announced. */
export function FileStateText({ className }: { className: string }) {
  const { fileState, fileMark, problems, saved } = useStudy();
  const text = fileStateText(fileState, fileMark, standingOf(problems), saved);
  return <p className={`file-state file-state--${fileState} ${className}`} role="status" data-file-state={fileState} data-draft-stored={saved} title={text}>{text}</p>;
}

/** The file picker behind "Load study file": a label over a hidden input, so the label is what is pressed. */
export function LoadStudyFile({ className, onPicked }: { className?: string; onPicked?: () => void }) {
  const { loadFile } = useStudy();
  return <label className={`button button--secondary file-button${className ? ` ${className}` : ""}`}>
    Load study file
    <input
      className="sr-only"
      type="file"
      accept={STUDY_FILE_ACCEPT}
      onChange={(event) => {
        const files = [...(event.target.files ?? [])];
        // The same file can be chosen again after a failed load, which a kept value would not report.
        event.target.value = "";
        onPicked?.();
        if (files.length > 0) void loadFile(files);
      }}
    />
  </label>;
}

/** "More": the actions that are not needed on every visit. */
function MoreMenu() {
  const { config, loadFile, startFromTemplate, tell, saveFile, canSaveAs } = useStudy();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const root = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const share = encodeShareConfig(config);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("mousedown", onPointerDown);
    const items = [...(root.current?.querySelectorAll<HTMLElement>("[role='menuitem']:not([disabled])") ?? [])];
    (items.find((item) => item.getClientRects().length > 0) ?? items[0])?.focus();
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) toggle.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent) {
    if (!open) return;
    if (event.key === "Escape") { event.stopPropagation(); close(true); return; }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = [...(root.current?.querySelectorAll<HTMLElement>("[role='menuitem']:not([disabled])") ?? [])];
    // An item the bar shows only where it is narrow is not drawn on a wide one, and cannot take the focus there.
    const drawn = items.filter((item) => item.getClientRects().length > 0);
    const list = drawn.length > 0 ? drawn : items;
    const at = list.indexOf(document.activeElement as HTMLElement);
    list[(at + (event.key === "ArrowDown" ? 1 : list.length - 1)) % list.length]?.focus();
  }

  async function copyShareLink() {
    close(true);
    if (share === null) return;
    try {
      // The link goes to the clipboard and nowhere else. A share link is used once, when a page opens with it:
      // left in the address bar, a reload would put this copy back over every later edit.
      const url = new URL("/", window.location.origin);
      url.searchParams.set("c", share);
      await navigator.clipboard.writeText(url.toString());
      tell("Share link copied. It holds the study as it stands and none of the keys.");
    } catch {
      tell("The share link could not be copied. The browser refused access to the clipboard.", true);
    }
  }

  return <div className="study-bar__more" ref={root} onKeyDown={onKeyDown}>
    <button ref={toggle} type="button" className="button button--secondary study-bar__more-toggle" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} onClick={() => setOpen((was) => !was)}>
      More<span aria-hidden="true" className="study-bar__caret">▾</span>
    </button>
    {/* The picker of the menu's own "Load study file", which stands in for the bar's button where the bar is narrow. */}
    <input
      ref={picker}
      className="sr-only"
      type="file"
      tabIndex={-1}
      aria-hidden="true"
      accept={STUDY_FILE_ACCEPT}
      onChange={(event) => {
        const files = [...(event.target.files ?? [])];
        event.target.value = "";
        if (files.length > 0) void loadFile(files);
      }}
    />
    {open ? <ul className="study-bar__menu" id={menuId} role="menu" aria-label="More study actions">
      <li role="none" className="study-bar__narrow-only">
        <button type="button" role="menuitem" onClick={() => { close(true); picker.current?.click(); }}>Load study file</button>
      </li>
      <li role="none">
        <button type="button" role="menuitem" onClick={() => { close(true); void saveFile(); }}>Save</button>
      </li>
      <li role="none">
        <button type="button" role="menuitem" onClick={() => { close(true); void saveFile({ saveAs: true }); }}>Save as…</button>
        {canSaveAs ? null : <small>This browser saves a study file as a download; it decides where the file goes.</small>}
      </li>
      <li role="none">
        <button type="button" role="menuitem" onClick={() => { close(true); startFromTemplate(); }}>Start from Study 1 template</button>
      </li>
      <li role="none">
        <button type="button" role="menuitem" disabled={share === null} onClick={() => void copyShareLink()}>Copy share link</button>
        {share === null ? <small>This study is not valid yet, so it cannot be shared.</small> : null}
      </li>
    </ul> : null}
  </div>;
}

/**
 * The study bar. Left to right: the study's title, how it stands, the view, the state of its file, and the
 * actions on it. "Save study file" is always enabled: a draft with problems is saved as it is, and the report
 * under the bar then says whether that file will load back. Where the browser lets the page write a file, the
 * first save asks where and later ones write the same file; "Save as" under More asks again.
 */
export function StudyBar() {
  const { config, saveFile, openSettings } = useStudy();
  const bar = useRef<HTMLElement>(null);
  const title = config.title.trim();

  /** The title is edited in the page's own title field; where a view does not draw one, in the settings. */
  function editTitle() {
    const field = bar.current?.closest(".setup-shell")?.querySelector<HTMLElement>("[data-path='title']");
    if (field) focusField(field);
    else openSettings({ section: "study", path: "title" });
  }

  return <section className="study-bar" aria-label="Study" ref={bar}>
    <div className="study-bar__study">
      <div className="study-bar__name">
        <button type="button" className="study-bar__title" title={title || undefined} onClick={editTitle}>
          <span className="sr-only">Edit the title of</span>{" "}{title || "Untitled study"}
        </button>
        <FileStateText className="study-bar__state" />
      </div>
      <ValidityPill />
      <ViewSwitch />
    </div>
    <div className="study-bar__actions">
      <LoadStudyFile className="study-bar__load" />
      <button type="button" className="button button--primary study-bar__save" onClick={() => { void saveFile(); }}><span>Save <span className="study-bar__wide-only">study file</span></span></button>
      <button type="button" className="icon-button study-bar__gear" aria-label="All settings" data-settings-gear onClick={() => openSettings()}><GearIcon /></button>
      <MoreMenu />
    </div>
  </section>;
}
