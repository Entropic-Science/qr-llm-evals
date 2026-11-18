import type { AuthResponse, UserView } from "@qr-evals/contracts";
import { useSyncExternalStore } from "react";

/**
 * The signed-in account, kept in localStorage so a reload stays signed in.
 *
 * Only the session token lives here. The api.entropic.science key typed at
 * sign-up is sent once and never stored in the browser; the server keeps its
 * SHA-256 only.
 */
export const SESSION_STORAGE_KEY = "qr-evals.session.v1";

export interface Session { token: string; expiresAt: string; user: UserView }

const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cached: Session | null = null;

function storage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

function parse(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<Session>;
    if (typeof value.token !== "string" || typeof value.expiresAt !== "string" || typeof value.user?.username !== "string") return null;
    if (Date.parse(value.expiresAt) <= Date.now()) return null;
    return value as Session;
  } catch { return null; }
}

/** The current session, or null when signed out, expired, or storage is blocked. */
export function getSession(): Session | null {
  let raw: string | null = null;
  try { raw = storage()?.getItem(SESSION_STORAGE_KEY) ?? null; } catch { raw = null; }
  if (raw !== cachedRaw) { cachedRaw = raw; cached = parse(raw); }
  else if (cached && Date.parse(cached.expiresAt) <= Date.now()) cached = null;
  return cached;
}

function notify(): void { for (const listener of listeners) listener(); }

export function setSession(auth: AuthResponse): Session {
  const session: Session = { token: auth.session_token, expiresAt: auth.expires_at, user: auth.user };
  try { storage()?.setItem(SESSION_STORAGE_KEY, JSON.stringify(session)); } catch { /* Signed in for this page only. */ }
  cachedRaw = undefined;
  notify();
  return session;
}

export function updateSessionUser(user: UserView): void {
  const current = getSession();
  if (!current || JSON.stringify(current.user) === JSON.stringify(user)) return;
  try { storage()?.setItem(SESSION_STORAGE_KEY, JSON.stringify({ ...current, user })); } catch { /* Storage blocked. */ }
  cachedRaw = undefined;
  notify();
}

export function clearSession(): void {
  try { storage()?.removeItem(SESSION_STORAGE_KEY); } catch { /* Already gone. */ }
  cachedRaw = undefined;
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => { if (event.key === SESSION_STORAGE_KEY || event.key === null) listener(); };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); };
}

/** Re-renders when the session changes, in this tab or another one. */
export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession, () => null);
}
