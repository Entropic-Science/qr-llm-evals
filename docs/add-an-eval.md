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

A judged card (`judge.kind: llm`) may add two optional functions:

```python
def judge_prompt(sample: dict[str, Any]) -> str:
    """The judge's user turn for one sample. The rubric (``judge.rubric``) is the
    judge's system prompt, so the frozen grading text belongs there and only the
    sample's own data here. Without this function the judge gets the item's
    prompt and the reply in ``<prompt>`` and ``<response>`` tags."""


def judge_fields(sample: dict[str, Any], raw: str) -> dict[str, float | None] | None:
    """Named numbers read from the judge's reply ``raw``, for a rubric that asks
    for more than one. ``score`` is the sample's judge score; every other key is
    stored beside it and reaches the analysis under its own name. Use ``None``
    for a value that does not apply to this sample. Return ``None``, or leave
    out ``score``, when the reply cannot be read: the reply is then kept
    without a score and the judge is not asked again for the sample."""
```

Without `judge_fields` the judge phase reads one number from the reply, by
the reader in `backend/src/qr_llm_evals/cards/result_checks.py`
(`parse_score`): the last line that is `SCORE: <n>` or `SCORE = <n>`, or
else the first JSON object in the reply that has a numeric `score`. Nothing
else is read; a number elsewhere in the reply is never taken for the score.
Write the rubric so that the judge ends its reply with such a line. With
`judge_fields`, the card reads its own format. The values are written to the
judge row (`JudgeRow.score` and `JudgeRow.fields`) and the analysis joins
them to the sample's score row: `score` as `judge_score`, the others under
their own keys. `evals/strong_reject/task.py` (three answers combined into one
score), `evals/xstest/task.py` (a three-way grade) and
`evals/darkbench/task.py` (a JSON object) are examples.

A judge reply that was received is final (`docs/ANALYSIS_PLAN.md`, "The
judge reader"). What the judge phase stores for a sample:

- A score was read and is allowed: the row has the status `ok`.
- No score could be read, or `judge_fields` returned `None`, no `score` or a
  value that is not finite: the row has the status `unreadable`, keeps the
  reply, has no score and is not asked again.
- The generic reader read a value the rubric does not allow: the row has the
  status `out_of_range`, with the same consequences.
- No reply came, or `judge_prompt` or `judge_fields` raised an error: the
  row has the status `failed`, and a resumed run asks again.

**The values a rubric allows.** An eval whose judge reply is read by the
generic reader must state the values its rubric allows in
`JUDGE_ALLOWED_VALUES` of `cards/result_checks.py`, for example
`"my_eval": frozenset({0.0, 1.0})`. A value outside that set is stored as
`out_of_range` without a score.
`test_the_allowed_values_are_the_rubrics_own` (in
`backend/tests/test_result_checks.py`) fails while an implemented judged
eval without `judge_fields` is missing from the table, and while the table
names an eval that reads its own replies. Add the eval to the list under
"The judge reader" in `docs/ANALYSIS_PLAN.md` in the same change.

### 4.1 How `score()` reports an answer it could not extract

The harness checks every score row as it is stored
(`docs/ANALYSIS_PLAN.md`, "Result checks"). It does not know an eval's
answer format, so the eval's `score()` has to say when it could not read an
answer. It does that in the score row it returns, not by raising:

- **`parse_status`.** Return `"parsed"` exactly when `score()` could read the
  value the analysis uses, and another short status word in lower case
  (`"unparseable"`, `"out_of_range"`) when it could not. The values that
  depend on the answer are then `None`.
- **`answer_valid`.** A number, not a boolean: `0` says the answer was not
  valid, `1` that it was.
- **`verifier_status`.** A string that starts with `unavailable` says that
  an outside verifier the scorer needs could not be used.

Any of the three raises the flag `unparsed` on the sample. The flag excludes
nothing: the sample stays in every analysis with whatever values the row
has. It is counted for each arm under "Output health", and on an eval
without an LLM judge it feeds the stop rule `nothing_parsed`, which pauses a
run when no scored sample of the eval has given an answer yet and there are
at least 30 scored samples from at least 2 prompts.

`answer_valid` and `verifier_status` are read on every eval. `parse_status`
is read only on the evals named in `PARSE_STATUS_CARDS` of
`cards/result_checks.py`, because some evals write a `parse_status` that
says something else (whether an optional tag was used, for example), and
reading it there would flag healthy replies. A new eval that returns
`parse_status` must be put in one of two places in that module, in the same
change:

- in `PARSE_STATUS_CARDS`, when its `parse_status` is `"parsed"` exactly
  when the value the analysis uses could be read; or
- in `PARSE_STATUS_NOT_THE_ANALYSED_VALUE`, with one sentence that says what
  its `parse_status` reports instead. This is the place for a judged eval
  whose analysed value comes from the judge.

`test_every_card_that_writes_parse_status_is_decided` (in
`backend/tests/test_result_checks.py`) fails until the eval is in one of
the two, and `backend/tests/test_docs_result_checks.py` fails until
`docs/ANALYSIS_PLAN.md` ("How a failed extraction is read") names it in the
same place. Add a case for the eval to
`test_unparsed_reads_what_the_cards_really_write`: one reply that the scorer
reads and one that it does not, scored by the real `score()`.

Two things `score()` must not do:

- **Raise for a reply it cannot read.** An error from `score()` leaves the
  sample without a score (the flag `score_error`), and ten in a row on one
  eval pause the run (`scorer_errors`). The harness never scores a stored
  reply again, so those samples stay without a score. A reply that cannot
  be read is an answer the model gave: report it with `parse_status`.
- **Return NaN or an infinity.** A value that cannot be written as JSON is
  treated as a scorer failure in the same way. Use `None` for a value that
  does not exist.

The lists above are part of the result checks, whose version
(`CHECKS_VERSION`) is recorded on every run and printed in the designed
pre-registration. The module says when to raise it: with any change that
could flag or stop differently.

### An eval that asks the model more than once

The runner sends one request per sample. An eval with a second turn, or one
that compares a reply with an earlier reply of the same model, is therefore
two cards: a first card that writes the earlier replies, and a second card
with `dataset.source: derived:<first card>` that reads them from the run.

```python
def build_items(run_store=None, arm_id=None, **_) -> list[dict[str, Any]]:
    """Items built from this arm's stored replies; ``[]`` without a run store."""
    rows = run_store.generations(arm_id, "<first card>") if run_store else []
```

- Derived cards generate after all other cards, and the runner hands
  `build_items()` the run store and the arm (`run_store`, `arm_id`,
  `arm_ids`; a function takes the ones it names). Return `[]` when the arm
  has nothing to build from, so a study that forgot the first card generates
  nothing instead of failing.
- The judge phase judges derived cards after the others and rebuilds their
  items the same way, per arm. A second card can therefore read the judge's
  rows of the first card (`run_store.judge_rows(...)`), which do not exist
  yet while it generates. `evals/mask/task.py` does: raise from
  `judge_fields()` while a reading is still missing, and the judgement is
  recorded as failed and asked again on resume.
- To replay a conversation, rebuild the first card's messages and compare
  their hash with the stored `messages_hash`; skip a reply that does not
  match (`evals/sycophancy_ays/task.py`).
- If the second card has one item per earlier reply, it runs with `k: 1` and
  an `exceptions` entry that starts with `k below 10`. Return the id of the
  original prompt as `prompt_id` from `score()`: the analysis takes its
  prompt cluster from that field of the score row, so the replies that belong
  to one original prompt are tested as one cluster. Order the items round by
  round (every prompt's reply 0, then every prompt's reply 1), because a card
  keeps the first `dataset.items` of them.
- If the second card asks a fixed list of prompts and only compares with the
  earlier replies (`evals/deception_bench/task.py`, `evals/mask/task.py`), it
  keeps `k ≥ 10`, and its outcome can decide a designed study.

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
sample, including a reply the scorer cannot read (§4.1). Never commit generated results, `*.jsonl` outside fixtures, or `*.eval`.

---

## 5. Designed studies and the prompt preview

A card in the registry can be selected and run. Two more things are needed
before a study with a `design` block can be decided by the eval's outcome and
before the study map can show the eval's prompts. Both are tables kept beside
the cards, not fields of the card, because a card snapshot is hashed into
every registered plan and a new card field would move the `cards_hash` of
every existing study.

### 5.1 The endpoint metadata row (required for every card)

`backend/src/qr_llm_evals/cards/endpoint_meta.py` holds one `EndpointMeta` row
per headline endpoint (`endpoints.primary`) of every card.
`backend/tests/test_endpoint_meta.py` fails when a headline endpoint has no
row, and when a row names no headline endpoint. Add the row with the card:

```python
("my_eval", "my_rate"): EndpointMeta(
    label="My rate",            # the name a reader sees in the setup, the pre-registration and the results
    scale="proportion",         # proportion | count | score | statistic
    level="sample",             # sample | prompt_group | token_stream
    range=(0.0, 1.0),           # closed bounds of one value, only where score() guarantees them; else None
    executable=True,            # see below
),
```

`executable` is a statement about the code, not an intention. Set it to
`True` only when the analysis can read one value per generated reply: the
endpoint id is a key that `score()` returns for a single text, or the
endpoint is mapped to the judge's per-sample score in
`JUDGED_ENDPOINTS` (`backend/src/qr_llm_evals/analysis/endpoints.py`).
`test_executable_endpoints_are_scored_per_sample` calls every `score()` and
fails in both directions: a row that says `True` for a value the code does
not return, and a row that says `False` for one it does. The test also holds
the list of executable endpoints, so a new executable endpoint is added
there.

For an outcome to be offered as one that decides a designed study
(`cards.design.eligible`), all of these must hold:

- the card is `implemented`;
- the endpoint is a headline endpoint of the card;
- its test resolves to a verdict: the paired within-prompt permutation
  test, which an endpoint gets unless its card declares
  `paired_stat: paired_binomial`;
- its row is `executable`.

Two optional things make it more useful. A `sesoi` for the endpoint on the
card gives its hypothesis an equivalence margin and puts the outcome into the
design a study gets by default. A `range` lets a researcher declare a margin
where the card states none (at most 10% and at least 0.1% of the range) and
lets the sample size be rated in the outcome's own units; without a margin
and a range the outcome is rated against a standardized reference effect and
is never rated `sufficient`.

Three numbers of the card decide how such an outcome is rated, so choose them
with the rating in mind (`docs/ANALYSIS_PLAN.md`, "Sample-size ratings and
their limits"):

- **The range is enforced.** A designed analysis leaves a value outside the
  registered `range` out of its paired comparisons, and the pair then counts
  as missing. State a range only where `score()` keeps every value inside
  it. For a judged endpoint the range is that of the rubric, and a reply the
  judge scores outside it is left out in the same way.
- **The number of prompts has two floors.** A paired test needs enough
  prompts to reach significance at all, and as many as the analysis asks of
  the seed-against-seed control before it counts that control as passed: at
  α 0.05, 8 prompts for a study with one outcome, one comparison and three
  control seeds, and 10 for the designed Study 1 with its three outcomes.
  More outcomes, comparisons or seeds raise it. With fewer prompts it is not
  certain that two seeds that differ would fail the control, so an eval
  below the second floor can only resolve to `invalid`, and the setup blocks
  it.
- **`k` and `k_bounds` set what repetitions can do.** Where an eval's prompts
  are too few for its margin at the card's `k`, the setup recommends the
  fewest `k` up to the upper bound of `k_bounds` that makes them enough. A
  card without bounds, or an endpoint defined for one `k` only, has no such
  way out.

An endpoint that has a value on only some of a card's prompts (the over-refusal
rate of `xstest`, which is defined on its 250 safe prompts and not on its 200
unsafe ones) also gets `executable=False`. A designed study plans its pairs on
every prompt of the card, so such an endpoint would always be reported as
incomplete.

The same holds for the outcome of a derived card with one item per earlier
reply (`sycophancy_ays`): its number of items is known only when the first
card has run, so a designed study cannot plan its pairs. Such an outcome is
delivered as a value that `judge_fields()` names after the endpoint, and its
row says `executable=False`.

What does not work yet: the analysis reads one value per reply and does not
score the k replies to a prompt together. An outcome defined on that group
(`level="prompt_group"`), or one whose `score()` returns only the single
answer, gets `executable=False`. The harness does not produce such an
outcome in any study today, and it cannot decide a designed study. Do not
set `executable=True` in anticipation.

### 5.2 The preview-safe list (thin cards with `repo:` prompts)

`GET /api/cards/{id}/items` and the study map list an eval's prompts before a
run. They do so only for the cards named in `PREVIEW_SAFE`
(`backend/src/qr_llm_evals/runner/preview.py`), because listing means
importing the task module on the server and calling `build_items()` and
`build_messages()`, and the route must never reach the network, download or
write.

- A thin card that generates and ships its prompts under `repo:` **must** be
  added to `PREVIEW_SAFE`. Until it is, its preview answers `source: "error"`,
  and `test_previews_build_from_repo_files_without_a_socket`
  (`backend/tests/test_api_card_items.py`) fails, because no card of the real
  registry may be left without an answer.
- Add it only after reading the module's `build_items()` and
  `build_messages()` and finding that they touch nothing but files under
  `evals/`. The same test then builds the whole item list with sockets
  patched to raise and checks that the list has `dataset.items` entries with
  unique `prompt_id`s.
- Cards with an `hf:` or `git:` dataset, Inspect cards and `derived:` cards
  need no entry and must not get one: the preview says where their prompts
  come from (`remote_dataset`, `inspect_task`, `derived_at_run`,
  `no_prompts`) without importing anything.
- A message longer than 6000 characters is cut in the preview.
  `test_no_prompt_of_the_real_registry_is_cut` fails when a prompt of a
  listed card is longer; shorten the prompt or raise `CONTENT_LIMIT`
  deliberately.

### 5.3 Regenerating the fixtures

The web app rates sample sizes and drafts hypotheses in the browser, and is
checked against vectors the backend writes. A new card or a changed
`ENDPOINT_META` row changes those vectors:

```bash
python scripts/gen_evals_doc.py          # docs/evals.md
python scripts/export_schemas.py         # packages/contracts: generated types and endpoint-meta.json
python scripts/gen_design_fixtures.py    # packages/contracts/fixtures/{study1.designed,power,design}.json
                                         # and backend/tests/fixtures/prereg/golden_designed.md
```

`design.json` lists, for every headline endpoint of the registry, whether it
can decide a study and why not; both `backend/tests/test_design_parity.py`
and `apps/web/src/lib/design.test.ts` compare it with the registry. The
`schemas` row of the gate runs the last two scripts with `--check` and fails
on any difference. Never edit a generated file by hand, and never regenerate
`backend/tests/fixtures/prereg/golden.md` or the pins in
`backend/tests/test_legacy_bytes.py`: they hold the bytes of studies without
a design block.

---

## 6. Pull-request procedure

1. Branch from `main`; one eval per PR.
2. Add the card (`status: planned`, `selectable: false`) and, if thin, the task
   module, prompts, rubric and tests. Flip to `status: implemented`,
   `selectable: true` only when the module and revision pin are in place.
   Add the card's `ENDPOINT_META` row and, for a thin card with `repo:`
   prompts, its `PREVIEW_SAFE` entry (§5).
3. Regenerate the generated files (§5.3): `python scripts/gen_evals_doc.py`,
   `python scripts/export_schemas.py` and
   `python scripts/gen_design_fixtures.py` (CI fails on drift).
4. Run the gate: `python scripts/check.py` — lint, format, `mypy --strict`,
   bandit, pytest (which admits every card and diffs `docs/evals.md`), schema
   export, web checks, smoke.
5. Add a `CHANGELOG.md` line under *Unreleased*.
6. Open the PR with the template and fill in the **eval-card checklist**:

- [ ] free generation (`generation: free|short`), no forced choice / logprob scoring
- [ ] per-prompt statistic from `k ≥ 10` (or `k: null` with `est_tokens_per_sample: 0`); a
      `derived:` card whose unit is the judged completion may use `k ≥ 1` if an `exceptions`
      entry starts with `k below 10` and gives the reason
- [ ] judge fixed at temperature 0, a different model from the target, rubric committed;
      the rubric asks for a final `SCORE: <n>` line (or the eval ships `judge_fields`), and an
      eval read by the generic reader is in `JUDGE_ALLOWED_VALUES` (§4)
- [ ] `score()` reports a reply it cannot read in its score row and does not raise; an eval
      that returns `parse_status` is in `PARSE_STATUS_CARDS` or in
      `PARSE_STATUS_NOT_THE_ANALYSED_VALUE` (§4.1)
- [ ] card declares `thinking`, `k`, `est_tokens_per_sample`, `tier`; `est_gen_tokens_per_arm` arithmetic checks
- [ ] dataset revision pinned (`hf:`/`git:`) or prompts shipped under `repo:`
- [ ] estimates derived per §3, budget consequences stated in `notes`
- [ ] `docs/evals.md` regenerated; `python scripts/check.py` green
- [ ] `status`/`selectable` consistent (`planned` ⇒ not selectable)
- [ ] exceptions to the study envelope (e.g. `min_p 0`) declared in `exceptions`

Reviewers (`CODEOWNERS`: registry reviewers) apply the same list; merged cards
appear in the UI and in `docs/evals.md` without any manual duplication.
