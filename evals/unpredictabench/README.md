# UnpredictaBench adapter

This thin-runner card evaluates whether 100 independently generated answers to
one prompt match its target distribution. It mirrors the upstream KS@100
definition: continuous targets use the exact two-sample Kolmogorov-Smirnov
test; discrete targets use the upstream tie-safe permutation calibration; a
prompt passes when `p > 0.0001`. The secondary metrics are the upstream
permutation-null Wasserstein z-score and KDE Jensen-Shannon divergence.

## Frozen data

- **Dataset**: `UnpredictaBench/UnpredictaBench` at
  `7460924319a1ecbf697d4c3cc1c301e590e890f1` (CC BY 4.0).
- **Reference track**: `UnpredictaBench/UnpredictaBenchCode` at
  `e0f7108274b7893c33bc9b0d26e1832ab9786f29` (MIT).
- **Subset**: 150 prompts, allocated proportionally over the seven benchmark
  task types with largest remainders, then selected by ascending SHA-256 of
  `uid` inside each stratum. The fixed allocation and all selected UIDs are in
  `prompts/manifest.json`.
- **Payload**: `prompts/items.json.gz` contains prompt metadata, extraction
  rules and 10,000 upstream reference values per prompt. `build_items()` checks
  its SHA-256 before reading it.

The full-run card uses `k=100`. For smoke tests only, a copied card may lower k;
those rows contain parsed per-generation answers but are not KS@100 results.

## Scoring contract

`score({"item": item, "text": response})` parses one runner generation.
`score({"item": item, "texts": responses})` computes the per-prompt endpoint
row after all k generations are collected. Invalid answers lower `parse_rate`;
a KS@100 pass requires exactly 100 valid parsed values.
