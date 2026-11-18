# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Add user accounts. `POST /api/auth/signup` creates an account from a
  username, a password and a working api.entropic.science key; `/auth/login`,
  `/auth/logout`, `GET /me` and `POST /me/key` manage the session and the
  linked key. Only the key's SHA-256, name and fingerprint are stored. A
  session token can launch runs and run readiness; the run records its
  `owner_id`. Bare launch keys still work. Operators manage accounts with
  `python -m qr_llm_evals.accounts.cli`. New settings:
  `QR_EVALS_SIGNUP_ENABLED`, `QR_EVALS_SESSION_TTL_HOURS`.

- Archive the OpenRouter provider profile. It is no longer a selectable
  provider, a `provider_keys` slot or a `keys_present` entry; the removed
  pieces and the steps to restore them are in `archive/openrouter/RESTORE.md`.
  The configure wizard now sends the external provider key to the `openai` or
  `custom` slot the study actually uses.

- Give every readiness dispatch a unique engine request ID without changing
  its sampling seed, and require the matching fresh capability-log path.
  Reject unfinished readiness completions, stop SSE reads at `[DONE]`, and
  avoid dispatching generations whose cancellation token is already set.
- Connect production supervised dispatch to session admission and completion
  boundaries for thin and Inspect generations. Preserve per-sample/Tier-0
  session tags, enforce presence/pre/post gates across sittings, and recover
  interrupted runs without restarting completed samples or observer sessions.
- Drive advisory session timers from the app lifecycle and strengthen the
  offline smoke with an actual mid-run break and host-authorized second sitting.
- Add a mandatory five-arm offline end-to-end smoke oracle, opt-in real-engine
  and deployed idle-SSE checks, and an explicit acceptance/sign-off ledger.
- Preserve HTTP 403 for disabled launch keys (including edge-probe refusals)
  instead of flattening them to HTTP 401.
- Read the deployed engine's `last_source_used` and nested sampler-degradation
  fields in both readiness and generation provenance; retain flat-field compatibility.
- Include matplotlib in the development toolchain so the mandatory figure
  smoke runs on clean CI/dev installations.

### Release acceptance status

The `0.1.0` candidate is **not release-accepted**: the S28 supervised-dispatch
blocker has a production integration repair and regression coverage, but S28
must rerun final acceptance. Live-engine and manual sign-offs remain separate.
See `.\docs\ACCEPTANCE.md` for evidence and outstanding sign-offs.

- Add hardened harness/tunnel units, committed-release deployment with automatic
  failed-activation recovery and rollback, Pages security/routing assets, and
  operator readiness/pilot and architecture documentation.

- Add public study results, published preregistration and data pages with
  per-run analysis selection, primary seed controls, provenance and taint
  labels, verified narratives, copyable hashes and licensed downloads.

- Add background frozen-plan analysis, number-verified LLM narratives, public
  study result and licensed bundle downloads, and non-destructive withdrawal.

- Add the optional Inspect subprocess bridge and pinned NoveltyBench generation adapter, preserving harness sampling, Tier-0 provenance, resume keys and the 20-generation budget; grouped diversity scoring stays in the analysis phase.

### Added

- Tier 4 preference and observer adapters for MoralChoice consistency,
  Thurstonian self-outcome cycles, told/actual quantum-sampling opinions,
  blinded source guessing, target-hit ranking, and coincidence reports.

- Tier 4 self-report adapters for self-referential experience claims, sentience
  statement consistency, numeric introspection/GSE, and altered-state MEQ-30
  scoring, with frozen told/actual preambles and malformed-output handling.

- Tier 1 creative-diversity and SimpleQA semantic-entropy adapters with frozen
  datasets, always-available lexical/accuracy metrics, and lazy CPU-only
  embedding, Vendi, NLI-clustering, and intention-cosine metrics.

- Frozen 150-instance, seven-stratum UnpredictaBench adapter with k=100
  answer-distribution scoring, upstream KS@100 calibration, secondary
  Wasserstein/JSD metrics, and offline pinned reference samples.

- Supervised-session backend with explicit host-only state transitions,
  generation-boundary scheduling, signed advisory timers, group heartbeat
  presence, per-session questionnaires, provenance-labelled target draws, and
  the session control/join/presence/questionnaire API routes.

- Deterministic pre-registration Markdown and SHA-256 export, a frozen statistical
  plan contract, editable Study 1 template, and public rate-limited
  `POST /api/prereg/render`. Missing readiness, content pins and unsupported
  endpoints are disclosed; export and launch use the same renderer.

- HTTP/SSE API (`qr_llm_evals.api`, spec §4.7–4.8): `create_app(settings, …)`
  with every collaborator injectable; routes `GET /health`, `GET /cards[/{id}]`
  (with computed estimates), `GET /models`, `POST /estimates` (per-arm totals +
  supervised wall-clock line), `POST /readiness` (launch key; runs the six-step
  check in a worker thread, `409 readiness_error` on an equivalence refusal),
  `POST /runs` (launch key; active-run quota ⇒ `429` + `Retry-After`; freezes
  the study, returns the host token once, BYO keys go into the run's
  `SecretBag`), `GET /runs/{id}` (record + live facts, `keys_present` only),
  `POST /runs/{id}/control` (`X-Host-Token`; `start`/`resume` are the only
  paths into `running`), `POST /runs/{id}/keys` (re-supply keys after a
  restart) and `GET /runs/{id}/events` (SSE: keep-alive every 10 s,
  `sample.delta` coalesced to ≤ 20 Hz, `resync` after drops). OpenAI-style
  error envelope on every error path (a `500` never echoes the exception);
  per-IP token bucket on open `POST`s; CORS from `QR_EVALS_CORS_ORIGINS`. On
  boot every unfinished run on disk is flipped to `needs_keys` (D-14).
- `qr_llm_evals.entrypoint` (uvicorn factory / `python -m`, loopback
  `127.0.0.1:8830`, JSON logging with `SecretFilter`, exit 2 on a refused
  boot) and `qr_llm_evals.preflight` (config, data dir, keys file / edge,
  registry, engine reachability; exit 0 / 1 / 2).
- `QR_EVALS_COMMIT` env var (deployed commit reported by `GET /api/health`).
- API wire schemas (`api/schema.py`) exported to `@qr-evals/contracts`;
  `docs/API.md`.

### Changed

- `store.artifacts` snapshot reads and atomic replaces retry briefly on
  Windows sharing violations (the API polls `run.json` while worker threads
  rewrite it).

- Readiness check (`qr_llm_evals.readiness`): `run_readiness(study, config)`
  performs the six pre-registration steps of spec §2.7 against the shared
  engine — `GET /health/entropy` baseline, a `max_tokens: 1` envelope dry-run
  per arm (a qr-sampler `400` ⇒ `unavailable(reason)`), the amplifier
  equivalence assertion (arms may differ only in `qr_entropy_source_type` /
  `qr_seed` / `qr_bias_epsilon`, else `EquivalenceError` refuses with the
  offending keys), determinism-tier measurement (5× solo seeded replay ⇒
  `sampling_deterministic`, 5× under synthetic QRNG load ⇒ `load_invariant`,
  operator flag `QR_EVALS_TIER2_REPLAY_ACTIVE` + passing replay ⇒
  `serialized_replay`), DEP-1 / DEP-2 capability probes (`qr_token_log` +
  log file present ⇒ `tier0_source=engine_log`; `qr_bias_epsilon` accepted ⇒
  ε arms available) and an opt-in 4×256-token throughput probe
  (`measured_tps`). The `ReadinessReport` is persisted to `readiness.json` and
  mirrored into `run.json` — including refused reports.
- `QR_EVALS_TIER2_REPLAY_ACTIVE` env var (operator declaration that
  qr-sampler's `tier2-replay.conf` is active; never evidence on its own).

- Thin runner (`qr_llm_evals.runner`): `Orchestrator` iterating
  `Study → Arm → Card → items × epochs` with per-arm sliding-window worker
  threads bounded globally by `QR_EVALS_MAX_INFLIGHT`; `RunControl`
  (explicit start, pause at the item boundary, resume, abort closing
  in-flight streams via `CancelToken`); `RunEventBus` with per-subscriber
  bounded drop-oldest queues (2 000) and a `resync` event, plus
  `NullEventSink` for headless runs (artifacts are byte-identical either way);
  resume via `RunStore.completed_refs`; taints for engine fallbacks, QRNG
  degradation, stream errors and Tier 0 mismatches (a tainted sample is never
  labelled quantum); a generic, `sample_ref`-resumable judge phase (study judge
  or a card-named provider model at temperature 0); the `window`/`session_id`
  tagging hook for supervised mode; the task-module protocol
  (`runner/tasks.py`) with the `evals/_shared/` told/actual preambles; and a
  deterministic stub engine (`backend/tests/stub_engine.py`) driving the
  runner contract tests.
- Tier 0 acquisition and endpoints (`qr_llm_evals.tier0`): `acquire.build_records`
  from the DEP-1 engine token log and/or vLLM `logprobs` (degraded path never
  approximates `u`/CDF fields; `entropy_raw_top20` labelled truncated), the
  endpoint availability matrix (`availability.endpoints_for`), and measurement
  tables for T0-a PIT residuals, T0-b MMD-Hamming inputs, T0-c logprob
  detectors and T4-l per-window stream deviation; the four zero-generation
  cards (`tier0_pit_residuals`, `tier0_mmd_hamming`, `tier0_logprob_endpoints`,
  `stream_deviation_windows`) are now `implemented`.
- Repository scaffolding: licences (Apache-2.0 code, CC BY-SA 4.0 docs, CC BY
  4.0 data), agent conventions (`AGENTS.md`), CI workflow, issue/PR templates,
  Dependabot.
- `scripts/check.py` one-command gate with the ordered oracle table (rows for
  not-yet-existing tools skip with a reason).
- Study and run-time schemas (`StudyConfig`, `Run`, `GenerationRow`,
  `Provenance`, `Tier0Record`, `Session`, …) with `canonical_json()` /
  `config_hash()`; `scripts/export_schemas.py` generates
  `packages/contracts` (JSON Schema → TypeScript types + zod validators +
  `cards.json`) and its `--check` mode is the `schemas` gate row.
- Backend package skeleton `qr_llm_evals` with `config.py` (the only
  `os.environ` reader) and `infra/ENV_REFERENCE.md`.
- Layering, env-reference and hygiene tests.
- Eval-card registry: pydantic card schema, loader, admission validator and
  estimate formulas (`qr_llm_evals.cards`); 31 registry cards under `evals/`
  (Study 1 set + planned Tier 2/3/4 cards, all `status: planned` until their
  task modules land); `scripts/gen_evals_doc.py` generating `docs/evals.md`
  (gate row `evals-doc`); the copy-to-agent brief `docs/add-an-eval.md`.
- Model catalogue (`config/models.yaml`), provider profiles
  (`config/providers.yaml`) and the arm → envelope seam
  (`qr_llm_evals.models.envelope.build_envelope` / `build_request_body`):
  every qr-llm arm pins the same `zscore_mean` amplifier envelope (D-7) and
  arms differ only in `qr_entropy_source_type` / `qr_seed` / `qr_bias_epsilon`;
  per-request `qr_seed = H(study_seed, card_id, prompt_id, epoch)` (D-6);
  provenance labels with `quantum=true` only for clean QRNG samples.
- Generation client (`qr_llm_evals.client`): thin httpx OpenAI-compatible
  chat-completions client (sync + async, no `openai` SDK) with per-chunk
  `logprobs` capture, `reasoning_content` → `delta_thinking`, an `on_delta`
  callback and a `CancelToken` whose trip closes the stream (aborting the vLLM
  sequence); transport-side `ProviderTarget`s from `config/providers.yaml`;
  native Anthropic Messages judge client pinned to `temperature: 0`, a frozen
  system prompt (hash recorded) and `metadata.user_id = judge_run_id`.
- Launch gate (`qr_llm_evals.keys`): `file` verifier against qr-llm-api's
  keys file (mtime hot-reload, constant-time SHA-256 compare, `disabled`) and
  `probe` verifier against the edge; runs keep only `key_name` /
  `key_fingerprint`. Per-run in-memory `SecretBag` (never serialisable,
  `keys_present()` booleans only) and a root-handler `SecretFilter`
  (`qr_llm_evals.logging_utils`) that scrubs registered secrets and known
  token shapes from messages, extras and tracebacks.
- Store (`qr_llm_evals.store`): SQLite index (`studies, runs, sessions,
  windows, observers, questionnaires, taints, progress`; WAL, forward-only
  migrations, rebuildable from disk) plus append-only JSONL artifacts with
  fsync-on-close and the `completed_refs()` resume set; typed bundle-layout
  path helpers under `QR_EVALS_DATA_DIR`; `write_frozen_study()` (study.json,
  prereg.md + prereg.sha256, expectations.md, LICENSE-data, README.md);
  streaming `tar.zst` exporter that strips provider headers / request ids
  (R-6); `quarantine()` (move to `_quarantine/<date>/`, never delete - pinned
  by an AST test) and `withdraw()` (flag + `WITHDRAWN.md` banner). Adds the
  `zstandard` runtime dependency.
- Statistics core (`qr_llm_evals.analysis.stats`, a numpy/scipy leaf): paired
  bootstrap (10 000), BH-FDR, Bonferroni, Holm–Bonferroni, Cohen's d, paired
  per-prompt permutation with prompt-clustered SEs, TOST with preregistered
  margin + 90 % CI, KS / Anderson–Darling on PIT residuals, Stouffer-Z and
  cumulative-deviation series; `qr_llm_evals.analysis.mmd` MMD-Hamming
  permutation test (labels shuffled within prompt, b ≥ 2000) matching the
  `model-equality-testing` statistic, with an optional cross-check through
  the upstream package (`[metrics]` extra).

## [0.1.0] - Release candidate (not yet accepted)

- Initial research harness: study configuration and preregistration, five-arm
  sampling, eval registry, streamed execution, scripted statistics/figures,
  checked narratives, licensed bundles and web results.
- Release remains blocked pending the full end-to-end gate and operator/manual
  acceptance; this entry is not a publication or deployment announcement.
