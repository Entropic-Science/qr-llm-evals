import { StepFrame } from "./StepFrame";
import type { StepProps } from "./steps";

/** What is measured: the evals that run in every arm, and the prompts and repetitions of each. */
export function Measure({ step }: StepProps) {
  return <StepFrame step={step} />;
}
