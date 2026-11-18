# Repository Guidelines — qr-llm-evals

`qr-llm-evals` is the **pre-registered research harness** behind
[evals.entropic.science](https://evals.entropic.science): a Python 3.11 FastAPI
backend on the `qr-server` box (loopback `:8830`, fronted by its own cloudflared
tunnel at `evals-api.entropic.science`) plus a static React SPA on Cloudflare
Pages. It compares **quantum-random** token sampling (hardware QRNG via
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
packages/contracts/         @qr-evals/contracts — src/generated is written by scripts/export_schemas.py
evals/                      the eval registry: <id>.yaml cards + <id>/ task modules
studies/                    study templates (study1.yaml)
config/                     providers.yaml, models.yaml, questionnaires.yaml
docs/                       DESIGN, SETUP_GUIDE, API, ANALYSIS_PLAN, determinism, add-an-eval, evals (generated)
infra/                      systemd units, env templates, ENV_REFERENCE.md
scripts/                    check.py (the gate), export_schemas.py, gen_evals_doc.py, smoke.py, deploy.sh
```

## Layering (enforced by `backend/tests/test_layering.py`; imports point down only)

```
config, client, keys, cards, models,  layer 0: no internal imports except config
accounts
tier0, store, prereg                  layer 1
runner, inspect_bridge, supervised    layer 2
analysis                              layer 2: imports store/cards/tier0 only; analysis.stats is a leaf
api                                   top: nothing imports api
```

Forbidden anywhere in `backend/`: the `openai` SDK, `vllm`, `modal`.
`torch` / `transformers` / `sentence_transformers` only under
`analysis/metrics_heavy`, imported lazily (`[metrics]` extra). Importing the
package must be side-effect free (no network, no writes, no downloads).

## Invariants (do not break)

1. **The harness owns the sampling envelope.** `qr_*` keys come from
   `models/envelope.py` per arm; never forward client-supplied `qr_*` from the
   browser. All arms pin the same amplifier (`zscore_mean`, calibration 0,
   prefetch off); arms differ only in `qr_entropy_source_type` / `qr_seed` /
   `qr_bias_epsilon`.
2. **Never label PRNG output as quantum.** Provenance `quantum=true` only for
   the `qrng` source with zero engine fallbacks; any fallback taints the sample.
3. **Never persist keys.** The launch bearer key only gates; BYO provider/judge
   keys live in the run's in-memory `SecretBag`, are redacted from every log and
   error path, and never enter `run.json`. `keys_present` booleans only. An
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
7. **`docs/evals.md` and `packages/contracts/src/generated` are generated.**
   Change the source (`evals/*.yaml`, pydantic schemas) and regenerate; CI fails
   on drift.
8. **Every env var read in `config.py` has a row in `infra/ENV_REFERENCE.md`.**
   `config.py` is the only module that touches `os.environ`.

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
- pytest keeps `tmp_path` under `.pytest_tmp/` (see `pyproject.toml`).
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
