import type { HealthSummary } from "@qr-evals/contracts";
import "../../styles/checks.css";

const carry = (count: number) =>
  `${count} sample${count === 1 ? " carries" : "s carry"} a taint in this run.`;
const leftOut = (count: number) =>
  `The clean analysis leaves out ${count} sample${count === 1 ? "" : "s"} for a taint.`;
/** Why the two counts can differ (`excluded_refs` in the backend's `analysis/endpoints.py`). */
const NOT_THE_SAME =
  "A taint marks a sample and does not always remove it: a sample whose only taint is stream_error and that was generated again and completed stays in the analysis.";

/**
 * The taint report of one run. Its counts are the health summary's, which reads the run's taint file; the
 * run record itself holds no taints. Without a summary the report says so instead of printing a zero.
 * `loading` is the time before the summary has answered.
 *
 * `excluded` is the number of samples the analysis leaves out, from the arm ledger of the analysis index. It
 * is not the tainted count; without it the report says the number is not recorded instead of implying one.
 */
export function TaintBanner({
  summary,
  loading = false,
  rule,
  excluded,
}: {
  summary: Pick<HealthSummary, "taints" | "tainted_samples"> | undefined;
  loading?: boolean;
  rule?: string | undefined;
  excluded?: number | undefined;
}) {
  const kinds = Object.entries(summary?.taints ?? {}).filter(
    (entry): entry is [string, number] =>
      typeof entry[1] === "number" && entry[1] > 0,
  );
  const tainted = (summary?.tainted_samples ?? 0) > 0 || kinds.length > 0;
  return (
    <section
      className={
        tainted || (!summary && !loading)
          ? "results-notice"
          : "results-integrity"
      }
      aria-label="Taint report"
    >
      <h2>Taint report</h2>
      {summary ? (
        <>
          <p>{carry(summary.tainted_samples)}</p>
          {excluded !== undefined ? (
            <p className="taint-excluded">
              {leftOut(excluded)}
              {excluded !== summary.tainted_samples && ` ${NOT_THE_SAME}`}
            </p>
          ) : (
            tainted &&
            rule && (
              <p className="taint-excluded">
                How many of them the analysis leaves out is not recorded here.{" "}
                {NOT_THE_SAME}
              </p>
            )
          )}
          {kinds.length > 0 && (
            <ul className="taint-kinds" aria-label="Taints by kind">
              {kinds.map(([kind, count]) => (
                <li key={kind}>
                  <code>{kind}</code> {count}
                </li>
              ))}
            </ul>
          )}
          {kinds.length > 0 && (
            <p>
              <small>
                A sample or a supervised window counts once for each kind of
                taint it carries.
              </small>
            </p>
          )}
        </>
      ) : loading ? (
        <p role="status">Loading the taint counts of this run...</p>
      ) : (
        <p>
          The taint counts of this run could not be loaded, so none are shown.
        </p>
      )}
      {rule && <p>Registered exclusion rule: {rule}</p>}
    </section>
  );
}
