import type { Halt, SessionControlRequest, SessionView } from "@qr-evals/contracts";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import { CheckStopLine } from "../components/run/CheckStopLine";
import { haltFromEvent, openHalts } from "../components/run/checks";
import { BreakScreen } from "../components/supervised/BreakScreen";
import { Briefing } from "../components/supervised/Briefing";
import { DisplaySettings } from "../components/supervised/DisplaySettings";
import { IntentionPanel } from "../components/supervised/IntentionPanel";
import { PresenceList, presenceFromEvent, type PresenceRow } from "../components/supervised/PresenceList";
import { Questionnaire } from "../components/supervised/Questionnaire";
import { RefocusScreen } from "../components/supervised/RefocusScreen";
import { SessionControls } from "../components/supervised/SessionControls";
import { applyStreamEvent, StreamPane, type StreamRecord } from "../components/supervised/StreamPane";
import { TiledStreams } from "../components/supervised/TiledStreams";
import { Timer } from "../components/supervised/Timer";
import { api, openEvents, type RunEvent } from "../lib/api";
import { loadConfig } from "../lib/config-store";
import { getHostToken } from "../lib/host-token";
import { getObserverIdentity, saveObserverName } from "../lib/observer-id";
import { loadDisplaySettings, saveDisplaySettings, type DisplayMode } from "../lib/supervised-settings";

type RouteProps = { params: { runId: string } };
type Stage = "briefing" | "intention" | "console";
const sessionKey = (runId: string) => `qr-evals.live-session.v1.${runId}`;

function eventObject(event: RunEvent): Record<string, unknown> { return typeof event.data === "object" && event.data !== null ? event.data as Record<string, unknown> : {}; }
function savedSession(runId: string): SessionView | null { try { const raw = localStorage.getItem(sessionKey(runId)); return raw ? JSON.parse(raw) as SessionView : null; } catch { return null; } }

export function Live({ params }: RouteProps) {
  const runId = decodeURIComponent(params.runId);
  const hostToken = useMemo(() => getHostToken(runId), [runId]);
  const identity = useMemo(() => getObserverIdentity(), []);
  const config = useMemo(() => loadConfig().config, []);
  const [stage, setStage] = useState<Stage>(() => savedSession(runId) ? "console" : "briefing");
  const [session, setSession] = useState<SessionView | null>(() => savedSession(runId));
  const [mode, setMode] = useState<DisplayMode>(() => savedSession(runId)?.session.display ?? config.supervised?.display ?? "single");
  const [settings, setSettings] = useState(() => loadDisplaySettings(mode));
  const [sessionState, setSessionState] = useState(() => savedSession(runId)?.state ?? "ready");
  const [remaining, setRemaining] = useState(0);
  const [timerKind, setTimerKind] = useState("session");
  const [streams, setStreams] = useState<StreamRecord[]>([]);
  const [presence, setPresence] = useState<PresenceRow[]>([]);
  const [preSubmitted, setPreSubmitted] = useState(false);
  const [postSubmitted, setPostSubmitted] = useState(false);
  const [resumeRequired, setResumeRequired] = useState(() => Boolean(savedSession(runId)?.session.started_at));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const runQuery = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId), enabled: Boolean(hostToken) });
  // A stop by a check that arrived as an event; `null` once the run is running again. Until either, the run record decides.
  const [eventStop, setEventStop] = useState<Halt | null | undefined>(undefined);
  const checkStop = eventStop === undefined ? openHalts(runQuery.data?.run.halts).at(-1) : eventStop ?? undefined;
  // The run's state: the last `run.state` event, and before one arrives the run record's.
  const [eventRunState, setEventRunState] = useState<string | undefined>(undefined);
  const runState = eventRunState ?? runQuery.data?.run.state ?? undefined;
  const runNeedsKeys = eventRunState === undefined ? Boolean(runQuery.data?.run.needs_keys) : eventRunState === "needs_keys";

  useEffect(() => { setSettings(loadDisplaySettings(mode)); }, [mode]);
  useEffect(() => {
    if (!session) return;
    const close = openEvents(runId, { hostToken: hostToken!, authTransport: "query", onEvent: (event) => {
      const data = eventObject(event);
      if ((event.type === "session.state" || event.type === "session.timer" || event.type === "observer.presence") && data.session_id !== session.session.session_id) return;
      if (event.type === "session.state" && typeof data.state === "string") { setSessionState(data.state); if (data.state === "refocus") setResumeRequired(true); }
      if (event.type === "session.timer" && typeof data.remaining_s === "number") { setRemaining(data.remaining_s); setTimerKind(typeof data.kind === "string" ? data.kind : "session"); }
      if (event.type === "observer.presence") { const present = presenceFromEvent(event.data); setPresence((prior) => present.map((item) => ({ ...item, pre: item.observer_id === identity.observerId ? preSubmitted : prior.find((old) => old.observer_id === item.observer_id)?.pre, post: item.observer_id === identity.observerId ? postSubmitted : prior.find((old) => old.observer_id === item.observer_id)?.post }))); }
      if (event.type.startsWith("sample.")) setStreams((value) => applyStreamEvent(value, event.type, data));
      if (event.type === "check.stopped") { const halt = haltFromEvent(event.data); if (halt) setEventStop(halt); }
      // Only a host resume moves a stopped run back to running, and the run that starts then closes the stop.
      if (event.type === "run.state" && typeof data.state === "string") setEventRunState(data.state);
      if (event.type === "run.state" && data.state === "running") setEventStop(null);
    }, onResync: () => { setMessage("The event stream skipped history. The run record is being refreshed."); void runQuery.refetch(); }, onError: () => setMessage("Live connection interrupted; reconnecting automatically.") });
    return close;
  }, [hostToken, identity.observerId, postSubmitted, preSubmitted, runId, session]);

  useEffect(() => {
    if (!session) return;
    const beat = () => { void api.presence(session.session.session_id, {}, identity.observerId).catch(() => undefined); };
    beat(); const id = window.setInterval(beat, 15_000); return () => window.clearInterval(id);
  }, [identity.observerId, session]);

  async function create(intention: string, displayName: string) {
    if (!hostToken) return; setBusy(true); setMessage(""); saveObserverName(displayName);
    try { const created = await api.createSession(runId, { display: mode, intention, host_observer_id: identity.observerId, host_display_name: displayName }, hostToken); const hostSafe = { ...created, session: { ...created.session, target: null } }; setSession(hostSafe); setSessionState(created.state); localStorage.setItem(sessionKey(runId), JSON.stringify(hostSafe)); setStage("console"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Session creation failed."); } finally { setBusy(false); }
  }
  async function answer(phase: "pre" | "post", answers: Record<string, number | string>) {
    if (!session) return; setBusy(true); setMessage("");
    try { await api.questionnaire(session.session.session_id, { phase, answers }, identity.observerId); if (phase === "pre") setPreSubmitted(true); else setPostSubmitted(true); }
    catch (error) { setMessage(error instanceof Error ? error.message : "The response was not accepted."); } finally { setBusy(false); }
  }
  async function control(action: SessionControlRequest["action"]) {
    if (!session || !hostToken) return; setBusy(true); setMessage(`${action.replaceAll("_", " ")} requested…`);
    try { const result = await api.controlSession(session.session.session_id, { action }, hostToken); setSessionState(result.state); setMessage(`Server state: ${result.state}.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Control request failed."); } finally { setBusy(false); }
  }
  async function markLeft(observerId: string) { if (!session) return; try { await api.presence(session.session.session_id, { left: true }, observerId); setPresence((items) => items.map((item) => item.observer_id === observerId ? { ...item, left: true } : item)); } catch (error) { setMessage(error instanceof Error ? error.message : "Could not mark observer left."); } }
  function updateSettings(next: typeof settings) { setSettings(next); saveDisplaySettings(mode, next); }

  if (!hostToken) return <section className="empty-state"><p className="eyebrow">Host token missing</p><h1>This console is read-only here.</h1><p>Open it in the browser that launched the run. Host tokens stay in session storage and are never persisted with the run.</p><Link className="text-link" href={`/runs/${encodeURIComponent(runId)}`}>View the run record →</Link></section>;
  if (runQuery.isError) return <section className="empty-state"><p className="eyebrow">Run unavailable</p><h1>The live instrument could not load.</h1><p>{runQuery.error instanceof Error ? runQuery.error.message : "Unknown error"}</p></section>;
  if (stage === "briefing") return <Briefing onConsent={() => setStage("intention")} />;
  if (stage === "intention") return <><IntentionPanel initialMinutes={config.supervised?.intention_timer_min ?? 3} displayName={identity.displayName || "Host"} busy={busy} onCreate={(text, name) => void create(text, name)} />{message ? <p className="supervised-message" role="alert">{message}</p> : null}</>;
  if (!session) return null;
  const intention = session.session.intention?.text ?? "Intention held by the host";
  const joinUrl = `${window.location.origin}/s/${session.join_code}?session=${encodeURIComponent(session.session.session_id)}`;
  const visibleStreams = [...streams].slice(-Math.max(1, settings.streams));
  return <div className="live-console"><header className="live-header"><div><div className="page-heading"><p className="eyebrow"><Link href={`/runs/${encodeURIComponent(runId)}`}>Run record</Link> / supervised</p><h1>Live observatory</h1></div><code>{session.session.session_id}</code></div><Timer anchor label={timerKind} remaining={remaining} /></header>
    <section className="join-strip" aria-label="Observer invitation"><div><small>Observer code</small><strong>{session.join_code}</strong></div><div><small>Share link</small><code>{joinUrl}</code></div><button className="button button--secondary" type="button" onClick={() => void navigator.clipboard.writeText(joinUrl)}>Copy link</button></section>
    <SessionControls state={sessionState} busy={busy} preComplete={preSubmitted} readyAction={resumeRequired ? "resume" : "start"} onAction={(action) => void control(action)} />
    {sessionState === "boundary_pending" ? <div className="boundary-notice" role="status"><strong>Boundary pending</strong><span>No new generations are being dispatched. In-flight generations are finishing before the break.</span></div> : null}
    {checkStop ? <CheckStopLine runId={runId} halt={checkStop} state={runState} needsKeys={runNeedsKeys} /> : null}
    {message ? <p className="supervised-message" role="status">{message}</p> : null}
    <div className="console-grid"><main>{!preSubmitted ? <Questionnaire phase="pre" busy={busy} onSubmit={(answers) => answer("pre", answers)} /> : null}{sessionState === "break" ? <BreakScreen remaining={remaining} /> : sessionState === "refocus" ? <RefocusScreen remaining={remaining} intention={intention} /> : mode === "tiled" ? <TiledStreams streams={visibleStreams} count={settings.streams} showThinking={settings.show_thinking} textSize={settings.text_size} /> : <StreamPane stream={visibleStreams.at(-1)} showThinking={settings.show_thinking} textSize={settings.text_size} />}{sessionState === "break" && !postSubmitted ? <Questionnaire phase="post" busy={busy} onSubmit={(answers) => answer("post", answers)} /> : null}</main><aside><DisplaySettings mode={mode} settings={settings} onMode={setMode} onChange={updateSettings} /><PresenceList observers={presence} onMarkLeft={(id) => void markLeft(id)} /><section className="host-blind"><p className="eyebrow">Blinding intact</p><strong>Target hidden from host</strong><p>Target themes and decoys are observer-only and are never inserted into prompts.</p></section></aside></div>
  </div>;
}
