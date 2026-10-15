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

## Primary family

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
Epsilon curves use available configured levels (Study 1 **0.01, 0.03, 0.10**);
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

Readiness is supplied evidence, not authenticated by a public render request.
No engine logs means no T0-a or T4-l and only raw-logprob T0-c endpoints.
Forward-pass nondeterminism, temporal confounding, evaluation awareness, priming,
judge/embedding bias and observer selection remain validity limits. None of
these tests by itself establishes consciousness or a causal observer mechanism.

## Rendering and hash semantics

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
