import type { AuthResponse } from "@qr-evals/contracts";

/** A sign-in response for tests; never a real token. */
export function authResponse(overrides: Partial<AuthResponse["user"]> = {}, expiresAt = new Date(Date.now() + 3_600_000).toISOString()): AuthResponse {
  return {
    session_token: "qrs_" + "test-session-token",
    expires_at: expiresAt,
    user: { user_id: "usr_1", username: "alice", key_name: "alice-key", key_fingerprint: "0a1b2c3d", key_linked_at: "2026-11-18T12:00:00Z", is_admin: false, created_at: "2026-11-18T12:00:00Z", ...overrides },
  };
}
