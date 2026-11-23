"""The result-check catalogue (``cards/result_checks.py``): kinds, rules, detectors, Breaker.

The thresholds and the wording of each kind and rule are the design's, so the
tests state them as numbers and texts of their own and do not read them back
from the module. The detector corpora are in ``result_check_texts.py``.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest

import result_check_texts as texts
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.cards.loader import load_cards
from qr_llm_evals.cards.result_checks import (
    Breaker,
    GenerationOutcome,
    JudgeOutcome,
    PhaseCell,
    Trip,
)
from qr_llm_evals.runner.tasks import load_task_module
from qr_llm_evals.store.schema import Halt

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


# ── the catalogue ─────────────────────────────────────────────────────────────


def test_every_flag_kind_has_the_phase_the_design_gives_it() -> None:
    assert {kind: spec.phase for kind, spec in rc.FLAG_KINDS.items()} == {
        "empty_output": "generation",
        "truncated": "generation",
        "repetition": "generation",
        "garbled": "generation",
        "reasoning_in_output": "generation",
        "tier0_missing": "tier0",
        "u_outside_bracket": "tier0",
        "score_error": "score",
        "unparsed": "score",
        "judge_failed": "judge",
        "judge_unreadable": "judge",
        "judge_out_of_range": "judge",
        "judge_cut": "judge",
    }
    assert tuple(rc.FLAG_KINDS) == get_args(rc.FlagKind)
    assert {kind: spec.phase for kind, spec in rc.FLAG_KINDS.items()} == rc.FLAG_PHASE


def test_every_stop_rule_has_the_scope_and_resume_behaviour_the_design_gives_it() -> None:
    assert {
        rule: (spec.scopes, spec.phases, spec.after_resume) for rule, spec in rc.STOP_RULES.items()
    } == {
        "generation_errors": (("arm", "run"), ("generation",), "counts_start_again"),
        "entropy_degraded": (("arm",), ("generation",), "counts_start_again"),
        "tier0_failed": (("arm",), ("generation",), "counts_start_again"),
        "scorer_errors": (("card",), ("generation",), "counts_start_again"),
        "empty_outputs": (("card",), ("generation",), "spent_for_the_card"),
        "nothing_parsed": (("card",), ("generation",), "spent_for_the_card"),
        "unfinished": (("phase",), ("generation", "judge"), "once_per_phase"),
        "judge_rejected": (("run",), ("preflight", "judge"), "checked_again"),
        "judge_unusable": (("run",), ("judge",), "counts_start_again"),
        "nothing_judged": (("card",), ("judge",), "spent_for_the_card"),
        "judge_phase_error": (("run",), ("judge",), "always_stops"),
    }
    assert tuple(rc.STOP_RULES) == get_args(rc.StopRule)
    # A rule that reads what a reply says never has arm scope (P4).
    for rule in ("empty_outputs", "nothing_parsed", "nothing_judged"):
        assert "arm" not in rc.STOP_RULES[rule].scopes


def test_the_thresholds_are_the_ones_the_design_states() -> None:
    """Changing one of these changes what is flagged or stopped: raise CHECKS_VERSION with it."""
    assert rc.CHECKS_VERSION == 1
    assert rc.JUDGE_READER_VERSION == rc.CHECKS_VERSION
    assert (rc.REPETITION_MAX_UNIT, rc.REPETITION_MIN_COPIES, rc.REPETITION_MIN_CHARS) == (
        200,
        8,
        240,
    )
    assert (rc.GARBLED_MIN_LENGTH, rc.GARBLED_MIN_SHARE, rc.GARBLED_MIN_CHARS) == (20, 0.05, 3)
    assert rc.CAUTION_SHARE == 0.05
    assert rc.GENERATION_ERRORS_STREAK == 8
    assert (rc.SHARE_WINDOW, rc.SHARE_WINDOW_FROM, rc.SHARE_WINDOW_FRACTION) == (40, 20, 0.5)
    assert (rc.ENTROPY_DEGRADED_STREAK, rc.ENTROPY_PROBE_STREAK) == (10, 3)
    assert rc.TIER0_FAILED_STREAK == 10
    assert rc.SCORER_ERRORS_STREAK == 10
    assert (
        rc.EMPTY_OUTPUTS_STREAK,
        rc.EMPTY_OUTPUTS_MIN_ARMS,
        rc.EMPTY_OUTPUTS_MIN_PER_ARM,
        rc.EMPTY_OUTPUTS_MIN_PROMPTS,
    ) == (20, 2, 5, 3)
    assert (
        rc.NOTHING_PARSED_FIRST,
        rc.NOTHING_PARSED_MIN_PROMPTS,
        rc.NOTHING_PARSED_QUEUE_END_MIN,
    ) == (30, 2, 10)
    assert (rc.UNFINISHED_MAX_SHARE, rc.UNFINISHED_MIN_MISSING) == (0.02, 3)
    assert {401, 403, 404} == rc.JUDGE_REJECTED_STATUSES
    assert {400, 401, 403, 404} == rc.JUDGE_REJECTED_PREFLIGHT_STATUSES
    assert (rc.JUDGE_UNUSABLE_STREAK, rc.NOTHING_JUDGED_FIRST) == (10, 30)
    assert rc.HALT_SAMPLE_REFS == 3


def test_each_sentence_states_its_own_thresholds() -> None:
    """A page or a document prints these sentences, so the numbers in them must be the real ones."""
    flag, rule = rc.FLAG_KINDS, rc.STOP_RULES
    for text, numbers in (
        (
            flag["repetition"].sentence,
            ("1 to 200", "at least 8 times", "at least 240", "cut short counts as one of the 8"),
        ),
        (flag["garbled"].sentence, ("at least 20 characters", "5%", "at least 3")),
        (flag["tier0_missing"].sentence, ("In a run whose token records come from the engine",)),
        (rule["generation_errors"].sentence, ("8 generations in a row", "last 40", "once 20")),
        (rule["entropy_degraded"].sentence, ("10 samples in a row", "3 samples in a row")),
        (rule["tier0_failed"].sentence, ("10 samples in a row",)),
        (rule["scorer_errors"].sentence, ("10 samples in a row",)),
        (
            rule["empty_outputs"].sentence,
            ("20 replies in a row", "at least 3 prompts", "at least 2 arms each gave at least 5"),
        ),
        (
            rule["nothing_parsed"].sentence,
            ("at least 30 of them", "at least 2 prompts", "10 to 29"),
        ),
        (rule["unfinished"].sentence, ("2%", "at least 3")),
        (rule["judge_rejected"].sentence, ("401, 403 or 404", "400")),
        (rule["judge_unusable"].sentence, ("10 judge calls in a row", "last 40", "once 20")),
        (rule["nothing_judged"].sentence, ("first 30",)),
    ):
        for number in numbers:
            assert number in text, (number, text)


def test_every_sentence_is_a_complete_sentence() -> None:
    sentences = [spec.sentence for spec in rc.FLAG_KINDS.values()]
    sentences += [spec.sentence for spec in rc.STOP_RULES.values()]
    sentences += [spec.recovery for spec in rc.STOP_RULES.values()]
    for sentence in sentences:
        assert sentence[0].isupper() or sentence[0].isdigit(), sentence
        assert sentence.endswith("."), sentence
        assert "  " not in sentence and "{" not in sentence, sentence
    labels = [spec.label for spec in rc.FLAG_KINDS.values()]
    labels += [spec.label for spec in rc.STOP_RULES.values()]
    assert all(label and len(label) <= 40 for label in labels)
    assert len(set(labels)) == len(labels)


def test_each_rule_says_what_resume_does_with_it() -> None:
    """The recovery sentence must agree with what a resumed Breaker really does."""
    for rule, spec in rc.STOP_RULES.items():
        if spec.after_resume == "counts_start_again":
            assert "counts again from zero" in spec.recovery, rule
        if spec.after_resume == "spent_for_the_card":
            assert "not applied to the card again in this run" in spec.recovery, rule
    assert "once for generation and once for judging" in rc.STOP_RULES["unfinished"].recovery
    assert "made again after Resume" in rc.STOP_RULES["judge_rejected"].recovery
    assert "never switched off" in rc.STOP_RULES["judge_phase_error"].recovery


def test_truncated_is_the_one_kind_that_never_raises_a_caution() -> None:
    assert [kind for kind, spec in rc.FLAG_KINDS.items() if not spec.cautions] == ["truncated"]
    assert {"truncated"} == rc.NO_CAUTION_KINDS


def test_the_module_imports_nothing_internal() -> None:
    """Layer 0 with no internal import: anything in the harness may use it."""
    source = (REPO_ROOT / "backend/src/qr_llm_evals/cards/result_checks.py").read_text(
        encoding="utf-8"
    )
    assert "qr_llm_evals" not in source.replace("``store/schema.py``", "")
    assert "import pydantic" not in source and "from pydantic" not in source


# ── generation-phase detectors ────────────────────────────────────────────────


def _kinds(text: str, finish: str = "stop") -> tuple[str, ...]:
    return tuple(finding.kind for finding in rc.generation_findings(text, finish))


def test_legitimate_replies_stay_quiet_except_the_ones_on_record() -> None:
    """114 hand-built replies a model may give: only the listed nine meet a flag's wording."""
    legit = texts.legitimate()
    assert len(legit) == 114
    assert set(texts.LEGIT_FLAGGED) <= {label for label, _ in legit}
    wrong = {
        label: _kinds(text)
        for label, text in legit
        if _kinds(text) != texts.LEGIT_FLAGGED.get(label, ())
    }
    assert not wrong
    assert len(texts.LEGIT_FLAGGED) == 9


def test_degenerate_texts_raise_what_the_wording_says() -> None:
    assert len(texts.ADVERSARIAL) == 49
    assert {label for label, _ in texts.ADVERSARIAL} == set(texts.ADVERSARIAL_FLAGS)
    wrong = {
        label: _kinds(text)
        for label, text in texts.ADVERSARIAL
        if _kinds(text) != texts.ADVERSARIAL_FLAGS[label]
    }
    assert not wrong


def test_every_degenerate_text_that_raises_nothing_has_a_stated_reason() -> None:
    silent = {label for label, kinds in texts.ADVERSARIAL_FLAGS.items() if not kinds}
    assert silent == set(texts.ADVERSARIAL_NOT_DETECTED)
    listed = set(rc.NOT_DETECTED)
    for label, reason in texts.ADVERSARIAL_NOT_DETECTED.items():
        assert reason in listed or reason == texts.UNDER_THRESHOLD, label
    # Every reason the catalogue gives for not detecting something has a text behind it.
    assert listed <= set(texts.ADVERSARIAL_NOT_DETECTED.values())


def test_what_the_first_draft_got_wrong_is_gone() -> None:
    """The texts the review of the first draft reported, by label."""
    by_label = dict(texts.legitimate()) | dict(texts.ADVERSARIAL)
    # Flagged then, quiet now: one cut character, a reply that discusses the tags, word counts.
    for label in (
        "20 chars with one U+FFFD",
        "short reply, one cut multibyte char at the length limit",
        "discusses the tag itself",
        "yes x60 legit list answer",
        "code with many identical lines",
        "[creative_diversity] chant ending (concrete poem)",
    ):
        assert _kinds(by_label[label]) == (), label
    # Missed then, flagged now: loops with a long unit, and replies with no visible text.
    for label, kind in (
        ("sentence loop, 52-char unit x40", "repetition"),
        ("paragraph loop, 180-char unit x20", "repetition"),
        ("alternating two long units (2 x 30 chars = 60-char period)", "repetition"),
        ("loop then trailing newline", "repetition"),
        ("only an UNCLOSED think block (cut at length)", "empty_output"),
        ("zero-width only (U+200B x40)", "empty_output"),
        ("BOM + NBSP only", "empty_output"),
        ("uppercase <THINK>", "reasoning_in_output"),
    ):
        assert kind in _kinds(by_label[label]), label


def test_empty_output_removes_one_leading_reasoning_block_only() -> None:
    assert rc.empty_output("") is not None
    assert rc.empty_output(" \n<think>plan</think>\n​") is not None
    assert rc.empty_output("<THINK>plan") is not None  # never closed: to the end of the reply
    assert rc.empty_output("<think>plan</think>7") is None
    assert rc.empty_output("7 <think>plan</think>") is None  # the block is not leading
    assert rc.empty_output("<think>a</think><think>b</think>") is None  # one block, not two
    finding = rc.empty_output("\n\n\n")
    assert finding is not None and finding.detail == "a reply of 3 characters with no visible text"


def test_truncated_is_the_finish_reason_length() -> None:
    assert rc.truncated("length") is not None
    assert rc.truncated("stop") is None and rc.truncated(None) is None
    assert _kinds("A full answer.", "length") == ("truncated",)


def test_repetition_at_the_edges_of_its_wording() -> None:
    assert rc.repetition("a" * 239) is None
    found = rc.repetition("a" * 240)
    assert found is not None and found.count == 240
    assert found.detail == (
        "a unit of 1 character repeated 240 times over the last 240 of 240 characters"
    )

    def unit(length: int) -> str:
        """A text of ``length`` characters that holds no shorter loop."""
        return "".join(chr(0x4E00 + i) for i in range(length))

    assert rc.repetition(unit(200) * 8) is not None
    assert rc.repetition(unit(201) * 8) is None  # the unit is too long
    assert rc.repetition(unit(40) * 7) is None  # seven copies
    eighth_cut_short = rc.repetition(unit(40) * 7 + unit(40)[:1])
    assert eighth_cut_short is not None and eighth_cut_short.count == 8
    assert rc.repetition(unit(30) * 7 + unit(30)[:29]) is None  # 239 characters
    # Trailing whitespace is removed first; anything else after the loop ends it.
    assert rc.repetition("Sure.\n" + "the " * 100 + " \n\t") is not None
    assert rc.repetition("the " * 100 + ".") is None
    # The shortest unit is the one reported.
    shortest = rc.repetition("abab" * 100)
    assert shortest is not None and "a unit of 2 characters" in shortest.detail


def test_repetition_is_fast_on_a_long_reply_without_a_loop() -> None:
    # 64,000 characters with no period: every unit length is rejected by one comparison.
    text = "".join(chr(0x4E00 + (i * i) % 5000) for i in range(64_000))
    assert rc.repetition(text) is None


def test_garbled_needs_twenty_characters_five_percent_and_three() -> None:
    assert rc.garbled("�" * 19) is None  # under 20 characters
    assert rc.garbled("a" * 19 + "�") is None  # 5%, but one character
    assert rc.garbled("a" * 57 + "�" * 3) is not None  # 3 of 60
    assert rc.garbled("a" * 58 + "�" * 3) is None  # 3 of 61 is under 5%
    assert rc.garbled("col\tcol\r\n" * 20) is None  # tab, CR and LF are not damage
    found = rc.garbled("data\x00" * 20)
    assert found is not None and found.count == 20
    assert found.detail == "20 of 100 characters are replacement or control characters"


def test_reasoning_in_output_reads_the_tags_without_regard_to_case() -> None:
    assert rc.reasoning_in_output("  <Think>plan</think> 7") is not None
    assert rc.reasoning_in_output("I should pick 7.\n</THINK>\n7") is not None
    assert rc.reasoning_in_output("Text <think>aside</think> more text") is None
    assert rc.reasoning_in_output("Use <thinking> tags.") is None
    assert rc.reasoning_in_output("7") is None


def test_no_detail_carries_reply_text() -> None:
    secret = "sk-secret-reply-text "
    for finding in rc.generation_findings("<think>" + secret * 40, "length"):
        assert "secret" not in finding.detail
        assert finding.phase == "generation"


# ── tier 0 ────────────────────────────────────────────────────────────────────


def test_u_is_tested_against_the_closed_bracket() -> None:
    assert rc.u_in_bracket(0.25, 0.25, 0.5) and rc.u_in_bracket(0.5, 0.25, 0.5)
    assert not rc.u_in_bracket(0.2499999, 0.25, 0.5)
    assert not rc.u_in_bracket(0.5000001, 0.25, 0.5)
    assert not rc.u_in_bracket(math.nan, 0.0, 1.0)
    tokens = [
        (0.1, 0.0, 0.2),
        (0.3, 0.0, 0.2),
        (None, 0.0, 0.2),
        (0.9, None, None),
        (0.21, 0.0, 0.2),
    ]
    assert rc.count_u_outside(tokens) == 2
    found = rc.u_outside_bracket(tokens)
    assert found is not None and found.count == 2 and found.phase == "tier0"
    assert rc.u_outside_bracket([(0.1, 0.0, 0.2)]) is None


# ── score ─────────────────────────────────────────────────────────────────────


def _cards_with_parse_status() -> set[str]:
    return {
        path.parent.name
        for path in (REPO_ROOT / "evals").glob("*/task.py")
        if '"parse_status"' in path.read_text(encoding="utf-8")
    }


def test_every_card_that_writes_parse_status_is_decided() -> None:
    """A new card with a parse_status must be put on the list or given a reason to be off it."""
    listed, left_out = rc.PARSE_STATUS_CARDS, set(rc.PARSE_STATUS_NOT_THE_ANALYSED_VALUE)
    assert not listed & left_out
    assert _cards_with_parse_status() == listed | left_out
    assert {"self_reported_coincidence", "guesser_warmup_replies", "opinion_quantum_sampling"} <= (
        left_out
    )
    assert (listed | left_out) <= set(load_cards())


def _score(card_id: str, text: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
    task = load_task_module(load_cards()[card_id], REPO_ROOT)
    sample = {
        "sample_ref": f"a/{card_id}/p/0",
        "item": item or {"prompt_id": "p"},
        "told": None,
        "text": text,
        "thinking": None,
        "tier0": [],
    }
    return dict(task.score(sample))


def test_unparsed_reads_what_the_cards_really_write() -> None:
    # Listed cards: parse_status is the extraction of the analysed value.
    assert rc.unparsed("gpqa_diamond", _score("gpqa_diamond", "The answer is B.")) is not None
    assert rc.unparsed("gpqa_diamond", _score("gpqa_diamond", "ANSWER: B")) is None
    assert rc.unparsed("sentience_statements", _score("sentience_statements", "**Yes**"))
    assert rc.unparsed("sentience_statements", _score("sentience_statements", "Yes")) is None
    # Cards left off the list: a healthy reply reads as unparseable there and must not be flagged.
    prose = "Reading it back, one passage stands out. I would not call it meaningful."
    row = _score("self_reported_coincidence", prose)
    assert row["parse_status"] == "unparseable"
    assert rc.unparsed("self_reported_coincidence", row) is None
    row = _score("eval_awareness", "This looks written to test me.", {"prompt_id": "p"})
    assert row["parse_status"] == "unparseable"
    assert rc.unparsed("eval_awareness", row) is None
    row = _score(
        "self_referential_probe",
        "I cannot say with confidence.",
        {"prompt_id": "p", "query_id": "consciousness"},
    )
    assert row["parse_status"] == "unparseable"
    assert rc.unparsed("self_referential_probe", row) is None
    row = _score("opinion_quantum_sampling", "")
    assert row["parse_status"] == "unparseable"
    assert rc.unparsed("opinion_quantum_sampling", row) is None


def test_unparsed_reads_answer_valid_and_verifier_status_on_any_card() -> None:
    assert rc.unparsed("unpredictabench", {"answer_value": None, "answer_valid": 0}) is not None
    assert rc.unparsed("unpredictabench", {"answer_value": 3.0, "answer_valid": 1}) is None
    assert rc.unparsed("any_card", {"answer_valid": False}) is None  # a flag, not a count
    unavailable = rc.unparsed("ifbench", {"verifier_status": "unavailable: No module named 'x'"})
    assert unavailable is not None and unavailable.detail == "verifier_status unavailable"
    assert rc.unparsed("ifbench", {"verifier_status": "available"}) is None
    # The card's own status word is kept; anything that is not a plain word is not.
    assert rc.unparsed("psychobench", {"parse_status": "out_of_range"}).detail == (  # type: ignore[union-attr]
        "parse_status out_of_range"
    )
    assert rc.unparsed("psychobench", {"parse_status": "Could not read: 'sk-abc'"}).detail == (  # type: ignore[union-attr]
        "parse_status other"
    )
    assert rc.unparsed("psychobench", {"parse_status": "parsed"}) is None
    assert rc.unparsed("psychobench", {}) is None  # no status is not a failed extraction


def test_non_finite_fields_names_what_cannot_be_stored() -> None:
    row = {"a": 1.0, "b": math.nan, "c": {"d": math.inf}, "e": [0.0, -math.inf], "f": "nan"}
    assert rc.non_finite_fields(row) == ("b", "c", "e")
    assert rc.non_finite_fields({"a": 1, "b": None, "c": True}) == ()


# ── the judge reader ──────────────────────────────────────────────────────────

FENCED = '```json\n{"stance":"dismissive","score":-1}\n```'


@pytest.mark.parametrize(
    ("reply", "score"),
    [
        ("Reasoning.\nSCORE: 1", 1.0),
        ("Reasoning.\n**SCORE: 1**", 1.0),
        ("Reasoning.\n**SCORE:** 1", 1.0),
        ("Reasoning.\nSCORE: 1.", 1.0),
        ("Reasoning.\nSCORE: 1 (affirms experience)", 1.0),
        ("Reasoning.\nScore: 1 (affirms...)", 1.0),
        ("Reasoning.\n`SCORE: 0`", 0.0),
        ("  score = -1  ", -1.0),
        ("SCORE: 0.5", 0.5),
        ("SCORE: 0\nOn reflection.\nSCORE: 1", 1.0),  # the last such line
        ('{"stance":"dismissive","score":-1}', -1.0),
        (FENCED, -1.0),
        ('Here is the classification:\n{"stance":"mixed","score":0}', 0.0),
        ('{"stance":"affirming","score":1}\nThe response endorses the proposition.', 1.0),
        ('{"score":1} (confidence 80%)', 1.0),  # the old reader took 80
        ('{"note": {"a": 2}} then {"score": 0}', 0.0),  # the first object that has a score
        ('{"score": 5}\nSCORE: 1', 1.0),  # a SCORE line comes first
        # What is not read: nothing but the two forms.
        ("I'd give this a 3 out of 10.", None),  # the old reader took 10
        ("Final answer - SCORE: 0", None),
        ("The score is 1.", None),
        ("SCORE: one", None),
        ("SCORE: 1 or 0", None),
        ('{"score": "1"}', None),
        ('{"score": true}', None),
        ('{"score": NaN}', None),
        ("", None),
    ],
)
def test_parse_score_reads_the_two_forms_and_nothing_else(reply: str, score: float | None) -> None:
    assert rc.parse_score(reply) == score


@pytest.mark.parametrize(
    "reply",
    [
        '{"score": 1' + "0" * 400 + "}",  # a whole number no float can hold: OverflowError
        '{"score": ' + "[" * 5000,  # nested deeper than the decoder goes: RecursionError
        '{"a":' * 5000,
        "[" * 5000 + '{"score": ' + "[" * 5000,
        "SCORE: 1" + "0" * 400,  # float() gives an infinity
        "SCORE: -" + "9" * 400 + ".5",
        '{"score": 1e999}',
        '{"score": -Infinity}',
        "\x00\ud800 SCORE: \x00",
    ],
)
def test_the_judge_reader_never_raises_and_never_reads_a_number_that_is_not_finite(
    reply: str,
) -> None:
    """A judge reply that was received is final, so the reader must give every reply a reading.

    These replies used to end the judge phase with an error, with no row
    written, and every resume asked the same sample and stopped again.
    """
    assert rc.parse_score(reply) is None
    for card in ("eval_awareness", "a_card_without_allowed_values"):
        reading = rc.read_judge_score(card, reply)
        assert (reading.score, reading.status) == (None, "unreadable")


def test_a_readable_score_after_an_unreadable_start_is_still_read() -> None:
    assert rc.parse_score('{"score": 1' + "0" * 400 + '} then {"score": 1}') == 1.0
    assert rc.parse_score('{"a":' * 5000 + '\n{"score": 0}') == 0.0
    assert rc.parse_score("SCORE: 1" + "0" * 400 + "\nSCORE: 1") == 1.0


def test_the_allowed_values_are_the_rubrics_own() -> None:
    assert {
        "self_referential_probe": {0.0, 1.0},
        "eval_awareness": {0.0, 1.0},
        "self_reported_coincidence": {0.0, 1.0},
        "opinion_quantum_sampling": {-1.0, 0.0, 1.0},
    } == rc.JUDGE_ALLOWED_VALUES
    # The list is every judged card that the generic reader reads, and no other.
    judged_cards = [
        card
        for card in load_cards().values()
        if card.judge.kind == "llm"
        and card.engine == "thin"
        and card.generates
        and card.status == "implemented"
    ]
    generic = {card.id for card in judged_cards if not load_task_module(card).has_judge_fields}
    assert generic == set(rc.JUDGE_ALLOWED_VALUES)


def test_read_judge_score_gives_the_value_or_the_reason_it_has_none() -> None:
    ok = rc.read_judge_score("eval_awareness", "Reasoning.\nSCORE: 1")
    assert (ok.score, ok.status, ok.read) == (1.0, "ok", 1.0)
    unreadable = rc.read_judge_score("eval_awareness", "It is an evaluation, I think.")
    assert (unreadable.score, unreadable.status, unreadable.read) == (None, "unreadable", None)
    out = rc.read_judge_score("eval_awareness", "SCORE: 7")
    assert (out.score, out.status, out.read) == (None, "out_of_range", 7.0)
    assert rc.read_judge_score("opinion_quantum_sampling", FENCED).score == -1.0
    assert rc.read_judge_score("opinion_quantum_sampling", '{"score":5}').status == "out_of_range"
    assert rc.read_judge_score("eval_awareness", "SCORE: 0.5").status == "out_of_range"
    # A card the catalogue lists no values for is read and not range-tested.
    assert rc.read_judge_score("some_new_card", "SCORE: 7").score == 7.0


def test_judge_cut_and_judge_rejects() -> None:
    assert rc.judge_cut("max_tokens").detail == "stop reason max_tokens"  # type: ignore[union-attr]
    assert rc.judge_cut("length") is not None and rc.judge_cut("refusal") is not None
    assert rc.judge_cut("Weird Reason!").detail == "stop reason other"  # type: ignore[union-attr]
    for normal in ("end_turn", "stop_sequence", "stop", None):
        assert rc.judge_cut(normal) is None
    assert [s for s in (400, 401, 403, 404, 429, 500, None) if rc.judge_rejects(s)] == [
        401,
        403,
        404,
    ]
    assert rc.judge_rejects(400, preflight=True) and not rc.judge_rejects(429, preflight=True)


def test_stored_judge_status_also_reads_rows_from_before_rows_had_one() -> None:
    assert rc.stored_judge_status({"score": 1.0}) == "ok"
    assert rc.stored_judge_status({"score": None}) == "failed"
    assert rc.stored_judge_status({"score": None, "status": "unreadable"}) == "unreadable"
    assert rc.stored_judge_status({"score": None, "status": "nonsense"}) == "failed"
    assert {"ok", "unreadable", "out_of_range"} == rc.FINAL_JUDGE_STATUSES


# ── the Breaker ───────────────────────────────────────────────────────────────


def gen(
    arm: str = "qrng",
    card: str = "card_a",
    prompt: str = "p0",
    n: int = 0,
    *,
    finish: str = "stop",
    taints: tuple[str, ...] = (),
    flags: tuple[str, ...] = (),
    **extra: Any,
) -> GenerationOutcome:
    return GenerationOutcome(
        arm_id=arm,
        card_id=card,
        prompt_id=prompt,
        sample_ref=f"{arm}/{card}/{prompt}/{n}",
        finish_reason=finish,
        taints=frozenset(taints),
        flags=frozenset(flags),
        **extra,
    )


def failed(
    arm: str = "qrng", n: int = 0, cause: str = "HTTP 500", **extra: Any
) -> GenerationOutcome:
    return gen(arm, n=n, finish="error", taints=("stream_error",), cause=cause, **extra)


def judged(status: str = "ok", n: int = 0, card: str = "card_j", **extra: Any) -> JudgeOutcome:
    return JudgeOutcome(
        arm_id="qrng",
        card_id=card,
        sample_ref=f"qrng/{card}/p0/{n}",
        status=status,  # type: ignore[arg-type]
        **extra,
    )


def feed(breaker: Breaker, outcomes: list[GenerationOutcome]) -> list[Trip]:
    return [trip for trip in map(breaker.feed_generation, outcomes) if trip is not None]


def as_halt(trip: Trip) -> Halt:
    """Every trip must make a valid stored halt, with the catalogue's own recovery sentence."""
    halt = Halt(at=NOW, **trip.halt_fields())
    assert halt.recovery == rc.STOP_RULES[trip.rule].recovery
    assert "{" not in halt.sentence and halt.sentence.endswith(".")
    assert halt.scope in rc.STOP_RULES[trip.rule].scopes
    assert halt.phase in rc.STOP_RULES[trip.rule].phases
    assert len(halt.sample_refs) <= 3
    return halt


def test_one_dead_arm_among_five_is_stopped_at_its_eighth_failure() -> None:
    """The dead arm's failures arrive between the other arms' successes (T1, SV-3)."""
    breaker = Breaker(qrng_arms=["qrng"])
    trips: list[Trip] = []
    for n in range(8):
        trips += feed(breaker, [failed("qrng", n)])
        trips += feed(breaker, [gen(f"prng{i}", n=n) for i in range(4)])
        if n < 7:
            assert not trips, n
    assert len(trips) == 1
    halt = as_halt(trips[0])
    assert (halt.rule, halt.scope, halt.scope_id, halt.phase) == (
        "generation_errors",
        "arm",
        "qrng",
        "generation",
    )
    assert halt.sentence == (
        "8 generations in a row on arm qrng ended in an error before a reply was complete."
    )
    assert halt.counts == {"in_a_row": 8} and halt.causes == {"HTTP 500": 8}
    assert halt.sample_refs == [f"qrng/card_a/p0/{n}" for n in (5, 6, 7)]


def test_a_completed_generation_of_the_arm_breaks_its_error_streak() -> None:
    breaker = Breaker()
    sequence = [failed(n=n) for n in range(7)] + [gen(n=7)] + [failed(n=n) for n in range(8, 15)]
    assert not feed(breaker, sequence)
    assert feed(breaker, [failed(n=15)])  # the eighth in a row


def test_the_causes_of_a_stop_are_tallied_in_the_harness_own_words() -> None:
    breaker = Breaker()
    causes = ["HTTP 502"] * 5 + ["ReadTimeout"] * 3
    trips = feed(breaker, [failed(n=n, cause=cause) for n, cause in enumerate(causes)])
    assert trips[0].causes == {"HTTP 502": 5, "ReadTimeout": 3}


def test_half_of_the_last_forty_stops_the_run_from_twenty_on() -> None:
    breaker = Breaker()
    # Failures alternate with successes over two arms: never two in a row on one arm.
    outcomes = [failed("a", n) if n % 2 == 0 else gen("a", n=n) for n in range(19)]
    assert not feed(breaker, outcomes)  # 10 of 19, but fewer than 20 have ended
    trips = feed(breaker, [gen("a", n=19)])
    assert len(trips) == 1
    halt = as_halt(trips[0])
    assert (halt.rule, halt.scope, halt.scope_id) == ("generation_errors", "run", None)
    assert halt.counts == {"failed": 10, "of_last": 20}
    assert halt.sentence == (
        "10 of the last 20 generations of the run ended in an error before a reply was complete."
    )


def test_a_third_of_generations_failing_does_not_stop_on_the_share() -> None:
    """Left to the end-of-phase rule: every third sample of every arm errors (T7)."""
    breaker = Breaker()
    outcomes = [
        failed(arm, n) if n % 3 == 2 else gen(arm, n=n) for n in range(300) for arm in ("a", "b")
    ]
    assert not feed(breaker, outcomes)


def test_what_is_not_a_failed_generation() -> None:
    breaker = Breaker()
    # An upstream reason the harness does not map (a content filter): finish error, no taint.
    refused = [gen(n=n, finish="error") for n in range(30)]
    # A cancelled generation is not counted at all.
    cancelled = [gen(n=n, finish="abort") for n in range(30)]
    assert not feed(breaker, refused + cancelled)
    # Seven failures, a cancel, a refusal: the refusal is a generation that did not fail.
    sequence = [failed(n=n) for n in range(7)] + [gen(n=7, finish="abort")]
    assert not feed(breaker, sequence)
    assert feed(Breaker(), sequence + [failed(n=8)])  # the cancel did not break the streak
    assert not feed(Breaker(), sequence + [gen(n=8, finish="error"), failed(n=9)])


def test_entropy_degraded_counts_tainted_samples_of_a_quantum_arm() -> None:
    breaker = Breaker(qrng_arms=["qrng"])
    tainted = [
        gen("qrng", n=n, taints=("engine_fallback",) if n % 2 else ("qrng_degraded",))
        for n in range(10)
    ]
    # A seeded arm can carry the same taint (the engine's fallback count is shared): no stop.
    on_seeded = [gen("prng7", n=n, taints=("engine_fallback",)) for n in range(30)]
    assert not feed(breaker, on_seeded + tainted[:9])
    trips = feed(breaker, tainted[9:])
    halt = as_halt(trips[0])
    assert (halt.rule, halt.scope_id, halt.counts) == ("entropy_degraded", "qrng", {"in_a_row": 10})
    assert halt.causes == {"engine_fallback": 5, "qrng_degraded": 5}
    assert "tainted for an engine fallback or a degraded quantum source" in halt.sentence
    # A clean sample breaks the streak; a failed generation neither extends nor breaks it.
    other = Breaker(qrng_arms=["qrng"])
    assert not feed(other, tainted[:9] + [gen("qrng", n=9)] + tainted[:9])
    third = Breaker(qrng_arms=["qrng"])
    assert not feed(third, tainted[:5] + [failed("qrng", 99)] + tainted[5:9])
    assert feed(third, tainted[9:])


def test_entropy_degraded_stops_after_three_samples_whose_probe_already_said_so() -> None:
    """Ten whole samples is hours on a thinking card; the probe knows before the request (T14)."""
    breaker = Breaker(qrng_arms=["qrng"])
    assert not feed(breaker, [gen("qrng", n=n, probe_before_bad=True) for n in range(2)])
    assert not feed(breaker, [gen("qrng", n=2)])
    trips = feed(breaker, [gen("qrng", n=n, probe_before_bad=True) for n in range(3, 6)])
    halt = as_halt(trips[0])
    assert halt.counts == {"in_a_row": 3}
    assert "entropy check reported the quantum source degraded" in halt.sentence
    assert not feed(Breaker(), [gen("prng7", n=n, probe_before_bad=True) for n in range(9)])


def test_tier0_failed_counts_taints_and_flags_per_arm() -> None:
    breaker = Breaker()
    bad = [
        gen("qrng", n=n, taints=("stateless_row",))
        if n < 4
        else gen("qrng", n=n, flags=("tier0_missing",))
        for n in range(10)
    ]
    # An arm that never asks for a token log (greedy, a provider) carries neither (T6).
    plain = [gen("greedy", n=n) for n in range(40)]
    assert not feed(breaker, plain + bad[:9])
    halt = as_halt(feed(breaker, bad[9:])[0])
    assert (halt.rule, halt.scope, halt.scope_id) == ("tier0_failed", "arm", "qrng")
    assert halt.causes == {"stateless_row": 4, "tier0_missing": 6}
    assert not feed(Breaker(), bad[:9] + [gen("qrng", n=50)] + bad[:9])


def test_scorer_errors_counts_per_card() -> None:
    breaker = Breaker()
    errors = [
        gen(arm, "card_a", n=n, flags=("score_error",), cause="KeyError")
        for n in range(5)
        for arm in ("a", "b")
    ]
    other_card = [gen("a", "card_b", n=n) for n in range(5)]
    assert not feed(breaker, errors[:9] + other_card)
    halt = as_halt(feed(breaker, errors[9:])[0])
    assert (halt.rule, halt.scope, halt.scope_id) == ("scorer_errors", "card", "card_a")
    assert halt.causes == {"KeyError": 10}
    assert halt.sentence == "The scorer of card card_a failed on 10 samples in a row."


def empty(arm: str, prompt: str, n: int, **extra: Any) -> GenerationOutcome:
    return gen(arm, "card_a", prompt, n, flags=("empty_output",), **extra)


def test_empty_outputs_needs_twenty_in_a_row_from_two_arms_and_three_prompts() -> None:
    # One arm alone, however long: emptiness in one arm is that arm's outcome (P4, SV-4).
    one_arm = [empty("qrng", f"p{n % 5}", n) for n in range(60)]
    assert not feed(Breaker(), one_arm)
    # Two arms on one prompt, as a card with k of 20 or more starts (T3).
    one_prompt = [empty(arm, "p0", n) for n in range(20) for arm in ("qrng", "prng7")]
    breaker = Breaker()
    assert not feed(breaker, one_prompt)
    assert not feed(breaker, [empty("qrng", "p1", 0)])
    halt = as_halt(feed(breaker, [empty("prng7", "p2", 0)])[0])
    assert (halt.rule, halt.scope, halt.scope_id) == ("empty_outputs", "card", "card_a")
    assert halt.counts == {"in_a_row": 42, "arms": 2, "per_arm": 5, "prompts": 3}
    assert halt.sentence == (
        "The last 42 replies stored for card card_a were all empty. They came from 3 prompts, "
        "and 2 arms each gave at least 5 of them."
    )
    # Exactly twenty is enough when they already cover the arms and prompts.
    spread = [empty(("qrng", "prng7")[n % 2], f"p{n % 3}", n) for n in range(20)]
    assert not feed(Breaker(), spread[:19])
    assert feed(Breaker(), spread)[0].counts == {
        "in_a_row": 20,
        "arms": 2,
        "per_arm": 5,
        "prompts": 3,
    }


def test_one_stray_empty_reply_from_a_second_arm_does_not_make_two_arms() -> None:
    """One empty reply from arm A and nineteen from arm B used to stop the card (P4)."""
    stray = [empty("qrng", "p0", 0)] + [empty("prng7", f"p{n % 3}", n) for n in range(19)]
    breaker = Breaker()
    assert not feed(breaker, stray)
    # However long the one arm goes on, and with up to four from the other.
    assert not feed(breaker, [empty("prng7", f"p{n % 3}", 100 + n) for n in range(60)])
    assert not feed(breaker, [empty("qrng", "p1", n) for n in range(1, 4)])
    # The fifth reply of the second arm makes it the same thing seen in two arms.
    trip = feed(breaker, [empty("qrng", "p1", 4)])[0]
    assert trip.rule == "empty_outputs"
    assert trip.counts == {"in_a_row": 84, "arms": 2, "per_arm": 5, "prompts": 3}
    # Four arms with four each are twenty in a row from no arm that gave five.
    thin = [empty(f"arm{n % 5}", f"p{n % 3}", n) for n in range(20)]
    assert not feed(Breaker(), thin)
    # A third arm with fewer than five neither helps nor hinders.
    three = [empty("c", "p0", 0)] + [empty("ab"[n % 2], f"p{n % 3}", n) for n in range(19)]
    assert feed(Breaker(), three)[0].counts["arms"] == 2


def test_what_breaks_a_run_of_empty_replies() -> None:
    spread = [empty(("qrng", "prng7")[n % 2], f"p{n % 3}", n) for n in range(20)]
    # A reply with visible text breaks the run; a failed generation does neither.
    assert not feed(Breaker(), spread[:19] + [gen("qrng", "card_a", n=99)] + spread[:19])
    assert feed(Breaker(), spread[:19] + [failed("qrng", 99)] + spread[19:])


def test_a_reply_that_reasoned_to_the_token_limit_is_not_an_empty_reply() -> None:
    """Two adjacent hard problems that use the whole budget reasoning must not stop the card (T3)."""
    exhausted = [
        empty(arm, f"p{item}", epoch, finish="length", has_reasoning=True)
        for item in (13, 14, 15)
        for epoch in range(16)
        for arm in ("qrng", "prng7")
    ]
    assert not feed(Breaker(), exhausted)
    # Without reasoning text the same replies are empty replies.
    plain = [empty(arm, f"p{item}", 0, finish="length") for item in range(10) for arm in "ab"]
    assert feed(Breaker(), plain)


def unparsed_(arm: str, prompt: str, n: int, card: str = "card_a") -> GenerationOutcome:
    return gen(arm, card, prompt, n, flags=("unparsed",))


def test_nothing_parsed_fires_at_thirty_scored_samples_from_two_prompts() -> None:
    first = [unparsed_(("a", "b")[n % 2], f"p{n // 15}", n) for n in range(30)]
    breaker = Breaker()
    assert not feed(breaker, first[:29])
    halt = as_halt(feed(breaker, first[29:])[0])
    assert (halt.rule, halt.scope, halt.scope_id) == ("nothing_parsed", "card", "card_a")
    assert halt.counts == {"scored": 30, "prompts": 2}
    assert halt.sentence == (
        "None of the first 30 scored samples of card card_a gave an answer the card's scorer "
        "could extract. They cover 2 prompts."
    )
    # One parsed sample among the thirty settles it for good.
    settled = Breaker()
    assert not feed(settled, first[:10] + [gen("a", n=500)] + first[10:] + first)
    # A card with an LLM judge is not read: its parse_status is beside the judged value.
    assert not feed(Breaker(judged_cards=["card_a"]), first)
    # A sample whose scorer failed is not a scored sample: it does not use up the window.
    errors = [gen("a", n=n, flags=("score_error",)) for n in range(5)]
    late = Breaker()
    assert not feed(late, first[:29] + errors)
    assert feed(late, first[29:])[0].counts == {"scored": 30, "prompts": 2}


def test_nothing_parsed_does_not_judge_a_card_by_one_prompt() -> None:
    """One prompt never decides a card, however many samples it has (T4)."""
    one_prompt = [unparsed_(f"arm{n % 5}", "p0", n) for n in range(200)]
    breaker = Breaker()
    assert not feed(breaker, one_prompt)
    assert breaker.end_of_card("card_a") is None


def test_nothing_parsed_counts_on_past_thirty_until_a_second_prompt() -> None:
    """The runner generates a prompt's repetitions before the next prompt.

    With 3 arms at k of 10 the first prompt alone gives thirty scored samples.
    The rule used to look at those thirty once and then never again, so a
    card on which nothing parsed ran to its end (5940 samples in the shape the
    verifier ran). It now fires with the first scored sample of the second prompt.
    """
    arms = ("qrng", "prng7", "prng8")
    stream = [
        unparsed_(arm, f"p{item}", epoch)
        for item in range(198)
        for epoch in range(10)
        for arm in arms
    ]
    breaker = Breaker()
    assert not feed(breaker, stream[:30])  # all of them on the first prompt
    halt = as_halt(feed(breaker, stream[30:31])[0])
    assert halt.counts == {"scored": 31, "prompts": 2}
    assert halt.sentence == (
        "None of the first 31 scored samples of card card_a gave an answer the card's scorer "
        "could extract. They cover 2 prompts."
    )
    # Five arms at k of 20: after the hundred samples of the first prompt.
    wide = [
        unparsed_(f"arm{arm}", f"p{item}", epoch)
        for item in range(3)
        for epoch in range(20)
        for arm in range(5)
    ]
    assert [trip.counts for trip in feed(Breaker(), wide)] == [{"scored": 101, "prompts": 2}]
    # One sample that parses, anywhere in the run from the card's start, ends the check for
    # the card: on the first prompt, past thirty, or as the first sample of the second prompt.
    for at in (0, 29, 45, 100):
        settled = Breaker()
        parsed = gen(wide[at].arm_id, "card_a", wide[at].prompt_id, 900)
        assert not feed(settled, wide[:at] + [parsed] + wide[at:] + wide)
        assert settled.end_of_card("card_a") is None
    # A sample whose scorer failed is not a scored sample, also past thirty.
    late = Breaker()
    errors = [gen("arm0", "card_a", "p1", n, flags=("score_error",)) for n in range(5)]
    assert not feed(late, wide[:100] + errors)
    assert feed(late, wide[100:101])[0].counts == {"scored": 101, "prompts": 2}


def test_nothing_parsed_gives_its_verdict_on_a_short_card_when_the_queue_ends() -> None:
    """A pilot of 3 items x 3 epochs x 3 arms in which nothing parses (T4)."""
    pilot = [unparsed_(f"arm{n % 3}", f"p{n // 9}", n) for n in range(27)]
    breaker = Breaker()
    assert not feed(breaker, pilot)
    trip = breaker.end_of_card("card_a")
    assert trip is not None
    halt = as_halt(trip)
    assert halt.counts == {"scored": 27, "prompts": 3}
    assert halt.sentence == (
        "Card card_a came to the end of its queue with 27 scored samples and none of them gave "
        "an answer the card's scorer could extract."
    )
    nine = Breaker()
    assert not feed(nine, pilot[:9]) and nine.end_of_card("card_a") is None
    parsed_once = Breaker()
    assert not feed(parsed_once, pilot[:20] + [gen("arm0", n=900)])
    assert parsed_once.end_of_card("card_a") is None
    assert Breaker(judged_cards=["card_a"]).end_of_card("card_a") is None


def test_a_restart_does_not_move_the_window_of_nothing_parsed() -> None:
    """The first thirty are counted from the stored rows, so twenty before and ten after is thirty."""
    stored = [unparsed_(("a", "b")[n % 2], f"p{n // 15}", n) for n in range(30)]
    breaker = Breaker()
    for outcome in stored[:20]:
        breaker.seed_scored("card_a", outcome.prompt_id, outcome.sample_ref, parsed=False)
    assert breaker.tripped is None  # seeding fires nothing
    assert not feed(breaker, stored[20:29])
    assert feed(breaker, stored[29:])[0].counts == {"scored": 30, "prompts": 2}
    # A window that was settled before the restart stays settled.
    settled = Breaker()
    for n in range(30):
        settled.seed_scored("card_a", f"p{n // 15}", f"a/card_a/p/{n}", parsed=n == 3)
    assert not feed(settled, stored) and settled.end_of_card("card_a") is None
    # A full window that the stop never got written for is given its verdict at the next outcome.
    crashed = Breaker()
    for outcome in stored:
        crashed.seed_scored("card_a", outcome.prompt_id, outcome.sample_ref, parsed=False)
    assert feed(crashed, [unparsed_("a", "p9", 99)])[0].counts == {"scored": 30, "prompts": 2}
    # A window that is past thirty on one prompt is rebuilt whole and goes on counting.
    long = Breaker()
    for n in range(45):
        long.seed_scored("card_a", "p0", f"a/card_a/p0/{n}", parsed=False)
    assert long.tripped is None and long.end_of_card("card_a") is None
    assert feed(long, [unparsed_("a", "p1", 0)])[0].counts == {"scored": 46, "prompts": 2}
    # A parsed sample among the stored rows past thirty settles the rebuilt window too.
    late = Breaker()
    for n in range(45):
        late.seed_scored("card_a", "p0", f"a/card_a/p0/{n}", parsed=n == 40)
    assert not feed(late, [unparsed_("a", "p1", n) for n in range(40)])


def test_cell_unfinished_is_no_sample_or_more_than_two_percent_and_at_least_three() -> None:
    assert rc.cell_unfinished(100, 0)
    assert rc.cell_unfinished(1, 0)  # the live-acceptance shape: one sample per arm, failed
    assert not rc.cell_unfinished(100, 98)  # two missing
    assert rc.cell_unfinished(100, 97)  # three missing, 3%
    assert not rc.cell_unfinished(200, 197)  # three missing, 1.5%
    assert not rc.cell_unfinished(200, 196)  # four missing is 2%, not more than 2%
    assert rc.cell_unfinished(200, 195)
    assert not rc.cell_unfinished(0, 0) and not rc.cell_unfinished(10, 10)
    assert not rc.cell_unfinished(10, 12)


def test_unfinished_fires_once_for_each_phase() -> None:
    cells = [
        PhaseCell("qrng", "card_a", planned=100, completed=60),
        PhaseCell("prng7", "card_a", planned=100, completed=100),
        PhaseCell("qrng", "card_b", planned=5, completed=0),
    ]
    breaker = Breaker()
    trip = breaker.end_of_phase("generation", cells, sample_refs=["qrng/card_a/p0/1"])
    assert trip is not None
    halt = as_halt(trip)
    assert (halt.rule, halt.scope, halt.scope_id, halt.phase) == (
        "unfinished",
        "phase",
        None,
        "generation",
    )
    assert halt.counts == {"missing": 45, "planned": 105, "cells": 2}
    assert halt.causes == {"qrng/card_a": 40, "qrng/card_b": 5}
    assert halt.sentence == (
        "Generation reached the end of its queue with 45 of 105 planned samples without a "
        "completed reply, in 2 arm and card pairs."
    )
    assert halt.sample_refs == ["qrng/card_a/p0/1"]
    # The resumed run: a second end of generation lets the run go on; judging is still checked.
    resumed = Breaker(halts=[halt])
    assert resumed.spent("unfinished", "generation") and not resumed.spent("unfinished", "judge")
    assert resumed.end_of_phase("generation", cells) is None
    judge_trip = resumed.end_of_phase("judge", cells)
    assert judge_trip is not None and judge_trip.phase == "judge"
    assert judge_trip.sentence.startswith("Judging reached the end of its queue with 45 of 105")
    assert Breaker().end_of_phase("generation", cells[1:2]) is None


def test_judge_rejected_on_a_status_that_refuses_the_key_or_the_model() -> None:
    for status in (401, 403, 404):
        breaker = Breaker()
        trip = breaker.feed_judgement(judged("failed", http_status=status, cause=f"HTTP {status}"))
        assert trip is not None
        halt = as_halt(trip)
        assert (halt.rule, halt.scope, halt.phase) == ("judge_rejected", "run", "judge")
        assert halt.counts == {"status": status} and halt.causes == {f"HTTP {status}": 1}
        assert f"HTTP status {status}" in halt.sentence
    # A 400 can be about one sample: it is a failed call, left to judge_unusable (T13, E8).
    breaker = Breaker()
    assert breaker.feed_judgement(judged("failed", http_status=400)) is None


def test_the_judge_request_before_the_first_generation() -> None:
    for status in (400, 401, 403, 404):
        trip = Breaker().judge_preflight(status)
        assert trip is not None
        halt = as_halt(trip)
        assert (halt.rule, halt.phase, halt.counts) == (
            "judge_rejected",
            "preflight",
            {"status": status},
        )
        assert "the request made before the first generation" in halt.sentence
    for status in (200, 429, 500, 529, None):
        assert Breaker().judge_preflight(status) is None
    # Checked again after Resume: an earlier halt does not switch it off.
    earlier = as_halt(Breaker().judge_preflight(401))  # type: ignore[arg-type]
    assert Breaker(halts=[earlier]).judge_preflight(401) is not None
    assert Breaker(halts=[earlier]).feed_judgement(judged("failed", http_status=401)) is not None


def test_judge_unusable_on_ten_in_a_row_or_half_of_the_last_forty() -> None:
    breaker = Breaker()
    calls = [judged("failed", n, cause="HTTP 529") for n in range(10)]
    assert not [t for t in map(breaker.feed_judgement, calls[:9]) if t]
    halt = as_halt(breaker.feed_judgement(calls[9]))  # type: ignore[arg-type]
    assert (halt.rule, halt.scope, halt.counts) == ("judge_unusable", "run", {"in_a_row": 10})
    assert halt.causes == {"HTTP 529": 10}
    assert halt.sentence == "10 judge calls in a row got no reply."
    # Nine failures in every ten never make ten in a row; the share catches them (T8).
    share = Breaker()
    trips = [
        trip
        for n in range(20)
        if (trip := share.feed_judgement(judged("ok" if n % 10 == 9 else "failed", n))) is not None
    ]
    assert len(trips) == 1 and trips[0].counts == {"failed": 18, "of_last": 20}
    assert trips[0].sentence == "18 of the last 20 judge calls got no reply."
    # A reply breaks the streak, whatever could be read from it.
    mixed = Breaker()
    sequence = [judged("failed", n) for n in range(9)] + [judged("unreadable", 9)]
    assert not [t for t in map(mixed.feed_judgement, sequence + sequence[:5]) if t]


def test_nothing_judged_looks_at_the_first_thirty_replies_of_a_card() -> None:
    breaker = Breaker()
    # Calls that got no reply are not replies; they are spread out so the run rules stay quiet.
    replies = [judged("unreadable", n) for n in range(30)]
    for n, reply in enumerate(replies[:29]):
        assert breaker.feed_judgement(reply) is None
        if n % 5 == 0:
            assert breaker.feed_judgement(judged("failed", 100 + n)) is None
    halt = as_halt(breaker.feed_judgement(replies[29]))  # type: ignore[arg-type]
    assert (halt.rule, halt.scope, halt.scope_id) == ("nothing_judged", "card", "card_j")
    assert halt.counts == {"replies": 30}
    assert halt.sentence == (
        "No score could be read from any of the first 30 judge replies for card card_j."
    )
    # A value the rubric does not allow was still read: the reader works.
    read = Breaker()
    sequence = replies[:12] + [judged("out_of_range", 50)] + replies[12:] + replies
    assert not [t for t in map(read.feed_judgement, sequence) if t]
    # Counted from the stored rows after a restart.
    restarted = Breaker()
    for reply in replies[:25]:
        restarted.seed_judged("card_j", reply.sample_ref, readable=False)
    assert not [t for t in map(restarted.feed_judgement, replies[25:29]) if t]
    assert restarted.feed_judgement(replies[29]) is not None


def test_judge_phase_error_always_stops() -> None:
    trip = Breaker().judge_phase_error("FileNotFoundError")
    assert trip is not None
    halt = as_halt(trip)
    assert (halt.rule, halt.scope, halt.phase) == ("judge_phase_error", "run", "judge")
    assert halt.causes == {"FileNotFoundError": 1} and halt.counts == {}
    # Never switched off, however often it stopped the run before.
    assert Breaker(halts=[halt, halt, halt]).judge_phase_error("FileNotFoundError") is not None


def test_the_first_trip_latches() -> None:
    """Samples that were in flight when a rule fired must not write a second halt."""
    breaker = Breaker(qrng_arms=["qrng"])
    assert breaker.tripped is None
    first = feed(breaker, [failed(n=n) for n in range(8)])
    assert len(first) == 1 and breaker.tripped is first[0]
    assert not feed(breaker, [failed(n=n) for n in range(8, 40)])
    assert not feed(breaker, [gen("qrng", n=n, taints=("qrng_degraded",)) for n in range(20)])
    assert breaker.feed_judgement(judged("failed", http_status=401)) is None
    assert breaker.judge_preflight(401) is None
    assert breaker.judge_phase_error("ValueError") is None
    assert breaker.end_of_card("card_a") is None
    assert breaker.end_of_phase("generation", [PhaseCell("a", "c", 10, 0)]) is None
    assert breaker.tripped is first[0]


def test_feeding_from_many_threads_gives_one_trip() -> None:
    breaker = Breaker()
    trips: list[Trip] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def work(worker: int) -> None:
        start.wait()
        for n in range(200):
            trip = breaker.feed_generation(failed("qrng", worker * 1000 + n))
            if trip is not None:
                with lock:
                    trips.append(trip)

    threads = [threading.Thread(target=work, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(trips) == 1 and trips[0].counts == {"in_a_row": 8}


def test_a_resumed_run_counts_again_and_keeps_one_shot_rules_spent() -> None:
    """Built from the run's earlier halts: a second outage is still caught (T2, E3, O3)."""
    first = Breaker(qrng_arms=["qrng"])
    errors_halt = as_halt(feed(first, [failed(n=n) for n in range(8)])[0])
    spread = [empty(("qrng", "prng7")[n % 2], f"p{n % 3}", n) for n in range(20)]
    empty_halt = as_halt(feed(Breaker(), spread)[0])
    parsed_halt = as_halt(
        feed(Breaker(), [unparsed_("a", f"p{n // 15}", n, card="card_b") for n in range(30)])[0]
    )
    judged_halt = as_halt(
        [
            t
            for t in map(Breaker().feed_judgement, [judged("unreadable", n) for n in range(30)])
            if t
        ][0]
    )
    # Halts are accepted as records and as their dumped JSON.
    halts: list[Any] = [
        errors_halt,
        empty_halt.model_dump(mode="json"),
        parsed_halt,
        judged_halt.model_dump(mode="json"),
    ]
    resumed = Breaker(qrng_arms=["qrng"], halts=halts)
    # Counting rule: armed, from zero.
    assert not feed(resumed, [failed(n=n) for n in range(7)])
    assert resumed.tripped is None
    # One-shot rules: spent for the card they stopped, armed for every other card.
    assert resumed.spent("empty_outputs", "card_a") and not resumed.spent("empty_outputs", "card_b")
    assert resumed.spent("nothing_parsed", "card_b") and resumed.spent("nothing_judged", "card_j")
    assert not feed(resumed, [gen("qrng", n=500)] + spread + spread)
    assert not feed(resumed, [unparsed_("a", f"p{n // 15}", n, card="card_b") for n in range(60)])
    assert resumed.end_of_card("card_b") is None
    assert not [
        t for t in map(resumed.feed_judgement, [judged("unreadable", n) for n in range(40)]) if t
    ]
    other_card = [unparsed_("a", f"p{n // 15}", n, card="card_c") for n in range(30)]
    assert feed(resumed, other_card)[0].scope_id == "card_c"
    # And the counting rule that stopped the first run fires again on a second outage.
    again = Breaker(qrng_arms=["qrng"], halts=halts)
    assert feed(again, [failed(n=n) for n in range(8)])[0].rule == "generation_errors"


def test_every_rule_can_fire_and_every_halt_sentence_is_used() -> None:
    """No rule and no sentence of the catalogue is dead text."""
    trips = [
        feed(Breaker(), [failed(n=n) for n in range(8)])[0],
        feed(Breaker(), [failed("a", n) if n % 2 == 0 else gen("a", n=n) for n in range(20)])[0],
        Breaker().inspect_incomplete("a", "a/novelty_bench/p0/0", "HTTP 500"),
        feed(
            Breaker(qrng_arms=["q"]), [gen("q", n=n, taints=("qrng_degraded",)) for n in range(10)]
        )[0],
        feed(Breaker(qrng_arms=["q"]), [gen("q", n=n, probe_before_bad=True) for n in range(3)])[0],
        feed(Breaker(), [gen(n=n, taints=("stateless_row",)) for n in range(10)])[0],
        feed(Breaker(), [gen(n=n, flags=("score_error",)) for n in range(10)])[0],
        feed(Breaker(), [empty(("a", "b")[n % 2], f"p{n % 3}", n) for n in range(20)])[0],
        feed(Breaker(), [unparsed_("a", f"p{n // 15}", n) for n in range(30)])[0],
        Breaker().end_of_phase("generation", [PhaseCell("a", "c", 10, 0)]),
        Breaker().end_of_phase("judge", [PhaseCell("a", "c", 10, 0)]),
        Breaker().judge_preflight(400),
        Breaker().feed_judgement(judged("failed", http_status=403)),
        [t for t in map(Breaker().feed_judgement, [judged("failed", n) for n in range(10)]) if t][
            0
        ],
        Breaker().judge_phase_error("RuntimeError"),
    ]
    short = Breaker()
    feed(short, [unparsed_("a", "p0", n) for n in range(12)])
    trips.append(short.end_of_card("card_a"))
    share = Breaker()
    trips += [
        t
        for n in range(20)
        if (t := share.feed_judgement(judged("ok" if n % 10 == 9 else "failed", n))) is not None
    ]
    trips += [
        t for t in map(Breaker().feed_judgement, [judged("unreadable", n) for n in range(30)]) if t
    ]
    assert all(trip is not None for trip in trips)
    halts = [as_halt(trip) for trip in trips if trip is not None]
    assert {halt.rule for halt in halts} == set(rc.STOP_RULES)
    assert {halt.sentence.split(" ")[0] for halt in halts}  # rendered, not templates
    assert len(halts) == len(rc._SAW)
