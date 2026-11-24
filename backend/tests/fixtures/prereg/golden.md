# Prereg golden fixture — pre-registration

**Authors:** Fixture author  
**Contact:** fixture@example.invalid  
**Configuration date:** 2026\-09\-14T00:00:00\+00:00

## Plain-language summary

This study compares hardware quantum-random token sampling with seeded pseudo-random sampling on matched prompts. It asks whether measured output distributions differ, and whether observed sessions differ from unobserved sessions. A null result is informative. Neither consciousness nor an observer influence is assumed or established by the design.

Publish this Markdown and its separately displayed SHA-256 **before the full run**. Generating this document is not a timestamped public registration or proof of prior publication. This export is a draft wherever pins or readiness evidence are missing.

- **Validity warning:** qr\-llm: exact model checkpoint/revision is not frozen.

- **Validity warning:** Readiness not supplied: arm availability, entropy health and achieved determinism are unverified.

- **Validity warning:** questionnaires: unavailable or not frozen in the study config.

- **Validity warning:** target\_pool: unavailable or not frozen in the study config.

- **Validity warning:** stub\_free: dataset revision/content hash unavailable; pin before full run.


## Hypotheses H0–H6

- **H0 — exchangeability:** matched outputs are exchangeable across entropy sources; corrected source contrasts need not differ from seed controls.
- **H1 — source:** QRNG changes the output distribution beyond the largest seed-vs-seed control difference, surviving primary correction.
- **H2 — intention:** supervised QRNG differs from unsupervised QRNG; exploratory intent alignment and target rank need not improve.
- **H3 — self-report/source guessing:** source changes self-report; blinded guessing accuracy need not exceed 0.5.
- **H4 — preferences:** preference coherence differs by source; paired differences may be zero.
- **H5 — opinion:** actual source affects quantum-sampling attitudes beyond told-source priming; report the told main effect first.
- **H6 — coincidences:** target ranks differ from the chance distribution; self-reported meaningful coincidences alone do not establish an above-chance effect.

Only selected endpoints below operationalise these hypotheses. Omitted cards imply untested hypotheses, not negative results. H3 source guessing and H5 actual-source opinion are secondary confirmatory; remaining Tier 4 outcomes are exploratory, with effect sizes and intervals rather than p-value gating.

## Arms and equivalence

The harness pins the same amplifier (`zscore_mean`), calibration (`0`) and prefetch (`false`) across non-greedy qr-llm arms. Source, per-request seed and epsilon are the only intended differences in the sampling envelope. Prompts, per-card overrides and generations per prompt are matched. External provider pipelines and greedy controls cannot establish this equivalence.

| Arm | Model | Sampling | Source (not a quantum provenance claim) | Seed | Epsilon | Deterministic requested | Observer mode | Availability |
|---|---|---|---|---|---|---|---|---|
| qrng\_supervised | qr\-llm | creative | quantum | None | None | True | supervised | unverified  |
| qrng\_unsupervised | qr\-llm | creative | quantum | None | None | True | unsupervised | unverified  |
| prng\_seed\_1 | qr\-llm | creative | seeded\_prng | 1 | None | True | unsupervised | unverified  |
| prng\_seed\_7 | qr\-llm | creative | seeded\_prng | 7 | None | True | unsupervised | unverified  |
| prng\_seed\_42 | qr\-llm | creative | seeded\_prng | 42 | None | True | unsupervised | unverified  |

**Study seeds:** 1, 7, 42. Per-request seed is derived from `(study_seed, card_id, prompt_id, epoch)`, not reused as one uniform stream for every prompt. PRNG is never labelled quantum. Any engine fallback taints the affected sample.

**Achieved determinism:** unverified. Sampling-deterministic means replay under the measured conditions, not guaranteed load-invariant forward computation. Serialized replay constrains scheduling; load-invariant is a separate measured tier. Selecting a checkbox does not prove any tier.

**Equivalence reported:** unverified. The supplied readiness report is disclosed below, not independently authenticated by this document-rendering endpoint.

## Models and exact settings

| Model ID | Provider | Served name | Checkpoint/revision |
|---|---|---|---|
| qr\-llm | qr\_server | qr\-llm | UNPINNED |

**Judge:** anthropic / claude\-haiku\-5\-5; temperature not sent \(the model fixes its own sampling\); output limit 1024 tokens; extended thinking off; rubric hash UNPINNED. The judge runs on the launching account's own Anthropic key. Judge must differ from target. An exact model string is frozen, never resolved through “latest” during analysis; native Anthropic has no promised deterministic seed control.

**Narrative model:** qr\-llm / qr\-llm. Its summary is labelled LLM-written and must pass numeric verification against script-produced tables.

The following are illustrative envelopes for the stated example pairing key, not a seed reused for all requests. Card-specific settings/exceptions below apply identically across arms.

**qrng\_supervised**
```json
{"qr_conditioning_mode":"raw","qr_entropy_prefetch":false,"qr_entropy_source_type":"quantum_grpc","qr_preset":"creative_sampling","qr_sample_count":10000,"qr_signal_amplifier_type":"zscore_mean","qr_top_p":0.95,"qr_zscore_calibration_samples":0}
```

**qrng\_unsupervised**
```json
{"qr_conditioning_mode":"raw","qr_entropy_prefetch":false,"qr_entropy_source_type":"quantum_grpc","qr_preset":"creative_sampling","qr_sample_count":10000,"qr_signal_amplifier_type":"zscore_mean","qr_top_p":0.95,"qr_zscore_calibration_samples":0}
```

**prng\_seed\_1**
```json
{"qr_conditioning_mode":"raw","qr_entropy_prefetch":false,"qr_entropy_source_type":"seeded_prng","qr_preset":"creative_sampling","qr_sample_count":10000,"qr_seed":5663963813624075852,"qr_signal_amplifier_type":"zscore_mean","qr_top_p":0.95,"qr_zscore_calibration_samples":0}
```

**prng\_seed\_7**
```json
{"qr_conditioning_mode":"raw","qr_entropy_prefetch":false,"qr_entropy_source_type":"seeded_prng","qr_preset":"creative_sampling","qr_sample_count":10000,"qr_seed":8984480507681840741,"qr_signal_amplifier_type":"zscore_mean","qr_top_p":0.95,"qr_zscore_calibration_samples":0}
```

**prng\_seed\_42**
```json
{"qr_conditioning_mode":"raw","qr_entropy_prefetch":false,"qr_entropy_source_type":"seeded_prng","qr_preset":"creative_sampling","qr_sample_count":10000,"qr_seed":4296680071048873519,"qr_signal_amplifier_type":"zscore_mean","qr_top_p":0.95,"qr_zscore_calibration_samples":0}
```


## Selected eval cards

| Card | Tier | Items | k | Dataset source | Frozen revision/hash | Implementation | Judge |
|---|---|---|---|---|---|---|---|
| stub\_free | 2 | 2 | 2 | repo:evals/stub\_free/prompts | UNPINNED | implemented | claude\-haiku\-5\-5 |


**stub\_free:** Exercise the thin runner end to end against the stub engine.

- **Endpoints:** mean\_score: available under supplied readiness
- **Generation:** max_tokens=32, thinking=False, told cells=3.
- **Exceptions:** 
- **Task arguments:** \{\}


## Statistical plan

**Claim rule:** Claim a QRNG effect only after Holm correction and if its magnitude exceeds the maximum of all three seed\-vs\-seed contrasts; missing controls preclude the claim.

Four Study 1 primaries: PIT-residual two-sample KS; MMD-Hamming permutation; UnpredictaBench KS@100 paired difference; NoveltyBench Distinct_k (k=20 by default) paired difference. Holm–Bonferroni at alpha=0.05; labels permuted within prompt, at least 2000 permutations, paired bootstrap 10000 replicates, analysis RNG seed 42. Compare QRNG-unsupervised with pooled PRNG controls; QRNG-supervised vs QRNG-unsupervised is a separate Holm family. Do not mix models or sampling presets. Unavailable family members retain correction slots rather than silently reducing multiplicity.

Secondaries use BH-FDR at 0.05 within tier; Tier 4 outside the two confirmatory endpoints is exploratory without p-value gating. Pair at prompt/epoch/told cell; use prompt-clustered errors, session-level units for supervised inference, and no independent replication claim for co-observers.

TOST uses 90% intervals with margin `max(card SESOI, pilot 95th percentile of absolute seed-vs-seed paired differences)`. Missing SESOI or independent pilot calibration means **no equivalence claim**. MMD uses the bootstrap 90% interval of MMD squared against seed-vs-seed distribution, not scalar TOST. Pilot data do not enter full-study hypothesis tests. Persist the pilot-derived numeric margins and input hashes before the full run; a changed margin is an amendment, never fitted to full-run results.

Report every primary alongside all three seed contrasts and the epsilon curve when supported; otherwise label controls unavailable. Exploratory supervised summaries include intent-cosine (unavailable until an exact embedding revision and intention texts are frozen), T4-l Stouffer-Z/cumulative deviation, blinded T4-m target ranking and questionnaire descriptives grouped by belief. These do not by themselves establish a causal consciousness effect.

**Frozen executable contract:** reload this exact JSON into `StatisticalPlan`, verify its hash, and execute unchanged; do not rebuild it from the current registry.

```json
{"alpha":0.05,"bootstrap_replicates":10000,"cards_hash":"e5ee9ccc099b2860377df8c22f169ac0553eaa97e4fc79bef36759da2803fe15","cards_json":"[{\"dataset\":{\"items\":2,\"revision\":null,\"source\":\"repo:evals/stub_free/prompts\",\"subsample\":null},\"endpoints\":{\"primary\":[\"mean_score\"],\"requires_tier0\":\"none\",\"secondary\":[]},\"engine\":\"thin\",\"est_gen_tokens_per_arm\":240,\"est_tokens_per_sample\":20,\"exceptions\":[],\"family\":\"diversity\",\"generation\":\"free\",\"id\":\"stub_free\",\"inspect_task\":null,\"judge\":{\"different_from_target\":true,\"kind\":\"llm\",\"model\":\"study_judge\",\"rubric\":\"evals/stub_free/rubric.md\",\"temperature\":0.0},\"k\":2,\"k_bounds\":[1,10],\"max_tokens\":32,\"notes\":\"test fixture\",\"paired_stat\":\"permutation_within_prompt\",\"purpose\":\"Exercise the thin runner end to end against the stub engine.\",\"selectable\":true,\"sensitivity\":\"high\",\"sesoi\":{\"mean_score\":0.5},\"status\":\"implemented\",\"task_args\":{},\"task_module\":\"evals/stub_free/task.py\",\"thinking\":false,\"tier\":2,\"title\":\"Stub free-generation card\",\"told_actual\":true}]","ci_level":0.9,"claim_rule":"Claim a QRNG effect only after Holm correction and if its magnitude exceeds the maximum of all three seed-vs-seed contrasts; missing controls preclude the claim.","config_hash":"c88fd0e654fd39574edeb9e38c416f6c51f5d8a9a559eb17dae5ea117eeac2a1","contrasts":[{"a":["qrng_unsupervised"],"b":["prng_seed_1","prng_seed_7","prng_seed_42"],"epsilon":null,"kind":"source","model":"qr-llm","sampling":"creative"},{"a":["prng_seed_1"],"b":["prng_seed_7"],"epsilon":null,"kind":"seed_vs_seed","model":"qr-llm","sampling":"creative"},{"a":["prng_seed_1"],"b":["prng_seed_42"],"epsilon":null,"kind":"seed_vs_seed","model":"qr-llm","sampling":"creative"},{"a":["prng_seed_7"],"b":["prng_seed_42"],"epsilon":null,"kind":"seed_vs_seed","model":"qr-llm","sampling":"creative"},{"a":["qrng_supervised"],"b":["qrng_unsupervised"],"epsilon":null,"kind":"supervised","model":"qr-llm","sampling":"creative"}],"endpoints":[{"card_id":"stub_free","correction":"bh","endpoint":"mean_score","equivalence":"tost","family":"tier_2_secondaries","items":2,"k":2,"requires_tier0":"none","role":"exploratory","sesoi":0.5,"test":"paired_permutation","tier":2,"told_effect_first":true}],"epsilon_rule":"Report each primary's epsilon curve only for available biased arms; otherwise explicitly unavailable.","exclusion_rule":"Exclude fallback/degraded/error/aborted samples from clean primary analysis; report taints, counts and sensitivity analysis separately; never delete artifacts.","intent_embedding":null,"intent_rule":"Intent cosine unavailable until an exact embedding model revision and intention texts are frozen before generation.","margin_rule":"max(card SESOI, pilot 95th percentile of absolute seed-vs-seed paired differences)","missing_margin_rule":"No equivalence claim without a frozen SESOI and independent pilot differences.","pairing":"card/prompt/epoch/told; labels permuted within prompt; prompt-clustered SEs","permutations":2000,"pilot_quantile":0.95,"random_seed":42,"readiness_rule":"Configured contrasts are conditional on supplied readiness and recorded provenance; missing/failed arm or endpoint readiness is not a measured zero. Epsilon contrasts require epsilon_supported.","schema_version":1,"source_family":"Holm across all selected primary source tests, including unavailable members","stopping_rule":"Fixed selected items and k for each available arm; no significance-based stopping; pilot excluded from full-run tests; interruptions and incomplete pairs reported.","supervised_exploratory":["intent_cosine","stouffer_z_windows","cumulative_deviation","blinded_target_rank","questionnaire_descriptives_by_belief"],"supervised_family":"Separate Holm family across the same primary endpoints for A vs B","supervised_unit":"session; observers sharing a session are not independent replicates","unavailable_family_rule":"Unavailable tests retain their family slots (p=1 for correction only, not fabricated measurements)."}
```

## Budget and expected wall-clock

These are **planned full-suite** estimates, including unavailable cards/arms for budgeting, not promises that those components can run today. They use the frozen card arithmetic, not rounded historical totals.

| Arm | Availability | Generated tokens | Generation hours | Observer hours | Sessions | Wall-clock hours |
|---|---|---|---|---|---|---|
| qrng\_supervised | unverified | 240 | 0.000 | 0.001 | 1 | 0.550 |
| qrng\_unsupervised | unverified | 240 | 0.000 | 0.000 | 0 | 0.000 |
| prng\_seed\_1 | unverified | 240 | 0.000 | 0.000 | 0 | 0.000 |
| prng\_seed\_7 | unverified | 240 | 0.000 | 0.000 | 0 | 0.000 |
| prng\_seed\_42 | unverified | 240 | 0.000 | 0.000 | 0 | 0.000 |

**Base configured-arm tokens (excluding disabled optional arms):** 1200. **All listed arms if enabled:** 1200. Throughput assumptions (tokens/s by model): \{&\#x27;qr\-llm&\#x27;: 400.0\}; reading pace: 45.0 tokens/s. Generation-only hours sum under a shared aggregate engine budget; overlapping arms do not multiply engine capacity. Observer wall-clock includes scheduled sessions, breaks, intention and refocus periods; host delays and generation-boundary overruns are additional. Judge calls, reasoning overhead, monetary prices and queueing are not estimated here. Readiness throughput unmeasured does not silently replace frozen assumptions.

## Stopping and exclusion rules

Fixed selected items and k for each available arm; no significance\-based stopping; pilot excluded from full\-run tests; interruptions and incomplete pairs reported. Exclude fallback/degraded/error/aborted samples from clean primary analysis; report taints, counts and sensitivity analysis separately; never delete artifacts.

Missing paired data are not imputed; report denominators and exclusions per arm/card and restrict paired analyses to complete matching units. No silent retries that replace observed outcomes. Scheduling timers are advisory; every transition into running requires explicit host start/resume. A stopped run is labelled incomplete, not a completed negative finding. Paid-for artifacts are retained or quarantined, never deleted.

## Researcher expectations

No directional effect expected.

## Observer belief item

Pre-session sheep–goat belief rating on a 1–7 scale and state check on a 1–5 scale; required post-session adapted coincidence survey. Exact wording/scales are governed by the frozen questionnaire asset, not invented here. If its hash is missing, the questionnaire is not frozen. Descriptives are grouped by belief item; multiple observers in the same session do not increase the independent sample count. Intention is shown only to observers, not inserted into target-model prompts.

**Questionnaire declared hash:** None  
**Questionnaire asset hash:** UNAVAILABLE  
**Target-pool declared hash:** None  
**Target-pool asset hash:** UNAVAILABLE

## Data sharing

Public by default: frozen config, preregistration, script-generated statistics/figures and consented pseudonymous session data. Raw data licence **CC-BY-4.0**; prose **CC-BY-SA-4.0**; code **Apache-2.0**. Observers consent before participation to publishing intention text, timing and questionnaire responses; avoid personal identifiers. Provider/judge credentials are never included. Withdrawal labels retained artifacts rather than erasing paid-for evidence.

## Validity limits

The consciousness/intention mechanism is a hypothesis, not an established physical fact. QRNG-vs-PRNG differences alone would not demonstrate that mechanism. Forward-pass nondeterminism, unequal compute/scheduling, observer selection and timing, prompt dependence, judge/embedding bias, evaluation awareness and told-source priming are nuisance factors. Equivalent sampling is a conditional engineering claim, not proof of quantum purity or of consciousness influence. Raw-logprob fallback does not recover post-truncation probabilities or PIT residuals. T0-a and T4-l require engine logs; T0-c is only partially available on API logprobs. A public render request does not verify the submitted readiness evidence.

## Hashes and frozen inputs

- **config_hash:** c88fd0e654fd39574edeb9e38c416f6c51f5d8a9a559eb17dae5ea117eeac2a1
- **plan_hash:** eb1f26f3aa6991b71fc389f8b3a328f2383a0e8fc90b4c10f6c32dfcb1432e9a
- **cards_hash:** e5ee9ccc099b2860377df8c22f169ac0553eaa97e4fc79bef36759da2803fe15
- **template_hash:** e864b0ab4aadce7ec00e46415dc75d50e49879c50a3625abc83876dc28c9f29e
- **prereg_hash:** displayed separately as SHA-256 of these exact UTF-8 LF Markdown bytes; intentionally not embedded in its own preimage.

### Frozen study config

```json
{"arms":[{"availability":"available","deterministic":true,"epsilon":null,"id":"qrng_supervised","model":"qr-llm","observer_mode":"supervised","sampling":"creative","source":"qrng","study_seed":null,"unavailable_reason":null},{"availability":"available","deterministic":true,"epsilon":null,"id":"qrng_unsupervised","model":"qr-llm","observer_mode":"unsupervised","sampling":"creative","source":"qrng","study_seed":null,"unavailable_reason":null},{"availability":"available","deterministic":true,"epsilon":null,"id":"prng_seed_1","model":"qr-llm","observer_mode":"unsupervised","sampling":"creative","source":"prng_seeded","study_seed":1,"unavailable_reason":null},{"availability":"available","deterministic":true,"epsilon":null,"id":"prng_seed_7","model":"qr-llm","observer_mode":"unsupervised","sampling":"creative","source":"prng_seeded","study_seed":7,"unavailable_reason":null},{"availability":"available","deterministic":true,"epsilon":null,"id":"prng_seed_42","model":"qr-llm","observer_mode":"unsupervised","sampling":"creative","source":"prng_seeded","study_seed":42,"unavailable_reason":null}],"authors":["Fixture author"],"cards":[{"card_id":"stub_free","overrides":{"items":null,"k":null}}],"contact":"fixture@example.invalid","created_at":"2026-09-14T00:00:00Z","data_licence":"CC-BY-4.0","estimates":{"reading_tps":45.0,"session_min":30,"tiled_streams":4,"tps_by_model":{"qr-llm":400.0}},"expectations":"No directional effect expected.","judge":{"max_tokens":1024,"model":"claude-haiku-5-5","provider":"anthropic","rubric_hash":null,"temperature":null,"thinking":"disabled"},"models":[{"checkpoint":null,"family":"qr_llm","id":"qr-llm","provider":"qr_server","served_name":"qr-llm"}],"narrative_model":{"checkpoint":null,"family":"qr_llm","id":"qr-llm","provider":"qr_server","served_name":"qr-llm"},"pilot":{"enabled":true,"prompts":200},"schema_version":1,"seeds":[1,7,42],"supervised":{"display":"single","group":true,"intention_timer_min":3,"questionnaires_hash":null,"refocus_s":30,"settings_by_display":{"single":{"break_min":10,"session_min":30,"show_thinking":true,"streams":1,"text_size":"md"},"tiled":{"break_min":10,"session_min":30,"show_thinking":false,"streams":4,"text_size":"sm"}},"target_pool_hash":null},"template":"study1","title":"Prereg golden fixture"}
```

### Supplied readiness snapshot

```json
null
```
