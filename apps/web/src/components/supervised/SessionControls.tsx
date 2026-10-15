export function SessionControls({ state, busy, preComplete, readyAction = "start", onAction }: { state: string; busy?: boolean; preComplete: boolean; readyAction?: "start" | "resume"; onAction: (action: "start" | "resume" | "stop_at_boundary" | "end_break") => void }) {
  const ready = state === "ready";
  return <div className="session-controls" aria-label="Host session controls"><div><p className="eyebrow">Manual control</p><strong>{state.replaceAll("_", " ")}</strong><small>{!preComplete ? "Required pre-session responses are missing." : "No timer can activate these controls."}</small></div><div>
    {ready ? <button className="button button--primary" type="button" disabled={busy || !preComplete} onClick={() => onAction(readyAction)}>{readyAction === "resume" ? "Resume" : "Start"}</button> : null}
    {state === "running" ? <button className="button button--secondary" type="button" disabled={busy} onClick={() => onAction("stop_at_boundary")}>Stop at boundary</button> : null}
    {state === "break" ? <button className="button button--primary" type="button" disabled={busy} onClick={() => onAction("end_break")}>End break</button> : null}
    {state === "refocus" ? <button className="button button--primary" type="button" disabled title="Resume becomes available after refocus reaches zero">Resume requires click</button> : null}
  </div></div>;
}
