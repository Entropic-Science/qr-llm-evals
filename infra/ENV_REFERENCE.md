# Environment reference — every variable the backend reads (no secret VALUES live here)

This is the one catalogue for every variable in `/etc/qr-llm-evals/qr-llm-evals.env`
on the `qr-server` box. `backend/src/qr_llm_evals/config.py` is the **only**
module that touches `os.environ`; `backend/tests/test_env_reference.py`
machine-checks that every `_env_*(e, "NAME")` read there has a row in this file,
and that every row here is still read (or is listed as operator-only).
**Adding an env var read by the backend requires a row here.**

> **Thin-client scope.** The harness runs no vLLM engine and no QRNG daemon. The
> shared inference tier (`qr-sampler-vllm.service`, loopback `:8000`) and the
> entropy tier (`qbert0g.service`) are owned by the qr-sampler qr-server profile;
> no engine knob is ever set here. Sampling envelopes (`qr_*`) are built per
> request by `qr_llm_evals.models.envelope` — never from the environment.

Each row gives **name / required / example / source**.

## Serving stack

| Variable | Required | Example | Source / notes |
|---|---|---|---|
| `QR_EVALS_VLLM_ORIGIN` | no (default `http://127.0.0.1:8000`) | `http://127.0.0.1:8000` | Shared vLLM + qr-sampler origin (spec D-1). A trailing `/v1` is stripped. |
| `QR_EVALS_EDGE_ORIGIN` | no (default `http://127.0.0.1:8820`) | `http://127.0.0.1:8820` | qr-llm-api edge; used only when `QR_EVALS_KEY_VERIFY=probe`. |

## Launch gate and quotas (spec §2.4)

| Variable | Required | Example | Source / notes |
|---|---|---|---|
| `QR_EVALS_KEY_VERIFY` | no (default `file`) | `file` or `probe` | `file` = constant-time SHA-256 match against the keys file; `probe` = one `max_tokens: 1` request to the edge. |
| `QR_EVALS_KEYS_FILE` | when `file` (default `/etc/qr-llm-api/api-keys.json`) | `/etc/qr-llm-api/api-keys.json` | qr-llm-api keys file (digests only); must be group-readable by `qr-llm-evals` (`docs/SETUP_GUIDE.md`). |
| `QR_EVALS_MAX_ACTIVE_RUNS_PER_KEY` | no (default `1`) | `1` | Active runs per launch key; over it ⇒ `429` + `Retry-After`. |
| `QR_EVALS_MAX_INFLIGHT` | no (default `3`) | `3` | Global in-flight generation cap; keep ≤ engine `--max-num-seqs` minus a reserve for other tenants. |

## Generation

| Variable | Required | Example | Source / notes |
|---|---|---|---|
| `QR_EVALS_PRIORITY` | no (default `0`) | `0` | vLLM `priority` sent with every generation (priority scheduling on the shared engine; lower = sooner). |

## Storage and Tier 0

| Variable | Required | Example | Source / notes |
|---|---|---|---|
| `QR_EVALS_DATA_DIR` | no (default `/var/lib/qr-llm-evals`) | `/var/lib/qr-llm-evals` | SQLite index + JSONL artifact root (spec D-4, §4.5). Never deleted; `store.quarantine()` only. |
| `QR_EVALS_TOKEN_LOG_DIR` | no (unset = DEP-1 unavailable) | `/var/lib/qr-sampler/token-log` | Engine-side per-request token log export (spec §2.6 DEP-1). Unset ⇒ `tier0_source=api_logprobs` (degraded, labelled). |

## HTTP server

| Variable | Required | Example | Source / notes |
|---|---|---|---|
| `QR_EVALS_BIND_HOST` | no (default `127.0.0.1`) | `127.0.0.1` | Loopback only; cloudflared fronts it (spec D-3). |
| `QR_EVALS_BIND_PORT` | no (default `8830`) | `8830` | Matches `infra/qr-llm-evals-cloudflared.service`. |
| `QR_EVALS_CORS_ORIGINS` | no (default `https://evals.entropic.science,http://localhost:5173`) | `https://evals.entropic.science` | Comma-separated browser origins allowed to call the API. |
| `QR_EVALS_LOG_LEVEL` | no (default `INFO`) | `INFO` | Root logger level; secrets are redacted by `SecretFilter` regardless of level. |

## Set by the operator, not read by the backend

| Variable | Required | Example | Source / notes |
|---|---|---|---|
| `PYTHONUTF8` | yes on Windows dev | `1` | Forces UTF-8 for the interpreter (Windows notes in `AGENTS.md`). |
| `TUNNEL_TOKEN` | yes on the box | *(token-mode connector)* | Read by `qr-llm-evals-cloudflared.service`, never by the backend. |

## DO NOT set — anti-list

Engine-side entropy knobs belong to the qr-sampler qr-server profile and must
never appear in this unit's environment:

* `QR_PRESET` — sampling presets are chosen per request by the envelope builder.
* `QR_ENTROPY_SOURCE_TYPE` — the entropy source is an **arm** property, never a process default.
* `QR_FALLBACK_MODE` — refused per-request key; engine-owned.
* `QR_GRPC_SERVER_ADDRESS` / `QR_GRPC_API_KEY` — Qbert0G is never called directly (spec §1.3).
* `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` — BYO keys arrive in the launch body and live only in the run's `SecretBag` (spec D-14); never in the environment.
