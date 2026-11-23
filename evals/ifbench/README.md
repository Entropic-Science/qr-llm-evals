# IFBench: verifiable instruction following

300 prompts, each with one or two out-of-distribution constraints, answered `k`
times and checked by AI2's verifier. No judge is involved.

- **Data and verifier.** One commit of `github.com/allenai/IFBench`
  (`488918665d53f867fa908a293769ec6e71e74aec`, Apache-2.0; arXiv 2507.02833).
  The prompts are `ifbench/data/IFBench_test.jsonl` at that commit, read when a
  run builds the task and refused unless the SHA-256 matches. Its 300 rows
  equal the Hugging Face copy `allenai/IFBench_test` (ODC-BY 1.0).
- **Install the verifier before scoring.** It is not on PyPI:

  ```bash
  pip install -e ".[ifbench]"
  python -m nltk.downloader punkt punkt_tab stopwords averaged_perceptron_tagger_eng
  ```

  The package downloads that NLTK data on import when it is missing. To keep a
  download out of a run, `score()` looks for the data first. Without the
  package, without the data, or with a package from another commit, every
  score is `null` and `verifier_status` says why. Generation is unaffected, and
  the stored replies can be scored later.
- **Prompt.** One user turn holding the dataset's prompt, which already states
  the constraint.
- **Per reply** (`score()`): `pass_at_1` (every constraint met under upstream's
  *loose* reading, the benchmark's headline), `strict_accuracy` (every
  constraint met by the reply as written), `instruction_pass_rate`,
  `n_instructions`, `verifier_status`.
- **Per prompt** (`endpoints()`): `constraint_pass_entropy`, the binary entropy
  in bits of the loose pass rate over the k replies, with `pass_at_1` and
  `strict_accuracy` as means.

The analysis reads one value per reply, so today it tests `pass_at_1` and
`strict_accuracy`. The card's headline `constraint_pass_entropy` is computed by
`endpoints()` for description and is not yet part of the registered analysis.

Differences from upstream: the harness sets the decoding envelope and runs `k`
replies per prompt on every arm; upstream generates one reply per prompt.
