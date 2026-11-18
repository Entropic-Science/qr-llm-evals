import { Link } from "wouter";
import { useSession } from "../../lib/session";
import type { WizardStepProps } from "./index";

export function Keys({ keys, setKeys }: WizardStepProps) {
  const session = useSession();
  return <section aria-labelledby="step-keys"><p className="eyebrow">Protocol 06 · volatile memory</p><h2 id="step-keys">Access keys</h2><p className="step-intro">Provider keys stay in this page's component state, are sent once at launch, and are never stored in this browser configuration.</p>
    <div className="field-stack">
      {session
        ? <p className="account-line">Launching as <strong>{session.user.username}</strong>, with the key {session.user.key_name ?? session.user.key_fingerprint}. <Link className="text-link" href="/account">Account</Link></p>
        : <p className="validation-note">Launching needs an account. <Link className="text-link" href="/login?next=/">Sign in</Link> or <Link className="text-link" href="/signup?next=/">create one</Link>. Your configuration is kept.</p>}
      <label>Anthropic judge key<input type="password" autoComplete="off" value={keys.anthropic} onChange={(event) => setKeys({ ...keys, anthropic: event.target.value })} placeholder="sk-ant-…" /></label>
      <label>External provider key <span className="optional">optional</span><input type="password" autoComplete="off" value={keys.external} onChange={(event) => setKeys({ ...keys, external: event.target.value })} /></label>
    </div><p className="privacy-note">Nothing typed here is included in localStorage or the share link.</p>
  </section>;
}
