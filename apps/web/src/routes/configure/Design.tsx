import type { HypothesisPlan } from "@qr-evals/contracts";
import { useMemo } from "react";
import { FixedRules } from "../../components/study/design/FixedRules";
import { HypothesisCard } from "../../components/study/design/HypothesisCard";
import { RecLegend } from "../../components/study/design/RecLegend";
import { SampleSizeTable } from "../../components/study/design/SampleSizeTable";
import { VerdictStamp } from "../../components/study/design/VerdictStamp";
import { VERDICT_MEANING, VERDICT_ORDER } from "../../components/study/design/verdicts";
import { draftHypotheses, familySizes } from "../../lib/design";
import { ProblemList } from "./ProblemList";
import { StepFrame } from "./StepFrame";
import type { StepProps } from "./steps";
import { useStudy } from "./StudyContext";

/** What the study would register, generated from the choices above it: nothing in it is typed. */
function Evidence({ hypotheses, claim }: { hypotheses: readonly HypothesisPlan[]; claim: string }) {
  return <>
    <section className="design-section" aria-labelledby="design-hypotheses">
      <h3 id="design-hypotheses">Hypotheses <small>preview</small></h3>
      <p className="field-note">
        Generated from the choices above; none is typed. There is one per deciding outcome and comparison. Under each are the verdicts
        it can end with as the study is configured, and the reason for each verdict it cannot reach.
      </p>
      {hypotheses.length === 0
        ? <p className="validation-note">No hypothesis can be drafted yet. Choose at least one outcome that decides the study, and keep every selected eval within its bounds.</p>
        : <ol className="hypothesis-list" aria-label="Hypotheses the study would register">
          {hypotheses.map((hypothesis) => <li key={hypothesis.id}><HypothesisCard hypothesis={hypothesis} claim={claim} /></li>)}
        </ol>}
      <details className="settings-details verdict-legend">
        <summary>What the verdicts mean</summary>
        <dl>{VERDICT_ORDER.map((verdict) => <div key={verdict}><dt><VerdictStamp verdict={verdict} /></dt><dd>{VERDICT_MEANING[verdict]}</dd></div>)}</dl>
        <p className="field-note">Each hypothesis ends with exactly one of these, decided by script from the rules of the pre-registration. Invalid and unavailable say that the run fell short, not that nothing is there.</p>
      </details>
    </section>
    {hypotheses.length > 0 ? <section className="design-section" aria-labelledby="design-sample-size">
      <h3 id="design-sample-size">Sample size <small>preview</small></h3>
      <SampleSizeTable hypotheses={hypotheses} />
    </section> : null}
  </>;
}

/**
 * What would count as a finding: the six decisions of the design block, each on its recommended value, then
 * what they add up to (the generated hypotheses, the verdicts each can reach, the sample-size table), what
 * the researcher expects, and what the harness fixes.
 *
 * A study without a design block gets the claim panel alone, which offers to declare one, and a sentence
 * that says what applies until then.
 */
export function Design({ step }: StepProps) {
  const { config, cards, recs, problemsByStep } = useStudy();
  const design = config.design ?? null;
  const hypotheses = useMemo(() => draftHypotheses(config, cards), [config, cards]);
  const listed = <ProblemList problems={problemsByStep[step.id]} label="Problems on this step" />;

  if (design === null) {
    return <StepFrame step={step} lead={<>
      {listed}
      <p className="design-note">
        Until a design is declared, the built-in Study 1 plan applies. Its hypotheses, outcomes and tests are fixed by the harness and
        cannot be edited here; it resolves no hypothesis to a verdict, and no sample size is rated for it. Declaring a design starts
        every choice at its recommended value.
      </p>
    </>} />;
  }

  return <StepFrame
    step={step}
    lead={<>{listed}<RecLegend /></>}
    before={{ expectations: <Evidence hypotheses={hypotheses} claim={design.claim} /> }}
  >
    <FixedRules recs={recs} families={familySizes(hypotheses)} />
  </StepFrame>;
}
