"""The "Result checks" section of the designed pre-registration.

The section is printed from the catalogue (``cards/result_checks.py``), so it
cannot name a flag or a stop rule that does not exist, or leave one out. These
tests read the rendered document and hold it against the catalogue, the
store's taint kinds and the analysis's exclusion rule.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, get_args

import pytest

from qr_llm_evals.analysis.endpoints import excluded_refs
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.models.schema import StudyConfig
from qr_llm_evals.prereg import load_renderer
from qr_llm_evals.prereg.render import _LIFTED_TAINT, _TAINT_TEXT, _design_text
from qr_llm_evals.store.schema import TaintKind

CONTRACT_FIXTURES = REPO_ROOT / "packages/contracts/fixtures"
TITLE = "Result checks"
#: ``- **label** (name): text`` — a flag kind or a stop rule as the section lists it.
NAMED = re.compile(r"^- \*\*(?P<label>.+?)\*\* \((?P<name>[^)]+)\): (?P<text>.+)$", re.M)
#: ``- name: text.`` — a taint kind.
TAINT = re.compile(r"^- (?P<name>[a-z\\_]+): (?P<text>.+)\.$", re.M)


def _designed() -> StudyConfig:
    data: dict[str, Any] = json.loads(
        (CONTRACT_FIXTURES / "study1.designed.json").read_text(encoding="utf-8")
    )
    return StudyConfig.model_validate(data)


def _section(document: str) -> str:
    start = document.index(f"\n## {TITLE}\n")
    end = document.index("\n## ", start + 1)
    return document[start:end]


def _part(section: str, opening: str) -> str:
    """One part of the section: from its bold opening sentence to the next one."""
    start = section.index(f"\n**{opening}**")
    end = section.find("\n**", start + 1)
    return section[start:] if end == -1 else section[start:end]


def _name(escaped: str) -> str:
    return escaped.replace("\\_", "_")


@pytest.fixture(scope="module")
def section() -> str:
    return _section(load_renderer().render(_designed()))


def test_every_flag_kind_in_the_section_is_the_catalogue_s_and_none_is_missing(
    section: str,
) -> None:
    listed = {
        _name(m["name"]): (m["label"], m["text"])
        for m in NAMED.finditer(_part(section, "A flag excludes nothing."))
    }
    assert list(listed) == list(rc.FLAG_KINDS)
    for kind, spec in rc.FLAG_KINDS.items():
        assert listed[kind] == (_design_text(spec.label), _design_text(spec.sentence))


def test_every_stop_rule_in_the_section_is_the_catalogue_s_and_none_is_missing(
    section: str,
) -> None:
    listed = {
        _name(m["name"]): (m["label"], m["text"])
        for m in NAMED.finditer(_part(section, "What stops a run."))
    }
    assert list(listed) == list(rc.STOP_RULES)
    for rule, spec in rc.STOP_RULES.items():
        # What the rule looks for, then what Resume recovers, both as the catalogue has them.
        assert listed[rule] == (
            _design_text(spec.label),
            f"{_design_text(spec.sentence)} {_design_text(spec.recovery)}",
        )


def test_no_other_name_of_a_check_appears_in_the_section(section: str) -> None:
    """Every word with an underscore is a flag kind, a stop rule or a taint kind."""
    known = {*rc.FLAG_KINDS, *rc.STOP_RULES, *get_args(TaintKind)}
    named = {_name(word) for word in re.findall(r"[a-z0-9]+(?:\\_[a-z0-9]+)+", section)}
    assert named <= known
    assert named >= {kind for kind in known if "_" in kind}


def test_the_section_states_the_version_of_the_checks(
    section: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert f"This document describes version {rc.CHECKS_VERSION} of these checks." in section
    monkeypatch.setattr(rc, "CHECKS_VERSION", rc.CHECKS_VERSION + 6)
    later = _section(load_renderer().render(_designed()))
    assert f"This document describes version {rc.CHECKS_VERSION} of these checks." in later
    assert later != section


def test_the_section_is_printed_from_the_catalogue_not_from_a_list_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kind or a rule added to the catalogue shows in the document with no other change."""
    kind = replace(rc.FLAG_KINDS["garbled"], label="made up label", sentence="A made up flag.")
    rule = replace(
        rc.STOP_RULES["scorer_errors"],
        label="made up rule",
        sentence="A made up stop.",
        recovery="Resume does a made up thing.",
    )
    monkeypatch.setitem(rc.FLAG_KINDS, "made_up_flag", kind)
    monkeypatch.setitem(rc.STOP_RULES, "made_up_rule", rule)
    monkeypatch.setattr(rc, "NOT_DETECTED", (*rc.NOT_DETECTED, "a made up gap"))
    section = _section(load_renderer().render(_designed()))
    assert "- **made up label** (garbled): A made up flag." in section
    assert (
        "- **made up rule** (scorer\\_errors): A made up stop. Resume does a made up thing."
        in section
    )
    assert "nonsense written in valid Unicode; a made up gap." in section


def test_what_the_checks_do_not_detect_is_the_catalogue_s_list(section: str) -> None:
    listed = _design_text("; ".join(rc.NOT_DETECTED))
    assert f"The checks do not detect the following: {listed}.\n" in section


def test_the_taints_in_the_section_are_the_store_s_taint_kinds(section: str) -> None:
    taints = _part(section, "A taint excludes.")
    listed = {_name(m["name"]): m["text"] for m in TAINT.finditer(taints)}
    assert set(listed) == set(get_args(TaintKind)) == set(_TAINT_TEXT)
    for kind, when in _TAINT_TEXT.items():
        assert listed[kind] == _design_text(when)


def test_the_taint_the_section_says_a_completed_reply_lifts_is_the_one_the_analysis_lifts(
    section: str, tmp_path: Path
) -> None:
    """The sentence about a retried sample states the rule the analysis applies."""
    taints = _part(section, "A taint excludes.")
    lifted = re.search(
        r"A (\S+) taint does not exclude a sample that has a completed reply\.", taints
    )
    assert lifted is not None and _name(lifted.group(1)) == _LIFTED_TAINT
    assert "A sample that also carries another taint stays out" in taints
    assert "a sample with no completed reply is not analysed" in taints

    rows = [{"sample_ref": f"a/c/{kind}/0", "kind": kind} for kind in get_args(TaintKind)]
    (tmp_path / "taints.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    completed = {row["sample_ref"] for row in rows}
    kept = completed - excluded_refs(tmp_path, completed)
    assert kept == {f"a/c/{_LIFTED_TAINT}/0"}
    # Without a completed reply the same taint excludes, as the section says.
    assert excluded_refs(tmp_path, set()) == completed


def test_the_section_says_what_a_flag_and_a_stop_are(section: str) -> None:
    flags = _part(section, "A flag excludes nothing.")
    assert (
        "A flagged sample stays in every analysis exactly as an unflagged one, and no test or "
        "verdict reads a flag."
    ) in flags
    stops = _part(section, "What stops a run.")
    assert "A stop is a pause, not an abort." in stops
    assert "The run does not continue by itself: the host resumes it or aborts it." in stops
    assert "the stops of a run are listed on the results page" in stops
    assert (
        "No completed reply is generated again and no judge reply that was received is asked "
        "for again because of what it says, with one exception: where an eval reads the judge's "
        "reply with its own code and that code fails on a reply, the judgement counts as failed "
        "and is asked again on resume."
    ) in stops


def test_a_study_without_a_design_block_has_no_such_section() -> None:
    legacy = json.loads((CONTRACT_FIXTURES / "study1.json").read_text(encoding="utf-8"))
    document = load_renderer().render(StudyConfig.model_validate(legacy))
    assert f"## {TITLE}" not in document and "version 1 of these checks" not in document
