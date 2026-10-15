import type {
  CardsResponse, CreateSessionRequest, EstimatesResponse, LaunchRequest, LaunchResponse,
  ModelsResponse, PreregRenderRequest, PreregRenderResponse, ReadinessReport, ReadinessRequest,
  RunControlRequest, RunControlResponse, RunKeysRequest, RunKeysResponse, RunView,
  SessionControlRequest, SessionControlResponse, SessionJoinRequest, SessionJoinResponse,
  Questionnaire, SessionPresenceRequest, SessionPresenceResponse, SessionQuestionnaireRequest, SessionView,
  StudyConfig,
} from "@qr-evals/contracts";

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "https://evals-api.entropic.science";
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string | null, readonly type?: string | null, readonly param?: string | null) { super(message); this.name = "ApiError"; }
}
type Headers = { bearer?: string; hostToken?: string; observerId?: string };
type ErrorEnvelope = { error?: { message?: string; code?: string | null; type?: string | null; param?: string | null } };

export async function apiFetch<Response, Body = never>(path: string, options: { method?: "GET" | "POST"; body?: Body; auth?: Headers; signal?: AbortSignal } = {}): Promise<Response> {
  const headers = new globalThis.Headers({ Accept: "application/json" });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.auth?.bearer) headers.set("Authorization", `Bearer ${options.auth.bearer}`);
  if (options.auth?.hostToken) headers.set("X-Host-Token", options.auth.hostToken);
  if (options.auth?.observerId) headers.set("X-Observer-Id", options.auth.observerId);
  const init: RequestInit = { method: options.method ?? (options.body === undefined ? "GET" : "POST"), headers };
  if (options.body !== undefined) init.body = JSON.stringify(options.body);
  if (options.signal) init.signal = options.signal;
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) { let envelope: ErrorEnvelope = {}; try { envelope = await response.json() as ErrorEnvelope; } catch { /* Non-JSON upstream response. */ } throw new ApiError(envelope.error?.message ?? `Request failed with status ${response.status}`, response.status, envelope.error?.code, envelope.error?.type, envelope.error?.param); }
  return await response.json() as Response;
}

export const api = {
  cards: () => apiFetch<CardsResponse>("/api/cards"),
  models: () => apiFetch<ModelsResponse>("/api/models"),
  estimates: (body: StudyConfig) => apiFetch<EstimatesResponse, StudyConfig>("/api/estimates", { body }),
  prereg: (body: PreregRenderRequest) => apiFetch<PreregRenderResponse, PreregRenderRequest>("/api/prereg/render", { body }),
  readiness: (body: ReadinessRequest, bearer: string) => apiFetch<ReadinessReport, ReadinessRequest>("/api/readiness", { body, auth: { bearer } }),
  launch: (body: LaunchRequest, bearer: string) => apiFetch<LaunchResponse, LaunchRequest>("/api/runs", { body, auth: { bearer } }),
  run: (runId: string) => apiFetch<RunView>(`/api/runs/${encodeURIComponent(runId)}`),
  controlRun: (runId: string, body: RunControlRequest, hostToken: string) => apiFetch<RunControlResponse, RunControlRequest>(`/api/runs/${encodeURIComponent(runId)}/control`, { body, auth: { hostToken } }),
  supplyKeys: (runId: string, body: RunKeysRequest, hostToken: string) => apiFetch<RunKeysResponse, RunKeysRequest>(`/api/runs/${encodeURIComponent(runId)}/keys`, { body, auth: { hostToken } }),
  createSession: (runId: string, body: CreateSessionRequest, hostToken: string) => apiFetch<SessionView, CreateSessionRequest>(`/api/runs/${encodeURIComponent(runId)}/sessions`, { body, auth: { hostToken } }),
  controlSession: (sessionId: string, body: SessionControlRequest, hostToken: string) => apiFetch<SessionControlResponse, SessionControlRequest>(`/api/sessions/${encodeURIComponent(sessionId)}/control`, { body, auth: { hostToken } }),
  joinSession: (sessionId: string, body: SessionJoinRequest) => apiFetch<SessionJoinResponse, SessionJoinRequest>(`/api/sessions/${encodeURIComponent(sessionId)}/join`, { body }),
  presence: (sessionId: string, body: SessionPresenceRequest, observerId: string) => apiFetch<SessionPresenceResponse, SessionPresenceRequest>(`/api/sessions/${encodeURIComponent(sessionId)}/presence`, { body, auth: { observerId } }),
  questionnaire: (sessionId: string, body: SessionQuestionnaireRequest, observerId: string) => apiFetch<Questionnaire, SessionQuestionnaireRequest>(`/api/sessions/${encodeURIComponent(sessionId)}/questionnaire`, { body, auth: { observerId } }),
};

export const RUN_EVENT_TYPES = ["run.state", "run.progress", "sample.started", "sample.delta", "sample.finished", "taint", "session.state", "session.timer", "observer.presence", "analysis.progress", "error", "resync"] as const;
export type RunEventType = typeof RUN_EVENT_TYPES[number];
export interface RunEvent<T = unknown> { id?: string; type: RunEventType; data: T }
export interface ResyncData { reason?: string; dropped?: number }
export interface OpenEventsOptions { hostToken?: string; authTransport?: "query"; onEvent: (event: RunEvent) => void; onResync: (event: RunEvent<ResyncData>) => void; onError?: (event: Event) => void }

export function openEvents(runId: string, options: OpenEventsOptions): () => void {
  const url = new URL(`${API_BASE}/api/runs/${encodeURIComponent(runId)}/events`);
  if (options.hostToken) {
    if (options.authTransport !== "query") throw new Error("EventSource cannot send X-Host-Token; opt in to authTransport: 'query' or use viewer access.");
    url.searchParams.set("host_token", options.hostToken);
  }
  const source = new EventSource(url);
  for (const type of RUN_EVENT_TYPES) source.addEventListener(type, (raw) => {
    const message = raw as MessageEvent<string>;
    let data: unknown = message.data;
    try { data = JSON.parse(message.data) as unknown; } catch { /* Plain text events remain plain text. */ }
    const event: RunEvent = { type, data };
    if (message.lastEventId) event.id = message.lastEventId;
    if (type === "resync") options.onResync(event as RunEvent<ResyncData>); else options.onEvent(event);
  });
  if (options.onError) source.addEventListener("error", options.onError);
  return () => source.close();
}
