import { Fragment, type ReactNode } from "react";
import { ProblemList } from "./ProblemList";
import type { StepDef } from "./steps";
import { useStudy } from "./StudyContext";

interface StepFrameProps {
  step: StepDef;
  /** Drawn in place of the step's own problem list, for a step that lists problems its own way. */
  lead?: ReactNode;
  /** Drawn among the step's panels: each entry right before the panel with that id. */
  before?: Readonly<Record<string, ReactNode>>;
  /** Drawn under the step's panels. */
  children?: ReactNode;
}

/** The id of a step's heading: what `aria-labelledby` names and where a `goTo` without a setting lands. */
export const stepHeadingId = (stepId: string) => `step-${stepId}`;

/**
 * One setup step: its number, heading and intro, the problems listed under it, and its settings panels.
 *
 * The number comes from the step's place among the steps offered, so no copy names a step by number.
 */
export function StepFrame({ step, lead, before, children }: StepFrameProps) {
  const { config, cards, steps, panelProps, problemsByStep } = useStudy();
  const position = steps.findIndex((candidate) => candidate.id === step.id) + 1;
  return <section className="wizard-step" aria-labelledby={stepHeadingId(step.id)} data-step={step.id}>
    <p className="eyebrow">Protocol {String(position).padStart(2, "0")}</p>
    <h2 id={stepHeadingId(step.id)} tabIndex={-1}>{step.heading}</h2>
    <p className="step-intro">{step.intro}</p>
    {lead === undefined ? <ProblemList problems={problemsByStep[step.id]} label="Problems on this step" /> : lead}
    <div className="wizard-step__panels">
      {step.panels(config, cards).map((panel) => <Fragment key={panel.id}>{before?.[panel.id]}<panel.Component {...panelProps} /></Fragment>)}
    </div>
    {children}
  </section>;
}
