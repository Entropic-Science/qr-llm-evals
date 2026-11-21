# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- The thesis analysis runs off the box. `thesis_report.py unpack` unpacks a
  study bundle downloaded from the account page (streamed, only ordinary files
  inside the target folder, never over an existing file), and `decide` and
  `pilot-check` give byte-identical output on it and on the server's run
  folder. `pilot-check` no longer needs `--plan`: a pilot runs before the lock,
  so it uses the plan of its own frozen study. The runbook and the handover
  note now say: download, then analyse locally.
- The bundle export streams large JSONL files. It used to parse a whole file
  into memory before sending it (about 2 GB for one arm's token records of a
  full thesis run, with nothing sent meanwhile); it now reads line by line,
  sends as it reads, and copies a line that holds no stripped key as it is.
  The exported rows are the same.

- An engine token log with more rows than the reply has tokens is accepted and
  cut to the reply's length. The live engine samples a few tokens after the
  one that ends the reply and discards them, and the log records every sampled
  position, so every real log is a little longer than its reply. A log that is
  shorter than the reply is still refused.

- Thesis study tools. `thesis_device_dump.py` dumps raw bytes from the entropy
  server for the device check, in requests of the size the sampler draws per
  token, and can be continued after an interruption. `thesis_report.py
  register` writes the registration text with the four hashes from a lock
  folder, and `thesis_report.py figures` draws the decision figures (effects,
  sampler, first word) from `decisions.json`. The runbook has the steps for
  filing the registration on OSF.
- `requirements-analysis.lock` pins matplotlib and statsmodels for the box, so
  a release can draw figures; `scripts/deploy.sh` already installs it when
  present.
- The nightly backup also copies the run artifacts to
  `/var/backups/qr-llm-evals/runs/`. Nothing is deleted from that copy, and a
  changed file keeps its previous version. `scripts/pull-backup.sh` pulls the
  accounts database, the configuration and, with `--runs`, every run to
  another machine and checks what it downloaded.

- Study 1 and the thesis study can be launched as written. `POST /runs` used
  to refuse every study that names a Tier 0 card, because those cards are not
  selectable on their own; a study may now name an implemented derived card.
  Planned cards are still refused.
- `studies/thesis_prompt_or_physics/thesis_launch.py` locks and launches the
  thesis study from a terminal: `lock` runs readiness and saves the
  preregistration with its four hashes, `pilot` launches with the quantum arms
  switched off, and `launch` starts the full run only if the study still
  matches the locked hashes. Passwords and keys are asked for, never given as
  arguments.
- The wizard has a "Load study file" button (YAML or JSON, parsed by the new
  `POST /config/parse`), lets an arm be the biased control with its bias, and
  its launch dialog offers pilot or full run and sends the preregistration
  hash, so a run launched from the web is no longer marked stale by default.

- The engine's per-token log is requested for real generations, not only by
  the readiness probe. When `QR_EVALS_TOKEN_LOG_DIR` is set, every generation
  on the engine sends `qr_token_log` and `qr_request_id` (the sample
  reference); qr-sampler now writes the log (its `QR_TOKEN_LOG_DIR`). The two
  keys are log controls and stay out of the sampling envelope, so envelope,
  configuration and pre-registration hashes do not change. The readiness probe
  sends the request id too, which qr-sampler requires.
- A full thesis run's analysis fits in a few gigabytes of memory. Loaded
  Tier-0 records share their field bookkeeping (`share_fields_set`): about
  0.55 KB per record instead of 2.9 KB, measured, which is about 5 GB instead
  of 26 GB for 9.8 million tokens. The log itself keeps the candidate list for
  the first token of a reply only, so a stored record is about 0.45 KB instead
  of several kilobytes. This replaces the earlier note that a full run needs
  an analysis machine with tens of gigabytes.

- The study judge defaults to the current Claude Haiku (`claude-haiku-5-5`) and
  has settings: model, longest answer (`max_tokens`), extended thinking (off by
  default) and temperature (not sent by default, because current Claude models
  reject it). They are part of the study configuration and its pre-registration
  hash, and the wizard's Judge step edits them. `JudgeSpec` changed shape, so
  configuration hashes of existing study files change; the `PIN_...` placeholder
  is gone from the templates.
- A study with judged evals cannot launch without the launching account's own
  Anthropic key (`400 param=provider_keys`); before, the run generated
  everything and then stopped for the key. `POST /judge/check` and the "Test
  the judge" button try the settings with the key first.
- Every judge call path (judge phase, Inspect grader) now takes its parameters
  from the study; the Inspect grader no longer uses a different output limit.

- Token usage is shown as four numbers: input and output tokens on the
  qr-server engine, separately for quantum arms (QRNG) and for everything
  else (PRNG). Each is priced (default $20 / $80 per million input / output
  tokens for QRNG, $5 / $20 for PRNG; `QR_EVALS_PRICE_*`) and the account and
  admin pages show what an account owes as "Outstanding". An admin marks an
  account paid from the admin page or with `accounts.cli settle`; that records
  a payment and leaves the usage history alone. The Calls / Prompt /
  Completion / Counted columns are gone. `UsageTotalsView` changed shape, and
  `accounts.sqlite` gains a `settlements` table (migration 3).
- The account page shows the disk space each run uses and asks people to
  download their finished runs; the admin page shows storage per account and
  the free space on the data volume (`GET /me/storage`).

- The thesis study's offline dry run is small by default and finishes in about
  five minutes; `--full` runs it close to the real study's size. Inside the
  simulator the store keeps artifact files open between rows; the real store
  is unchanged.

- Keep the "Prompt or Physics?" thesis study in its own folder,
  `studies/thesis_prompt_or_physics/` (template, settings, decision module,
  scripts, runbook). The `qr_llm_evals` package no longer contains or imports
  it. The decision script now requires the settings file and the hash
  registered for it (`thesis_report.py hash`, `--settings-sha256`) and refuses
  other settings; an unknown `cluster` is an error; the first-word test (H4)
  is unavailable when the logged candidates do not cover the distribution or
  disagree with the logged brackets; the model-versus-observer contrast is
  reported, not part of the decision.
- A derived card may use `k` below 10 only if it declares it under
  `exceptions` (`k below 10: ...`); the rule is no longer relaxed for every
  derived card.
- A resumed run generates with the `top_logprobs` depth it started with, even
  if the deployment setting changed, so one run never mixes depths.
- `AGENTS.md` invariant 1 now names `qr_population_mean` (biased control arms
  only) in place of `qr_bias_epsilon`.

- Ship the box's daily backup (`accounts.sqlite` and both `/etc`
  configuration directories) and the keys-file access watcher under
  `infra/`, and document them. `docs/SETUP_GUIDE.md` also gains: updating a
  box that has no GitHub access with a git bundle, issuing a key for a new
  user, restoring accounts from a backup, and deploying later releases.

- Deploy the web app as a Cloudflare Worker with static assets instead of a
  Pages project. `wrangler.jsonc` names the Worker, points at
  `apps/web/dist` and serves `index.html` for client-side routes; the
  Pages-only `_redirects` file is removed (`docs/SETUP_GUIDE.md` section 5).

- Serve `evals-api.entropic.science` through qr-llm-api's existing tunnel
  instead of a dedicated one. The harness no longer ships a cloudflared unit
  or a tunnel token file, and `scripts/deploy.sh` no longer expects the unit
  link. The hostname is added as a second public hostname on that tunnel
  (`docs/SETUP_GUIDE.md` section 4).

- Harden accounts and quotas after review. A `custom:` model must use an
  `https://` base URL on a public host, so it cannot reach the shared engine
  unmetered. A disabled account can no longer start or resume runs, and its
  running runs pause. Judge and narrative calls made on the qr-server engine
  are charged. A study with no qr-server arm is not held back by a spent
  quota. If usage cannot be recorded, the run pauses. Login is throttled per
  username as well as per address, and the address now comes from
  `CF-Connecting-IP`. `GET /api/runs/{id}` no longer returns `owner_id` and
  reports `owner_quota_exhausted`. `POST /api/readiness` needs the same
  standing as a launch.

- Document account setup for operators (`docs/SETUP_GUIDE.md`: first admin,
  quotas, backups of `accounts.sqlite`) and what an account stores
  (`docs/PRIVACY.md`).

- Add sign-in to the web app: `/login`, `/signup`, an `/account` page with
  the linked key, the token quota and usage tables, and an `/admin` page
  listing every account. The configure wizard no longer asks for a launch
  key; launching uses the signed-in account. The run page says when a run
  was paused because the token quota ran out.
- Launching with a bare `qrk_` key is now off by default
  (`QR_EVALS_ALLOW_KEY_LAUNCH`), because such runs are not metered and bypass
  token quotas. `POST /api/runs` and `/api/readiness` need a session unless an
  operator switches it on.

- Meter token usage per account and enforce a token quota. Every generation,
  judge call and narrative call of an account's runs is recorded
  (`GET /api/me/usage`). Generations on the qr-server engine count against a
  lifetime quota (`QR_EVALS_DEFAULT_TOKEN_QUOTA`, default 2 000 000; `0` =
  unlimited): a spent budget refuses new launches, and a running run is paused
  when it reaches zero. Admins see every account and set quotas with
  `GET /api/admin/users` and `POST /api/admin/users/{id}`, or
  `python -m qr_llm_evals.accounts.cli set-quota`. Judge rows now carry
  `usage`.

- Add user accounts. `POST /api/auth/signup` creates an account from a
  username, a password and a working api.entropic.science key; `/auth/login`,
  `/auth/logout`, `GET /me` and `POST /me/key` manage the session and the
  linked key. Only the key's SHA-256, name and fingerprint are stored. A
  session token can launch runs and run readiness; the run records its
  `owner_id`. Operators manage accounts with
  `python -m qr_llm_evals.accounts.cli`. New settings:
  `QR_EVALS_SIGNUP_ENABLED`, `QR_EVALS_SESSION_TTL_HOURS`.

- Archive the OpenRouter provider profile. It is no longer a selectable
  provider, a `provider_keys` slot or a `keys_present` entry; the removed
  pieces and the steps to restore them are in `archive/openrouter/RESTORE.md`.
  The configure wizard now sends the external provider key to the `openai` or
  `custom` slot the study actually uses.

- Revise the "Prompt or Physics?" thesis study after review. The sampler check
  (H0) now asks whether the quantum arm is fair *within a fixed tolerance*
  (KS distance of `u` from uniform below 0.005 with a one-sided DKW bound) instead
  of exact uniformity, which any harmless flaw failed at 1.7M tokens; a passing
  null result is no longer discarded when the sampler is slightly off. New: a
  first-word test (H4: "Yes"-first rate on "Are you conscious?" against the
  model's own odds, adjusted for device-wide unfairness), bent-number arms as a
  positive control for the claim measure (C1), a guesser warm-up on temperature
  0.5 vs 1.5 replies (C2, the original plan's control restored), a fixed 5-point
  equivalence margin (the pilot no longer sets it), seed pairs as a sanity check
  instead of a bar, base-prompt clustering (the 30 prompt variants are 10
  prompts at three depths), every told-nothing reply guessed (1,800, once per
  guessing arm) instead of 600 guessed 40 times, an all-replies analysis next to
  the clean one, a pilot check on ordinary numbers only
  (`thesis_report.py pilot-check` replaces `pilot-margins`), and
  `studies/thesis_prompt_or_physics/thesis_device_check.py` / `studies/thesis_prompt_or_physics/thesis_first_word_tokens.py`.
- Realise biased (ε) arms by lowering the z-score amplifier's reference mean
  (`qr_population_mean`, a per-request field the engine accepts) instead of the
  never-deployed `qr_bias_epsilon` (DEP-2): every `u` becomes `Φ(z + ε)`.
  `qr_population_mean`/`qr_population_std` join the refused override keys and
  `qr_population_mean` the equivalence keys; readiness probes the bend; ε 0.50
  joins the grid.
- Items may carry their own `qr_overrides` (merged over the card's, validated the
  same way). Derived guessing cards may use `k ≥ 1`. `load_data` can keep tainted
  samples (`include_tainted`). The source-guessing chance test accepts any two
  labels. New cards `guesser_warmup_replies` and `guesser_warmup`.
- Cut the analysis's peak memory. The MMD permutation test no longer builds the
  full N×N Hamming kernel (about 5 GB for the thesis dry run): it sums per-prompt
  blocks (`_mmd_blockwise`, identical statistic to within 1e-15), sizes its
  in-memory threshold by n·m·L, and stores permuted labels as int8.
  `exclude_tainted` filters scores and Tier-0 rows while sharing the loaded
  records, so the decision module loads a run once for both the clean and the
  all-replies analysis. Note: `load_data` still holds every Tier-0 token record
  in memory (about 4.4 KB each); a full thesis run needs an analysis machine with
  tens of gigabytes of memory.
- MMD-Hamming leaves out prompts with fewer than two samples in either arm and
  reports them as `n_prompts_dropped`. Such prompts add cross-arm pairs without
  within-arm pairs; with the thesis study's one-guess-per-item derived cards they
  were 4,800 of 4,854 prompts and pushed the statistic to -0.16 (quantum vs
  seeds) and -0.25 (seed vs seed) for identical arms. P-values were valid but the
  statistic was not interpretable.

- Make the per-token `top_logprobs` depth a deployment setting
  (`QR_EVALS_TOP_LOGPROBS`, default 20, up to 1000) instead of a fixed 20.
  Runner, readiness probes and provider bodies use it; each run records it in
  `request_settings.json`, and the analysis reports per-arm top-k coverage
  (`tier0_topk_coverage`, `tier0.endpoints.topk_coverage`).
- Feed the study judge's per-sample scores into the analysis (`judge_score`);
  judged endpoints such as `experience_claim_rate` were previously never filled.
- Compute the told/actual decomposition the analysis plan promises for told/actual
  cards (told-source priming, actual-source main effect, interaction, 3 × 2 cell
  means) instead of only labelling the effect order.
- Fix prompt clustering for told/actual sample refs (they were clustered by epoch).
- Fix the randomised PIT draw: `V` is now keyed per token (seed, sample ref,
  token index; `tier0.endpoints.token_uniforms`) instead of restarting one seeded
  stream on every call. Per-sample calls had shared one `V` sequence across
  samples and arms had shared it index by index, so under fair sampling the
  lag-1 test rejected about half the time at α 0.05, pooled multi-arm
  uniformity tests were inflated as much, and two-arm KS comparisons were
  conservative (about 0.3%). Tier-0 residual results from earlier runs change.
- Keep one told cell per (card, prompt, epoch) in the stream-level Tier-0 tests
  (`tier0_pit_residuals`, `tier0_mmd_hamming`, `tier0_logprob_endpoints`;
  `tier0.records.independent_streams`). The three told cells share one `qr_seed`
  by design, so on seeded arms their streams are identical and pooling them made
  the tests anti-conservative (the offline dry run's pooled uniformity test
  rejected a fair sampler at p = 0.002). Thesis H0 still uses every quantum token.
- Report two more sampler checks beside thesis H0: whether each logged `u` lies in
  its chosen token's bracket (`u_in_bracket`) and whether the logged `u` values are
  uniform (`u_uniform`).
- Test source-guessing accuracy against chance with the judged completion as the
  unit (balanced accuracy, label-permutation null), add a cross-validated
  outside-observer classifier on Tier-0 features and a privileged-access contrast.
- Add the `source_guessing_self_report` card (T4-f variant on told-nothing
  self-referential replies, balanced QRNG/PRNG, fresh-context and in-context
  guesses), a 5-point SESOI on `experience_claim_rate`, and the
  `studies/thesis_prompt_or_physics/study.yaml` template with `studies/thesis_prompt_or_physics/README.md`,
  `studies/thesis_prompt_or_physics/thesis_dry_run.py` (offline fair-sampling simulator) and
  `studies/thesis_prompt_or_physics/thesis_report.py` (pilot margins, H0–H3 decision table).

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
