import { StepFrame } from "./StepFrame";
import type { StepProps } from "./steps";

/** What is compared: the comparisons, their control seeds, the positive controls and the observed arm. */
export function Compare({ step }: StepProps) {
  return <StepFrame step={step} />;
}
