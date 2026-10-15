export interface StreamRecord { sampleRef: string; prompt: string; text: string; thinking: string; finished: boolean; armId?: string | undefined; cardId?: string | undefined }

export function applyStreamEvent(streams: StreamRecord[], type: string, data: Record<string, unknown>): StreamRecord[] {
  const ref = typeof data.sample_ref === "string" ? data.sample_ref : undefined;
  if (!ref) return streams;
  if (type === "sample.started") return [...streams.filter((item) => item.sampleRef !== ref), { sampleRef: ref, prompt: typeof data.prompt_preview === "string" ? data.prompt_preview : "", text: "", thinking: "", finished: false, armId: typeof data.arm_id === "string" ? data.arm_id : undefined, cardId: typeof data.card_id === "string" ? data.card_id : undefined }];
  const index = streams.findIndex((item) => item.sampleRef === ref); if (index < 0) return streams;
  const next = [...streams]; const current = next[index]; if (!current) return streams;
  if (type === "sample.delta") next[index] = { ...current, text: current.text + (typeof data.text === "string" ? data.text : ""), thinking: current.thinking + (typeof data.thinking === "string" ? data.thinking : "") };
  if (type === "sample.finished") next[index] = { ...current, finished: true };
  return next;
}

export function StreamPane({ stream, showThinking, textSize = "md", index }: { stream?: StreamRecord | undefined; showThinking: boolean; textSize?: "sm" | "md" | "lg"; index?: number }) {
  return <article className={`stream-pane stream-pane--${textSize}`} aria-label={`Generation stream${index === undefined ? "" : ` ${index + 1}`}`}>
    <header><span>Specimen {index === undefined ? "01" : String(index + 1).padStart(2, "0")}</span><span className={stream?.finished ? "is-finished" : "is-live"}>{stream ? (stream.finished ? "complete" : "receiving") : "awaiting sample"}</span></header>
    {stream ? <><section><h3>Prompt preview</h3><p className="stream-prompt">{stream.prompt || "Prompt not supplied by the event."}</p></section>{showThinking && stream.thinking ? <details open><summary>Thinking</summary><pre>{stream.thinking}</pre></details> : null}<section className="stream-output"><h3>Output</h3><p>{stream.text || <span className="stream-cursor">Waiting for first token</span>}</p></section></> : <p className="stream-empty">The readout will open when a sample starts.</p>}
  </article>;
}
