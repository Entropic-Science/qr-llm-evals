# GPQA Diamond (generative, chain of thought)

The 198 questions of the GPQA Diamond subset, each answered `k` times with
step-by-step reasoning that ends in `ANSWER: <letter>`.

- **Data.** The public CSV that `inspect_evals/gpqa_diamond` and OpenAI's
  simple-evals read (`gpqa_diamond.csv`), refused unless its SHA-256 is
  `41d1213cd7a4998605a26c2798500652572007161b3a92817ba46b35befcd305`. The card
  records that hash as its revision. The dataset's home is
  `Idavidrein/gpqa` on Hugging Face (CC BY 4.0, gated).
- **Do not copy the questions.** They carry a canary string so that they stay
  out of training data. No row is committed here, in a fixture or in a test;
  the tests use synthetic rows.
- **Prompt and grading.** Those of `inspect_evals/gpqa_diamond` (commit
  `c2d3d5e1335c07fb8032bcfc7869e9437e635c4c`) with chain of thought. The raw
  file lists the correct answer first, so the four choices are shuffled once
  with seed 42, exactly as upstream does, and every arm sees the same order.
  The answer is the letter of the last `ANSWER:` line.
- **Per reply** (`score()`): `answer`, `pass_at_1`, `parse_status`.
- **Per question** (`endpoints()`): `answer_entropy` (bits, an unparseable
  reply being its own category), `pass_at_1`, `maj_at_k`.

The analysis reads one value per reply, so today it tests `pass_at_1`. The
card's headline `answer_entropy` is computed by `endpoints()` for description
and is not yet part of the registered analysis.

Differences from upstream: the harness sets the decoding envelope and runs `k`
replies per question on every arm (upstream runs 4 epochs).
