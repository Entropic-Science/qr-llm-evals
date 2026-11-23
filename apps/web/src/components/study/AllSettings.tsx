import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { PANELS, SECTIONS, panelFor, type PanelDef, type SectionId } from "../../lib/panels";
import type { Problem } from "../../lib/problems";
import { FieldScope } from "../../routes/configure/fields/Field";
import { focusField, locateField } from "../../routes/configure/focus";
import { problemPath } from "../../routes/configure/problem-target";
import { useStudy } from "../../routes/configure/StudyContext";
import { Modal } from "../Modal";
import { LoadReport } from "./LoadReport";
import { panelId } from "./panels/Panel";
import { sectionStanding, standingOf } from "./standing";
import { FileStateText, LoadStudyFile } from "./StudyBar";

// All settings: every setting of the study in one long dialog, opened with the gear.
//
// It draws the same panels as the steps, from the same registry and over the same study, so an edit here is an
// edit of the study and shows at once in the step or the map behind the dialog. There is nothing to apply and
// no copy to discard: the file is the durable copy, and saving it is in the footer.

/** The prefix of every element id in the dialog. The same panel may be drawn in the step behind it. */
export const SETTINGS_SCOPE = "settings-";

const sectionElementId = (id: SectionId) => `${SETTINGS_SCOPE}section-${id}`;
const SEVERITY: Record<Problem["severity"], { text: string; glyph: string }> = {
  error: { text: "Blocks launch", glyph: "✕" },
  warning: { text: "Warning", glyph: "!" },
};

/** The panel a problem is fixed in, or undefined for a problem no one setting answers. */
function panelOf(problem: Problem): PanelDef | undefined {
  const path = problemPath(problem);
  return path === undefined ? undefined : panelFor(path);
}

/** The section a problem is listed in: its panel's, or the first for a problem of the study as a whole. */
function sectionOf(problem: Problem): SectionId {
  return panelOf(problem)?.section ?? "study";
}

/** What the filter is matched against for one panel: its name, the keys it edits, and the labels drawn in it. */
function searchText(panel: PanelDef, element: Element | null): string {
  const drawn = element === null ? [] : [
    ...[...element.querySelectorAll("legend, label, .field__label, th, caption, summary")].map((node) => node.textContent ?? ""),
    ...[...element.querySelectorAll("[data-path]")].map((node) => node.getAttribute("data-path") ?? ""),
    ...[...element.querySelectorAll("[aria-label]")].map((node) => node.getAttribute("aria-label") ?? ""),
  ];
  return [panel.label, ...panel.paths, ...drawn].join("\n").toLowerCase();
}

const sameText = (a: Readonly<Record<string, string>>, b: Readonly<Record<string, string>>) => PANELS.every((panel) => a[panel.id] === b[panel.id]);

/** The problems of one section, each with its severity in words, its message, the way to its setting and its one-click fix. */
function SectionProblems({ label, problems, onShow }: { label: string; problems: readonly Problem[]; onShow: (path: string) => void }) {
  const { config, cards, updateConfig } = useStudy();
  if (problems.length === 0) return null;
  return <div role="alert" className="all-settings__problems">
    <ul className="problem-list" aria-label={`Problems in ${label}`}>
      {problems.map((problem) => {
        const path = problemPath(problem);
        const severity = SEVERITY[problem.severity];
        const fix = problem.fix;
        return <li key={`${problem.code}|${problem.nodeId}|${problem.message}`} className={`problem problem--${problem.severity}`} data-code={problem.code}>
          <span className="problem__severity"><span aria-hidden="true">{severity.glyph}</span> {severity.text}</span>
          <span className="problem__message">{problem.message}</span>
          {path !== undefined || fix ? <span className="problem__actions">
            {path !== undefined ? <button type="button" className="text-button" onClick={() => onShow(path)}>Show the setting</button> : null}
            {fix ? <button type="button" className="text-button" onClick={() => updateConfig(fix.apply(config, cards))}>{fix.label}</button> : null}
          </span> : null}
        </li>;
      })}
    </ul>
  </div>;
}

/**
 * The settings dialog. It is open while the study context holds a settings request, and it starts where that
 * request says: on a section, on one setting, or showing only the problems.
 */
export function AllSettings() {
  const { settings, closeSettings, panelProps, problems, saveFile, config, cards } = useStudy();
  const [query, setQuery] = useState("");
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set());
  const [current, setCurrent] = useState<SectionId>("study");
  const [texts, setTexts] = useState<Readonly<Record<string, string>>>({});
  // The setting or section to put the focus on once the dialog is drawn with it in sight.
  const [landing, setLanding] = useState<{ path: string | undefined; section: SectionId | undefined } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const open = settings !== null;
  const nonce = settings?.nonce;

  // Each request starts the dialog afresh: no filter left over from the last visit hides what it points at.
  // (State is reset while rendering, which React does before anything is drawn.)
  const [seen, setSeen] = useState<number | undefined>(undefined);
  if (nonce !== seen) {
    setSeen(nonce);
    if (settings !== null) {
      setQuery("");
      setProblemsOnly(settings.problemsOnly === true);
      setKept(new Set());
      const target = settings.section ?? (settings.path === undefined ? undefined : panelFor(settings.path)?.section);
      if (target !== undefined || seen === undefined) setCurrent(target ?? "study");
      setLanding(settings.path !== undefined || settings.section !== undefined ? { path: settings.path, section: settings.section } : null);
    }
  }

  // The labels a panel draws are only known once it is drawn, and they follow the study (an eval's row names it).
  useLayoutEffect(() => {
    if (!open) return;
    const next = Object.fromEntries(PANELS.map((panel) => [panel.id, searchText(panel, root.current?.querySelector(`[data-panel="${panel.id}"]`) ?? null)]));
    setTexts((before) => (sameText(before, next) ? before : next));
  }, [open, config, cards]);

  const needle = query.trim().toLowerCase();
  const byPanel = useMemo(() => {
    const by = new Map<string, Problem[]>();
    for (const problem of problems) {
      const id = panelOf(problem)?.id;
      if (id !== undefined) by.set(id, [...(by.get(id) ?? []), problem]);
    }
    return by;
  }, [problems]);
  const bySection = useMemo(() => {
    const by = new Map<SectionId, Problem[]>();
    for (const problem of problems) by.set(sectionOf(problem), [...(by.get(sectionOf(problem)) ?? []), problem]);
    return by;
  }, [problems]);

  // A panel shown for its problem stays while "Problems only" is ticked, also once the problem is fixed: it
  // must not vanish under the hands that are fixing it. Unticking the box lets go of them.
  const unkept = problemsOnly ? [...byPanel.keys()].filter((id) => !kept.has(id)) : [];
  if (unkept.length > 0) setKept(new Set([...kept, ...unkept]));
  else if (!problemsOnly && kept.size > 0) setKept(new Set());

  const shows = (panel: PanelDef) => (!problemsOnly || byPanel.has(panel.id) || kept.has(panel.id))
    && (needle === "" || (texts[panel.id] ?? [panel.label, ...panel.paths].join("\n").toLowerCase()).includes(needle));
  const sections = SECTIONS.map((section) => {
    const panels = PANELS.filter((panel) => panel.section === section.id);
    const listed = needle === "" ? bySection.get(section.id) ?? [] : [];
    const visible = panels.filter(shows);
    return { ...section, panels, visible, listed, shown: visible.length > 0 || listed.length > 0 };
  });
  const anything = sections.some((section) => section.shown);

  /** Puts the focus on the setting of a problem. Its panel has that problem, so "Problems only" never hides it. */
  const show = useCallback((path: string) => {
    const panel = panelFor(path);
    if (panel) setCurrent(panel.section);
    setLanding({ path, section: undefined });
  }, []);

  // A landing is acted on after the render that cleared the filters, when its setting is on the page.
  useEffect(() => {
    const scope = root.current;
    if (landing === null || !scope) return;
    if (landing.path !== undefined) {
      const panel = panelFor(landing.path);
      const field = locateField(scope, landing.path) ?? (panel ? scope.querySelector<HTMLElement>(`[id="${panelId(panel.id, SETTINGS_SCOPE)}"]`) : null);
      if (field) { focusField(field); return; }
    }
    if (landing.section !== undefined) {
      const heading = scope.querySelector<HTMLElement>(`[id="${sectionElementId(landing.section)}"] h3`);
      if (heading) focusField(heading, "start");
    }
  }, [landing]);

  // The section in view is the one marked in the list of sections.
  useEffect(() => {
    if (!open) return;
    const body = root.current?.closest<HTMLElement>(".modal__body");
    if (!body) return;
    let frame = 0;
    const mark = () => {
      frame = 0;
      const line = body.getBoundingClientRect().top + 96;
      let inView: SectionId | null = null;
      for (const section of SECTIONS) {
        const element = body.querySelector<HTMLElement>(`[id="${sectionElementId(section.id)}"]`);
        if (element && !element.hidden && element.getBoundingClientRect().top <= line) inView = section.id;
      }
      if (inView !== null) setCurrent(inView);
    };
    const onScroll = () => { if (frame === 0) frame = window.requestAnimationFrame(mark); };
    body.addEventListener("scroll", onScroll, { passive: true });
    return () => { body.removeEventListener("scroll", onScroll); if (frame !== 0) window.cancelAnimationFrame(frame); };
  }, [open]);

  function jumpTo(id: SectionId) {
    setCurrent(id);
    const heading = root.current?.querySelector<HTMLElement>(`[id="${sectionElementId(id)}"] h3`);
    if (heading) focusField(heading, "start");
  }

  const gear = useCallback(() => document.querySelector<HTMLElement>("[data-settings-gear]"), []);

  return <Modal
    open={open}
    onClose={closeSettings}
    size="page"
    eyebrow="Study settings"
    title="Everything in this study"
    restoreFocus={gear}
    toolbar={<div className="all-settings__toolbar">
      <input
        type="search"
        aria-label="Filter settings"
        placeholder="Filter by a name or a key, such as alpha or design.claim"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          // The first Escape clears the filter; the next one closes the dialog.
          if (event.key === "Escape" && query !== "") { event.stopPropagation(); setQuery(""); }
        }}
      />
      <label className="all-settings__toggle"><input type="checkbox" checked={problemsOnly} onChange={(event) => setProblemsOnly(event.target.checked)} />Problems only</label>
    </div>}
    footer={<div className="all-settings__footer">
      <FileStateText className="all-settings__state" />
      <LoadStudyFile />
      <button type="button" className="button button--primary" onClick={() => { void saveFile(); }}>Save study file</button>
      <button type="button" className="button button--secondary" onClick={closeSettings}>Done</button>
    </div>}
  >
    <FieldScope.Provider value={SETTINGS_SCOPE}>
      <div className="all-settings" ref={root}>
        <nav className="all-settings__nav" aria-label="Settings sections">
          <ol>
            {sections.map((section) => {
              const standing = standingOf(bySection.get(section.id) ?? []);
              const state = standing.errors > 0 ? "has-errors" : standing.warnings > 0 ? "has-warnings" : "is-clear";
              return <li key={section.id} hidden={!section.shown}>
                <a
                  href={`#${sectionElementId(section.id)}`}
                  className={`${current === section.id ? "is-current " : ""}${state}`}
                  aria-current={current === section.id ? "true" : undefined}
                  onClick={(event) => { event.preventDefault(); jumpTo(section.id); }}
                >
                  <span className="all-settings__nav-label">{section.label}</span>
                  {state === "is-clear" ? null : <span className="all-settings__nav-state"><span aria-hidden="true">{standing.errors > 0 ? "✕" : "!"}</span> {sectionStanding(standing)}</span>}
                </a>
              </li>;
            })}
          </ol>
        </nav>
        <div className="all-settings__sections">
          <LoadReport inDialog />
          {sections.map((section) => <section key={section.id} id={sectionElementId(section.id)} className="all-settings__section" aria-labelledby={`${sectionElementId(section.id)}-title`} hidden={!section.shown}>
            <h3 id={`${sectionElementId(section.id)}-title`} tabIndex={-1}>{section.label}</h3>
            <SectionProblems label={section.label} problems={section.listed} onShow={show} />
            {section.panels.map((panel) => <div key={panel.id} className={`all-settings__panel${panel.advanced ? " is-advanced" : ""}`} data-panel={panel.id} hidden={!section.visible.includes(panel)}>
              <panel.Component {...panelProps} />
            </div>)}
          </section>)}
          {anything ? null : <p className="all-settings__empty" role="status">
            {needle !== ""
              ? `No settings match "${query.trim()}".`
              : "This study has no problems. Untick \"Problems only\" to see every setting."}
          </p>}
        </div>
      </div>
    </FieldScope.Provider>
  </Modal>;
}
