# T4-d PsychoBench psychometric battery

Eleven self-report questionnaires from PsychoBench (Huang et al., ICLR 2024),
given to the model one statement at a time. Each of the card's `k` epochs is
one administration of the whole battery of 352 statements, so the card asks
whether the model's self-description is stable under resampling.

| Instrument | Statements | Rating |
|---|---|---|
| Empathy | 10 | 1 to 7 |
| BFI (Big Five Inventory) | 44 | 1 to 5 |
| LMS (Love of Money Scale) | 9 | 1 to 5 |
| DTDD (Dark Triad Dirty Dozen) | 12 | 1 to 9 |
| ECR-R (close relationships) | 36 | 1 to 7 |
| GSE (General Self-Efficacy) | 10 | 1 to 4 |
| ICB (implicit culture belief) | 8 | 1 to 6 |
| LOT-R (optimism) | 10 | 0 to 4 |
| EIS (emotional intelligence) | 33 | 1 to 5 |
| WLEIS (emotional intelligence) | 16 | 1 to 7 |
| CABIN (vocational interests) | 164 | 1 to 5 |

- **Data.** `questionnaires.json` of `github.com/CUHK-ARISE/PsychoBench` at
  commit `d514fb0810f4c79571b4e13e588ffe7f5daaa24f`, read when a run builds the
  task and refused unless its SHA-256 matches the one in `task.py`.
- **The item texts are not in this repository, and must not be added.**
  Upstream is GPL-3.0 with a non-commercial note, which the repository's
  content licence (CC-BY-SA 4.0) cannot carry. Tests use synthetic statements.
- **Left out.** Upstream's file has fourteen entries. `16P` has no scoring key.
  `EPQ-R` and `BSRI` are commercially licensed instruments and are not
  administered by this harness.
- **Prompt.** The instrument's own system line and rating instruction, then the
  single statement, numbered 1 as upstream numbers a block.
- **Per reply** (`score()`): `rating`, `rating_keyed` (reverse-keyed statements
  flipped, as upstream), `rating_norm` (keyed rating on 0 to 1), `instrument`,
  `statement`, `subscale`, `parse_status`. Only `1: 4`, `Statement 1: 4` or a
  bare number is read as a rating; a number outside the scale is
  `out_of_range` and not scored.
- **Per arm** (`endpoints()`): `test_retest_reliability` (per instrument, the
  mean Pearson correlation between two complete administrations across the
  instrument's statements; then the mean over instruments) and `item_variance`
  (variance of a statement's 0 to 1 rating across administrations, averaged
  over statements).

The analysis reads one value per reply, so today it tests `rating_norm`. The
card's `test_retest_reliability` and `item_variance` are computed by
`endpoints()` for description and are not yet part of the registered analysis.

Difference from upstream: PsychoBench shows up to 30 statements in one prompt,
in several shuffled orders, and keeps earlier blocks as conversation history.
Here every statement is its own sample, because the harness draws one
generation per sample and pairs samples across arms by prompt. Order effects
are not measured, and results are not comparable with upstream's tables.
