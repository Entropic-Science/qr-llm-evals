import { stepState, type StepState } from "./steps";
import { useStudy } from "./StudyContext";

const GLYPH: Record<StepState, string> = { "has-errors": "✕", "has-warnings": "!", "is-clear": "○" };
const two = (value: number) => String(value).padStart(2, "0");

/**
 * The steps of the setup, each with how it stands: problems to fix, things to check, or clear.
 *
 * The state is words and a glyph as well as a class, and it is in the button's name ("Design: 1 to check").
 * Every step can be opened at any time; problems stop the launch, never the way through the steps.
 */
export function StepRail() {
  const { steps, step, stepIndex, setStep, problemsByStep } = useStudy();
  return <nav className="step-rail" aria-label="Setup steps">
    <p className="step-rail__progress">{two(stepIndex + 1)} / {two(steps.length)}</p>
    <ol>
      {steps.map((candidate, index) => {
        const { state, text } = stepState(problemsByStep[candidate.id]);
        const current = candidate.id === step.id;
        return <li key={candidate.id}>
          <button
            type="button"
            className={`${current ? "is-current " : ""}${state}`}
            aria-current={current ? "step" : undefined}
            aria-label={`${candidate.label}: ${text}`}
            onClick={() => setStep(candidate.id)}
          >
            <span aria-hidden="true">{two(index + 1)}</span>
            <span className="step-rail__label">{candidate.label}</span>
            <span className="step-rail__state"><span className="step-rail__glyph" aria-hidden="true">{GLYPH[state]}</span>{text}</span>
          </button>
        </li>;
      })}
    </ol>
  </nav>;
}
