# Determinism in the evaluation harness

Determinism is not a single switch. It is a measured property of a complete serving
envelope: model weights, software and hardware, engine flags, prompt bytes, sampling
configuration, entropy source, and request scheduling. The harness records the tier it
actually observes; it does not turn an operator claim into a guarantee.

The purpose of a deterministic control is narrow and scientific: replaying the seeded
pseudo-random arm should isolate the entropy stream from other sources of variation. A
quantum-random arm is intentionally not seed-reproducible, but its recorded draws can be
replayed after the fact.

## The four reported tiers

| Tier | Meaning | What may still vary |
|---|---|---|
| `none` | No replay guarantee was requested or established. | Logits, entropy, scheduling, and output tokens. |
| `sampling_deterministic` | For a fixed prompt and derived seed, qr-sampler supplies the same counter-based pseudo-random draw at each token position. | Engine logits may change with batching, cache history, or load, so a changed probability boundary can still change a token. |
| `serialized_replay` | Sampling is deterministic and the operator runs a serialized, pinned replay configuration. Identical requests are expected to replay on that deployment. | Changes to the software/hardware envelope; it is not a concurrent-load guarantee. |
| `load_invariant` | Sampling is deterministic and the forward pass uses verified batch-invariant kernels, so the same request replays while unrelated traffic changes. | Changes outside the frozen envelope and intentionally variable telemetry. |

These tier names are claims with evidence, not quality scores. `sampling_deterministic`
is useful even when it is not bit-reproducible under load: it establishes that the PRNG
stream itself is not being consumed in scheduler-dependent order.

## What the harness measures and records

The harness derives each PRNG request seed from `(study seed, card id, prompt id, epoch)`.
It sends the fixed sampler envelope, pairs matching prompt/epoch samples across arms, and
records the model, sampler settings, source provenance, derived seed, engine health,
fallbacks, and achieved determinism tier. It tests replay where the deployment exposes a
replay check. Any fallback taints provenance; pseudo-random output is never labelled
quantum.

For QRNG, reproducibility means **record-and-replay of the consumed entropy stream**, not
regenerating fresh quantum measurements. A replay can test the pipeline, but the replayed
run is not a new quantum-random observation.

The current hybrid-attention checkpoint cannot use vLLM's batch-invariant mode. The normal
deployment can therefore establish `sampling_deterministic`; a separately configured,
serialized deployment may establish `serialized_replay`. The preregistration and results
must state the tier achieved at run time rather than promising `load_invariant` in advance.

## What the operator must set

The operator—not the browser—owns the serving envelope. To claim more than
`sampling_deterministic`, the operator must:

1. Pin the checkpoint hash, tokenizer, qr-sampler, vLLM, Torch/CUDA, numerical libraries,
   GPU model/count, parallel layout, dtype, quantization, and computation-shaping flags.
2. Use the documented serialized replay deployment for `serialized_replay`, including one
   active sequence and prefix caching disabled.
3. Use a compatible full-attention model and verified batch-invariant kernels before
   claiming `load_invariant`.
4. Keep async scheduling, speculative decoding, guided decoding, and unregistered prompt
   templating out of the deterministic lane.
5. Run the verification protocol after every envelope change and retain its report with
   the study artifacts.

The harness checks reported readiness and refuses to silently upgrade the tier. It cannot
change engine-wide settings from a web request, prove an unreported deployment property,
or make an external provider's opaque PRNG deterministic.

## Popup text

**Deterministic means reproducible within a recorded serving envelope—not “temperature
zero,” and not that the model becomes predictable in every environment.**

The seeded PRNG control uses the same draw at each token position when its prompt and seed
are unchanged. On the currently served hybrid-attention model, concurrent load can still
change the forward-pass logits, so today the normal service is
**sampling-deterministic**, not load-invariant. A serialized replay deployment can provide
a stronger tier; full load invariance requires compatible batch-invariant kernels.

The QRNG arm is intentionally fresh. It can only be reproduced by replaying its recorded
entropy stream, and that replay is labelled as a replay—not as a new quantum sample.

The harness records the tier actually achieved in the pre-registration and results. The
operator must pin and verify the model, software, hardware, and engine configuration; this
browser setting cannot change those deployment-wide conditions.

## Replay interpretation

A matching replay supports the integrity of the frozen envelope. A mismatch does not, by
itself, show an effect of quantum entropy: first inspect the tier, cache state, fallback and
taint records, checkpoint and package hashes, engine logs, and whether the same prompt bytes
and derived seed were used. Runs with an entropy fallback or a mismatched envelope must be
reported as such rather than folded into the quantum condition.

For the serving-stack implementation detail and verification runbook, see the qr-sampler
repository's `docs/determinism.md`.
