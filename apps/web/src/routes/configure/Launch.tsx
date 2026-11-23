import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "wouter";
import { LaunchDialog } from "../../components/run/LaunchDialog";
import { needsStudyJudge } from "../../lib/judge";
import { hasErrors } from "../../lib/problems";
import { getSession } from "../../lib/session";
import { useStudy } from "./StudyContext";

// The keys a launch needs and the launch itself.
//
// The keys live here, in memory, for as long as the setup page is open: they survive a walk through the
// steps, and they are never part of the study, so they reach neither storage, a share link nor a study file.

export interface VolatileKeys {
  anthropic: string;
  external: string;
}

interface LaunchValue {
  keys: VolatileKeys;
  setKeys: (keys: VolatileKeys) => void;
  /** Why the last attempt to launch did not open the launch dialog, or "". */
  note: string;
  /** Opens the launch dialog, or says in `note` what is missing. */
  launch: () => void;
}

const NO_KEYS: VolatileKeys = { anthropic: "", external: "" };
const LaunchContext = createContext<LaunchValue | null>(null);

export function useLaunch(): LaunchValue {
  const value = useContext(LaunchContext);
  if (value === null) throw new Error("useLaunch() is called outside <LaunchProvider>.");
  return value;
}

export function LaunchProvider({ children }: { children: ReactNode }) {
  const { config, cards, problems } = useStudy();
  const [, navigate] = useLocation();
  const [keys, setKeys] = useState<VolatileKeys>(NO_KEYS);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);

  const launch = useCallback(() => {
    if (hasErrors(problems)) { setNote("This study has problems that block a launch. Fix them first."); return; }
    if (!getSession()) { setNote("Sign in before launching; the link is under Access keys on this step. The study configuration is kept; provider keys need typing again."); return; }
    if (needsStudyJudge(config, cards) && !keys.anthropic) { setNote("This study has evals scored by the judge, which runs on your own Anthropic key. Add the key under Access keys, then launch."); return; }
    setNote("");
    setOpen(true);
  }, [config, cards, problems, keys.anthropic]);

  const providerKeys = useMemo(() => {
    const sent: Record<string, string> = {};
    if (keys.anthropic) sent.anthropic = keys.anthropic;
    // Sent only when the study has an external model. Both slots get it: a custom base URL on a known
    // provider's host resolves to that provider's slot server-side.
    if (keys.external && config.models.some((model) => model.provider === "openai" || model.provider === "custom")) {
      sent.openai = keys.external;
      sent.custom = keys.external;
    }
    return sent;
  }, [keys, config.models]);

  const value = useMemo<LaunchValue>(() => ({ keys, setKeys, note, launch }), [keys, note, launch]);
  return <LaunchContext.Provider value={value}>
    {children}
    {/* Drawn only while it is open: the kind of run it starts on is read from the study as it stands at each opening, not as it stood when the page loaded. */}
    {open ? <LaunchDialog open config={config} providerKeys={providerKeys} onClose={() => setOpen(false)} onClearKeys={() => setKeys(NO_KEYS)} onLaunched={(runId) => navigate(`/runs/${encodeURIComponent(runId)}`)} /> : null}
  </LaunchContext.Provider>;
}
