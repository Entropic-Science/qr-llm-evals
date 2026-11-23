import { StepFrame } from "./StepFrame";
import type { StepProps } from "./steps";

/** The judge, where an eval is scored by one, and the supervised sessions, where the study has them. */
export function Instruments({ step }: StepProps) {
  return <StepFrame step={step} />;
}
