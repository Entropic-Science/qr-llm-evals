import type { StudyConfig } from "@qr-evals/contracts";
import { useState } from "react";
import { api } from "../../lib/api";
import { setHostToken } from "../../lib/host-token";
import { Modal } from "../Modal";

interface LaunchDialogProps {
  open: boolean;
  config: StudyConfig;
  launchKey: string;
  providerKeys: Record<string, string>;
  onClose: () => void;
  onClearKeys: () => void;
  onLaunched: (runId: string) => void;
}

export function LaunchDialog({ open, config, launchKey, providerKeys, onClose, onClearKeys, onLaunched }: LaunchDialogProps) {
  const [state, setState] = useState<"idle" | "checking" | "launching" | "error">("idle");
  const [message, setMessage] = useState("");
  async function launch() {
    if (!launchKey) { setState("error"); setMessage("A launch key is required."); return; }
    try {
      setState("checking"); setMessage("Checking engine, entropy, and arm equivalence…");
      const readiness = await api.readiness({ config, throughput_probe: false }, launchKey);
      setState("launching"); setMessage("Readiness recorded. Creating the server-side run…");
      const result = await api.launch({ config, readiness, kind: config.pilot?.enabled ? "pilot" : "full", provider_keys: providerKeys }, launchKey);
      setHostToken(result.run_id, result.host_token);
      onClearKeys();
      onLaunched(result.run_id);
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Launch failed.");
    }
  }
  return <Modal open={open} onClose={state === "checking" || state === "launching" ? () => undefined : onClose} eyebrow="Final launch gate" title="Readiness before generation" footer={<div className="launch-dialog__actions"><button className="button button--secondary" type="button" disabled={state === "checking" || state === "launching"} onClick={onClose}>Cancel</button><button className="button button--primary" type="button" disabled={state === "checking" || state === "launching"} onClick={() => void launch()}>Check readiness &amp; launch</button></div>}>
    <p>This performs the preregistered readiness check first, then creates the run. <strong>It does not start generation.</strong> The host must explicitly press Start on the run page.</p>
    <dl className="launch-ledger"><div><dt>Arms</dt><dd>{config.arms.length}</dd></div><div><dt>Evaluations</dt><dd>{config.cards.length}</dd></div><div><dt>Key handling</dt><dd>Sent once · cleared after success</dd></div></dl>
    {message ? <p className={state === "error" ? "prereg-error" : "launch-dialog__status"} role="status">{message}</p> : null}
  </Modal>;
}
