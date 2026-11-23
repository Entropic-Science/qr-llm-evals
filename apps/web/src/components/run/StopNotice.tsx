import type { Halt } from "@qr-evals/contracts";
import { useId } from "react";
import { KeyForm } from "./KeyForm";
import { phaseText, scopeText, tally, tallyLabel, timeText } from "./checks";
import "../../styles/checks.css";

/** Where the run stands, as far as the notice needs to know. */
export type Standing = "paused" | "finishing" | "needs_keys" | "aborted" | "other";

export function standingOf(state: string | undefined, needsKeys: boolean): Standing {
  if (state === "aborted") return "aborted";
  if (needsKeys || state === "needs_keys") return "needs_keys";
  if (state === "paused") return "paused";
  // The halt is stored the moment the rule fires; the state changes to paused once the requests under way have
  // ended. A run that is resumed closes its halts as it goes back to running, so running with an open halt is only this.
  return state === "running" ? "finishing" : "other";
}

/**
 * Who can resume. The server accepts a control request only with the host token, which it hands out once, in
 * the answer to the launch; the launching tab keeps it in session storage (`lib/host-token.ts`).
 */
function whoCanResume(standing: "paused" | "needs_keys", isHost: boolean): string {
  if (standing === "needs_keys") {
    return isHost
      ? "The server restarted after this stop, so the run first needs its provider keys again. Supply them in the form on this page. The Resume button appears once a key is accepted."
      : "The server restarted after this stop, so the run needs its provider keys again before it can be resumed. Only a browser tab that holds the run's host token can supply them and resume.";
  }
  return isHost
    ? "This browser tab holds the run's host token, so the Resume and Abort buttons below work here. The run stays paused until Resume is pressed. The server refuses Resume while the account that launched the run is disabled or has no token quota left, and the page then shows the server's reason."
    : "Only a browser tab that holds the run's host token can resume or abort the run. The server gives that token once, to the tab that launched the run, and does not issue it again. This view has no token, so it shows no controls.";
}

/** While the requests that were under way when the rule fired are still ending. */
const FINISHING_TEXT = "The check has fired and the run takes no new work. Requests that were already under way are finishing. The run changes to paused when they are done, and it can be resumed from then.";

const ABORT_TEXT = "Abort ends the run for good. An aborted run cannot be resumed, and only a complete run can be analysed. What is stored so far is kept.";

/**
 * "Stopped by a check": one open halt of a run, in the harness's own words.
 *
 * The sentence, the tally, the samples and what Resume recovers are read from the halt. What this component
 * adds is where the run stands now and who can act, and for a judge that refuses requests the key form.
 */
export function StopNotice({ runId, halt, state, needsKeys = false, hostToken, onKeysSupplied }: {
  runId: string;
  halt: Halt;
  state: string | undefined;
  needsKeys?: boolean;
  hostToken: string | undefined;
  onKeysSupplied: () => void;
}) {
  const titleId = useId();
  const standing = standingOf(state, needsKeys);
  const counted = tally(halt);
  const samples = halt.sample_refs ?? [];
  const isHost = Boolean(hostToken);
  return <section className="stop-notice" role="alert" aria-labelledby={titleId} data-rule={halt.rule}>
    <p className="eyebrow">Result check</p>
    <h2 id={titleId}>Stopped by a check</h2>
    <p className="stop-notice__saw">{halt.sentence}</p>
    <dl className="stop-notice__facts">
      <div><dt>Where and when</dt><dd>{scopeText(halt)}, {phaseText(halt)}, on <time dateTime={halt.at}>{timeText(halt.at)}</time>.</dd></div>
      {counted.length ? <div><dt>{tallyLabel(halt)}</dt><dd><ul className="stop-notice__tally">{counted.map(([cause, count]) => <li key={cause}><code>{cause}</code> × {count}</li>)}</ul></dd></div> : null}
      {samples.length ? <div><dt>Samples the stop names</dt><dd><ul className="stop-notice__samples">{samples.map((ref) => <li key={ref}><code>{ref}</code></li>)}</ul><small>A stop names at most three samples.</small></dd></div> : null}
      {standing === "finishing" ? <div><dt>Now</dt><dd>{FINISHING_TEXT}</dd></div> : null}
      {standing === "paused" || standing === "finishing" || standing === "needs_keys" ? <div><dt>What Resume recovers</dt><dd>{halt.recovery}</dd></div> : null}
      {standing === "paused" || standing === "needs_keys" ? <div><dt>Who can resume</dt><dd>{whoCanResume(standing, isHost)}</dd></div> : null}
      {standing === "paused" || standing === "finishing" ? <div><dt>Abort</dt><dd>{ABORT_TEXT}</dd></div> : null}
      {standing === "aborted" ? <div><dt>Now</dt><dd>The run was aborted after this stop and before it was resumed. It cannot be resumed. What was stored is kept.</dd></div> : null}
      {standing === "other" ? <div><dt>Now</dt><dd>The run is in the state {state ?? "unknown"}. Its record still lists this stop as not resumed.</dd></div> : null}
    </dl>
    {halt.rule === "judge_rejected" && standing === "paused" && hostToken ? <KeyForm runId={runId} hostToken={hostToken} title="Supply a judge key" onSupplied={onKeysSupplied}>
      The study's judge is called with the key in the slot <code>anthropic</code>. An eval that names its own judge model uses the slot of that model's provider. A key supplied here replaces the one the run holds; it stays in the server's memory for this run and is not written to disk. Supply it, then press Resume.
    </KeyForm> : null}
  </section>;
}
