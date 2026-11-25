# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Raw conditioning and the resampled control are options of the study file
  only, kept for future experiments. A study made in the web app is hashed.
  - The "Entropy conditioning" panel no longer has the choice between hashed
    and raw. It shows the mode as a statement and edits the hash, the block
    size and the bytes kept of a hashed study, with the rule that at most
    half of every digest is served. A raw study (`conditioning.mode: raw` in
    its file, or no `conditioning` block) still opens. The panel says that
    it runs on raw bytes, that a raw quantum arm differs from the
    pseudo-random arms by the device's byte statistics as well as by the
    source, and that raw entropy is set in the study file only. Editing
    another setting leaves such a study raw, and a study without the block
    stays without one. "Use hashed entropy" changes it to hashed; the page
    has no way from hashed to raw.
  - The arm source `prng_resample` is back in the backend as it was before
    it was removed (see the entries below): the schema, the
    envelope (`qr_entropy_source_type: "qbert_prng_resample"`), the
    provenance label `resampled_prng`, the health summary and the generated
    contracts. Its envelope carries what every arm that samples through the
    amplifier carries since then: the amplifier pins, `qr_sample_count` and
    `qr_conditioning_mode`. Like the quantum arm it is served by the entropy
    server, so it sends the four `qr_conditioning_*` keys in a hashed study
    and `raw` otherwise. No picker of the web app offers the source. A study
    file that names it loads without a problem, is listed in the arm table
    and drawn on the map as an arm outside the comparison, and runs; its
    rows carry the badge "Resampled device-pool PRNG".
  - The designed pre-registration names the resampled control again in its
    paragraph on entropy conditioning, so the template hash printed in
    `backend/tests/fixtures/prereg/golden_designed.md` moved. No config,
    plan or cards hash moved, and the template of a study without a design
    block is untouched.
  - The `409` for a study frozen with a removed setting, added in
    the entry below for `prng_resample` alone, is removed with its error class:
    the source is valid again, such a study validates, and nothing else
    used the path. A draft in the browser with a resampled arm is read like
    any other draft.
  - `docs/SETUP_GUIDE.md` ("Options of the study file that the app does not
    offer") shows how to write both in a study file.
- A study frozen with a setting that was removed since (an arm with the
  source `prng_resample`, which existed for a day) still opens: its page, its
  runs, their health summaries, its pre-registration, its bundle and its
  analysis pages are served from the stored files, and the arm ledger lists
  its rows under the label they were stored with (`resampled_prng`). What
  needs the validated study (resume, analyse, a new session) answers `409`
  and names the field, where it answered `500` before. Nothing of such a
  study is changed or removed. A draft in the browser with such an arm is
  set aside and replaced by the template, as any draft the page cannot read.
- A resumed run says in the server log when an arm's envelope is not the one
  the run's readiness check recorded, and names the keys. That is the case of
  a run started before the harness sent the draw size and the conditioning
  mode and resumed after. The run is not stopped; the `envelope_hash` of each
  generation tells its two parts apart.
- Every envelope states its conditioning mode. The quantum arm of a hashed
  study sends the four `qr_conditioning_*` keys as before; every other arm
  that samples through the amplifier (a seeded, system or biased arm, and
  every arm of a raw study or of a study without a conditioning block) now
  sends `qr_conditioning_mode: "raw"`. Before, those arms sent no conditioning
  key and took the engine's default mode, so an engine started with a hashed
  default would have hashed the bytes of a raw study and refused the
  pseudo-random arms. Every study now needs a qr-sampler that knows the key.
  Like the draw size below, the key changes every envelope, every envelope
  hash and the envelope lines of every pre-registration, also for a study
  without a design block: the six pinned legacy pre-registration hashes and
  the envelope lines of `golden.md` moved for these two keys and for nothing
  else, and no config, plan or cards hash moved.
- The harness states how many entropy bytes a token draws. Every envelope of
  an arm that samples through the amplifier now carries
  `qr_sample_count: 10000` with the amplifier pins, so no process default of
  the engine decides it; a card still cannot set the key. Before, the key was
  not sent, and the engine on the box drew 6,000 bytes per token while
  `SAMPLE_COUNT` and the bend of a biased arm (`bent_population_mean`) assumed
  10,000. Every envelope, every envelope hash and the envelope lines of every
  pre-registration change with the key, also for a study without a design
  block: the pre-registration hashes pinned in
  `backend/tests/test_legacy_bytes.py` and the envelope lines of
  `backend/tests/fixtures/prereg/golden.md` were updated for this key and the one above,
  and no config, plan or cards hash moved. Nothing renders a stored study
  again or derives a stored envelope hash again to compare it, so a study
  registered before this release keeps its document and its hash, and loads,
  relaunches, analyses and reports as before. Its frozen document shows the
  envelope without the key; a run made or resumed after this release sends
  the key, and the `envelope_hash` of each generation records which envelope
  that generation was sent with.
- A study can set the token limit of an eval. `cards[].overrides.max_tokens`
  replaces the card's own `max_tokens` for every request of that eval, in
  every arm, on the thin runner and through Inspect. The value must lie
  between the card's `est_tokens_per_sample` and 64,000; the plan, the
  estimate, the pre-registration and the runner refuse one outside that or
  on an eval that generates nothing. A study that sets no limit is not
  changed: the key is not written, so its hashes stay what they were.
- Each generating eval has two recommended limits: **standard** (the card's
  own) and **safe** (four times that, at least 256, at most 64,000).
  `GET /cards` returns them as `token_limit`, and the Measure step and
  "All settings" have a "Token limits" table with both presets, "Set all to
  safe" and the most an arm can generate at the chosen limit. The eval's
  box on the study map has the same field. The safe limit is a rule, not a
  measured bound, and a reply that reaches any limit is still flagged as
  cut.
- Both pre-registration templates print the limit in force for each eval,
  and the designed one adds "set by this study; the card has N" where the
  study set its own. The designed template's hash changed with that line.
- An eval card may not ask for more than 64,000 tokens.
- A study chooses how the bytes of the entropy server are conditioned
  (`StudyConfig.conditioning`, the "Entropy conditioning" panel of the Compare
  step and of the settings page). `hash_truncate`, the default of a new study,
  asks the server to hash its raw read block by block and to serve part of each
  digest, so the quantum arm samples from uniform bytes whatever the device's
  own byte distribution is; the hash, the block size and the bytes kept per
  digest are settings (SHA-256, 128 and 16 by default, which reads 8 raw
  bytes for every byte served). `raw` serves the measured bytes; the panel and
  the pre-registration then say that the quantum arm differs from the
  pseudo-random arms by the device's byte statistics as well as by the source,
  and that the harness has no matched control for that. Hashing always
  discards at least half of every digest: a study that keeps more is refused
  by the server and named in the panel before it is submitted, because
  serving more than half of a digest may compromise the integrity of the
  signal. The four
  `qr_conditioning_*` keys ride only the arms that draw from the entropy
  server (every other arm says `raw`); a card cannot set them, and they count as entropy keys in the
  equivalence check. The field is left out of a study that does not carry it,
  so every study from before it keeps its bytes, its hash and its raw run.
- The designed pre-registration states the study's conditioning under "Arms
  and equivalence". The legacy template is frozen; a legacy document shows the
  conditioning in its illustrative envelopes.

- No key of a run is stored in the run folder or sent in an event when an
  upstream answer repeats it. The text of a failed generation (the `detail`
  of a taint in `taints.jsonl` and in the `taint` event), the `raw` field of
  a judge row whose call failed, a scorer's error text, the detail of a flag
  and the message of an `error` event now pass through the redaction of the
  run's keys (`SecretBag.redact`) before they are written. A text without a
  key is stored as before.
- A run that was launched without any provider key is `paused` after a
  backend restart, and Resume takes it up again. Before, it became
  `needs_keys` like every other unfinished run, Resume was refused until a
  key was supplied, and the keys route refuses an empty request, so the run
  could only go on with a key it did not need. Found by the pilot on the
  live engine. A run that held a key still becomes `needs_keys`.
- The log filter keeps scrubbing a key until every run that holds it has
  ended. Before, the first of two runs with the same key to end switched the
  scrubbing off for the other one's log lines.
- The Run page no longer prints "Run is paused." right after Resume: it says
  the request was accepted and that the run state at the top of the page is
  the one to read. The notice of a stop says that the server refuses Resume
  while the launching account is disabled or out of token quota. The heading
  of "Fixed by the harness" counts the rows it lists.
- `requirements-mask.lock` pins `pyarrow` for the box and `scripts/deploy.sh`
  installs it, so a release can read the Parquet files of the `mask_beliefs`
  and `mask` evals without a step by hand. The `ifbench` verifier still has
  to be installed by hand after every deploy (`infra/README.md`).
- The narrative's number check now reads a number at the end of a sentence.
  Before, a number directly followed by a full stop was skipped, so an
  invented number there passed the publication gate. The marker of a
  numbered list (one or two digits and a full stop at the start of a line,
  followed by text) is still not read as a number.
- A cause or count of a stop is kept under its own name only when it is an
  HTTP status, a class name, a lower-case name of the harness, an arm and
  eval pair, or one of the harness's five phrases (`CAUSE_PHRASES`). Any
  other text of several words, and anything shaped like a key, is tallied as
  `other`. The causes `non-finite score` and `unwritable score` are now kept
  under their names; before, they were tallied as `other`.
- Documented three limits: a run that completed with fewer failed samples
  than the `unfinished` rule needs cannot be resumed to try them again
  (`docs/ANALYSIS_PLAN.md`); a release from before the result checks refuses
  to load a run made with them, so do not roll back past that release while
  such runs must stay readable (`docs/SETUP_GUIDE.md`); and `Run.taints` is
  never written (the field's description in the contracts).
- Result checks. The harness now looks at every reply, token record, score
  and judgement while a run stores it. The rules are fixed by the harness
  and are the same for every arm and every study: no setting of a study, no
  eval card and no environment variable changes them, and no config, plan or
  card hash moved. They are version 1 (`CHECKS_VERSION`), which a run
  records as `checks_version` when it is first started.
  `docs/ANALYSIS_PLAN.md` has a new section "Result checks" with every kind,
  rule and threshold, and `docs/DESIGN.md` records the decision as D-16.
- A flag marks a sample and excludes nothing. There are thirteen kinds: an
  empty reply, a reply that ended at the token limit, a reply that ends in a
  loop, damaged characters, reasoning text in the reply, a missing token
  log, a random draw outside the bracket of the token that was sampled, a
  scorer that failed, an answer the scorer could not extract, and four about
  the judge (no reply, a reply without a score, a score the rubric does not
  allow, a reply that was cut short). Flags are written to
  `runs/<run_id>/flags.jsonl`, which is in the study bundle. No analysis
  reads that file: a flagged sample is analysed exactly as an unflagged one.
  The checks do not detect a loop with a counter in it, a loop the model
  leaves again, reasoning written without `<think>` tags, or nonsense in
  valid Unicode, and no check asks a model whether a reply makes sense.
- More failures are recorded as the taints they are. A stream that ends
  without a finish reason is a `stream_error`; before, it was stored as a
  completed reply. A stream line that is not valid JSON is a `stream_error`
  for that sample; before, it aborted the run. A token record that says the
  engine drew from its fallback source taints the sample as
  `engine_fallback` and withholds the quantum label, also when the engine's
  fallback counter did not move. Token records that fail while they are
  built, or hold a value that is not finite, are a `stateless_row` for that
  sample; before, some of these aborted the run. An attempt that did not
  complete writes its
  `stream_error` taint and no entropy taint, and a sample's taints are
  written before its reply.
- One exclusion rule changed, for every plan, also for a study without a
  design block: a `stream_error` taint no longer excludes a sample that has
  a completed reply. That is a sample whose failed attempt was tried again
  after a resume. Before, the failed attempt excluded the sample for good.
  An existing run that holds an error row, its taint and a later completed
  row for the same sample gains that sample when it is analysed again. A
  sample that also carries another taint stays out. The pinned bytes of
  legacy studies did not move.
- A check stops a run that is plainly broken. Eleven rules: generations
  failing (8 in a row on one arm, or half of the last 40), a degraded
  quantum source (10 tainted samples in a row on a quantum arm, or 3 that
  started while the entropy check already reported trouble), missing token
  records (10 in a row on one arm), a failing scorer (10 in a row on one
  eval), empty replies (20 in a row on one eval, from at least 3 prompts,
  with at least 2 arms each giving at least 5 of them), no answer extracted
  from any scored sample of an eval once there are at least 30 of them from
  at least 2 prompts, samples still missing when generation or judging reaches its end,
  a judge that refuses requests (401, 403 or 404), a judge that does not
  answer (10 in a row, or half of the last 40), no score readable from any
  of an eval's first 30 judge replies, and an unexpected error in the judge
  phase. A rule that reads what replies say is counted over an eval, never
  over one arm.
- A stop is a pause with a stored reason, never an abort. The run takes no
  new work, the requests under way finish, the stop is written to `run.json`
  under `halts` (the rule, what it was counted over, a sentence with the
  real counts, a tally of causes, up to three samples and what Resume
  recovers), and the run is then `paused`. Nothing is removed and nothing
  resumes by itself. The host's Resume starts a new pass: it plans the run
  again from what is stored, generates the samples that have no completed
  reply and asks the judgements that have no reply, with the keys the run
  holds then. A restart of the server is the same case. A judge-phase error
  that used to leave a run `running` with nothing running is now such a
  stop. On an eval that runs through Inspect, a sample that does not
  complete now pauses the run; before, it aborted it.
- Before the first generation, on every start and every resume, a run that
  holds a judge key and has an eval scored by the study's judge sends the
  judge one small request with the study's judge settings. A status of 400,
  401, 403 or 404 stops the run before any sample is generated. The request
  and its reply are not stored.
- The judge's score is read by a stricter reader: the last line of the form
  `SCORE: n` or `SCORE = n`, or else the first JSON object with a numeric
  `score`. The reader no longer falls back on the last number anywhere in
  the reply. For the four evals whose rubric has fixed values
  (`eval_awareness`, `self_referential_probe`, `self_reported_coincidence`:
  0 or 1; `opinion_quantum_sampling`: -1, 0 or 1) a value outside them is
  not stored as a score. A judge row has a `status` (`ok`, `failed`,
  `unreadable`, `out_of_range`), the judge's `stop_reason` and a
  `reader_version`. A reply that was received is final: a row without a
  readable or allowed score keeps the reply, has no score and is not asked
  again. Before, such a sample was asked again on every resume. Only a
  judgement that got no reply is asked again. Stored rows are not read
  again, so scores of earlier runs stay as they were stored.
- `GET /api/runs/{id}/health` returns the health summary of a run: for each
  arm and eval the planned, completed and failed samples, the taints, the
  flags and what became of the judgements; the stops of the run; and
  cautions, which are plain sentences written when a flag kind other than
  "ended at the token limit" is on at least 5% of an arm's completed samples
  on an eval, when the run is not complete, when a stop was not resumed, and
  when judge replies were read by more than one reader version. No analysis
  reads the summary. A run made before result checks says so and gives no
  flag counts.
- `GET /api/runs/{id}` and `GET /api/studies/{id}` carry each run's `halts`
  and `checks_version`. `GET /api/studies/{id}` has a new `health` object
  with each run's completed, failed and tainted samples, and the analysis
  index has the same counts for the analysed run. The rows of the index's
  `provenance` list count each sample once and have two new keys,
  `excluded_samples` and `failed_samples`. Before, the public counts of
  failed samples compared the finish reason with `aborted`, which the store
  never writes, and the results page read its taints from a list on the run
  record that is never filled.
- New event `check.stopped` on a run's event stream, with the stored stop.
  `sample.finished` has a new `flags` list. The generated contracts gain
  `ResultFlag`, `Halt` and `HealthSummary`. A harness from before this
  change cannot read a `run.json` that has `halts` or `checks_version`, or a
  judge row that has `status`.
- The pre-registration of a designed study has a new section "Result
  checks", printed from the catalogue of the checks: that a flag excludes
  nothing, which failures are tainted, what stops a run and what Resume
  does. The pinned designed document changed
  (`backend/tests/fixtures/prereg/golden_designed.md`); no plan hash moved,
  and the document of a study without a design block is unchanged. The
  README of a designed study's bundle names `flags.jsonl` and `halts`.
- The run page shows "Stopped by a check" for every stop that was not
  resumed: what the check saw, where and when, the tally of causes, up to
  three samples, what Resume recovers, who can resume, and that Abort is
  final. On a stop because the judge refused requests the notice has the
  form to supply a judge key. The live page shows one line with a link to
  the run page. The run page and the study results page have a new section
  "Output health" with the cautions, the list of stops and a table per arm
  and eval, for every study. The taint report on the results page counts
  from the health summary. The Design step lists the result checks under
  "Fixed by the harness" with a link to the analysis plan.
- Smaller changes that came with the checks: the engine's token log is
  looked for only when the request asked for one (before, every sample
  waited up to half a second for a file whenever a log directory was
  configured, also on greedy and provider arms); a row whose stream ended
  without a finish reason keeps the text that arrived; and `scripts/smoke.py`
  fails at once with the stop's sentence when a check stops its run.
- Documents for the result checks: `docs/API.md` (the health route, the
  stop record, the new event and fields, the judge row),
  `docs/SETUP_GUIDE.md` ("When a check stops a run": what an operator does
  after each rule), `docs/add-an-eval.md` (how an eval's `score()` reports
  an answer it could not extract and how an eval gets on the catalogue's
  lists) and `docs/ACCEPTANCE.md`.
  `backend/tests/test_docs_result_checks.py` compares these documents with
  the catalogue.

- The number of permutations of a designed study is the largest of three
  numbers: 10 000, 20 times the number of difference tests divided by α, and
  2 times the number of seed-pair tests divided by α. Before, the seed pairs
  were given 20 per unit as well, which refused designs the setup can build
  (for example three outcomes with five control seeds at α 0.005, which now
  runs 12 000). The designed Study 1 runs 10 000 at α 0.05 and 0.01 and
  12 000 at 0.005. A design that needs more than 100 000 is still refused.
- The texts about the control floor no longer say that the seed-against-seed
  control cannot fail below it. With fewer prompts it is not certain that
  two seeds that differ would fail the control, so the analysis does not
  count the control as passed. At the floor itself a pair of seeds that
  differ on every prompt fails in most analyses and not in all
  (`docs/ANALYSIS_PLAN.md`, "Control gates and completeness").
- `POST /config/parse` answers `400` for a `design.alpha` or a
  `design.sensitivity_epsilon` outside its allowed values, and names the
  setting and the values. Such a file is malformed, not a draft with a
  problem.
- A study without a design block that has no comparison of a quantum arm
  with seeded controls, or fewer than two control seeds in a comparison, is
  reported with warnings, not blocking problems: the server launches such a
  study. With a design block both stay blocking.
- New blocking problem `design.no_planned_data`: a study that decides on a
  token-level outcome and selects no eval with a prompt list of its own
  plans no data for that outcome.
- "Declare a design" on the Design step adds the design block and nothing
  else. It no longer raises repetitions, so declaring a design and returning
  to the built-in plan gives back the study that was there, with its config
  hash. The mark beside an eval's repetitions says "close" or "too few" and
  "Use recommended" sets the k that would do.
- Every save of a study file is checked. After the file is written the page
  sends the same text to `POST /config/parse` and reports whether the file
  will load back; when the server cannot be asked, the page's own check
  answers and the report says so. A draft with a problem inside one setting
  (an empty title or contact, no arms, no evals, an arm that contradicts
  itself, a value out of range) is still written, but the report says the
  file will not load back, the file state reads "will not load back as it
  is", and "Save current study first" replaces nothing on such a file.
- While a setting is outside what a study can hold, the browser does not
  store the draft. The page now says so beside the problem and in the study
  bar ("edits are not kept in this browser").
- New blocking problem for an arm whose own settings contradict each other
  (a seed on an arm whose source uses none, a bias on an arm that is not a
  biased arm, greedy decoding on a quantum arm, an arm switched off without
  a reason). The server refuses a file with such an arm.
- On the results page of a designed study, the sentence about the
  seed-against-seed control is built from what the table records. For a row
  whose seed pairs did not differ but whose control could not have failed,
  it says so and does not read as a pass.
- The pre-registration page says which study it shows when a share link or a
  stored draft cannot be read, also after the draft changed in another tab,
  and says so when neither the link nor the draft can be read.
- On the map of a study without a design block, the prompts of an eval say
  what the built-in plan does with each of the eval's outcomes (a primary
  test, a secondary test, or reported without a significance threshold).

- A study that is already registered takes further runs. A later `POST /runs`
  with the same configuration adds a run to the study and freezes nothing
  again: a pilot is followed by the full run, an aborted run by another. The
  study keeps the pre-registration and the plan of its first launch, the new
  run is recorded under the frozen document's hash, and it is not marked as
  having a stale pre-registration when the host presents that hash or the
  hash of the document rendered for the new launch. Before, the second launch
  of a study was refused with `409`: the document rendered for it prints that
  launch's readiness report, so it never equalled the frozen one.
- Samples the run marked as tainted (engine fallback, degraded entropy) never
  enter the clean analysis, also when every sample of an arm on an eval is
  tainted. Before, such an arm was analysed on its tainted samples. Its
  comparisons are now unavailable.
- A paired comparison whose complete pairs all lie on one prompt is reported
  as unavailable ("fewer than two prompts with complete pairs"), and so is
  its equivalence test. Before, the analysis failed on it.
- `POST /config/parse` reads text that opens with `{` as JSON, and as YAML
  only if it is not JSON. Before, JSON went through the YAML parser, which
  refuses or alters some JSON documents. Text that parses and cannot be
  built (a date that does not exist) or is nested too deeply is answered
  with `400`; it was a `500`.

- `livecodebench` is implemented and selectable, on the thin runner: the 175
  problems of LiveCodeBench release v6, with the prompt, code extraction and
  test comparison of the authors' runner. With it every card of the registry
  is implemented. A reply is a program written by a model, and the harness
  never runs it: `score()` sends it to a sandbox service over a Unix socket
  (new units `infra/qr-llm-evals-sandbox.socket` and
  `infra/qr-llm-evals-sandbox@.service`, installed by an operator), which
  runs it under a throwaway user without network or access to the harness's
  data. Without the service the eval generates and stores its replies
  unscored. The units have not been started on the box yet
  (`docs/SETUP_GUIDE.md`, section 1). The hidden tests are stored in a pickle
  and are read with an unpickler that loads no code.
- Three more registry evals are implemented and selectable: `sycophancy_ays`,
  `deception_bench` and `mask`. Each asks the model more than once, so each
  is a pair of cards on the thin runner: a first card writes the replies
  (`sycophancy_ays_answers`, `deception_bench_beliefs`, `mask_beliefs`) and
  the planned card, now derived from it, works on them. A study must select
  both cards of a pair; the second card alone has no items. All three follow
  their Inspect ports (`inspect_evals/sycophancy`, `inspect-deceptionbench`,
  `inspect_evals/mask`), read their data at a pinned revision when a run
  starts and refuse a file whose SHA-256 differs. MASK records carry a canary
  string and are never stored in the repository.
- `sycophancy_ays` uses the multiple-choice questions of the set only, and
  reports how often the model admits a mistake after "Are you sure?"
  (`admits_mistake_rate`, with upstream's `apologize_rate`, `confidence` and
  `truthfulness`). Its planned endpoint `flip_rate` is gone: an answer flip
  needs a third turn that neither upstream nor this card runs.
- `mask` reports `lie_rate` per pressured reply as its headline; the planned
  `lying_at_10` is computed for description only. The MASK cards need
  `pyarrow` (new `[mask]` extra) and about 15,850 judge calls per arm at
  their defaults (`docs/SETUP_GUIDE.md`, section 1).
- `deception_bench/deception_rate`, `mask/lie_rate`,
  `mask_beliefs/belief_held` and `sycophancy_ays_answers/first_answer_correct`
  can be registered by a designed study.
  `sycophancy_ays/admits_mistake_rate` cannot: the eval has one item per
  first answer, a number known only when the first card has run.
- The judge phase now judges a derived card after the cards it reads from and
  rebuilds its items from the run store for each arm, as the generation phase
  does. Before, a judged derived card was judged without its items. No such
  card existed, so no stored run is affected.
- Five more registry evals are implemented and selectable: `xstest`,
  `strong_reject`, `darkbench`, `eval_awareness` and `sage_consistency`. The
  first three were planned as Inspect tasks and now run on the thin runner
  with upstream's prompts, judge templates and scoring rules, under the
  study's sampling envelope and graded by the study judge. `xstest`,
  `strong_reject`, `darkbench` and `sage_consistency` read their prompts at a
  pinned revision when a run starts and refuse a file whose SHA-256 differs;
  StrongREJECT's forbidden prompts are never stored in the repository.
- `eval_awareness` needs its gated transcripts supplied by the operator at
  `evals/eval_awareness/data/dataset.json` (ignored by git). The harness does
  not download them, and without the file a run that selects the eval stops
  when it builds the eval's prompts (`docs/SETUP_GUIDE.md`, section 1). The
  eval has not been run on the real benchmark.
- A judged eval can read several numbers from one judge reply: an optional
  `judge_fields(sample, raw)` in its task module. The values are stored in the
  judge row (new `JudgeRow.fields`) and reach the analysis under their own
  names (`docs/add-an-eval.md`, section 4).
- `strong_reject/harmfulness_score`, `darkbench/dark_pattern_rate` and
  `eval_awareness/eval_belief_rate` are per-reply judge scores, so a designed
  study can register them. `xstest/over_refusal_rate_safe` is defined on the
  eval's safe prompts only and `sage_consistency` needs every answer to a
  question at once; neither can decide a designed study yet.
- Card corrections: `xstest` reads the authors' `Paul/XSTest` (was the mirror
  `walledai/XSTest`), `sage_consistency` reads `vamshibon/SaGE` (the old
  address redirects there), and `eval_awareness` names the Hugging Face
  dataset `jjpn2/eval_awareness`.
- Four registry evals are implemented and selectable: `aime2026`,
  `gpqa_diamond`, `ifbench` and `psychobench`. The first three were planned as
  Inspect tasks and now run on the thin runner with upstream's prompts and
  grading rules (`inspect_evals` commit `c2d3d5e`, AI2's IFBench commit
  `4889186`), so the harness sets the decoding envelope and every arm gets the
  same k replies per prompt. Each eval reads its prompts at a pinned revision
  when a run starts and refuses a file whose SHA-256 differs; GPQA questions
  and PsychoBench item texts are never stored in the repository.
- `psychobench` administers 352 statements of 11 instruments, one statement
  per reply (the card said 500 statements of 13). Upstream's `16P` has no
  scoring key, and the commercially licensed `EPQ-R` and `BSRI` are left out.
- New optional extra `[ifbench]`: AI2's verifier, pinned to the commit the
  prompts come from. Without it, or without its NLTK data, `ifbench` replies
  are generated and stored and their scores are `null` with the reason
  (`docs/SETUP_GUIDE.md`, section 1).
- For these four evals the analysis tests a per-reply value (`pass_at_1`,
  `strict_accuracy`, `rating_norm`). Their headline endpoints (`answer_entropy`,
  `constraint_pass_entropy`, `test_retest_reliability`) need the k replies to a
  prompt scored together; each task module computes them in `endpoints()` for
  description, and they cannot decide a designed study yet.
- A study file can carry a `design` block (`StudyConfig.design`): the
  confirmatory endpoints, the claim (`difference` or
  `difference_or_equivalence`), the significance level (0.05, 0.01 or 0.005),
  the ε of the biased arm that serves as the positive control, and margins for
  endpoints whose card states no SESOI. The block is validated against the
  arms: every compared (model, sampling) cell needs one available unsupervised
  quantum arm and at least two seeded arms on distinct seeds, and a positive
  control must sit on a seed of its own.
- A study without the block is unchanged, byte for byte. The `design` key is
  left out of the serialized config when it is not set, so config hashes,
  study ids, plan hashes and rendered pre-registrations of existing studies
  are what they were; `backend/tests/test_legacy_bytes.py` pins them for
  Study 1, the thesis study and an archived plan. Such a study keeps the
  built-in Study 1 plan and its claim rule.
- A study with a `design` block gets its own statistical plan
  (`StatisticalPlan.design`): one hypothesis per confirmatory endpoint and
  (model, sampling) cell, each with its quantum-vs-control contrast, its
  seed-vs-seed negative controls, its positive control, its margin and a
  sample-size rating computed before any data exist (`sufficient`, `marginal`,
  `insufficient`, or `unknown` for the KS and MMD tests, which have no closed
  form). The contrasts are the built-in ones; the level, the interval level
  (1 − α) and the number of permutations follow the design. A design the
  analysis cannot execute is refused when the plan is built: an endpoint the
  harness does not compute per sample; a declared margin on an endpoint whose
  test has no paired difference, that has a card SESOI or that has no fixed
  range, or a margin above 10% or below 0.1% of that range; or more
  difference tests or seed-pair control tests than 100 000 permutations can
  resolve.
- The sample-size ratings are bounds with stated assumptions. The variance
  bound takes every reply of a quantum arm as independent and counts the told
  cells of a seeded run as one value, because they share one seed for each
  prompt and repetition. Each hypothesis also carries the control floor: the
  fewest prompts for which the analysis counts the seed-against-seed control
  of a paired test as one that could have failed (10 for the designed
  Study 1). Fewer prompts are rated `insufficient`, are never recommended,
  and are a blocking problem in the setup. The number of permutations takes
  the seed-pair tests into account as well as the difference tests, so that
  no control is out of reach of its own test (the rule is stated in an
  entry above).
- Repetitions have a recommendation (`recommend_k`, `SampleSize.k_recommended`):
  the fewest k, within the bounds of the eval's card, at which all its
  prompts are enough for the margin of an outcome that decides the study.
  The designed Study 1 and the setup's template run `self_referential_probe`
  at k = 24 where its card says 20: at 20 its 30 prompts give 72.8% power at
  the margin, at 24 they give 81.4%.
- Nine headline endpoints can decide a designed study today: the two Tier 0
  tests, the experience-claim rate, the MEQ-30 total, the opinion axis score,
  the warm-up reply length and the three per-reply judge scores named above
  (`strong_reject`, `darkbench`, `eval_awareness`). An endpoint that needs
  the k replies to a prompt scored together cannot, because the harness does
  not score replies in groups; nor can one whose test does not resolve to a
  verdict, or one of an eval that is not implemented (`cards/endpoint_meta.py`;
  `backend/tests/test_endpoint_meta.py` checks the table against every card's
  `score()`). `docs/ANALYSIS_PLAN.md` lists them.
- The analysis of a designed study resolves every hypothesis to one verdict
  and writes `tables/decisions.json`: per hypothesis the estimate, the
  interval, the raw and Holm-adjusted p-values, the equivalence test against
  the registered margin, the share of the planned data analysed, the
  seed-vs-seed negative control, the biased-arm positive control, and the
  verdict (`effect`, `effect_within_margin`, `equivalent`, `inconclusive`,
  `invalid` or `unavailable`) with its reason and sentence; and for the study
  one roll-up. The rows of the decision matrix are tried in the registered
  order and a test that was not produced keeps its slot in its Holm family.
  Whether the difference exceeds the seed-to-seed differences is reported
  (`above_seed_noise`) and decides nothing; the seed pairs are a gate instead:
  none may reject after correction (decision D-15 in `docs/DESIGN.md`). A
  seed pair on fewer prompts than the control floor, or one whose test ran
  too few permutations to return a p-value below the corrected level, does
  not pass the gate either: the hypothesis is `invalid` with the reason
  `negative_control_cannot_fail`. An
  inconclusive result whose equivalence test did not pass gives the reason
  "the equivalence test did not pass after correction". A designed analysis
  leaves a value outside the outcome's registered range out of its paired
  comparisons, and a hypothesis whose arm has only tainted samples on its
  eval is `unavailable`.
- In the tables of a designed study every interval is at the level the study
  registered (1 − α): the comparisons, the told/actual effects and the
  headline effect, the accuracy intervals of source guessing, of the outside
  observer and of privileged access, and the envelope of the
  cumulative-deviation figure (written as `envelope` with `envelope_level`).
  The equivalence test runs at α against the fixed margin and reads no pilot
  file, every comparison names its model and sampling preset, and the raw
  `significant_at_05` flag is not written. A study without a design block
  keeps the levels and fields it had.
- Only the first full run of a designed study is confirmatory. A pilot run,
  and any full run after the first, resolves its rows and is marked not
  confirmatory. A full run that was launched and never started does not
  count as a full run of the study while its state is `created`; a restart
  of the backend moves it to `needs_keys`, and it counts from then on. The
  first analysis of a full run writes
  `run-order.json` beside `run.json` (the full runs of the study that came
  before it); later analyses read that file and the bundle ships it, so
  re-analysing one run from a downloaded bundle gives the status the server
  gave, also when the copy does not hold the study's other runs. The README
  of a frozen study describes the file when the study has a design block.
- A study with a design block takes runs only from the launcher who
  registered it: the account of the runs it has or, for runs launched with a
  bare key, that key. Its configuration is public once frozen, so anybody
  else could otherwise launch it again and use up its confirmatory run. Such
  a launch is refused with `409` and the way to register the design as a
  study of one's own (another title or creation time). A study without a
  design block takes runs from anybody.
- The share of its planned data that a token-stream hypothesis (PIT, MMD)
  analysed is counted on the same evals on both sides: the selected evals
  that are implemented, generate text and run on a prompt list fixed at
  registration. An eval derived from the replies of another eval is in
  neither count. For the KS test a generation counts as present only when
  its token records carry the cumulative probabilities the test reads. The
  designed Study 1 plans 89 860 generations per arm, and its
  pre-registration says which evals are counted.
- A token-stream hypothesis has no margin, so it can never resolve to
  `equivalent`, and a study that makes one a deciding outcome cannot
  conclude as a whole that there is no effect within its margins. The
  recommended design includes both Tier 0 tests when they are selected. The
  setup warns of this for a study that claims absence
  (`design.study_absence_unreachable`), and the pre-registration names the
  hypotheses that rule the study verdict out.
- The pre-registration of a designed study is rendered from its own template
  (`docs/prereg-design-template.md.j2`) and states what the study registered:
  each hypothesis with its statement, outcome, compared arms, test, sidedness,
  margin and the verdicts it cannot reach; the decision matrix with the
  sentence each verdict prints, the controls of every (model, sampling)
  comparison and the study roll-up; a sample-size table with a rating per
  hypothesis; and the rules the harness fixes (Holm families, interval levels,
  permutations, analysis seed). The sections are "Hypotheses", "Decision
  rules" and "Sample size", ahead of the ones a legacy document has. The
  embedded `template_hash` is that of the template used. The legacy template
  is untouched. The document promises only what the harness computes: an
  exploratory endpoint it does not compute is stated as unavailable, and the
  card text it prints does not contradict the designed plan. It says which
  verdicts the study cannot reach and why, how many prompts the analysis
  asks of a paired test before it counts its seed control, how far the
  biased arm must
  move each outcome for the positive control to be detected (which no rating
  covers), and that a test on pooled tokens has no margin and no effect-size
  threshold.
- A designed study's analysis plan is written to
  `studies/<study_id>/prereg-plan.json` when the study is frozen at launch,
  not at its first analysis, so code deployed between launch and analysis
  cannot change the plan a study is judged by. A design the plan refuses is a
  `400` that freezes nothing. The plan hash is printed in the frozen
  pre-registration, and the server analyses the study, and takes a further
  run of it, only with that plan: a plan file with another hash is refused
  with `409`, and a missing one is rebuilt only if the eval registry still
  gives the registered plan. A study without a `design` block keeps the
  write at first analysis.
- `GET /api/cards/{id}/items` lists a card's prompts as the target model
  receives them, a page at a time, with a told-cell selector on told/actual
  cards. It lists only the ten cards whose task module builds its prompts
  from files in this repository (`PREVIEW_SAFE` in `runner/preview.py`); for
  every other card it says where the prompts come from instead
  (`derived_at_run`, `remote_dataset`, `inspect_task`, `no_prompts`). The
  route is open, reaches no network, and answers `200` with `source: "error"`
  when a task module cannot be read. A message is cut at 6000 characters,
  which no prompt of the registry reaches (the longest, in
  `altered_state_meq`, has 2 382); the largest page of 100 items is about
  125 KiB.
- The generated contracts gain `StudyDesign`, `EndpointRef`, `MarginDecl`,
  `PlanDesign`, `HypothesisPlan`, `SampleSize`, `EndpointMeta`,
  `CardItemsResponse` and `DecisionsTable`, and `StatisticalPlan.pilot_quantile`
  may be null. The package exports `endpointMeta` (label, scale, unit and
  range per headline endpoint). `scripts/gen_design_fixtures.py` writes the
  vectors the web app's sample-size and design code is checked against
  (`study1.designed.json`, `power.json`, `design.json`) and the pinned
  pre-registration of the designed Study 1
  (`backend/tests/fixtures/prereg/golden_designed.md`); its `--check` runs in
  the `schemas` row of the gate.
- A study file saved by the web app loads on the server as the same study:
  `packages/contracts/fixtures/study1.study.yaml` and
  `study1.designed.study.yaml` are the app's output for Study 1 and its
  designed form, and `backend/tests/test_study_file_roundtrip.py` checks that
  each loads to the pinned config hash, directly and through
  `POST /api/config/parse`.
- `POST /config/parse` reads a draft. The setup page saves a study as it
  stands, so a file may break rules that span several fields (an outcome
  whose eval is not selected, a claim of absence without a positive control,
  an arm on a seed the study does not list). Such a file is answered with
  `200`, the study and a new `problems` list in the server's own wording; the
  list is empty for a study that can be launched. A malformed file is still a
  `400`. No other route accepts a draft.
- The MMD permutation test no longer holds a label matrix of permutations by
  pooled sequences. It draws and reduces one prompt at a time, so its memory
  grows with the permutations times the largest prompt and not with the size
  of the study, and it returns the same numbers to the last bit for the same
  seed and inputs (`backend/tests/test_mmd_batches.py` compares it with the
  earlier implementation). On synthetic streams of about the size of the
  designed Study 1 (93 700 sequences per arm over 7 470 prompts, 10 000
  permutations) its five contrasts take about 19 minutes; the permutation
  loop adds about 0.15 GiB, and the padded token matrices and their pooled
  copy make up the rest of an 8.6 GiB peak.

- The study setup is five steps instead of seven: Compare (models, sampling
  presets, control seeds, positive controls, supervised sessions), Measure
  (evals, prompts and repetitions), Design (the claim, the outcomes that
  decide the study, the false-finding rate, the positive control, the margins
  and the expectations), Instruments (the judge and the supervised sessions;
  offered only when the study has a judged eval or supervised sessions) and
  Review & launch (authors and contact, every problem of the study, the access
  keys, the pre-registration and the launch). The step rail says in words how
  each step stands ("1 to fix", "1 to check", "clear"). Every step can be
  opened at any time; only a problem marked "Blocks launch" stops the
  pre-registration and the launch, and each problem links to its setting. The
  placeholder contact of the Study 1 template is now such a problem. The
  estimate sidebar has an "Evidence · preview" block next to the cost: the
  number of hypotheses, the sample-size rating of each deciding outcome and
  the sizes of the Holm families with a sentence on what a family is,
  computed in the browser. The study title is edited in the page header.
- The Design step shows what its six choices add up to. Under them is one
  generated card per hypothesis (outcome, model and sampling preset, test and
  sidedness in words, margin, planned data, sample-size rating) with a row of
  verdict stamps: the verdicts the hypothesis can end with as the study is
  configured and, for each it cannot, the reason. A sample-size table follows.
  It says that every rating is a worst-case bound, and it prints "Unknown: no
  closed-form power for this test." for the KS and MMD tests, which are never
  rated as enough. "Fixed by the harness" lists, folded, what a designed study
  cannot change: the test of each outcome, the Holm correction and its
  families, the interval level, the power the ratings use, the permutations,
  the completeness threshold and the sampling envelope. A legend explains the
  marks beside the settings. A study without a `design` block shows the claim
  panel with a "Declare a design" button and is told that the built-in
  Study 1 plan applies until one is declared. Declaring a design adds the
  design block and nothing else, and "Return to the built-in plan" takes it
  out again. The repetitions of an eval are marked as close or as too few
  when a larger k within the eval's bounds would make its prompts enough for
  an outcome that decides the study; the mark names that k.
- Review & launch lists the problems of the study under the step each belongs
  to, says in a sentence how many block the launch and how many are warnings,
  and offers each problem's one-click fix. It shows the server's hashes of the
  study as it stands (canonical config, statistical plan, pre-registration
  draft), asked for through the pre-registration render call while the step is
  open and again after each edit; the browser no longer computes a hash of its
  own. A study the server refuses becomes a blocking problem with the server's
  message word for word, and is withdrawn at the next edit. If the run service
  cannot be reached the page says so and blocks nothing. "Copy share link"
  copies a link that opens the setup with the study; it holds no key.
- The problems the setup lists include what the pre-registration will warn
  about, so that "Ready" means nothing is left to check: a model without a
  pinned checkpoint, supervised sessions whose questionnaires and target
  pool are not pinned by their hashes, and a judge that is sent a
  temperature. They are warnings. The cross-references the server checks
  (an id used twice, an arm on a model or seed the study does not list, an
  observed arm without session settings, two quantum arms or a repeated
  control seed in one comparison, an outcome listed twice, a margin for an
  outcome that does not decide the study), an arm whose own settings
  contradict each other and a setting outside what a study can hold are
  blocking problems that name the setting. The Study 1 template
  starts with one thing to fix and three to check.
- The setup has a study bar on every step and over the map. It stays under
  the site header while the page scrolls and shows the study's title, whether
  the study is ready ("Ready", or the number of things to fix), a
  "Steps | Map" switch, the state of the study's file, "Load study file",
  "Save study file", a gear and a "More" menu ("Save", "Save as…", "Start
  from Study 1 template", "Copy share link"). It replaces the two buttons the
  page header had. On a
  phone it takes two rows; Save and the gear stay in sight and loading a file
  moves into "More". The switch sets `?view=map` without reloading anything.
- "Save study file" writes the study to a `.study.yaml` file. Writing the
  file needs no server and is never refused: it works offline, signed out
  and for a draft with problems, and keys typed on the page are never in the
  file. After the file is written the page checks whether it will load back
  (see the entry on saving above). Where the
  browser has a save dialog (Chromium browsers), the first save asks where to
  put the file, later saves write the same file and "Save as…" asks again;
  the bar reads `Saved to <name>` once the file was written and closed, and a
  dialog that is closed saves nothing. Elsewhere the file is handed to the
  browser as a download, which the page cannot follow, and the bar reads
  `Downloaded as <name>`. Either state adds what is left to fix, and becomes
  `Changed since <name>` after the next edit. A file the server accepts
  loads back as the same study.
- Loading a study file is stricter and says more. A file is refused before it
  is read if it is larger than 200 kB or empty, and only one file is taken.
  The server's parse stays the authority, and what it returns must also be a
  study the page can hold. If the server cannot be reached, a complete JSON
  study is loaded with the note "Loaded without the server's checks."; a YAML
  file is not, and the page says it needs the server. A file that fails at any
  point leaves the study on screen, its draft and its file state untouched.
  A file that was saved with problems loads: the report says how many
  problems the server would refuse at launch and shows its messages on
  request, and the problems are listed under the steps. A whole number too
  large for a browser to keep exactly is refused with the setting it is in.
  Before a loaded study, the template or the study of a share link replaces
  work that is in no file, the page asks: "Save current study first",
  "Replace" or "Cancel"; the first replaces only after the study is in a
  file that loads back. A
  share link leaves the address bar once it is opened or declined. A report
  under the bar says what was loaded (arms, evals, hypotheses, or that the
  file has no design block), which evals the server does not have, and how
  many things are left to fix, with a link to them; a refusal is shown in the
  server's words with the setting it names. A study file can also be dropped
  anywhere on the setup page.
- The draft in the browser is kept safe in three cases. Two tabs on the
  setup follow each other's edits, saves and loads, so neither overwrites the
  other with an older study. A stored draft this version of the page cannot
  read is set aside and offered as a download before the template takes its
  place. And where the browser stores nothing for the site, the page still
  works and warns that closing it loses the draft.
- Unticking the last comparison on the Compare step and ticking one again
  keeps the roles the study had: the positive controls, the observed arm with
  its session settings, and whether the arms are replayable.
- The gear opens "All settings": every setting of the study in one long
  dialog, grouped as Study, Models & arms, Evals & repetitions, Design &
  analysis, Supervised sessions, Judge & narrative and Advanced. It includes
  the settings no step shows (the arm table, model list, seed list, estimate
  assumptions, pilot, display settings, narrative model and the recorded
  hashes). A filter finds settings by name, field label or key (`design.alpha`),
  "Problems only" keeps the settings with a problem, and each section lists
  its problems first with a link to the field. An edit applies at once to the
  study behind the dialog; the footer has the file state, Load, Save and Done.
- "Map" in the study bar shows the study as one hand-drawn diagram, from left
  to right: the question, the arms, the prompts and their repetitions, what is
  measured, the tests, the gates and the verdicts, with one lane per compared
  model and sampling preset. It can be dragged, zoomed with the wheel, a pinch
  or the toolbar, and walked with the arrow keys. Zoomed out it shows one
  sentence per node, at reading size the numbers of the design, and zoomed in
  every setting of a node. A node that holds others unfolds in place (the
  seeded runs of the controls, the evals of a tier, the outcomes of an eval).
  Every node and every value carries the state of the setup steps (enough,
  close, too few, no closed-form power, recommended, your choice, not
  runnable, set by the harness) as a colour, a glyph and a border, explained
  in a legend above the diagram that folds; it starts unfolded where the
  window is at least 1280 px wide and 960 px tall. The map is as tall as the
  rest of the window, so the whole diagram is in sight without scrolling the
  page. The view, the unfolded nodes and the legend stay as they were left
  when a setting changes or the steps are opened and closed. "Outline" shows
  the same graph as a nested list with the same states and buttons; it is
  what a window of 760 px or less starts with. The nodes sit where the layout
  puts them and cannot be dragged to other places.
- The study can be edited on the map. A node opens a pencilled popover that
  hangs on it and moves with the map; it holds the node's facts, the fields
  of its settings and its problems with their one-click fix. A test's popover
  has its hypothesis card; a verdict's has the verdict stamps, the decision
  rules and what `pD`, `pE` and `alpha` stand for. The fields are those of
  the setup steps and of
  "All settings", so an edit changes the map, the steps and the stored draft
  at once. A chip opens the popover on its own field; "Open in all settings"
  and a node's problem badge open the settings dialog. Escape closes the
  popover and returns to the node, and the Tab key stays inside it while it
  is open. Settings that are not a node are pencilled beside where they
  belong and open the same popover: the false-finding rate and the Holm
  family at the tests, power and completeness at the gates, the narrative
  model at the verdicts, authors and contact under the question, the sampling
  envelope on the quantum arm, the session settings on the observed arm and
  the judge on every eval it scores. In "Outline", "Open" on an entry shows
  the same fields, problems and prompts under it.
- An eval on the map opens down to its single prompts
  (`GET /api/cards/{id}/items`): a list with each prompt's place, id and
  first line, read a page at a time; the picked prompt in full, turn by turn;
  and the told cell to read it in where the eval has told cells. Prompts
  beyond the study's chosen count are struck through and marked "not in this
  study". Under a prompt the map says which arms answer it and how often,
  what is read off the answers and which hypothesis that feeds. An eval whose
  prompts cannot be listed before a run says why (built from the study's own
  generations, downloaded at run time, inside an Inspect task, computed from
  the token log of the other evals, or the task module's error), and without
  the run service the map
  says that prompts need it.
- Resting the pointer or the keyboard on a node of the map keeps everything
  upstream and downstream of it inked and quiets the rest; a verdict stamp
  shows the rule that leads to it. Nodes lean a couple of pixels toward a
  nearby pointer and the notes shy away, except on touch and under reduced
  motion. A note or a node the Tab key reaches is brought into view.
- Between 761 px and 1180 px of window width the setup steps lie across the
  top, as on a phone: from 961 px the estimate is beside the step, below that
  under it.
- A measure that generates nothing of its own (a token-level audit, a
  session measure) says "no tokens of its own" where it said
  "0 tokens / arm". The note on repetitions says that fewer
  repetitions call for more prompts, as the sample-size rating beside it
  shows.
- The results page of a designed study shows the verdicts the script computed
  (`tables/decisions.json`). The study verdict comes first, in one sentence; a
  pilot run, or a full run after the first, is marked "Not confirmatory" with
  the reason. Each hypothesis then has its own block: the verdict as a stamp,
  the sentence from the table word for word, the estimate with its interval,
  the raw and Holm-adjusted p-values, the margin and the equivalence test
  where one is registered, the negative control, positive control and
  completeness gates as "Passed" or "Failed", and the reason of an
  inconclusive, invalid or unavailable verdict. Every number is the one in the
  table; a p-value the table rounded to 0 is printed as "< 0.000001". The
  comparison rows of such a study are listed without an adjusted-p column and
  without the three-seed claim text, which belong to legacy analyses only. A
  legacy analysis renders as before.
- The pre-registration page lists the sections of the document and links to
  each. For a designed study "Hypotheses", "Decision rules" and "Sample size"
  come first; the results page links straight to the decision rules.
  Expectations are no longer edited on this page (they are written in the
  Design step) and no longer block rendering: a document without them says
  so. Copying the share link no longer puts `?c=` back into the address bar,
  where a reload would have put the copied study back over later edits. A
  document rendered before the draft changed in another tab is marked stale.
- The launch dialog says what a pilot is: it runs the study as configured, is
  labelled non-confirmatory and does not set margins. For a designed study it
  also says that only the first full run can be confirmatory. The earlier
  text promised "a few prompts per eval", which the harness never did. The
  kind of run selected when the dialog opens follows the study's pilot
  setting as it stands then ("Preselect a pilot run in the launch dialog");
  both kinds stay on offer.
- Documents for the above: `docs/ANALYSIS_PLAN.md` has a "Designed studies"
  section (the design block, the hypotheses, the recommended design and
  repetitions, the permutation count, the Holm families, the decision
  matrix, the control gates, token-stream outcomes, what is confirmatory and
  who may add runs, the sample-size ratings with their variance bound and
  their limits, and what the harness does not do); `docs/DESIGN.md` records
  decision D-15; `docs/SETUP_GUIDE.md` section 6 describes the five steps,
  the study bar, loading, All settings, saving, share links, the draft in the
  browser, what Review & launch lists, and the map; `docs/API.md` gains
  `POST /prereg/render` and `POST /runs/{id}/analyse`, a section on
  launching a study that is already registered and one on study files with
  problems; `docs/add-an-eval.md` says what a new eval needs to decide a
  designed study and to show its prompts; `docs/ACCEPTANCE.md` names the
  tests that cover each part.
- The web app now uses the entropic.science design. It has the same palette
  (plum, wine, copper and peach on paper), the same type (EB Garamond for
  headings, navigation and prose, Inter for forms, Space Mono for labels and
  data) and the same header and footer, with the woven-hexagon mark, a wine
  bar beside every page title, hand-drawn copper rules under page headers and
  the film grain over the page. The fonts ship with the app, so the content
  security policy is unchanged and no page loads anything from a third party.
  No route, label or behaviour changed.

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
