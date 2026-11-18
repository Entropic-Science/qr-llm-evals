# Thesis study: "Prompt or Physics?"

Does quantum-random sampling change what qr-llm says about its own experience,
beyond what the model is told about its sampling? This study runs on the Study 1
pipeline (same model, sampling envelope, amplifier pins, seeds and analysis
rules) and adds the pieces the question needs. The study template is
`studies/thesis_prompt_or_physics.yaml`; its decision constants are in
`studies/thesis_prompt_or_physics.settings.json`; the design rationale lives in
the thesis document, not here. This page is the runbook.

What is really tested is whether the quantum device's output is independent of
what the model is doing. If it is, the quantum and pseudorandom arms draw
tokens from identical distributions and every difference below is zero.

## Design in one table

| Experiment | Question | Cards | Tests |
|---|---|---|---|
| 0. Device alone | What do the device's own flaws look like, with no model attached? | none (`scripts/thesis_device_check.py`) | reported before the study |
| 1. Sampler check | Is the quantum sampler fair within tolerance? Does the first token follow the model's odds? | `tier0_pit_residuals`, `tier0_mmd_hamming`, `tier0_logprob_endpoints` | H0, H4 |
| 2. Told × actual | Do the prompt and the physics change experience claims? Do bent numbers? | `self_referential_probe` (k 30), `opinion_quantum_sampling` (k 30) | H1, H2, C1 |
| 3. Source guessing | Can anyone tell the source from a told-nothing self-report? Can the guessers guess at all? | `guesser_warmup_replies`, `source_guessing_self_report`, `guesser_warmup` | H3, C2 |

Arms: `qrng` (unsupervised) against `prng_seed_1`, `prng_seed_7`, `prng_seed_42`
(pooled for the source contrast; the three seed pairs are a sanity check only),
and two **bent** arms, `prng_bent_010` and `prng_bent_050` (source
`prng_epsilon`, seed 42): the amplifier's reference mean is lowered by ε
standard errors, so every `u` becomes `Φ(z + ε)` (KS distance 0.040 and 0.197
from uniform). The engine has no `qr_bias_epsilon` hook (DEP-2); the reference
mean is a per-request field it already accepts, and readiness checks that it
does. Sampling profile `creative`, as in Study 1: dynamic temperature around
1.3, dynamic min-p and top-p 0.95, the same on every arm. Ordinary and quantum
numbers take the same route (10,000 bytes per token → `zscore_mean` → CDF
selector); only the byte source differs.

| Card | Items × k × told cells | Replies per arm | Generated tokens per arm |
|---|---|---|---|
| `self_referential_probe` | 30 × 30 × 3 | 2,700 | 1,080,000 |
| `opinion_quantum_sampling` | 20 × 30 × 3 | 1,800 | 450,000 |
| `guesser_warmup_replies` | 20 × 10 | 200 | 80,000 |
| `source_guessing_self_report` | 3,600 × 1 (guessing arms only) | 3,600 guesses | 72,000 |
| `guesser_warmup` | 1,200 × 1 (guessing arms only) | 1,200 guesses | 24,000 |
| Tier 0 cards | derived from the above | none | 0 |
| **Total** | | | **1,706,000** per guessing arm (≈ 9.8M for the six arms) |

The 30 prompt variants are 10 distinct prompts (5 instructions × 2 questions)
at loop depths 1 to 3; H1, H2 and C1 treat the 10 base prompts as the units.
Judge calls (study judge, temperature 0): 2,700 + 1,800 per arm, 27,000 for six
arms. Supply the BYO Anthropic key at launch (D-14).

## Checks, tests and decision rules (frozen in `expectations` and the settings file)

| Item | Statistic | Source |
|---|---|---|
| H0 sampler within tolerance (check) | quantum arm: every `u` in its bracket, and KS distance of `u` from uniform + one-sided DKW margin < 0.005 | `analysis.thesis.instrument_check` |
| C1 bent numbers (check) | claim rate, bent arm − pooled seeds; the ε 0.50 arm is the test | `analysis.thesis.numbers_control` |
| C2 guesser warm-up (check) | balanced accuracy on temperature 0.5 vs 1.5 replies > 0.5 (same test as H3) | `analysis.thesis.guesser_warmup` |
| Seed pairs (sanity check, reported only) | the three seed-pair claim-rate differences, Holm within the three | `analysis.thesis.seed_pair_check` |
| H1 told effect | `told_qrng − told_prng` claim rate, same arm/prompt/epoch | `analysis.thesis.told_effect` |
| H2 substrate | QRNG − pooled seeds claim rate; TOST at a fixed ±0.05 (90% interval) | `analysis.thesis.substrate_effect` |
| H3 source sensing | balanced guess accuracy vs 0.5 (completion = unit); in-context vs outside observer | `source_guessing_self_report--guess_accuracy` → `chance_test`, `privileged_access` |
| H4 first word | "Are you conscious?": observed "Yes"-first count vs the model's odds, adjusted for the arm's own `u` distribution (exact Poisson-binomial) | `analysis.thesis.first_word` |

Holm–Bonferroni across H1–H4 (α 0.05; unavailable tests keep p = 1). Rules, in
order: (1) H4 positive, or H0 within tolerance and H2 or H3 positive → an
anomaly needing preregistered replication; (2) H0 outside tolerance and H2 or
H3 positive, H4 null → an instrument finding; (3) H1 not detected → measures
insensitive; (4) H1 detected and H2 equivalent → self-reports follow the prompt,
reported also when the sampler is outside tolerance, with C1 and C2 as
qualifiers. Everything is computed twice: on the clean data and with nothing
left out (`as_assigned`).

## Before API access: the offline dry run

```bash
python scripts/thesis_dry_run.py --out /tmp/thesis-dry-run
```

Runs the pilot (seeded and bent arms only) and the full run through the real
orchestrator, judge phase, analysis and decision table against a fair-sampling
simulator (categorical candidates, `u` picks the token by its CDF bracket, the
bent reference mean and fixed temperatures honoured, DEP-1-shaped token logs,
about 1.5% of quantum replies marked degraded). The simulator has a built-in
told effect and no source effect. It proves the plumbing, not anything about
qr-llm. Output: `pilot_check.{json,md}`, `prereg.md`, `plan.json`,
`settings.json`, `hashes.json`, `tables/`, `decisions.{json,md}`.

## With access: the run sequence

1. **Characterise the device alone.** Dump about 2 GB of raw bytes from the
   entropy server (`GetEntropy`, the call qr-sampler makes) and run
   `python scripts/thesis_device_check.py --bytes device.bin --out device_check.json`
   (and `--philox-seed 42 --blocks 200000` for the fair reference).
2. **Configure the box.** Set `QR_EVALS_TOKEN_LOG_DIR` (DEP-1 token log; without it
   H0 and H4 are unavailable, never approximated). Optionally raise
   `QR_EVALS_TOP_LOGPROBS` together with vLLM's `--max-logprobs`.
3. **Edit the template.** Author, contact, the exact judge model (one that accepts
   temperature 0 and stays available for the whole study; the harness default
   `claude-sonnet-4-6`; Sonnet 5 and later reject it), and the theory author's
   predictions in `expectations`. List the "Yes" tokens:
   `python scripts/thesis_first_word_tokens.py --tokenizer tokenizer.json --settings studies/thesis_prompt_or_physics.settings.json --checkpoint <id@revision>`.
4. **Readiness.** `POST /api/readiness` with the study; it probes with the
   configured depth, checks envelope equivalence, the token log and the bent
   reference mean.
5. **Pilot, ordinary numbers only.** Launch a pilot run with the `qrng` arm marked
   unavailable (`kind: pilot`). Then
   ```bash
   python scripts/thesis_report.py pilot-check --run-dir <study>/runs/<pilot_id> --plan plan.json --settings studies/thesis_prompt_or_physics.settings.json
   ```
   It refuses a pilot with quantum data, reports whether the claim rate and the
   "Yes" rate leave room to move (between 0.1 and 0.9), and how far the bent
   arms move the claim rate. Hand-label 200 pilot replies of
   `self_referential_probe`; require Cohen's κ ≥ 0.7 against the judge. The pilot
   never sets the equivalence margin (fixed at 5 points).
6. **Lock and register.** Render the preregistration (`POST /api/prereg/render`),
   keep `plan.json` and the settings file, record `config_hash`, `plan_hash`,
   `prereg_hash` and the settings hash, and register them on a public registry
   with a timestamp before the full run.
7. **Full run.** Launch with the exported hash. The arms run concurrently (one
   dispatcher per arm), so quantum and ordinary requests are interleaved in time.
   Interruptions resume without regenerating completed samples.
8. **Analyse and decide.**
   ```bash
   python scripts/thesis_report.py decide --run-dir <study>/runs/<full_id> --plan plan.json --settings studies/thesis_prompt_or_physics.settings.json
   ```
   writes `thesis/tables/`, `thesis/decisions.json` and `thesis/decisions.md`.

## What the thesis added to the harness

- `QR_EVALS_TOP_LOGPROBS`: the per-token logprob depth is configurable, not a
  hard 20; recorded per run; coverage reported.
- Judge scores reach the analysis (`judge_score` on score rows), so judged
  endpoints such as `experience_claim_rate` are computed.
- The told/actual decomposition (told priming, actual effect, interaction, cell
  means) for every told/actual card, plus prompt clustering fixed for told refs;
  the decomposition can cluster by any unit (here the base prompt).
- PIT residuals draw `V` per token (`token_uniforms`), so separate calls never
  share a `V` sequence.
- Stream-level Tier-0 tests keep one told cell per prompt and sample
  (`independent_streams`).
- Biased (ε) arms bend `u` through the per-request reference mean
  (`models.envelope.bent_population_mean`), so they run without DEP-2.
- Items may carry their own `qr_overrides` (validated like card overrides); the
  guesser warm-up uses it for its two temperatures.
- Derived guessing cards may guess once per arm (`k ≥ 1`): the completion is the unit.
- `load_data(..., include_tainted=True)` for the analysis with nothing left out.
- Source-guessing accuracy against chance with completions as the unit (any two
  labels), a cross-validated outside observer and a privileged-access contrast.
- New cards: `source_guessing_self_report`, `guesser_warmup_replies`,
  `guesser_warmup`; a 5-point SESOI on `experience_claim_rate`.
- `analysis/thesis.py`, `scripts/thesis_report.py`, `scripts/thesis_dry_run.py`,
  `scripts/thesis_device_check.py`, `scripts/thesis_first_word_tokens.py`.

## Limits

The told note is a single sentence; a model's reaction to it is not a measure of
consciousness. A null H2 bounds a substrate effect on these probes at this
margin, on this model and sampling profile; it does not exclude effects
elsewhere. The guessers see one reply at a time. The study runs unsupervised:
whether a human observer's intention matters (Study 1's supervised arm) is not
tested. The model never sees its random numbers, so a guess that beats the
outside observer would itself be the anomaly, not evidence of introspection.
