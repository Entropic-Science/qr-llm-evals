import type { RunKeysRequest } from "@qr-evals/contracts";
import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../../lib/api";

/**
 * The form through which a run's host hands the server a provider key.
 *
 * It is the same form in both places it appears: on a run that needs its keys again after the server
 * restarted, and on a run a check stopped because the judge refused its requests. The caller says which.
 */
export function KeyForm({ runId, hostToken, title, children, onSupplied }: {
  runId: string;
  hostToken: string;
  title: string;
  /** Why the key is asked for, in a sentence or two. */
  children: ReactNode;
  onSupplied: () => void;
}) {
  const [provider, setProvider] = useState("anthropic");
  const [key, setKey] = useState("");
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = key;
    if (!value.trim()) return;
    setMessage("Supplying key…");
    try {
      const body: RunKeysRequest = { provider_keys: { [provider.trim().toLowerCase()]: value } };
      await api.supplyKeys(runId, body, hostToken);
      setKey("");
      setMessage("Key accepted and cleared from this form.");
      onSupplied();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Key supply failed."); }
  }
  return <form className="run-alert run-key-form" onSubmit={(event) => void submit(event)}>
    <div><strong>{title}</strong><span>{children}</span></div>
    <label>Provider slot<input value={provider} onChange={(event) => setProvider(event.target.value)} autoComplete="off" /></label>
    <label>Provider key<input type="password" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" /></label>
    <button className="button button--primary" type="submit">Supply key</button>
    {message ? <p role="status">{message}</p> : null}
  </form>;
}
