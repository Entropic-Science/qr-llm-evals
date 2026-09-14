# Contributing to qr-llm-evals

Thanks for helping build an honest instrument. Two kinds of contribution are
common; both go through a pull request and the same gate.

## 1. Adding an eval to the registry

Follow `docs/add-an-eval.md` (the same text the UI tells you to hand to your
coding agent). In short:

1. Create `evals/<id>.yaml` (card schema in `docs/DESIGN.md` §4.3) and, for
   thin-runner cards, `evals/<id>/task.py` with `build_items()`,
   `build_messages()` and `score()`.
2. Cards must use **free generation** (no forced choice), report a **per-prompt
   statistic from k ≥ 10 samples**, fix the judge (temperature 0, a different
   model than the target), pin the **dataset revision**, and declare
   `thinking`, `k`, `max_tokens`, `tier` and derived **estimates**.
3. Regenerate the registry doc: `python scripts/gen_evals_doc.py`.
4. Run `python scripts/check.py` and fill in the eval-card checklist in the PR
   template.

Registry reviewers (`.github/CODEOWNERS`) approve cards.

## 2. Changing the harness

- Read `AGENTS.md` (layering, invariants, Windows notes) before coding.
- Keep `config.py` the only `os.environ` reader and add a row to
  `infra/ENV_REFERENCE.md` for every new variable.
- Contracts are generated: change the pydantic schema, run
  `python scripts/export_schemas.py`, commit the output.
- Add tests beside the change; live tests use `@pytest.mark.live`.
- Add a `CHANGELOG.md` entry under *Unreleased*.

## Pull requests

- One logical change per PR, concise imperative commit subjects, no
  `Co-Authored-By` trailers.
- `python scripts/check.py` must be green locally; CI runs the same table.
- Never commit run artifacts (`results/`, `*.jsonl`, `*.eval`) or secrets.

## Conduct and security

See `CODE_OF_CONDUCT.md` and `SECURITY.md`.
