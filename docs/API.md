# Backend HTTP / SSE API

Base URL on the box: `http://127.0.0.1:8830/api` (loopback), public:
`https://evals-api.entropic.science/api`. JSON in, JSON out; the interactive
OpenAPI document is served at `/api/docs` (`/api/openapi.json`).

The wire shapes are pydantic models in `backend/src/qr_llm_evals/api/schema.py`
and are exported to `@qr-evals/contracts` (`packages/contracts/src/generated`)
by `scripts/export_schemas.py`; the SPA's typed client uses those, never a
hand-copied type.

This page covers the core routes (S11). Supervised sessions (`/sessions/*`,
`POST /runs/{id}/sessions`), `POST /prereg/render`, `POST /runs/{id}/analyse`
and the `/studies/*` results routes are documented as they land.

## Authentication

| Header | Who | Used by |
|---|---|---|
| `Authorization: Bearer qrs_…` | A signed-in account: the session token returned by `POST /auth/signup` or `POST /auth/login`; the server stores only its SHA-256 | `POST /auth/logout`, `GET /me`, `POST /me/key`, `POST /readiness`, `POST /runs` |
| `Authorization: Bearer qrk_…` | Bare launch key (an api.entropic.science key, verified against the same keys file or by an edge probe — `QR_EVALS_KEY_VERIFY`). Still accepted until the web app signs users in | `POST /readiness`, `POST /runs` |
| `X-Host-Token: host_…` | The run's host: the random token returned **once** by `POST /runs`; the record stores only its SHA-256 | `POST /runs/{id}/control`, `POST /runs/{id}/keys` |
| `X-Observer-Id` | A self-chosen observer id (localStorage; no account behind it) | observer routes (supervised mode) |

An account is a username, a password (stored as an scrypt hash) and one linked
api.entropic.science key. The key is verified when it is linked and then
dropped: the account keeps its SHA-256, its name in the keys file and an
8-hex fingerprint, never the key. One key links to one account. In file mode
the linked key is re-checked by its digest on every launch, so disabling or
removing a key in the keys file locks its account out of launching (`403`)
until a working key is linked; in probe mode the key is trusted from the
moment it was linked. Accounts live in `<QR_EVALS_DATA_DIR>/accounts.sqlite`.

The launch key only gates: it is never stored. `run.key_name` keeps the key's
name (file mode) or fingerprint (probe mode) for quota accounting, and
`run.owner_id` the launching account (`null` for a bare-key launch). BYO
provider/judge keys travel in the launch body, live in the run's in-memory
`SecretBag`, and surface only as `keys_present: {provider: bool}`.

- Missing / unrecognised credential → `401` (`authentication_error`, with `WWW-Authenticate: Bearer` on the launch gate).
- Credential presented but not the host of this run → `403` (`permission_error`).
- Keys file unreadable / edge down → `503` + `Retry-After` (never a silent refusal).

## Error envelope

Every non-2xx response, including `404` for unknown routes and `400` for body
validation, has the OpenAI-style shape:

```json
{"error": {"message": "…plain language…", "type": "invalid_request_error", "param": "config.cards", "code": 400}}
```

`type` ∈ `invalid_request_error`, `authentication_error`, `permission_error`,
`not_found_error`, `conflict_error`, `rate_limit_error`, `upstream_error`,
`readiness_error`, `server_error`. A `500` never echoes the exception text.

## Quotas and rate limits

| Limit | Scope | Response |
|---|---|---|
| `QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY` (default 1) active (not `complete`/`aborted`) runs | per launch key, which is also per account (one key links to one account) | `429` + `Retry-After: 60` |
| Token bucket, 10 burst / 30 per minute sustained | per client IP (`X-Forwarded-For` first hop, else peer) on the open `POST` routes (`/estimates`, later `/prereg/render`, `/sessions/{id}/join`), and separately on `/auth/signup` and `/auth/login` | `429` + `Retry-After` |
| `QR_EVALS_DEFAULT_TOKEN_QUOTA` (default 2 000 000; `0` = unlimited), or the per-account value an admin set | per account, lifetime, counting prompt + completion tokens of generations on the qr-server engine only | `403` on `POST /runs` when the budget is spent, or when a `full` run's estimate exceeds what is left; `403` on `start` / `resume`; a running run is paused when the budget reaches zero (`quota.exhausted` event) |
| `QR_EVALS_MAX_INFLIGHT` (default 3) | global in-flight generations | enforced by the runner, not by HTTP |

## Routes

| Method | Path | Auth | Body → Response | Notes |
|---|---|---|---|---|
| GET | `/health` | none | → `HealthResponse` | `status` is `degraded` when the shared engine's `/health` does not answer; `version`, `commit` (`QR_EVALS_COMMIT`), `key_verify`, `active_runs`. |
| POST | `/auth/signup` | none (IP bucket) | `SignupRequest {username, password, api_key}` → `201 AuthResponse {user, session_token, expires_at}` | Creates the account only if `api_key` verifies. `403 param=api_key` for a refused key or when `QR_EVALS_SIGNUP_ENABLED=0`; `409` for a taken username or a key already linked to an account; `400` for an invalid username (3-32 of `a-z 0-9 . _ -`) or password (10-256 characters). |
| POST | `/auth/login` | none (IP bucket) | `LoginRequest {username, password}` → `AuthResponse` | `401` with one message for an unknown username and a wrong password; `403` for a disabled account. Each login issues a new token valid for `QR_EVALS_SESSION_TTL_HOURS`. |
| POST | `/auth/logout` | session | → `{ok: true}` | Revokes the presented token only. |
| GET | `/me` | session | → `UserView {user_id, username, key_name, key_fingerprint, key_linked_at, is_admin, created_at}` | |
| POST | `/me/key` | session | `LinkKeyRequest {api_key}` → `UserView` | Links a different key, verified the same way as at signup. |
| GET | `/me/usage` | session | → `UsageResponse {quota, totals, by_kind, by_source, by_day, by_run}` | Token totals for the account's runs. `by_kind` splits generation / judge / narrative; `by_source` splits generations by provenance source label; `by_day` is the last 30 UTC days and `by_run` the last 20 runs. Only `charged_tokens` count against `quota`. Runs launched with a bare key are not metered. |
| GET | `/admin/users` | admin session | → `AdminUsersResponse {default_token_quota, users[]}` | Every account with its totals and quota. Admin is a flag set with the operator CLI. |
| POST | `/admin/users/{id}` | admin session | `AdminUserUpdate {token_quota?, disabled?, is_admin?}` → `AdminUserView` | Only fields present in the body change. `token_quota: null` returns the account to the default, `0` is unlimited. An admin cannot disable or demote their own account (`409`). |
| GET | `/cards` | none | → `CardsResponse` | Every registry card with a headline `estimate` on the default `qr-llm` target at catalogue throughput. |
| GET | `/cards/{id}` | none | → `CardView` | `404` for unknown ids. |
| GET | `/models` | none | → `ModelsResponse` | Catalogue rows (`pipeline_pinned`, `tps`, `key_slot`) + provider profiles + `default_tps_by_model`. |
| POST | `/estimates` | none (IP bucket) | `StudyConfig` → `EstimatesResponse` | `by_arm[arm_id]` = `SelectionEstimate`; `totals`; `supervised` (observer hours, sessions, wall-clock incl. breaks) for supervised arms. Unknown card → `400 param=cards`. |
| POST | `/readiness` | session or key | `ReadinessRequest {config, run_id?, throughput_probe?}` → `ReadinessReport` | Runs spec §2.7 in a worker thread. Equivalence refusal → `409 readiness_error`; engine unusable → `502`. With `run_id` the report is persisted on the run. |
| POST | `/runs` | session or key | `LaunchRequest {config, kind, prereg_hash?, provider_keys?}` → `201 LaunchResponse {run_id, study_id, host_token, join_code, prereg_stale}` | Freezes the study (config + prereg) under `studies/<study_id>/`, creates `runs/<run_id>/`, state `created`. `prereg_stale` is true unless `prereg_hash` equals the hash of the frozen prereg. `429` on quota; `409` if the study id is frozen with a different config. |
| GET | `/runs/{id}` | none | → `RunView {run, live, control_state, subscribers, readiness}` | `run` is the stored record (secrets structurally impossible; `keys_present` only). |
| POST | `/runs/{id}/control` | host | `{action: start \| pause \| resume \| abort}` → `RunControlResponse` | `start` (from `created`/`ready`) and `resume` (from `paused`) are the only ways into `running`; nothing auto-starts. `pause` takes effect at the next item boundary. Invalid transition → `409`. `resume` on a `needs_keys` run → `409` until keys are supplied. |
| POST | `/runs/{id}/keys` | host | `{provider_keys}` → `RunKeysResponse` | Re-supply BYO keys (after a backend restart the run is `needs_keys`); the run becomes `paused` and can be resumed. |
| GET | `/runs/{id}/events` | none | SSE | See below. |

Run states: `created → running ⇄ paused → complete`, plus `needs_keys`
(judge/provider key missing, or the process restarted), `analysing`
(analysis phase) and `aborted`. `complete` and `aborted` are terminal.

### Launch body example

```json
{
  "config": { "...": "StudyConfig, see packages/contracts/schema/StudyConfig.json" },
  "kind": "full",
  "prereg_hash": "…64 hex from the exported prereg…",
  "provider_keys": { "anthropic": "sk-ant-…", "openai": null }
}
```

## SSE: `GET /runs/{id}/events`

`Content-Type: text/event-stream`; frames are `event:` / `id:` / `data:` (JSON,
one line). Open to viewers — it carries prompts, outputs and provenance, never
keys.

| Event | Data |
|---|---|
| `run.state` | `{run_id, state, at}` — always the first frame (replayed for late subscribers) |
| `run.progress` | per arm/card counters, at most every 2 s |
| `sample.started` | `{sample_ref, arm_id, card_id, prompt_preview, window}` |
| `sample.delta` | `{sample_ref, text?, thinking?}` — coalesced to ≤ 20 Hz per stream |
| `sample.finished` | `{sample_ref, finish_reason, tokens, tps, provenance}` |
| `taint` | a `Taint` record |
| `session.state`, `session.timer`, `observer.presence` | supervised mode |
| `quota.exhausted` | `{run_id, limit, used}` — the owner's token quota ran out and the run was paused; resuming needs a higher quota |
| `analysis.progress` | analysis phase |
| `error` | `{sample_ref?, message, code?}` |
| `resync` | `{dropped}` — the per-subscriber queue (2 000 events, drop-oldest) overflowed; refetch `GET /runs/{id}` |

A `: keep-alive` comment is written after 10 s of silence. The stream ends
when the run reaches a terminal state (the bus closes) or the client
disconnects. A run that is not live in this process (finished, or orphaned by
a restart) still answers with its current `run.state` and then closes.

## Public study artifacts

All of these GET routes are unauthenticated under `/api/studies/{study_id}`.

| Suffix | Response |
|---|---|
| (none) | Frozen study configuration, public run summaries, analysis IDs keyed by run, withdrawal status. |
| `/prereg.md` | Exact archived preregistration Markdown download. |
| `/LICENSE-data` | Archived data licence download (CC BY 4.0; prose is CC BY-SA 4.0). |
| `/bundle.tar.zst` | Complete retained bundle, including withdrawn runs. |
| `/analysis/{analysis_id}/index` | Archived `plan`, `manifest`, table and figure stems, and actual sample provenance counts by arm/source label. |
| `/analysis/{analysis_id}/plan.json` | Frozen statistical plan, including the claim rule. |
| `/analysis/{analysis_id}/manifest.json` | Reproducibility manifest and narrative publication gate. |
| `/analysis/{analysis_id}/tables/{name}` | Script-generated endpoint table JSON. |
| `/analysis/{analysis_id}/figures/{name}.svg` | Script-generated SVG. |
| `/analysis/{analysis_id}/narrative.md` | Labelled narrative Markdown or withholding notice. |

Every analysis route accepts `?run_id=<run_id>`. Always supply it when
selecting a run: pilot and full runs may share the same plan-derived analysis
ID. An omitted run ID only resolves when that analysis exists uniquely.
Unknown or ambiguous IDs return 404. Manifest responses require cache
revalidation because narrative verification is recorded after statistics.

The index reports only source labels and counts from archived generations;
missing provenance is `unavailable`. A recorded quantum source with fallback,
degradation, or no verified quantum flag is `unverified`. The UI must not infer
verified quantum provenance from the configured source. Narrative prose is
shown only when `manifest.narrative.published` is exactly `true`.

## Process entry points

- `python -m qr_llm_evals.entrypoint` (or `uvicorn qr_llm_evals.entrypoint:build_asgi_app --factory`) binds `QR_EVALS_BIND_HOST:QR_EVALS_BIND_PORT` (default `127.0.0.1:8830`). Boot refuses (exit 2, one JSON line + one plain line) on a malformed environment, an unreadable keys file or an unwritable data dir.
- `python -m qr_llm_evals.preflight` runs the same checks without binding: config, data dir, keys file (or edge probe), registry, engine reachability (non-fatal). Exit 0 / 1 (warnings) / 2 (refused).
- On boot every unfinished run on disk is flipped to `needs_keys`: a restart lost the in-memory secret bags (spec D-14); the host re-enters keys and resumes, and completed `sample_ref`s are not regenerated.
