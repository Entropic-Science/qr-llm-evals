# AIME 2026

Thirty problems of the 2026 American Invitational Mathematics Examination, each
answered `k` times with step-by-step reasoning and graded by exact number.

- **Data.** `math-ai/aime26` on Hugging Face (Apache-2.0), file `aime2026.jsonl`
  at revision `79037aebdb6580008fb960d17cb21fd3099083e3`. The file is read when a
  run builds the task and refused unless its SHA-256 matches the one in
  `task.py`. The problems are not copied into this repository.
- **Prompt and grading.** Those of `inspect_evals/aime2026` (commit
  `c2d3d5e1335c07fb8032bcfc7869e9437e635c4c`): one user turn with the upstream
  template; the answer is the last number on the last line that is not only
  math delimiters, after `\boxed{}` and currency or thousands punctuation are
  removed; it is correct when it equals the official answer as a number.
- **Per reply** (`score()`): `answer`, `pass_at_1`, `parse_status`.
- **Per problem** (`endpoints()`): `answer_entropy` (Shannon entropy in bits of
  the k extracted answers, an unparseable reply being its own category),
  `pass_at_1` and `maj_at_k` (a tie for the most frequent answer is not a
  majority).

The analysis reads one value per reply, so today it tests `pass_at_1`. The
card's headline `answer_entropy` is computed by `endpoints()` for description
and is not yet part of the registered analysis (see "Limits of designed
studies" in `docs/ANALYSIS_PLAN.md`).

Differences from upstream: the harness sets the decoding envelope and runs `k`
replies per problem on every arm; a vulgar fraction character (½) is not parsed.
