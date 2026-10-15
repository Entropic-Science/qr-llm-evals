import { useState, type FormEvent } from "react";

export type QuestionnairePhase = "pre" | "post";
type Item = { id: string; prompt: string; kind: "scale" | "text"; min?: number; max?: number; required: boolean };
export const QUESTIONNAIRES: Record<QuestionnairePhase, Item[]> = {
  pre: [
    { id: "influence_belief", prompt: "How possible does it feel that sustained intention could influence a quantum-random system during this short session?", kind: "scale", min: 1, max: 7, required: true },
    { id: "calmness", prompt: "How calm do you feel right now?", kind: "scale", min: 1, max: 5, required: true },
    { id: "focus", prompt: "How focused do you feel right now?", kind: "scale", min: 1, max: 5, required: true },
  ],
  post: [
    { id: "meaningful_coincidence", prompt: "During this session, how strongly did you notice a coincidence that felt personally meaningful?", kind: "scale", min: 1, max: 7, required: true },
    { id: "unusual_relevance", prompt: "How unusually relevant did the output feel to the intention?", kind: "scale", min: 1, max: 7, required: true },
    { id: "surprise", prompt: "How surprising did the output feel?", kind: "scale", min: 1, max: 7, required: true },
    { id: "anything_meaningful", prompt: "Did anything strike you as meaningful? You may leave this blank.", kind: "text", required: false },
  ],
};

export function Questionnaire({ phase, onSubmit, busy = false, submitted = false }: { phase: QuestionnairePhase; onSubmit: (answers: Record<string, number | string>) => Promise<void> | void; busy?: boolean; submitted?: boolean }) {
  const [answers, setAnswers] = useState<Record<string, number | string>>({});
  const items = QUESTIONNAIRES[phase];
  const complete = items.every((item) => !item.required || answers[item.id] !== undefined && answers[item.id] !== "");
  async function submit(event: FormEvent) { event.preventDefault(); if (complete) await onSubmit(answers); }
  if (submitted) return <section className="questionnaire questionnaire--complete" role="status"><p className="eyebrow">{phase} questionnaire</p><h2>Response recorded.</h2><p>The server accepted this pseudonymous response.</p></section>;
  return <form className="questionnaire" onSubmit={(event) => void submit(event)}><fieldset disabled={busy}><legend>{phase === "pre" ? "Before the session" : "After the session"}</legend><p>All numbered items are required. There are no correct answers.</p>{items.map((item) => <div className="questionnaire__item" key={item.id}><label htmlFor={`${phase}-${item.id}`}>{item.prompt}{item.required ? <span> required</span> : null}</label>{item.kind === "scale" ? <div className="scale-options">{Array.from({ length: (item.max ?? 1) - (item.min ?? 1) + 1 }, (_, index) => (item.min ?? 1) + index).map((value) => <label key={value}><input type="radio" name={item.id} value={value} checked={answers[item.id] === value} onChange={() => setAnswers({ ...answers, [item.id]: value })} /><span>{value}</span></label>)}</div> : <textarea id={`${phase}-${item.id}`} rows={3} value={String(answers[item.id] ?? "")} onChange={(event) => setAnswers({ ...answers, [item.id]: event.target.value })} />}</div>)}<button className="button button--primary" type="submit" disabled={!complete || busy}>{busy ? "Submitting…" : `Submit ${phase}-session responses`}</button></fieldset></form>;
}
