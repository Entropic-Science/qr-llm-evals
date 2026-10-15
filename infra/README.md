# Deployment assets

The operator runbook is `.\docs\SETUP_GUIDE.md`; architecture is
`.\docs\DESIGN.md`; all backend settings are in `.\infra\ENV_REFERENCE.md`.
This directory configures **only the harness**, never the shared engine or QRNG.

- **API unit**: `.\infra\qr-llm-evals.service`, dedicated `qr-llm-evals` user,
  one process on `127.0.0.1:8830`, preflight before binding, strict filesystem
  sandbox with writable state under `/var/lib/qr-llm-evals`.
- **Tunnel unit**: `.\infra\qr-llm-evals-cloudflared.service`, token-mode
  connector with its own environment file, no token on the command line.
- **Examples**: `.\infra\qr-llm-evals.env.example` and
  `.\infra\cloudflared.env.example` contain placeholders, not secrets.
- **Release scripts**: `.\scripts\deploy.sh` and `.\scripts\rollback.sh` run
  on the Linux box as root from a reviewed checkout. Deploy refuses all
  non-ignored dirty files, archives a commit, creates a fresh venv from hashed
  runtime pins, preflights as the service user, switches the release symlink,
  restarts, and verifies exact commit plus healthy engine. Failure restores
  the old release; neither script deletes releases or research artifacts.

## Release layout and operational limits

`/opt/qr-llm-evals/releases/<UTC>-<commit>/` is root-owned and not writable by
service users. `current` and `previous` are symlinks there. The data root is
outside all releases. A single `flock` serializes deploy and rollback.
Unit files in `/etc/systemd/system` link through `current`, so application and
unit definitions roll back together. Tunnel restart/token rotation is explicit
operator work; an API restart never tears down the tunnel.

Preflight and health must return success, including engine availability. Unlike
interactive entrypoint startup, deployment treats preflight exit 1 (engine
warning) as refusal, not permission to launch studies. Health is polled at most
15 times, each request bounded to 5 seconds, with 2-second pauses.

The runtime lock is for Linux/Python 3.11, not a Windows dependency lock.
Optional committed `.\requirements-analysis.lock`, `.\requirements-inspect.lock`,
and `.\requirements-metrics.lock` are consumed if present, with hashes and
runtime constraints. They are **not currently supplied**: do not claim a core
release can execute every selected card or produce all figures. Package build
is an editable install with `--no-deps`; build isolation may download build
requirements from `.\pyproject.toml`. Pin build tooling too before claiming a
fully offline/reproducible deployment.

Keep at least the previous compatible release and a verified backup. Rollback
is code rollback, **not database downgrade**; rehearse schema compatibility on
an offline copy before switching versions. Stop at a generation boundary before
maintenance. No script starts/resumes a supervised session or changes engine
settings. Never use a release-cleanup command on the data directory.
