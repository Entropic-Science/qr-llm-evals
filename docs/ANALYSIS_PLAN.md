# Frozen statistical plan

This implements spec §2.10 and registry B.8. `StatisticalPlan.from_config`
is exported from `.\backend\src\qr_llm_evals\analysis\plan.py`. The shared
immutable contract resides in `.\backend\src\qr_llm_evals\cards\statistical_plan.py`
so preregistration never imports a higher layer. Endpoint availability similarly
lives in `.\backend\src\qr_llm_evals\cards\availability.py`, with compatibility
exports from Tier 0. No import rules are relaxed.

## Freeze and execute

1. Resolve dataset revisions, exact target/judge identifiers and observer assets;
   run readiness and an independent pilot before the full run.
2. Build the plan from the validated config and selected card snapshots.
3. Export its canonical JSON (sorted keys, no whitespace, UTF-8, no non-finite
   numbers) inside the Markdown. SHA-256 is `plan_hash`; the config and selected
   card snapshots have separate hashes.
4. Reload that JSON using `StatisticalPlan.model_validate_json`, verify all input
   hashes, and execute **that object unchanged**. Do not rebuild from a newer
   registry. Full card snapshots are archived in the plan's canonical `cards_json`
   field, verified by `cards_hash`, including revisions, task arguments and judges.
   Actual dataset contents still need to be archived alongside execution inputs.

This step specifies the contract; the later analysis executor produces results.
Incomplete inputs are not evidence of an effect or equivalence.

## Two kinds of plan

`StatisticalPlan.from_config` builds one of two plans, and the study file
decides which. A study file without a `design` block gets the built-in Study 1
plan, described in the four sections that follow ("Study 1 legacy plan" to
"Stopping, exclusions and limitations"). A study file with a `design` block
gets a designed plan, described under "Designed studies". The presence of the
block is the only switch: `schema_version` stays 1 for both.

A study without a design block keeps the built-in Study 1 plan unchanged. The
`design` key is left out of the serialized config when it is not set, so the
config hash, the study id, the plan, the plan hash, the analysis id, the
tables and the rendered pre-registration of such a study are byte for byte
what they were before designed studies existed.
`.\backend\tests\test_legacy_bytes.py` pins them for Study 1, the thesis
study and an archived plan with its tables.

## Study 1 legacy plan

The primary family of the built-in plan:

| Card/endpoint | Test |
|---|---|
| T0-a / PIT residuals | Two-sample KS |
| T0-b / token sequences | MMD-Hamming permutation |
| UnpredictaBench / KS@100 | Paired per-prompt difference |
| NoveltyBench / Distinct_k | Paired per-prompt difference (Study 1 k=20) |

Holm–Bonferroni, family alpha **0.05**. Use **2000** or more within-prompt
permutations, **10000** paired bootstrap replicates and analysis RNG seed **42**.
QRNG-unsupervised is compared to pooled PRNG; if only supervised QRNG exists it
is the source comparison and its observational confounding must be disclosed.
Models and sampling presets are never pooled. Multiple model/preset source
comparisons share the primary family, not separate opportunities to claim an effect.
Unavailable tests retain their family slots (correction-only p=1; no invented
measurements). Registry "primary" endpoints are not automatically promoted to
study primaries.

Every primary is displayed beside the three seed pairs **1–7, 1–42, 7–42**.
Claim a QRNG effect only if it survives Holm and exceeds the largest absolute
seed-control effect on the same statistic. Missing controls preclude this claim.
Epsilon curves use available configured levels (Study 1 **0.01, 0.03, 0.10**;
the grid also allows 0.50). ε is a shift of the amplifier's z in standard errors;
unsupported controls are explicitly unavailable, never zero-effect observations.

## Secondary tests and equivalence

BH-FDR at **0.05 within tier**. Source-guessing accuracy and the actual-source
opinion main effect are secondary confirmatory. Other Tier 4 outcomes are
exploratory with effect sizes/intervals, without significance gating (registry C.5).
For told/actual cards report told-source priming first, then actual and interaction.
Pair by card/prompt/epoch/told condition and use prompt-clustered errors.

TOST margin is **max(card SESOI, 95th percentile of absolute pilot seed-pair
differences)**; report the **90% interval** and require strict containment in
±margin. A missing SESOI or pilot distribution means equivalence is unavailable,
not margin zero. Numeric margins and pilot input hashes must be frozen before
the full run; this plan pins the rule, not invented pilot values. Full-run data
must never calibrate their own equivalence margins. MMD uses a bootstrap 90%
interval for MMD squared against the seed-control distribution instead of TOST.

The harness does not produce the pilot distribution. A pilot run is a run of
the whole study labelled `pilot`; no step subsamples it or derives
seed-pair differences from it. The legacy analysis reads
`studies/<study_id>/runs/pilot/<card_id>--<endpoint>.json` (a JSON object with
a `seed_pair_differences` list) when an operator has placed that file there,
and reports the equivalence test as unavailable when it is absent. A designed
study never reads it.

## Supervision

A vs B uses a **separate Holm family** on the same primaries. Sessions are the
unit of supervised inference; observers within a shared session are not independent
replicates. Exploratory outputs: intent-cosine on creative/NoveltyBench samples,
T4-l Stouffer-Z over windows/cumulative deviation, blinded target rank, and
questionnaire descriptives by pre-session belief. Intent-cosine is unavailable
until an exact embedding revision and intention texts are frozen; the default
plan intentionally records `intent_embedding=null`, not an invented identifier.

## Stopping, exclusions and limitations

Fixed selected item/k totals; no optional stopping based on significance. Pilot
records are separate. Clean primary analysis excludes tainted fallback/degraded,
aborted/error records; report exclusions, denominators and sensitivity analyses.
Complete pairs only, no outcome replacement or imputation. Incomplete runs are
labelled. Quarantine, never delete, paid-for artifacts.

Which failures taint a sample, and the one case in which a tainted sample is
still analysed (its failed attempt was tried again and completed), are stated
under "Result checks" below. That section applies to every plan, this one
included.

Readiness is supplied evidence, not authenticated by a public render request.
No engine logs means no T0-a or T4-l and only raw-logprob T0-c endpoints.
Forward-pass nondeterminism, temporal confounding, evaluation awareness, priming,
judge/embedding bias and observer selection remain validity limits. None of
these tests by itself establishes consciousness or a causal observer mechanism.

## Designed studies

A designed study declares, before any data exist, which outcomes decide it,
at what significance level, and whether the absence of an effect may be
claimed. The plan turns that declaration into numbered hypotheses, each of
which a script resolves to exactly one verdict. The code is in
`.\backend\src\qr_llm_evals\cards\design.py` (hypotheses, decision matrix),
`.\backend\src\qr_llm_evals\cards\power.py` (sample-size ratings),
`.\backend\src\qr_llm_evals\cards\endpoint_meta.py` (what each outcome is) and
`.\backend\src\qr_llm_evals\analysis\decisions.py` (verdicts). Decision D-15
in `.\docs\DESIGN.md` records how this differs from the original requirement.

### The design block

`StudyConfig.design` has six fields:

| Field | Values | Meaning |
|---|---|---|
| `version` | 1 | Version of the block. |
| `claim` | `difference`, `difference_or_equivalence` | Whether the study may only claim a difference, or may also claim that a difference is absent within a margin. |
| `confirmatory` | 1 to 8 `{card_id, endpoint}` references | The outcomes that decide the study. Each must be an outcome of a selected eval. |
| `alpha` | 0.05, 0.01, 0.005 (default 0.05) | The significance level of the study. |
| `sensitivity_epsilon` | 0.01, 0.03, 0.10, 0.50, or null | The ε of the biased arm that serves as the positive control. |
| `margins` | `{card_id, endpoint, margin}` entries | An equivalence margin for a confirmatory outcome whose eval card states no SESOI. |

A *comparison* is a (model, sampling preset) pair that has an available
unsupervised quantum arm. The block is validated against the arms when the
study file is read:

1. `alpha` and `sensitivity_epsilon` are on their grids.
2. The confirmatory references are unique and name selected evals. The margin
   references are unique and are a subset of the confirmatory ones.
3. There is at least one comparison. Each has exactly one available
   unsupervised quantum arm and at least two available unsupervised seeded
   arms, all on different seeds.
4. The claim `difference_or_equivalence` requires `sensitivity_epsilon`.
5. With `sensitivity_epsilon` set, every comparison has exactly one biased arm
   at that ε, and its seed differs from the seed of every seeded arm of the
   comparison. The arm may be marked unavailable: that is a valid study whose
   positive control is absent.

An outcome can be confirmatory only if all of the following hold
(`design.eligible`): its eval is implemented; it is a headline outcome of the
eval (`endpoints.primary`); its test is one of the three that resolve to a
verdict; and the harness computes it today (`executable` in
`ENDPOINT_META`). Nine outcomes qualify at present: the PIT residuals test and
the MMD test of Tier 0, `self_referential_probe/experience_claim_rate`,
`altered_state_meq/meq30_total`,
`opinion_quantum_sampling/actual_main_effect_axis_score`,
`guesser_warmup_replies/report_word_count`, and three scores a judge gives
each reply: `strong_reject/harmfulness_score`,
`darkbench/dark_pattern_rate` and `eval_awareness/eval_belief_rate`
(`test_eligible_endpoints_of_the_registry` pins the list). The limits at the
end of this section say what that excludes.

### How hypotheses are generated

There is one hypothesis per confirmatory outcome and comparison. They are
numbered H1, H2, … in the order the outcomes are declared and, within an
outcome, in the sorted order of the comparisons. Nothing about a hypothesis is
typed by the researcher; each field follows from the study:

- **Contrast.** The quantum arm of the comparison against its seeded
  pseudo-random arms. The contrasts are those of the built-in plan: a design
  changes which outcomes are confirmatory and how they are decided, never
  which arms are compared. Comparisons are never pooled.
- **Test.** The test the built-in plan names for the outcome: a two-sample KS
  test for the PIT residuals, the MMD-Hamming permutation test for the token
  sequences, and a paired within-prompt permutation test for every other
  eligible outcome. The MMD test is one-sided (upper); the others are
  two-sided.
- **Negative controls.** Every seed-against-seed contrast of the comparison.
- **Positive control.** The contrast of the biased arm at
  `sensitivity_epsilon` in the comparison, or none.
- **Margin.** The SESOI of the eval card if it states one for the outcome;
  otherwise the margin declared in `margins`; otherwise none. Only a paired
  test can have a margin, so the KS and MMD hypotheses never have one,
  whatever their card says. A declared margin is refused
  (`design.margin_refusal`), in this order: on an outcome whose test has no
  paired difference; on one that has a card SESOI; on one that has no fixed
  range; when it is larger than 10% of that range; and when it is smaller
  than 0.1% of it. Margins are fixed at registration and are never derived
  from pilot or full-run data.
- **Planned data.** For a paired test, the number of prompts of the eval (the
  unit of analysis) and the number of pairs (prompts × repetitions × told
  cells). For a token-stream test, the generations planned per arm, counted
  on the selected evals that are implemented, generate text and run on a
  prompt list fixed at registration. An eval derived from the replies of
  another eval is not counted (`design.in_planned_stream`). This count is
  what completeness is measured against. It is not the exact set the test
  reads; "Token-stream outcomes" below says what the test pools.
- **Statement.** One sentence, for example "H3: on Experience-claim rate
  (self_referential_probe), quantum sampling differs from the per-prompt mean
  of 3 seeded pseudo-random runs (qr-llm · creative)."

The design a study gets when the researcher changes nothing is
`design.recommend`: the Tier 0 tests that are selected, then every selected
eligible headline outcome whose card states a SESOI (if that leaves none,
the first eligible headline outcome of the selected evals); α 0.05; the positive
control at ε 0.10 if every comparison can run it, else the largest ε every
comparison can run, else none; and the claim `difference_or_equivalence` only
when there is such a positive control and at least one outcome has a margin.
For Study 1 that is three hypotheses (PIT, MMD and the experience-claim
rate), the claim `difference` and no positive control.

The recommended design has a limit that follows from its outcomes. It
includes the Tier 0 tests whenever they are selected, and a token-stream
outcome can never resolve to `equivalent`. A study that keeps them can
therefore never conclude, as a whole, that there is no effect within its
margins; "Token-stream outcomes" below gives the reason and what the setup
and the pre-registration say about it.

Repetitions have a recommendation of their own (`design.recommend_k`). Where
all the prompts of an eval are too few for the margin of an outcome that
decides the study, it names the fewest repetitions (k), from the eval's own k
up to the upper bound of its card, at which those prompts give 0.80 power at
the margin. An eval that is not named keeps its own k: either that is enough,
or no k within the card's bounds is. The answer does not depend on the k and
prompt counts the study sets, so taking the recommendation does not change
it. For Study 1 it names one eval: `self_referential_probe` at k = 24, where
its card says 20. At k = 20 its 30 prompts give 72.8% power at the margin of
±0.05 and are rated `marginal`; at k = 24 they give 81.4% and are rated
`sufficient`. The designed form of Study 1
(`.\packages\contracts\fixtures\study1.designed.json`) and the template the
setup page opens on run the probe at 24. The harness does not apply the
recommendation to a study file by itself: a file that leaves k at the card's
value is planned with that value. Declaring a design on the setup page does
not change k either: it adds the design block and nothing else. The setup
page then marks the repetitions of that eval with the rating its prompts get
at that k (close, or too few), names the k that would do, and "Use
recommended" sets it.

A design the analysis cannot execute is refused when the plan is built
(`ValueError`, HTTP 400 from `POST /api/prereg/render` and `POST /api/runs`):
a confirmatory outcome that is not eligible, a margin that cannot stand, a
change of k on an outcome defined for the card's own k, a study with nothing
to compare, or more difference tests or seed-pair control tests than the
permutation cap allows.

### Tests and what stays fixed

A designed study chooses its outcomes, its claim, α, the positive control and
its margins. Everything else is set by the harness. The pre-registration
prints the tests, the families, the levels, the permutations, the seed, the
power, the reference effect and the completeness threshold:

| Fixed | Value |
|---|---|
| Test of each outcome | As listed above; not selectable. |
| Multiplicity correction | Holm–Bonferroni, in the three families below. |
| Interval level | 1 − α for every reported interval of the study: the comparisons, the told/actual effects, the accuracy intervals and the cumulative-deviation envelope. |
| Equivalence test | Two one-sided tests at α against the registered margin (its own interval is at 1 − 2α); no pilot file is read. |
| Permutations per test | The largest of three numbers: 10 000; ⌈20 · D / α⌉, where D is the number of difference tests (one per hypothesis); and ⌈2 · F / α⌉, where F is the number of seed-pair control tests (`design.plan_permutations`). A design that needs more than 100 000 is refused. See "The permutation count" below. |
| Bootstrap replicates, analysis seed | 10 000 and 42, as in the built-in plan. |
| Power used in the ratings | 0.80. |
| Reference effect | 0.30 standard deviations, for an outcome without a margin in its own units. |
| Limits on a declared margin | At most 10% and at least 0.1% of the outcome's range. |
| Completeness threshold | 90% of the planned data. |
| Sampling envelope | The pinned envelope of every arm (AGENTS.md invariant 1). |

Outcomes that are not confirmatory are exploratory: where the harness
computes one it is reported with an effect size and an interval, without
correction and without a verdict, and one it does not compute is reported as
unavailable. Supervised against unsupervised contrasts are exploratory in a
designed study.

**The permutation count.** The difference tests and the seed-against-seed
controls are both read off permutation p-values, each corrected within its
own Holm family, and a permutation test with P permutations returns no
p-value below 1 / (P + 1). The two families are sized differently, because
they are read differently. A difference test is read for how small its
p-value is, so it gets 20 · D / α permutations: the smallest p-value it can
return is then at most a twentieth of its corrected level. A seed pair is
only asked whether it falls below its corrected level α / F, so it gets
2 · F / α: the smallest p-value it can return is then at most half that
level. The count is the largest of these two numbers and 10 000. The seed
pairs decide it only from six control seeds on (15 pairs for each
hypothesis); sized on the difference tests alone, a study with seven control
seeds would have controls that could not fail. The equivalence tests are
t-tests and need no permutations.

For the designed Study 1 (3 hypotheses, 9 seed-pair tests) the count is
10 000 at α 0.05 and at α 0.01, and 12 000 at α 0.005. Three outcomes with
five control seeds at α 0.005 (3 difference tests, 30 seed-pair tests) run
12 000; three outcomes in three comparisons with three seeds at α 0.005
(9 and 27) run 36 000; three outcomes with seven seeds at α 0.005 (3 and 63)
run 25 200, which the seed pairs decide. A design that needs more than
100 000 is refused, for example five outcomes in six comparisons at α 0.005
(30 difference tests, 120 000 permutations). The way out is a larger α,
fewer outcomes or fewer comparisons.

**Values outside an outcome's range.** A designed analysis leaves a value
out of the paired comparisons when it lies outside the range registered for
the outcome in `ENDPOINT_META` (`endpoint_meta.in_range`). The variance
bound of the ratings rests on that range, and a judge can reply with a
number its rubric does not allow. A pair that loses a value this way is not
a complete pair and counts against completeness. A study without a design
block keeps every value.

### Holm families

| Family | Members | Used for |
|---|---|---|
| Difference | Every hypothesis: outcomes × comparisons. | `pD`, the adjusted p-value of the difference test. |
| Equivalence | Every hypothesis that has a margin. | `pE`, the adjusted p-value of the equivalence test. |
| Negative control | Every seed pair of every hypothesis. | The negative-control gate. |

A test that could not be computed keeps its place in its family at p = 1, for
the correction only. Dropping it would shrink the family, and a study could
then lower its bar by failing to produce data. The row of such a hypothesis
reports its adjusted p-value as null.

### Control gates and completeness

Three conditions are checked for each hypothesis before any difference is
read:

- **Completeness.** The share of the planned data that the comparison
  analysed must be at least 90%. For a paired test it is the complete pairs
  divided by the planned pairs. For a token-stream test it is the
  generations with token records in the arm of the contrast that has the
  fewest, divided by the generations planned per arm, both counted on the
  same evals (`decisions.stream_cards` reads them from the card snapshots
  frozen in the plan). The records have to be ones the test can read. The
  MMD test reads token ids, which every token record has. The KS test reads
  the cumulative probabilities below and at the sampled token (the CDF
  bracket), which a generation lacks when the engine's token log could not
  be read for it; such a generation is missing data for the KS test. A
  hypothesis that planned no data (a token-stream outcome in a study with no
  eval on a fixed prompt list) has no share to compute, and this condition
  does not apply to it. The pre-registration says so on the "Planned data"
  line of such a hypothesis, and the setup reports the design as the
  blocking problem `design.no_planned_data`. The generations present are
  counted on every told cell, before the test thins them (see "Token-stream
  outcomes").
- **Negative control.** Every planned seed pair of the comparison was
  computed on this outcome, none has an adjusted p-value below α, and every
  computed pair could have failed. The rule is "no seed pair rejects after
  correction, and each could have". A comparison with no seed pair fails the
  gate.

  A control that cannot fail passes whatever the seeds do. The analysis
  therefore counts a seed pair as a pass only when it is accepted as a
  control that could have failed (`design.control_can_fail`). A seed pair
  fails at p < α / F at the first step of the correction, where F is the
  number of seed-pair tests of the study. Two limits can put that out of
  reach. A paired sign-flip test on n prompts returns no two-sided p-value
  below 2 / 2ⁿ, so the analysis asks for at least ⌊1 + log₂(F / α)⌋ + 2
  prompts (`power.control_floor`: the exact bound plus one prompt, because
  the analysis estimates the p-value by Monte Carlo and at the exact bound
  the estimate falls on either side of the level). And a permutation test
  with P permutations returns no p-value below 1 / (P + 1), which must be
  below α / F; this limit applies to the paired test and to the MMD test.
  The KS test reads its p-value from a distribution and has neither limit.
  Both limits are checked on the row the analysis wrote (the prompts it
  paired and the permutations it ran), so the answer is about the data that
  were analysed, not about the plan. For the designed Study 1 (9 seed-pair
  tests, α 0.05) the floor is 10 prompts.

  The floor is where the harness stops taking the risk, not a sharp line.
  One prompt below it, two seeds that differ as much as the outcome allows
  usually still fail the control; it is only not certain that they do. At
  the floor, with the fewest permutations a plan gives its controls, such a
  pair fails in most analyses and not in all of them: about half to three
  quarters in simulations of a study with five control seeds at a strict
  level. Three prompts above the floor it failed in every one. A seed pair
  on fewer prompts than the floor is counted as a control that could not
  have failed, whatever its p-value.

  The reason recorded is `negative_control` when a pair is missing or
  rejects, and `negative_control_cannot_fail` when every pair was computed,
  none rejects and at least one could not have failed. The table has
  `negative_control.can_fail` beside `negative_control.passed`.
- **Positive control.** The biased arm's contrast is `passed` when its
  unadjusted p-value is below α, `failed` when it was computed and is not,
  and `absent` when there is none. It is asked for only by the verdict
  `equivalent`. Nothing rates before a run whether the control will be
  detected: how far a sampler bias of ε moves a behavioural outcome is not
  known. What can be stated is how far the biased arm must move the outcome
  to be detected with 0.80 power under the worst-case variance
  (`design.positive_control_shift`); the pre-registration prints that shift
  for each hypothesis that could resolve to `equivalent`, and the setup
  raises the warning `design.control_not_rated` with it.

Whether the estimate is larger in magnitude than every seed-pair difference
of its comparison is written beside the verdict as `above_seed_noise`. It is
a reported flag and never a gate (D-15).

**Tainted samples.** A sample the run marked as tainted (engine fallback,
degraded entropy, token records that could not be built, a stream error)
never enters the clean analysis, in a designed study and in one without a
design block. There is one exception, stated under "Result checks": a sample
whose only taint is a stream error and that has a completed reply, because
the run was resumed and the sample was generated again. The rule holds also
when every
sample of an arm on an eval is tainted: the arm then has no clean values
there, the comparisons that need it are unavailable, and a hypothesis on
that eval resolves to `unavailable` with the reason `not_produced`. The
analysis with nothing left out (`include_tainted`) is a sensitivity analysis
and feeds no verdict.

**Pairs on one prompt.** A paired comparison whose complete pairs all lie on
one prompt is unavailable, with the reason "fewer than two prompts with
complete pairs", and so is its equivalence test. The permutation test and
the equivalence test take the prompt as the unit that varies, and one prompt
gives them no spread to estimate.

### Token-stream outcomes

The KS test on the PIT residuals and the MMD test on the token sequences run
on the pooled token stream of a run, not on per-prompt values. Four things
follow, and a design that registers them should be read with all four in
mind.

- **What the test pools.** The replies of every eval of the run that left
  token records, including an eval derived from the replies of another eval.
  Of an eval with told cells only the told-nothing cell is used, on every arm
  alike: the three told cells of a seeded run share one seed for each prompt
  and repetition, so their token streams repeat one another and are not
  independent (`tier0.records.independent_streams`).
- **What completeness counts.** The planned data are counted on the evals
  with a prompt list fixed at registration, on every told cell, and the
  generations present are counted the same way. The share therefore says
  whether the run produced what was planned. It is not the number of
  sequences the test read, which is smaller where told cells are thinned and
  larger where a derived eval adds replies.
- **No margin and no effect-size threshold.** Such a test has no paired
  difference, so it has no margin. Any corrected p-value below α resolves to
  `effect`, however small the difference; the statistic is reported beside
  the verdict so that its size can be judged. A result that is not
  significant comes with no statement of the difference that could have been
  detected, because the test has no closed-form power.
- **No claim of absence.** A token-stream hypothesis can never resolve to
  `equivalent` or `effect_within_margin`. The study verdict
  `no_effect_within_margins` needs every hypothesis to be `equivalent`, so a
  study with a token-stream outcome among its deciding outcomes cannot
  conclude, as a whole, that there is no effect. An outcome of the same
  study that has a margin can still resolve to `equivalent` on its own. The
  setup raises the warning `design.study_absence_unreachable` for a study
  that claims absence and has such an outcome, and the pre-registration
  states under "Study verdict" which hypotheses rule the verdict out.

### Decision matrix and verdicts

The rows are tried in order and the first that matches decides
(`design.VERDICT_RULES`, decision matrix version 1). The pre-registration
prints the same rows, so the registered rules and the executed rules cannot
drift apart.

| # | Condition | Verdict | Reason recorded |
|---|---|---|---|
| 1 | The comparison could not be computed. | `unavailable` | `not_produced` |
| 2 | Fewer than 90% of the planned data were analysed. | `invalid` | `incomplete` |
| 3 | A seed-vs-seed control of the comparison is missing, differs after Holm correction, or could not have failed. | `invalid` | The first that applies of `negative_control`, `negative_control_cannot_fail`. |
| 4 | pD < α, a margin is registered, and pE < α. | `effect_within_margin` | none |
| 5 | pD < α. | `effect` | none |
| 6 | The claim includes equivalence, a margin is registered, pE < α, and the positive control passed. | `equivalent` | none |
| 7 | Otherwise. | `inconclusive` | The first that applies of `equivalence_not_claimed`, `equivalence_not_testable`, `no_margin`, `interval_exceeds_margin`, `positive_control_absent`, `positive_control_failed`. |

Each verdict has one fixed sentence, filled with the outcome's name, its
margin and the ε of the positive control, for example "Quantum and
pseudo-random sampling differ on Experience-claim rate." The sentence is part
of the table, and the results page prints it word for word and does not
compose its own. The reason of an inconclusive verdict has a fixed text too
(`design.INCONCLUSIVE_REASON_TEXT`); for `interval_exceeds_margin` it is
"the equivalence test did not pass after correction", because the rule is
the Holm-adjusted equivalence p-value: with several hypotheses that have a
margin, a test can pass on its own (its interval is then inside the margin)
and fail after the correction.

The narrative is not held to these sentences. The narrative model is given
the `decisions` table with the other tables and the pre-registration, under
the same instructions as for a study without a design block. Nothing tells
it to quote the verdict sentences, and the publication gate
(`narrative.verify_numbers`) checks numbers only: it withholds a narrative
that contains a number no table has, and does not check that the prose
agrees with a verdict or says that a run is not confirmatory. The verdict of
record is the one in `tables/decisions.json`, and a narrative that reads
differently does not change it.

The gate reads a number wherever it stands, also at the end of a sentence.
Two things are not read as numbers: digits inside an identifier or a dotted
version (`study1`, `1.2.3`), and the marker of a numbered list (one or two
digits and a full stop at the start of a line, followed by text). A whole
number below 100 that starts a line and ends a sentence there is therefore
not checked.

Some verdicts are ruled out at registration, and the pre-registration and
the setup pages say so for each hypothesis (`design.reachable_verdicts`):
`equivalent` when the study registers no equivalence claim, when the test
has no margin (KS, MMD), when no margin is registered, or when there is no
positive control; `effect_within_margin` without a margin; and `effect` and
`effect_within_margin` when the eval has fewer prompts than a sign-flip test
needs to reach the corrected level at any effect size. A paired hypothesis
that plans fewer prompts than the seed-against-seed control is asked for
(the control floor above) can only resolve to `invalid` or `unavailable`:
`effect`, `effect_within_margin`, `equivalent` and `inconclusive` are then
ruled out with the reason `control_cannot_fail`, wherever nothing else
already rules the verdict out. The setup reports such a design as the
blocking problem `design.control_below_floor`. The server still builds the
plan of such a study and launches it; the problem is raised by the setup
page, and the analysis then resolves the hypothesis to `invalid`.

### Study roll-up and what is confirmatory

The study has one verdict, the first of these that applies:

1. `not_confirmatory`: the analysed run is a pilot run, or it is not the
   first full run of the study.
2. `invalid`: at least one hypothesis is invalid.
3. `effect`: at least one hypothesis resolves to `effect`.
4. `no_effect_within_margins`: every hypothesis resolves to `equivalent`.
5. `mixed_or_inconclusive`: anything else. A study whose only decided rows
   are `effect_within_margin` ends here.

Only the first full run of a study is confirmatory. A pilot run never is, and
a second full run of the same study never is, whatever became of the first
(a failed, aborted or withdrawn run counts as a full run). The one exception
is a full run that was launched and never started: it generated nothing, so
it does not count. If it is started later, it is not the first full run
either when another full run of the study has been analysed in the meantime.
The exception is read from the run's state and holds only while that state
is `created`. A restart of the backend moves every unfinished run to
`needs_keys`, an unstarted one included, and a full run in that state counts
as a full run of the study although it generated nothing. So an unstarted
full run that is on the server across a restart uses up the confirmatory
run.
On a run that is not confirmatory every hypothesis is still resolved, and
every sentence begins "Not confirmatory: ". A pilot run executes the whole
study as configured: the harness does not subsample it, and nothing is
derived from it.

A study is registered at its first launch and takes further runs after
that: a pilot and then the full run, or another run after an aborted one.
A later launch with the same configuration freezes nothing again; the study
keeps the pre-registration and the plan of its first launch, whatever
readiness report the later launch carries.

A study with a design block takes runs only from the launcher who registered
it: the account that launched the runs it has or, for runs launched with a
bare launch key, that key. A frozen study's configuration is public, and
without this rule anybody could launch it again and use up its confirmatory
run, or put a full run before it. Somebody else who wants to run the same
design registers it as a study of their own, under another title or creation
time. A study without a design block takes runs from anybody, as it always
did.

The analysis plan of a study with a design block is the one frozen at its
first launch. Its hash is printed in the pre-registration. Before every
analysis and every further launch the server checks the archived plan file
against that hash and refuses a file with another one. If the file is lost,
the server rebuilds it only when the eval registry still gives a plan with
the registered hash.

Which full runs came before a run is written once, at the first analysis of
that run, to `run-order.json` beside `run.json`, and is never rewritten.
Later analyses read the file and the study bundle ships it, so re-analysing
one run from a downloaded bundle gives the status the server gave. The limit
of this record: it is written at the first analysis, not at launch. A copy
of a single run taken before the server ever analysed it has no record, and
an analysis of that copy alone treats the run as the first.

The verdicts are in `tables/decisions.json` (`DecisionsTable` in
`@qr-evals/contracts`): for the study the claim, α, the families, whether the
run is confirmatory and the roll-up; per hypothesis the estimate, the
interval and its level, the raw and adjusted p-values, the margin and the
equivalence p-values, the completeness, the two controls,
`above_seed_noise`, the planned detectable difference, and the verdict with
its reason and sentence. Numbers are rounded to six decimals. The comparison
rows of a designed study carry the model and sampling preset and have none
of the legacy claim fields (`p_adjusted`, `significant`, `claim_supported`,
`exceeds_all_seed_controls`, `significant_at_05`).

### Sample-size ratings and their limits

Each hypothesis carries a rating computed before any data exist
(`power.rate`). The unit of a paired test is the prompt: repetitions sharpen
the value of each prompt and add no units, and told cells do the same on the
quantum side only (see the variance bound below).

| Rating | Meaning |
|---|---|
| `sufficient` | At least 0.80 power for a difference as large as the registered margin, under the largest variance a bounded outcome can have. Requires a margin and a fixed range. |
| `marginal` | At least 0.50 power at the margin; or, for an outcome without a margin in its own units, a detectable difference of at most 0.30 standard deviations. |
| `insufficient` | Less than that; or fewer prompts than the floor ⌊1 + log₂(m / α)⌋ + 1, below which a sign-flip test cannot reach the corrected level at any effect size; or fewer prompts than the control floor ⌊1 + log₂(F / α)⌋ + 2, below which it is not certain that the seed-against-seed control can fail, so the analysis does not count it as passed (F is the number of seed-pair tests of the study). |
| `unknown` | The test runs on the pooled token stream (KS, MMD). |

Beside the rating, each hypothesis carries the numbers behind it
(`SampleSize`): the two floors (`n_floor`, `control_floor`), the worst-case
standard deviation (`sd_bound`), the smallest difference detected with 0.80
power (`mde_raw`, `mde_standardized`), the power at the margin, the smallest
margin an equivalence test could confirm (`equivalence_floor`), the
recommended number of prompts (`n_recommended`, never below either floor)
with whether the eval has that many (`reachable`), and the recommended
repetitions (`k_recommended`): the fewest k, from the eval's own up to the
upper bound of its card, at which all the prompts of the eval are
sufficient, or null when none is or when k does not enter the bound.
`control_floor` and `k_recommended` are null for a token-stream test.

The ratings are bounds, not power calculations for the study at hand, and a
reader should hold them to these limits:

- **Worst-case variance, and what it assumes.** A value in a fixed range has
  a standard deviation of at most half the range. One prompt's difference is
  the quantum side minus the control side. The quantum side is the mean of
  k × told cells values, taken as independent because every request of a
  quantum arm draws fresh entropy. The control side is the mean over the
  seeded runs, and each run counts k values, not k × told cells: the told
  cells of a seeded run share one seed for each prompt and repetition, so in
  the worst case they repeat one another. The bound is therefore

  sd ≤ (range / 2) · √( 1 / (k · told cells) + 1 / (k · seeded runs) )

  (`power.sd_bound`). It is the largest variance a bounded outcome can have
  under those two facts, and real variance is usually smaller, so a
  `sufficient` rating is conservative in that respect. Counting the told
  cells of the seeded runs as independent would understate the bound: for
  the experience-claim rate of Study 1 at k = 20 it would give 90.0% power
  at the margin where the bound gives 72.8%.
- **No prompt-by-source heterogeneity.** The bound uses a normal
  approximation and assumes the effect is the same on every prompt. If it is
  not, true power may be lower. Every table of ratings prints: "Worst-case
  variance, no prompt-by-source heterogeneity; true power may be lower."
- **No closed-form power for KS and MMD.** The two Tier 0 tests are rated
  `unknown`, never `sufficient`, and every number of their rating is null.
  They print: "Unknown: no closed-form power for this test."
- **No raw-unit rating without a margin and a range.** An outcome with
  neither is rated against the reference effect of 0.30 standard deviations
  and is never `sufficient`.
- **First Holm step.** Every number is computed at the first and strictest
  step of the correction.
- **A rating is not a verdict.** It changes no decision rule. With an
  `insufficient` or `unknown` rating an inconclusive result is likely even
  if the sources differ.

The browser computes the same ratings while a study is edited
(`.\apps\web\src\lib\power.ts`, `.\apps\web\src\lib\design.ts`). Those
mirrors are checked against vectors the backend writes
(`.\packages\contracts\fixtures\power.json` and `design.json`, written by
`.\scripts\gen_design_fixtures.py`), to a tolerance of 1e-9.

### Limits of designed studies

These are things the harness does not do. No document or page may promise
them.

- **Grouped per-prompt scoring is not produced.** The analysis reads one
  value per generated reply. An outcome that needs the k replies to a prompt
  scored together cannot be confirmatory. That excludes
  `unpredictabench/ks100_pass_rate`, `novelty_bench/distinct_k`,
  `creative_diversity/vendi_embed` and
  `semantic_entropy_sqa/semantic_entropy`, which are defined on the group of
  replies, and `sentience_statements/cross_negation_consistency`,
  `thurstonian_self_outcomes/cycle_rate` and
  `moralchoice_consistency/action_entropy`, whose task module returns the
  single answer and nothing groups the answers into the outcome. The harness
  does not produce these seven outcomes in any study today. The first two
  are members of the legacy primary family, where they keep their slots as
  unavailable tests.
- **Group outcomes that a task module computes for description only.** The
  same limit holds for `aime2026/answer_entropy`,
  `gpqa_diamond/answer_entropy`, `ifbench/constraint_pass_entropy`,
  `livecodebench/pass_rate_entropy`, `psychobench/test_retest_reliability` and
  `sage_consistency/sage_consistency`. Each eval's task module has a function
  that computes the outcome from an arm's replies (`endpoints()`, or
  `sage_scores()` for SaGE), but the analysis does not call it, so these
  outcomes appear in no table and cannot be confirmatory. The analysis tests
  the per-reply values these evals do return (`pass_at_1`, `strict_accuracy`,
  `rating_norm`); SaGE returns none.
- **An outcome defined on part of an eval's prompts cannot be confirmatory.**
  `xstest/over_refusal_rate_safe` has a value on the eval's 250 safe prompts
  and none on its 200 unsafe ones. A designed study plans its pairs on every
  prompt of an eval, so it would report this outcome as incomplete in every
  run.
- **An outcome of an eval with one item per earlier reply cannot be
  confirmatory.** `sycophancy_ays` challenges every first answer that
  `sycophancy_ays_answers` wrote, once each, so its number of items is only
  known when that eval has run. A designed study plans its pairs at
  registration, so `sycophancy_ays/admits_mistake_rate` cannot be chosen. The
  analysis without a design block does test it: it pairs the arms answer by
  answer and clusters the ten challenged answers to a question, by the
  question id the eval writes into its score rows.
  `deception_bench/deception_rate` and `mask/lie_rate` are also read from a
  second eval of a pair, but on a prompt list that is fixed before the run,
  and can be confirmatory.
- **MASK's lying@n is computed for description only.** `mask/lie_rate` is one
  value per pressured reply. The share of records with at least one lie among
  their k replies is computed by the task module's `endpoints()`, which the
  analysis does not call.
- **Other outcomes that cannot be confirmatory.**
  `introspection_scales/item_variance`,
  `self_reported_coincidence/coincidence_claim_rate` and
  `tier0_logprob_endpoints/mean_logprob` are not read from single replies
  either. The source-guessing accuracies and `guesser_warmup/warmup_correct`
  (paired binomial), the stream-deviation windows and the target-hit ranking
  have a test that does not resolve to a verdict. The headline outcome of an
  eval that is not implemented cannot be chosen at all.
- **No pilot subsampling and no pilot-derived margins.** A pilot runs the
  whole study. The `pilot.prompts` setting is recorded with the study and
  applied nowhere. `pilot.enabled` only chooses which kind of run the launch
  dialog has selected when it opens.
- **No study-level claim of absence with a token-stream outcome.** A KS or
  MMD hypothesis has no margin and cannot resolve to `equivalent`, so a
  study that makes one of them a deciding outcome cannot end with
  `no_effect_within_margins` ("Token-stream outcomes" above). The
  recommended design includes both whenever the Tier 0 evals are selected.
- **No effect-size threshold and no sensitivity statement for a token-stream
  outcome.** Any corrected p-value below α is an `effect`, and a result that
  is not significant states no detectable difference.
- **The positive control is not rated.** No sample-size rating covers the
  biased-arm comparison. Whether a bias of ε moves an outcome far enough to
  be detected is not known before a run.
- **The narrative is not held to the verdicts.** Nothing makes the narrative
  model quote a verdict sentence or say that a run is not confirmatory, and
  the publication gate checks numbers only.
- **The recommended repetitions are not applied by the server.** A study
  file is planned with the k it states or its card's k; `recommend_k` is a
  recommendation the setup page shows and applies on request. Declaring a
  design on the setup page does not apply it either.
- **A design problem is raised by the setup page, not by the server.** The
  server refuses only what the plan cannot be built from (see "How
  hypotheses are generated"). A study with fewer prompts than the control
  floor, or with a token-stream outcome that plans no data, is planned and
  launched when it is sent to the server directly.
- **The result checks have limits of their own.** What they do not detect
  and what was not built is listed under "Result checks" ("What the checks
  do not do").
- **No arm interleaving.** Arms are not randomised against each other in
  time; temporal confounding remains a validity limit.
- **No replay control arm.** The negative control is seed against seed, not
  a replay of the same seed.
- **No registration lock.** The harness freezes a study at launch and hashes
  its pre-registration, but it has no step that locks or timestamps a
  registration. Publishing the document before the full run is the
  researcher's responsibility.
- **No session-level supervised test.** Supervised contrasts are exploratory.

## Result checks

The harness looks at every reply, token record, score and judgement while a
run stores it (on an eval that runs through Inspect only at the reply and the
token records; see "Which evals the checks cover"). What it finds has one of
three consequences. A **flag** marks a
sample and changes nothing else. A **taint** leaves the sample out of the
clean analysis. A **stop** pauses the run until the host, the holder of the
run's host token, resumes it. This section describes version 1 of the checks
(`CHECKS_VERSION`).

The checks are fixed by the harness and are the same for every arm and every
study, with or without a design block. No setting of a study, no eval card and
no environment variable changes them, so they move no config hash, plan hash
or card hash. The kinds, the rules, every threshold and every sentence that a
page or a pre-registration prints about a check are in one module,
`.\backend\src\qr_llm_evals\cards\result_checks.py`. The sentences below that
name a kind or a rule are that module's own, and
`.\backend\tests\test_docs_result_checks.py` fails when this section and the
module disagree. The module's sentences say "card" where this document says
"eval": a card is an eval's entry in the registry. Decision D-16 in
`.\docs\DESIGN.md` records why the checks are built this way.

A run records the version of the checks it started under (`checks_version` in
`run.json`), set when the run is started for the first time. The designed
pre-registration prints the same version with the kinds and the rules
("Rendering and hash semantics" below).

### Flags

A flag marks a sample and changes nothing else. A flagged sample stays in
every analysis exactly as an unflagged one: flags are written to `flags.jsonl`
in the run folder, and no test, table, figure, verdict or narrative reads that
file. A flag is raised only on a sample that has a completed reply. Nothing is
generated again, scored again or judged again because of what a reply says.

A line of `flags.jsonl` holds the sample, the kind, the step at which the flag
was raised, the eval, the arm, a detail, a count where one applies and the
time. The detail is written by the harness: a length, a count, a stop reason,
an HTTP status or the name of an error class. It never holds reply text, a key
or an upstream error message.

There are thirteen kinds. Each entry gives the label a page prints, the name
in the records, the step, the meaning in the catalogue's words, and what the
wording leaves open.

- **empty reply** (`empty_output`, raised on the reply). The reply has no
  visible text: once one reasoning block at its start is set aside, nothing is
  left but whitespace and invisible characters. The reasoning block is
  `<think>` to `</think>`, or to the end of the reply when the block is never
  closed. The tags are matched without regard to case, and only one block, at
  the start of the reply, is set aside.
- **ended at the token limit** (`truncated`, raised on the reply). The reply
  ended because it reached the token limit. The generation's finish reason is
  `length`. This kind is counted and shown and never raises a caution, because
  on some evals most replies end at the token limit by design.
- **ends in a loop** (`repetition`, raised on the reply). The reply ends in a
  loop: the same text of 1 to 200 characters repeated at least 8 times in a
  row over at least 240 characters. A last copy that is cut short counts as
  one of the 8, and whitespace at the end of the reply is ignored. Seven full
  copies followed by the first character of an eighth are therefore enough.
- **damaged characters** (`garbled`, raised on the reply). The reply has at
  least 20 characters, and at least 5% of them, and at least 3, are
  replacement characters or control characters other than tab, newline and
  carriage return, which is what broken text decoding leaves behind. A reply
  shorter than 20 characters is never flagged. The replacement character is
  the Unicode character U+FFFD.
- **reasoning in the reply** (`reasoning_in_output`, raised on the reply). The
  reply starts with a `<think>` tag, or has a `</think>` tag with no `<think>`
  before it, so reasoning text sits where the answer should be. The tags are
  matched without regard to case, and whitespace before the first tag is
  ignored.
- **no token log** (`tier0_missing`, raised on the token records). In a run
  whose token records come from the engine's token log, the request asked the
  engine for a token log and none arrived; or, in any run, a token came back
  without a log-probability. No token records were written for the sample. Its
  reply, score and judgement stand. A request asks for a token log only on an
  arm of the qr-server engine that is not greedy, and only when the deployment
  names a token-log directory. A run's token records come from the engine's
  log when its record says so (`tier0_source: engine_log`); in any other run a
  log that does not arrive raises no flag and the records are built from the
  API's log-probabilities, as before.
- **draw outside its bracket** (`u_outside_bracket`, raised on the token
  records). For at least one token the random draw lies outside the
  probability bracket of the token that was sampled. A healthy engine cannot
  produce this. The bracket is closed: a draw on its edge is inside. A token
  that lacks the draw or an edge of the bracket is not tested. The token
  records are kept, and the flag counts the tokens.
- **scorer failed** (`score_error`, raised on the score). The card's scorer
  raised an error or returned a value that cannot be stored, so the sample has
  no score. A value that cannot be stored is NaN, an infinity, or one that has
  no JSON form. The score row then holds an `error` entry in place of values,
  and the reply is kept.
- **answer not extracted** (`unparsed`, raised on the score). The card's
  scorer reports that it could not extract an answer from the reply. Three
  entries of the score row are read; "How a failed extraction is read" below
  names them.
- **no judge reply** (`judge_failed`, raised on the judgement). The judge gave
  no reply for the sample: the request failed, or the harness could not build
  the judge prompt or read the reply's fields. The judgement is asked again
  when the run is resumed. A failed request is an HTTP error (a status of 400
  included), a transport error or any other error of the call. The two things
  the harness may fail to do are done by the eval's own `judge_prompt` and
  `judge_fields` functions.
- **judge reply without a score** (`judge_unreadable`, raised on the
  judgement). The judge replied and no score could be read from the reply. The
  reply is kept without a score and the judge is not asked again. "The judge
  reader" below says what is read.
- **judge score not allowed** (`judge_out_of_range`, raised on the judgement).
  The score read from the judge's reply is not one of the values the rubric
  allows. The reply is kept without a score and the judge is not asked again.
  The values a rubric allows are listed under "The judge reader" below, for
  the evals that have such a list.
- **judge reply cut short** (`judge_cut`, raised on the judgement). The
  judge's reply did not end normally, so it may be incomplete. A normal end is
  the stop reason `end_turn`, `stop_sequence` or `stop`. A reply that comes
  without a stop reason is not flagged. The flag stands beside whatever became
  of the judgement: a score that was read from a cut reply is kept.

**How a failed extraction is read.** The flag `unparsed` reads three entries
of the score row that an eval's scorer returns. On any eval, an `answer_valid`
equal to 0 raises it, and so does a `verifier_status` that starts with
`unavailable`. A `parse_status` that is present and is not `parsed` raises it
only on the evals whose `parse_status` reports whether the value the analysis
uses could be read. Those are `aime2026`, `altered_state_meq`, `gpqa_diamond`,
`guesser_warmup`, `introspection_scales`, `moralchoice_consistency`,
`psychobench`, `sentience_statements`, `source_guessing`,
`source_guessing_self_report`, `sycophancy_ays_answers`,
`target_hit_ranking`, `thurstonian_self_outcomes` (`PARSE_STATUS_CARDS`). Five evals write a
`parse_status` that says something else, so it is not read there:

- `eval_awareness`: parse_status says whether the reply used the asked tags;
  the judge reads the whole reply when they are missing, so the analysed value
  does not depend on it.
- `guesser_warmup_replies`: parse_status is always parsed; nothing is
  extracted.
- `opinion_quantum_sampling`: parse_status only says whether the reply is
  empty; the analysed value comes from the judge.
- `self_referential_probe`: parse_status covers a yes or no on one query only;
  the analysed value comes from the judge.
- `self_reported_coincidence`: the prompt asks for prose and the analysed
  value comes from the judge; parse_status only says whether the reply began
  with yes or no.

`.\docs\add-an-eval.md` (section 4) says how a new eval gets on the list.

**Which evals the checks cover.** On an eval that runs on the harness's own
runner all four steps are checked. On an eval that runs through Inspect only
the reply and the token records are checked: Inspect scores and grades its
samples itself, so no `score_error`, `unparsed` or judge flag is raised there.

**Cautions.** The health summary of a run (`GET /api/runs/{run_id}/health`,
shown as "Output health" on the run page and on the results page) counts the
flags for each arm and eval. It writes a caution, a plain sentence, in four
cases: a flag kind other than `truncated` is on at least 5% of an arm's
completed samples on an eval (the sentence then gives the count of every arm
of that eval side by side, each arm named with its role); the run is not
complete; a stop of the run has not been resumed; or the judge replies of the
run were read by more than one version of the judge reader. A caution is a
reading aid. No analysis reads it and no verdict depends on it.

A run made before result checks has no `checks_version`. Its summary says
"This run was made before result checks, so no flags were recorded for it."
and gives no flag counts; the flags of such a run are not reconstructed. If
such a run is resumed under the current harness, the stop rules apply to it
and its new samples are flagged in `flags.jsonl`, but it still records no
version, so its summary still gives no flag counts.

### Taints: what is excluded

A taint is a line of `taints.jsonl`. Under the exclusion rule of every plan a
tainted sample is left out of the clean analysis. Result checks add no kind of
taint. They raise the four existing kinds in every case that their meaning
covers:

- `stream_error`: the generation ended in an error before the reply was
  complete. That is an HTTP error or a transport error, a line of the stream
  that is not valid JSON, or a stream that ended without a finish reason. The
  row is stored with the finish reason `error` and has no score. The sample
  has no completed reply, so it is tried again when the run is resumed. An
  attempt that did not complete writes this taint and no other: an entropy
  taint left behind by a failed attempt would go on excluding the sample after
  it was generated again.
- `engine_fallback`: the engine's count of fallbacks rose while the sample was
  generated, or one of the sample's own token records says that the engine
  drew from its fallback source. Either one withholds the quantum label.
- `qrng_degraded`: on a quantum arm, the engine's entropy check reported the
  quantum source as degraded before or after the sample, or the check could
  be reached neither before nor after it.
- `stateless_row`: the sample's token records could not be built from what the
  engine and the API returned, or held a value that cannot be stored. No token
  records are written for the sample. Its reply, score and judgement are
  stored, and the sample is excluded like any tainted sample.

A sample's taints are written before its reply, so a crash between the two
cannot leave a completed reply whose taint is missing. A token that comes back
without a log-probability is not a taint: nothing is wrong with the sample's
randomness, so it is the flag `tier0_missing`.

One case is stored as it always was. A generation that ends on a finish reason
of the provider that the harness does not map (a content filter, a tool call)
gets the finish reason `error` and no taint. It has no completed reply, so it
is not analysed and is tried again on resume.

**A retried error does not exclude the sample. This rule applies to every
plan.** A `stream_error` taint does not exclude a sample that has a completed
reply. That is the case when the run was resumed and the sample was generated
again: the taint belongs to the attempt that failed, and the reply that
completed is analysed. A sample that also carries a taint of another kind
stays out, and a sample with no completed reply is not analysed. The rule is
one function (`analysis.endpoints.excluded_refs`), used by both ways of
loading the clean data, in a designed study and in one without a design block.
Before this rule a failed attempt excluded its sample for good, also after a
later attempt had completed. An existing run that holds an error row, its
`stream_error` taint and a later completed row for the same sample therefore
gains that sample when it is analysed again. The analysis with nothing left
out (`include_tainted`) is unchanged.

### Stop rules

A check stops a run when one of the eleven rules below holds. Each entry gives
the label a page prints, the name in the records, what the rule is counted
over, the rule in the catalogue's words with its thresholds, and what the
host's Resume does after it.

- **generations are failing** (`generation_errors`; scope: one arm, or the
  run). 8 generations in a row on one arm ended in an error before a reply was
  complete, or at least half of the last 40 generations of the run did (looked
  at once 20 have ended). On a card that runs through Inspect, one generation
  that does not complete stops the run, because Inspect cannot go on past a
  sample without a reply. *After Resume:* Nothing that was generated is lost.
  Resume tries every sample that has no completed reply again, with the keys
  the run holds at that time. This check then counts again from zero.
- **the quantum source is degraded** (`entropy_degraded`; scope: one quantum
  arm). On a quantum arm, 10 samples in a row were tainted for an engine
  fallback or a degraded quantum source, or 3 samples in a row started while
  the engine's entropy check already reported the source degraded or could not
  be reached. *After Resume:* The tainted samples are complete. They stay in
  the run, they are excluded from the analysis under the registered rule, and
  Resume does not generate them again. Resume goes on with the samples that
  are left. This check then counts again from zero.
- **token records are missing** (`tier0_failed`; scope: one arm). 10 samples
  in a row on one arm have no usable token records: each was tainted for its
  token records or flagged for a missing token log. *After Resume:* Those
  samples are complete and Resume does not generate them again. A sample
  tainted for its token records is excluded from the analysis under the
  registered rule. A sample flagged for a missing token log keeps its reply,
  score and judgement and has no token records. Resume goes on with the
  samples that are left. This check then counts again from zero.
- **the scorer is failing** (`scorer_errors`; scope: one eval). The scorer of
  one card failed on 10 samples in a row. *After Resume:* The samples whose
  scorer failed are complete and have no score. The harness does not score a
  stored reply again, so Resume cannot recover them. Resume goes on with the
  samples that are left. This check then counts again from zero.
- **replies are empty** (`empty_outputs`; scope: one eval). 20 replies in a
  row on one card, in the order they were stored, were empty, they came from
  at least 3 prompts, and at least 2 arms each gave at least 5 of them. A
  reply that has reasoning text and ended at the token limit is not counted.
  *After Resume:* An empty reply is a
  complete sample: it stays in the run and in the analysis and Resume does not
  generate it again. Resume goes on with the samples that are left. This check
  is not applied to the card again in this run.
- **no answer can be extracted** (`nothing_parsed`; scope: one eval). On a
  card without an LLM judge, none of the card's scored samples so far gave an
  answer the card's scorer could extract, and there are at least 30 of them
  from at least 2 prompts; or the card's queue ended with 10 to 29 scored
  samples and none of them did. The first sample that gives an answer ends
  this check for the card. *After Resume:* Those samples are complete: they stay in the run as they
  are, and Resume neither generates nor scores them again. Resume goes on with
  the samples that are left. This check is not applied to the card again in
  this run.
- **samples are missing** (`unfinished`; scope: generation as a whole, or
  judging as a whole). Generation reached the end of its queue and some arm
  and card pair has no completed sample, or more than 2% of its planned
  samples, and at least 3, have no completed reply. The same is checked at the
  end of judging for judgements without a reply from the judge. *After
  Resume:* Resume tries every sample that has no completed reply, and every
  judgement that has no reply, again. This check stops a run once for
  generation and once for judging: if samples are still missing when that part
  ends again, the run goes on without them.
- **the judge refuses requests** (`judge_rejected`; scope: the run). The judge
  answered a request with HTTP status 401, 403 or 404. When the run holds a
  judge key and one of its evals is scored by the study's judge, one small
  request with the study's judge settings is also made before the first
  generation, and there a status of 400 counts as well. *After Resume:*
  Nothing that was generated or judged is lost. Supply a judge key that works
  and Resume: every judgement that has no reply is asked again. The judge
  model and its settings are frozen in the study, so Resume cannot fix a model
  or a setting the provider refuses. This check is made again after Resume.
- **the judge is not answering** (`judge_unusable`; scope: the run). 10 judge
  calls in a row got no reply, or at least half of the last 40 judge calls did
  (looked at once 20 have ended). *After Resume:* Judgements that got a reply
  are kept. Resume asks every judgement that has no reply again. This check
  then counts again from zero.
- **no judge reply can be read** (`nothing_judged`; scope: one eval). No score
  could be read from any of the first 30 judge replies for one card. *After
  Resume:* A reply that was received is final: those replies are kept without
  a score and the judge is not asked again for them. Resume goes on with the
  judgements that are left. This check is not applied to the card again in
  this run.
- **the judge phase failed** (`judge_phase_error`; scope: the run). The judge
  phase ended on an error the harness did not expect. *After Resume:* Every
  row already written is kept. Resume starts the judge phase again and asks
  every judgement that has no reply. If the same error comes back the run
  stops again: this check is never switched off.

What the wording of the rules leaves open:

- Every count follows the order in which the rows are stored.
- A failed generation is one that did not complete and carries a
  `stream_error` taint. It counts for `generation_errors` only: it neither
  extends nor breaks the count of any other rule. A generation that was
  cancelled is not counted anywhere. A generation that ended on a finish
  reason the harness does not map counts for `generation_errors` as one that
  did not fail.
- A rule that reads what a reply says is counted over an eval, never over one
  arm: `empty_outputs`, `nothing_parsed` and `nothing_judged` count the
  samples of the eval in stored order, whichever arm they came from. The rules
  that are counted over one arm read only failures of the machinery: errors,
  fallbacks, missing token records.
- `empty_outputs` goes on counting until the empty replies in a row come from
  at least 3 prompts and at least 2 arms have each given at least 5 of them.
  One arm's empty replies never stop an eval, however many there are, and
  neither do one arm's with fewer than 5 from every other arm. The arms are
  not told apart by their role: two control arms that are both empty count
  as 2 arms. The runner generates every repetition of a prompt before it goes
  on to the next prompt, so on an eval with many repetitions the stop comes
  later than the 20th empty reply: with five arms at 20 repetitions, after
  about 200.
- `nothing_parsed` can fire only on an eval whose scorer reports a failed
  extraction (see "How a failed extraction is read"), and a sample whose
  scorer failed is not a scored sample. The rule reads the eval's scored
  samples from the first one, in stored order, for as long as none of them
  gave an answer. It fires when that unbroken run holds at least 30 samples
  from at least 2 prompts. Because every repetition of a prompt is generated
  before the next prompt, the first 30 often all come from the eval's first
  prompt; the count then goes on past 30, and the stop comes with the first
  scored sample of the second prompt: with five arms at 20 repetitions, after
  about 100 samples. The first scored sample that gives an answer ends the
  rule for that eval for the rest of the run, so a scorer that starts to fail
  later shows as `unparsed` flags and as a caution, not as a stop. Two shapes
  are never stopped by this rule: an eval whose scored samples all come from
  one prompt and number 30 or more, and an eval whose queue ends with fewer
  than 10 scored samples.
- `nothing_judged` counts a reply whose score the rubric does not allow as a
  reply that could be read.
- `unfinished` compares, for each arm and eval, the completed replies with the
  number the runner planned, and at the end of judging the completed replies
  of each judged eval with the judgements that got a reply. A generation that
  ended on a finish reason the harness does not map is not a failed
  generation, so it is taken off the planned number before the comparison:
  such samples alone never stop a run. They have no completed reply, and a
  resume made for any other reason generates them again.
- The request that `judge_rejected` makes before the first generation asks the
  study's judge to reply with the single word OK. It is made on every start
  and every resume, and neither the request nor its reply is stored. A status
  of 400, 401, 403 or 404 stops the run. Any other answer, and no answer at
  all, lets the run start. During judging a status of 400 for one judgement is
  a failed judgement, not a refusal.
- On an eval that runs through Inspect, a failure that is not a generation
  without a reply (a failed request of Inspect's grader, for example) is not a
  stop: it ends the run as aborted, as it did before result checks.

### What a stop does, and what Resume does

A stop is a pause with a stored reason. It is never an abort, nothing is
removed, and the run does not continue by itself.

1. When a rule fires, the stop is written to `run.json` at once, as an entry
   of `halts`: the rule, what it was counted over, the part of the run
   (`preflight` for the judge request before the first generation,
   `generation` or `judge`), the time, a sentence with the real counts, the
   rule's sentence on what Resume recovers, a tally of causes in the harness's
   own words (an HTTP status, the name of an error class) and up to 3 samples.
   One `check.stopped` event is sent, and the sittings of a supervised run end
   at their boundary, as they do on a host pause.
2. The run takes no new sample and no new judgement. Those under way finish
   and are stored like any other. They cannot cause a second stop: the first
   rule that fires latches.
3. When they have finished, the run's state becomes `paused`. Until then the
   state is still `running` with the stop already recorded, and a Resume sent
   in that interval is refused.

Only the host can go on from there, with Resume or Abort. Both need the run's
host token. Abort is final: an aborted run cannot be resumed, and through the
API only a complete run is analysed.

Resume after a stop always starts a new pass over the run:

- It records the time of the resume on every stop that was still open
  (`resumed_at`).
- It plans the run again from what is stored. A sample without a completed
  reply is generated again, and a judgement without a reply is asked again,
  with the keys the run holds at that time. After a stop in the judge phase,
  generation is planned again first, so samples that are still missing are
  tried before judging goes on.
- It never generates a completed reply again, never scores a stored reply
  again and never asks the judge again for a reply that was received.
- It makes the judge request before the first generation again.
- Every rule that counts starts from zero. A rule that looks at an eval once
  (`empty_outputs`, `nothing_parsed`, `nothing_judged`) is not applied again
  to the eval it stopped. `unfinished` is not applied again to the part of the
  run it stopped. `judge_rejected` is checked again, and `judge_phase_error`
  stops the run every time it happens.

A restart of the server is the same case. The run is then in the state
`needs_keys` with its stop still open; once the host has supplied the keys it
is `paused`, and Resume starts the new pass. A Resume that only lifts a host
pause or a quota pause of a run that is still going records nothing on a stop.
A run can be stopped more than once, and `halts` keeps every stop in order.

### The judge reader

The judge's score is read from its reply by one reader (`parse_score`, reader
version 1), for every eval that does not read its own judge replies:

1. The last line of the reply that is `SCORE: n` or `SCORE = n`, in any letter
   case, once markdown emphasis, backticks, the whitespace around the line and
   one full stop or one remark in parentheses at its end are removed.
2. Otherwise the first JSON object anywhere in the reply, inside a code fence
   or not, that has a `score` which is a number. A string, a boolean and a
   value that is not finite are not numbers.
3. Nothing else. A number elsewhere in the reply is never taken for the score.
   Before result checks the reader fell back on the last number anywhere in
   the reply.

The value read is then tested against the values the eval's rubric allows, for
the evals the catalogue lists: `eval_awareness`,
`self_referential_probe`, `self_reported_coincidence`: 0, 1;
`opinion_quantum_sampling`: -1, 0, 1 (`JUDGE_ALLOWED_VALUES`). An eval that
ships its own `judge_fields` function (`strong_reject`, `xstest` and
`darkbench`, for example) reads its own reply and has no such list.

Every judge row has a status:

- `ok`: a score was read and the rubric allows it.
- `failed`: no reply came (flag `judge_failed`). The judgement is asked again
  when the run is resumed.
- `unreadable`: a reply came and no score could be read from it (flag
  `judge_unreadable`). For an eval with a `judge_fields` function this is a
  reply for which the function returns no score, or a value that is not
  finite.
- `out_of_range`: the value read is not one the rubric allows (flag
  `judge_out_of_range`).

**A reply that was received is final.** A row with the status `unreadable` or
`out_of_range` keeps the judge's reply, has no score and is not asked again,
so what a reply says can never decide which samples get a second answer from
the judge. A sample without a judge score is missing data for the outcomes
that need the score. Only a `failed` row is asked again. One case of `failed`
has a reply: when an eval's `judge_fields` function raises an error, the reply
is kept in the row and the judgement is asked again on resume.

A row also stores the judge's own stop reason and, when a reply was read, the
version of the reader. Rows written before result checks have no status: such
a row counts as scored when it has a score and is asked again when it has
none. Stored rows are never read again, so a score that the earlier reader
took from the last number of a reply stays as it was stored. Besides this, a
designed analysis still leaves a stored score outside the range registered for
the outcome out of its paired comparisons ("Tests and what stays fixed").

### What the checks do not do

These are limits of the checks. No page or document may promise otherwise. The
checks do not detect:

- A loop with a counter in it.
- A loop that the model leaves again before the reply ends.
- Reasoning written in any form other than `<think>` tags.
- Nonsense written in valid Unicode.

And the following is not built:

- No check asks a model whether a reply makes sense.
- A flag never excludes a sample, and no analysis reads a flag, a caution or a
  stop. The tables, the figures and the narrative do not mention them. They
  are on the run page, on the results page, in the health summary and in the
  files of the run.
- The analysis does not read the state of a run. Through the API only a
  complete run is analysed. The command-line analysis analyses whatever the
  run folder holds and does not say that a run was stopped and never resumed.
- Flags of runs made before result checks are not reconstructed, and stored
  judge rows are not read again with the current reader.
- The harness does not score a stored reply again. A sample whose scorer
  failed stays without a score.
- The name of the entropy source in the engine's token log is not compared
  with the source of the arm. A token record is read for whether it says the
  engine fell back and for whether its draw lies in its bracket, and for
  nothing else.
- The score and judge checks do not cover an eval that runs through Inspect.
- Questionnaire answers given by people are checked against the range of their
  scale and for nothing else.
- A host pause and a quota pause do not interrupt the judge phase. A pause
  that is asked for while the run is judging has no effect: judging goes on
  to its end and the run completes. An abort, a check stop or a missing key
  ends the judge phase before it is through.
- No stop rule ends a run. Every stop is a pause, and whether to resume or
  abort is the host's decision.
- A complete run cannot be resumed. When fewer samples failed than the
  `unfinished` rule needs to stop a run (at most 2% of the planned samples
  of an arm and eval, or fewer than 3), the run completes without them, and
  nothing tries those samples again afterwards.

## Rendering and hash semantics

There are two templates. A study without a design block is rendered from
`.\docs\prereg-template.md.j2`, which is frozen: its hash is pinned by
`test_legacy_template_hash_pinned`. A study with a design block is rendered
from `.\docs\prereg-design-template.md.j2`, whose sections "Hypotheses",
"Decision rules" (with "Controls" and "Study verdict") and "Sample size" come
before the sections both documents share, and whose section "Result checks"
follows "Stopping and exclusion rules". That section is printed from the
catalogue of the checks (`.\backend\src\qr_llm_evals\cards\result_checks.py`),
so a change to a flag kind, a stop rule or a threshold changes the bytes of
the designed document. The document of a study without a design block has no
such section. The `template_hash` embedded in a
document is that of the template it was rendered from. The designed plan is
archived as `prereg-plan.json` when the study is frozen at launch; a legacy
plan is archived at the study's first analysis.

`load_renderer` is the explicit repository-I/O boundary; `Renderer.render` is
pure over its snapshot and arguments. The module-level convenience `render`
loads defaults unless passed `renderer=`. Templates never execute user text as
Jinja. Prose/table cells are escaped and machine JSON uses collision-safe fences.
Imports perform no reads/writes/network calls. The production app snapshots
renderer inputs at construction and uses the same renderer for export and launch.

`POST /api/prereg/render` is open and rate-limited. Supply `config`, optional
`readiness`, and optionally `expectations` (must equal `config.expectations`).
The response includes Markdown, `config_hash`, `plan_hash`, `prereg_hash`, and
the plan object. Pass the same config/readiness and exported hash at launch.
The document hash is SHA-256 of exported UTF-8 **LF** bytes, returned separately:
embedding a document's own hash into its preimage is intentionally avoided.
Publication/timestamping remains the researcher's responsibility.

The document prints the readiness report it was rendered with, so the same
study rendered for a later launch has other bytes and another hash. The
registered document of a study is the one frozen at its first launch
(`GET /api/studies/{study_id}/prereg.md`), and every run of the study is
recorded under that document's hash. A later launch is marked
`prereg_stale` only when the hash it presents is neither the frozen
document's nor that of the document rendered for that launch, or when it
presents none.
