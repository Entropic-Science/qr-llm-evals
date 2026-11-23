const ID_KEY = "qr-evals.observer-id.v1";
const NAME_KEY = "qr-evals.observer-name.v1";

function randomId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return `obs_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

/** This browser's storage, or null where reading `window.localStorage` itself throws (all site data blocked). */
function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

/** The observer's identity: the one this browser remembers, or a new one that lasts for this visit where it cannot remember any. */
export function getObserverIdentity(storage: Storage | null = browserStorage()): { observerId: string; displayName: string } {
  let observerId = "";
  let displayName = "";
  try { observerId = storage?.getItem(ID_KEY) ?? ""; displayName = storage?.getItem(NAME_KEY) ?? ""; } catch { /* Private browsing may block storage. */ }
  if (!observerId) {
    observerId = randomId();
    try { storage?.setItem(ID_KEY, observerId); } catch { /* The in-memory identity still works this visit. */ }
  }
  return { observerId, displayName };
}

export function saveObserverName(displayName: string, storage: Storage | null = browserStorage()): void {
  try { storage?.setItem(NAME_KEY, displayName.trim()); } catch { /* Storage is optional. */ }
}
