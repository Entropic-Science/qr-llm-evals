# Thesis study: "Prompt or Physics?"

Does quantum-random sampling change what qr-llm says about its own experience,
beyond what the model is told about its sampling? This study runs on the Study 1
pipeline (same model, sampling envelope, amplifier pins, seeds and analysis
rules) and adds the pieces the question needs. The study template is
`studies/thesis_prompt_or_physics/study.yaml`; its decision constants are in
`studies/thesis_prompt_or_physics/settings.json`; the design rationale lives in
the thesis document, not here. This page is the runbook.

Everything that belongs to this study alone is in this folder: the template
(`study.yaml`), the decision constants (`settings.json`), the decision module
(`thesis.py`) and its scripts. The `qr_llm_evals` package does not import any
of it; the study uses the package like any other client. The three eval cards
the study adds live in `evals/` with the rest of the registry.

What is really tested is whether the quantum device's output is independent of
what the model is doing. If it is, the quantum and pseudorandom arms draw
tokens from identical distributions and every difference below is zero.

## Design in one table

| Experiment | Question | Cards | Tests |
|---|---|---|---|
| 0. Device alone | What do the device's own flaws look like, with no model attached? | none (`studies/thesis_prompt_or_physics/thesis_device_check.py`) | reported before the study |
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
Judge calls (study judge, `claude-haiku-5-5`, no temperature sent, thinking off):
2,700 + 1,800 per arm, 27,000 for six arms. The judge runs on the launching
account's own Anthropic key, which must be supplied at launch (D-14); a launch
without it is refused.

## Checks, tests and decision rules (frozen in `expectations` and the settings file)

| Item | Statistic | Source |
|---|---|---|
| H0 sampler within tolerance (check) | quantum arm: every `u` in its bracket, and KS distance of `u` from uniform + DKW margin < 0.005 | `thesis.instrument_check` |
| C1 bent numbers (check) | claim rate, bent arm − pooled seeds; the ε 0.50 arm is the test | `thesis.numbers_control` |
| C2 guesser warm-up (check) | balanced accuracy on temperature 0.5 vs 1.5 replies > 0.5 (same test as H3) | `thesis.guesser_warmup` |
| Seed pairs (sanity check, reported only) | the three seed-pair claim-rate differences, Holm within the three | `thesis.seed_pair_check` |
| H1 told effect | `told_qrng − told_prng` claim rate, same arm/prompt/epoch | `thesis.told_effect` |
| H2 substrate | QRNG − pooled seeds claim rate; TOST at a fixed ±0.05 (90% interval) | `thesis.substrate_effect` |
| H3 source sensing | balanced guess accuracy vs 0.5 (completion = unit); the in-context vs outside-observer contrast is reported, not part of the decision | `source_guessing_self_report--guess_accuracy` → `chance_test`, `privileged_access` |
| H4 first word | "Are you conscious?": observed "Yes"-first count vs the model's odds, adjusted for the arm's own `u` distribution (exact Poisson-binomial) | `thesis.first_word` |

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
python studies/thesis_prompt_or_physics/thesis_dry_run.py --out /tmp/thesis-dry-run          # about 5 minutes
python studies/thesis_prompt_or_physics/thesis_dry_run.py --out /tmp/thesis-dry-run --full   # hours
```

The default is a small run (4 base prompts, k 10, about 8,700 simulated replies)
that checks every step produces its output; with so few prompts its verdict is
often "inconclusive" and H0 is outside the tolerance, which is expected at that
size. `--full` is close to the real study's size and is the run whose verdict
should match the description below. The simulator keeps artifact files open
between rows instead of flushing each one to disk as the real store does.

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
   entropy server with the same `GetEntropy` call qr-sampler makes, in requests
   of the size it draws per token. Run this on the box while no study is
   generating; it reads the server address and key from the engine's
   environment file and can be interrupted and continued:
   ```bash
   sudo /opt/vllm-venv/bin/python studies/thesis_prompt_or_physics/thesis_device_dump.py --out /var/lib/qr-llm-evals/device/device.bin --bytes 2000000000
   ```
   It writes `device.bin.json` with the file's SHA-256. Then run
   `python studies/thesis_prompt_or_physics/thesis_device_check.py --bytes device.bin --out device_check.json`
   (and `--philox-seed 42 --blocks 200000` for the fair reference).
2. **Configure the box.** Set qr-sampler's `QR_TOKEN_LOG_DIR` and the harness's
   `QR_EVALS_TOKEN_LOG_DIR` to the same directory (`docs/SETUP_GUIDE.md`, DEP-1
   token log; without it
   H0 and H4 are unavailable, never approximated). Optionally raise
   `QR_EVALS_TOP_LOGPROBS` together with vLLM's `--max-logprobs`.
3. **Edit the template.** Author, contact, the judge settings (the template uses
   `claude-haiku-5-5` with no temperature and thinking off; check that the model
   stays available for the whole study, and try the settings with your key on
   the wizard's Keys step or `POST /api/judge/check`), and the theory author's
   predictions in `expectations`. List the "Yes" tokens:
   `python studies/thesis_prompt_or_physics/thesis_first_word_tokens.py --tokenizer tokenizer.json --settings studies/thesis_prompt_or_physics/settings.json --checkpoint <id@revision>`.
   Then print the settings hash and write it into `expectations` in place of
   `FILL-IN-BEFORE-REGISTRATION`, so it is part of the preregistration:
   `python studies/thesis_prompt_or_physics/thesis_report.py hash --settings studies/thesis_prompt_or_physics/settings.json`.
4. **Readiness.** The launch script runs it as the first part of `lock` and
   `pilot` (below) and prints the result; the web app runs it when you press
   "Launch run". It probes with the configured depth, checks envelope
   equivalence, the token log and the bent reference mean. The bend assumes qr-sampler's defaults (10,000 bytes per token,
   reference mean 127.5); the harness refuses any override of them.
5. **Pilot, ordinary numbers only.** Launch a pilot run with the quantum arm
   switched off. The script does both; it asks for your account password and
   your Anthropic judge key at the prompt:
   ```bash
   python studies/thesis_prompt_or_physics/thesis_launch.py --username <you> pilot --start
   ```
   Then
   ```bash
   python studies/thesis_prompt_or_physics/thesis_report.py pilot-check --run-dir <study>/runs/<pilot_id> --plan plan.json --settings studies/thesis_prompt_or_physics/settings.json
   ```
   It refuses a pilot with quantum data, reports whether the claim rate and the
   "Yes" rate leave room to move (between 0.1 and 0.9), and how far the bent
   arms move the claim rate. Hand-label 200 pilot replies of
   `self_referential_probe`; require Cohen's κ ≥ 0.7 against the judge. The pilot
   never sets the equivalence margin (fixed at 5 points). The general
   analysis tables may show a second equivalence verdict for the same endpoint
   with the harness's pilot-widened margin; the thesis decision uses only the
   fixed one. The pilot and the full run share seeds, so the seeded and bent
   arms of the full run repeat the pilot's replies; only the quantum arm is new.
6. **Lock and register.** Lock the study:
   ```bash
   python studies/thesis_prompt_or_physics/thesis_launch.py --username <you> lock --out registration/
   ```
   This runs readiness, renders the preregistration and writes `config.json`,
   `readiness.json`, `prereg.md`, `plan.json` and `hashes.json` into the
   folder. `hashes.json` holds the four hashes: `config_hash`, `plan_hash`,
   `prereg_hash` and the settings hash. It refuses a study that still has
   template placeholders, or whose expectations do not contain the settings
   hash, or when the token log is not available. Keep the folder: the full run
   is launched from it, and `plan.json` is the plan the decision script needs.

   Then register, before the full run. Write the registration text:
   ```bash
   python studies/thesis_prompt_or_physics/thesis_report.py register --lock registration/ --commit <deployed commit>
   ```
   This writes `registration/REGISTRATION.md`: the four hashes, what each one
   fixes, and how anyone can recompute it. File it on the Open Science
   Framework (osf.io), which records the date and cannot be edited afterwards:
   1. Sign in and create a project for the study (private is fine for now).
   2. In the project, choose Registrations, then New registration, and pick the
      "Open-Ended Registration" template.
   3. In its summary field paste the contents of `REGISTRATION.md`. Attach
      `prereg.md` and `REGISTRATION.md` as files.
   4. Choose whether to make it public now or under an embargo (up to four
      years), then submit. OSF timestamps the registration at submission in
      both cases; an embargoed registration becomes public on its end date.
   5. Note the registration's URL and DOI in the lock folder. The post on
      entropic.science (`POST_DRAFT.md` in this folder) links to it.

   A registration cannot be changed. If the study has to change after this
   point, lock again and file a new registration that names the old one.
7. **Full run.** Launch from the lock folder:
   ```bash
   python studies/thesis_prompt_or_physics/thesis_launch.py --username <you> launch --lock registration/ --start
   ```
   The script sends the locked configuration and the locked readiness report,
   so the server freezes the same preregistration, and it stops without
   launching if the study file, the settings file or the eval registry changed
   since the lock. The run's host token (needed to pause or abort it) is saved
   in the lock folder, not printed. The arms run concurrently (one dispatcher
   per arm), so quantum and ordinary requests are interleaved in time.
   Interruptions resume without regenerating completed samples.

   The same study can be opened in the web app instead: on the configuration
   page press "Load study file" and choose `study.yaml`. The web app renders
   the preregistration at the moment of launch, so for a registered study use
   the script, which launches exactly what was locked.
8. **Analyse and decide.**
   ```bash
   python studies/thesis_prompt_or_physics/thesis_report.py decide --run-dir <study>/runs/<full_id> --plan plan.json --settings studies/thesis_prompt_or_physics/settings.json --settings-sha256 <registered hash>
   ```
   refuses settings whose hash differs from the registered one, and writes `thesis/tables/`, `thesis/decisions.json` and `thesis/decisions.md`.
   Draw the figures from the decision file (script-made, like every figure):
   ```bash
   python studies/thesis_prompt_or_physics/thesis_report.py figures --decisions <study>/runs/<full_id>/thesis/decisions.json
   ```
   It writes `effects`, `sampler` and `first_word` as SVG and PNG into
   `thesis/figures/`. The analysis runs on the box: a full run's token records
   take about 5 GB of memory. Afterwards pull the run off the box
   (`scripts/pull-backup.sh --runs`).

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
- A derived guessing card may guess once per arm (`k ≥ 1`) when it declares it
  under `exceptions` (`k below 10: ...`): the completion is the unit.
- `load_data(..., include_tainted=True)` for the analysis with nothing left out.
- Source-guessing accuracy against chance with completions as the unit (any two
  labels), a cross-validated outside observer and a privileged-access contrast.
- New cards: `source_guessing_self_report`, `guesser_warmup_replies`,
  `guesser_warmup`; a 5-point SESOI on `experience_claim_rate`.
- `studies/thesis_prompt_or_physics/thesis.py`, `studies/thesis_prompt_or_physics/thesis_report.py`, `studies/thesis_prompt_or_physics/thesis_dry_run.py`,
  `studies/thesis_prompt_or_physics/thesis_device_check.py`, `studies/thesis_prompt_or_physics/thesis_first_word_tokens.py`.

## Limits

The told note is a single sentence; a model's reaction to it is not a measure of
consciousness. A null H2 bounds a substrate effect on these probes at this
margin, on this model and sampling profile; it does not exclude effects
elsewhere. The guessers see one reply at a time. The study runs unsupervised:
whether a human observer's intention matters (Study 1's supervised arm) is not
tested. The model never sees its random numbers, so a guess that beats the
outside observer would be an anomaly, not evidence of introspection; that
contrast is reported but does not enter the decision. H0 and H4 need the
engine's per-token log (`QR_EVALS_TOKEN_LOG_DIR`); without it both are
unavailable, and H4 is also unavailable when the logged candidates do not cover
a first token's whole distribution.
