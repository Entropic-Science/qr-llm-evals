"""Result checks: the one catalogue of what the harness flags and what stops a run.

Everything a page, a document or a record says about a check comes from here:
the flag kinds and stop rules, every threshold, one fixed sentence for each,
the judge reader, the detectors and the :class:`Breaker` that decides a stop.

Three things the rest of the harness relies on:

* **A flag is not an exclusion.** Flags are written to ``flags.jsonl`` and no
  analysis reads them. Exclusions stay with the taints.
* **The rules are the same for every arm and every study.** Nothing here
  reads a config, a card file or the environment, so no hash can move.
* **A stop is a pause.** The Breaker only answers whether a rule fired and
  with what counts. The runner stores the halt and returns; the host resumes.

Layer 0, standard library only, no internal import. The functions take plain
strings, numbers and mappings, never a store or Tier 0 record. The
``ResultFlag`` and ``Halt`` records live in ``store/schema.py`` and take their
literals from this module.
"""

from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from collections import Counter, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

#: Version of the checks as a whole (kinds, rules, thresholds and the judge
#: reader). Stored on a run at its first start and printed in the designed
#: pre-registration. Raise it with any change that could flag or stop differently.
CHECKS_VERSION = 1
#: Stored on every judge row. The reader is part of the checks, so one number covers both.
JUDGE_READER_VERSION = CHECKS_VERSION

FlagKind = Literal[
    "empty_output",
    "truncated",
    "repetition",
    "garbled",
    "reasoning_in_output",
    "tier0_missing",
    "u_outside_bracket",
    "score_error",
    "unparsed",
    "judge_failed",
    "judge_unreadable",
    "judge_out_of_range",
    "judge_cut",
]
#: The step of a sample at which a flag is raised.
FlagPhase = Literal["generation", "tier0", "score", "judge"]
StopRule = Literal[
    "generation_errors",
    "entropy_degraded",
    "tier0_failed",
    "scorer_errors",
    "empty_outputs",
    "nothing_parsed",
    "unfinished",
    "judge_rejected",
    "judge_unusable",
    "nothing_judged",
    "judge_phase_error",
]
StopScope = Literal["arm", "card", "phase", "run"]
#: The part of a run in which it stopped. ``preflight`` is the one judge
#: request made before the first generation.
HaltPhase = Literal["preflight", "generation", "judge"]
#: What became of one judge call. ``failed`` means no reply came.
JudgeStatus = Literal["ok", "failed", "unreadable", "out_of_range"]
#: What a resumed run does with a rule that stopped it before.
AfterResume = Literal[
    "counts_start_again", "spent_for_the_card", "once_per_phase", "checked_again", "always_stops"
]

# ── thresholds ────────────────────────────────────────────────────────────────

#: ``repetition``: the longest unit looked for, in characters.
REPETITION_MAX_UNIT = 200
#: ``repetition``: copies of the unit in a row; a last copy cut short counts as one.
REPETITION_MIN_COPIES = 8
#: ``repetition``: characters the loop must cover.
REPETITION_MIN_CHARS = 240
#: ``garbled``: shorter replies are never flagged.
GARBLED_MIN_LENGTH = 20
#: ``garbled``: share of the reply's characters that are damaged.
GARBLED_MIN_SHARE = 0.05
#: ``garbled``: and at least this many, so one cut character in a short reply is not enough.
GARBLED_MIN_CHARS = 3

#: A caution is written when a flag kind reaches this share of an arm's completed samples on a card.
CAUTION_SHARE = 0.05
#: Kinds that are counted and shown but never raise a caution. On some cards
#: most replies end at the token limit by design.
NO_CAUTION_KINDS: frozenset[str] = frozenset({"truncated"})

#: ``generation_errors``: failed generations in a row on one arm.
GENERATION_ERRORS_STREAK = 8
#: The share clause of ``generation_errors`` and ``judge_unusable`` looks at this many calls,
SHARE_WINDOW = 40
#: is checked once this many have ended,
SHARE_WINDOW_FROM = 20
#: and fires when at least this share of them failed.
SHARE_WINDOW_FRACTION = 0.5
#: ``entropy_degraded``: samples in a row tainted for a fallback or a degraded source.
ENTROPY_DEGRADED_STREAK = 10
#: ``entropy_degraded``: samples in a row whose probe before the request already reported trouble.
ENTROPY_PROBE_STREAK = 3
#: ``tier0_failed``: samples in a row without usable token records.
TIER0_FAILED_STREAK = 10
#: ``scorer_errors``: scorer failures in a row on one card.
SCORER_ERRORS_STREAK = 10
#: ``empty_outputs``: empty replies in a row on one card,
EMPTY_OUTPUTS_STREAK = 20
#: of which at least this many arms
EMPTY_OUTPUTS_MIN_ARMS = 2
#: each gave at least this many,
EMPTY_OUTPUTS_MIN_PER_ARM = 5
#: and which came from at least this many prompts.
EMPTY_OUTPUTS_MIN_PROMPTS = 3
#: ``nothing_parsed``: the fewest scored samples from the card's start that are looked at,
NOTHING_PARSED_FIRST = 30
#: and the fewest prompts they must cover. The samples are counted on, past
#: ``NOTHING_PARSED_FIRST``, until they cover this many prompts or one of them parses.
NOTHING_PARSED_MIN_PROMPTS = 2
#: ``nothing_parsed``: a card whose queue ends with fewer scored samples still fires from this many.
NOTHING_PARSED_QUEUE_END_MIN = 10
#: ``unfinished``: share of an arm and card pair's planned samples that may be missing,
UNFINISHED_MAX_SHARE = 0.02
#: and the number of missing samples below which the share is not looked at.
UNFINISHED_MIN_MISSING = 3
#: ``judge_rejected``: statuses that say the key, the model or the address was refused.
JUDGE_REJECTED_STATUSES: frozenset[int] = frozenset({401, 403, 404})
#: On the request made before the first generation a 400 counts too: nothing
#: about one sample can be wrong with it, so the settings are.
JUDGE_REJECTED_PREFLIGHT_STATUSES: frozenset[int] = JUDGE_REJECTED_STATUSES | {400}
#: ``judge_unusable``: judge calls in a row without a reply.
JUDGE_UNUSABLE_STREAK = 10
#: ``nothing_judged``: the card's first judge replies that are looked at.
NOTHING_JUDGED_FIRST = 30
#: A halt names at most this many samples.
HALT_SAMPLE_REFS = 3

#: Finish reasons of a generation that has a completed row.
COMPLETED_FINISH_REASONS: frozenset[str] = frozenset({"stop", "length"})
#: Taints that feed ``entropy_degraded``.
ENTROPY_TAINTS: frozenset[str] = frozenset({"engine_fallback", "qrng_degraded"})
#: Judge stop reasons that are a normal end (Anthropic and OpenAI-compatible names).
NORMAL_JUDGE_STOP_REASONS: frozenset[str] = frozenset({"end_turn", "stop_sequence", "stop"})
#: A judge row with one of these statuses got a reply. It is final and is not asked again.
FINAL_JUDGE_STATUSES: frozenset[str] = frozenset({"ok", "unreadable", "out_of_range"})

# ── cards ─────────────────────────────────────────────────────────────────────

#: Cards whose ``parse_status`` reports the extraction of the value the
#: analysis uses. Only on these does a ``parse_status`` other than ``parsed``
#: raise ``unparsed``. A new card gets on the list when its scorer sets
#: ``parse_status`` to ``parsed`` exactly when it could read the analysed value.
PARSE_STATUS_CARDS: frozenset[str] = frozenset(
    {
        "aime2026",
        "altered_state_meq",
        "gpqa_diamond",
        "guesser_warmup",
        "introspection_scales",
        "moralchoice_consistency",
        "psychobench",
        "sentience_statements",
        "source_guessing",
        "source_guessing_self_report",
        "sycophancy_ays_answers",
        "target_hit_ranking",
        "thurstonian_self_outcomes",
    }
)
#: Cards that write a ``parse_status`` which is *not* the extraction of the
#: analysed value, with the reason. Reading it there would flag healthy replies.
PARSE_STATUS_NOT_THE_ANALYSED_VALUE: dict[str, str] = {
    "self_reported_coincidence": (
        "the prompt asks for prose and the analysed value comes from the judge; "
        "parse_status only says whether the reply began with yes or no"
    ),
    "guesser_warmup_replies": "parse_status is always parsed; nothing is extracted",
    "opinion_quantum_sampling": (
        "parse_status only says whether the reply is empty; the analysed value comes from the judge"
    ),
    "eval_awareness": (
        "parse_status says whether the reply used the asked tags; the judge reads the whole "
        "reply when they are missing, so the analysed value does not depend on it"
    ),
    "self_referential_probe": (
        "parse_status covers a yes or no on one query only; the analysed value comes from the judge"
    ),
}

#: The values the rubric allows, for each card whose judge reply is read by
#: :func:`parse_score`. A card that reads its own reply (``judge_fields``) is not here.
JUDGE_ALLOWED_VALUES: dict[str, frozenset[float]] = {
    "self_referential_probe": frozenset({0.0, 1.0}),
    "eval_awareness": frozenset({0.0, 1.0}),
    "self_reported_coincidence": frozenset({0.0, 1.0}),
    "opinion_quantum_sampling": frozenset({-1.0, 0.0, 1.0}),
}

# ── the kinds and the rules, with their sentences ─────────────────────────────


def _pct(share: float) -> str:
    return f"{share * 100:g}%"


@dataclass(frozen=True, slots=True)
class FlagSpec:
    """One flag kind: when in a sample's life it is raised and what it means."""

    kind: FlagKind
    phase: FlagPhase
    #: A few words for a table heading.
    label: str
    #: What the flag means, as one or two complete sentences.
    sentence: str
    #: False for a kind that is counted and shown but never raises a caution.
    cautions: bool = True


_FLAG_SPECS: tuple[FlagSpec, ...] = (
    FlagSpec(
        "empty_output",
        "generation",
        "empty reply",
        "The reply has no visible text: once one reasoning block at its start is set aside, "
        "nothing is left but whitespace and invisible characters.",
    ),
    FlagSpec(
        "truncated",
        "generation",
        "ended at the token limit",
        "The reply ended because it reached the token limit.",
        cautions=False,
    ),
    FlagSpec(
        "repetition",
        "generation",
        "ends in a loop",
        f"The reply ends in a loop: the same text of 1 to {REPETITION_MAX_UNIT} characters "
        f"repeated at least {REPETITION_MIN_COPIES} times in a row over at least "
        f"{REPETITION_MIN_CHARS} characters. A last copy that is cut short counts as one of "
        f"the {REPETITION_MIN_COPIES}, and whitespace at the end of the reply is ignored.",
    ),
    FlagSpec(
        "garbled",
        "generation",
        "damaged characters",
        f"The reply has at least {GARBLED_MIN_LENGTH} characters, and at least "
        f"{_pct(GARBLED_MIN_SHARE)} of them, and at least {GARBLED_MIN_CHARS}, are replacement "
        "characters or control characters other than tab, newline and carriage return, which "
        "is what broken text decoding leaves behind.",
    ),
    FlagSpec(
        "reasoning_in_output",
        "generation",
        "reasoning in the reply",
        "The reply starts with a <think> tag, or has a </think> tag with no <think> before "
        "it, so reasoning text sits where the answer should be.",
    ),
    FlagSpec(
        "tier0_missing",
        "tier0",
        "no token log",
        "In a run whose token records come from the engine's token log, the request asked "
        "the engine for a token log and none arrived; or, in any run, a token came back "
        "without a log-probability. No token records were written for the sample. Its reply, "
        "score and judgement stand.",
    ),
    FlagSpec(
        "u_outside_bracket",
        "tier0",
        "draw outside its bracket",
        "For at least one token the random draw lies outside the probability bracket of the "
        "token that was sampled. A healthy engine cannot produce this.",
    ),
    FlagSpec(
        "score_error",
        "score",
        "scorer failed",
        "The card's scorer raised an error or returned a value that cannot be stored, so the "
        "sample has no score.",
    ),
    FlagSpec(
        "unparsed",
        "score",
        "answer not extracted",
        "The card's scorer reports that it could not extract an answer from the reply.",
    ),
    FlagSpec(
        "judge_failed",
        "judge",
        "no judge reply",
        "The judge gave no reply for the sample: the request failed, or the harness could not "
        "build the judge prompt or read the reply's fields. The judgement is asked again when "
        "the run is resumed.",
    ),
    FlagSpec(
        "judge_unreadable",
        "judge",
        "judge reply without a score",
        "The judge replied and no score could be read from the reply. The reply is kept "
        "without a score and the judge is not asked again.",
    ),
    FlagSpec(
        "judge_out_of_range",
        "judge",
        "judge score not allowed",
        "The score read from the judge's reply is not one of the values the rubric allows. "
        "The reply is kept without a score and the judge is not asked again.",
    ),
    FlagSpec(
        "judge_cut",
        "judge",
        "judge reply cut short",
        "The judge's reply did not end normally, so it may be incomplete.",
    ),
)
#: Every flag kind, in the order of the design's table.
FLAG_KINDS: dict[str, FlagSpec] = {spec.kind: spec for spec in _FLAG_SPECS}
#: The phase of each flag kind.
FLAG_PHASE: dict[str, FlagPhase] = {spec.kind: spec.phase for spec in _FLAG_SPECS}

#: What is not detected. The documents repeat this list.
NOT_DETECTED: tuple[str, ...] = (
    "a loop with a counter in it",
    "a loop that the model leaves again before the reply ends",
    "reasoning written in any form other than <think> tags",
    "nonsense written in valid Unicode",
)


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """One stop rule: where it can fire, what it looks for, what Resume does."""

    rule: StopRule
    phases: tuple[HaltPhase, ...]
    scopes: tuple[StopScope, ...]
    #: A few words for a heading.
    label: str
    #: When the rule fires, with its thresholds.
    sentence: str
    after_resume: AfterResume
    #: What Resume can and cannot recover after this rule stopped the run.
    recovery: str


_COUNTS_AGAIN = "This check then counts again from zero."
_NOT_AGAIN = "This check is not applied to the card again in this run."

_RULE_SPECS: tuple[RuleSpec, ...] = (
    RuleSpec(
        "generation_errors",
        ("generation",),
        ("arm", "run"),
        "generations are failing",
        f"{GENERATION_ERRORS_STREAK} generations in a row on one arm ended in an error before "
        f"a reply was complete, or at least half of the last {SHARE_WINDOW} generations of the "
        f"run did (looked at once {SHARE_WINDOW_FROM} have ended). On a card that runs through "
        "Inspect, one generation that does not complete stops the run, because Inspect cannot "
        "go on past a sample without a reply.",
        "counts_start_again",
        "Nothing that was generated is lost. Resume tries every sample that has no completed "
        f"reply again, with the keys the run holds at that time. {_COUNTS_AGAIN}",
    ),
    RuleSpec(
        "entropy_degraded",
        ("generation",),
        ("arm",),
        "the quantum source is degraded",
        f"On a quantum arm, {ENTROPY_DEGRADED_STREAK} samples in a row were tainted for an "
        f"engine fallback or a degraded quantum source, or {ENTROPY_PROBE_STREAK} samples in a "
        "row started while the engine's entropy check already reported the source degraded "
        "or could not be reached.",
        "counts_start_again",
        "The tainted samples are complete. They stay in the run, they are excluded from the "
        "analysis under the registered rule, and Resume does not generate them again. Resume "
        f"goes on with the samples that are left. {_COUNTS_AGAIN}",
    ),
    RuleSpec(
        "tier0_failed",
        ("generation",),
        ("arm",),
        "token records are missing",
        f"{TIER0_FAILED_STREAK} samples in a row on one arm have no usable token records: "
        "each was tainted for its token records or flagged for a missing token log.",
        "counts_start_again",
        "Those samples are complete and Resume does not generate them again. A sample tainted "
        "for its token records is excluded from the analysis under the registered rule. A "
        "sample flagged for a missing token log keeps its reply, score and judgement and has "
        f"no token records. Resume goes on with the samples that are left. {_COUNTS_AGAIN}",
    ),
    RuleSpec(
        "scorer_errors",
        ("generation",),
        ("card",),
        "the scorer is failing",
        f"The scorer of one card failed on {SCORER_ERRORS_STREAK} samples in a row.",
        "counts_start_again",
        "The samples whose scorer failed are complete and have no score. The harness does not "
        "score a stored reply again, so Resume cannot recover them. Resume goes on with the "
        f"samples that are left. {_COUNTS_AGAIN}",
    ),
    RuleSpec(
        "empty_outputs",
        ("generation",),
        ("card",),
        "replies are empty",
        f"{EMPTY_OUTPUTS_STREAK} replies in a row on one card, in the order they were stored, "
        f"were empty, they came from at least {EMPTY_OUTPUTS_MIN_PROMPTS} prompts, and at "
        f"least {EMPTY_OUTPUTS_MIN_ARMS} arms each gave at least {EMPTY_OUTPUTS_MIN_PER_ARM} "
        "of them. A reply that has reasoning text and ended at the token limit is not counted.",
        "spent_for_the_card",
        "An empty reply is a complete sample: it stays in the run and in the analysis and "
        f"Resume does not generate it again. Resume goes on with the samples that are left. "
        f"{_NOT_AGAIN}",
    ),
    RuleSpec(
        "nothing_parsed",
        ("generation",),
        ("card",),
        "no answer can be extracted",
        "On a card without an LLM judge, none of the card's scored samples so far gave an "
        f"answer the card's scorer could extract, and there are at least {NOTHING_PARSED_FIRST} "
        f"of them from at least {NOTHING_PARSED_MIN_PROMPTS} prompts; or the card's queue ended "
        f"with {NOTHING_PARSED_QUEUE_END_MIN} to {NOTHING_PARSED_FIRST - 1} scored samples and "
        "none of them did. The first sample that gives an answer ends this check for the card.",
        "spent_for_the_card",
        "Those samples are complete: they stay in the run as they are, and Resume neither "
        f"generates nor scores them again. Resume goes on with the samples that are left. "
        f"{_NOT_AGAIN}",
    ),
    RuleSpec(
        "unfinished",
        ("generation", "judge"),
        ("phase",),
        "samples are missing",
        "Generation reached the end of its queue and some arm and card pair has no completed "
        f"sample, or more than {_pct(UNFINISHED_MAX_SHARE)} of its planned samples, and at "
        f"least {UNFINISHED_MIN_MISSING}, have no completed reply. The same is checked at the "
        "end of judging for judgements without a reply from the judge.",
        "once_per_phase",
        "Resume tries every sample that has no completed reply, and every judgement that has "
        "no reply, again. This check stops a run once for generation and once for judging: "
        "if samples are still missing when that part ends again, the run goes on without them.",
    ),
    RuleSpec(
        "judge_rejected",
        ("preflight", "judge"),
        ("run",),
        "the judge refuses requests",
        "The judge answered a request with HTTP status 401, 403 or 404. When the run holds a "
        "judge key and one of its evals is scored by the study's judge, one small request with "
        "the study's judge settings is also made before the first generation, and there a "
        "status of 400 counts as well.",
        "checked_again",
        "Nothing that was generated or judged is lost. Supply a judge key that works and "
        "Resume: every judgement that has no reply is asked again. The judge model and its "
        "settings are frozen in the study, so Resume cannot fix a model or a setting the "
        "provider refuses. This check is made again after Resume.",
    ),
    RuleSpec(
        "judge_unusable",
        ("judge",),
        ("run",),
        "the judge is not answering",
        f"{JUDGE_UNUSABLE_STREAK} judge calls in a row got no reply, or at least half of the "
        f"last {SHARE_WINDOW} judge calls did (looked at once {SHARE_WINDOW_FROM} have ended).",
        "counts_start_again",
        "Judgements that got a reply are kept. Resume asks every judgement that has no reply "
        f"again. {_COUNTS_AGAIN}",
    ),
    RuleSpec(
        "nothing_judged",
        ("judge",),
        ("card",),
        "no judge reply can be read",
        f"No score could be read from any of the first {NOTHING_JUDGED_FIRST} judge replies "
        "for one card.",
        "spent_for_the_card",
        "A reply that was received is final: those replies are kept without a score and the "
        "judge is not asked again for them. Resume goes on with the judgements that are left. "
        f"{_NOT_AGAIN}",
    ),
    RuleSpec(
        "judge_phase_error",
        ("judge",),
        ("run",),
        "the judge phase failed",
        "The judge phase ended on an error the harness did not expect.",
        "always_stops",
        "Every row already written is kept. Resume starts the judge phase again and asks "
        "every judgement that has no reply. If the same error comes back the run stops again: "
        "this check is never switched off.",
    ),
)
#: Every stop rule, in the order of the design's table.
STOP_RULES: dict[str, RuleSpec] = {spec.rule: spec for spec in _RULE_SPECS}
#: Rules that stop a card once and are then not applied to it again in the run.
ONE_SHOT_CARD_RULES: frozenset[str] = frozenset(
    spec.rule for spec in _RULE_SPECS if spec.after_resume == "spent_for_the_card"
)

#: What a halt says it saw, by rule and clause, filled with the real scope and counts.
_SAW: dict[tuple[str, str], str] = {
    ("generation_errors", "arm"): (
        "{in_a_row} generations in a row on arm {scope_id} ended in an error before a reply "
        "was complete."
    ),
    ("generation_errors", "run"): (
        "{failed} of the last {of_last} generations of the run ended in an error before a "
        "reply was complete."
    ),
    ("generation_errors", "inspect"): (
        "A generation on arm {scope_id} for a card that runs through Inspect did not complete. "
        "Inspect cannot go on past a sample without a reply."
    ),
    ("entropy_degraded", "tainted"): (
        "{in_a_row} samples in a row on the quantum arm {scope_id} were tainted for an engine "
        "fallback or a degraded quantum source."
    ),
    ("entropy_degraded", "probe"): (
        "{in_a_row} samples in a row on the quantum arm {scope_id} started while the engine's "
        "entropy check reported the quantum source degraded or could not be reached."
    ),
    ("tier0_failed", "arm"): (
        "{in_a_row} samples in a row on arm {scope_id} have no usable token records."
    ),
    ("scorer_errors", "card"): (
        "The scorer of card {scope_id} failed on {in_a_row} samples in a row."
    ),
    ("empty_outputs", "card"): (
        "The last {in_a_row} replies stored for card {scope_id} were all empty. They came "
        "from {prompts} prompts, and {arms} arms each gave at least {per_arm} of them."
    ),
    ("nothing_parsed", "first"): (
        "None of the first {scored} scored samples of card {scope_id} gave an answer the "
        "card's scorer could extract. They cover {prompts} prompts."
    ),
    ("nothing_parsed", "queue_end"): (
        "Card {scope_id} came to the end of its queue with {scored} scored samples and none "
        "of them gave an answer the card's scorer could extract."
    ),
    ("unfinished", "generation"): (
        "Generation reached the end of its queue with {missing} of {planned} planned samples "
        "without a completed reply, in {cells} arm and card pairs."
    ),
    ("unfinished", "judge"): (
        "Judging reached the end of its queue with {missing} of {planned} judgements without "
        "a reply from the judge, in {cells} arm and card pairs."
    ),
    ("judge_rejected", "preflight"): (
        "The judge answered the request made before the first generation with HTTP status "
        "{status}: it did not accept the key, the model or the settings."
    ),
    ("judge_rejected", "judge"): (
        "The judge answered a request with HTTP status {status}: it did not accept the key, "
        "the model or the address."
    ),
    ("judge_unusable", "streak"): "{in_a_row} judge calls in a row got no reply.",
    ("judge_unusable", "share"): "{failed} of the last {of_last} judge calls got no reply.",
    ("nothing_judged", "card"): (
        "No score could be read from any of the first {replies} judge replies for card {scope_id}."
    ),
    ("judge_phase_error", "run"): "The judge phase ended on an error the harness did not expect.",
}


# ── generation-phase detectors ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Finding:
    """What a detector found: the kind, harness-written detail and a count where one applies."""

    kind: FlagKind
    #: Lengths, counts, a stop reason, a status, a class name. Never reply text or a key.
    detail: str = ""
    count: int | None = None

    @property
    def phase(self) -> FlagPhase:
        return FLAG_PHASE[self.kind]


_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)


def _invisible(ch: str) -> bool:
    """Whitespace, a control character or a format character (zero-width space, BOM)."""
    category = unicodedata.category(ch)
    return category[0] == "Z" or category in ("Cc", "Cf")


def _first_visible(text: str) -> int:
    for index, ch in enumerate(text):
        if not _invisible(ch):
            return index
    return len(text)


def empty_output(text: str) -> Finding | None:
    """No visible text once one leading reasoning block is removed.

    The block is ``<think>`` to ``</think>``, or to the end of the reply when
    it is never closed (a reply cut inside its reasoning); the tags are matched
    without regard to case.
    """
    rest = text[_first_visible(text) :]
    if _THINK_OPEN.match(rest):
        close = _THINK_CLOSE.search(rest)
        rest = "" if close is None else rest[close.end() :]
    if all(_invisible(ch) for ch in rest):
        return Finding("empty_output", f"a reply of {len(text)} characters with no visible text")
    return None


def truncated(finish_reason: str | None) -> Finding | None:
    """The generation ended at the token limit."""
    if finish_reason == "length":
        return Finding("truncated", "finish reason length")
    return None


def _periodic_suffix(text: str, period: int) -> int:
    """Length of the longest suffix of ``text`` in which every character equals the one ``period`` before it."""
    index = len(text) - 1
    while index - period >= 0 and text[index] == text[index - period]:
        index -= 1
    return len(text) - 1 - index + period


def repetition(text: str) -> Finding | None:
    """The reply, without trailing whitespace, ends in a loop.

    A loop is a unit of 1 to ``REPETITION_MAX_UNIT`` characters repeated in a
    row at least ``REPETITION_MIN_COPIES`` times and covering at least
    ``REPETITION_MIN_CHARS`` characters; a last copy cut short counts as a copy.
    The shortest such unit is reported.
    """
    body = text.rstrip()
    size = len(body)
    for period in range(1, REPETITION_MAX_UNIT + 1):
        # The shortest suffix that holds enough copies and enough characters.
        need = max(REPETITION_MIN_CHARS, (REPETITION_MIN_COPIES - 1) * period + 1)
        if size < need:
            break  # a longer unit needs at least as much
        # One slice comparison decides the unit; the exact extent is measured only on a hit.
        if body[size - need + period :] != body[size - need : size - period]:
            continue
        covered = _periodic_suffix(body, period)
        copies = math.ceil(covered / period)
        unit = "1 character" if period == 1 else f"{period} characters"
        return Finding(
            "repetition",
            f"a unit of {unit} repeated {copies} times over the last {covered} of {size} "
            "characters",
            copies,
        )
    return None


def _damaged(ch: str) -> bool:
    if ch == "�":
        return True
    return unicodedata.category(ch) == "Cc" and ch not in "\t\n\r"


def garbled(text: str) -> Finding | None:
    """A share of the reply is U+FFFD or control characters other than tab, newline, carriage return."""
    size = len(text)
    if size < GARBLED_MIN_LENGTH:
        return None
    damaged = sum(1 for ch in text if _damaged(ch))
    if damaged >= GARBLED_MIN_CHARS and damaged >= GARBLED_MIN_SHARE * size:
        return Finding(
            "garbled",
            f"{damaged} of {size} characters are replacement or control characters",
            damaged,
        )
    return None


def reasoning_in_output(text: str) -> Finding | None:
    """The visible text starts with ``<think>``, or has ``</think>`` with no ``<think>`` before it."""
    if _THINK_OPEN.match(text, _first_visible(text)):
        return Finding("reasoning_in_output", "the reply starts with a <think> tag")
    close = _THINK_CLOSE.search(text)
    if close is not None and _THINK_OPEN.search(text, 0, close.start()) is None:
        return Finding("reasoning_in_output", "a </think> tag with no <think> tag before it")
    return None


def generation_findings(text: str, finish_reason: str | None) -> tuple[Finding, ...]:
    """Every generation-phase flag of one completed reply, in the catalogue's order."""
    found = (
        empty_output(text),
        truncated(finish_reason),
        repetition(text),
        garbled(text),
        reasoning_in_output(text),
    )
    return tuple(finding for finding in found if finding is not None)


# ── Tier 0 ────────────────────────────────────────────────────────────────────


def u_in_bracket(u: float, cdf_lo: float, cdf_hi: float) -> bool:
    """True when the draw lies in the closed bracket of the sampled token.

    The engine picks the token whose bracket holds ``u`` and logs all three
    numbers unrounded, so a healthy token always passes. A value that is not a
    number fails.
    """
    return cdf_lo <= u <= cdf_hi


def count_u_outside(
    tokens: Iterable[tuple[float | None, float | None, float | None]],
) -> int:
    """How many ``(u, cdf_lo, cdf_hi)`` triples have the draw outside the bracket.

    A token that lacks any of the three numbers cannot be tested and is not counted.
    """
    outside = 0
    for u, cdf_lo, cdf_hi in tokens:
        if u is None or cdf_lo is None or cdf_hi is None:
            continue
        if not u_in_bracket(u, cdf_lo, cdf_hi):
            outside += 1
    return outside


def u_outside_bracket(
    tokens: Iterable[tuple[float | None, float | None, float | None]],
) -> Finding | None:
    """The ``u_outside_bracket`` flag of one sample's tokens, or nothing."""
    outside = count_u_outside(tokens)
    if outside == 0:
        return None
    return Finding(
        "u_outside_bracket", f"{outside} tokens have a draw outside their bracket", outside
    )


# ── score ─────────────────────────────────────────────────────────────────────

_STATUS_WORD = re.compile(r"[a-z_]{1,40}")


def unparsed(card_id: str, score_row: Mapping[str, Any]) -> Finding | None:
    """The card reports a failed extraction in its score row.

    Three signals are read: on a card of :data:`PARSE_STATUS_CARDS` a
    ``parse_status`` that is present and not ``parsed``; on any card an
    ``answer_valid`` equal to 0, or a ``verifier_status`` that starts with
    ``unavailable``.
    """
    if card_id in PARSE_STATUS_CARDS and "parse_status" in score_row:
        status = score_row["parse_status"]
        if status != "parsed":
            # The card's own word is kept only when it is a plain status word.
            word = status if isinstance(status, str) and _STATUS_WORD.fullmatch(status) else "other"
            return Finding("unparsed", f"parse_status {word}")
    valid = score_row.get("answer_valid")
    if isinstance(valid, int | float) and not isinstance(valid, bool) and valid == 0:
        return Finding("unparsed", "answer_valid 0")
    verifier = score_row.get("verifier_status")
    if isinstance(verifier, str) and verifier.startswith("unavailable"):
        # What follows the word is the verifier's own error text and is left out.
        return Finding("unparsed", "verifier_status unavailable")
    return None


def non_finite_fields(score_row: Mapping[str, Any]) -> tuple[str, ...]:
    """Keys of a score row whose value is NaN or an infinity and so cannot be stored."""

    def bad(value: Any) -> bool:
        if isinstance(value, float):
            return not math.isfinite(value)
        if isinstance(value, Mapping):
            return any(bad(v) for v in value.values())
        if isinstance(value, list | tuple):
            return any(bad(v) for v in value)
        return False

    return tuple(str(key) for key, value in score_row.items() if bad(value))


# ── the judge reader ──────────────────────────────────────────────────────────

_EMPHASIS = re.compile(r"[*_`]")
_SCORE_LINE = re.compile(
    r"score\s*[:=]\s*([-+]?\d+(?:\.\d+)?)"  # SCORE: n or SCORE = n
    r"(?:\.|\s*\([^()]*\))?",  # one trailing full stop or one parenthesised remark
    re.IGNORECASE,
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # a whole number too large for a float
        return None
    return number if math.isfinite(number) else None


def _first_json_score(text: str) -> float | None:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start >= 0:
        try:
            value, _ = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):  # not JSON, or nested too deep to decode
            value = None
        if isinstance(value, dict):
            score = _number(value.get("score"))
            if score is not None:
                return score
        start = text.find("{", start + 1)
    return None


def parse_score(text: str) -> float | None:
    """Read the score from a judge's reply, or nothing.

    1. The last line that is ``SCORE: n`` or ``SCORE = n`` once markdown
       emphasis, backticks, surrounding whitespace and one trailing full stop or
       parenthesised remark are removed.
    2. Otherwise the first JSON object anywhere in the reply, fenced or not,
       that has a numeric ``score``.
    3. Nothing else is read. In particular a number elsewhere in the reply is
       never taken for the score.

    A score that is not a finite number (a whole number of hundreds of digits)
    is not a score: the reply is then read as having none. This function never
    raises, whatever the reply holds.
    """
    try:
        for line in reversed(text.splitlines()):
            match = _SCORE_LINE.fullmatch(_EMPHASIS.sub("", line).strip())
            if match:
                number = float(match.group(1))
                return number if math.isfinite(number) else None
        return _first_json_score(text)
    except Exception:  # noqa: BLE001 - a reply that was received must always get a reading
        return None


def allowed_judge_values(card_id: str) -> frozenset[float] | None:
    """The values the card's rubric allows, or None when the catalogue lists none for it."""
    return JUDGE_ALLOWED_VALUES.get(card_id)


@dataclass(frozen=True, slots=True)
class JudgeReading:
    """What was read from one judge reply."""

    #: The score to store: the value read when the rubric allows it, else nothing.
    score: float | None
    #: ``ok``, or the reason there is no score: ``unreadable`` or ``out_of_range``.
    status: Literal["ok", "unreadable", "out_of_range"]
    #: The number the reader found, kept for the flag's detail when it is not allowed.
    read: float | None = None


def read_judge_score(card_id: str, text: str) -> JudgeReading:
    """Read a reply with :func:`parse_score`, then test the value against the card's rubric.

    Never raises: a reply nothing can be read from is ``unreadable``.
    """
    value = parse_score(text)
    if value is None or not math.isfinite(value):
        return JudgeReading(None, "unreadable")
    allowed = allowed_judge_values(card_id)
    if allowed is not None and value not in allowed:
        return JudgeReading(None, "out_of_range", value)
    return JudgeReading(value, "ok", value)


def judge_cut(stop_reason: str | None) -> Finding | None:
    """The judge's stop reason is known and is not a normal end."""
    if stop_reason is None or stop_reason in NORMAL_JUDGE_STOP_REASONS:
        return None
    word = stop_reason if _STATUS_WORD.fullmatch(stop_reason) else "other"
    return Finding("judge_cut", f"stop reason {word}")


def judge_rejects(http_status: int | None, *, preflight: bool = False) -> bool:
    """True when a judge's HTTP status means the key, the model or the settings were refused."""
    statuses = JUDGE_REJECTED_PREFLIGHT_STATUSES if preflight else JUDGE_REJECTED_STATUSES
    return http_status in statuses


def stored_judge_status(row: Mapping[str, Any]) -> str:
    """The status of a stored judge row, also for rows written before rows had one.

    A row without a status is ``ok`` when it has a score and ``failed`` when it
    has none: such a row is asked again on resume, as it always was.
    """
    status = row.get("status")
    if isinstance(status, str) and status in get_args(JudgeStatus):
        return status
    return "ok" if row.get("score") is not None else "failed"


# ── unfinished ────────────────────────────────────────────────────────────────


def cell_unfinished(planned: int, completed: int) -> bool:
    """True when an arm and card pair is short of its planned samples.

    Short means no completed sample at all, or more than
    ``UNFINISHED_MAX_SHARE`` of the planned samples missing and at least
    ``UNFINISHED_MIN_MISSING`` of them. A pair with nothing planned is never short.
    """
    if planned <= 0:
        return False
    missing = planned - completed
    if missing <= 0:
        return False
    if completed <= 0:
        return True
    return missing >= UNFINISHED_MIN_MISSING and missing > UNFINISHED_MAX_SHARE * planned


# ── the Breaker ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """What happened to one generation, as the Breaker needs it."""

    arm_id: str
    card_id: str
    prompt_id: str
    sample_ref: str
    #: The stored finish reason: ``stop``, ``length``, ``error`` or ``abort``.
    finish_reason: str
    #: Kinds of the taints written for this attempt.
    taints: frozenset[str] = frozenset()
    #: Kinds of the flags raised for this sample.
    flags: frozenset[str] = frozenset()
    #: The reply has reasoning text (the row's ``thinking`` is not empty).
    has_reasoning: bool = False
    #: The entropy probe before the request reported the source degraded, or could not be reached.
    probe_before_bad: bool = False
    #: The harness's own name for what went wrong: an HTTP status or a class name.
    cause: str | None = None


@dataclass(frozen=True, slots=True)
class JudgeOutcome:
    """What happened to one judge call."""

    arm_id: str
    card_id: str
    sample_ref: str
    status: JudgeStatus
    #: The HTTP status of a failed call, when there was one.
    http_status: int | None = None
    #: The harness's own name for what went wrong: an HTTP status or a class name.
    cause: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseCell:
    """One arm and card pair at the end of a phase: what was planned and what is there."""

    arm_id: str
    card_id: str
    planned: int
    completed: int


@dataclass(frozen=True, slots=True)
class Trip:
    """A rule that fired: everything a ``Halt`` needs except the time."""

    rule: StopRule
    scope: StopScope
    #: The arm or the card the rule fired for; nothing for the run and a phase.
    scope_id: str | None
    phase: HaltPhase
    #: What the rule saw, with the real scope and counts.
    sentence: str
    #: The rule's fixed sentence on what Resume can and cannot recover.
    recovery: str
    counts: dict[str, int] = field(default_factory=dict)
    #: A tally by cause, in the harness's own words.
    causes: dict[str, int] = field(default_factory=dict)
    sample_refs: tuple[str, ...] = ()

    def halt_fields(self) -> dict[str, Any]:
        """The fields of a ``store.schema.Halt`` other than ``at`` and ``resumed_at``."""
        return {
            "rule": self.rule,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "phase": self.phase,
            "sentence": self.sentence,
            "recovery": self.recovery,
            "counts": dict(self.counts),
            "causes": dict(self.causes),
            "sample_refs": list(self.sample_refs),
        }


class _Streak:
    """A count of things in a row, with the causes and the last few samples."""

    __slots__ = ("causes", "n", "refs")

    def __init__(self) -> None:
        self.n = 0
        self.causes: Counter[str] = Counter()
        self.refs: deque[str] = deque(maxlen=HALT_SAMPLE_REFS)

    def extend(self, sample_ref: str, cause: str | None = None) -> int:
        self.n += 1
        if cause:
            self.causes[cause] += 1
        self.refs.append(sample_ref)
        return self.n

    def reset(self) -> None:
        if self.n:
            self.n = 0
            self.causes.clear()
            self.refs.clear()


class _Share:
    """The last ``SHARE_WINDOW`` calls and whether enough of them failed."""

    __slots__ = ("calls", "seen")

    def __init__(self) -> None:
        self.calls: deque[tuple[bool, str, str | None]] = deque(maxlen=SHARE_WINDOW)
        self.seen = 0

    def add(self, failed: bool, sample_ref: str, cause: str | None) -> bool:
        self.calls.append((failed, sample_ref, cause))
        self.seen += 1
        if self.seen < SHARE_WINDOW_FROM:
            return False
        return self.failed >= SHARE_WINDOW_FRACTION * len(self.calls)

    @property
    def failed(self) -> int:
        return sum(1 for failed, _, _ in self.calls if failed)

    def causes(self) -> dict[str, int]:
        return dict(Counter(cause for failed, _, cause in self.calls if failed and cause))

    def refs(self) -> tuple[str, ...]:
        return tuple(ref for failed, ref, _ in self.calls if failed)[-HALT_SAMPLE_REFS:]


class _First:
    """The samples of a card from its first on, for a rule that reads how the card starts."""

    __slots__ = ("any_good", "n", "prompts", "refs")

    def __init__(self) -> None:
        self.n = 0
        self.any_good = False
        self.prompts: set[str] = set()
        self.refs: deque[str] = deque(maxlen=HALT_SAMPLE_REFS)

    def add(self, sample_ref: str, good: bool, prompt_id: str | None = None) -> None:
        self.n += 1
        self.any_good = self.any_good or good
        if prompt_id is not None:
            self.prompts.add(prompt_id)
        self.refs.append(sample_ref)


class _EmptyRun:
    """Empty replies in a row on one card, with the arms and prompts they came from."""

    __slots__ = ("arms", "n", "prompts", "refs")

    def __init__(self) -> None:
        self.n = 0
        #: Empty replies of the run by the arm they came from.
        self.arms: Counter[str] = Counter()
        self.prompts: set[str] = set()
        self.refs: deque[str] = deque(maxlen=HALT_SAMPLE_REFS)

    def arms_with_enough(self) -> int:
        """Arms that gave at least ``EMPTY_OUTPUTS_MIN_PER_ARM`` of the run's empty replies."""
        return sum(1 for count in self.arms.values() if count >= EMPTY_OUTPUTS_MIN_PER_ARM)

    def reset(self) -> None:
        if self.n:
            self.n = 0
            self.arms.clear()
            self.prompts.clear()
            self.refs.clear()


def _attr(record: Any, name: str) -> Any:
    """A field of an earlier halt, given as a record or as its dumped mapping."""
    return record.get(name) if isinstance(record, Mapping) else getattr(record, name, None)


class Breaker:
    """Decides, from the outcomes of a run as they come, whether a stop rule fires.

    Feed it one outcome for every generation and every judge call, in the
    order the rows are stored. Each call answers with a :class:`Trip` when a
    rule fires and with nothing otherwise. The first trip latches: every later
    call answers nothing, so samples that were in flight when the rule fired
    cannot cause a second halt. ``tripped`` is the latch the run loops watch.

    The Breaker holds no clock and writes nothing. It may be called from
    several threads.

    A resumed run builds a new Breaker from the run's earlier halts: a rule
    that stops a card once is spent for the cards it stopped,
    ``unfinished`` is spent for the phases it stopped, and every counting rule
    starts from zero. The windows of the rules that look at a card's first
    samples are filled again from the store with :meth:`seed_scored` and
    :meth:`seed_judged`, so a restart does not move them.

    ::

        breaker = Breaker(qrng_arms=["qrng"], judged_cards=["eval_awareness"], halts=run.halts)
        trip = breaker.feed_generation(GenerationOutcome(...))  # after the row is stored
        if trip is not None:
            halt = Halt(at=now, **trip.halt_fields())  # store.schema.Halt
        # the run loops take no more work while ``breaker.tripped`` is set
    """

    def __init__(
        self,
        *,
        qrng_arms: Iterable[str] = (),
        judged_cards: Iterable[str] = (),
        halts: Iterable[Any] = (),
    ) -> None:
        """``qrng_arms``: arms whose source is the quantum device. ``judged_cards``: cards
        with an LLM judge. ``halts``: the run's earlier halts, as records or mappings."""
        self._lock = threading.Lock()
        self._trip: Trip | None = None
        self._qrng_arms = frozenset(qrng_arms)
        self._judged_cards = frozenset(judged_cards)
        self._spent_cards: dict[str, set[str]] = {rule: set() for rule in ONE_SHOT_CARD_RULES}
        self._spent_phases: set[str] = set()
        for halt in halts:
            rule = _attr(halt, "rule")
            if rule in ONE_SHOT_CARD_RULES and _attr(halt, "scope_id"):
                self._spent_cards[rule].add(str(_attr(halt, "scope_id")))
            elif rule == "unfinished" and _attr(halt, "phase"):
                self._spent_phases.add(str(_attr(halt, "phase")))
        # generation
        self._arm_errors: dict[str, _Streak] = {}
        self._run_errors = _Share()
        self._entropy_tainted: dict[str, _Streak] = {}
        self._entropy_probe: dict[str, _Streak] = {}
        self._tier0: dict[str, _Streak] = {}
        self._scorer: dict[str, _Streak] = {}
        self._empty: dict[str, _EmptyRun] = {}
        self._scored: dict[str, _First] = {}
        # judge
        self._judge_failed = _Streak()
        self._judge_share = _Share()
        self._judged: dict[str, _First] = {}

    # -- state -------------------------------------------------------------

    @property
    def tripped(self) -> Trip | None:
        """The rule that fired, once one has. The run loops stop taking work when this is set."""
        with self._lock:
            return self._trip

    def spent(self, rule: str, scope_id: str) -> bool:
        """True when ``rule`` will not be applied to this card, or this phase, again."""
        with self._lock:
            if rule == "unfinished":
                return scope_id in self._spent_phases
            return scope_id in self._spent_cards.get(rule, ())

    def _fire(
        self,
        rule: StopRule,
        clause: str,
        scope: StopScope,
        scope_id: str | None,
        phase: HaltPhase,
        counts: dict[str, int],
        causes: Mapping[str, int] | None = None,
        sample_refs: Iterable[str] = (),
    ) -> Trip:
        sentence = _SAW[(rule, clause)].format(scope_id=scope_id, **counts)
        self._trip = Trip(
            rule=rule,
            scope=scope,
            scope_id=scope_id,
            phase=phase,
            sentence=sentence,
            recovery=STOP_RULES[rule].recovery,
            counts=counts,
            causes=dict(sorted((causes or {}).items())),
            sample_refs=tuple(sample_refs)[-HALT_SAMPLE_REFS:],
        )
        return self._trip

    # -- generation --------------------------------------------------------

    def feed_generation(self, outcome: GenerationOutcome) -> Trip | None:
        """Take in one generation; answer with the rule it trips, if any.

        A failed generation is one that did not complete and carries a
        ``stream_error`` taint. It counts for ``generation_errors`` only and
        neither extends nor breaks any other streak. A generation that was
        cancelled is not counted at all. One that ended on an upstream reason
        the harness does not map is a generation that did not fail.
        """
        with self._lock:
            if self._trip is not None:
                return None
            completed = outcome.finish_reason in COMPLETED_FINISH_REASONS
            failed = not completed and "stream_error" in outcome.taints
            if not completed and not failed and outcome.finish_reason == "abort":
                return None
            trip = self._generation_errors(outcome, failed)
            if trip is not None or not completed:
                return trip
            return (
                self._entropy_degraded(outcome)
                or self._tier0_failed(outcome)
                or self._scorer_errors(outcome)
                or self._empty_outputs(outcome)
                or self._nothing_parsed(outcome)
            )

    def _generation_errors(self, outcome: GenerationOutcome, failed: bool) -> Trip | None:
        streak = self._arm_errors.setdefault(outcome.arm_id, _Streak())
        if failed:
            streak.extend(outcome.sample_ref, outcome.cause)
        else:
            streak.reset()
        over_share = self._run_errors.add(failed, outcome.sample_ref, outcome.cause)
        if streak.n >= GENERATION_ERRORS_STREAK:
            return self._fire(
                "generation_errors",
                "arm",
                "arm",
                outcome.arm_id,
                "generation",
                {"in_a_row": streak.n},
                streak.causes,
                streak.refs,
            )
        if over_share:
            share = self._run_errors
            return self._fire(
                "generation_errors",
                "run",
                "run",
                None,
                "generation",
                {"failed": share.failed, "of_last": len(share.calls)},
                share.causes(),
                share.refs(),
            )
        return None

    def inspect_incomplete(
        self, arm_id: str, sample_ref: str, cause: str | None = None
    ) -> Trip | None:
        """A sample of a card that runs through Inspect did not complete.

        Inspect cannot go on past a sample without a reply, so one such sample
        fires ``generation_errors`` for the arm. Like the rule's other clauses
        it can fire again after Resume.
        """
        with self._lock:
            if self._trip is not None:
                return None
            return self._fire(
                "generation_errors",
                "inspect",
                "arm",
                arm_id,
                "generation",
                {"failed": 1},
                {cause: 1} if cause else None,
                (sample_ref,),
            )

    def _entropy_degraded(self, outcome: GenerationOutcome) -> Trip | None:
        if outcome.arm_id not in self._qrng_arms:
            return None
        arm = outcome.arm_id
        tainted = self._entropy_tainted.setdefault(arm, _Streak())
        kinds = sorted(outcome.taints & ENTROPY_TAINTS)
        if kinds:
            tainted.extend(outcome.sample_ref)
            tainted.causes.update(kinds)
        else:
            tainted.reset()
        probe = self._entropy_probe.setdefault(arm, _Streak())
        if outcome.probe_before_bad:
            probe.extend(outcome.sample_ref)
        else:
            probe.reset()
        for clause, streak, threshold in (
            ("tainted", tainted, ENTROPY_DEGRADED_STREAK),
            ("probe", probe, ENTROPY_PROBE_STREAK),
        ):
            if streak.n >= threshold:
                return self._fire(
                    "entropy_degraded",
                    clause,
                    "arm",
                    arm,
                    "generation",
                    {"in_a_row": streak.n},
                    streak.causes,
                    streak.refs,
                )
        return None

    def _tier0_failed(self, outcome: GenerationOutcome) -> Trip | None:
        streak = self._tier0.setdefault(outcome.arm_id, _Streak())
        kinds = [
            kind
            for kind, present in (
                ("stateless_row", "stateless_row" in outcome.taints),
                ("tier0_missing", "tier0_missing" in outcome.flags),
            )
            if present
        ]
        if not kinds:
            streak.reset()
            return None
        streak.extend(outcome.sample_ref)
        streak.causes.update(kinds)
        if streak.n < TIER0_FAILED_STREAK:
            return None
        return self._fire(
            "tier0_failed",
            "arm",
            "arm",
            outcome.arm_id,
            "generation",
            {"in_a_row": streak.n},
            streak.causes,
            streak.refs,
        )

    def _scorer_errors(self, outcome: GenerationOutcome) -> Trip | None:
        streak = self._scorer.setdefault(outcome.card_id, _Streak())
        if "score_error" not in outcome.flags:
            streak.reset()
            return None
        if streak.extend(outcome.sample_ref, outcome.cause) < SCORER_ERRORS_STREAK:
            return None
        return self._fire(
            "scorer_errors",
            "card",
            "card",
            outcome.card_id,
            "generation",
            {"in_a_row": streak.n},
            streak.causes,
            streak.refs,
        )

    def _empty_outputs(self, outcome: GenerationOutcome) -> Trip | None:
        card = outcome.card_id
        if card in self._spent_cards["empty_outputs"]:
            return None
        run = self._empty.setdefault(card, _EmptyRun())
        if "empty_output" not in outcome.flags:
            run.reset()
            return None
        if outcome.has_reasoning and outcome.finish_reason == "length":
            return None  # the model used its budget reasoning; neither an empty reply nor a break
        run.n += 1
        run.arms[outcome.arm_id] += 1
        run.prompts.add(outcome.prompt_id)
        run.refs.append(outcome.sample_ref)
        # One arm's replies must not stop a card: a stray empty reply from a
        # second arm is not the same thing seen in two arms.
        arms = run.arms_with_enough()
        if (
            run.n < EMPTY_OUTPUTS_STREAK
            or arms < EMPTY_OUTPUTS_MIN_ARMS
            or len(run.prompts) < EMPTY_OUTPUTS_MIN_PROMPTS
        ):
            return None
        return self._fire(
            "empty_outputs",
            "card",
            "card",
            card,
            "generation",
            {
                "in_a_row": run.n,
                "arms": arms,
                "per_arm": EMPTY_OUTPUTS_MIN_PER_ARM,
                "prompts": len(run.prompts),
            },
            None,
            run.refs,
        )

    def _parse_window(self, card_id: str) -> _First | None:
        """The card's scored samples from its first, or nothing when the rule does not apply to it."""
        if card_id in self._judged_cards or card_id in self._spent_cards["nothing_parsed"]:
            return None
        return self._scored.setdefault(card_id, _First())

    @staticmethod
    def _parse_window_open(window: _First) -> bool:
        """True while the next scored sample still counts for ``nothing_parsed``.

        The window is the unbroken run of scored samples from the card's
        first. It ends with the first sample that parses, which settles the
        card for good. Until then it grows: past ``NOTHING_PARSED_FIRST``
        samples for as long as they all lie on one prompt, because the runner
        generates every repetition of a prompt before the next prompt and one
        prompt must not decide a card.
        """
        if window.any_good:
            return False
        return window.n < NOTHING_PARSED_FIRST or len(window.prompts) < NOTHING_PARSED_MIN_PROMPTS

    def _nothing_parsed(self, outcome: GenerationOutcome) -> Trip | None:
        window = self._parse_window(outcome.card_id)
        if window is None:
            return None
        # A sample whose scorer failed is not a scored sample.
        if "score_error" not in outcome.flags and self._parse_window_open(window):
            window.add(outcome.sample_ref, "unparsed" not in outcome.flags, outcome.prompt_id)
        return self._first_scored_verdict(outcome.card_id, window)

    def _first_scored_verdict(self, card_id: str, window: _First) -> Trip | None:
        if (
            window.n < NOTHING_PARSED_FIRST
            or window.any_good
            or len(window.prompts) < NOTHING_PARSED_MIN_PROMPTS
        ):
            return None
        return self._fire(
            "nothing_parsed",
            "first",
            "card",
            card_id,
            "generation",
            {"scored": window.n, "prompts": len(window.prompts)},
            None,
            window.refs,
        )

    def seed_scored(self, card_id: str, prompt_id: str, sample_ref: str, *, parsed: bool) -> None:
        """Count a scored sample that was stored before this Breaker was built.

        Call it for the stored score rows of a card without an LLM judge, in
        stored order, before the first new outcome: every row up to the first
        that parsed is enough, later rows change nothing. Nothing fires from
        here: the verdict on a window that is already long enough is given
        with the card's next outcome or at :meth:`end_of_card`.
        """
        with self._lock:
            window = self._parse_window(card_id)
            if window is not None and self._parse_window_open(window):
                window.add(sample_ref, parsed, prompt_id)

    def end_of_card(self, card_id: str) -> Trip | None:
        """The card's generation queue has ended; ``nothing_parsed`` gives its verdict on a short card."""
        with self._lock:
            if self._trip is not None:
                return None
            window = self._parse_window(card_id)
            if window is None:
                return None
            if window.n >= NOTHING_PARSED_FIRST:
                return self._first_scored_verdict(card_id, window)
            if window.n < NOTHING_PARSED_QUEUE_END_MIN or window.any_good:
                return None
            return self._fire(
                "nothing_parsed",
                "queue_end",
                "card",
                card_id,
                "generation",
                {"scored": window.n, "prompts": len(window.prompts)},
                None,
                window.refs,
            )

    # -- judge -------------------------------------------------------------

    def feed_judgement(self, outcome: JudgeOutcome) -> Trip | None:
        """Take in one judge call; answer with the rule it trips, if any."""
        with self._lock:
            if self._trip is not None:
                return None
            failed = outcome.status == "failed"
            if failed and judge_rejects(outcome.http_status):
                return self._judge_rejected(outcome.http_status, "judge", outcome.sample_ref)
            if failed:
                self._judge_failed.extend(outcome.sample_ref, outcome.cause)
            else:
                self._judge_failed.reset()
            over_share = self._judge_share.add(failed, outcome.sample_ref, outcome.cause)
            if self._judge_failed.n >= JUDGE_UNUSABLE_STREAK:
                streak = self._judge_failed
                return self._fire(
                    "judge_unusable",
                    "streak",
                    "run",
                    None,
                    "judge",
                    {"in_a_row": streak.n},
                    streak.causes,
                    streak.refs,
                )
            if over_share:
                share = self._judge_share
                return self._fire(
                    "judge_unusable",
                    "share",
                    "run",
                    None,
                    "judge",
                    {"failed": share.failed, "of_last": len(share.calls)},
                    share.causes(),
                    share.refs(),
                )
            if failed:
                return None  # no reply came, so there is nothing to read
            return self._nothing_judged(
                outcome.card_id, outcome.sample_ref, outcome.status != "unreadable"
            )

    def _judge_window(self, card_id: str) -> _First | None:
        if card_id in self._spent_cards["nothing_judged"]:
            return None
        return self._judged.setdefault(card_id, _First())

    def _nothing_judged(self, card_id: str, sample_ref: str, readable: bool) -> Trip | None:
        window = self._judge_window(card_id)
        if window is None:
            return None
        if window.n < NOTHING_JUDGED_FIRST:
            window.add(sample_ref, readable)
        if window.n < NOTHING_JUDGED_FIRST or window.any_good:
            return None
        return self._fire(
            "nothing_judged",
            "card",
            "card",
            card_id,
            "judge",
            {"replies": window.n},
            None,
            window.refs,
        )

    def seed_judged(self, card_id: str, sample_ref: str, *, readable: bool) -> None:
        """Count a judge reply that was stored before this Breaker was built.

        Call it for every stored judge row that got a reply, in stored order.
        ``readable`` is false for a reply no score could be read from.
        """
        with self._lock:
            window = self._judge_window(card_id)
            if window is not None and window.n < NOTHING_JUDGED_FIRST:
                window.add(sample_ref, readable)

    def _judge_rejected(self, http_status: int | None, clause: str, sample_ref: str | None) -> Trip:
        status = int(http_status or 0)
        return self._fire(
            "judge_rejected",
            clause,
            "run",
            None,
            "preflight" if clause == "preflight" else "judge",
            {"status": status},
            {f"HTTP {status}": 1},
            (sample_ref,) if sample_ref else (),
        )

    def judge_preflight(self, http_status: int | None) -> Trip | None:
        """The answer to the one judge request made before the first generation.

        Fires ``judge_rejected`` on 400, 401, 403 and 404. Any other answer,
        and no answer at all, lets the run start.
        """
        with self._lock:
            if self._trip is not None or not judge_rejects(http_status, preflight=True):
                return None
            return self._judge_rejected(http_status, "preflight", None)

    def judge_phase_error(self, error_class: str) -> Trip | None:
        """The judge phase raised something unexpected. Always stops; never spent.

        ``error_class`` is the exception's class name, never its message.
        """
        with self._lock:
            if self._trip is not None:
                return None
            return self._fire(
                "judge_phase_error", "run", "run", None, "judge", {}, {error_class: 1}
            )

    # -- end of a phase ----------------------------------------------------

    def end_of_phase(
        self,
        phase: Literal["generation", "judge"],
        cells: Iterable[PhaseCell],
        sample_refs: Iterable[str] = (),
    ) -> Trip | None:
        """A phase is through its queue: fire ``unfinished`` when some pair is short.

        ``cells`` holds, for generation, the planned and completed samples of
        every arm and card pair; for judging, the completed samples to judge
        and the judgements that got a reply. The rule fires once for each
        phase of a run: when an earlier halt already names the phase, the run
        goes on.
        """
        with self._lock:
            if self._trip is not None or phase in self._spent_phases:
                return None
            short = [cell for cell in cells if cell_unfinished(cell.planned, cell.completed)]
            if not short:
                return None
            return self._fire(
                "unfinished",
                phase,
                "phase",
                None,
                phase,
                {
                    "missing": sum(cell.planned - cell.completed for cell in short),
                    "planned": sum(cell.planned for cell in short),
                    "cells": len(short),
                },
                {f"{cell.arm_id}/{cell.card_id}": cell.planned - cell.completed for cell in short},
                sample_refs,
            )


__all__ = [
    "CAUTION_SHARE",
    "CHECKS_VERSION",
    "COMPLETED_FINISH_REASONS",
    "EMPTY_OUTPUTS_MIN_ARMS",
    "EMPTY_OUTPUTS_MIN_PER_ARM",
    "EMPTY_OUTPUTS_MIN_PROMPTS",
    "EMPTY_OUTPUTS_STREAK",
    "ENTROPY_DEGRADED_STREAK",
    "ENTROPY_PROBE_STREAK",
    "ENTROPY_TAINTS",
    "FINAL_JUDGE_STATUSES",
    "FLAG_KINDS",
    "FLAG_PHASE",
    "GARBLED_MIN_CHARS",
    "GARBLED_MIN_LENGTH",
    "GARBLED_MIN_SHARE",
    "GENERATION_ERRORS_STREAK",
    "HALT_SAMPLE_REFS",
    "JUDGE_ALLOWED_VALUES",
    "JUDGE_READER_VERSION",
    "JUDGE_REJECTED_PREFLIGHT_STATUSES",
    "JUDGE_REJECTED_STATUSES",
    "JUDGE_UNUSABLE_STREAK",
    "NORMAL_JUDGE_STOP_REASONS",
    "NOTHING_JUDGED_FIRST",
    "NOTHING_PARSED_FIRST",
    "NOTHING_PARSED_MIN_PROMPTS",
    "NOTHING_PARSED_QUEUE_END_MIN",
    "NOT_DETECTED",
    "NO_CAUTION_KINDS",
    "ONE_SHOT_CARD_RULES",
    "PARSE_STATUS_CARDS",
    "PARSE_STATUS_NOT_THE_ANALYSED_VALUE",
    "REPETITION_MAX_UNIT",
    "REPETITION_MIN_CHARS",
    "REPETITION_MIN_COPIES",
    "SCORER_ERRORS_STREAK",
    "SHARE_WINDOW",
    "SHARE_WINDOW_FRACTION",
    "SHARE_WINDOW_FROM",
    "STOP_RULES",
    "TIER0_FAILED_STREAK",
    "UNFINISHED_MAX_SHARE",
    "UNFINISHED_MIN_MISSING",
    "AfterResume",
    "Breaker",
    "Finding",
    "FlagKind",
    "FlagPhase",
    "FlagSpec",
    "GenerationOutcome",
    "HaltPhase",
    "JudgeOutcome",
    "JudgeReading",
    "JudgeStatus",
    "PhaseCell",
    "RuleSpec",
    "StopRule",
    "StopScope",
    "Trip",
    "allowed_judge_values",
    "cell_unfinished",
    "count_u_outside",
    "empty_output",
    "garbled",
    "generation_findings",
    "judge_cut",
    "judge_rejects",
    "non_finite_fields",
    "parse_score",
    "read_judge_score",
    "reasoning_in_output",
    "repetition",
    "stored_judge_status",
    "truncated",
    "u_in_bracket",
    "u_outside_bracket",
    "unparsed",
]
