import type { Halt } from "@qr-evals/contracts";
import type { ReactNode } from "react";
import { Link } from "wouter";
import { standingOf } from "./StopNotice";
import "../../styles/checks.css";

/**
 * The live page's one line about a stop by a check. What Resume recovers and the controls are on the run
 * page; this only says where the run stands now and where to go. `state` is the run's state as far as the
 * page knows it; without one the line says nothing about what the run does next.
 */
export function CheckStopLine({ runId, halt, state, needsKeys = false }: { runId: string; halt: Halt; state: string | undefined; needsKeys?: boolean }) {
  const record = <Link href={`/runs/${encodeURIComponent(runId)}`}>Open the run record</Link>;
  const standing = state === undefined && !needsKeys ? "unknown" : standingOf(state, needsKeys);
  const now: Record<typeof standing, ReactNode> = {
    finishing: <>The run takes no new work: requests already under way finish, and the run then stays paused until it is resumed. {record} to read what Resume recovers and to resume or abort the run.</>,
    paused: <>The run is paused and stays paused until it is resumed. {record} to read what Resume recovers and to resume or abort the run.</>,
    needs_keys: <>The server restarted after this stop, so the run needs its provider keys again before it can be resumed. {record} to supply them and resume the run.</>,
    aborted: <>The run was aborted after this stop and before it was resumed. It cannot be resumed. What was stored is kept. {record} to read what the stop saw.</>,
    other: <>The run is now in the state {state}. Its record still lists this stop as not resumed. {record} to read what the stop saw.</>,
    unknown: <>{record} to see where the run stands now and what can be done.</>,
  };
  return <div className="boundary-notice check-stop-line" role="alert" data-standing={standing}>
    <strong>Stopped by a check</strong>
    <span>{halt.sentence} {now[standing]}</span>
  </div>;
}
