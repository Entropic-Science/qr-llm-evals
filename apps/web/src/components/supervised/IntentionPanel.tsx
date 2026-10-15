import { useEffect, useState } from "react";
import { Timer } from "./Timer";

export function IntentionPanel({ initialMinutes = 3, displayName, onCreate, busy }: { initialMinutes?: number; displayName: string; onCreate: (intention: string, displayName: string) => void; busy?: boolean }) {
  const [text, setText] = useState("");
  const [name, setName] = useState(displayName);
  const [remaining, setRemaining] = useState(initialMinutes * 60);
  useEffect(() => { const id = window.setInterval(() => setRemaining((value) => value - 1), 1000); return () => window.clearInterval(id); }, []);
  return <section className="intention-panel notebook-page" aria-labelledby="intention-title"><header><div><p className="eyebrow">Preparation</p><h1 id="intention-title">Set an intention.</h1></div><Timer label="Advisory pause" remaining={remaining} anchor /></header>
    <p>The clock may pass zero. It is only a guide and will never initiate generation.</p>
    <label>Host display name<input value={name} maxLength={64} onChange={(event) => setName(event.target.value)} autoComplete="nickname" /></label>
    <label>Your intention<textarea value={text} maxLength={10000} rows={5} onChange={(event) => setText(event.target.value)} placeholder="Write a clear, personal semantic direction…" /></label>
    <button className="button button--primary" type="button" disabled={busy || !text.trim() || !name.trim()} onClick={() => onCreate(text.trim(), name.trim())}>{busy ? "Creating session…" : "Create supervised session"}</button>
  </section>;
}
