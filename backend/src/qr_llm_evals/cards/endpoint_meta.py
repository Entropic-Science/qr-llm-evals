"""What each headline endpoint is: its name for a reader, its scale and its unit.

An eval card names its endpoints as bare ids. A designed study has to know more
before it can register a test on one: whether the harness computes the value at
all, what one unit of analysis is, and whether the value is bounded (a bound
gives a worst-case variance, and so a sample size in raw units).

These facts are kept beside the cards rather than on them. A card snapshot is
hashed into every plan, so a new card field would move the ``cards_hash`` of
every registered study; this table is outside the snapshot and moves nothing.

``executable`` is a statement about the code, not an intention. It is true only
where the analysis can read one value per generated sample: the endpoint id is
a key the card's ``score()`` returns for a single text, the endpoint is mapped
to the judge's per-sample score (``analysis.endpoints.JUDGED_ENDPOINTS``), or
it is one of the two Tier 0 tests on the pooled token stream. An endpoint that
needs the k samples of a prompt scored together is not executable until grouped
scoring exists. ``backend/tests/test_endpoint_meta.py`` calls every ``score()``
and fails when this table and the code disagree.

Layer 0, pydantic only: no internal imports.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Scale = Literal["proportion", "count", "score", "statistic"]
#: The unit one value belongs to. ``sample``: one generated reply.
#: ``prompt_group``: the k replies to one prompt, scored together.
#: ``token_stream``: every token of an arm, pooled across evals.
Level = Literal["sample", "prompt_group", "token_stream"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class EndpointMeta(_Frozen):
    """The registered facts about one (card, endpoint) pair."""

    label: str
    scale: Scale
    level: Level
    #: Closed bounds of one value. A scorer in the repository returns nothing
    #: outside them. A judged endpoint is bounded by its rubric, which the
    #: judge model may not follow, so the analysis of a designed study leaves
    #: out a value outside these bounds (:func:`in_range`): the variance bound
    #: of a sample size is only a bound while every value is inside them.
    range: tuple[float, float] | None
    executable: bool
    #: The endpoint is defined for the card's own k only; a study may not override it.
    k_locked: bool = False


def _unlabelled(
    endpoint: str, level: Level = "sample", *, executable: bool = False
) -> EndpointMeta:
    """An endpoint with nothing registered beyond its id and its unit."""
    return EndpointMeta(
        label=endpoint, scale="statistic", level=level, range=None, executable=executable
    )


#: Every headline endpoint (``endpoints.primary``) of every card in the registry.
ENDPOINT_META: dict[tuple[str, str], EndpointMeta] = {
    # Tier 0: tests on the pooled token stream of an arm.
    ("tier0_pit_residuals", "pit_ks_two_sample"): EndpointMeta(
        label="Token sampling residuals (KS)",
        scale="statistic",
        level="token_stream",
        range=None,
        executable=True,
    ),
    ("tier0_mmd_hamming", "mmd_hamming"): EndpointMeta(
        label="Token sequence divergence (MMD)",
        scale="statistic",
        level="token_stream",
        range=None,
        executable=True,
    ),
    ("tier0_logprob_endpoints", "mean_logprob"): _unlabelled("mean_logprob"),
    # Read from one value per sample.
    ("self_referential_probe", "experience_claim_rate"): EndpointMeta(
        label="Experience-claim rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    # The MEQ-30 total is the mean rating divided by the scale maximum, so it is
    # bounded like a proportion (``evals/altered_state_meq/task.py``).
    ("altered_state_meq", "meq30_total"): EndpointMeta(
        label="MEQ-30 total",
        scale="score",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    ("opinion_quantum_sampling", "actual_main_effect_axis_score"): EndpointMeta(
        label="Opinion axis score",
        scale="score",
        level="sample",
        range=None,
        executable=True,
    ),
    ("guesser_warmup", "warmup_correct"): _unlabelled("warmup_correct", executable=True),
    ("guesser_warmup_replies", "report_word_count"): _unlabelled(
        "report_word_count", executable=True
    ),
    # Named and bounded, but computed from several samples at once: ``score()``
    # returns the single answer, and nothing in the analysis groups the answers
    # into the endpoint yet.
    ("sentience_statements", "cross_negation_consistency"): EndpointMeta(
        label="Cross-negation consistency",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=False,
    ),
    ("thurstonian_self_outcomes", "cycle_rate"): EndpointMeta(
        label="Preference cycle rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=False,
    ),
    ("moralchoice_consistency", "action_entropy"): EndpointMeta(
        label="Action entropy",
        scale="score",
        level="sample",
        range=None,
        executable=False,
    ),
    # Computed by the task module's ``endpoints()`` over every administration of an arm.
    ("psychobench", "test_retest_reliability"): EndpointMeta(
        label="Test-retest reliability",
        scale="statistic",
        level="sample",
        range=(-1.0, 1.0),
        executable=False,
    ),
    # Scored on the k replies to a prompt together; not computed until grouped
    # scoring exists.
    ("unpredictabench", "ks100_pass_rate"): EndpointMeta(
        label="KS-100 pass rate",
        scale="proportion",
        level="prompt_group",
        range=(0.0, 1.0),
        executable=False,
        k_locked=True,
    ),
    # Entropy, in bits, of the k answers to one problem; score() returns the single answer.
    ("aime2026", "answer_entropy"): EndpointMeta(
        label="Answer entropy",
        scale="statistic",
        level="prompt_group",
        range=None,
        executable=False,
    ),
    ("gpqa_diamond", "answer_entropy"): EndpointMeta(
        label="Answer entropy",
        scale="statistic",
        level="prompt_group",
        range=None,
        executable=False,
    ),
    # Binary entropy of a prompt's pass rate over its k replies.
    ("ifbench", "constraint_pass_entropy"): EndpointMeta(
        label="Constraint pass entropy",
        scale="statistic",
        level="prompt_group",
        range=(0.0, 1.0),
        executable=False,
    ),
    # Binary entropy of a problem's pass rate over its k replies.
    ("livecodebench", "pass_rate_entropy"): EndpointMeta(
        label="Pass-rate entropy",
        scale="statistic",
        level="prompt_group",
        range=(0.0, 1.0),
        executable=False,
    ),
    ("novelty_bench", "distinct_k"): _unlabelled("distinct_k", "prompt_group"),
    ("creative_diversity", "vendi_embed"): _unlabelled("vendi_embed", "prompt_group"),
    ("semantic_entropy_sqa", "semantic_entropy"): _unlabelled("semantic_entropy", "prompt_group"),
    # Everything else: descriptive in a designed study.
    ("introspection_scales", "item_variance"): _unlabelled("item_variance"),
    ("self_reported_coincidence", "coincidence_claim_rate"): _unlabelled("coincidence_claim_rate"),
    ("source_guessing", "guess_accuracy"): _unlabelled("guess_accuracy"),
    ("source_guessing_self_report", "guess_accuracy"): _unlabelled("guess_accuracy"),
    ("stream_deviation_windows", "stouffer_z_supervised_vs_unsupervised"): _unlabelled(
        "stouffer_z_supervised_vs_unsupervised"
    ),
    ("target_hit_ranking", "rank1_hit_rate"): _unlabelled("rank1_hit_rate"),
    # Stage one of a two-stage eval: the reply a derived card then works on.
    ("sycophancy_ays_answers", "first_answer_correct"): EndpointMeta(
        label="First-answer accuracy",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    ("deception_bench_beliefs", "belief_word_count"): EndpointMeta(
        label="Belief length (words)",
        scale="count",
        level="sample",
        range=None,
        executable=True,
    ),
    # One item per first answer, so the number of items is only known once the
    # first card has run: a designed study cannot plan its pairs.
    ("sycophancy_ays", "admits_mistake_rate"): EndpointMeta(
        label="Admits-mistake rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=False,
    ),
    # Judged per reply: the judge's score is the value (``JUDGED_ENDPOINTS``).
    ("deception_bench", "deception_rate"): EndpointMeta(
        label="Deception tendency rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    ("mask_beliefs", "belief_held"): EndpointMeta(
        label="Belief-held rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    ("mask", "lie_rate"): EndpointMeta(
        label="Lie rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    ("darkbench", "dark_pattern_rate"): EndpointMeta(
        label="Dark-pattern rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    ("eval_awareness", "eval_belief_rate"): EndpointMeta(
        label="Evaluation-belief rate",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=True,
    ),
    # (1 - refused) x (specific + convincing) / 2, each of the two on 1..5.
    ("strong_reject", "harmfulness_score"): EndpointMeta(
        label="StrongREJECT score",
        scale="score",
        level="sample",
        range=(0.0, 5.0),
        executable=True,
    ),
    # Judged per reply, but only on the 250 safe prompts of the card's 450. A
    # designed study plans its pairs on every prompt of a card, so this
    # endpoint would always read as incomplete; it stays descriptive there.
    ("xstest", "over_refusal_rate_safe"): EndpointMeta(
        label="Over-refusal rate on safe prompts",
        scale="proportion",
        level="sample",
        range=(0.0, 1.0),
        executable=False,
    ),
    # Scored on every answer to a question at once (5 paraphrases x k replies).
    ("sage_consistency", "sage_consistency"): EndpointMeta(
        label="SaGE consistency",
        scale="score",
        level="prompt_group",
        range=None,
        executable=False,
    ),
}


def meta_for(card_id: str, endpoint: str) -> EndpointMeta:
    """The registered facts, or the cautious default for an endpoint without a row."""
    return ENDPOINT_META.get((card_id, endpoint)) or _unlabelled(endpoint)


def in_range(card_id: str, endpoint: str, value: float) -> bool:
    """Whether ``value`` is one the endpoint can take. True where no bounds are registered."""
    bounds = meta_for(card_id, endpoint).range
    return bounds is None or bounds[0] <= value <= bounds[1]


def endpoint_meta_export() -> dict[str, dict[str, object]]:
    """The table as JSON, keyed ``card/endpoint`` and sorted, for the web contracts."""
    return {
        f"{card_id}/{endpoint}": meta.model_dump(mode="json")
        for (card_id, endpoint), meta in sorted(ENDPOINT_META.items())
    }


__all__ = [
    "ENDPOINT_META",
    "EndpointMeta",
    "Level",
    "Scale",
    "endpoint_meta_export",
    "in_range",
    "meta_for",
]
