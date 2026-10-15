# 0.1.0 acceptance ledger

**Status: NOT ACCEPTED.** This records observations, not a release declaration.
S28 remains InProgress until the mandatory smoke and full gate pass. Automated
checks do not substitute for the study lead's manual or scientific sign-off.

## Live replay repair investigation — 2026-09-15

**Still blocked; live-repair step and S28 remain InProgress.** No serving-stack
restart or cross-repository edit was performed. No real artifact was deleted,
quarantined, relabelled, or marked complete.

Harness repairs:

- Readiness dispatches append a fresh UUID to the readable request-ID prefix.
  The seed, prompt, and sampling envelope are unchanged. Capability-log lookup
  uses the actual dispatched ID, so an old probe file cannot satisfy a new check.
- Readiness rejects cancelled, missing-terminal, error, and filtered completions
  instead of accepting an incomplete token prefix as replay evidence.
- Both client paths stop reading at `[DONE]` and refuse dispatch when the cancel
  token is already set. Regression tests cover closure and preserved usage.

These are code-level defects, **not a proven root cause of the live stall**.
HTTP read timeouts remain per-read limits; the client still lacks an absolute
stream deadline and cannot promptly observe cancellation during a silent read.
Response closure signals disconnect; actual engine abort is not confirmed.

Verification: focused client/readiness tests passed (65 passed, one live test
skipped), including ten added regression cases. Three full gate attempts were
collected, none is recorded as a clean full-gate pass:

- `.\.cache\live-repair-gate.log`: exit 1, only the Hypothesis input-generation
  speed health check in `.\backend\tests\test_seeds.py` failed; 527 tests passed.
- `.\.cache\live-repair-gate-final.log`: exit 1, only formatting failed after
  a docstring edit; 528 Python tests passed / 10 skipped. Formatting was fixed.
- `.\.cache\live-repair-gate-verified.log`: exit 1, the same Hypothesis speed
  health check recurred (six inputs in 1.80 seconds); 527 passed / 10 skipped.
  Lint, formatting, strict types, security, generated-file checks, 49 web tests,
  10 contract tests, web build and the complete offline smoke passed. No timing
  health check was disabled and no unrelated seed test was changed.

Observed evidence (times Europe/Budapest):

- At about 18:46, engine health and model discovery returned HTTP 200. The
  reported model was `qr-llm`; entropy health's sampler snapshot was about 4,673
  seconds old. Global token count was 1,562,477. Stale health alone does not
  establish current per-request quantum provenance.
- The existing replay acceptance test, started before these edits, again failed
  to produce a terminal pytest summary within its 300-second watchdog window.
  At 18:49:59, py-spy showed the main thread in a dry-run `EngineProbe.generate`
  HTTP body read inside `run_readiness`, before seeded replay. The shell wrapper
  did not preserve pytest's exit status; its own exit 0 is **not a passing test**.
  Output is retained in `.\.cache\live-replay-diagnosis.log`.
- A separate one-token seeded request with a fresh ID,
  `readiness/diagnosis/cd9e953b55114f1c9471aacbd9a7fd3a`, received HTTP 200 and
  `text/event-stream` headers but no body bytes, then raised `ReadTimeout` at
  15.5 seconds. Global token count changed from 1,581,677 to 1,582,189 during
  the check. Other engine work may account for that counter; it is not evidence
  of progress for this request. See `.\.cache\live-unique-request.log`.
- Local harness port 8830 still refused connections; the public API hostname
  still failed DNS lookup (11001). Deployed launch-key and idle-SSE acceptance
  remain unperformed. Five solo replays for each of 1/7/42 and the five-arm
  real-generation acceptance remain **unconfirmed**.

Operator handoff: inspect engine request admission, scheduler state and abort
logs for the fresh ID above and the preceding `readiness/*/dry_run` requests.
Establish whether they queued, entered decoding, errored without ending SSE,
or remained active after disconnect. Do not restart a shared engine without
coordinating its other work. Then rerun the real replay and five-arm checks;
do not substitute synthetic provenance or a process watchdog for production
deadline/cancellation behavior. The interrupted historical runs listed below
remain preserved.

## Supervised integration repair — 2026-09-15

The production factory now shares the API session registry and admits each
supervised generation through the session controller, for both thin and Inspect
dispatch. Unsatisfied session/presence/pre gates do not consume global engine
capacity. Completion releases the real slot, updates the EMA, and drains manual
or natural boundaries without reserving phantom work. Generation and Tier-0
records inherit the admitted session; unsupervised controls remain untagged.

The repaired smoke crosses a real mid-run break, proves no supervised dispatch
before session start or during the break, enforces the prior post questionnaire,
and requires an explicit second-session resume without duplicate samples.
The integration regression suite additionally exercises tiled limits, a natural
boundary *before* the target based on expected generation duration, pause/abort,
late-observer pre gates, runtime advisory timers, Inspect cached resume, and
fresh-process session/questionnaire recovery.

**Verification:** final full one-command gate exited **0** on 2026-09-15:
518 Python tests passed / 10 skipped; 49 web tests and 10 contract tests passed;
lint, format, strict types, security, generated-file checks, web build and smoke
all passed. The nine added integration cases are in
`.\backend\tests\test_session_integration.py`. The strengthened smoke also
passed standalone, retaining its 40-generation, five-arm, analysis/figure/hash
and 12 byte-identical replay-table assertions. Full log:
`.\.cache\session-integration-gate-final.log` (ignored).

Recovery closes an interrupted open window at recovery time, an upper bound
rather than evidence of continuous observation. It never restores `running`:
keys, fresh presence/pre and an explicit new sitting are required. Run-level
start/resume and session-level start/resume remain separate host-authorized gates.

**S28 must still be resumed for final acceptance.** This repair is API/runner
integration evidence, not live-hardware or browser acceptance. In particular,
the existing host console's same-session Resume flow must be checked against
the per-sitting API contract (create a new session, collect fresh pre, then
resume); browser wiring was outside this repair's owned files. No manual or
scientific sign-off below is inferred from the synthetic tests.

## Original blocking integration finding (historical S28 baseline)

The following finding records the pre-repair failure, not the current factory.

The production factory in `.\backend\src\qr_llm_evals\api\app.py` does not
connect supervised session control to the runner. The runner's default tagger
in `.\backend\src\qr_llm_evals\runner\orchestrator.py` assigns
`window=unsupervised`, `session_id=null` to every generation, including the
`qrng_supervised` arm. No production caller invokes the session controller's
`slot_freed` / `generation_finished` boundary hooks. Session HTTP transitions
can succeed without controlling actual generation dispatch.

**Consequence:** an observer session cannot currently establish the required
supervised-vs-unsupervised comparison or enforce generation-boundary breaks.
Passing isolated state-machine tests is insufficient. Do not run Study 1 or
publish these labels as evidence of supervised exposure.

Structured repair step: **Fix supervised session–runner production integration
(S28 blocker)**, ID `f071f7a8-bfa7-4949-95bb-5342135727d4`, placed before S28.
After it passes, resume S28 and rerun the full acceptance gate. In particular,
extend the smoke to prove dispatch waits during the break and resumes only
after the next host action; do not merely patch the emitted labels.

## Automated verification

| Check | Who | When | Result / evidence |
|---|---|---|---|
| Full one-command gate | S28 agent | 2026-09-15 | **FAILED (exit 1), smoke only.** Lint, format, strict types (107 source files), security, tests (509 passed / 10 skipped), schema drift, eval-doc drift, web typecheck/lint/tests/build all passed. Web: 49 tests; contracts: 10 tests. Final static recheck also passed |
| Offline pilot and analysis | S28 agent | 2026-09-15 | Passed: 40 synthetic generations, two thin fixture cards, five baseline arms, output/thinking SSE, API-logprob Tier 0, prereg/config/plan hash checks, figures, checked narrative, bundle export and 12 byte-identical replay tables |
| Supervised E2E | S28 agent | 2026-09-15 | **FAILED:** both thin cards on `qrng_supervised` have no supervised window/session tags; smoke exits 1 |
| Launch-key regression | S28 agent | 2026-09-15 | Passed: valid launch 201; unknown 401; disabled 403. File/probe-mode 403 propagation covered by offline tests |
| Real engine readiness, five solo replays per seed, 64-token requests | S28 agent | 2026-09-15 | **Not accepted:** first live attempt failed 2 tests on the engine-health response shape; parsing fixed with regression tests. Full live rerun spent several minutes waiting in seeded replay SSE, then reported a failure (`F`) and entered generation. The process was terminated at about nine minutes; no completed rerun or detailed final failure summary is claimed |
| Deployed health/cards + 60 s idle SSE | S28 agent | 2026-09-15 | **Not verified:** `evals-api.entropic.science` failed DNS resolution (`getaddrinfo`, Windows 11001). No run created and no SSE test performed |
| Live 20-prompt × five-arm pilot, analysis/figures and prereg hash | Operator / study lead | — | Pending on-box readiness, credentials and integration repair; synthetic smoke is not this pilot |

Local command logs: `.\.cache\s28-gate.log`, `.\.cache\s28-live.log`,
`.\.cache\s28-live-full.log`, `.\.cache\s28-live-stall.txt`,
`.\.cache\s28-deployed.log` (ignored; not research artifacts). Commands and
status summaries here are the durable handoff; logs may be regenerated.

Last offline smoke hash evidence (synthetic run only):

| Digest | Value |
|---|---|
| Config SHA-256 | `86e06d8c1af09efabb5bd10dd05baec895dfb72e8c60e7cced82ae50c2b807b0` |
| Prereg SHA-256 | `48553c36440f800652d71e1f46efabc4611fbd546d8896a86d762c3080a179ad` |
| Plan SHA-256 | `664af0052a1531c0b20fca245bc4f01cddcaef4d7f8b9eb14638bda747567b4e` |

Readiness timestamps change between attempts, so the prereg digest is expected
to change when that input changes. Each attempt checks its own frozen hash and
reproduces all 12 tables from its exported data. Backend warnings were dependency
deprecations in the optional Inspect test; the web build also emitted non-fatal bundle
size and dependency annotation warnings. Neither changes the failed smoke result.

### Offline oracle scope

`.\scripts\smoke.py` runs the real app lifespan and production orchestrator
factory with injected stub engine/judge transports, two small synthetic cards
derived from the existing test task, and one passive Tier-0 primary. The passive
card is selectable **only in the synthetic registry** to exercise unavailable
primary figures on the API-logprob fallback path; it does not enable DEP-1 or
change the public registry. Synthetic QRNG labels mean the stub simulated
healthy QRNG provenance, not that quantum hardware was used.

The simulated observer submits pre/post questionnaires, uses host controls,
crosses a manually requested session boundary and exercises a negative break
timer. The smoke also checks actual generation tags; this currently fails.
It does not yet demonstrate a natural timed production boundary or safe
multi-sitting generation resume. These are mandatory repair acceptance cases.

The replay moves exported analysis output aside before invoking the CLI, so
pre-existing bundled tables cannot accidentally count as recomputation.
Only this temporary synthetic tree is cleaned up. Real live probe generations
are retained under the configured data root's `live-acceptance` subdirectory.
The initial five real generations are retained under
`C:\var\lib\qr-llm-evals\live-acceptance`; the later full-suite attempt set
`QR_EVALS_DATA_DIR` to the repository's `.\data` directory. No retained run was
deleted to recover from a failed check.
The interrupted `live_7b1ec6c00ebf9f58` record remains under that directory;
its `running` state is an orphaned process state, **not** evidence of a live
worker. Initial stack inspection showed seeded-replay HTTP reads; the saved
final stack shows the subsequent generation's orchestrator waiting for workers.

## Manual acceptance — spec §5.4

Record reviewer, timestamp and observed result for **each** row. Use a second
browser for group tests. Leave unperformed checks Pending; do not infer them
from unit tests or screenshots of another step.

| Check | Who | When | Result |
|---|---|---|---|
| Study 1 configuration → exported prereg in under five minutes; record elapsed time | Study lead | — | Pending |
| Deterministic toggle opens briefing matching `.\docs\determinism.md` | Reviewer | — | Pending |
| Supervised briefing is direct, neutral, and frames observer influence as an unproven hypothesis | Study lead | — | Pending |
| Initial intention timer can go negative; no generation starts automatically | Reviewer | — | Pending |
| Live prompt/output/thinking render correctly in single and tiled views | Reviewer | — | Pending |
| Natural session boundary stops after completed generations within a few minutes of target | Operator | — | **Blocked by production session–runner integration** |
| Expired break/refocus never starts another sitting without explicit host start/resume | Reviewer | — | **Blocked at generation-dispatch level** |
| Observer joins from second browser; presence and intention are visible and recorded | Reviewer | — | Pending |
| Pre questionnaire blocks observation/start; prior post questionnaire blocks next session unless observer explicitly leaves | Reviewer | — | Pending end-to-end (HTTP gate tested offline) |
| Every primary shows source treatment beside all three seed contrasts and available epsilon controls | Study lead | — | Pending |
| Claim rule is stated; unavailable/missing controls preclude a claim rather than becoming measured zeros | Study lead | — | Pending |
| Narrative is labelled LLM-written and withheld on unverified numbers | Reviewer | — | Pending manual check (offline tests present) |
| Figures/tables/bundle/prereg/licence downloads work and hashes verify | Reviewer | — | Pending deployed check (offline replay passed) |
| PRNG is never rendered as quantum; fallback-tainted QRNG is not presented as clean quantum | Reviewer | — | Pending browser check |

## Study release gates — PRD §2 and §7.4

| Required order / definition of done | Who | When | Result |
|---|---|---|---|
| G1: public minimalist UI and working configuration/export flow | Operator + reviewer | — | Pending deployed/manual acceptance |
| G2: paired envelopes, honest provenance, reproducible seeded controls and real supervised dispatch | Study lead | — | Blocked as above; live evidence pending |
| G3: frozen-plan scripts, figures, labelled verified narrative, permanent results | Study lead | — | Offline pipeline passed; public acceptance pending |
| G4: registry + generated eval documentation + contribution brief | Registry reviewer | — | Generated-file gate checks passed; manual admission review pending |
| Readiness first: hardware health, envelope acceptance/equivalence, achieved determinism and Tier-0 capability | Operator | — | Pending real-engine evidence |
| Independent pilot (~200 prompts per PRD; 20-prompt live acceptance is only a smoke) and pilot-derived margins | Study lead | — | Pending |
| Freeze exact model/judge/rubric/dataset/observer asset revisions, expectations, hypotheses/claim rules and prereg hashes | Study lead | — | Pending; template placeholders are not frozen evidence |
| G5: approved Study 1 prereg then full run with controlled supervised sittings, then analysis and publication | Study lead | — | Not authorized by this ledger |

## Reproduction commands

Run from the repository root with Python 3.11. The development extra includes
matplotlib so the figure oracle is not silently skipped on a fresh install.

```powershell
$env:PYTHONUTF8 = '1'
$env:QR_EVALS_DATA_DIR = (Join-Path (Get-Location) 'data')
.\.venv\Scripts\python.exe .\scripts\check.py
.\.venv\Scripts\python.exe .\scripts\smoke.py
.\.venv\Scripts\python.exe -m pytest .\backend\tests\live\test_box.py --run-live -rs
```

On the box, use configured `QR_EVALS_*` settings (the environment is read only
by the configuration module). For an SSH-forwarded engine/API, use the
`--live-engine`, `--live-api` and `--live-model` pytest options. A reachable
unhealthy engine or a rejected envelope is a **failure**, not a connectivity
skip. The older live tests still expect the configured on-box services;
run the full `pytest --run-live` there after reachability is established.
The new on-box tests have a 300-second per-test wall-clock watchdog that dumps
stacks and exits the test process on a stalled stream. This is an explicit
failure, never a skip; HTTP read timeouts alone do not bound an idle SSE stream.
Structured live-repair step: **Resolve stalled live seeded replay before S28
acceptance**, ID `6452a91e-2d66-42b8-8ee4-852d27dfe0bb`, placed before S28.

Use `--live-key-file` and `--live-disabled-key-file` for private files containing
the two raw test keys. Never commit them or supply secrets as command-line
values. The server's registry contains hashes, not recoverable raw keys; the
tests do not provision or disable keys. A successful 201 probe is immediately
aborted and retained; it never starts generation.

```powershell
# Existing idle run: read-only test, no launch key required.
.\.venv\Scripts\python.exe .\scripts\smoke.py --live https://evals-api.entropic.science --run-id run_ID
# Without --run-id, prompt for a key, create an idle probe and abort it afterwards.
.\.venv\Scripts\python.exe .\scripts\smoke.py --live https://evals-api.entropic.science
```

The live smoke requires 60 seconds of actual idle streaming, multiple
keep-alives, no heartbeat gap over 25 seconds and no sample events. It fails
on early EOF, buffering, timeout, wrong content type or an active run. Public
DNS, a listening tunnel and a suitable idle run are operator prerequisites.
