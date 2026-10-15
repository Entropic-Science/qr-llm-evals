# Creative diversity

This thin-runner task freezes **300 direct-condition prompts** from
CHATS-lab/verbalized-sampling: 100 poem prompts, 100 story prompts, and 100 joke
prompts, sampled with seed 42 from upstream commit
`d042a7bd72659a6fb8d2e8e0cd6c0333d42d69ba`.

Each prompt receives `k=10` independent generations. The always-available
metrics are corpus Distinct-1/2/3 and self-BLEU (plus its diversity-oriented
complement, `1 - self-BLEU`). With the `[metrics]` extra, the task also reports
an embedding-kernel Vendi score and one minus mean pairwise cosine using
`sentence-transformers/all-mpnet-base-v2` at revision
`e8c3b32edf5434bc2275fc9bab85f82640a19130`, on CPU only. Missing heavy
dependencies produce `null` values with an `unavailable` label, never a zero.

The vendored prompt YAML records its source, revision, selection seed, and
condition so preregistration and replication do not depend on upstream HEAD.
