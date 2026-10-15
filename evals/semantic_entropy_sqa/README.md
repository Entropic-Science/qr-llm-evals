# Semantic entropy on SimpleQA Verified

At task-build time this adapter reads SimpleQA Verified from the immutable
Hugging Face revision `ed392714d14fa5225b07c2b456453983bc75f690` and selects a
deterministic, proportionally topic-stratified 300-item subset (seed 20260914).
Each question receives `k=20` concise answers.

Exact-match accuracy is always available. The `[metrics]` extra adds mutual
NLI-entailment clustering with `microsoft/deberta-large-mnli` revision
`7296194b9009373def4f7c5dad292651e4b5cf4e`, followed by Shannon entropy over
the empirical cluster distribution and embedding Vendi. Both frozen models run
on CPU. If the extra or checkpoint is unavailable, heavy endpoints are emitted
as `null` together with an explicit `unavailable` label.

Network access occurs only when `build_items()` resolves the pinned dataset;
importing either this task or the core package remains side-effect free.
