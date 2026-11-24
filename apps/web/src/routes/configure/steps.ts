import type { EvalCard } from "@qr-evals/contracts";
import type { JSX } from "react";
import { comparisonPanel, conditioningPanel } from "../../components/study/panels/arms";
import { alphaPanel, claimPanel, expectationsPanel, marginsPanel, outcomesPanel, positiveControlPanel } from "../../components/study/panels/design";
import { judgePanel } from "../../components/study/panels/judge";
import { evalsPanel, promptsPanel } from "../../components/study/panels/measures";
import { peoplePanel } from "../../components/study/panels/study";
import { sessionsPanel } from "../../components/study/panels/supervised";
import type { WizardStudyConfig } from "../../lib/config-store";
import { needsStudyJudge } from "../../lib/judge";
import type { PanelDef } from "../../lib/panels";
import { problemsFor, type Problem, type StepId } from "../../lib/problems";
import { Compare } from "./Compare";
import { Design } from "./Design";
import { Instruments } from "./Instruments";
import { Measure } from "./Measure";
import { Review } from "./Review";

// The setup steps, as data.
//
// A step is a heading, a sentence or two, and settings panels from `lib/panels.ts`; it has no field of its
// own. The rail, the step numbers and every "go to that step" link are derived from this list, so adding,
// removing or reordering a step is an edit here and nowhere else. No step number is typed in any copy.

export interface StepProps {
  step: StepDef;
}

export interface StepDef {
  id: StepId;
  /** The step's name on the rail and in links to it. */
  label: string;
  heading: string;
  intro: string;
  /** The settings panels the step draws for this study, in order. Advanced panels belong to the settings page. */
  panels: (config: WizardStudyConfig, cards: readonly EvalCard[]) => readonly PanelDef[];
  /** Whether the step is offered for this study. */
  shown: (config: WizardStudyConfig, cards: readonly EvalCard[]) => boolean;
  /** The problems, out of all the study's, that are listed under this step. */
  problems: (all: readonly Problem[]) => Problem[];
  Component: (props: StepProps) => JSX.Element;
}

const always = () => true;
const designed = (config: WizardStudyConfig) => config.design !== null && config.design !== undefined;
const supervised = (config: WizardStudyConfig) => config.supervised !== null && config.supervised !== undefined;

export const STEPS: readonly StepDef[] = [
  {
    id: "compare",
    label: "Compare",
    heading: "What is compared",
    intro: "Choose the models and sampling presets to compare. In each comparison one quantum arm is set against the mean of several seeded pseudo-random control runs.",
    panels: () => [comparisonPanel, conditioningPanel],
    shown: always,
    problems: (all) => problemsFor("compare", all),
    Component: Compare,
  },
  {
    id: "measure",
    label: "Measure",
    heading: "What is measured",
    intro: "Choose the evals that run in every arm, and how many prompts and repetitions each one gets. The token-level measures always run.",
    panels: () => [evalsPanel, promptsPanel],
    shown: always,
    problems: (all) => problemsFor("measure", all),
    Component: Measure,
  },
  {
    id: "design",
    label: "Design",
    heading: "What would count as a finding",
    intro: "Say, before any data exist, what would count as a finding. Each choice starts at its recommended value and is written into the pre-registration.",
    // A study on the built-in plan has nothing to set but the way out of it, and what the researcher expects.
    panels: (config) => (designed(config)
      ? [claimPanel, outcomesPanel, alphaPanel, promptsPanel, positiveControlPanel, marginsPanel, expectationsPanel]
      : [claimPanel, expectationsPanel]),
    shown: always,
    problems: (all) => problemsFor("design", all),
    Component: Design,
  },
  {
    id: "instruments",
    label: "Instruments",
    heading: "Judge and sessions",
    intro: "Set the judge that scores some of the evals and the way supervised sessions are run. Only the instruments this study uses are shown.",
    panels: (config, cards) => [...(needsStudyJudge(config, cards) ? [judgePanel] : []), ...(supervised(config) ? [sessionsPanel] : [])],
    // A study with no judged eval and no supervised sessions has no instrument to set.
    shown: (config, cards) => needsStudyJudge(config, cards) || supervised(config),
    problems: (all) => problemsFor("instruments", all),
    Component: Instruments,
  },
  {
    id: "review",
    label: "Review & launch",
    heading: "Review and launch",
    intro: "Check the study, add the keys it needs and launch. A problem marked as blocking has to be fixed first; a warning does not stop a launch.",
    panels: () => [peoplePanel],
    shown: always,
    problems: (all) => problemsFor("review", all),
    Component: Review,
  },
];

export type StepState = "has-errors" | "has-warnings" | "is-clear";

/** How a step stands, from the problems listed under it: as a class for the rail, and as words. */
export function stepState(own: readonly Problem[]): { state: StepState; text: string } {
  const errors = own.filter((problem) => problem.severity === "error").length;
  const warnings = own.length - errors;
  if (errors > 0) return { state: "has-errors", text: warnings > 0 ? `${errors} to fix, ${warnings} to check` : `${errors} to fix` };
  if (warnings > 0) return { state: "has-warnings", text: `${warnings} to check` };
  return { state: "is-clear", text: "clear" };
}
