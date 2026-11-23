import { useState } from "react";
import { Link } from "wouter";
import { encodeShareConfig } from "../../lib/config-store";
import { draftHypotheses, sourceCells } from "../../lib/design";
import { hasErrors } from "../../lib/problems";
import { KeysPanel } from "./KeysPanel";
import { useLaunch } from "./Launch";
import { ProblemGroups } from "./ProblemList";
import { ServerHashes } from "./ServerRecord";
import { StepFrame } from "./StepFrame";
import type { StepProps } from "./steps";
import { useStudy } from "./StudyContext";

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;

/** How the study stands, in a sentence: what blocks a launch and what does not. */
function standing(errors: number, warnings: number): string {
  const blocks = errors === 0 ? "" : `${plural(errors, "problem")} ${errors === 1 ? "blocks" : "block"} the launch and the pre-registration, and ${errors === 1 ? "is" : "are"} marked "Blocks launch".`;
  const warns = warnings === 0 ? "" : `${plural(warnings, "warning")} ${warnings === 1 ? "does" : "do"} not stop a launch; ${warnings === 1 ? "it weakens" : "they weaken"} what the study can show.`;
  return [blocks, warns].filter(Boolean).join(" ");
}

/** What the last attempt to copy the share link came to, and the link it was about. */
type CopyState = { share: string; text: string; failed: boolean } | null;

/**
 * The last step: who the study is by, what stands in the way of a launch, the server's hashes of the study,
 * the keys, the pre-registration and the launch itself.
 *
 * Every problem of the study is listed here under its step, with the way to its setting and its one-click
 * fix. An error blocks the launch; a warning does not. The keys never leave this page's memory.
 */
export function Review({ step }: StepProps) {
  const { config, cards, problems } = useStudy();
  const { note, launch } = useLaunch();
  const [copied, setCopied] = useState<CopyState>(null);
  const share = encodeShareConfig(config);
  const errors = problems.filter((problem) => problem.severity === "error").length;
  const warnings = problems.length - errors;
  const blocked = hasErrors(problems);
  const ready = share !== null && !blocked;
  const designed = config.design !== null && config.design !== undefined;

  async function copyShareLink() {
    if (share === null) return;
    try {
      // The link opens the setup with this study. It goes to the clipboard only: a share link is used once
      // when a page loads, so one left in the address bar would put this copy back over later edits.
      const url = new URL("/", window.location.origin);
      url.searchParams.set("c", share);
      await navigator.clipboard.writeText(url.toString());
      setCopied({ share, text: "Share link copied. It holds the study as it stands and none of the keys.", failed: false });
    } catch {
      setCopied({ share, text: "The share link could not be copied. The browser refused access to the clipboard.", failed: true });
    }
  }

  return <StepFrame
    step={step}
    lead={problems.length > 0
      ? <div className="review-problems">
        <p className="review-problems__standing">{standing(errors, warnings)}</p>
        <ProblemGroups label="Problems in this study" />
      </div>
      : <p className="privacy-note">Nothing in this study stands in the way of a launch.</p>}
  >
    <dl className="review-ledger">
      <div><dt>Study</dt><dd>{config.title}</dd></div>
      <div><dt>Comparisons</dt><dd>{sourceCells(config).length}</dd></div>
      <div><dt>Models</dt><dd>{config.models.length}</dd></div>
      <div><dt>Arms</dt><dd>{config.arms.length}</dd></div>
      <div><dt>Evals</dt><dd>{config.cards.length}</dd></div>
      <div><dt>Hypotheses</dt><dd>{designed ? draftHypotheses(config, cards).length : "None of its own: the built-in Study 1 plan applies."}</dd></div>
    </dl>
    <ServerHashes />
    <KeysPanel />
    <div className="review-actions">
      {ready
        ? <Link className="button button--primary" href={`/prereg?c=${encodeURIComponent(share)}`}>Generate pre-registration</Link>
        : <button className="button button--primary" type="button" disabled>Generate pre-registration</button>}
      <button className="button button--secondary" type="button" disabled={share === null} onClick={() => void copyShareLink()}>Copy share link</button>
      <button className="button button--secondary" type="button" disabled={blocked} onClick={launch}>Launch run</button>
    </div>
    {blocked
      ? <p className="validation-note" role="alert">{plural(errors, "problem")} above {errors === 1 ? "blocks" : "block"} the pre-registration and the launch. Warnings do not.</p>
      : share === null ? <p className="validation-note" role="alert">This study is not valid yet, so it cannot be preregistered or shared.</p> : null}
    {copied && copied.share === share ? <p className={copied.failed ? "prereg-action-state prereg-action-state--error" : "prereg-action-state"} role="status">{copied.text}</p> : null}
    {note && <p className="validation-note" role="status">{note}</p>}
  </StepFrame>;
}
