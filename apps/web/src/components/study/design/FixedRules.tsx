import type { FamilySizes } from "@qr-evals/contracts";
import { Link } from "wouter";
import { metaFor } from "../../../lib/design";
import type { Rec } from "../../../lib/recommendations";
import { RecGlyph } from "../../RecChip";

interface FixedRulesProps {
  /** `rate(config, cards)`. The rules are its `fixed.*` entries: what the harness sets and nobody edits. */
  recs: readonly Rec[];
  /** How many tests each Holm family corrects for, when the study has hypotheses. */
  families?: FamilySizes;
}

const TEST_PATH = "fixed.test.";
/** What the harness checks while a run goes. The checks are the same for every study and no setting changes them. */
export const RESULT_CHECKS_TEXT = "The harness checks every reply, score and judgement as the run goes: it flags what looks wrong without excluding it from any analysis, and it pauses the run when something is plainly broken.";
/** The app shows `docs/ANALYSIS_PLAN.md` at this address; its section "Result checks" describes the checks. */
export const RESULT_CHECKS_DOC = "/docs/ANALYSIS_PLAN";
const tests = (count: number) => `${count} test${count === 1 ? "" : "s"}`;

/** What a fixed rule is about. A test belongs to an outcome; every other rule belongs to the study. */
function subject(rec: Rec): string {
  if (!rec.path.startsWith(TEST_PATH)) return rec.label;
  const [cardId = "", endpoint = ""] = rec.path.slice(TEST_PATH.length).split("/");
  return `Test of ${metaFor(cardId, endpoint).label}`;
}

/** The fixed rules, each once: a rule that sits on several arms or comparisons is still one rule. */
export function fixedRules(recs: readonly Rec[]): Rec[] {
  const seen = new Set<string>();
  return recs.filter((rec) => {
    if (!rec.path.startsWith("fixed.") || seen.has(rec.path)) return false;
    seen.add(rec.path);
    return true;
  });
}

/**
 * "Fixed by the harness": what a designed study cannot change, folded away under the choices it can.
 *
 * Every row is a locked recommendation (the padlock of `RecChip`), with its value and the reason in words.
 */
export function FixedRules({ recs, families }: FixedRulesProps) {
  const rules = fixedRules(recs);
  if (rules.length === 0) return null;
  return <details className="fixed-rules">
    <summary>Fixed by the harness <small>{rules.length} rules that are not choices</small></summary>
    <p className="field-note">These follow from the choices above or are the same for every study. None of them can be edited here or in a study file.</p>
    <dl className="fixed-rules__list">
      {rules.map((rec) => <div key={rec.path} data-path={rec.path}>
        <dt>{subject(rec)}</dt>
        <dd>
          <span className="rec rec--locked rating-mark"><RecGlyph state="locked" /><span className="rec__value">{rec.value}</span></span>
          <span>{rec.have} {rec.basis}</span>
        </dd>
      </div>)}
      {families ? <div data-path="fixed.families">
        <dt>Holm families</dt>
        <dd><span>
          {tests(families.difference)} for a difference, {tests(families.equivalence)} for equivalence
          and {tests(families.negative_control)} of seed against seed. Each family is corrected on its own.
        </span></dd>
      </div> : null}
      <div data-path="fixed.result_checks">
        <dt>Result checks</dt>
        <dd><span>
          {RESULT_CHECKS_TEXT}{" "}
          <Link className="text-link" href={RESULT_CHECKS_DOC}>See "Result checks" in the analysis plan.</Link>
        </span></dd>
      </div>
    </dl>
    <p className="field-note">Prompts and repetitions are fixed before the run; nothing stops or extends a run on significance. Only the first full run of a study is confirmatory, and a pilot run never is.</p>
  </details>;
}
