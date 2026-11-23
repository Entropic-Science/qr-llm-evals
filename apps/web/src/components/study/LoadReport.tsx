import { useStudy } from "../../routes/configure/StudyContext";
import { standingOf, thingsToFix } from "./standing";

/**
 * What became of the last study file: what a loaded file held and what is left to fix in it, or why a file
 * was not loaded. It is announced as a status, and as an alert when it reports a failure or problems.
 *
 * The counts are the file's, as it was loaded. The problems are the study's as it stands now, so the line goes
 * once they are fixed.
 */
export function LoadReport({ inDialog = false }: { inDialog?: boolean }) {
  const { fileReport, dismissFileReport, problems, openSettings } = useStudy();
  if (fileReport === null) return null;
  const standing = standingOf(problems);
  const dismiss = <button type="button" className="text-button load-report__dismiss" onClick={dismissFileReport}>Dismiss</button>;

  if (fileReport.kind === "reading") {
    return <div className="load-report" role="status" data-report="reading"><p>Reading {fileReport.name}.</p></div>;
  }
  if (fileReport.kind === "failed") {
    return <div className="load-report load-report--failed" role="alert" data-report="failed">
      <p><span className="load-report__mark" aria-hidden="true">✕</span>{fileReport.message}</p>
      {fileReport.setting ? <p>The setting it names is <code>{fileReport.setting}</code>. The study you are editing was not changed.</p> : null}
      {dismiss}
    </div>;
  }
  if (fileReport.kind === "note") {
    return <div className={`load-report${fileReport.failed ? " load-report--failed" : ""}`} role={fileReport.failed ? "alert" : "status"} data-report="note">
      <p><span className="load-report__mark" aria-hidden="true">{fileReport.failed ? "✕" : "✓"}</span>{fileReport.message}</p>
      {dismiss}
    </div>;
  }

  const { name, arms, evals, hypotheses, unknownCards, checked, serverProblems } = fileReport;
  const flagged = unknownCards.length > 0 || problems.length > 0 || serverProblems.length > 0;
  return <div className={`load-report${flagged ? " load-report--flagged" : ""}`} role={flagged ? "alert" : "status"} data-report="loaded">
    <p>
      <span className="load-report__mark" aria-hidden="true">{flagged ? "!" : "✓"}</span>
      Loaded {name}: {arms} {arms === 1 ? "arm" : "arms"}, {evals} {evals === 1 ? "eval" : "evals"}
      {hypotheses === null
        ? ". No design block: the analysis plan will be the built-in Study 1 plan until you add one on the Design step."
        : `, ${hypotheses} ${hypotheses === 1 ? "hypothesis" : "hypotheses"}.`}
    </p>
    {checked ? null : <p>Loaded without the server&apos;s checks.</p>}
    {unknownCards.length > 0 ? <p>It names evals this server does not have: {unknownCards.join(", ")}.</p> : null}
    {/* The server's words are shown, not folded away: the page has no check of its own for some of what the
        server refuses, so this list can be the only place such a problem is said. */}
    {serverProblems.length > 0 ? <div className="load-report__server" data-server-problems={serverProblems.length}>
      <p>
        The study was loaded as it is. The server read the file and would refuse this study at launch
        for {serverProblems.length === 1 ? "the reason" : `the ${serverProblems.length} reasons`} below, in its own words:
      </p>
      <ul>{serverProblems.map((message) => <li key={message}>{message}</li>)}</ul>
    </div> : null}
    {problems.length > 0 ? <p className="load-report__problems">
      {standing.errors > 0 ? thingsToFix(standing.errors) : null}
      {standing.errors > 0 && standing.warnings > 0 ? ", " : null}
      {standing.warnings > 0 ? `${standing.warnings} to check` : null}
      .{" "}
      {inDialog
        ? <button type="button" className="text-button" onClick={() => openSettings({ problemsOnly: true })}>Show only the problems</button>
        : <button type="button" className="text-button" onClick={() => openSettings({ problemsOnly: true })}>Open settings</button>}
    </p> : null}
    {dismiss}
  </div>;
}
