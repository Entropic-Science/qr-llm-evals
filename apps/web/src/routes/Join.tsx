import type { SessionJoinResponse } from "@qr-evals/contracts";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Briefing } from "../components/supervised/Briefing";
import { BreakScreen } from "../components/supervised/BreakScreen";
import { DisplaySettings } from "../components/supervised/DisplaySettings";
import { Questionnaire } from "../components/supervised/Questionnaire";
import { RefocusScreen } from "../components/supervised/RefocusScreen";
import { applyStreamEvent, StreamPane, type StreamRecord } from "../components/supervised/StreamPane";
import { TiledStreams } from "../components/supervised/TiledStreams";
import { Timer } from "../components/supervised/Timer";
import { API_BASE, api, openEvents } from "../lib/api";
import { getObserverIdentity, saveObserverName } from "../lib/observer-id";
import { loadDisplaySettings, saveDisplaySettings, type DisplayMode } from "../lib/supervised-settings";

type RouteProps = { params: { joinCode: string } };
type Stage = "briefing" | "identity" | "pre" | "viewer" | "post";
function object(data: unknown): Record<string, unknown> { return typeof data === "object" && data !== null ? data as Record<string, unknown> : {}; }

export function Join({ params }: RouteProps) {
  const joinCode = decodeURIComponent(params.joinCode).toUpperCase();
  const sessionId = new URLSearchParams(window.location.search).get("session") ?? "";
  const identity = useMemo(() => getObserverIdentity(), []);
  const [stage, setStage] = useState<Stage>("briefing");
  const [name, setName] = useState(identity.displayName);
  const [joined, setJoined] = useState<SessionJoinResponse | null>(null);
  const [runId, setRunId] = useState("");
  const [sessionState, setSessionState] = useState("ready");
  const [remaining, setRemaining] = useState(0);
  const [timerKind, setTimerKind] = useState("session");
  const [streams, setStreams] = useState<StreamRecord[]>([]);
  const [mode, setMode] = useState<DisplayMode>("single");
  const [settings, setSettings] = useState(() => loadDisplaySettings("single"));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => { setSettings(loadDisplaySettings(mode)); }, [mode]);
  useEffect(() => {
    if (!joined || !runId) return;
    const close = openEvents(runId, { onEvent: (event) => { const data = object(event.data); if ((event.type === "session.state" || event.type === "session.timer") && data.session_id !== joined.session_id) return; if (event.type === "session.state" && typeof data.state === "string") { setSessionState(data.state); if (data.state === "break") setStage("post"); } if (event.type === "session.timer" && typeof data.remaining_s === "number") { setRemaining(data.remaining_s); setTimerKind(typeof data.kind === "string" ? data.kind : "session"); } if (event.type.startsWith("sample.")) setStreams((value) => applyStreamEvent(value, event.type, data)); }, onResync: () => setMessage("Some live events were skipped. New output will continue here."), onError: () => setMessage("Live connection interrupted; reconnecting automatically.") });
    return close;
  }, [joined, runId]);
  useEffect(() => {
    if (!joined) return;
    const beat = () => void api.presence(joined.session_id, {}, identity.observerId).catch(() => undefined);
    beat(); const interval = window.setInterval(beat, 15_000);
    const leave = () => { void fetch(`${API_BASE}/api/sessions/${encodeURIComponent(joined.session_id)}/presence`, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-Observer-Id": identity.observerId }, body: JSON.stringify({ left: true }) }); };
    window.addEventListener("pagehide", leave); return () => { window.clearInterval(interval); window.removeEventListener("pagehide", leave); };
  }, [identity.observerId, joined]);

  async function join(event: FormEvent) { event.preventDefault(); if (!sessionId || !name.trim()) return; setBusy(true); setMessage(""); saveObserverName(name); try { const result = await api.joinSession(sessionId, { join_code: joinCode, display_name: name.trim(), observer_id: identity.observerId, consent: true }); setJoined(result); setRunId(result.observer.run_id); setStage(result.pre_required === false ? "viewer" : "pre"); } catch (error) { setMessage(error instanceof Error ? error.message : "Could not join the session."); } finally { setBusy(false); } }
  async function answer(phase: "pre" | "post", answers: Record<string, number | string>) { if (!joined) return; setBusy(true); setMessage(""); try { await api.questionnaire(joined.session_id, { phase, answers }, identity.observerId); setStage(phase === "pre" ? "viewer" : "viewer"); } catch (error) { setMessage(error instanceof Error ? error.message : "Response was not accepted."); } finally { setBusy(false); } }
  function updateSettings(next: typeof settings) { setSettings(next); saveDisplaySettings(mode, next); }

  if (!sessionId) return <section className="empty-state"><p className="eyebrow">Incomplete invitation</p><h1>This observer link is missing its session.</h1><p>Ask the host to copy the full share link from the live console. A join code alone cannot be resolved by the current server contract.</p></section>;
  if (stage === "briefing") return <Briefing join onConsent={() => setStage("identity")} />;
  if (stage === "identity") return <form className="join-identity notebook-page" onSubmit={(event) => void join(event)}><div className="page-heading"><p className="eyebrow">Observer {joinCode}</p><h1>Join pseudonymously.</h1></div><label>Display name<input value={name} maxLength={64} autoFocus onChange={(event) => setName(event.target.value)} autoComplete="nickname" /></label><p>Your stable pseudonymous observer ID is <code>{identity.observerId}</code>.</p><button className="button button--primary" type="submit" disabled={busy || !name.trim()}>{busy ? "Joining…" : "I consent and join"}</button>{message ? <p role="alert" className="supervised-message">{message}</p> : null}</form>;
  if (stage === "pre") return <div className="observer-question"><div className="page-heading"><p className="eyebrow">Required before viewing</p><h1>Record your starting point.</h1></div><Questionnaire phase="pre" busy={busy} onSubmit={(answers) => answer("pre", answers)} />{message ? <p role="alert" className="supervised-message">{message}</p> : null}</div>;
  const visibleStreams = [...streams].slice(-Math.max(1, settings.streams));
  return <div className="observer-view"><header className="observer-header"><div><div className="page-heading"><p className="eyebrow">Observer readout · {joinCode}</p><h1>Shared live view</h1></div></div><Timer anchor label={timerKind} remaining={remaining} /></header>
    <section className="target-readout" aria-labelledby="target-title"><p className="eyebrow">Observer-only target</p><h2 id="target-title">Target not exposed by the current observer API.</h2><p>The interface will not invent or infer a target. It is never placed in a model prompt.</p></section>
    {message ? <p role="status" className="supervised-message">{message}</p> : null}
    {stage === "post" ? <Questionnaire phase="post" busy={busy} onSubmit={(answers) => answer("post", answers)} /> : sessionState === "break" ? <BreakScreen remaining={remaining} /> : sessionState === "refocus" ? <RefocusScreen remaining={remaining} intention="Return to the intention you held at the start." /> : mode === "tiled" ? <TiledStreams streams={visibleStreams} count={settings.streams} showThinking={settings.show_thinking} textSize={settings.text_size} /> : <StreamPane stream={visibleStreams.at(-1)} showThinking={settings.show_thinking} textSize={settings.text_size} />}
    <DisplaySettings mode={mode} settings={settings} onMode={setMode} onChange={updateSettings} />
  </div>;
}
