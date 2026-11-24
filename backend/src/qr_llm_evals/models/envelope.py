"""Arm → engine envelope: the single seam producing every generation request (spec §2.2, D-6, D-7).

An arm is ``(model, sampling, source, determinism, observer_mode, seed?)``.
:func:`build_envelope` turns it into the ``vllm_xargs`` dict (the ``qr_*``
keys qr-sampler reads per request) and :func:`build_request_body` wraps that
into the full chat-completions body for the shared vLLM on loopback — or, for
external providers, the plain OpenAI body their profile accepts.

Invariants enforced here (AGENTS.md invariant 1):

* **The harness owns the envelope.** Nothing from the browser or a card can
  set an infrastructure key (:data:`REFUSED_QR_KEYS`), the entropy source, the
  preset or the seed; :func:`validate_overrides` refuses them up front.
* **All arms pin the same amplifier** (:data:`AMPLIFIER_PINS`, D-7). The box's
  process default is the ``server`` amplifier via ``QR_PRESET=qthought_think``;
  without the pin the QRNG arm and the seeded arm would sample through
  different pipelines and the study would compare amplifiers, not entropy.
* **Arms differ only in the entropy keys** (:data:`EQUIVALENCE_KEYS`);
  :func:`equivalence_violations` is what the readiness check asserts.
* **Conditioning belongs to the study, and to the bytes of the entropy
  server.** A study's ``conditioning`` (``hash_truncate``) is sent on every arm
  whose source draws from the entropy server (:data:`CONDITIONED_SOURCES`) and
  on no other: a seeded or system PRNG is uniform already and the engine
  refuses conditioning on it. The four keys are therefore entropy keys. A raw
  study sends none of them, so its envelopes are those of a study from before
  the field.
* **Biased (epsilon) arms bend the numbers, nothing else.** The engine has no
  ``qr_bias_epsilon`` hook (DEP-2), so a ``prng_epsilon`` arm shifts the
  z-score amplifier's reference mean instead (:func:`bent_population_mean`):
  every ``u`` becomes ``Φ(z + ε)``, as if the bytes averaged ``ε`` standard
  errors above 127.5. Same bytes, same amplifier, same selector.
* **Greedy consults no randomness.** On qr lanes ``qr_fixed_temperature: 0``
  is *not* greedy (the one-hot force ignores vLLM ``temperature``); greedy is
  ``qr_bypass: true`` + ``temperature: 0`` and is refused on ``source=qrng``.

Constants copied from sibling repos carry a comment naming the source and are
compared live by ``backend/tests/test_envelope.py`` (``@pytest.mark.live``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from qr_llm_evals.config import DEFAULT_TOP_LOGPROBS, MAX_TOP_LOGPROBS
from qr_llm_evals.models.providers import UNKNOWN_PROFILE, ProviderProfile
from qr_llm_evals.models.schema import (
    CONDITIONED_SOURCES,
    Arm,
    Conditioning,
    ModelRef,
    Sampling,
    Source,
)
from qr_llm_evals.models.seeds import derive_seed

# ── constants copied from sibling repositories (kept in lockstep) ────────────

#: SOURCE: qr-llm-api ``src/qr_llm_api/lanes.py`` ``CREATIVE_GUARD`` — owui's
#: creative-lane stability guard (cut the far tail so imperfect quantum draws
#: stop landing on garbage tokens). The public ``qr-llm`` lane sends exactly this.
CREATIVE_GUARD: Final[dict[str, float]] = {"qr_top_p": 0.95}

#: SOURCE: qr-llm-api ``src/qr_llm_api/lanes.py`` ``DETERMINISTIC_ENVELOPE`` —
#: the seeded control envelope owui's compare proxy composes on top of the
#: ``deterministic_prng`` preset (``qr_seed`` is added per request).
DETERMINISTIC_ENVELOPE: Final[dict[str, Any]] = {
    "qr_signal_amplifier_type": "zscore_mean",
    "qr_entropy_prefetch": False,
    "qr_zscore_calibration_samples": 0,
}

#: D-7: every qr-llm arm pins the deterministic envelope's amplifier triple,
#: including the QRNG arm, so all arms share one pipeline up to the source.
AMPLIFIER_PINS: Final[dict[str, Any]] = dict(DETERMINISTIC_ENVELOPE)

#: SOURCE: qr-sampler ``src/qr_sampler/config/model.py`` defaults
#: (``population_mean``, ``population_std``, ``sample_count``): the z-score
#: amplifier's reference mean and standard deviation of one byte, and the bytes
#: drawn per token. The harness never sends them except to bend an epsilon arm.
POPULATION_MEAN: Final[float] = 127.5
POPULATION_STD: Final[float] = 73.61215932167728
SAMPLE_COUNT: Final[int] = 10_000
#: Standard error of one token's byte mean, ``POPULATION_STD / sqrt(SAMPLE_COUNT)``.
SAMPLE_MEAN_SE: Final[float] = POPULATION_STD / SAMPLE_COUNT**0.5


def bent_population_mean(epsilon: float) -> float:
    """Reference mean that turns every ``u = Φ(z)`` into ``Φ(z + epsilon)``.

    The amplifier computes ``z = (M − μ0) / SE``; lowering ``μ0`` by
    ``epsilon · SE`` adds ``epsilon`` to every ``z``. The bend is meaning-blind
    and identical for every token, which is what a biased device would do.
    """
    return POPULATION_MEAN - float(epsilon) * SAMPLE_MEAN_SE


#: SOURCE: qr-llm-research ``client/qr_args.py`` ``REFUSED_QR_KEYS`` /
#: ``REFUSED_QR_PREFIXES`` — a local mirror of qr-sampler's server-side rejection
#: of non-per-request fields, extended with the keys THIS builder owns (source,
#: preset, seed, bypass, epsilon) and the diagnostic / draw-size knobs cards
#: must never set (spec §2.2 common body).
REFUSED_QR_KEYS: Final[frozenset[str]] = frozenset(
    {
        # infrastructure (qr-llm-research mirror)
        "qr_fallback_mode",
        "qr_entropy_source_instances",
        # owned by this builder
        "qr_entropy_source_type",
        "qr_preset",
        "qr_seed",
        "qr_bypass",
        "qr_bias_epsilon",
        "qr_population_mean",
        "qr_population_std",
        "qr_signal_amplifier_type",
        "qr_zscore_calibration_samples",
        "qr_entropy_prefetch",
        "qr_conditioning_mode",
        "qr_conditioning_hash",
        "qr_conditioning_in_bytes",
        "qr_conditioning_keep_bytes",
        # diagnostic / draw-size knobs
        "qr_diagnostic_mode",
        "qr_log_level",
        "qr_sample_count",
        "qr_draw_source_id",
    }
)
REFUSED_QR_PREFIXES: Final[tuple[str, ...]] = ("qr_grpc_", "qr_cb_", "qr_qrng_max_")

# ── the sampling / source tables (spec §2.2) ─────────────────────────────────

#: Sampling → (qr_preset, extra pins). Greedy has no preset (it bypasses the plugin).
SAMPLING_TABLE: Final[dict[Sampling, tuple[str | None, dict[str, Any]]]] = {
    "creative": ("creative_sampling", dict(CREATIVE_GUARD)),
    "t1_minp": ("normal_t1", {"qr_min_p_base": 0.10}),
    "greedy": (None, {"qr_bypass": True}),
}

#: Source → ``qr_entropy_source_type`` on the wire. ``provider`` has no qr envelope.
SOURCE_TYPES: Final[dict[Source, str]] = {
    "qrng": "quantum_grpc",
    "prng_seeded": "seeded_prng",
    "prng_system": "system",
    "prng_epsilon": "seeded_prng",
}

#: The only keys on which qr-llm arms of one study may differ (spec §5.2).
EQUIVALENCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "qr_entropy_source_type",
        "qr_seed",
        "qr_bias_epsilon",
        "qr_population_mean",
        "qr_conditioning_mode",
        "qr_conditioning_hash",
        "qr_conditioning_in_bytes",
        "qr_conditioning_keep_bytes",
    }
)


def conditioning_xargs(arm: Arm, conditioning: Conditioning | None) -> dict[str, Any]:
    """The ``qr_conditioning_*`` keys of ``arm`` under the study's conditioning.

    Empty for a raw (or absent) conditioning and for every source that does not
    draw from the entropy server.
    """
    if conditioning is None or conditioning.mode == "raw":
        return {}
    if arm.source not in CONDITIONED_SOURCES:
        return {}
    return {
        "qr_conditioning_mode": conditioning.mode,
        "qr_conditioning_hash": conditioning.hash,
        "qr_conditioning_in_bytes": conditioning.in_bytes,
        "qr_conditioning_keep_bytes": conditioning.keep_bytes,
    }


GREEDY_QRNG_REASON: Final[str] = "greedy consults no randomness"

#: Default per-token ``top_logprobs`` depth for qr-llm targets (spec §2.2). It is a
#: default, not a hard limit: deployments set ``QR_EVALS_TOP_LOGPROBS`` (``Settings.
#: top_logprobs``) up to :data:`MAX_TOP_LOGPROBS`, matched by the engine's
#: ``--max-logprobs``, and pass it to :func:`build_request_body`.
TOP_LOGPROBS: Final[int] = DEFAULT_TOP_LOGPROBS

#: Closest OpenAI-surface approximation of each sampling mode for external
#: providers. ``creative`` cannot be reproduced outside qr-sampler (HVH drift
#: is engine-side); its provider form is a fixed T=1 with the same far-tail guard.
PROVIDER_SAMPLING: Final[dict[Sampling, dict[str, float]]] = {
    "creative": {"temperature": 1.0, "top_p": 0.95},
    "t1_minp": {"temperature": 1.0, "min_p": 0.10},
    "greedy": {"temperature": 0.0},
}


class EnvelopeError(ValueError):
    """The arm / overrides cannot be turned into an honest envelope."""


@dataclass(frozen=True)
class SampleContext:
    """The per-request pairing key and card-level generation settings."""

    card_id: str
    prompt_id: str
    epoch: int
    max_tokens: int
    thinking: bool = False
    told_condition: str | None = None

    def sample_ref(self, arm_id: str) -> str:
        """``<arm_id>/<card_id>/<prompt_id>/<epoch>[/<told>]`` — the join key (store schema)."""
        parts = [arm_id, self.card_id, self.prompt_id, str(self.epoch)]
        if self.told_condition:
            parts.append(self.told_condition)
        return "/".join(parts)


def check_top_logprobs(depth: int) -> int:
    """Return ``depth`` if it is a valid per-token ``top_logprobs`` request, else raise.

    ``0`` asks for the chosen token's logprob only; the ceiling is
    :data:`MAX_TOP_LOGPROBS`. Whether the engine honours the depth is a readiness
    question (vLLM answers ``400`` above its ``--max-logprobs``).
    """
    if isinstance(depth, bool) or not isinstance(depth, int):
        raise EnvelopeError(f"top_logprobs must be an integer, got {depth!r}")
    if not 0 <= depth <= MAX_TOP_LOGPROBS:
        raise EnvelopeError(f"top_logprobs={depth} outside [0, {MAX_TOP_LOGPROBS}]")
    return depth


def validate_overrides(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """Refuse infrastructure / owned ``qr_*`` keys and non-``qr_*`` keys (fail before the first request)."""
    out = dict(overrides or {})
    bad = sorted(
        k
        for k in out
        if not k.startswith("qr_") or k in REFUSED_QR_KEYS or k.startswith(REFUSED_QR_PREFIXES)
    )
    if bad:
        raise EnvelopeError(
            f"refused qr_* override keys {bad}: the harness owns the entropy source, preset, "
            "seed, amplifier and bypass; infrastructure and diagnostic keys are never forwarded"
        )
    return out


def request_seed(arm: Arm, ctx: SampleContext) -> int | None:
    """The derived per-request seed for seeded arms, else ``None`` (D-6)."""
    if arm.study_seed is None:
        return None
    return derive_seed(arm.study_seed, ctx.card_id, ctx.prompt_id, ctx.epoch)


def build_envelope(
    arm: Arm,
    ctx: SampleContext,
    overrides: Mapping[str, Any] | None = None,
    *,
    conditioning: Conditioning | None = None,
) -> dict[str, Any]:
    """The ``vllm_xargs`` dict for one generation on a qr-llm target.

    Key order is fixed (source, preset, sampling pins, seed, amplifier pins,
    bent reference mean, conditioning, card overrides) so the JSON bytes are
    reproducible; tests pin them. ``conditioning`` is the study's
    (:func:`conditioning_xargs`).
    """
    if arm.source == "provider":
        raise EnvelopeError(f"arm {arm.id!r}: provider arms carry no qr_* envelope")
    if arm.sampling == "greedy" and arm.source == "qrng":
        raise EnvelopeError(f"arm {arm.id!r}: {GREEDY_QRNG_REASON}")
    extra = validate_overrides(overrides)

    preset, pins = SAMPLING_TABLE[arm.sampling]
    if arm.sampling == "greedy":
        # qr_bypass hands sampling to vLLM's native sampler; qr-sampler refuses
        # a seed alongside bypass, and no entropy is drawn, so nothing else is sent.
        return {**pins, **extra}

    envelope: dict[str, Any] = {"qr_entropy_source_type": SOURCE_TYPES[arm.source]}
    envelope["qr_preset"] = preset
    envelope.update(pins)
    seed = request_seed(arm, ctx)
    if seed is not None:
        envelope["qr_seed"] = seed
    envelope.update(AMPLIFIER_PINS)
    if arm.source == "prng_epsilon":
        # The engine has no epsilon hook (DEP-2); the bend is the amplifier's reference
        # mean, a per-request field. The arm stays `unavailable` until readiness sees it accepted.
        envelope["qr_population_mean"] = bent_population_mean(float(arm.epsilon or 0.0))
    envelope.update(conditioning_xargs(arm, conditioning))
    envelope.update(extra)
    return envelope


def token_log_xargs(request_id: str) -> dict[str, Any]:
    """The two keys that ask qr-sampler for this request's per-token log.

    They are log controls, not sampling settings: qr-sampler keeps them out of
    its configuration, so they change neither what is sampled nor its config
    hash. For the same reason they are added to the request's ``vllm_xargs``
    only, never to the envelope returned by :func:`build_envelope`, whose hash
    is what the pre-registration and the provenance record pin.
    """
    return {"qr_token_log": True, "qr_request_id": request_id}  # nosec B105 - flags, no secret


def envelope_json(envelope: Mapping[str, Any]) -> str:
    """Canonical bytes (sorted keys, no whitespace) — what :func:`envelope_hash` hashes."""
    return json.dumps(dict(envelope), sort_keys=True, separators=(",", ":"), allow_nan=False)


def envelope_hash(envelope: Mapping[str, Any]) -> str:
    """``sha256(envelope_json)``; stored in ``Provenance.envelope_hash``."""
    return hashlib.sha256(envelope_json(envelope).encode("utf-8")).hexdigest()


def pipeline_signature(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """The envelope with the entropy keys removed: what must be identical across arms."""
    return {k: v for k, v in envelope.items() if k not in EQUIVALENCE_KEYS}


def equivalence_violations(envelopes: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Readable reasons why the given ``{arm_id: envelope}`` set is not one pipeline.

    Empty ⇒ ``ReadinessReport.equivalence_ok``. Greedy (bypass) arms are
    excluded: they are sanity arms outside the pipeline by construction.
    """
    sigs = {
        arm_id: pipeline_signature(env)
        for arm_id, env in envelopes.items()
        if not env.get("qr_bypass")
    }
    if len(sigs) < 2:
        return []
    ref_id, ref = next(iter(sigs.items()))
    problems: list[str] = []
    for arm_id, sig in sigs.items():
        if sig == ref:
            continue
        keys = sorted(set(ref) | set(sig))
        diffs = [k for k in keys if ref.get(k, "<absent>") != sig.get(k, "<absent>")]
        problems.append(f"{arm_id} differs from {ref_id} on {diffs}")
    return problems


def _provider_sampling(
    arm: Arm, ctx: SampleContext, profile: ProviderProfile, top_logprobs: int = TOP_LOGPROBS
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key, value in PROVIDER_SAMPLING[arm.sampling].items():
        if profile.accepts(key):
            body[key] = value
    seed = request_seed(arm, ctx)
    if seed is not None and profile.accepts("seed"):
        body["seed"] = seed
    if profile.wants_logprobs:
        body["logprobs"] = True
        if profile.accepts("top_logprobs") and profile.top_logprobs_cap > 0:
            body["top_logprobs"] = min(top_logprobs, profile.top_logprobs_cap)
    return body


def build_request_body(
    arm: Arm,
    model: ModelRef,
    ctx: SampleContext,
    messages: list[dict[str, Any]],
    *,
    priority: int = 0,
    stream: bool = True,
    overrides: Mapping[str, Any] | None = None,
    profile: ProviderProfile | None = None,
    top_logprobs: int = TOP_LOGPROBS,
    token_log: bool = False,
    conditioning: Conditioning | None = None,
) -> dict[str, Any]:
    """The full chat-completions body (spec §2.2 common body).

    qr-llm targets get ``logprobs`` / ``top_logprobs`` / ``chat_template_kwargs`` /
    ``request_id`` / ``vllm_xargs`` / ``priority``; external targets get only what
    their :class:`ProviderProfile` accepts. The ``request_id`` is the sample's
    join key so engine-side token logs can be matched back (spec D-8).
    ``top_logprobs`` is the deployment's configured depth (default
    :data:`TOP_LOGPROBS`); provider arms are further capped by their profile.
    ``token_log`` asks the engine for the request's per-token log
    (:func:`token_log_xargs`); a greedy arm bypasses the sampler and has none.
    """
    if arm.model != model.id:
        raise EnvelopeError(f"arm {arm.id!r} targets {arm.model!r}, got model {model.id!r}")
    depth = check_top_logprobs(top_logprobs)
    body: dict[str, Any] = {
        "model": model.served_name,
        "messages": messages,
        "max_tokens": ctx.max_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}

    if model.provider == "qr_server":
        if arm.source == "provider":
            raise EnvelopeError(f"arm {arm.id!r}: source 'provider' is not a qr-llm source")
        envelope = build_envelope(arm, ctx, overrides, conditioning=conditioning)
        if arm.sampling == "greedy":
            body["temperature"] = 0.0
        elif token_log:
            envelope = {**envelope, **token_log_xargs(ctx.sample_ref(arm.id))}
        body.update(
            {
                "logprobs": True,
                "top_logprobs": depth,
                "chat_template_kwargs": {"enable_thinking": ctx.thinking},
                "request_id": ctx.sample_ref(arm.id),
                "vllm_xargs": envelope,
                "priority": priority,
            }
        )
        return body

    if arm.source != "provider":
        raise EnvelopeError(
            f"arm {arm.id!r}: source {arm.source!r} needs the pinned qr-llm target, "
            f"not provider {model.provider!r}"
        )
    body.update(_provider_sampling(arm, ctx, profile or UNKNOWN_PROFILE, depth))
    return body


__all__ = [
    "AMPLIFIER_PINS",
    "CREATIVE_GUARD",
    "DETERMINISTIC_ENVELOPE",
    "EQUIVALENCE_KEYS",
    "GREEDY_QRNG_REASON",
    "MAX_TOP_LOGPROBS",
    "POPULATION_MEAN",
    "POPULATION_STD",
    "PROVIDER_SAMPLING",
    "REFUSED_QR_KEYS",
    "REFUSED_QR_PREFIXES",
    "SAMPLE_COUNT",
    "SAMPLE_MEAN_SE",
    "SAMPLING_TABLE",
    "SOURCE_TYPES",
    "TOP_LOGPROBS",
    "EnvelopeError",
    "SampleContext",
    "bent_population_mean",
    "build_envelope",
    "build_request_body",
    "check_top_logprobs",
    "conditioning_xargs",
    "envelope_hash",
    "envelope_json",
    "equivalence_violations",
    "pipeline_signature",
    "request_seed",
    "token_log_xargs",
    "validate_overrides",
]
