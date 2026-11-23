import type { RunControlRequest, RunView } from "@qr-evals/contracts";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useReducer, useState } from "react";
import { Link } from "wouter";
import { OutputHealth, OutputHealthUnavailable } from "../components/results/OutputHealth";
import { flagKindsOfEvent, haltFromEvent, openHalts, withHalt } from "../components/run/checks";
import { ControlBar } from "../components/run/ControlBar";
import { KeyForm } from "../components/run/KeyForm";
import { ProgressGrid } from "../components/run/ProgressGrid";
import { ProvenanceBadge } from "../components/run/ProvenanceBadge";
import { ReadinessPanel } from "../components/run/ReadinessPanel";
import { StopNotice } from "../components/run/StopNotice";
import { TaintList } from "../components/run/TaintList";
import { api, openEvents, type RunEvent } from "../lib/api";
import { getHostToken } from "../lib/host-token";
import { downloadUrl } from "../lib/results";
import { clearRefetchSignal, reduceRunEvent, runEventState, type RunEventState } from "../lib/run-events";

type RouteProps = { params: { runId: string } };
type Connection = "connecting" | "live" | "reconnecting" | "closed";

/** One line for an event in the recent-activity list. `flagLabel` gives a flag kind its name from the catalogue. */
export function eventSummary(event: RunEvent, flagLabel: (kind: string) => string = (kind) => kind.replaceAll("_", " ")): string {
  const data = typeof event.data === "object" && event.data !== null ? event.data as Record<string, unknown> : {};
  if (event.type === "run.state") return `state → ${String(data.state ?? "unknown")}`;
  if (event.type === "run.progress") return `${String(data.done ?? 0)} / ${String(data.total ?? 0)} complete`;
  if (event.type === "sample.started") return `${String(data.arm_id ?? "arm")} · ${String(data.card_id ?? "card")}`;
  if (event.type === "sample.finished") {
    const flags = flagKindsOfEvent(event.data).map(flagLabel);
    return `${String(data.finish_reason ?? "finished")} · ${String(data.tokens ?? "?")} tokens${flags.length ? ` · flagged: ${flags.join(", ")}` : ""}`;
  }
  if (event.type === "check.stopped") return `stopped by a check · ${typeof data.sentence === "string" ? data.sentence : "see the notice above"}`;
  if (event.type === "taint") return String(data.kind ?? "integrity warning");
  if (event.type === "resync") return `stream gap · ${String(data.dropped ?? "unknown")} dropped`;
  if (event.type === "error") return String(data.message ?? "runner error");
  return event.type.replaceAll(".", " ");
}

function NeedsKeys({ runId, hostToken, onSupplied }: { runId: string; hostToken: string | undefined; onSupplied: () => void }) {
  if (!hostToken) return <div className="run-alert"><strong>This run needs provider keys.</strong><span>Only its host can re-supply them.</span></div>;
  return <KeyForm runId={runId} hostToken={hostToken} title="Provider keys required" onSupplied={onSupplied}>The backend restarted. Re-supply a key to resume; it remains in React memory only.</KeyForm>;
}

/** The view after one event. A stop by a check is added to the run's halts at once, so its notice needs no reload. */
function reduceEvent(state: RunEventState, event: RunEvent): RunEventState {
  const next = reduceRunEvent(state, event);
  const halt = event.type === "check.stopped" ? haltFromEvent(event.data) : null;
  return halt ? { ...next, view: withHalt(next.view, halt) } : next;
}

export function Run({ params }: RouteProps) {
  const runId = decodeURIComponent(params.runId);
  const queryClient = useQueryClient();
  const hostToken = useMemo(() => getHostToken(runId), [runId]);
  const queryKey = ["run", runId] as const;
  const healthKey = ["run-health", runId] as const;
  const query = useQuery({ queryKey, queryFn: () => api.run(runId), refetchInterval: 30_000 });
  const health = useQuery({ queryKey: healthKey, queryFn: ({ signal }) => api.health(runId, signal), refetchInterval: 30_000, retry: false });
  const [liveState, dispatch] = useReducer((state: RunEventState | null, action: RunEvent | RunView) => {
    if ("run" in action) return state ? clearRefetchSignal(state, action) : runEventState(action);
    return state ? reduceEvent(state, action) : state;
  }, null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [controlBusy, setControlBusy] = useState(false);
  const [controlMessage, setControlMessage] = useState("");

  useEffect(() => { if (query.data) dispatch(query.data); }, [query.data]);
  useEffect(() => {
    setConnection("connecting");
    const close = openEvents(runId, { onEvent: (event) => {
      setConnection("live"); dispatch(event);
      if (event.type === "quota.exhausted") void queryClient.invalidateQueries({ queryKey });
      // A stop, and any change of state (a resume closes the stop), is read again from the stored record.
      if (event.type === "check.stopped" || event.type === "run.state") { void queryClient.invalidateQueries({ queryKey }); void queryClient.invalidateQueries({ queryKey: healthKey }); }
    }, onResync: (event) => { setConnection("reconnecting"); dispatch(event); void queryClient.invalidateQueries({ queryKey }); }, onError: () => { setConnection("reconnecting"); void queryClient.invalidateQueries({ queryKey }); } });
    return () => { close(); setConnection("closed"); };
  }, [queryClient, runId]);
  useEffect(() => { if (liveState?.refetchRequired) void queryClient.invalidateQueries({ queryKey }); }, [liveState?.refetchRequired, queryClient]);

  async function control(action: RunControlRequest["action"]) {
    if (!hostToken) return;
    setControlBusy(true); setControlMessage(`${action} requested…`);
    try { const result = await api.controlRun(runId, { action }, hostToken); setControlMessage(`Run is ${result.state}.`); await query.refetch(); void health.refetch(); }
    catch (error) { setControlMessage(error instanceof Error ? error.message : "Control request failed."); }
    finally { setControlBusy(false); }
  }

  if (query.isLoading && !liveState) return <section className="run-loading" aria-live="polite"><span aria-hidden="true" /><p>Loading run instrument <code>{runId}</code>…</p></section>;
  if (query.isError && !liveState) return <section className="empty-state"><p className="eyebrow">Run unavailable</p><h1>Could not load this run.</h1><p>{query.error instanceof Error ? query.error.message : "The run endpoint is unavailable."}</p><Link className="text-link" href="/">Return to instrument →</Link></section>;
  const view = liveState?.view ?? query.data;
  if (!view) return null;
  const run = view.run;
  const readiness = view.readiness ?? run.readiness;
  const needsKeys = Boolean(run.needs_keys) || run.state === "needs_keys";
  // A key that is accepted changes the run record, and the health summary names the run's state.
  const refreshRecord = () => { void query.refetch(); void health.refetch(); };
  const flagLabels = new Map((health.data?.flag_kinds ?? []).map((legend) => [legend.kind as string, legend.label]));
  const flagLabel = (kind: string) => flagLabels.get(kind) ?? kind.replaceAll("_", " ");
  return <div className="run-page">
    <header className="run-header"><div><div className="page-heading"><p className="eyebrow"><Link href="/">Instrument</Link> / Run</p><h1>Live run record</h1></div><code className="run-id">{run.run_id}</code></div><div className="run-status" aria-live="polite"><span className={`run-status__lamp run-status__lamp--${run.state ?? "unknown"}`} aria-hidden="true" /><div><small>Run state</small><strong>{run.state ?? "unavailable"}</strong><span>{view.live ? "worker active" : "server record"} · {view.subscribers ?? 0} viewer{view.subscribers === 1 ? "" : "s"}</span></div></div></header>
    {run.prereg_stale ? <div className="run-alert run-alert--warning" role="alert"><strong>Pre-registration is stale</strong><span>The hash supplied at launch does not match the configuration frozen by the server. Treat comparisons accordingly.</span></div> : null}
    {run.state === "paused" && view.owner_quota_exhausted ? <div className="run-alert run-alert--warning" role="alert"><strong>Token quota used up</strong><span>The account that launched this run has no tokens left, so the run was paused. It can be resumed once an admin raises the quota.</span></div> : null}
    {run.state === "complete" && run.study_id ? <div className="run-alert run-alert--note" role="note"><strong>Keep your own copy</strong><span>This run is finished. <a className="text-link" href={downloadUrl(run.study_id, "bundle.tar.zst")}>Download its data</a> to your machine; the server is working storage, not an archive.</span></div> : null}
    {openHalts(run.halts).map((halt) => <StopNotice key={`${halt.rule}-${halt.at}`} runId={runId} halt={halt} state={run.state} needsKeys={needsKeys} hostToken={hostToken} onKeysSupplied={refreshRecord} />)}
    {needsKeys ? <NeedsKeys runId={runId} hostToken={hostToken} onSupplied={refreshRecord} /> : null}
    <TaintList taints={run.taints} />
    <ControlBar hostToken={hostToken} state={run.state} busy={controlBusy} message={controlMessage} onAction={(action) => void control(action)} />
    <div className="run-provenance-strip"><ProvenanceBadge sourceLabel={null} /><span>Source is shown only when an event supplies a verified <code>source_label</code>; this record does not guess from arm names.</span></div>
    <ReadinessPanel readiness={readiness} tier0Source={run.tier0_source} determinismTier={run.determinism_tier} />
    <ProgressGrid progress={run.progress} sourceByArm={liveState?.sourceByArm} />
    {health.data ? <OutputHealth summary={health.data} variant="run" /> : <OutputHealthUnavailable variant="run" loading={health.isPending} message={health.error instanceof Error ? health.error.message : undefined} />}
    <section className="run-panel event-viewer" aria-labelledby="events-title"><header className="run-section-heading"><div><p className="eyebrow">Server-sent events</p><h2 id="events-title">Recent activity</h2></div><span className={`connection-state connection-state--${connection}`} role="status">{connection}</span></header>{liveState?.events.length ? <ol>{[...liveState.events].reverse().map((event, index) => <li key={`${event.id ?? "local"}-${index}`}><code>{event.type}</code><span>{eventSummary(event, flagLabel)}</span></li>)}</ol> : <p className="run-unavailable">Waiting for the event stream. The durable run record remains authoritative.</p>}</section>
  </div>;
}
