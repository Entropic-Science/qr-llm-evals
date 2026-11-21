import { useState } from "react";
import { Link } from "wouter";
import { api, ApiError } from "../../lib/api";
import { DEFAULT_JUDGE, needsStudyJudge } from "../../lib/judge";
import { clearSession, useSession } from "../../lib/session";
import type { WizardStepProps } from "./index";

type Check = { kind: "idle" | "busy" | "ok" | "error"; message: string };

export function Keys({ keys, setKeys, config, cards }: WizardStepProps) {
  const session = useSession();
  const [check, setCheck] = useState<Check>({ kind: "idle", message: "" });
  const judged = needsStudyJudge(config, cards);
  const judge = { ...DEFAULT_JUDGE, ...config.judge };

  async function testJudge() {
    if (!session) { setCheck({ kind: "error", message: "Sign in first; the test runs through your account." }); return; }
    setCheck({ kind: "busy", message: "Asking the judge for a one-word answer…" });
    try {
      const result = await api.judgeCheck({ judge, api_key: keys.anthropic }, session.token);
      setCheck(result.ok
        ? { kind: "ok", message: `${result.model ?? judge.model} answered. ${result.detail}` }
        : { kind: "error", message: `The judge call failed${result.status ? ` (${result.status})` : ""}: ${result.detail}` });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { clearSession(); return; }
      setCheck({ kind: "error", message: cause instanceof Error ? cause.message : "The test did not reach the server." });
    }
  }

  return <section aria-labelledby="step-keys"><p className="eyebrow">Protocol 06 · volatile memory</p><h2 id="step-keys">Access keys</h2><p className="step-intro">Provider keys stay in this page's component state, are sent once at launch, and are never stored in this browser configuration.</p>
    <div className="field-stack">
      {session
        ? <p className="account-line">Launching as <strong>{session.user.username}</strong>, with the key {session.user.key_name ?? session.user.key_fingerprint}. <Link className="text-link" href="/account">Account</Link></p>
        : <p className="validation-note">Launching needs an account. <Link className="text-link" href="/login?next=/">Sign in</Link> or <Link className="text-link" href="/signup?next=/">create one</Link> before typing keys here: the study configuration is kept across sign-in, the keys on this step are not.</p>}
      <label>Anthropic judge key<input type="password" autoComplete="off" required={judged} value={keys.anthropic} onChange={(event) => { setKeys({ ...keys, anthropic: event.target.value }); setCheck({ kind: "idle", message: "" }); }} placeholder="sk-ant-…" /></label>
      <p className="field-note">{judged
        ? "Required. This study has evals scored by the judge. The judge runs on your own Anthropic key and Anthropic bills you for it; the run cannot launch without the key."
        : "Not needed: none of the selected evals uses the judge."}</p>
      <div className="auth-form__actions"><button className="button button--secondary" type="button" disabled={!keys.anthropic || check.kind === "busy"} onClick={() => void testJudge()}>Test the judge</button><span className="field-note">One short call to {judge.model || "the judge model"} with the settings from step 05.</span></div>
      {check.message ? <p className={check.kind === "error" ? "prereg-error" : "privacy-note"} role={check.kind === "error" ? "alert" : "status"}>{check.message}</p> : null}
      <label>External provider key <span className="optional">optional</span><input type="password" autoComplete="off" value={keys.external} onChange={(event) => setKeys({ ...keys, external: event.target.value })} /></label>
    </div><p className="privacy-note">Nothing typed here is included in localStorage or the share link.</p>
  </section>;
}
