# Repository Guidelines — qr-llm-evals

`qr-llm-evals` is the **pre-registered research harness** behind
[evals.entropic.science](https://evals.entropic.science): a Python 3.11 FastAPI
backend on the `qr-server` box (loopback `:8830`, reached at
`evals-api.entropic.science` through qr-llm-api's cloudflared tunnel) plus a
static React SPA served as Cloudflare Workers static assets (`wrangler.jsonc`).
It compares **quantum-random** token sampling (hardware QRNG via
qr-sampler) against **seeded pseudo-random** sampling on identical pipelines,
runs a registry of evals against both, and produces pre-registration documents,
script-generated statistics/figures, and an LLM-written (but number-verified)
narrative.

The binding design is `docs/DESIGN.md` (from the spec); the eval registry is
`docs/evals.md` (**generated** — never hand-edit); operator steps are
`docs/SETUP_GUIDE.md`; every env var is catalogued in `infra/ENV_REFERENCE.md`.
This file is the developer/agent conventions.

## Layout

```
backend/src/qr_llm_evals/   FastAPI backend (see layering below)
backend/tests/              pytest; stub engine; live tests opt-in via --run-live
apps/web/                   Vite + React 19 + Tailwind 4 SPA (@qr-evals/web)
packages/contracts/         @qr-evals/contracts — src/generated is written by scripts/export_schemas.py;
                            fixtures/ holds the vectors and study files the backend and the web app
                            are both checked against (see invariant 7)
evals/                      the eval registry: <id>.yaml cards + <id>/ task modules
studies/                    study templates (study1.yaml); a study's own decision code, scripts
                            and runbook live in its folder (studies/thesis_prompt_or_physics/)
config/                     providers.yaml, models.yaml, questionnaires.yaml
docs/                       DESIGN, SETUP_GUIDE, API, ANALYSIS_PLAN, ACCEPTANCE, determinism, add-an-eval,
                            evals (generated), and the two pre-registration templates:
                            prereg-template.md.j2 (a study without a design block; frozen) and
                            prereg-design-template.md.j2 (a study with one)
infra/                      systemd units, env templates, ENV_REFERENCE.md
scripts/                    check.py (the gate), export_schemas.py, gen_design_fixtures.py, gen_checks_fixture.py,
                            gen_evals_doc.py, smoke.py, deploy.sh
```

The web app's setup is `apps/web/src/routes/configure/` (five steps listed as
data in `steps.ts`, one shared study in `StudyContext.tsx`). Every editable
field lives in exactly one panel (`apps/web/src/lib/panels.ts`,
`apps/web/src/components/study/panels/`); the steps, the "All settings"
dialog (`components/study/AllSettings.tsx`) and the study map
(`components/map/`, `lib/map/`) all draw those panels, and
`lib/panels.test.ts` fails when a key of `StudyConfig` has no panel. Do not
re-implement a field in a view.

## Layering (enforced by `backend/tests/test_layering.py`; imports point down only)

```
config, client, keys, cards, models,  layer 0: no internal imports except config
accounts
tier0, store, prereg                  layer 1
runner, inspect_bridge, supervised    layer 2
analysis                              layer 2: imports store/cards/tier0 only; analysis.stats is a leaf
api                                   top: nothing imports api
```

Where the designed-study modules sit:

```
cards/endpoint_meta.py      layer 0, pydantic only: what each headline endpoint is; outside the card
                            snapshot, so it never moves a cards_hash
cards/design_schema.py      layer 0, pydantic only: PlanDesign, HypothesisPlan, SampleSize, DecisionsTable
cards/power.py              layer 0, stdlib + the two above: sample-size ratings
cards/design.py             layer 0, cards only: eligible, recommend, recommend_k, project, plan_permutations,
                            control_can_fail, VERDICT_RULES, problems (PROBLEM_CODES names every code);
                            takes the config as dumped JSON because cards may not import models
analysis/decisions.py       imports cards and analysis.stats only: verdicts, run-order.json
runner/preview.py           layer 2: prompt preview for GET /cards/{id}/items; imports only the task
                            modules named in PREVIEW_SAFE
prereg/render.py            layer 1: may import cards and models, never analysis, so the designed
                            template states the gates and the roll-up in prose and a test ties the
                            verdict names to analysis/decisions.py
```

Where the result-check modules sit (`docs/ANALYSIS_PLAN.md`, "Result checks";
decision D-16 in `docs/DESIGN.md`):

```
cards/result_checks.py      layer 0, standard library only, no internal import: CHECKS_VERSION, the
                            flag kinds and stop rules with every threshold and sentence, the lists of
                            evals (PARSE_STATUS_CARDS, JUDGE_ALLOWED_VALUES), the detectors, the judge
                            reader (parse_score) and the Breaker, which decides whether a rule fired
store/schema.py             ResultFlag (a line of flags.jsonl), Halt (a stop, kept in Run.halts),
                            Run.checks_version, JudgeRow.status / stop_reason / reader_version; the
                            literals come from the catalogue
store/health.py             layer 1, cards and models only: summarise(), the health summary of a run
                            (counts per arm and eval, the stops, the cautions)
runner/orchestrator.py,     layer 2: raise the flags, write the taints and flags, feed the Breaker,
runner/judge_phase.py       store the Halt and pause; a resume builds a new orchestrator and Breaker
api/routes/run_health.py    GET /runs/{id}/health and the per-run counts, kept until a file changes
```

Nothing under `analysis/` reads a flag, a caution or a stop, and
`backend/tests/test_docs_result_checks.py` fails if a module there starts to.
A change to the catalogue (a kind, a rule, a threshold, a sentence, a list of
evals) is made together with `docs/ANALYSIS_PLAN.md` and, for a stop rule,
`docs/SETUP_GUIDE.md` ("When a check stops a run"); the same test compares
both with the catalogue. Such a change also moves the designed
pre-registration, so regenerate the fixtures (invariant 7), and raises
`CHECKS_VERSION` when it could flag or stop differently. A key of
`Halt.causes` or `Halt.counts` that is not a harness name (`HTTP nnn`, a
class name, a lower-case name such as `in_a_row`, `arm/eval`, or one of the
phrases in `CAUSE_PHRASES` of the catalogue) is stored as `other`: add a new
phrase to `CAUSE_PHRASES`, or widen `_HALT_NAME` in `store/schema.py` for a
new kind of name. The other text of a halt is only passed through the log
redaction (registered keys and known key shapes). The
orchestrator writes the `Run` record it holds, so code that changes
`run.json` from elsewhere starts from a fresh `read_run()`.

`apps/web/src/lib/power.ts` and `apps/web/src/lib/design.ts` mirror
`cards/power.py` and `cards/design.py` so the setup rates a study in the
browser. Change both sides together and regenerate the fixtures (invariant 7).
Two lists on the web side must equal the backend's, and tests compare them
with `design.json`: `REGISTRY_CODES` in `apps/web/src/lib/problems.ts` equals
`PROBLEM_CODES` of `cards/design.py`, order included, and `REASON_TEXT` in
`apps/web/src/components/results/verdict-text.ts` equals
`INCONCLUSIVE_REASON_TEXT`. The setup adds problems of its own (every
`draft.*`, `review.*` and `instruments.*` code, and further `compare.*`,
`measure.*` and `design.*` codes); those are not in `PROBLEM_CODES` and have
no backend counterpart.

A study that breaks rules spanning several fields is a draft. Only
`POST /config/parse` reads one (`models.schema.read_draft`), so that a file
the setup saved with problems loads again; every other caller validates with
`StudyConfig.model_validate` and must keep doing so.

Forbidden anywhere in `backend/`: the `openai` SDK, `vllm`, `modal`.
`torch` / `transformers` / `sentence_transformers` only under
`analysis/metrics_heavy`, imported lazily (`[metrics]` extra). Importing the
package must be side-effect free (no network, no writes, no downloads).

## Invariants (do not break)

1. **The harness owns the sampling envelope.** `qr_*` keys come from
   `models/envelope.py` per arm; never forward client-supplied `qr_*` from the
   browser. All arms pin the same amplifier (`zscore_mean`, calibration 0,
   prefetch off); arms differ only in `qr_entropy_source_type` / `qr_seed` /
   `qr_population_mean`. The last one is set only on a biased (ε) control arm:
   it lowers the amplifier's reference mean by ε standard errors, so every
   `u` becomes `Φ(z + ε)`. No arm that is compared as quantum against
   pseudo-random may carry it. The study's entropy conditioning
   (`StudyConfig.conditioning`, `qr_conditioning_*`) is sent only on arms that
   draw from the entropy server (`qrng`, `prng_resample`) and is omitted for
   `raw`; the field is never serialized when absent, so older studies keep
   their hash.
2. **Never label PRNG output as quantum.** Provenance `quantum=true` only for
   the `qrng` source with zero engine fallbacks; any fallback taints the sample.
3. **Never persist keys.** The launch bearer key only gates; BYO provider/judge
   keys live in the run's in-memory `SecretBag`, are redacted from every log and
   error path, and never enter `run.json`. `keys_present` booleans only.
   Text from outside the harness (an upstream error body, an exception
   message) passes through `SecretBag.redact` before it is stored in the run
   folder or sent as an event; `backend/tests/test_run_secrets.py` reads
   every file and event of such runs. An
   account's linked api.entropic.science key is verified once and dropped:
   `accounts.sqlite` keeps its SHA-256, name and fingerprint, and session
   tokens as SHA-256 only. Passwords are scrypt hashes. No plaintext secret is
   written anywhere, and `accounts.sqlite` (unlike `index.sqlite`) cannot be
   rebuilt, so it is never deleted.
4. **Never `rm` paid-for artifacts.** Runs cost QRNG time and GPU time;
   `store.quarantine()` moves, never deletes.
5. **Figures and statistics come from scripts, never from an LLM.** The
   narrative model may only restate numbers that appear in the tables
   (`analysis.narrative.verify_numbers`).
6. **Timers never auto-start a supervised session.** Every transition into
   `running` requires an explicit host `start`/`resume`.
7. **`docs/evals.md`, `packages/contracts/src/generated` and the design
   fixtures are generated.** Change the source and regenerate; CI fails on
   drift (the `schemas` and `evals-doc` rows of the gate).

   | Generated | Source | Regenerate with |
   |---|---|---|
   | `docs/evals.md` | `evals/*.yaml` | `python scripts/gen_evals_doc.py` |
   | `packages/contracts/src/generated/` (types, zod, `endpoint-meta.json`) and `packages/contracts/schema/` | pydantic schemas, `cards/endpoint_meta.py` | `python scripts/export_schemas.py` |
   | `packages/contracts/fixtures/study1.designed.json`, `power.json`, `design.json` and `backend/tests/fixtures/prereg/golden_designed.md` | `cards/power.py`, `cards/design.py`, `cards/endpoint_meta.py`, `cards/result_checks.py` (the "Result checks" section of the designed pre-registration is printed from it), `prereg/render.py`, `docs/prereg-design-template.md.j2`, the registry | `python scripts/gen_design_fixtures.py` |
   | `apps/web/src/components/run/checks-fixture.json` (the stops and health summaries the web tests read) | `cards/result_checks.py`, `store/health.py`, `store/schema.py` | `python scripts/gen_checks_fixture.py` |
   | `packages/contracts/fixtures/study1.study.yaml`, `study1.designed.study.yaml` | `apps/web/src/lib/study-file.ts` and the two JSON fixtures | `cd apps/web && UPDATE_STUDY_FIXTURES=1 pnpm exec vitest run src/lib/study-file.test.ts` |

   A regenerated designed fixture moves the pins `STUDY1_DESIGNED_PLAN_HASH`
   and, if the study itself changed, `STUDY1_DESIGNED_CONFIG_HASH` in
   `backend/tests/test_schemas.py`; update them in the same change and say
   why.
8. **Every env var read in `config.py` has a row in `infra/ENV_REFERENCE.md`.**
   `config.py` is the only module that touches `os.environ`.
9. **A study without a `design` block keeps its bytes.** Its config hash,
   plan, plan hash, analysis id, tables and pre-registration are pinned in
   `backend/tests/test_legacy_bytes.py` and
   `backend/tests/fixtures/prereg/golden.md`; the archived legacy bundles
   under `backend/tests/fixtures/bundle_mini/` and `bundle_wide/` are the
   inputs of those pins. Never recompute those pins, never regenerate
   `golden.md` or the two bundles, and never edit `docs/prereg-template.md.j2`,
   `studies/study1.yaml` or `packages/contracts/fixtures/study1.json`.
   Anything new for designed studies goes behind `config.design is not None`
   or `plan.design is not None`.
10. **Never promise what the harness does not do.** No UI copy, document or
    pre-registration text may describe a feature that does not exist. Today
    that means pilot subsampling, pilot-derived margins, grouped per-prompt
    scoring, arm interleaving, a replay control arm, a registration lock, a
    rating of the positive control, a claim of absence or an effect-size
    threshold on a token-stream outcome, and a narrative that is held to the
    verdicts. For the result checks it means a check that asks a model
    whether a reply makes sense, a flag that excludes a sample, scoring a
    stored reply again, flags for runs made before the checks, and score or
    judge checks on an eval that runs through Inspect.
    `docs/ANALYSIS_PLAN.md` ("Limits of designed studies" and, under "Result
    checks", "What the checks do not do") is the list; shorten it in the
    change that builds the feature.
11. **A flag is not an exclusion, and a stop is a pause.** A result-check
    flag (`flags.jsonl`) marks a sample and changes no analysis; exclusions
    stay with the four taint kinds, and no new taint kind is added for a
    check. Nothing is generated again, scored again or judged again because
    of what a reply says: only a sample without a completed reply and a
    judgement without a reply are tried again. The rules are constants of
    the harness, the same for every arm and study: no study, plan, card or
    env setting may change one. A check that stops a run stores a `Halt`
    and pauses; it never aborts, removes nothing and never resumes by
    itself (invariants 4 and 6).

## The gate

```bash
python scripts/check.py            # lint → format → types → security → tests → schemas → evals-doc → web → smoke
python scripts/check.py --only lint,types
python scripts/check.py --list
```

Run it before marking any step done or opening a PR. CI runs the same table via
`--only`. Rows whose tool does not exist yet skip with a printed reason.

Setup:

```bash
py -3.11 -m venv .venv && .venv/Scripts/activate   # Linux/macOS: python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"                             # + ".[inspect,analysis]" for the engine/analysis phases
pnpm install                                        # pnpm 10, Node 24 (.nvmrc)
```

Lock files: `requirements.lock` (runtime) and `requirements-dev.lock` (runtime +
dev tools) are **Linux/3.11 pins for the box**, generated cross-platform with
`uv pip compile --python-platform linux --python-version 3.11 --generate-hashes`
(see the header comment in each file). `pyproject.toml` keeps the ranges.

## Windows notes

- Set `PYTHONUTF8=1` (`scripts/check.py` sets it for its children). Every
  `open()` / `read_text()` / `write_text()` passes `encoding="utf-8"`.
- Build the venv with `C:\Python311\python.exe`; run tools via the venv
  interpreter (`.venv\Scripts\python.exe -m pytest`).
- pytest keeps `tmp_path` under `.pytest_tmp/` (see `pyproject.toml`). Each
  session clears that directory when it starts, so two pytest sessions in one
  checkout break each other: give a second one its own `--basetemp`.
- Line endings are normalised to LF by `.gitattributes`; prereg hashes are
  computed over LF bytes.

## Commit and PR rules

- Concise imperative subject (`Add readiness probe`, `Pin creative envelope`),
  body only when the *why* is not obvious. **No `Co-Authored-By` trailers.**
- One logical change per PR; fill the PR template, including the eval-card
  checklist when touching `evals/`.
- Add a `CHANGELOG.md` entry under *Unreleased* for user-visible changes.
- Never commit `results/`, `*.jsonl` outside fixtures, `*.eval`, `.env`, or
  `api-keys.json` (`backend/tests/test_hygiene.py` checks tracked files).
