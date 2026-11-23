import { useId } from "react";
import { panelFor } from "../../lib/panels";
import type { Problem } from "../../lib/problems";
import { problemPath } from "./problem-target";
import { stepState } from "./steps";
import { useStudy } from "./StudyContext";

interface ProblemListProps {
  problems: readonly Problem[];
  /** What the list is called for a screen reader. */
  label: string;
  /** Whether each row names its step and links to it. Without it the rows link to their setting on this step. */
  showStep?: boolean;
  /** With `showStep`: the step's name is spoken, not drawn, because a heading above the list already shows it. */
  stepUnderHeading?: boolean;
  /** Whether a row offers the problem's one-click fix. On a step the fix is offered at the field itself. */
  fixes?: boolean;
}

/** The one problem whose message is not this page's: the server's refusal, shown word for word. */
const SERVER_REFUSAL = "review.server_rejected";

const SEVERITY: Record<Problem["severity"], { text: string; glyph: string }> = {
  error: { text: "Blocks launch", glyph: "✕" },
  warning: { text: "Warning", glyph: "!" },
};

/**
 * Problems of the study, each with its severity in words, its message and the way to its setting.
 *
 * Severity is carried by the words and the glyph as well as by the colour of the row's edge. Nothing is drawn
 * for an empty list.
 */
export function ProblemList({ problems, label, showStep = false, stepUnderHeading = false, fixes = false }: ProblemListProps) {
  const { config, cards, updateConfig, goTo, openSettings, stepFor, step } = useStudy();
  const scope = useId();
  if (problems.length === 0) return null;
  return <ul className="problem-list" aria-label={label}>
    {problems.map((problem, index) => {
      const home = stepFor(problem);
      const path = problemPath(problem);
      const severity = SEVERITY[problem.severity];
      // A setting that only the settings dialog draws (the model table, the seed list, the title) is shown there:
      // a step that does not have its panel could only put the focus on its own heading.
      const panel = path === undefined ? undefined : panelFor(path);
      const inDialog = path !== undefined && panel !== undefined && !home.panels(config, cards).includes(panel);
      const elsewhere = showStep && home.id !== step.id && !inDialog;
      const fix = fixes ? problem.fix : undefined;
      // Two rows can offer a button of the same name; each button is described by the message of its own row.
      const messageId = `${scope}-${index}`;
      return <li key={`${problem.code}|${problem.nodeId}|${problem.message}`} className={`problem problem--${problem.severity}`} data-code={problem.code}>
        <span className="problem__severity"><span aria-hidden="true">{severity.glyph}</span> {severity.text}{showStep ? <small className={stepUnderHeading ? "sr-only" : undefined}> · {home.label}</small> : null}</span>
        <span className="problem__message" id={messageId}>{problem.message}</span>
        {problem.code === SERVER_REFUSAL ? <span className="problem__source">This is the run service's own message: it read the study and refused it. Change the study and it is asked again.</span> : null}
        {elsewhere || path !== undefined || fix ? <span className="problem__actions">
          {elsewhere
            ? <button type="button" className="text-button" aria-describedby={messageId} onClick={() => goTo({ stepId: home.id, ...(path === undefined ? {} : { path }) })}>Open {home.label}</button>
            : path !== undefined ? <button type="button" className="text-button" aria-describedby={messageId} onClick={() => (inDialog ? openSettings({ path }) : goTo({ stepId: home.id, path }))}>Show the setting</button> : null}
          {fix ? <button type="button" className="text-button" aria-describedby={messageId} onClick={() => updateConfig(fix.apply(config, cards))}>{fix.label}</button> : null}
        </span> : null}
      </li>;
    })}
  </ul>;
}

/**
 * Every problem of the study, grouped under the step it belongs to, in step order.
 *
 * Each group names its step and says how it stands in the words of the rail ("1 to fix"). A row links to its
 * step and setting and offers its one-click fix when it has one.
 */
export function ProblemGroups({ label }: { label: string }) {
  const { steps, problemsByStep } = useStudy();
  const groups = steps.map((step) => ({ step, own: problemsByStep[step.id] })).filter(({ own }) => own.length > 0);
  if (groups.length === 0) return null;
  return <ul className="problem-groups" aria-label={label}>
    {groups.map(({ step, own }) => <li key={step.id} className="problem-group" data-step-group={step.id}>
      <h3 className="problem-group__step">{step.label} <small>{stepState(own).text}</small></h3>
      <ProblemList problems={own} label={`Problems on ${step.label}`} showStep stepUnderHeading fixes />
    </li>)}
  </ul>;
}
