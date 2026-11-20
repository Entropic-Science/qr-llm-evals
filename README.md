# qr-llm-evals

**Pre-registered research harness for quantum-random LLM sampling** —
[evals.entropic.science](https://evals.entropic.science).

An LLM chooses each token by sampling from a probability distribution. Normally
that choice is settled by a pseudo-random number generator (PRNG). Entropic
Science's `qr-sampler` replaces it with a hardware quantum random number
generator (QRNG). This harness lets anyone:

1. pick models, sampling methods (greedy / temperature 1 + min-p / creative),
   the entropy source (QRNG vs seeded PRNG) and the determinism mode;
2. select evals from a reviewed registry (`docs/evals.md`) with live token and
   wall-clock estimates;
3. export a **pre-registration document** (hashed) before anything runs;
4. run the study server-side — optionally in **supervised mode**, where
   observers watch the generation stream in timed sessions;
5. get script-generated statistics, tables and SVG figures, plus a clearly
   labelled LLM-written summary whose numbers are verified against the tables.

The first study compares supervised QRNG creative sampling against seeded PRNG
(seeds 1, 7, 42) on the full registry — identical pipelines up to the entropy
source.

## Layout

| Path | What |
|---|---|
| `backend/` | Python 3.11 FastAPI backend (`qr_llm_evals`), pytest suite |
| `apps/web/` | React 19 + Vite 7 + Tailwind 4 SPA (`@qr-evals/web`) |
| `packages/contracts/` | Generated API contracts (`@qr-evals/contracts`) |
| `evals/` | The eval registry — one YAML card + task module per eval |
| `docs/` | Design, setup guide, API, analysis plan, determinism briefing, `evals.md` (generated) |
| `infra/` | systemd units, env templates, `ENV_REFERENCE.md` |
| `scripts/` | `check.py` (one-command gate), generators, smoke, deploy |

## Develop

```bash
# Python 3.11 (the box interpreter); Windows: C:\Python311\python.exe -m venv .venv
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pnpm install                    # Node 24, pnpm 10
python scripts/check.py         # the whole gate; see AGENTS.md
python -m qr_llm_evals.entrypoint # configure keys and writable state first
# In a second shell: VITE_API_BASE= pnpm dev (uses the local /api proxy)
pnpm dev                        # defaults to the hosted API unless overridden
```

Read `AGENTS.md` first — it holds the layering rules, invariants and commit
conventions. Operators: `docs/SETUP_GUIDE.md`. Contributing an eval:
`docs/add-an-eval.md` and `CONTRIBUTING.md`.

## Deploy and operate

Follow `.\docs\SETUP_GUIDE.md` for the Linux box, keys-file permissions, and
the Cloudflare Workers web app. Deployment scripts are **on-box**, committed-tree only:
`.\scripts\deploy.sh` builds a retained release and verifies commit-specific
health; `.\scripts\rollback.sh` restores the previous compatible release.
Runtime data stays outside releases. No script changes DNS, starts a supervised
session, or restarts the shared vLLM/QRNG services.

The optional Inspect/analysis/metrics dependencies require additional release
preparation; a healthy core API is not full-study readiness. Run the readiness
check and ~200-prompt pilot before freezing/publishing the full-run prereg.
Architecture and D-1 through D-14 are in `.\docs\DESIGN.md`; deployment environment
settings are catalogued in `.\infra\ENV_REFERENCE.md`.

## Licences

- Code: [Apache-2.0](LICENSE)
- Documentation, pre-registration and results prose: [CC BY-SA 4.0](LICENSE-docs)
- Raw study data: [CC BY 4.0](LICENSE-data)

Part of the [Entropic Science](https://github.com/Entropic-Science/community)
open research community.
