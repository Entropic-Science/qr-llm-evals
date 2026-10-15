import type { RunControlRequest } from "@qr-evals/contracts";

type Action = RunControlRequest["action"];

export function ControlBar({ hostToken, state, busy, message, onAction }: { hostToken: string | undefined; state: string | undefined; busy?: boolean; message?: string; onAction: (action: Action) => void }) {
  if (!hostToken) return null;
  const actions: Action[] = state === "created" || state === "ready" ? ["start", "abort"] : state === "running" ? ["pause", "abort"] : state === "paused" ? ["resume", "abort"] : [];
  return <section className="control-bar" aria-label="Host run controls"><div><p className="eyebrow">Host authority</p><strong>Manual control only</strong><span>Timers and events never start this run.</span></div><div className="control-bar__actions">{actions.map((action) => <button key={action} className={action === "abort" ? "button button--danger" : "button button--primary"} type="button" disabled={busy} onClick={() => onAction(action)}>{action[0]?.toUpperCase()}{action.slice(1)}</button>)}</div>{message ? <p role="status" className="control-bar__message">{message}</p> : null}</section>;
}
