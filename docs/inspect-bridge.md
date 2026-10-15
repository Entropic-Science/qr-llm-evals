# Inspect bridge

Install the optional engine with `pip install -e ".[dev,inspect]"`. The core
backend does not import Inspect at startup. On Windows, Git's long-path support
may be needed when installing the pinned `inspect-evals` repository.

## Generation and provenance

The API injects the Inspect executor into the runner; neither layer-2 package
imports the other. A spawned child owns Inspect scheduling and `.eval` logs.
Its `qrllm/<arm_id>` provider reads the sample context established by the first
solver and sends a request over an anonymous, memory-only pipe. The parent
uses **the same dispatch method** as thin tasks, including the envelope, derived
seed, entropy probes, streaming events, generation/Tier-0 rows and taints.
Inspect settings cannot override the harness sampling envelope. Provider and
judge keys stay in the parent's `SecretBag`, not in child arguments or logs.

The parent controls start, pause at generation boundaries, abort and resume.
Completed generations are replayed to Inspect without new paid requests;
changed prompt hashes fail closed. Existing logs are retained and each retry
writes a new `.eval` log under the arm/card's `inspect` directory. Child failure
stops this run without terminating the API. Log sample-end events drive the
runner's progress bus; text/thinking deltas come directly from parent dispatch.

## NoveltyBench adaptation

The pinned upstream implementation is
[inspect-evals 360484a0](https://github.com/UKGovernmentBEIS/inspect_evals/blob/360484a06383f9260279938262d78ed646ddbca1/src/inspect_evals/novelty_bench/novelty_bench.py).
Its actual dataset is `yimingzhang/novelty-bench`, curated split, revision
`255584e0f4538d1f2acb4c1d287b4d3bb30fe2f9`; the earlier design's
`novelty-bench/nb-curated` was a placeholder, not the upstream dataset path.

The generation adapter reproduces the upstream dataset and `id`/`prompt` field
mapping without importing its heavy scorer modules. It deliberately replaces
the upstream nested `multiple_generator(k)` with **one generation per epoch**.
Keeping that nested solver while also setting `epochs=k` would produce k²
responses and reuse pairing keys across forks. Study 1 instead produces
100 × 20 responses, with an estimated **800,000 generated tokens per arm**.

Inspect epochs are 1-based; harness epochs are 0-based. Every sample's log
metadata includes `qr_join_key = [card_id, sample_id, epoch - 1]`, matching the
generation row and Tier-0 `sample_ref`. The task records the upstream commit,
dataset revision, original task arguments, and this mapping. Epoch reduction
is explicitly `mean` as specified, but it is **not** a diversity estimator.

## Analysis handoff

No Distinct_k, Utility_k or Vendi values are invented during generation. The
analysis phase must group all k completions by **(arm, card, prompt)** and run
the frozen partitioner/reward/embedding models under the metrics extra.
It must freeze and report those model checkpoint revisions and use identical
checkpoints across arms. Incomplete groups must not be treated as full-k scores.
The fixed temperature-zero grader role is configured through the harness's
native Anthropic client, although NoveltyBench's classifier/reward scoring does
not use that LLM role. A successful generation log is not a scored analysis.

## Verification

The offline tests exercise real Inspect tasks with an injected tiny dataset and
the stub engine, including byte-identical thin/Inspect generation and Tier-0
files, epoch mapping, retained logs, resume, and subprocess crashes. They skip
cleanly when Inspect is absent. Run the repository gate before shipping.
