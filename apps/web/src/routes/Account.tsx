import type { BillingView, QuotaView, StorageResponse, UsageResponse } from "@qr-evals/contracts";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation } from "wouter";
import { formatBytes, formatTokens, formatUsd, UsageTotalsTable } from "../components/UsageTotalsTable";
import { downloadUrl } from "../lib/results";
import { api, ApiError } from "../lib/api";
import { clearSession, updateSessionUser, useSession, type Session } from "../lib/session";

const SOURCE_LABELS: Record<string, string> = {
  quantum: "Quantum (QRNG)",
  seeded_prng: "Seeded PRNG",
  system_prng: "System PRNG",
  seeded_prng_biased: "Seeded PRNG, biased",
  provider_prng: "External provider",
  unlabelled: "Unlabelled",
};
const KIND_LABELS: Record<string, string> = { generation: "Generation", judge: "Judge", narrative: "Narrative" };

export function QuotaMeter({ quota }: { quota: QuotaView }) {
  if (quota.limit === 0) return <div className="quota-meter"><p className="quota-meter__figure"><strong>{formatTokens(quota.used)}</strong> tokens used</p><p className="quota-meter__note">This account has no token limit.</p></div>;
  const share = Math.min(1, quota.used / quota.limit);
  return <div className={quota.exhausted ? "quota-meter is-exhausted" : "quota-meter"}>
    <p className="quota-meter__figure"><strong>{formatTokens(quota.used)}</strong> of {formatTokens(quota.limit)} tokens used</p>
    <div className="quota-meter__track" role="progressbar" aria-label="Token quota used" aria-valuemin={0} aria-valuemax={quota.limit} aria-valuenow={Math.min(quota.used, quota.limit)}><span style={{ width: `${share * 100}%` }} /></div>
    <p className="quota-meter__note">{quota.exhausted
      ? "The quota is used up. New runs are refused and running ones are paused until an admin raises it."
      : `${formatTokens(quota.remaining ?? 0)} left. Only tokens on the qr-server engine count; calls on your own provider key, such as the judge, do not.`}</p>
  </div>;
}

export function Outstanding({ billing }: { billing: BillingView }) {
  const { prices } = billing;
  const credit = billing.outstanding_microusd < 0;
  return <div className="outstanding">
    <p className="outstanding__figure"><strong>{formatUsd(Math.abs(billing.outstanding_microusd))}</strong> {credit ? "in credit" : "outstanding"}</p>
    <p className="quota-meter__note">Per million tokens: QRNG ${prices.qrng_input} in and ${prices.qrng_output} out, PRNG ${prices.prng_input} in and ${prices.prng_output} out. Input is the prompt sent to the model, output is what it generated. Nothing is charged automatically; an admin marks the balance as paid once you have paid.</p>
    {billing.settlements.length > 0 ? <ul className="outstanding__history" aria-label="Payments recorded">{billing.settlements.map((item) => <li key={item.settled_at}>{new Date(item.settled_at).toLocaleDateString()}: {formatUsd(item.amount_microusd)} paid{item.note ? ` (${item.note})` : ""}</li>)}</ul> : null}
  </div>;
}

const FINISHED = new Set(["complete", "aborted"]);

function StoragePanel({ storage }: { storage: StorageResponse }) {
  const finished = storage.runs.filter((run) => FINISHED.has(run.state)).length;
  return <section className="account-panel" aria-labelledby="account-storage">
    <h2 id="account-storage">Storage</h2>
    {storage.runs.length === 0 ? <p className="usage-empty">No run data is stored for this account.</p> : <>
      <p className="outstanding__figure"><strong>{formatBytes(storage.total_bytes)}</strong> stored across {storage.runs.length} {storage.runs.length === 1 ? "run" : "runs"}</p>
      <p className="quota-meter__note">{finished > 0 ? "Download your finished runs and keep your own copy. " : ""}The server is working storage, not an archive: its backups are a safety net, and your own copy is the one you control.</p>
      <div className="table-frame usage-table"><table>
        <caption className="sr-only">Disk space used by each of your runs</caption>
        <thead><tr><th scope="col">Run</th><th scope="col">State</th><th scope="col">Started</th><th scope="col" className="num">Size</th><th scope="col">Your copy</th></tr></thead>
        <tbody>{storage.runs.map((run) => <tr key={run.run_id}>
          <th scope="row"><Link className="text-link" href={`/runs/${encodeURIComponent(run.run_id)}`}>{run.run_id}</Link></th>
          <td>{run.state}</td>
          <td>{new Date(run.created_at).toLocaleDateString()}</td>
          <td className="num">{formatBytes(run.size_bytes)}</td>
          <td><a className="text-link" href={downloadUrl(run.study_id, "bundle.tar.zst")} aria-label={`Download the data of ${run.run_id}`}>Download</a></td>
        </tr>)}</tbody>
      </table></div>
    </>}
  </section>;
}

function LinkedKey({ session }: { session: Session }) {
  const [apiKey, setApiKey] = useState("");
  const [state, setState] = useState<{ kind: "idle" | "busy" | "ok" | "error"; message: string }>({ kind: "idle", message: "" });
  const { user } = session;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setState({ kind: "busy", message: "" });
    try {
      const updated = await api.linkKey({ api_key: apiKey }, session.token);
      setApiKey("");
      updateSessionUser(updated);
      setState({ kind: "ok", message: "Key linked." });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { clearSession(); return; }
      setState({ kind: "error", message: cause instanceof ApiError ? cause.message : "The request did not reach the server. Try again." });
    }
  }
  return <section className="account-panel" aria-labelledby="account-key">
    <h2 id="account-key">Linked key</h2>
    <dl className="review-ledger"><div><dt>Key name</dt><dd>{user.key_name ?? "Not named"}</dd></div><div><dt>Fingerprint</dt><dd><code>{user.key_fingerprint}</code></dd></div><div><dt>Linked</dt><dd>{new Date(user.key_linked_at).toLocaleDateString()}</dd></div></dl>
    <form className="field-stack auth-form" onSubmit={(event) => void submit(event)}>
      <label>Link a different api.entropic.science key<input type="password" autoComplete="off" required placeholder="qrk_…" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
      {state.message ? <p className={state.kind === "error" ? "prereg-error" : "privacy-note"} role="status">{state.message}</p> : null}
      <div className="auth-form__actions"><button className="button button--secondary" type="submit" disabled={state.kind === "busy" || !apiKey}>Link key</button></div>
    </form>
  </section>;
}

function Usage({ usage }: { usage: UsageResponse }) {
  return <>
    <section className="account-panel" aria-labelledby="account-balance"><h2 id="account-balance">Balance</h2><Outstanding billing={usage.billing} /></section>
    <section className="account-panel" aria-labelledby="account-quota"><h2 id="account-quota">Token quota</h2><QuotaMeter quota={usage.quota} /></section>
    <section className="account-panel" aria-labelledby="account-usage">
      <h2 id="account-usage">Token usage</h2>
      <h3>By kind of call</h3>
      <UsageTotalsTable caption="Token usage by kind of call" keyLabel="Kind" buckets={usage.by_kind} empty="No runs have used tokens yet." labelFor={(key) => KIND_LABELS[key] ?? key} />
      <h3>Generations by randomness source</h3>
      <UsageTotalsTable caption="Generation tokens by randomness source" keyLabel="Source" buckets={usage.by_source} empty="No generations yet." labelFor={(key) => SOURCE_LABELS[key] ?? key} />
      <h3>By day (UTC)</h3>
      <UsageTotalsTable caption="Token usage by day" keyLabel="Day" buckets={usage.by_day} empty="No usage in the last 30 days." />
      <h3>By run</h3>
      <UsageTotalsTable caption="Token usage by run" keyLabel="Run" buckets={usage.by_run} empty="No runs yet." labelFor={(key) => <Link className="text-link" href={`/runs/${encodeURIComponent(key)}`}>{key}</Link>} />
    </section>
  </>;
}

export function Account() {
  const session = useSession();
  const [, navigate] = useLocation();
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [storage, setStorage] = useState<StorageResponse | null>(null);
  const [error, setError] = useState("");
  const token = session?.token;

  useEffect(() => {
    setUsage(null); setStorage(null); setError("");
    if (!token) return;
    let live = true;
    // The storage read-out is secondary: if it fails the rest of the page still shows.
    api.storage(token).then((value) => { if (live) setStorage(value); }).catch(() => undefined);
    api.usage(token).then((value) => { if (live) setUsage(value); }).catch((cause: unknown) => {
      if (!live) return;
      if (cause instanceof ApiError && cause.status === 401) { clearSession(); return; }
      setError(cause instanceof Error ? cause.message : "Usage could not be loaded.");
    });
    return () => { live = false; };
  }, [token]);

  if (!session) return <section className="auth-page"><div className="page-heading"><p className="eyebrow">Account</p><h1>You are signed out</h1></div><p className="step-intro">Sign in to see your linked key and the tokens your runs have used.</p><div className="auth-form__actions"><Link className="button button--primary" href="/login?next=/account">Sign in</Link><Link className="text-link" href="/signup?next=/account">Create an account</Link></div></section>;

  async function signOut(current: Session) {
    try { await api.logout(current.token); } catch { /* The local session is cleared either way. */ }
    clearSession();
    navigate("/");
  }

  return <div className="account-page">
    <header className="configure-header"><div><div className="page-heading"><p className="eyebrow">Account</p><h1>{session.user.username}</h1></div></div><div className="auth-form__actions">{session.user.is_admin ? <Link className="button button--secondary" href="/admin">All accounts</Link> : null}<button className="button button--secondary" type="button" onClick={() => void signOut(session)}>Sign out</button></div></header>
    {error ? <p className="prereg-error" role="alert">{error}</p> : null}
    {usage ? <Usage usage={usage} /> : error ? null : <p className="usage-empty" role="status">Loading usage…</p>}
    {storage ? <StoragePanel storage={storage} /> : null}
    <LinkedKey session={session} />
  </div>;
}
