# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Repository scaffolding: licences (Apache-2.0 code, CC BY-SA 4.0 docs, CC BY
  4.0 data), agent conventions (`AGENTS.md`), CI workflow, issue/PR templates,
  Dependabot.
- `scripts/check.py` one-command gate with the ordered oracle table (rows for
  not-yet-existing tools skip with a reason).
- Backend package skeleton `qr_llm_evals` with `config.py` (the only
  `os.environ` reader) and `infra/ENV_REFERENCE.md`.
- Layering, env-reference and hygiene tests.
- pnpm 10 workspace with a minimal `@qr-evals/web` SPA and `@qr-evals/contracts`.
