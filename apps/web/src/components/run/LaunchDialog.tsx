import type { StudyConfig } from "@qr-evals/contracts";
import { useState } from "react";
import { Link } from "wouter";
import { api, ApiError } from "../../lib/api";
import { setHostToken } from "../../lib/host-token";
import { clearSession, getSession } from "../../lib/session";
import { Modal } from "../Modal";

interface LaunchDialogProps {
  open: boolean;
  config: StudyConfig;
  providerKeys: Record<string, string>;
  onClose: () => void;
  onClearKeys: () => void;
  onLaunched: (runId: string) => void;
}

// Both kinds run every prompt and repetition the study specifies: nothing
// subsamples a pilot, and nothing derives a margin from one.
/** Run-kind labels of a study with a design block, whose verdicts a script computes. */
export const DESIGNED_KIND_LABELS = {
  pilot: "Pilot: runs the study as configured and is labelled non-confirmatory. It does not set margins.",
  // The plan of a designed study registers this as its stopping rule.
  full: "Full run: runs the study as configured. Only the first full run of a study can be confirmatory.",
} as const;
/** Run-kind labels of a study on the fixed Study 1 plan, which has no verdicts. */
export const LEGACY_KIND_LABELS = {
  pilot: "Pilot: runs the study as configured. Its results are labelled as a pilot. It does not set margins.",
  full: "Full run: runs the study as configured.",
} as const;

export function LaunchDialog({ open, config, providerKeys, onClose, onClearKeys, onLaunched }: LaunchDialogProps) {
  const [state, setState] = useState<"idle" | "checking" | "launching" | "error">("idle");
  const [signedOut, setSignedOut] = useState(false);
  const [message, setMessage] = useState("");
  // The study's pilot setting chooses which kind is selected when the dialog opens; both stay on offer. A
  // study that says nothing about a pilot starts on one, as the server's default has it.
  const [kind, setKind] = useState<"pilot" | "full">((config.pilot?.enabled ?? true) ? "pilot" : "full");
  const labels = config.design != null ? DESIGNED_KIND_LABELS : LEGACY_KIND_LABELS;
  async function launch() {
    const session = getSession();
    if (!session) { setState("error"); setSignedOut(true); setMessage("Your session has ended. Sign in again to launch."); return; }
    setSignedOut(false);
    const launchKey = session.token;
    try {
      setState("checking"); setMessage("Checking engine, entropy, and arm equivalence…");
      const readiness = await api.readiness({ config, throughput_probe: false }, launchKey);
      setState("launching"); setMessage("Readiness recorded. Freezing the pre-registration and creating the run…");
      // The hash of the pre-registration for exactly this configuration and this
      // readiness report; the server compares it with the document it freezes.
      const prereg = await api.prereg({ config, readiness });
      const result = await api.launch({ config, readiness, kind, prereg_hash: prereg.prereg_hash, provider_keys: providerKeys }, launchKey);
      setHostToken(result.run_id, result.host_token);
      onClearKeys();
      onLaunched(result.run_id);
    } catch (error) {
      setState("error");
      if (error instanceof ApiError && error.status === 401) { clearSession(); setSignedOut(true); setMessage("Your session has ended. Sign in again to launch."); return; }
      setMessage(error instanceof Error ? error.message : "Launch failed.");
    }
  }
  return <Modal open={open} onClose={state === "checking" || state === "launching" ? () => undefined : onClose} eyebrow="Final launch gate" title="Readiness before generation" footer={<div className="launch-dialog__actions"><button className="button button--secondary" type="button" disabled={state === "checking" || state === "launching"} onClick={onClose}>Cancel</button><button className="button button--primary" type="button" disabled={state === "checking" || state === "launching"} onClick={() => void launch()}>Check readiness &amp; launch</button></div>}>
    <p>This performs the preregistered readiness check first, then creates the run. <strong>It does not start generation.</strong> The host must explicitly press Start on the run page.</p>
    <fieldset className="launch-kind"><legend>Kind of run</legend>
      <label><input type="radio" name="run-kind" checked={kind === "pilot"} onChange={() => setKind("pilot")} /> <span>{labels.pilot}</span></label>
      <label><input type="radio" name="run-kind" checked={kind === "full"} onChange={() => setKind("full")} /> <span>{labels.full}</span></label>
    </fieldset>
    <dl className="launch-ledger"><div><dt>Arms</dt><dd>{config.arms.length}</dd></div><div><dt>Evaluations</dt><dd>{config.cards.length}</dd></div><div><dt>Provider keys</dt><dd>Sent once · cleared after success</dd></div></dl>
    {message ? <p className={state === "error" ? "prereg-error" : "launch-dialog__status"} role={state === "error" ? "alert" : "status"}>{message}{signedOut ? <> <Link className="text-link" href="/login?next=/">Sign in</Link> (provider keys need typing again).</> : null}</p> : null}
  </Modal>;
}
