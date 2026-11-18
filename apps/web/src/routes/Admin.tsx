import type { AdminUsersResponse, AdminUserUpdate, AdminUserView } from "@qr-evals/contracts";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "wouter";
import { formatTokens } from "../components/UsageTotalsTable";
import { api, ApiError } from "../lib/api";
import { clearSession, useSession } from "../lib/session";

function quotaLabel(row: AdminUserView, fallback: number): string {
  const limit = row.quota.limit;
  const text = limit === 0 ? "Unlimited" : formatTokens(limit);
  return row.token_quota === null ? `${text} (default${fallback === limit ? "" : " changed"})` : text;
}

function QuotaEditor({ row, onSave }: { row: AdminUserView; onSave: (update: AdminUserUpdate) => Promise<void> }) {
  const [value, setValue] = useState(row.token_quota === null ? "" : String(row.token_quota));
  const [busy, setBusy] = useState(false);
  const [invalid, setInvalid] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (trimmed !== "" && !/^\d+$/.test(trimmed)) { setInvalid(true); return; }
    setInvalid(false);
    setBusy(true);
    try { await onSave({ token_quota: trimmed === "" ? null : Number(trimmed) }); } finally { setBusy(false); }
  }
  return <form className="quota-editor" onSubmit={(event) => void submit(event)}>
    <label><span className="sr-only">Token quota for {row.user.username}. Empty for the default, 0 for unlimited.</span><input inputMode="numeric" placeholder="default" aria-invalid={invalid} value={value} onChange={(event) => { setValue(event.target.value); setInvalid(false); }} /></label>
    <button className="button button--secondary" type="submit" disabled={busy} aria-label={`Set quota for ${row.user.username}`}>Set</button>
    {invalid ? <span className="quota-editor__error" role="alert">Whole number, 0, or empty</span> : null}
  </form>;
}

export function Admin() {
  const session = useSession();
  const [data, setData] = useState<AdminUsersResponse | null>(null);
  const [error, setError] = useState("");
  const token = session?.token;
  const isAdmin = session?.user.is_admin ?? false;

  const load = useCallback(async () => {
    if (!token) return;
    try { setData(await api.adminUsers(token)); setError(""); } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { clearSession(); return; }
      setError(cause instanceof Error ? cause.message : "Accounts could not be loaded.");
    }
  }, [token]);
  useEffect(() => { if (isAdmin) void load(); }, [isAdmin, load]);

  if (!session) return <section className="auth-page"><p className="eyebrow">Admin</p><h1>You are signed out</h1><div className="auth-form__actions"><Link className="button button--primary" href="/login?next=/admin">Sign in</Link></div></section>;
  if (!isAdmin) return <section className="auth-page"><p className="eyebrow">Admin</p><h1>Admins only</h1><p className="step-intro">This page lists every account. Your account is not an admin.</p><Link className="text-link" href="/account">Back to your account</Link></section>;

  async function update(userId: string, body: AdminUserUpdate) {
    if (!token) return;
    try { await api.adminUpdateUser(userId, body, token); await load(); } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { clearSession(); return; }
      setError(cause instanceof Error ? cause.message : "The change was not saved.");
    }
  }

  return <div className="account-page">
    <header className="configure-header"><div><p className="eyebrow">Admin</p><h1>All accounts</h1></div><Link className="button button--secondary" href="/account">Your account</Link></header>
    {error ? <p className="prereg-error" role="alert">{error}</p> : null}
    {data === null ? (error ? null : <p className="usage-empty" role="status">Loading accounts…</p>) : <>
      <p className="step-intro">Default quota: {data.default_token_quota === 0 ? "unlimited" : `${formatTokens(data.default_token_quota)} tokens`}. Leave a quota empty to follow the default; enter 0 for unlimited.</p>
      <div className="table-frame usage-table"><table>
        <caption className="sr-only">Every account with its token usage and quota</caption>
        <thead><tr><th scope="col">Account</th><th scope="col">Key</th><th scope="col" className="num">Calls</th><th scope="col" className="num">Prompt</th><th scope="col" className="num">Completion</th><th scope="col" className="num">Counted</th><th scope="col">Quota</th><th scope="col">Set quota</th><th scope="col">Access</th></tr></thead>
        <tbody>{data.users.map((row) => {
          const self = row.user.user_id === session.user.user_id;
          return <tr key={row.user.user_id} className={row.disabled ? "is-disabled" : undefined}>
            <th scope="row">{row.user.username}{row.user.is_admin ? <span className="badge">admin</span> : null}{row.disabled ? <span className="badge">disabled</span> : null}</th>
            <td>{row.user.key_name ?? row.user.key_fingerprint}</td>
            <td className="num">{formatTokens(row.totals.calls)}</td>
            <td className="num">{formatTokens(row.totals.prompt_tokens)}</td>
            <td className="num">{formatTokens(row.totals.completion_tokens)}</td>
            <td className="num">{formatTokens(row.quota.used)}{row.quota.exhausted ? <span className="badge">used up</span> : null}</td>
            <td>{quotaLabel(row, data.default_token_quota)}</td>
            <td><QuotaEditor key={`${row.user.user_id}:${row.token_quota ?? "default"}`} row={row} onSave={(body) => update(row.user.user_id, body)} /></td>
            <td><button className="button button--secondary" type="button" disabled={self} title={self ? "You cannot disable your own account" : undefined} aria-label={`${row.disabled ? "Enable" : "Disable"} ${row.user.username}`} onClick={() => void update(row.user.user_id, { disabled: !row.disabled })}>{row.disabled ? "Enable" : "Disable"}</button></td>
          </tr>;
        })}</tbody>
      </table></div>
    </>}
  </div>;
}
