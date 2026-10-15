import type { ObserverPresence } from "@qr-evals/contracts";

export interface PresenceRow extends ObserverPresence { pre?: boolean | undefined; post?: boolean | undefined; left?: boolean | undefined }
export function presenceFromEvent(data: unknown): ObserverPresence[] {
  if (typeof data !== "object" || data === null || !Array.isArray((data as { present?: unknown }).present)) return [];
  return (data as { present: unknown[] }).present.filter((item): item is ObserverPresence => typeof item === "object" && item !== null && typeof (item as ObserverPresence).observer_id === "string" && typeof (item as ObserverPresence).name === "string");
}

export function PresenceList({ observers, onMarkLeft }: { observers: PresenceRow[]; onMarkLeft?: (observerId: string) => void }) {
  return <section className="presence-list" aria-labelledby="presence-title"><header><div><p className="eyebrow">Group</p><h2 id="presence-title">Observers</h2></div><strong>{observers.filter((item) => !item.left).length} present</strong></header>{observers.length ? <ul>{observers.map((observer) => <li key={observer.observer_id}><span className={observer.left ? "presence-dot is-left" : "presence-dot"} aria-hidden="true" /><div><strong>{observer.name}</strong><code>{observer.observer_id}</code></div><span className={observer.pre ? "is-done" : "is-missing"}>pre {observer.pre ? "received" : "missing"}</span><span className={observer.post ? "is-done" : "is-missing"}>post {observer.post ? "received" : "missing"}</span>{onMarkLeft && !observer.left ? <button type="button" onClick={() => onMarkLeft(observer.observer_id)}>Mark left</button> : null}</li>)}</ul> : <p className="stream-empty">No presence event has arrived yet.</p>}</section>;
}
