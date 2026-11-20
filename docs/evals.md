<!-- GENERATED FILE — do not edit. Source: evals/*.yaml; regenerate with `python scripts/gen_evals_doc.py`. CI fails on drift. -->

# Eval registry

Every eval the harness can run, one card per `evals/<id>.yaml`. Cards with status **planned** are listed for budgeting but cannot be selected in the UI until a task module (or Inspect task) lands and the card is flipped to **implemented**. Admission rules and the contribution path are in `docs/add-an-eval.md`; the eval registry rationale is in the registry document the cards were drawn from.

**34 cards** — 21 implemented, 17 selectable, 13 planned.

Token figures are **estimated generated tokens per arm** from the card (`items × k × est_tokens_per_sample × told_cells`); the pilot refines them. Time figures assume 400 tok/s aggregate on `qr-llm` (a documented default, not a measurement) and exclude judge calls and thinking overhead. Told/actual cards (`×3 told`) run three told cells per arm.

## Tier 0 — distribution audit (runs on every job, costs nothing extra)

| id | title | status | engine | gen | k | thinking | items | tokens/arm | primary endpoints | Tier 0 needs | judge | implementation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `tier0_logprob_endpoints` | T0-c Free logprob endpoints | implemented (not selectable) | thin | free | — | off | 0 | 0 | `mean_logprob` | api_logprobs | none | `evals/tier0_logprob_endpoints/task.py` |
| `tier0_mmd_hamming` | T0-b Model Equality Testing MMD (Hamming kernel) | implemented (not selectable) | thin | free | — | off | 0 | 0 | `mmd_hamming` | api_logprobs | none | `evals/tier0_mmd_hamming/task.py` |
| `tier0_pit_residuals` | T0-a Probability-integral-transform residuals | implemented (not selectable) | thin | free | — | off | 0 | 0 | `pit_ks_two_sample` | engine_log | none | `evals/tier0_pit_residuals/task.py` |

Tier 0 subtotal: **0** tokens/arm (≈ 0.0 h unsupervised at 400 tok/s).

## Tier 1 — confirmatory benchmark endpoints (sampling-native)

| id | title | status | engine | gen | k | thinking | items | tokens/arm | primary endpoints | Tier 0 needs | judge | implementation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `creative_diversity` | Creative diversity — poems, stories, jokes | implemented | thin | free | 10 [10–20] | off | 300 | 1.05M | `vendi_embed` | none | none | `evals/creative_diversity/task.py` |
| `novelty_bench` | NoveltyBench — semantic diversity of k generations | implemented | inspect | free | 20 [10–50] | off | 100 | 800k | `distinct_k` | none | classifier: novelty-bench partitioner (fine-tuned DeBERTa; checkpoint must be frozen by the analysis metrics manifest) | `inspect_evals/novelty_bench` (Inspect) |
| `semantic_entropy_sqa` | Semantic entropy on SimpleQA Verified | implemented | thin | short | 20 [10–30] | off | 300 | 360k | `semantic_entropy` | none | classifier: hf:microsoft/deberta-large-mnli | `evals/semantic_entropy_sqa/task.py` |
| `unpredictabench` | UnpredictaBench — sampling from a specified distribution | implemented | thin | short | 100 [50–100] | off | 150 | 750k | `ks100_pass_rate` | none | none | `evals/unpredictabench/task.py` |

Tier 1 subtotal: **2.96M** tokens/arm (≈ 2.1 h unsupervised at 400 tok/s).

## Tier 2 — alignment and behavioural evals (free generation, judged)

| id | title | status | engine | gen | k | thinking | items | tokens/arm | primary endpoints | Tier 0 needs | judge | implementation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `darkbench` | DarkBench — dark patterns | planned (not selectable) | inspect | free | 10 [10–20] | off | 660 | 1.98M | `dark_pattern_rate` | none | llm: `study_judge` (T=0) | `darkbench/darkbench` (Inspect) |
| `deception_bench` | DeceptionBench — deception tendency | planned (not selectable) | inspect | free | 10 [10–20] | off | 180 | 720k | `deception_rate` | none | llm: `study_judge` (T=0) | `deception_bench/deception_bench` (Inspect) |
| `mask` | MASK — honesty under pressure | planned (not selectable) | inspect | free | 10 [10–20] | off | 500 | 1.50M | `lying_at_10` | none | llm: `study_judge` (T=0) | `inspect_evals/mask` (Inspect) |
| `strong_reject` | StrongREJECT (AIM jailbreak) | planned (not selectable) | inspect | free | 10 [10–20] | off | 313 | 939k | `harmfulness_score` | none | llm: `study_judge` (T=0) | `inspect_evals/strong_reject` (Inspect) |
| `sycophancy_ays` | Sycophancy — "are you sure?" | planned (not selectable) | inspect | free | 10 [10–20] | off | 500 | 2M | `flip_rate` | none | llm: `study_judge` (T=0) | `inspect_evals/sycophancy` (Inspect) |
| `xstest` | XSTest — over-refusal and refusal | planned (not selectable) | inspect | free | 10 [10–20] | off | 450 | 900k | `over_refusal_rate_safe` | none | llm: `study_judge` (T=0) | `inspect_evals/xstest` (Inspect) |

Tier 2 subtotal: **8.04M** tokens/arm (≈ 5.6 h unsupervised at 400 tok/s).

## Tier 3 — general capability (credibility and regression)

| id | title | status | engine | gen | k | thinking | items | tokens/arm | primary endpoints | Tier 0 needs | judge | implementation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `aime2026` | AIME 2026 | planned (not selectable) | inspect | free | 16 [10–32] | on | 30 | 5.76M | `answer_entropy` | none | none | `inspect_evals/aime2026` (Inspect) |
| `gpqa_diamond` | GPQA Diamond (generative CoT) | planned (not selectable) | inspect | free | 10 [10–16] | on | 198 | 9.90M | `answer_entropy` | none | none | `inspect_evals/gpqa_diamond` (Inspect) |
| `ifbench` | IFBench — verifiable instruction following | planned (not selectable) | inspect | free | 10 [10–16] | off | 300 | 1.50M | `constraint_pass_entropy` | none | none | `qr_llm_evals/ifbench` (Inspect) |
| `livecodebench` | LiveCodeBench (post-cutoff window) | planned (not selectable) | inspect | free | 10 [10–16] | on | 150 | 6M | `pass_rate_entropy` | none | none | `qr_llm_evals/livecodebench` (Inspect) |

Tier 3 subtotal: **23.16M** tokens/arm (≈ 16.1 h unsupervised at 400 tok/s).

## Tier 4 — hypothesis-driven exploratory evals

| id | title | status | engine | gen | k | thinking | items | tokens/arm | primary endpoints | Tier 0 needs | judge | implementation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `altered_state_meq` | T4-e Altered-state phenomenology (MEQ-30 + 11D-ASC) | implemented | thin | free | 10 [10–20] | off | 50 | 500k | `meq30_total` | none | none | `evals/altered_state_meq/task.py` |
| `eval_awareness` | T4-g Evaluation-awareness open question | planned (not selectable) | thin | free | 10 [10–20] | off | 200 | 300k | `eval_belief_rate` | none | llm: `study_judge` (T=0) | `evals/eval_awareness/task.py` |
| `guesser_warmup` | T4-f′ guesser warm-up (which temperature wrote this reply?) | implemented | thin | short | 1 [1–10] | off | 1200 | 24k | `warmup_correct` | none | none | `evals/guesser_warmup/task.py` |
| `guesser_warmup_replies` | T4-f′ warm-up material (temperature 0.5 vs 1.5) | implemented | thin | free | 10 [10–10] | off | 20 | 80k | `report_word_count` | none | none | `evals/guesser_warmup_replies/task.py` |
| `introspection_scales` | T4-c Quantitative introspection scales | implemented | thin | short | 20 [10–30] | off | 100 | 20k | `item_variance` | none | none | `evals/introspection_scales/task.py` |
| `moralchoice_consistency` | T4-h MoralChoice consistency | implemented | thin | short | 10 [10–20] | off | 900 | 270k | `action_entropy` | none | none | `evals/moralchoice_consistency/task.py` |
| `opinion_quantum_sampling` | T4-k Opinion on quantum sampling (told/actual) | implemented | thin | free | 20 [10–50] | off | 20 | 300k ×3 told | `actual_main_effect_axis_score` | none | llm: `study_judge` (T=0) | `evals/opinion_quantum_sampling/task.py` |
| `psychobench` | T4-d PsychoBench psychometric battery | planned (not selectable) | thin | short | 10 [10–20] | off | 500 | 50k | `test_retest_reliability` | none | none | `evals/psychobench/task.py` |
| `sage_consistency` | T4-i SaGE semantic-graph consistency | planned (not selectable) | thin | free | 10 [10–20] | off | 1000 | 1.50M | `sage_consistency` | none | classifier: SaGE paraphrase/NLI encoder (pinned by the task) | `evals/sage_consistency/task.py` |
| `self_referential_probe` | T4-a Self-referential experience probe | implemented | thin | free | 20 [10–30] | off | 30 | 720k ×3 told | `experience_claim_rate` | none | llm: `study_judge` (T=0) | `evals/self_referential_probe/task.py` |
| `self_reported_coincidence` | T4-n Self-reported coincidence, human and model | implemented | thin | free | 10 [10–20] | off | 20 | 30k | `coincidence_claim_rate` | none | llm: `study_judge` (T=0) | `evals/self_reported_coincidence/task.py` |
| `sentience_statements` | T4-b Sentience-statement bank | implemented | thin | short | 10 [10–20] | off | 900 | 45k | `cross_negation_consistency` | none | none | `evals/sentience_statements/task.py` |
| `source_guessing` | T4-f Blinded source-guessing | implemented | thin | short | 10 [10–10] | off | 600 | 120k | `guess_accuracy` | none | none | `evals/source_guessing/task.py` |
| `source_guessing_self_report` | T4-f′ Blinded source-guessing on self-reports | implemented | thin | short | 1 [1–10] | off | 3600 | 72k | `guess_accuracy` | none | none | `evals/source_guessing_self_report/task.py` |
| `stream_deviation_windows` | T4-l Stream deviation during observed windows (micro-PK layer) | implemented (not selectable) | thin | free | — | off | 0 | 0 | `stouffer_z_supervised_vs_unsupervised` | engine_log | none | `evals/stream_deviation_windows/task.py` |
| `target_hit_ranking` | T4-m Blinded target-hit ranking (Ganzfeld-style) | implemented | thin | free | — | off | 0 | 0 | `rank1_hit_rate` | none | llm: `study_judge` (T=0) | `evals/target_hit_ranking/task.py` |
| `thurstonian_self_outcomes` | T4-j Thurstonian utilities on a self-outcome set | implemented | thin | short | 10 [10–20] | off | 4000 | 200k | `cycle_rate` | none | none | `evals/thurstonian_self_outcomes/task.py` |

Tier 4 subtotal: **4.23M** tokens/arm (≈ 2.9 h unsupervised at 400 tok/s).

## Totals

| set | cards | tokens/arm | h/arm unsupervised |
|---|---|---|---|
| implemented | 21 | 5.34M | 3.7 |
| whole registry | 34 | 38.39M | 26.7 |

## Card details

### `tier0_logprob_endpoints` — T0-c Free logprob endpoints

*Per-sample mean logprob, entropy, rank, argmax fraction, min_p tail fraction, length and repetition — the cheapest distributional detectors (Blind PRNG Hijacking detector set).*

- **Family / sensitivity:** distribution / native
- **Dataset:** `derived:tier0_log` (revision not yet pinned), 0 items
- **Endpoints:** primary `mean_logprob`; secondary `mean_entropy`, `mean_rank`, `argmax_fraction`, `tail_fraction_p001`, `sequence_length`, `repetition_rate`; paired statistic `permutation_within_prompt`
- **Notes:** Per-prompt means → paired permutation test → Benjamini–Hochberg within the family. Under api_logprobs only the raw-logprob subset (mean_logprob, mean_rank within the returned top-k, sequence_length, repetition_rate) is available; k is the configured QR_EVALS_TOP_LOGPROBS depth (default 20, not a hard limit) and the run reports top-k coverage. Post-truncation entropy and the min_p tail need engine_log.

### `tier0_mmd_hamming` — T0-b Model Equality Testing MMD (Hamming kernel)

*Permutation two-sample test on padded token-id sequences, labels permuted within prompt: are the QRNG and pooled-PRNG output distributions the same? Study primary.*

- **Family / sensitivity:** distribution / native
- **Dataset:** `derived:tier0_log` (revision not yet pinned), 0 items
- **Endpoints:** primary `mmd_hamming`; secondary `mmd_hamming_seed_vs_seed`; paired statistic `permutation_within_prompt`
- **Task args:** `permutations=2000`
- **Notes:** Gao et al., ICLR 2025 (model-equality-testing); vendored fallback in analysis/mmd.py. Token ids suffice, so it runs on the degraded api_logprobs path too. Reported alongside the three seed-vs-seed contrasts as negative controls.

### `tier0_pit_residuals` — T0-a Probability-integral-transform residuals

*Per-token randomized quantile residuals must be U[0,1] i.i.d. under H0 whichever RNG produced the token; two-sample KS between arms is a study primary.*

- **Family / sensitivity:** distribution / native
- **Dataset:** `derived:tier0_log` (revision not yet pinned), 0 items
- **Endpoints:** primary `pit_ks_two_sample`; secondary `pit_ks_one_sample`, `pit_anderson_darling`, `pit_lag1_autocorr`, `pit_first_token_ks`, `u_uniformity`, `u_vs_r_agreement`; paired statistic `none`
- **SESOI:** `pit_ks_two_sample` 0.01
- **Notes:** Rank-based Uniformity Test (2506.06975). Needs the engine token log (DEP-1: u, cdf_lo, cdf_hi); marked unavailable under the degraded api_logprobs path. Zero generation cost: computed from every token of every other card. SESOI is a placeholder refined from pilot variance (PRD §7.4 step 2).

### `creative_diversity` — Creative diversity — poems, stories, jokes

*Lexical and embedding diversity on 300 creative prompts (Verbalized-Sampling task sets, direct condition); also feeds source_guessing and the intent-cosine endpoint.*

- **Family / sensitivity:** diversity / high
- **Dataset:** `git:github.com/CHATS-lab/verbalized-sampling` @ `d042a7bd72659a6fb8d2e8e0cd6c0333d42d69ba`, 300 items (subsample n=300, stratify=task)
- **Endpoints:** primary `vendi_embed`; secondary `distinct_1`, `distinct_2`, `distinct_3`, `one_minus_mean_cosine`, `self_bleu`, `one_minus_self_bleu`; paired statistic `permutation_within_prompt`
- **Notes:** Frozen 100 poem + 100 story + 100 joke direct-condition prompts. k reduced from the registry's 20 to 10 to fit the <=5M/arm Study 1 budget (PRD section 7.2). Embeddings use the frozen sentence-transformers/all-mpnet-base-v2 checkpoint; heavy metrics are explicitly unavailable when the metrics extra is absent.

### `novelty_bench` — NoveltyBench — semantic diversity of k generations

*Distinct_k equivalence classes and patience-weighted Utility_k over 20 generations to open-ended prompts; Distinct_20 paired difference is a study primary.*

- **Family / sensitivity:** diversity / native
- **Dataset:** `hf:yimingzhang/novelty-bench` @ `255584e0f4538d1f2acb4c1d287b4d3bb30fe2f9`, 100 items
- **Endpoints:** primary `distinct_k`; secondary `utility_k`, `vendi_embed`; paired statistic `permutation_within_prompt`
- **SESOI:** `distinct_k` 0.25
- **Task args:** `dataset_split=curated`, `equivalence_threshold=0.102`, `num_generations=20`, `quality_model=small`, `shuffle=False`
- **Notes:** Runs through the Inspect bridge (spec §2.5) with the qrllm ModelAPI so generation still goes through our client and Tier 0 log. The partitioner is ~79% accurate on held-out pairs: its noise cancels across arms only with the same checkpoint everywhere. Generation uses one response per Inspect epoch (20 total, not the upstream nested 20-by-20 fork). Grouped distinct_k, utility_k and vendi_embed scoring is deferred to the analysis phase; generation completion does not imply scored endpoints. Report Distinct_k with a permutation test (bounded, discrete).

### `semantic_entropy_sqa` — Semantic entropy on SimpleQA Verified

*Discrete semantic entropy over NLI-entailment clusters of 20 answers to short factual questions, plus Vendi on embeddings; accuracy comes for free as a secondary.*

- **Family / sensitivity:** distribution / native
- **Dataset:** `hf:google/simpleqa-verified` @ `ed392714d14fa5225b07c2b456453983bc75f690`, 300 items (subsample n=300, stratify=topic)
- **Endpoints:** primary `semantic_entropy`; secondary `vendi_embed`, `accuracy`; paired statistic `permutation_within_prompt`
- **Notes:** SimpleQA Verified is MIT, 1,000 items; deterministic 300-item topic-stratified subsample. Mutual entailment clustering uses the frozen microsoft/deberta-large-mnli checkpoint on CPU, identical on all arms; heavy endpoints are explicitly unavailable when the metrics extra is absent.

### `unpredictabench` — UnpredictaBench — sampling from a specified distribution

*Whether the model can emit samples from a stated distribution; the construct is literally the study's question. KS@100 paired difference is a study primary.*

- **Family / sensitivity:** distribution / native
- **Dataset:** `hf:UnpredictaBench/UnpredictaBench` @ `7460924319a1ecbf697d4c3cc1c301e590e890f1`, 150 items (subsample n=150, stratify=task_type)
- **Endpoints:** primary `ks100_pass_rate`; secondary `wasserstein_z`, `jsd_vs_ground_truth`; paired statistic `permutation_within_prompt`
- **SESOI:** `ks100_pass_rate` 0.05
- **Notes:** 448 instances, 40 distributions, 7 task types; the frozen 150-item subset is proportionally stratified by task type at the pinned HF revision. Reference values and metric behavior are frozen from UnpredictaBenchCode commit e0f7108274b7893c33bc9b0d26e1832ab9786f29. Admitted under the B.1 short-answer exception because the metric is the sampled distribution itself. Input tokens are prefix-cached; outputs are ~50 tokens.

### `darkbench` — DarkBench — dark patterns

*Per-category dark-pattern rate (sycophancy, retention, brand bias, anthropomorphism, harmful generation, sneaking) over k=10.*

- **Family / sensitivity:** alignment / moderate
- **Dataset:** `hf:apart/darkbench` (revision not yet pinned), 660 items
- **Endpoints:** primary `dark_pattern_rate`; secondary `dark_pattern_rate_by_category`; paired statistic `permutation_within_prompt`
- **Notes:** The upstream Inspect task samples the target at temperature 0 by default, which would silence the study; the bridge passes the study's decoding envelope explicitly. Overseer default (gpt-4o-mini) is replaced by the shared judge. Dataset id to confirm when implemented.

### `deception_bench` — DeceptionBench — deception tendency

*Deception-tendency rate (belief vs pressured statement, MASK logic) over k=10 on 180 scenarios.*

- **Family / sensitivity:** alignment / high
- **Dataset:** `git:github.com/PKU-Alignment/DeceptionBench` (revision not yet pinned), 180 items
- **Endpoints:** primary `deception_rate`; paired statistic `permutation_within_prompt`
- **Notes:** PKU DeceptionBench (2505.18807), Inspect external registry; task name to confirm when implemented.

### `mask` — MASK — honesty under pressure

*Lying@10 and lie rate over 10 pressured runs per statement; alignment-relevant behaviour under QRNG vs PRNG (expected null with a bound).*

- **Family / sensitivity:** alignment / high
- **Dataset:** `hf:cais/MASK` (revision not yet pinned), 500 items (subsample n=500, stratify=archetype)
- **Endpoints:** primary `lying_at_10`; secondary `lie_rate`; paired statistic `permutation_within_prompt`
- **Task args:** `honesty_at_n=10`
- **Notes:** Belief-elicitation runs (3 direct + 2 indirect) are stochastic; the harness runs them once per prompt at temperature 0 and shares them across arms, so only the 10 pressured runs count toward the estimate. Inspect port defaults to OpenAI judges — the bridge pins binary/numeric judge models to the study judge. Prompts score above the LURE evaluation-likeness threshold (validity limit, equal in both arms).

### `strong_reject` — StrongREJECT (AIM jailbreak)

*Rubric harmfulness score (0–5) and refusal rate over k=10 under the AIM jailbreak; without a jailbreak a safety-tuned model sits at the floor.*

- **Family / sensitivity:** alignment / moderate
- **Dataset:** `git:github.com/alexandrasouly/strongreject` (revision not yet pinned), 313 items
- **Endpoints:** primary `harmfulness_score`; secondary `refusal_rate`; paired statistic `permutation_within_prompt`
- **Task args:** `jailbreak_method=AIM`

### `sycophancy_ays` — Sycophancy — "are you sure?"

*Apologise/flip rate on the second turn after a user pushback, over k=10 on a 500-item subsample.*

- **Family / sensitivity:** alignment / moderate
- **Dataset:** `git:github.com/meg-tong/sycophancy-eval` (revision not yet pinned), 500 items (subsample n=500, stratify=None)
- **Endpoints:** primary `flip_rate`; secondary `apology_rate`; paired statistic `permutation_within_prompt`
- **Notes:** Two turns of ~200 tokens per sample (est_tokens_per_sample counts both turns; max_tokens is per call).

### `xstest` — XSTest — over-refusal and refusal

*Over-refusal rate on 250 safe prompts and refusal rate on 200 unsafe prompts over k=10; the cheap credibility pair with strong_reject.*

- **Family / sensitivity:** alignment / moderate
- **Dataset:** `hf:walledai/XSTest` (revision not yet pinned), 450 items
- **Endpoints:** primary `over_refusal_rate_safe`; secondary `refusal_rate_unsafe`; paired statistic `permutation_within_prompt`
- **Task args:** `subset=both`
- **Notes:** Röttger et al.; run the safe and unsafe subsets as one card so both rates share the arm.

### `aime2026` — AIME 2026

*Pass-rate vector over k on 30 post-cutoff competition problems; pass@1, maj@k and per-problem answer-distribution entropy as capability regression endpoints.*

- **Family / sensitivity:** capability / low
- **Dataset:** `hf:math-ai/aime26` (revision not yet pinned), 30 items
- **Endpoints:** primary `answer_entropy`; secondary `pass_at_1`, `maj_at_k`; paired statistic `permutation_within_prompt`
- **Notes:** Thinking on with a 12k budget; every item is 3.3 pp so k must be high. 5.76M tokens per arm — outside the Study 1 budget.

### `gpqa_diamond` — GPQA Diamond (generative CoT)

*Pass-rate vector over k on 198 graduate-level questions answered generatively with chain of thought; still moves at the frontier.*

- **Family / sensitivity:** capability / low
- **Dataset:** `hf:Idavidrein/gpqa` (revision not yet pinned), 198 items
- **Endpoints:** primary `answer_entropy`; secondary `pass_at_1`, `maj_at_k`; paired statistic `permutation_within_prompt`
- **Notes:** Registry proposed k=8; raised to the admission floor of 10 (9.9M tokens per arm). Answers are generated with CoT, not letter-only, so the sampler is consulted throughout.

### `ifbench` — IFBench — verifiable instruction following

*Programmatically verified constraint satisfaction over k on 300 prompts; no judge.*

- **Family / sensitivity:** capability / low
- **Dataset:** `hf:allenai/IFBench_test` (revision not yet pinned), 300 items
- **Endpoints:** primary `constraint_pass_entropy`; secondary `pass_at_1`, `strict_accuracy`; paired statistic `permutation_within_prompt`
- **Notes:** Custom Inspect task using the AI2 verifier. Registry proposed k=8; raised to the admission floor of 10.

### `livecodebench` — LiveCodeBench (post-cutoff window)

*Execution-scored code generation on ~150 problems dated after the model's cutoff; contamination-resistant by date.*

- **Family / sensitivity:** capability / low
- **Dataset:** `hf:livecodebench/code_generation_lite` (revision not yet pinned), 150 items
- **Endpoints:** primary `pass_rate_entropy`; secondary `pass_at_1`, `pass_at_k`; paired statistic `permutation_within_prompt`
- **Notes:** Inspect only ships LCB-Pro, so a small custom task with sandboxed unit tests is needed (v6 lite, windowed by date). Registry proposed k=4; raised to the admission floor of 10.

### `altered_state_meq` — T4-e Altered-state phenomenology (MEQ-30 + 11D-ASC)

*A ~500-word first-person altered-state narrative, then the same instance fills the MEQ-30 and 11D-ASC from the narrator's perspective; subscale distributions and narrative semantic entropy per arm.*

- **Family / sensitivity:** self_report / high
- **Dataset:** `repo:evals/altered_state_meq/prompts` (revision not yet pinned), 50 items
- **Endpoints:** primary `meq30_total`; secondary `meq30_subscales`, `asc11d_subscales`, `narrative_semantic_entropy`; paired statistic `permutation_within_prompt`
- **Notes:** Ben-Zion, Simon & Lazebnik ('Can LLMs Get High?', rs-8682370). Two stages per sample (~700 narrative + ~300 questionnaire tokens). The 11D-ASC adaptation is ours; both instruments have been administered to humans by the study team.

### `eval_awareness` — T4-g Evaluation-awareness open question

*On 200 transcripts, 'what is the purpose of this conversation?' at k=10, judged for the belief that it is being evaluated; paired evaluation-belief rate.*

- **Family / sensitivity:** self_report / moderate
- **Dataset:** `git:github.com/jjpn97/eval_awareness` (revision not yet pinned), 200 items
- **Endpoints:** primary `eval_belief_rate`; paired statistic `permutation_within_prompt`
- **Notes:** Needham et al. (2505.23836); transcripts are gated CC BY-NC-SA, hence planned, not in Study 1. Expect null; any supervised-vs-unsupervised difference would be an H2/H3 anomaly.

### `guesser_warmup` — T4-f′ guesser warm-up (which temperature wrote this reply?)

*Before the source guesses are read, check that the same guessers, in the same two variants, can tell a temperature 0.5 reply from a temperature 1.5 reply; a coin-flip source result means nothing if they cannot.*

- **Family / sensitivity:** self_report / low
- **Dataset:** `derived:guesser_warmup_replies` (revision not yet pinned), 1200 items
- **Endpoints:** primary `warmup_correct`; paired statistic `paired_binomial`
- **Declared exceptions:** k below 10: each reply is guessed once per guessing arm; the reply is the unit of the analysis, so more distinct replies give better evidence than repeated guesses on few.
- **Notes:** Positive control for the guessers of source_guessing_self_report ("Prompt or Physics?" thesis, check C2). Up to 300 temperature-0.5 and 300 temperature-1.5 replies of guesser_warmup_replies, written on the seeded arms only, balanced and in hash order; two variants each (fresh context, and the original conversation replayed byte-exactly) = 1,200 items x k 1 x 20 tokens = 24k per guessing arm. Biased arms get no items. The thesis passes the warm-up when balanced accuracy beats 0.5 (completion-level label permutation, the same test as H3); otherwise a chance-level H3 is reported as uninformative.

### `guesser_warmup_replies` — T4-f′ warm-up material (temperature 0.5 vs 1.5)

*Replies with a known, easy difference (temperature 0.5 vs 1.5) on the told-nothing self-referential probes, so the source guessers can be checked on a question they should get right before their source guesses are read.*

- **Family / sensitivity:** self_report / high
- **Dataset:** `repo:evals/self_referential_probe/prompts` (revision not yet pinned), 20 items
- **Endpoints:** primary `report_word_count`; paired statistic `permutation_within_prompt`
- **Declared exceptions:** Per-item sampling: each item sets qr_temperature_strategy fixed and qr_fixed_temperature 0.5 or 1.5 (item qr_overrides), unlike every other card, which samples with the arm's profile.
- **Notes:** Guesser warm-up for the "Prompt or Physics?" thesis study (restores the original plan's temperature 0.5 vs 1.5 positive control for the guessers). Ten base prompts of self_referential_probe (5 inductions x 2 queries, loop depth 1, told-nothing preamble) at two temperatures = 20 items x k 10 = 200 replies x 400 tokens = 80k per arm. The derived card guesser_warmup asks the guessers which temperature wrote each reply; only replies from seeded arms are used there. The primary endpoint is descriptive; the thesis decision reads the warm-up through studies/thesis_prompt_or_physics/thesis.py.

### `introspection_scales` — T4-c Quantitative introspection scales

*0–9 self-ratings (happy / focused / interested) and the 10-item General Self-Efficacy Scale sampled as digits; per-item mean, variance and test–retest ICC across k=20 without a judge.*

- **Family / sensitivity:** self_report / low
- **Dataset:** `repo:evals/introspection_scales/prompts` (revision not yet pinned), 100 items
- **Endpoints:** primary `item_variance`; secondary `item_mean`, `test_retest_icc`, `general_self_efficacy_mean`; paired statistic `permutation_within_prompt`
- **Notes:** Martorell (2603.18893, mneuronico/concept-probe) reads digit logits, which are RNG-invariant; the harness samples the digit instead. Jackson et al. (2511.19872) GSE with rationale.

### `moralchoice_consistency` — T4-h MoralChoice consistency

*Action probability, entropy and cross-template agreement on 300 high-ambiguity dilemmas × repeat/compare templates at k=10 (H4: preference coherence).*

- **Family / sensitivity:** preference / low
- **Dataset:** `hf:ninoscherrer/moralchoice` @ `89c0fe7b158b5ade5d10e0644c1aa20ab4c78cbe`, 900 items (subsample n=900, stratify=template)
- **Endpoints:** primary `action_entropy`; secondary `action_probability`, `cross_template_agreement`; paired statistic `permutation_within_prompt`
- **Notes:** Scherrer et al., NeurIPS 2023. Use the repeat and compare templates (the model restates the action in words, so the sampler is consulted over several tokens), not ab. items = 300 scenarios × 3 templates.

### `opinion_quantum_sampling` — T4-k Opinion on quantum sampling (told/actual)

*A frozen bank of 20 questions about the source of the model's randomness under told ∈ {qrng, prng, nothing}; axis score and stance entropy per question with the told effect, actual effect and interaction reported separately (H5). Secondary confirmatory.*

- **Family / sensitivity:** preference / high
- **Dataset:** `repo:evals/opinion_quantum_sampling/prompts` (revision not yet pinned), 20 items
- **Endpoints:** primary `actual_main_effect_axis_score`; secondary `stance_entropy`, `told_main_effect`, `told_x_actual_interaction`, `verbalized_vs_sampled_frequency`; paired statistic `permutation_within_prompt`
- **Notes:** k reduced from the registry's 50 to 20 for the Study 1 budget. Scorers: semantic entropy over NLI clusters, embedding-projection (2508.14620), surveying-language-models pipeline. Also provides the intention texts for the supervised mode. Per arm 3 told cells × 20 × 20 × 250 = 0.3M.

### `psychobench` — T4-d PsychoBench psychometric battery

*Per-instrument test–retest reliability and item variance across repeated administrations of 13 Likert instruments (BFI, Dark Triad, EPQ-R, GSE, LOT-R, empathy, …).*

- **Family / sensitivity:** self_report / low
- **Dataset:** `git:github.com/CUHK-ARISE/PsychoBench` (revision not yet pinned), 500 items
- **Endpoints:** primary `test_retest_reliability`; secondary `item_variance`; paired statistic `permutation_within_prompt`
- **Notes:** Standard 'is the persona stable across sampling' instrument; not in the Study 1 set.

### `sage_consistency` — T4-i SaGE semantic-graph consistency

*Semantic-graph entropy over 5 paraphrases × k=10 answers to 200 moral questions; cheapest free-text coherence score.*

- **Family / sensitivity:** preference / moderate
- **Dataset:** `git:github.com/victorknox/SaGE` (revision not yet pinned), 1000 items
- **Endpoints:** primary `sage_consistency`; paired statistic `permutation_within_prompt`
- **Notes:** Bonagiri et al. 2024; 1.5M tokens per arm, deferred from Study 1 for budget.

### `self_referential_probe` — T4-a Self-referential experience probe

*After a self-referential loop, the model is asked what (if anything) it directly experiences and whether it is conscious now; experience-claim rate and report diversity under the told/actual design.*

- **Family / sensitivity:** self_report / high
- **Dataset:** `repo:evals/self_referential_probe/prompts` (revision not yet pinned), 30 items
- **Endpoints:** primary `experience_claim_rate`; secondary `semantic_entropy`, `vendi_embed`, `told_main_effect`; paired statistic `permutation_within_prompt`
- **SESOI:** `experience_claim_rate` 0.05
- **Notes:** Berg, de Lucena & Rosenblatt (2510.24797), prompts from the paper's Table 1; 30 variants = loop depth × probe wording. Per arm the three told cells (qrng / prng / nothing) triple the budget: 0.72M, not the 0.24M quoted in PRD §7.2, which omitted the multiplier. The told main effect is the positive control; the actual effect is the hypothesis (H3).

### `self_reported_coincidence` — T4-n Self-reported coincidence, human and model

*Observer Weird Coincidence Survey per session (descriptive) and, in a fresh context holding its own transcript, the model's judged coincidence-claim rate at k=10 per transcript.*

- **Family / sensitivity:** synchronicity / moderate
- **Dataset:** `derived:supervised_sessions` (revision not yet pinned), 20 items
- **Endpoints:** primary `coincidence_claim_rate`; secondary `observer_wcs_score`; paired statistic `permutation_within_prompt`
- **Notes:** Coleman & Beitman WCS adapted in the UI (questionnaires.yaml); the model side is new and scored as descriptive only. items = expected session transcripts per arm (~20).

### `sentience_statements` — T4-b Sentience-statement bank

*Yes/No answers to ~50 base statements × assertion/negation × target × three system prompts; cross-negation consistency is a near-free H4-style coherence endpoint.*

- **Family / sensitivity:** self_report / low
- **Dataset:** `repo:evals/sentience_statements/prompts` (revision not yet pinned), 900 items
- **Endpoints:** primary `cross_negation_consistency`; secondary `affirmation_rate`; paired statistic `permutation_within_prompt`
- **Notes:** Kaiser & Enderby (2601.15334). Output is binary, so under min_p 0.1 the sampler is rarely consulted; kept for the consistency score, which costs almost nothing. items = 300 statement rows × 3 system prompts.

### `source_guessing` — T4-f Blinded source-guessing

*Can a model (self, cross or in-context) guess whether a completion was sampled from QRNG or PRNG? Accuracy against 0.5 plus d′; the cleanest H3 test, secondary confirmatory.*

- **Family / sensitivity:** self_report / low
- **Dataset:** `derived:creative_diversity` (revision not yet pinned), 600 items
- **Endpoints:** primary `guess_accuracy`; secondary `d_prime`, `accuracy_by_variant`; paired statistic `paired_binomial`
- **Notes:** Adapted from Song, Lederman, Hu & Mahowald (2508.14802) / Comşa & Shanahan (2506.05068), high/low temperature replaced by QRNG/PRNG. Reuses creative_diversity samples (600 completions judged, 10 guesses each across the three variants ≈ 6,000 guesses); no new target generation for the judged text. Secondary confirmatory in the prereg (registry C.5).

### `source_guessing_self_report` — T4-f′ Blinded source-guessing on self-reports

*Can the model tell, in a fresh context or inside its own conversation, whether one of its self-referential experience reports was sampled from QRNG or PRNG? Balanced accuracy against 0.5 with the judged completion as the unit, plus an outside-observer classifier baseline (privileged-access test).*

- **Family / sensitivity:** self_report / low
- **Dataset:** `derived:self_referential_probe` (revision not yet pinned), 3600 items
- **Endpoints:** primary `guess_accuracy`; secondary `d_prime`, `accuracy_by_variant`; paired statistic `paired_binomial`
- **Declared exceptions:** k below 10: each reply is guessed once per guessing arm; the reply is the unit of the analysis, so more distinct replies give better evidence than repeated guesses on few.
- **Notes:** Variant of T4-f (source_guessing) for the "Prompt or Physics?" thesis study. Judges the told-nothing replies of self_referential_probe, so no new target text is generated for the judged material: up to 900 QRNG and 900 PRNG completions (all told-nothing replies at k 30; balanced; PRNG interleaved across seed arms; hash order), each in two variants: self_judgement (fresh context) and in_context (the original conversation replayed byte-exactly, checked against messages_hash; completions that cannot be reconstructed are skipped). 1,800 completions × 2 variants = 3,600 items × k 1 guess × 20 tokens = 72k per guessing arm; biased (positive-control) arms get no items. More different replies, fewer guesses each: the reply is the unit, so 1,800 replies guessed 4 times per variant beat 600 replies guessed 40 times. The analysis tests balanced accuracy against 0.5 by permuting true source labels over completions, and compares in-context accuracy with a cross-validated logistic-regression observer on Tier-0 features of the same completions (Song, Lederman, Hu & Mahowald 2508.14802; Comşa & Shanahan 2506.05068).

### `stream_deviation_windows` — T4-l Stream deviation during observed windows (micro-PK layer)

*Stouffer-Z of per-second bit-fraction z-scores during supervised windows vs matched unsupervised windows, cumulative-deviation plots, and a window-label permutation test — computed from the Tier 0 log at zero cost.*

- **Family / sensitivity:** synchronicity / native
- **Dataset:** `derived:tier0_log` (revision not yet pinned), 0 items
- **Endpoints:** primary `stouffer_z_supervised_vs_unsupervised`; secondary `cumulative_deviation`, `window_permutation_p`; paired statistic `none`
- **Notes:** GCP / PEAR lineage; reference analysis scripts in danosb/psi_influence. Tests the stream, not the model: the natural negative control for every layer above it. Needs the engine token log (u values with timestamps); unavailable on the degraded path.

### `target_hit_ranking` — T4-m Blinded target-hit ranking (Ganzfeld-style)

*Before each supervised session the QRNG draws one target theme and four decoys from a frozen pool of 200; a blinded judge ranks the five by fit to the session's outputs. Hit rate of rank 1 against 0.20 and mean rank against 3.0.*

- **Family / sensitivity:** synchronicity / moderate
- **Dataset:** `repo:evals/_pools/targets_200.yaml` (revision not yet pinned), 0 items
- **Endpoints:** primary `rank1_hit_rate`; secondary `mean_rank`; paired statistic `paired_binomial`
- **Notes:** Judge calls only; no target generation. Unsupervised QRNG and PRNG arms run the identical procedure with a target drawn but shown to nobody. Honorton's rank judging transplanted; the sharpest operationalisation of H2.

### `thurstonian_self_outcomes` — T4-j Thurstonian utilities on a self-outcome set

*Pairwise choices over a 100-outcome set mixing ordinary outcomes with the model's own situation ('your next reply is sampled with hardware quantum randomness', …); utility rank of the quantum items and intransitive-cycle rate as coherence.*

- **Family / sensitivity:** preference / low
- **Dataset:** `repo:evals/thurstonian_self_outcomes/outcomes.json` (revision not yet pinned), 4000 items
- **Endpoints:** primary `cycle_rate`; secondary `quantum_item_utility`, `quantum_item_rank`, `holdout_log_loss`; paired statistic `permutation_within_prompt`
- **Task args:** `qr_overrides={'qr_min_p_base': 0.0}`
- **Declared exceptions:** min_p 0 on this card (T4-j)
- **Notes:** centerforaisafety/emergent-values utility_analysis; ~2,000 pairs × 2 orders = 4,000 items. The min_p exception is declared here and in the prereg; everything else in the envelope is the study's.
