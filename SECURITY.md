# Security policy

## Reporting

Report vulnerabilities privately via
[GitHub private vulnerability reporting](https://github.com/Entropic-Science/qr-llm-evals/security/advisories/new)
or by email to **security@entropic.science**. Please do not open a public issue.
We acknowledge reports within 5 working days.

## Scope

- The backend (`backend/`) and its deployment (`infra/`) on the `qr-server` box.
- The public API at `evals-api.entropic.science` and the SPA at
  `evals.entropic.science`.

Out of scope here (report to the owning repo): the shared vLLM/qr-sampler
engine, Qbert0G, the `qr-llm-api` edge, and entropic.science itself.

## What counts

- **Key leakage** — a launch bearer key or a BYO provider/judge key appearing in
  logs, artifacts, `run.json`, error envelopes or the browser.
- **Provenance laundering** — any path by which PRNG output can be labelled
  `quantum`, engine fallbacks go unrecorded, or arm envelopes diverge beyond the
  entropy-source keys.
- **Run control without the host token** — start/resume/pause/stop or
  questionnaire submission by a party that is not the session host / observer.
- **Quota bypass** — running more than `QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY` or
  exceeding `QR_EVALS_MAX_INFLIGHT` on the shared engine.
- Client-controlled `qr_*` / `vllm_xargs` reaching the engine.

## Supported versions

Only `main` is supported; fixes ship as new releases, not backports.
