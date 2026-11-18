import type { AuthResponse, UserView } from "@qr-evals/contracts";
import { useSyncExternalStore } from "react";

/**
 * The signed-in account, kept in localStorage so a reload stays signed in.
 *
 * Only the session token lives here. The api.entropic.science key typed at
 * sign-up is sent once and never stored in the browser; the server keeps its
 * SHA-256 only. When storage is blocked the session is held in memory, so the
 * user stays signed in until the page is closed.
 */
export const SESSION_STORAGE_KEY = "qr-evals.session.v1";

export interface Session { token: string; expiresAt: string; user: UserView }

const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cached: Session | null = null;
/** Fallback for a browser that refuses localStorage writes. */
let memory: string | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;

function storage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

function readRaw(): string | null {
  try { return storage()?.getItem(SESSION_STORAGE_KEY) ?? memory; } catch { return memory; }
}

function writeRaw(raw: string | null): void {
  memory = null;
  try {
    const store = storage();
    if (!store) { memory = raw; return; }
    if (raw === null) store.removeItem(SESSION_STORAGE_KEY); else store.setItem(SESSION_STORAGE_KEY, raw);
  } catch { memory = raw; }
}

function expired(session: Session): boolean {
  const at = Date.parse(session.expiresAt);
  return Number.isNaN(at) || at <= Date.now();
}

function parse(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<Session>;
    if (typeof value.token !== "string" || typeof value.expiresAt !== "string" || typeof value.user?.username !== "string") return null;
    return expired(value as Session) ? null : value as Session;
  } catch { return null; }
}

/** The current session, or null when signed out or expired. An expired one is removed. */
export function getSession(): Session | null {
  const raw = readRaw();
  if (raw !== cachedRaw) { cachedRaw = raw; cached = parse(raw); }
  else if (cached && expired(cached)) cached = null;
  if (raw !== null && cached === null) { writeRaw(null); cachedRaw = null; }
  return cached;
}

function notify(): void { for (const listener of listeners) listener(); }

/** Re-render subscribers at the moment the session expires. */
function scheduleExpiry(): void {
  if (expiryTimer !== undefined) clearTimeout(expiryTimer);
  expiryTimer = undefined;
  const session = getSession();
  if (!session || listeners.size === 0) return;
  // setTimeout overflows above ~24.8 days; wake at least that often and re-check.
  const wait = Math.min(Date.parse(session.expiresAt) - Date.now() + 50, 2_000_000_000);
  expiryTimer = setTimeout(() => { notify(); scheduleExpiry(); }, Math.max(0, wait));
}

function changed(): void { cachedRaw = undefined; notify(); scheduleExpiry(); }

export function setSession(auth: AuthResponse): Session {
  const session: Session = { token: auth.session_token, expiresAt: auth.expires_at, user: auth.user };
  writeRaw(JSON.stringify(session));
  changed();
  return session;
}

export function updateSessionUser(user: UserView): void {
  const current = getSession();
  if (!current || JSON.stringify(current.user) === JSON.stringify(user)) return;
  writeRaw(JSON.stringify({ ...current, user }));
  changed();
}

export function clearSession(): void {
  writeRaw(null);
  changed();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => { if (event.key === SESSION_STORAGE_KEY || event.key === null) { listener(); scheduleExpiry(); } };
  window.addEventListener("storage", onStorage);
  scheduleExpiry();
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); scheduleExpiry(); };
}

/** Re-renders when the session changes or expires, in this tab or another one. */
export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession, () => null);
}

/** Where to go after signing in: a same-site path from `?next=`, else the account page. */
export function nextPath(search: string, fallback = "/account"): string {
  const next = new URLSearchParams(search).get("next");
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return fallback;
  return next;
}
