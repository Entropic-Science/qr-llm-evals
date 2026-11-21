import { useState, type FormEvent } from "react";
import { Link, useLocation } from "wouter";
import { api, ApiError } from "../lib/api";
import { nextPath, setSession } from "../lib/session";

type Mode = "login" | "signup";

function AuthForm({ mode }: { mode: Mode }) {
  const [, navigate] = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const signup = mode === "signup";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const auth = signup
        ? await api.signup({ username, password, api_key: apiKey })
        : await api.login({ username, password });
      setApiKey(""); setPassword("");
      setSession(auth);
      navigate(nextPath(window.location.search));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The request did not reach the server. Try again.");
    } finally { setBusy(false); }
  }

  return <section className="auth-page" aria-labelledby="auth-title">
    <div className="page-heading"><p className="eyebrow">{signup ? "New account" : "Account"}</p>
    <h1 id="auth-title">{signup ? "Create an account" : "Sign in"}</h1></div>
    <p className="step-intro">{signup
      ? "An account links one api.entropic.science key to a username. Runs you launch are recorded under it, with the tokens they use."
      : "Sign in to launch runs and see the tokens your runs have used."}</p>
    <form className="field-stack auth-form" onSubmit={(event) => void submit(event)}>
      <label>Username<input name="username" autoComplete="username" required minLength={3} maxLength={32} value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label>Password<input name="password" type="password" autoComplete={signup ? "new-password" : "current-password"} required minLength={signup ? 10 : 1} maxLength={256} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      {signup ? <label>api.entropic.science key<input name="api_key" type="password" autoComplete="off" required placeholder="qrk_…" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label> : null}
      {signup ? <p className="privacy-note">The key is checked once and then discarded. The server keeps only its SHA-256, and this browser keeps nothing of it.</p> : null}
      {error ? <p className="prereg-error" role="alert">{error}</p> : null}
      <div className="auth-form__actions">
        <button className="button button--primary" type="submit" disabled={busy}>{busy ? "Working…" : signup ? "Create account" : "Sign in"}</button>
        {signup
          ? <Link className="text-link" href={`/login${window.location.search}`}>I already have an account</Link>
          : <Link className="text-link" href={`/signup${window.location.search}`}>Create an account</Link>}
      </div>
    </form>
  </section>;
}

export function Login() { return <AuthForm mode="login" />; }
export function Signup() { return <AuthForm mode="signup" />; }
