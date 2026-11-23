"""The documents say about the result checks what the code says.

``docs/ANALYSIS_PLAN.md`` ("Result checks") is the section the setup links to
and the one a reader is sent to from the pre-registration. It prints the
sentence of every flag kind and stop rule, the lists of the catalogue and its
thresholds. ``docs/SETUP_GUIDE.md`` tells an operator what to do after each
stop rule, ``docs/API.md`` gives the shapes and ``docs/add-an-eval.md`` says
how an eval gets on the catalogue's lists.

Each test here fails when the catalogue (``cards/result_checks.py``) or a
record changes and the document that describes it does not. No sentence about
a check may stand in a document without the code behind it (AGENTS.md
invariant 10).
"""

from __future__ import annotations

import re
from typing import get_args

import pytest

from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.runner.events import EVENT_TYPES
from qr_llm_evals.store.health import (
    MADE_BEFORE_RESULT_CHECKS,
    CautionKind,
    CellHealth,
    HealthSummary,
    JudgementKey,
)
from qr_llm_evals.store.schema import Halt, ResultFlag, TaintKind

DOCS = REPO_ROOT / "docs"


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _plain(text: str) -> str:
    """Text as a reader takes it in: no code marks, one space between words."""
    return " ".join(text.replace("`", "").split())


def _part(doc: str, heading: str) -> str:
    """The text under ``heading``, up to the next heading of the same or a higher level."""
    level = heading.split(" ", 1)[0]
    start = doc.index(f"\n{heading}\n") + 1
    following = re.compile(rf"^#{{1,{len(level)}}} ", re.MULTILINE)
    end = following.search(doc, start + len(heading))
    return doc[start : end.start() if end else len(doc)]


def _entries(part: str) -> list[str]:
    """The top-level list entries of a part, each as one string."""
    return [entry for entry in re.split(r"\n(?=- \*\*)", part) if entry.startswith("- **")]


def _entry(part: str, name: str) -> str:
    found = [entry for entry in _entries(part) if f"(`{name}`" in entry]
    assert len(found) == 1, f"{name}: {len(found)} entries"
    return found[0]


def _named(part: str) -> list[str]:
    """The record names of a part's entries, in the order they are printed."""
    return re.findall(r"^- \*\*[^*\n]+\*\* \(`([a-z0-9_]+)`", part, re.MULTILINE)


@pytest.fixture(scope="module")
def plan() -> str:
    return _part(_read("ANALYSIS_PLAN.md"), "## Result checks")


# ── docs/ANALYSIS_PLAN.md ─────────────────────────────────────────────────────


def test_the_setup_links_to_a_section_that_exists() -> None:
    """The Design step says: See "Result checks" in the analysis plan."""
    fixed_rules = (
        REPO_ROOT / "apps" / "web" / "src" / "components" / "study" / "design" / "FixedRules.tsx"
    ).read_text(encoding="utf-8")
    assert 'See "Result checks" in the analysis plan.' in fixed_rules
    assert 'RESULT_CHECKS_DOC = "/docs/ANALYSIS_PLAN"' in fixed_rules
    assert "\n## Result checks\n" in _read("ANALYSIS_PLAN.md")


def test_the_plan_lists_the_flag_kinds_of_the_catalogue_and_no_other(plan: str) -> None:
    assert _named(_part(plan, "### Flags")) == list(rc.FLAG_KINDS)


@pytest.mark.parametrize("kind", list(rc.FLAG_KINDS))
def test_the_plan_prints_each_flag_kind_in_the_catalogue_s_words(plan: str, kind: str) -> None:
    spec = rc.FLAG_KINDS[kind]
    entry = _entry(_part(plan, "### Flags"), kind)
    assert entry.startswith(f"- **{spec.label}** (`{kind}`")
    assert _plain(spec.sentence) in _plain(entry)


def test_the_plan_lists_the_stop_rules_of_the_catalogue_and_no_other(plan: str) -> None:
    assert _named(_part(plan, "### Stop rules")) == list(rc.STOP_RULES)


@pytest.mark.parametrize("rule", list(rc.STOP_RULES))
def test_the_plan_prints_each_stop_rule_and_what_resume_does_after_it(plan: str, rule: str) -> None:
    spec = rc.STOP_RULES[rule]
    entry = _entry(_part(plan, "### Stop rules"), rule)
    assert entry.startswith(f"- **{spec.label}** (`{rule}`")
    assert _plain(spec.sentence) in _plain(entry)
    assert _plain(spec.recovery) in _plain(entry)


def test_the_plan_says_which_rules_are_not_applied_again(plan: str) -> None:
    resume = _plain(_part(plan, "### What a stop does, and what Resume does"))
    once = sorted(rc.ONE_SHOT_CARD_RULES, key=list(rc.STOP_RULES).index)
    assert f"({', '.join(once)})" in resume
    assert f"up to {rc.HALT_SAMPLE_REFS} samples" in resume


def test_the_plan_repeats_what_the_checks_do_not_detect(plan: str) -> None:
    limits = _plain(_part(plan, "### What the checks do not do")).lower()
    for phrase in rc.NOT_DETECTED:
        assert f"- {_plain(phrase).lower()}." in limits


def test_the_plan_names_the_evals_whose_parse_status_is_read_and_those_left_out(plan: str) -> None:
    flags = _part(plan, "### Flags")
    listed = ", ".join(f"`{card}`" for card in sorted(rc.PARSE_STATUS_CARDS))
    assert _plain(listed) in _plain(flags)
    left_out = re.findall(r"^- `([a-z0-9_]+)`: ", flags, re.MULTILINE)
    assert sorted(left_out) == sorted(rc.PARSE_STATUS_NOT_THE_ANALYSED_VALUE)
    for card, reason in rc.PARSE_STATUS_NOT_THE_ANALYSED_VALUE.items():
        assert f"- {card}: {_plain(reason)}." in _plain(flags)


def test_the_plan_names_the_taint_kinds_of_the_store_and_no_other(plan: str) -> None:
    taints = _part(plan, "### Taints: what is excluded")
    assert sorted(re.findall(r"^- `([a-z_]+)`: ", taints, re.MULTILINE)) == sorted(
        get_args(TaintKind)
    )


def test_the_plan_states_the_judge_reader_s_allowed_values(plan: str) -> None:
    reader = _plain(_part(plan, "### The judge reader"))
    assert f"reader version {rc.JUDGE_READER_VERSION}" in reader
    by_values: dict[tuple[float, ...], list[str]] = {}
    for card, values in rc.JUDGE_ALLOWED_VALUES.items():
        by_values.setdefault(tuple(sorted(values)), []).append(card)
    for values, cards in by_values.items():
        stated = f"{', '.join(sorted(cards))}: {', '.join(f'{value:g}' for value in values)}"
        assert stated in reader
    for status in get_args(rc.JudgeStatus):
        assert f"\n- `{status}`: " in _part(plan, "### The judge reader")


def test_the_plan_states_the_version_and_the_numbers_it_adds_in_its_own_words(plan: str) -> None:
    text = _plain(plan)
    assert f"version {rc.CHECKS_VERSION} of the checks" in text
    share = f"{rc.CAUTION_SHARE * 100:g}%"
    assert f"at least {share} of an arm's completed samples" in text
    assert MADE_BEFORE_RESULT_CHECKS in text
    # The stop rule's request before the first generation, as the runner makes it.
    statuses = sorted(rc.JUDGE_REJECTED_PREFLIGHT_STATUSES)
    stated = ", ".join(str(status) for status in statuses[:-1]) + f" or {statuses[-1]}"
    assert f"A status of {stated} stops the run." in text


def test_no_analysis_module_reads_a_flag_a_caution_or_a_stop(plan: str) -> None:
    """The plan says a flag changes nothing in any analysis. That holds while nothing there reads one."""
    assert "no test, table, figure, verdict or narrative reads that file" in _plain(plan)
    assert "no analysis reads a flag, a caution or a stop" in _plain(plan)
    reads = re.compile(
        r"flags\.jsonl|flags_jsonl|result_checks|store\.health|ResultFlag|\bhalts\b|checks_version"
    )
    analysis = REPO_ROOT / "backend" / "src" / "qr_llm_evals" / "analysis"
    sources = sorted(analysis.rglob("*.py"))
    assert sources
    for path in sources:
        found = reads.search(path.read_text(encoding="utf-8"))
        assert found is None, f"{path.name} names {found.group(0) if found else ''}"


# ── docs/SETUP_GUIDE.md ───────────────────────────────────────────────────────

_WORDS = {2: "two", 3: "three", 8: "eight", 10: "ten", 20: "twenty"}

#: The numbers an operator is told for each rule. A threshold that changes
#: in the catalogue has to change in the guide.
_GUIDE_NUMBERS: dict[str, tuple[int, ...]] = {
    "generation_errors": (rc.GENERATION_ERRORS_STREAK, rc.SHARE_WINDOW),
    "entropy_degraded": (rc.ENTROPY_DEGRADED_STREAK, rc.ENTROPY_PROBE_STREAK),
    "tier0_failed": (rc.TIER0_FAILED_STREAK,),
    "scorer_errors": (rc.SCORER_ERRORS_STREAK,),
    "empty_outputs": (
        rc.EMPTY_OUTPUTS_STREAK,
        rc.EMPTY_OUTPUTS_MIN_ARMS,
        rc.EMPTY_OUTPUTS_MIN_PER_ARM,
        rc.EMPTY_OUTPUTS_MIN_PROMPTS,
    ),
    "nothing_parsed": (rc.NOTHING_PARSED_FIRST, rc.NOTHING_PARSED_MIN_PROMPTS),
    "unfinished": (rc.UNFINISHED_MIN_MISSING,),
    "judge_rejected": tuple(sorted(rc.JUDGE_REJECTED_PREFLIGHT_STATUSES)),
    "judge_unusable": (rc.JUDGE_UNUSABLE_STREAK, rc.SHARE_WINDOW),
    "nothing_judged": (rc.NOTHING_JUDGED_FIRST,),
    "judge_phase_error": (),
}


@pytest.fixture(scope="module")
def guide() -> str:
    return _part(_read("SETUP_GUIDE.md"), "### When a check stops a run")


def test_the_guide_has_an_entry_for_every_stop_rule_and_no_other(guide: str) -> None:
    assert _named(guide) == list(rc.STOP_RULES)
    assert set(_GUIDE_NUMBERS) == set(rc.STOP_RULES)


@pytest.mark.parametrize("rule", list(rc.STOP_RULES))
def test_the_guide_gives_each_rule_its_real_thresholds(guide: str, rule: str) -> None:
    entry = _entry(guide, rule)
    spec = rc.STOP_RULES[rule]
    assert entry.lower().startswith(f"- **{spec.label}** (`{rule}`")
    for number in _GUIDE_NUMBERS[rule]:
        said = rf"\b({number}|{_WORDS[number]})\b" if number in _WORDS else rf"\b{number}\b"
        assert re.search(said, entry, re.IGNORECASE), f"{rule}: {number} is not stated"
    if rule == "unfinished":
        assert f"{rc.UNFINISHED_MAX_SHARE * 100:g}%" in entry


def test_the_guide_says_what_the_run_page_and_the_server_say(guide: str) -> None:
    text = _plain(guide)
    notice = (
        REPO_ROOT / "apps" / "web" / "src" / "components" / "run" / "StopNotice.tsx"
    ).read_text(encoding="utf-8")
    assert "Stopped by a check" in text and ">Stopped by a check<" in notice
    registry = (REPO_ROOT / "backend" / "src" / "qr_llm_evals" / "api" / "registry.py").read_text(
        encoding="utf-8"
    )
    # Resume is refused while the requests under way are still finishing.
    assert "resume once the run is paused" in registry
    assert "Resume is refused until they have" in text


# ── docs/API.md ───────────────────────────────────────────────────────────────


def test_the_api_document_lists_every_event_the_runner_can_send() -> None:
    events = _part(_read("API.md"), "## SSE: `GET /runs/{id}/events`")
    for event_type in EVENT_TYPES:
        assert f"`{event_type}`" in events, event_type
    row = next(line for line in events.splitlines() if line.startswith("| `check.stopped` |"))
    for field in Halt.model_fields:
        assert field in row, field
    finished = next(
        line for line in events.splitlines() if line.startswith("| `sample.finished` |")
    )
    assert "flags" in finished and "{kind, detail, count}" in finished


def test_the_api_document_gives_the_shapes_of_the_records() -> None:
    api = _read("API.md")
    checks = _part(api, "## Result checks")
    halt = _part(checks, "### A stop: `Halt`")
    assert re.findall(r"^\| `([a-z_]+)` \|", halt, re.MULTILINE) == list(Halt.model_fields)
    for rule in rc.STOP_RULES:
        assert f"`{rule}`" in halt
    for value in (*get_args(rc.StopScope), *get_args(rc.HaltPhase)):
        assert f"`{value}`" in halt
    assert f"Up to {rc.HALT_SAMPLE_REFS} samples" in halt

    health = _part(checks, "### The health summary: `GET /runs/{id}/health`")
    for field in (*HealthSummary.model_fields, *CellHealth.model_fields):
        assert f"`{field}`" in health, field
    for value in (*get_args(CautionKind), *get_args(JudgementKey)):
        assert f"`{value}`" in health, value
    assert MADE_BEFORE_RESULT_CHECKS in _plain(health)
    assert f"{rc.CAUTION_SHARE * 100:g}%" in health

    records = _plain(_part(checks, "### Records in the run folder and the bundle"))
    for field in ResultFlag.model_fields:
        assert field in records, field
    for phase in get_args(rc.FlagPhase):
        assert phase in records
    for field in ("status", "stop_reason", "reader_version"):
        assert field in records
    assert "| GET | `/runs/{id}/health` | none |" in api


# ── docs/add-an-eval.md ───────────────────────────────────────────────────────


def test_the_eval_guide_names_lists_and_tests_that_exist() -> None:
    guide = _read("add-an-eval.md")
    for name in (
        "PARSE_STATUS_CARDS",
        "PARSE_STATUS_NOT_THE_ANALYSED_VALUE",
        "JUDGE_ALLOWED_VALUES",
    ):
        assert f"`{name}`" in guide
        assert hasattr(rc, name)
    tests = (REPO_ROOT / "backend" / "tests" / "test_result_checks.py").read_text(encoding="utf-8")
    for test in (
        "test_every_card_that_writes_parse_status_is_decided",
        "test_the_allowed_values_are_the_rubrics_own",
        "test_unparsed_reads_what_the_cards_really_write",
    ):
        assert f"`{test}`" in guide
        assert f"def {test}(" in tests
    # The reader no longer takes the last number of a reply, and the guide must not say it does.
    assert rc.parse_score("I would say 7 out of 10.") is None
    assert "the last number in" not in _plain(guide)
    assert (
        f"at least {rc.NOTHING_PARSED_FIRST} scored samples from at least "
        f"{rc.NOTHING_PARSED_MIN_PROMPTS} prompts"
    ) in _plain(guide)


def test_the_plan_states_the_limits_of_the_rules_that_read_replies(plan: str) -> None:
    """What the rules' sentences leave open is said in the plan, with the catalogue's numbers."""
    text = _plain(_part(plan, "### Stop rules"))
    assert (
        f"at least {rc.EMPTY_OUTPUTS_MIN_PROMPTS} prompts and at least "
        f"{rc.EMPTY_OUTPUTS_MIN_ARMS} arms have each given at least "
        f"{rc.EMPTY_OUTPUTS_MIN_PER_ARM} of them"
    ) in text
    assert (
        f"at least {rc.NOTHING_PARSED_FIRST} samples from at least "
        f"{rc.NOTHING_PARSED_MIN_PROMPTS} prompts"
    ) in text
    assert f"the count then goes on past {rc.NOTHING_PARSED_FIRST}" in text
    assert f"fewer than {rc.NOTHING_PARSED_QUEUE_END_MIN} scored samples" in text
    # The limit this section used to state is gone with the behaviour it described.
    assert "samples are looked at once" not in text
    assert "does not fire for that eval at all" not in text
    # A reply the provider ended for its own reason is not a missing sample.
    assert "is taken off the planned number before the comparison" in text
