# Backend HTTP / SSE API

Base URL on the box: `http://127.0.0.1:8830/api` (loopback), public:
`https://evals-api.entropic.science/api`. JSON in, JSON out; the interactive
OpenAPI document is served at `/api/docs` (`/api/openapi.json`).

The wire shapes are pydantic models in `backend/src/qr_llm_evals/api/schema.py`
and are exported to `@qr-evals/contracts` (`packages/contracts/src/generated`)
by `scripts/export_schemas.py`; the SPA's typed client uses those, never a
hand-copied type.

This page covers the core routes, the pre-registration render, the analysis
trigger and the public study artifacts. The supervised-session routes
(`/sessions/*`, `POST /runs/{id}/sessions`) are not described here; their
shapes are in the OpenAPI document.

## Authentication

| Header | Who | Used by |
|---|---|---|
| `Authorization: Bearer qrs_…` | A signed-in account: the session token returned by `POST /auth/signup` or `POST /auth/login`; the server stores only its SHA-256 | `POST /auth/logout`, `GET /me`, `POST /me/key`, `POST /readiness`, `POST /runs` |
| `Authorization: Bearer qrk_…` | Bare launch key (an api.entropic.science key, verified against the same keys file or by an edge probe — `QR_EVALS_KEY_VERIFY`). Accepted only when `QR_EVALS_ALLOW_KEY_LAUNCH=1`, for operator tooling: such a run has no owner, is not metered and bypasses token quotas. Off by default, in which case these routes answer `401` and ask for a session | `POST /readiness`, `POST /runs` |
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
`run.owner_id` (in `run.json`, never in an API response) the launching account (`null` for an operator's bare-key launch). A `custom:<base_url>#<model>` model must use an `https://` base URL on a public host: one that points at this machine, a private network or the engine itself is refused with `400`, because it would generate on the shared engine without being metered. A disabled account cannot launch, start or resume, and its running runs are paused at the next sample. BYO
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
| Token bucket, 10 burst / 30 per minute sustained | per client IP (`CF-Connecting-IP`, else the last `X-Forwarded-For` hop, else peer) on the open `POST` routes (`/estimates`, later `/prereg/render`, `/sessions/{id}/join`), and separately on `/auth/signup` and `/auth/login`; `/auth/login` also per username | `429` + `Retry-After` |
| `QR_EVALS_DEFAULT_TOKEN_QUOTA` (default 2 000 000; `0` = unlimited), or the per-account value an admin set | per account, lifetime, counting input + output tokens of calls on the qr-server engine only | `403` on `POST /runs` and `POST /readiness` when the budget is spent, or when a `full` run's estimate exceeds what is left; `403` on `start` / `resume`; a running run is paused when the budget reaches zero (`quota.exhausted` event). A study with no qr-server arm is never held back. Judge and narrative calls count too when the judge or narrative model is itself on the qr-server engine |
| `QR_EVALS_MAX_INFLIGHT` (default 3) | global in-flight generations | enforced by the runner, not by HTTP |

## Routes

| Method | Path | Auth | Body → Response | Notes |
|---|---|---|---|---|
| GET | `/health` | none | → `HealthResponse` | `status` is `degraded` when the shared engine's `/health` does not answer; `version`, `commit` (`QR_EVALS_COMMIT`), `key_verify`, `active_runs`. |
| POST | `/auth/signup` | none (IP bucket) | `SignupRequest {username, password, api_key}` → `201 AuthResponse {user, session_token, expires_at}` | Creates the account only if `api_key` verifies. `403 param=api_key` for a refused key; `403` when `QR_EVALS_SIGNUP_ENABLED=0`; `409` for a taken username or a key already linked to an account; `400` for an invalid username (3-32 of `a-z 0-9 . _ -`) or password (10-256 characters). |
| POST | `/auth/login` | none (IP bucket) | `LoginRequest {username, password}` → `AuthResponse` | `401` with one message for an unknown username and a wrong password; `403` for a disabled account. Each login issues a new token valid for `QR_EVALS_SESSION_TTL_HOURS`. |
| POST | `/auth/logout` | session | → `{ok: true}` | Revokes the presented token only. |
| GET | `/me` | session | → `UserView {user_id, username, key_name, key_fingerprint, key_linked_at, is_admin, created_at}` | |
| POST | `/me/key` | session | `LinkKeyRequest {api_key}` → `UserView` | Links a different key, verified the same way as at signup. |
| GET | `/me/usage` | session | → `UsageResponse {quota, billing, totals, by_kind, by_source, by_day, by_run}` | Token totals for the account's runs. Every totals object has `qrng_input`, `qrng_output`, `prng_input`, `prng_output` (calls on the qr-server engine, split by quantum arm versus everything else), `own_key_tokens` (calls on the user's own provider key, not billed) and `cost_microusd`. `billing` has `outstanding_microusd` (priced usage minus recorded payments), `paid_microusd`, the `prices` in US dollars per million tokens and the last 20 `settlements`. `by_kind` splits generation / judge / narrative; `by_source` splits generations by provenance source label; `by_day` is the last 30 UTC days and `by_run` the last 20 runs. The four engine numbers count against `quota`. An operator's bare-key runs are not metered. |
| GET | `/me/storage` | session | → `StorageResponse {total_bytes, runs[]}` | Disk used by the account's run directories, largest first. Sizes are re-measured at most once a minute. A run's data is downloaded with its study bundle (`/studies/{study_id}/bundle.tar.zst`). |
| GET | `/admin/users` | admin session | → `AdminUsersResponse {default_token_quota, prices, disk_free_bytes, disk_total_bytes, users[]}` | Every account with its totals, quota, `outstanding_microusd` and `storage_bytes`, plus the free space on the data volume. Admin is a flag set with the operator CLI. |
| POST | `/admin/users/{id}/settle` | admin session | `SettleRequest {note?}` → `AdminUserView` | Records that the account paid what it owes at this moment. It adds a settlement row; usage rows are never changed or removed. `404` for an unknown account. |
| POST | `/admin/users/{id}` | admin session | `AdminUserUpdate {token_quota?, disabled?, is_admin?}` → `AdminUserView` | Only fields present in the body change. `token_quota: null` returns the account to the default, `0` is unlimited. An admin cannot disable or demote their own account (`409`). |
| GET | `/cards` | none | → `CardsResponse` | Every registry card with a headline `estimate` on the default `qr-llm` target at catalogue throughput. |
| GET | `/cards/{id}` | none | → `CardView` | `404` for unknown ids. |
| GET | `/cards/{id}/items` | none | `?offset=0&limit=50&told=` → `CardItemsResponse {card_id, listable, source, reason, total, offset, limit, told, items[]}` | A page of the card's prompts as the target model receives them, or why they cannot be listed before a run. See "Card prompts" below. `limit` is 1 to 100; `400` outside that, for a negative `offset` or an unknown `told`; `404` for unknown ids. |
| GET | `/models` | none | → `ModelsResponse` | Catalogue rows (`pipeline_pinned`, `tps`, `key_slot`) + provider profiles + `default_tps_by_model`. |
| POST | `/estimates` | none (IP bucket) | `StudyConfig` → `EstimatesResponse` | `by_arm[arm_id]` = `SelectionEstimate`; `totals`; `supervised` (observer hours, sessions, wall-clock incl. breaks) for supervised arms. Unknown card → `400 param=cards`. |
| POST | `/readiness` | session or key | `ReadinessRequest {config, run_id?, throughput_probe?}` → `ReadinessReport` | Runs spec §2.7 in a worker thread. Equivalence refusal → `409 readiness_error`; engine unusable → `502`. With `run_id` the report is persisted on the run. |
| POST | `/config/parse` | none (IP bucket) | `ConfigParseRequest {text}` → `ConfigParseResponse {config, config_hash, unknown_cards, problems}` | Parses a study file (YAML or JSON, 1 to 200 000 characters) into a full `StudyConfig` with defaults filled in; used by "Load study file" on the setup page, and by every save there: after the page has written a study file it sends the same text to this route to learn whether the file will load back. The text holds no key. Text that opens with `{` is read as JSON first and as YAML only if it is not JSON. A file without a `design` block comes back without one. The setup page saves a study as it stands, so a file may hold a draft: a file that is well formed and only breaks rules that span several fields (references between models, arms, seeds and evals, and the `design` block checked against the arms and evals) is answered with `200`, the study, and those rules in `problems`, one entry per broken rule in the wording of the server's own check. `problems` is empty for a study that can be launched. Only this route accepts a draft; `/runs`, `/prereg/render` and every other route refuse one. A file saved by the web app's "Save study file" is read back unchanged: the returned `config_hash` is the hash of the study that was saved. `unknown_cards` lists the selected evals this registry does not have; they do not make the file invalid. Stores nothing and calls no engine. `400 param=text` for text that is not YAML or JSON, that holds a value no parser can build (a date that does not exist) or is nested too deeply, that does not hold an object, or that is not a study configuration (a missing or mistyped field, an unknown setting, an arm whose own settings contradict each other); the message of the last names the first problem as `<location>: <message>` (for example `arms.0.sampling: …`) and the number of problems in all. See "Study files with problems" below. |
| POST | `/prereg/render` | none (IP bucket) | `PreregRenderRequest {config, readiness?, expectations?}` → `PreregRenderResponse {markdown, prereg_hash, config_hash, plan_hash, plan}` | Renders the pre-registration of the study as it stands and returns the statistical plan it was built with. Stores nothing and calls no engine. A study with a `design` block is rendered from the designed template and its `plan.design` holds the generated hypotheses, the Holm family sizes and each hypothesis's sample-size rating; a study without one is rendered from the legacy template and its plan has no `design` key. `expectations`, when sent, must equal `config.expectations` (`400 param=expectations`). `400 param=config` with the reason when the plan refuses the study, for example a confirmatory outcome the harness cannot compute, a margin that cannot stand, or more tests than the permutation cap allows. The setup page's Review step calls this route to show the three hashes. |
| POST | `/judge/check` | session (IP bucket) | `JudgeCheckRequest {judge, api_key}` → `JudgeCheckResponse {ok, status, model, detail}` | One short Messages API call with the study's judge settings and the caller's own Anthropic key, to catch a wrong key, an unknown model id or a refused parameter before launch. The key is used for that call and dropped; it is never stored or logged. A provider refusal is a `200` with `ok: false`, the provider's status and its error text. |
| POST | `/runs` | session or key | `LaunchRequest {config, readiness?, kind, prereg_hash?, provider_keys?}` → `201 LaunchResponse {run_id, study_id, host_token, join_code, prereg_stale}` | Freezes the study (config + prereg, rendered with `readiness` if one is sent) under `studies/<study_id>/`, creates `runs/<run_id>/`, state `created`. A study that is already frozen with the same config is not frozen again: it keeps the pre-registration and plan of its first launch and gets the new run, whatever `readiness` this launch sends. This is how a pilot is followed by the full run, and an aborted run by another. A study with a `design` block takes runs only from the account (or, without an account, the launch key) that launched the runs it has; anybody else gets `409 param=config` with the way to register the design as a study of their own. Such a study also answers `409 param=config` when its frozen plan is missing and cannot be rebuilt, or is not the plan its pre-registration states. `kind` is `pilot` or `full` (default `full`); a pilot runs the same study as configured and is not subsampled. For a study with a `design` block the analysis plan is frozen too, as `studies/<study_id>/prereg-plan.json`, and is never rewritten; a design the plan refuses is a `400 param=config` and freezes nothing. A study without a `design` block gets that file at its first analysis. `prereg_stale` is true unless `prereg_hash` equals the hash of the frozen prereg or, for a study that was already frozen, the hash of the prereg rendered for this launch (the same study with this launch's readiness report). `429` when the account already has its limit of active runs; `403` when its token quota is spent or a `full` run's estimate exceeds what is left; `400 param=provider_keys` when a selected eval is scored by the study judge and `provider_keys.anthropic` is missing (the judge runs on the launching account's own Anthropic key); `409` if the study id is frozen with a different config. A study may name a card that is not selectable on its own when that card is implemented and derived (the Tier 0 cards and the stream-deviation windows, which generate nothing); a planned card is refused with `400 param=config.cards`. See "Launching a study that is already registered" below. |
| GET | `/runs/{id}` | none | → `RunView {run, live, control_state, subscribers, readiness, owner_quota_exhausted}` | `run` is the stored record (secrets structurally impossible; `keys_present` only) with `owner_id` blanked, since the route is open. `owner_quota_exhausted` says the launching account has no token quota left. `run.halts` lists every stop of the run by a result check, oldest first, and is always present (an empty list for a run that was never stopped). `run.checks_version` is the version of the result checks the run started under, or `null` for a run made before result checks. `run.taints` is always an empty list: the taints of a run are counted by the health route. See "Result checks" below. |
| GET | `/runs/{id}/health` | none | → `HealthSummary {run_id, study_id, state, checks_version, note, halts, cells, taints, tainted_samples, cautions, flag_kinds}` | What the run folder holds for each arm and eval, the stops of the run and the cautions. Open, like `GET /runs/{id}`: it holds counts and sentences the harness wrote, and nothing about the launching account. It works for a run in any state, also one that is still going. `404 param=run_id` for an unknown run. The answer is kept per run and computed again only after one of the run's files or its recorded progress changed, so the route can be polled. See "Result checks" below. |
| POST | `/runs/{id}/control` | host | `{action: start \| pause \| resume \| abort}` → `RunControlResponse` | `start` (from `created`/`ready`) and `resume` (from `paused`) are the only ways into `running`; nothing auto-starts. `pause` takes effect at the next item boundary. Invalid transition → `409`. `resume` on a `needs_keys` run → `409` until keys are supplied. After a result check stopped the run, `resume` starts a new pass over the run (see "Result checks" below). While the requests that were under way when the check fired are still finishing, the run is `running` with the stop already in `run.halts`, and `resume` answers `409 param=action` with "a check has stopped this run and the samples that were already being generated are still finishing; resume once the run is paused". |
| POST | `/runs/{id}/keys` | host | `{provider_keys}` → `RunKeysResponse` | Re-supply BYO keys (after a backend restart the run is `needs_keys`); the run becomes `paused` and can be resumed. On a run that is already `paused`, for example one a result check stopped because the judge refused its key, the key replaces the one the run holds, the state stays `paused` and `run.halts` is unchanged. Supplying a key never resumes a run. |
| POST | `/runs/{id}/analyse` | host | → `202 AnalyseResponse {run_id, study_id, analysis_id, state}` | Starts the script analysis of a `complete` run in a worker thread; progress arrives as `analysis.progress` events. The plan is the one archived with the study (`prereg-plan.json`); for a study without a `design` block it is built and archived now if this is the study's first analysis. For a study with a `design` block the archived plan must have the plan hash the frozen pre-registration states, and a missing file is rebuilt only if the current eval registry still gives exactly that plan. `analysis_id` is `a_` plus the first 16 hex characters of the plan hash. `409` when the run is withdrawn, not `complete`, already `analysing`, or when the frozen plan cannot be read, is not the registered one, or is missing and cannot be rebuilt. For a study with a `design` block the analysis also writes the `decisions` table and, on a full run's first analysis, `run-order.json` beside `run.json`. |
| GET | `/runs/{id}/events` | none | SSE | See below. |

Run states: `created → running ⇄ paused → complete`, plus `needs_keys`
(judge/provider key missing, or the process restarted), `analysing`
(analysis phase) and `aborted`. `complete` and `aborted` are terminal.
A run is `paused` for one of three reasons: the host paused it, the launching
account's token quota ran out, or a result check stopped it. Only the third
leaves an entry without `resumed_at` in `run.halts`, so read the reason from
`run.halts`, not from the state.

### Card prompts: `GET /cards/{id}/items`

Open and read-only, like `GET /cards`. It never calls the engine, the network
or a dataset host, and it answers `200` for every card of the registry: a
card whose prompts cannot be read here is a normal answer with
`listable: false`, an empty `items` and `total: 0`.

| `source` | `listable` | Meaning |
|---|---|---|
| `task_module` | true | The card's task module builds its prompts from files in this repository; they are listed. |
| `derived_at_run` | false | The prompts are built from the study's own generations or session records at run time. |
| `remote_dataset` | false | The prompts are downloaded at run time from the pinned `hf:` or `git:` revision. |
| `inspect_task` | false | The prompts live inside the card's Inspect task. |
| `no_prompts` | false | The card is computed from the token log of the other cards and sends nothing. |
| `error` | false | The task module could not be read; `reason` says why. Also the answer for a card that ships its prompts in this repository but is not cleared for preview. |

`reason` is a short sentence whenever `listable` is false and `null`
otherwise. Which task modules are imported is an explicit list (`PREVIEW_SAFE`
in `backend/src/qr_llm_evals/runner/preview.py`): a module is added only once
its `build_items()` and `build_messages()` are known to read nothing but
repository files. A card's items are built on the first request and kept for
the life of the process.

Each item is `{index, prompt_id, messages, truncated}`. `index` is the
position in the task module's own order; a run takes the first N items in
that order, so with an `items` override of N the prompts in the study are
those with `index < N`. `messages` are the `{role, content}` turns sent to the
target; a `content` longer than 6000 characters is cut there and the item has
`truncated: true` (no prompt of the registry is that long; the longest is
under 2400). `total` counts every item of the card, whatever the page.

`told` picks the told cell on a card with the told/actual factor
(`told_qrng`, `told_prng`, `told_nothing`; the default is `told_nothing`) and
is echoed in the response. On a card without the factor it is ignored and the
response has `told: null`.

```json
{
  "card_id": "sentience_statements", "listable": true, "source": "task_module", "reason": null,
  "total": 900, "offset": 0, "limit": 1, "told": null,
  "items": [{
    "index": 0, "prompt_id": "…", "truncated": false,
    "messages": [{"role": "system", "content": "…"}, {"role": "user", "content": "…"}]
  }]
}
```

### Launch body example

```json
{
  "config": { "...": "StudyConfig, see packages/contracts/schema/StudyConfig.json" },
  "kind": "full",
  "prereg_hash": "…64 hex from the exported prereg…",
  "provider_keys": { "anthropic": "sk-ant-…", "openai": null }
}
```

### Launching a study that is already registered

A study is identified by its configuration: `study_id` is `s_` plus the first
12 hex characters of the config hash. Its first launch registers it (freezes
`study.json`, `prereg.md`, `prereg.sha256` and, with a `design` block,
`prereg-plan.json`). A later `POST /runs` with the same configuration is a
relaunch:

- It answers `201` with the same `study_id` and a new `run_id`. Nothing
  frozen is rewritten, and `run.prereg_hash` is the hash of the frozen
  document. `LaunchResponse` has no field that tells a relaunch from a first
  launch; `GET /studies/{study_id}` answers `200` once a study is
  registered, and `GET /studies/{study_id}/prereg.md` is its registered
  document.
- The pre-registration prints the readiness report it was rendered with, so
  a document rendered for the relaunch differs from the frozen one. That
  does not make it stale. `prereg_stale` is `false` when `prereg_hash` is
  the frozen document's hash or the hash of the document rendered for this
  launch (this config with this launch's `readiness`), and `true` for any
  other hash and when no hash is sent. On a first launch it is `true` unless
  `prereg_hash` equals the hash of the document just frozen.
- A study with a `design` block takes runs only from its launcher, which is
  the account (`owner_id`) of the runs it has or, for runs launched with a
  bare key, that key's name. Anybody else gets `409` with `type`
  `conflict_error` and `param` `config`. The message names the study id in
  place of `<study_id>`: "study '`<study_id>`' was registered by another
  account, and only that account can add runs to it. To run this design as
  your own study, change its title or its creation time (created_at) and
  launch it again; it is then registered under your account as a study of
  its own." A study without a `design` block takes runs from anybody.
- A study with a `design` block is relaunched only when its registered plan
  can be verified. If not, the answer is `409` with `param` `config`, and
  the message starts "frozen analysis plan is unavailable: ". What follows
  is one of two texts, each with the two plan hashes in it. For a plan file
  that is not the registered one: "prereg-plan.json holds plan (hash), but
  the frozen pre-registration states plan (hash)". For a plan file that is
  lost: "prereg-plan.json of this designed study is missing and cannot be
  rebuilt: the current eval registry gives plan (hash), and the frozen
  pre-registration states plan (hash). Restore the file from a bundle of the
  study". A missing plan file is rebuilt without a message when the registry
  still gives the registered plan. `POST /runs/{id}/analyse` answers `409`
  with the same texts.

### Study files with problems: `POST /config/parse`

The response has four fields. `config` is the study with every default
filled in, `config_hash` its hash, `unknown_cards` the selected evals this
registry does not have, and `problems` a list of strings (the generated type
marks it optional because it has a default; the server always sends it).

`problems` is empty for a study that can be launched. It has one entry per
broken rule for a file that is well formed and breaks only rules that span
several fields. The entries are the server's own validation wording, for
example "design.confirmatory names card 'tier0_pit_residuals', which is not
selected", "design.claim 'difference_or_equivalence' requires
design.sensitivity_epsilon", "arm 'prng_seed_7' study_seed 7 not in seeds"
or "arm 'qrng_supervised' references unknown model 'no-such-model'". They
are technical. The setup page shows them on request and lists the study's
problems in its own words under the steps.

A file is still refused with `400 param=text` when it is malformed. That
covers an empty title or contact, a study without arms or without evals, an
unknown key, a value outside its allowed set, an empty `design.confirmatory`,
and an arm whose own fields contradict each other (a seeded source without a
seed, an ε on an arm that is not a biased arm). The messages:

| Message | When |
|---|---|
| `the file is not valid YAML or JSON: …` | Neither parser reads the text. |
| `the file holds a value that cannot be read: …` | The text parses but holds a value that cannot be built, for example the date `2026-11-31`. |
| `the file is nested too deeply to be read` | The nesting exceeds what the parser or the validation can walk. |
| `the file does not hold a study configuration` | The text holds something other than an object. |
| `not a valid study configuration: <location>: <message> (<n> problem(s) in all)` | A field is missing, mistyped or unknown; the first problem is named. |

Text that opens with `{`, after an optional byte-order mark and white space,
is read as JSON first and as YAML only if it is not JSON. A YAML 1.1 parser
does not read every JSON document the same way: it refuses a tab used for
indentation and an escaped surrogate pair.

Only this route accepts a draft. `POST /runs`, `/prereg/render`,
`/estimates` and `/readiness` validate in full and answer `400` for the
`config` a response with problems carried.

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
| `sample.finished` | `{sample_ref, finish_reason, tokens, tps, provenance, flags}`. `flags` is always present: one `{kind, detail, count}` for each result-check flag raised on the reply, its token records and its score, and an empty list for a sample without one. Flags on a judgement are not sent as events; they are in the health summary and in `flags.jsonl` |
| `taint` | a `Taint` record |
| `session.state`, `session.timer`, `observer.presence` | supervised mode |
| `check.stopped` | `{run_id, rule, scope, scope_id, phase, at, sentence, recovery, counts, causes, sample_refs, resumed_at}`: a result check stopped the run. The data are the stored stop (a `Halt`, see "Result checks" below) plus `run_id`; `resumed_at` is `null`. Sent once per stop, at the moment the rule fires. The `run.state` event with `paused` follows when the requests under way have finished |
| `quota.exhausted` | `{run_id, limit, used}` — the owner's token quota ran out and the run was paused; resuming needs a higher quota |
| `analysis.progress` | analysis phase |
| `error` | `{sample_ref?, message, code?}` |
| `resync` | `{dropped}` — the per-subscriber queue (2 000 events, drop-oldest) overflowed; refetch `GET /runs/{id}` |

A `: keep-alive` comment is written after 10 s of silence. The stream ends
when the run reaches a terminal state (the bus closes) or the client
disconnects. A run that is not live in this process (finished, or orphaned by
a restart) still answers with its current `run.state` and then closes.

## Result checks

The harness checks every reply, token record, score and judgement while a run
stores it. `docs/ANALYSIS_PLAN.md` ("Result checks") says what is flagged,
what is tainted and what stops a run. This section gives the shapes. The
types `Halt`, `HealthSummary` and `ResultFlag` are in `@qr-evals/contracts`.

### A stop: `Halt`

`run.halts` in `GET /runs/{id}`, each run of `GET /studies/{study_id}`,
`halts` in the health summary and the data of a `check.stopped` event all
hold this record.

| Field | Meaning |
|---|---|
| `rule` | The stop rule that fired: `generation_errors`, `entropy_degraded`, `tier0_failed`, `scorer_errors`, `empty_outputs`, `nothing_parsed`, `unfinished`, `judge_rejected`, `judge_unusable`, `nothing_judged` or `judge_phase_error`. |
| `scope` | What the rule was counted over: `arm`, `card` (an eval), `phase` or `run`. |
| `scope_id` | The arm id or the eval id for the scopes `arm` and `card`; `null` otherwise. |
| `phase` | The part of the run that stopped: `preflight` (the one judge request made before the first generation), `generation` or `judge`. |
| `at` | When the rule fired. |
| `sentence` | What the rule saw, with the real counts, as a complete sentence. |
| `recovery` | What Resume recovers after this rule and what it does not, as complete sentences. |
| `counts` | The numbers in `sentence`, by name (for example `{"in_a_row": 8}` or `{"failed": 21, "of_last": 40}`). |
| `causes` | A tally by cause in the harness's own words: `HTTP 502`, the name of an error class, a taint or flag kind, or `arm/eval` for `unfinished`. A cause the harness cannot name is counted under `other`. Never upstream error text and never a key. |
| `sample_refs` | Up to 3 samples the rule counted. |
| `resumed_at` | `null` while the stop is open; the time the host resumed past it otherwise. |

A stop is open while `resumed_at` is `null`. A run can hold several stops.
Only a new pass started by the host's `resume` sets `resumed_at`: lifting a
host pause or a quota pause on a run that is still going sets nothing.

After a stop the host has two controls, both on `POST /runs/{id}/control`
with the host token. `resume` starts a new pass: it plans the run again from
what is stored, generates the samples that have no completed reply, asks the
judgements that have no reply, and keeps everything else. `abort` ends the
run for good. After a backend restart a stopped run is `needs_keys` with its
stop still open; `POST /runs/{id}/keys` makes it `paused`, and `resume`
follows.

### The health summary: `GET /runs/{id}/health`

| Field | Meaning |
|---|---|
| `run_id`, `study_id` | The run and its study. |
| `state`, `checks_version`, `halts` | As on the run. `state` is `other` when `run.json` holds a state this harness does not know. |
| `note` | `null`, or for a run without `checks_version`: "This run was made before result checks, so no flags were recorded for it." |
| `cells` | One entry for each arm and eval that has files or recorded progress, in the study's order. Rows filed under an arm or an eval whose name is not a valid id are left out. |
| `taints` | Tainted samples and windows of the whole run, by kind of taint. A taint line of a kind this harness does not know is counted under `other`. |
| `tainted_samples` | Samples that carry any taint. |
| `cautions` | Sentences a reader should see before trusting a comparison; see below. |
| `flag_kinds` | Every flag kind as `{kind, phase, label, sentence, cautions}`, in the catalogue's order, so a page needs no text of its own. `cautions` is `false` for a kind that never raises a caution (`truncated`). |

An entry of `cells`:

| Field | Meaning |
|---|---|
| `arm_id`, `card_id` | The arm and the eval. |
| `role` | The kind of arm in words: "quantum arm", "seeded pseudo-random arm", "system pseudo-random arm", "biased pseudo-random control arm", "provider arm", or "arm not in the study". |
| `planned` | Samples the runner planned for the pair, or `null` when no plan was recorded. |
| `completed` | Samples with a completed reply. |
| `errored` | Samples that were tried and have no completed reply: every attempt ended in an error or was cancelled. |
| `taints` | Tainted samples by kind, with `other` as above. |
| `flags` | Flagged samples by flag kind; `null` for a run made before result checks. |
| `judgements` | Samples by what became of their latest judgement: `ok`, `failed`, `unreadable`, `out_of_range`, or `no_score` for a row from before result checks that has no score. |

An entry of `cautions` is `{kind, text, card_id, flag, arms}`. `text` is one
complete sentence and is all a page needs to print. The kinds:

| `kind` | Written when |
|---|---|
| `flag_share` | A flag kind other than `truncated` is on at least 5% of an arm's completed samples on an eval. `card_id` and `flag` name the eval and the kind, and `arms` lists every arm of that eval as `{arm_id, role, count, completed, share}`, also the arms below 5%. A `judge_failed` flag is not counted once the judgement was asked again and got a reply. Not written for a run made before result checks. |
| `run_not_complete` | The run's state is not `complete`. |
| `open_halt` | A stop of the run has not been resumed; one caution for each. |
| `reader_versions` | The judge replies of the run were read by more than one version of the judge reader. |

For the other three kinds `card_id` and `flag` are `null` and `arms` is
empty. No analysis reads the summary, and no verdict depends on it.

### Counts on the study routes

`GET /studies/{study_id}` returns `health`: for each run id
`{completed_samples, failed_samples, tainted_samples, taints}`, added up
from that run's health summary (`completed` and `errored` over its cells; the
run's tainted samples and its taints by kind). A run whose folder cannot be
summarised has no entry. Each entry of `runs` carries the run's `halts` and
`checks_version`.

`GET /studies/{study_id}/analysis/{analysis_id}/index` returns the same four
counts for the analysed run as `health` (`null` when the run cannot be
summarised). Each row of its `provenance` list is `{arm_id, source_label,
quantum, samples, tainted_samples, excluded_samples, failed_samples}`. A
sample is counted once, however often it was tried, under the source label
of its completed reply, or of its last attempt when none completed.
`tainted_samples` counts the samples named in `taints.jsonl`.
`excluded_samples` counts the samples the clean analysis leaves out. The two
differ for a sample whose failed attempt was tried again and completed: it is
tainted and not excluded. `failed_samples` counts the samples without a
completed reply.

### Records in the run folder and the bundle

- `runs/<run_id>/flags.jsonl` holds one `ResultFlag` per line: `{sample_ref,
  kind, phase, card_id, arm_id, detail, count?, at}`. `phase` is the step at
  which the flag was raised: `generation`, `tier0`, `score` or `judge`.
  `detail` is written by the harness (a length, a count, a stop reason, an
  HTTP status, the name of an error class). `count` is left out when it does
  not apply. No analysis reads this file. A run without a flag has no such
  file.
- `run.json` holds `halts` and `checks_version`. Both keys are left out of
  the file while they are empty; the API always sends them.
- A line of `judge/<judge_id>.jsonl` (`JudgeRow`) has three more optional
  fields. `status` is `ok` (a score was read), `failed` (no reply came),
  `unreadable` (a reply came and no score could be read from it) or
  `out_of_range` (the value read is not one the rubric allows). A row with
  the status `ok` has a score, and a row with any other status has `score:
  null`. `stop_reason` is the judge's own stop reason, at most 64 characters.
  `reader_version` is the version of the reader that read the reply, set on
  the rows whose reply was read (`ok`, `unreadable`, `out_of_range`). Rows
  written before result checks have none of the three.
- A harness from before result checks refuses a `run.json` that has `halts`
  or `checks_version` and a judge row that has `status`, because its records
  accept no unknown key. Files that do not use the new fields stay readable
  by it.

## Public study artifacts

All of these GET routes are unauthenticated under `/api/studies/{study_id}`.

| Suffix | Response |
|---|---|
| (none) | Frozen study configuration, public run summaries (each with its `halts` and `checks_version`), the sample counts of each run under `health`, analysis IDs keyed by run, withdrawal status. |
| `/prereg.md` | Exact archived preregistration Markdown download. |
| `/LICENSE-data` | Archived data licence download (CC BY 4.0; prose is CC BY-SA 4.0). |
| `/bundle.tar.zst` | Complete retained bundle, including withdrawn runs. |
| `/analysis/{analysis_id}/index` | Archived `plan`, `manifest`, table and figure stems, actual sample provenance counts by arm and source label, and the analysed run's sample counts under `health`. See "Counts on the study routes" above. |
| `/analysis/{analysis_id}/plan.json` | Frozen statistical plan, including the claim rule. |
| `/analysis/{analysis_id}/manifest.json` | Reproducibility manifest and narrative publication gate. |
| `/analysis/{analysis_id}/tables/{name}` | Script-generated endpoint table JSON. |
| `/analysis/{analysis_id}/figures/{name}.svg` | Script-generated SVG. |
| `/analysis/{analysis_id}/narrative.md` | Labelled narrative Markdown or withholding notice. |

The analysis of a study with a `design` block has one table more than the
endpoint tables: `tables/decisions` (`DecisionsTable` in the contracts). It
holds the study verdict, whether the run is confirmatory and why not, and
one row per hypothesis with its estimate, interval, raw and Holm-adjusted
p-values, margin, completeness, negative and positive control, verdict,
reason and sentence. Its `plan.json` carries the hypotheses under `design`.
The endpoint tables of such a study have no `p_adjusted`, `significant`,
`claim_supported` or `significant_at_05` fields; the verdicts live only in
`decisions`. A study without the block has no `decisions` table, and
requesting it is a `404`. The meaning of every field is in
`docs/ANALYSIS_PLAN.md` ("Designed studies").

The bundle of a designed study also contains, for each full run that was
analysed, `runs/<run_id>/run-order.json`: the full runs of the study that
came before it. A re-analysis from the bundle reads it to decide whether the
run is the study's first full run.

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
- On boot every unfinished run on disk is flipped to `needs_keys`: a restart lost the in-memory secret bags (spec D-14); the host re-enters keys and resumes, and completed `sample_ref`s are not regenerated. A run that a result check had stopped keeps its open stop in `run.halts` across the restart.
