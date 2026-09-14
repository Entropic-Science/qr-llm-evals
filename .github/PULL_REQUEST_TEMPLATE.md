## Summary

<!-- What changes and why. Link the issue / plan step. -->

## Checks run

- [ ] `python scripts/check.py` is green locally (paste the final `OK:` line)
- [ ] New/changed behaviour has tests beside it
- [ ] `CHANGELOG.md` entry under *Unreleased* (if user-visible)

## Eval-card checklist (required when touching `evals/`; registry admission B.1)

- [ ] **Free generation** — the task elicits open-ended text, not a forced choice
- [ ] **Per-prompt statistic from k ≥ 10 samples** — the endpoint is computed per prompt over ≥ 10 generations
- [ ] **Judge is fixed** — temperature 0, frozen rubric hash, a *different* model than the target
- [ ] **Card declares** `thinking`, `k`, `max_tokens`, `tier`, `engine`
- [ ] **Dataset revision pinned** (HF revision / commit / content hash)
- [ ] **Estimates derived per `docs/add-an-eval.md`** (tokens, wall-clock, observer-hours) and shown in the card
- [ ] `python scripts/gen_evals_doc.py` was run and `docs/evals.md` is committed

## Docs touched

<!-- DESIGN.md / SETUP_GUIDE.md / API.md / ANALYSIS_PLAN.md / ENV_REFERENCE.md / none -->
