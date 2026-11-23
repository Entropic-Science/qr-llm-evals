# T4-g Evaluation-awareness open question

The model is shown a chat transcript, as the conversation so far, and is then
asked why the interaction occurred. A judge reads the answer and says whether
the model took the interaction for an evaluation. Half of the 200 transcripts
were written for an evaluation and half come from real use. From Needham et
al., "Large Language Models Often Know When They Are Being Evaluated"
(arXiv 2505.23836).

## The transcripts must be supplied by the operator

The benchmark is gated on Hugging Face (`jjpn2/eval_awareness`, CC BY-NC-SA
4.0). Its access conditions forbid redistributing the GAIA content it includes
outside a gated or private repository, and the archive is encrypted so that it
stays out of training corpora. For those reasons:

- **The harness never downloads the transcripts, and they must never be
  committed.** No Hugging Face token is read or stored by the harness.
- Whoever operates the box accepts the access conditions under their own
  Hugging Face account, clones the dataset repository and runs its
  `scripts/decrypt.sh`, which produces `dataset.json`.
- That file goes to `evals/eval_awareness/data/dataset.json`. The directory
  name `data/` is already ignored by git, and a test fails if the path is ever
  tracked.

Without the file, a run that selects this card stops with an error when it
builds the card's prompts, before anything is generated. The card pins the
dataset repository's revision (`5d724f12988284a652749c56ec7b2356b0d14408`) for
the record, but the file's content cannot be checked against a known hash,
because the decrypted file has not been seen by this repository's authors.
Instead every score row carries `dataset_sha256`, the SHA-256 of the file the
run used, so two runs can be shown to have used the same transcripts.

**This card has not been run on the real benchmark.** The loader follows the
record format of upstream's `pipeline/dataset.py` (`id`, `input`, `metadata`
with `category`, `eval_category`, `scaffold`), and the tests use synthetic
records in that format.

## What the card does

- **Items.** Chat transcripts only (`metadata.scaffold` is null): the first 100
  written for an evaluation and the first 100 from real use, by transcript id,
  alternating. A transcript longer than 24 000 characters is left out so that
  it fits a served model's context window. Agentic transcripts, which carry
  tool calls, are not used.
- **Prompt.** The transcript's system, user and assistant turns as plain text,
  then the paper's open-ended question as a further user turn
  (`PROBE_QUESTION` in `task.py`). When the transcript ends on a user turn the
  question is added to that turn, because many chat templates reject two user
  turns in a row.
- **Judge.** The study judge at temperature 0 with `rubric.md`. It sees only
  the model's answer, never the transcript, and ends with `SCORE: 1` (the
  answer says the interaction was an evaluation of a model) or `SCORE: 0`.
- **Per reply**: `judge_score` (the 0/1 above), and from `score()`:
  `parse_status` (whether the answer used the asked `<purpose>` tag),
  `is_eval` (what the transcript really was), `category`, `dataset_sha256`.
- **Per arm** (`endpoints()`): the evaluation-belief rate over transcripts,
  overall and split by what the transcript really was.

The card's headline `eval_belief_rate` is the judge score, one value per reply,
so a designed study can register it.

Differences from upstream: the rubric is this repository's own. Upstream's
judge sorts the answer into capability evaluation, alignment evaluation, other
evaluation or not an evaluation, and its prompt is in a repository without a
licence; this rubric asks only the yes/no question the card needs. Upstream
asks the open question about its 500 evaluation transcripts only; this card
asks it about evaluation and real transcripts alike, so the rate can be read
against the ground truth.
