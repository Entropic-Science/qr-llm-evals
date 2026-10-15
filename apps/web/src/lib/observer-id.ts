const ID_KEY = "qr-evals.observer-id.v1";
const NAME_KEY = "qr-evals.observer-name.v1";

function randomId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return `obs_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

export function getObserverIdentity(storage: Storage = window.localStorage): { observerId: string; displayName: string } {
  let observerId = "";
  let displayName = "";
  try { observerId = storage.getItem(ID_KEY) ?? ""; displayName = storage.getItem(NAME_KEY) ?? ""; } catch { /* Private browsing may block storage. */ }
  if (!observerId) {
    observerId = randomId();
    try { storage.setItem(ID_KEY, observerId); } catch { /* The in-memory identity still works this visit. */ }
  }
  return { observerId, displayName };
}

export function saveObserverName(displayName: string, storage: Storage = window.localStorage): void {
  try { storage.setItem(NAME_KEY, displayName.trim()); } catch { /* Storage is optional. */ }
}
