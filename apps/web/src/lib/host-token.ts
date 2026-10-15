const PREFIX = "qr-evals.host-token.v1.";

function storage(): Storage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.sessionStorage;
  } catch {
    return undefined;
  }
}

export function getHostToken(runId: string): string | undefined {
  try {
    return storage()?.getItem(`${PREFIX}${runId}`) ?? undefined;
  } catch {
    return undefined;
  }
}

export function setHostToken(runId: string, token: string): void {
  if (!token) return;
  try {
    storage()?.setItem(`${PREFIX}${runId}`, token);
  } catch {
    // Storage can be disabled; the run remains available in viewer mode.
  }
}

export function clearHostToken(runId: string): void {
  try {
    storage()?.removeItem(`${PREFIX}${runId}`);
  } catch {
    // Treat unavailable storage as already cleared.
  }
}
