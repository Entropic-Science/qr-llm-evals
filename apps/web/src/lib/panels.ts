import type { JSX } from "react";
import { armTablePanel, estimatesPanel, modelsPanel, pilotPanel, recordPanel, seedListPanel } from "../components/study/panels/advanced";
import { comparisonPanel } from "../components/study/panels/arms";
import { alphaPanel, claimPanel, expectationsPanel, marginsPanel, outcomesPanel, positiveControlPanel } from "../components/study/panels/design";
import { judgePanel, narrativePanel } from "../components/study/panels/judge";
import { evalsPanel, promptsPanel } from "../components/study/panels/measures";
import { peoplePanel, titlePanel } from "../components/study/panels/study";
import { displaySettingsPanel, sessionsPanel } from "../components/study/panels/supervised";
import type { FieldProps } from "../routes/configure/fields/Field";

// The settings panels: the one place each setting of a study is edited.
//
// A wizard step, the settings page and the map inspector all draw panels from this list; none of them has a
// field of its own. `paths` says which keys of the study a panel edits, and `panels.test.ts` walks the
// StudyConfig schema to check that every key is claimed by exactly one panel, so a key added to the schema
// cannot be left without a place to edit it.

export type SectionId = "study" | "arms" | "measures" | "design" | "supervised" | "judge" | "advanced";

/** What a panel is given. Nothing in it belongs to the wizard. */
export type PanelProps = FieldProps;

export interface PanelDef {
  id: string;
  section: SectionId;
  label: string;
  /** Shown on the settings page only, not in a wizard step. */
  advanced?: boolean;
  Component: (props: PanelProps) => JSX.Element;
  /**
   * The study key paths the panel edits: list items as `[]`, the keys of an open map as `.*`
   * (`arms[].study_seed`, `estimates.tps_by_model.*`). A panel that rewrites a whole list or block names it
   * without a leaf (`arms`); the leaves under it then belong to the panel that edits them one by one.
   */
  paths: readonly string[];
}

/** The sections of the settings page, in page order. */
export const SECTIONS: readonly { id: SectionId; label: string }[] = [
  { id: "study", label: "Study" },
  { id: "arms", label: "Models & arms" },
  { id: "measures", label: "Evals & repetitions" },
  { id: "design", label: "Design & analysis" },
  { id: "supervised", label: "Supervised sessions" },
  { id: "judge", label: "Judge & narrative" },
  { id: "advanced", label: "Advanced" },
];

/** Every panel, in page order: by section, and within a section in reading order. */
export const PANELS: readonly PanelDef[] = [
  titlePanel, peoplePanel,
  comparisonPanel,
  evalsPanel, promptsPanel,
  claimPanel, outcomesPanel, alphaPanel, positiveControlPanel, marginsPanel, expectationsPanel,
  sessionsPanel, displaySettingsPanel,
  judgePanel, narrativePanel,
  armTablePanel, modelsPanel, seedListPanel, estimatesPanel, pilotPanel, recordPanel,
];

export function panelsIn(section: SectionId): PanelDef[] {
  return PANELS.filter((panel) => panel.section === section);
}

const within = (path: string, parent: string) => path === parent || path.startsWith(`${parent}.`) || path.startsWith(`${parent}[`);

/** A study key path for the path of a recommendation (`cards.{id}.items`, `roster.seeds`, `design.margins.{card}/{endpoint}`) or of one list item. */
function studyPath(path: string): string {
  const override = /^cards\.[^.]+\.(k|items)$/.exec(path);
  if (override) return `cards[].overrides.${override[1]}`;
  if (path.startsWith("design.margins.")) return "design.margins[].margin";
  if (path.startsWith("roster.")) return "arms";
  return path.replace(/\[\d+\]/g, "[]");
}

/**
 * The panel that edits a setting, for a link from a problem, a chip or a map node to it. `path` is a study key
 * path or the path of a `Rec`.
 *
 * The panel that names the path itself wins; then the one that names a key under it (`design.margins` leads
 * to the margins panel); then the one that names a block it sits in. A setting the harness fixes has no panel.
 */
export function panelFor(path: string): PanelDef | undefined {
  const generic = studyPath(path);
  // A key of an open map (`estimates.tps_by_model.qr-llm`) is claimed as `estimates.tps_by_model.*`.
  const names = (claimed: string) => claimed === generic || (claimed.endsWith(".*") && generic.startsWith(claimed.slice(0, -1)));
  return PANELS.find((panel) => panel.paths.some(names))
    ?? PANELS.find((panel) => panel.paths.some((claimed) => within(claimed, generic)))
    ?? PANELS.find((panel) => panel.paths.some((claimed) => within(generic, claimed)));
}
