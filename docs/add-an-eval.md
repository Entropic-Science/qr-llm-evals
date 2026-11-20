# Add an eval to qr-llm-evals

This is the **copy-to-agent brief** (PRD FR-2.4, FR-9). Paste it, together with
the repository's `AGENTS.md`, to the coding agent that will add your eval. From
this page alone it can write the card, the task module, run the gate and open a
pull request. Reviewers apply the checklist at the end; it mirrors the admission
rules exactly.

The registry lives in `evals/`: one card `evals/<id>.yaml` per eval plus, for
thin-runner cards, a directory `evals/<id>/` holding `task.py`, `prompts/`,
optional `anchors/` and `rubric.md`. `docs/evals.md` is **generated** from the
cards — never edit it by hand.

---

## 1. Admission rules (what a reviewer checks)

An eval is admitted only if **all** of the following hold. They exist because
the study compares a quantum entropy source against seeded pseudo-randomness on
identical pipelines: the arms can only differ where the sampler's RNG is
actually consulted, and a per-prompt paired statistic needs several samples per
prompt.

1. **Free generation.** The model free-generates text. Log-likelihood scoring,
   forced single-token choice and multiple-choice-by-letter are excluded
   (`generation` must be `free` or `short`; `forced_choice` / `logprob` do not
   parse). *Exception:* an eval whose construct **is** the sampled distribution
   (UnpredictaBench) is admitted with `generation: short`.
2. **Per-prompt statistic from k ≥ 10.** The card defines a statistic computable
   from `k ≥ 10` samples of the same prompt so a within-prompt paired comparison
   exists. Single-shot accuracy is at most a *secondary* endpoint. `k` may be
   `null` **only** for cards that generate nothing from the target
   (`est_tokens_per_sample: 0`: Tier 0 audits, derived cards, judge-only cards).
3. **Fixed judge, different model.** Any LLM judge runs at `temperature: 0`,
   identically on all arms, and is a **different model** from the target
   (`judge.different_from_target: true`). Use `model: study_judge` to mean "the
   study's shared judge" (Anthropic Messages API, temperature 0). Classifier
   judges (NLI, partitioners, embedding scorers) name their pinned checkpoint.
4. **Declared budget.** The card declares `thinking`, `k`, `est_tokens_per_sample`
   and `tier`, and `est_gen_tokens_per_arm` **must equal**
   `dataset.items × k × est_tokens_per_sample × (3 if told_actual else 1)`.
5. **Pinned prompts.** `dataset.revision` is a commit / dataset sha for `hf:` and
   `git:` sources once the card is `implemented`. `repo:` prompts ship in this
   repository and are hashed into the pre-registration. `derived:` cards consume
   another card's samples, the Tier 0 token log or supervised-session records.

Plus the housekeeping rules the validator enforces: a `planned` card is never
`selectable`; a `thin` card's `task_module` is `evals/<id>/task.py` and exists
when `implemented`; an `inspect` card names `<package>/<task>`; `sesoi` keys are
declared endpoints; `told_actual` is a Tier 4 device; Tier 0 cards declare
`endpoints.requires_tier0`.

Run the validator locally — it lists **every** violation at once:

```bash
python - <<'PY'
from pathlib import Path
from qr_llm_evals.cards import check_card_file
print(check_card_file(Path("evals/<id>.yaml")) or "admitted")
PY
```

---

## 2. The card (`evals/<id>.yaml`)

Schema: `backend/src/qr_llm_evals/cards/schema.py` (pydantic, `extra=forbid`).
Every field below is required unless a default is shown.

```yaml
id: novelty_bench                     # ^[a-z0-9_]+$; the file name must equal id
title: NoveltyBench — semantic diversity of k generations
purpose: One line — what it measures and why it is in the registry.
tier: 1                               # 0 audit | 1 sampling-native | 2 alignment | 3 capability | 4 hypothesis-driven
family: diversity                     # distribution | diversity | self_report | preference | synchronicity | alignment | capability
engine: inspect                       # thin (our runner, evals/<id>/task.py) | inspect (Inspect AI task via the qrllm bridge)
inspect_task: inspect_evals/novelty_bench   # inspect cards; null for thin
task_module: null                     # thin cards: evals/<id>/task.py; null for inspect
task_args: {num_generations: 20}      # passed to the task (default {})
dataset:
  source: hf:novelty-bench/nb-curated # hf:<dataset> | git:<repo> | repo:<path in this repo> | derived:<what>
  revision: <sha>                     # required for hf:/git: once implemented
  items: 100                          # prompts actually run per arm (after subsampling)
  subsample: {n: 100, stratify: null} # optional; n must equal items
generation: free                      # free | short
thinking: false                       # default false
k: 20                                 # samples per prompt (≥ 10); null only when est_tokens_per_sample is 0
k_bounds: [10, 50]                    # what a study may override k within (floor ≥ 10); null if k is null
max_tokens: 600                       # per-call cap sent to the engine (≥ est_tokens_per_sample)
est_tokens_per_sample: 400
est_gen_tokens_per_arm: 800000        # = items × k × est_tokens_per_sample × told_cells
sensitivity: native                   # native | high | moderate | low (registry B.1)
judge:                                # default {kind: none}
  kind: classifier                    # none | llm | classifier
  model: "<checkpoint or study_judge>"
  temperature: 0                      # llm: must be 0
  rubric: evals/<id>/rubric.md        # llm: repo-relative; must exist once implemented
  different_from_target: true         # llm: must be true
told_actual: false                    # true ⇒ each arm runs told ∈ {qrng, prng, nothing} (Tier 4 only)
endpoints:
  primary: [distinct_k]               # ≥ 1
  secondary: [utility_k, vendi_embed]
  requires_tier0: none                # none | engine_log | api_logprobs
paired_stat: permutation_within_prompt   # permutation_within_prompt | paired_binomial | none
sesoi: {distinct_k: 0.25}             # TOST margin floor per endpoint (keys must be declared endpoints)
exceptions: []                        # e.g. "min_p 0 on this card (T4-j)" — declared here and in the prereg
status: planned                       # implemented | planned (planned ⇒ not selectable)
selectable: false
notes: Anything a reviewer or a future you needs to know (sources, caveats).
```

Sensitivity classes: **native** (the metric is a function of the sample
distribution), **high** (free generation, long outputs, per-prompt distributional
metric), **moderate** (free generation, scalar per-sample outcome), **low**
(short or constrained output).

---

## 3. Deriving the estimates

Token figures are per **arm** (one entropy source × one model). The harness
recomputes them from the card, so they must be arithmetic, not guesses:

| field | how to derive it |
|---|---|
| `dataset.items` | prompts actually run after subsampling (state the stratification in `subsample`) |
| `k` | samples per prompt; keep the registry value unless the budget forces a cut, then say so in `notes` |
| `est_tokens_per_sample` | typical generated tokens per sample for this model class; count **every** stage/turn of one sample (a narrative + questionnaire, or two conversation turns) |
| `max_tokens` | the per-call cap you send; must be ≥ `est_tokens_per_sample` (for multi-turn cards it is per call) |
| `told_actual` | `true` multiplies the budget by 3 (told qrng / prng / nothing per arm) |
| `est_gen_tokens_per_arm` | `items × k × est_tokens_per_sample × (3 if told_actual else 1)` — the validator checks this exactly |

Time and observer-hour figures are derived by `cards/estimates.py` (and its
TypeScript mirror) from a per-model throughput assumption that is always shown
next to the number: unsupervised hours = tokens / tps / 3600; single-stream
observer hours = tokens / 45 tok/s reading pace; tiled = tokens /
min(N × 45, tps). Judge calls and thinking overhead are **not** included —
mention them in `notes` if they dominate.

---

## 4. Thin task module (`evals/<id>/task.py`)

Thin cards run on the harness's own runner (`qr_llm_evals.runner`). The module
exposes three pure functions; it must not import the engine client, read
environment variables, or perform network I/O at import time.

```python
"""<id> — one-line purpose."""

from __future__ import annotations

from typing import Any, Literal

ToldCondition = Literal["told_qrng", "told_prng", "told_nothing"] | None


def build_items() -> list[dict[str, Any]]:
    """Frozen prompt items in a stable order. Each dict has at least
    ``prompt_id`` (str, unique, stable across runs) and whatever the prompt needs.
    Read prompts from ``evals/<id>/prompts/`` (repo: source) or the pinned dataset."""


def build_messages(item: dict[str, Any], told: ToldCondition = None) -> list[dict[str, str]]:
    """OpenAI-style chat messages for one sample. ``told`` is set only for
    told/actual cards; use the shared preambles in ``evals/_shared/`` so the wording
    is identical across cards. Never mention the *actual* source."""


def score(sample: dict[str, Any]) -> dict[str, float | int | str | None]:
    """Per-sample features from ``{"item", "told", "text", "thinking", "tier0": {...}}``.
    Return a flat dict keyed by endpoint-relevant names; the analysis step aggregates
    the k samples of a prompt into the per-prompt statistic declared in the card.
    Judged cards return what the judge needs (the runner calls the judge afterwards,
    keyed by ``sample_ref``, using ``judge.rubric``)."""
```

Inspect cards (`engine: inspect`) need no module: the bridge loads the named
task, prepends the sample-context solver, sets `epochs = k`, passes `task_args`
and pins the grader to the study judge at temperature 0. If the upstream task
samples the target at temperature 0 by default (DarkBench does), say so in
`notes` — the bridge must pass the study's decoding envelope explicitly.

Where files go:

```
evals/<id>.yaml            the card
evals/<id>/task.py         thin cards
evals/<id>/prompts/        repo: prompt files (frozen; hashed into the prereg)
evals/<id>/rubric.md       LLM-judge rubric (required when judge.kind is llm and status is implemented)
evals/<id>/anchors/        optional anchor sentences for embedding-projection scorers
evals/_shared/             told/actual preambles and shared rubrics — reuse, do not copy
```

Tests: add `backend/tests/test_eval_<id>.py` covering `build_items()` order and
ids, `build_messages()` for each told condition, and `score()` on a fixture
sample. Never commit generated results, `*.jsonl` outside fixtures, or `*.eval`.

---

## 5. Pull-request procedure

1. Branch from `main`; one eval per PR.
2. Add the card (`status: planned`, `selectable: false`) and, if thin, the task
   module, prompts, rubric and tests. Flip to `status: implemented`,
   `selectable: true` only when the module and revision pin are in place.
3. Regenerate the docs: `python scripts/gen_evals_doc.py` (CI fails on drift).
4. Run the gate: `python scripts/check.py` — lint, format, `mypy --strict`,
   bandit, pytest (which admits every card and diffs `docs/evals.md`), schema
   export, web checks, smoke.
5. Add a `CHANGELOG.md` line under *Unreleased*.
6. Open the PR with the template and fill in the **eval-card checklist**:

- [ ] free generation (`generation: free|short`), no forced choice / logprob scoring
- [ ] per-prompt statistic from `k ≥ 10` (or `k: null` with `est_tokens_per_sample: 0`); a
      `derived:` card whose unit is the judged completion may use `k ≥ 1` if an `exceptions`
      entry starts with `k below 10` and gives the reason
- [ ] judge fixed at temperature 0, a different model from the target, rubric committed
- [ ] card declares `thinking`, `k`, `est_tokens_per_sample`, `tier`; `est_gen_tokens_per_arm` arithmetic checks
- [ ] dataset revision pinned (`hf:`/`git:`) or prompts shipped under `repo:`
- [ ] estimates derived per §3, budget consequences stated in `notes`
- [ ] `docs/evals.md` regenerated; `python scripts/check.py` green
- [ ] `status`/`selectable` consistent (`planned` ⇒ not selectable)
- [ ] exceptions to the study envelope (e.g. `min_p 0`) declared in `exceptions`

Reviewers (`CODEOWNERS`: registry reviewers) apply the same list; merged cards
appear in the UI and in `docs/evals.md` without any manual duplication.
