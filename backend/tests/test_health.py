"""The health summary of a run folder (``store/health.py``).

Each test writes a small run with the store's own writers and reads it back
through :func:`summarise`. Nothing here touches ``analysis/``: the summary is a
reading aid and no verdict depends on it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.store import HealthSummary, Store, summarise
from qr_llm_evals.store.health import (
    MADE_BEFORE_RESULT_CHECKS,
    UNKNOWN_ROLE,
    summarise_run_dir,
)
from qr_llm_evals.store.schema import (
    ArmProgress,
    CardProgress,
    GenerationRow,
    Halt,
    JudgeRow,
    Progress,
    Provenance,
    ResultFlag,
    Run,
    Taint,
    Timing,
)
from qr_llm_evals.store.store import RunStore

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
H = "a" * 64
CARD = "eval_awareness"
ARMS = ("qrng", "prng7", "bent")


def _study() -> StudyConfig:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    return StudyConfig.model_validate(
        {
            "title": "Health",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [model],
            "arms": [
                {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"},
                {
                    "id": "prng7",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "prng_seeded",
                    "study_seed": 7,
                },
                {
                    "id": "bent",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "prng_epsilon",
                    "study_seed": 42,
                    "epsilon": 0.5,
                },
            ],
            "cards": [{"card_id": CARD}, {"card_id": "creative_diversity"}],
            "judge": {"model": "claude-sonnet-4"},
            "narrative_model": model,
            "expectations": "None.",
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "data")
    yield s  # type: ignore[misc]
    s.close()


def _new_run(store: Store, **fields: Any) -> RunStore:
    study = _study()
    study_id = store.freeze_study(study, "# Prereg\n").study_id
    run = Run.model_validate(
        {
            "run_id": "run1",
            "study_id": study_id,
            "kind": "full",
            "created_at": NOW,
            "key_name": "k",
            "host_token_hash": H,
            "config_hash": config_hash(study),
            "checks_version": 1,
            **fields,
        }
    )
    return store.create_run(run)


def _ref(arm: str, n: int, card: str = CARD) -> str:
    return f"{arm}/{card}/p{n}/0"


def _generate(rs: RunStore, arm: str, n: int, finish: str = "stop", card: str = CARD) -> str:
    ref = _ref(arm, n, card)
    quantum = arm == "qrng"
    rs.append_generation(
        GenerationRow(
            sample_ref=ref,
            arm_id=arm,
            card_id=card,
            prompt_id=f"p{n}",
            epoch=0,
            messages_hash=H,
            output="reply",
            finish_reason=finish,  # type: ignore[arg-type]
            provenance=Provenance(
                model_id="qr-llm",
                served_name="qr-llm",
                sampling="creative",
                envelope_hash=H,
                source_label="quantum" if quantum else "seeded_prng",
                quantum=quantum,
                determinism_tier="none",
                pipeline_pinned=True,
            ),
            timing=Timing(started=NOW),
            request_id=ref,
        )
    )
    return ref


def _flag(rs: RunStore, ref: str, kind: str) -> None:
    arm, card = ref.split("/")[:2]
    rs.append_flag(
        ResultFlag(
            sample_ref=ref,
            kind=kind,  # type: ignore[arg-type]
            phase=rc.FLAG_PHASE[kind],
            card_id=card,
            arm_id=arm,
            at=NOW,
        )
    )


def _judge(rs: RunStore, ref: str, **fields: Any) -> None:
    arm, card = ref.split("/")[:2]
    row = JudgeRow.model_validate(
        {
            "sample_ref": ref,
            "judge_id": "study_judge",
            "model": "m",
            "rubric_hash": H,
            "at": NOW,
            **fields,
        }
    )
    rs.append_judge(arm, card, row)


def _cells(summary: HealthSummary) -> dict[tuple[str, str], Any]:
    return {(cell.arm_id, cell.card_id): cell for cell in summary.cells}


def test_the_summary_counts_what_each_arm_and_card_pair_holds(store: Store) -> None:
    progress = Progress(
        by_arm={arm: ArmProgress(by_card={CARD: CardProgress(done=0, total=12)}) for arm in ARMS}
    )
    rs = _new_run(store, state="complete", progress=progress)
    for arm in ARMS:
        for n in range(10):
            _generate(rs, arm, n)
    # Two samples of the quantum arm never completed; a third failed once and was tried again.
    for n in (10, 11):
        ref = _generate(rs, "qrng", n, "error")
        rs.append_taint(Taint(sample_ref=ref, kind="stream_error", at=NOW))
    _generate(rs, "qrng", 12, "error")
    retried = _generate(rs, "qrng", 12)
    rs.append_taint(Taint(sample_ref=retried, kind="stream_error", at=NOW))
    rs.append_taint(Taint(sample_ref=_ref("qrng", 0), kind="engine_fallback", at=NOW))
    rs.append_taint(Taint(sample_ref=_ref("qrng", 0), kind="engine_fallback", at=NOW))
    rs.append_taint(Taint(window_id="w1", kind="qrng_degraded", at=NOW))
    # Flags: a loop on one of eleven, and most replies at the token limit.
    _flag(rs, _ref("qrng", 3), "repetition")
    for n in range(8):
        _flag(rs, _ref("qrng", n), "truncated")
    # Judgements: a reply read, one that failed and was asked again, two replies with no score.
    for n in range(7):
        _judge(rs, _ref("qrng", n), score=1.0, status="ok", reader_version=1)
    _judge(rs, _ref("qrng", 7), status="failed")
    _judge(rs, _ref("qrng", 7), score=0.0, status="ok", reader_version=1)
    _judge(rs, _ref("qrng", 8), status="unreadable", reader_version=1)
    _judge(rs, _ref("qrng", 9), status="out_of_range", reader_version=1)
    _judge(rs, _ref("qrng", 12), status="failed")

    summary = summarise(rs)
    assert (summary.run_id, summary.state, summary.checks_version) == ("run1", "complete", 1)
    assert summary.note is None and summary.halts == []
    # The table follows the study's own order of arms.
    assert [(cell.arm_id, cell.card_id) for cell in summary.cells] == [(arm, CARD) for arm in ARMS]
    quantum = _cells(summary)[("qrng", CARD)]
    assert quantum.model_dump() == {
        "arm_id": "qrng",
        "card_id": CARD,
        "role": "quantum arm",
        "planned": 12,
        "completed": 11,
        "errored": 2,
        "taints": {"engine_fallback": 1, "stream_error": 3},
        "flags": {"truncated": 8, "repetition": 1},
        "judgements": {"failed": 1, "ok": 8, "out_of_range": 1, "unreadable": 1},
    }
    seeded = _cells(summary)[("prng7", CARD)]
    assert (seeded.role, seeded.planned, seeded.completed, seeded.errored) == (
        "seeded pseudo-random arm",
        12,
        10,
        0,
    )
    assert seeded.taints == {} and seeded.flags == {} and seeded.judgements == {}
    assert _cells(summary)[("bent", CARD)].role == "biased pseudo-random control arm"
    # The run's own totals: a sample or a window counts once per kind.
    assert summary.taints == {"engine_fallback": 1, "qrng_degraded": 1, "stream_error": 3}
    assert summary.tainted_samples == 4


def test_a_flag_on_five_percent_of_an_arm_is_a_caution_with_every_arm_beside_it(
    store: Store,
) -> None:
    rs = _new_run(store, state="complete")
    for arm in ARMS:
        for n in range(40):
            _generate(rs, arm, n)
    for n in range(2):  # 2 of 40: exactly 5%
        _flag(rs, _ref("qrng", n), "repetition")
    _flag(rs, _ref("prng7", 0), "repetition")  # 1 of 40: under the line, still shown
    _flag(rs, _ref("bent", 0), "garbled")  # 1 of 40: no caution of its own
    for n in range(40):  # every reply at the token limit: counted, never a caution
        _flag(rs, _ref("bent", n), "truncated")

    summary = summarise(rs)
    assert [c.kind for c in summary.cautions] == ["flag_share"]
    caution = summary.cautions[0]
    assert (caution.card_id, caution.flag) == (CARD, "repetition")
    assert [(a.arm_id, a.role, a.count, a.completed, a.share) for a in caution.arms] == [
        ("qrng", "quantum arm", 2, 40, 0.05),
        ("prng7", "seeded pseudo-random arm", 1, 40, 0.025),
        ("bent", "biased pseudo-random control arm", 0, 40, 0.0),
    ]
    assert caution.text == (
        f'On card {CARD}, the flag "ends in a loop" is on at least 5.0% of an arm\'s completed '
        "samples: arm qrng (quantum arm) 2 of 40 (5.0%); arm prng7 (seeded pseudo-random arm) "
        "1 of 40 (2.5%); arm bent (biased pseudo-random control arm) 0 of 40 (0.0%)."
    )
    assert _cells(summary)[("bent", CARD)].flags == {"truncated": 40, "garbled": 1}
    # One more flagged sample on another card does not change this card's line.
    assert all(c.card_id == CARD for c in summary.cautions)


def test_a_flag_counts_only_on_samples_that_completed(store: Store) -> None:
    """The share is of an arm's completed samples; a judgement that was retried is not missing."""
    rs = _new_run(store, state="complete")
    for n in range(20):
        _generate(rs, "qrng", n)
    for n in range(3):  # failed, then asked again and answered
        _flag(rs, _ref("qrng", n), "judge_failed")
        _judge(rs, _ref("qrng", n), status="failed")
        _judge(rs, _ref("qrng", n), score=1.0, status="ok", reader_version=1)
    assert summarise(rs).cautions == []
    assert _cells(summarise(rs))[("qrng", CARD)].flags == {"judge_failed": 3}
    # One that is still without a reply is 1 of 20, which is 5%.
    _flag(rs, _ref("qrng", 5), "judge_failed")
    _judge(rs, _ref("qrng", 5), status="failed")
    caution = summarise(rs).cautions[0]
    assert (caution.flag, caution.arms[0].count, caution.arms[0].completed) == (
        "judge_failed",
        1,
        20,
    )


def test_a_run_made_before_result_checks_has_no_flag_counts(store: Store) -> None:
    rs = _new_run(store, state="complete", checks_version=None)
    for n in range(4):
        _generate(rs, "qrng", n)
    _judge(rs, _ref("qrng", 0), score=1.0)
    _judge(rs, _ref("qrng", 1))  # no score and no status: the row cannot say why
    # A flags file beside an old run is not read: the run did not start under the checks.
    _flag(rs, _ref("qrng", 0), "repetition")

    summary = summarise(rs)
    assert summary.checks_version is None
    assert summary.note == MADE_BEFORE_RESULT_CHECKS
    assert "made before result checks" in summary.note
    cell = _cells(summary)[("qrng", CARD)]
    assert cell.flags is None
    assert cell.judgements == {"no_score": 1, "ok": 1}
    assert cell.completed == 4
    assert summary.cautions == []


def test_a_run_that_is_not_complete_or_has_an_open_halt_is_a_caution(store: Store) -> None:
    rs = _new_run(store, state="paused")
    halt = Halt(
        rule="generation_errors",
        scope="arm",
        scope_id="qrng",
        phase="generation",
        at=NOW,
        sentence="8 generations in a row on arm qrng ended in an error before a reply was complete.",
        recovery=rc.STOP_RULES["generation_errors"].recovery,
        counts={"in_a_row": 8},
    )
    rs.append_halt(halt)
    summary = summarise(rs)
    assert summary.halts == [halt]
    assert [c.kind for c in summary.cautions] == ["run_not_complete", "open_halt"]
    assert summary.cautions[0].text == (
        "The run is in the state paused, not complete: the counts describe what is stored so far."
    )
    assert summary.cautions[1].text == (
        "A check stopped the run and the run has not been resumed. " + halt.sentence
    )
    # Resumed and finished: the halt stays on the record and is no longer a caution.
    resumed = rs.stamp_resumed(NOW)
    rs.write_run(resumed.model_copy(update={"state": "complete"}))
    summary = summarise(rs)
    assert [h.resumed_at for h in summary.halts] == [NOW]
    assert summary.cautions == []


def test_more_than_one_reader_version_is_a_caution(store: Store) -> None:
    rs = _new_run(store, state="complete")
    for n in range(40):
        _generate(rs, "qrng", n)
    for n in range(30):
        _judge(rs, _ref("qrng", n), score=1.0, status="ok", reader_version=1)
    _judge(rs, _ref("qrng", 30), status="failed")  # no reply, so nothing was read
    assert summarise(rs).cautions == []
    for n in range(31, 34):
        _judge(rs, _ref("qrng", n), score=0.0)  # a row from before rows had a reader version
    _judge(rs, _ref("qrng", 34), status="unreadable", reader_version=2)
    cautions = summarise(rs).cautions
    assert [c.kind for c in cautions] == ["reader_versions"]
    assert cautions[0].text == (
        "The judge replies of this run were read by more than one version of the judge reader "
        "(3 by a harness from before result checks, 30 by version 1, 1 by version 2), so their "
        "scores were not all read by the same rules."
    )


def test_planned_comes_from_the_progress_the_runner_recorded(store: Store) -> None:
    """The store cannot build a card's items, so a run without recorded progress has no plan."""
    rs = _new_run(store, state="running")
    _generate(rs, "qrng", 0)
    assert _cells(summarise(rs))[("qrng", CARD)].planned is None
    # While a run is going the totals are in the index before run.json is next written.
    rs.set_progress("qrng", CARD, done=1, total=50)
    rs.set_progress("prng7", "creative_diversity", done=0, total=7)
    cells = _cells(summarise(rs))
    assert cells[("qrng", CARD)].planned == 50
    # A pair that is planned and has no row yet is in the table with nothing completed.
    waiting = cells[("prng7", "creative_diversity")]
    assert (waiting.planned, waiting.completed, waiting.errored) == (7, 0, 0)
    # Read as a bare folder, without the index, the plan is whatever run.json holds.
    assert _cells(summarise_run_dir(rs.run_dir))[("qrng", CARD)].planned is None
    record = rs.read_run().model_copy(
        update={
            "progress": Progress(
                by_arm={"qrng": ArmProgress(by_card={CARD: CardProgress(total=60)})}
            )
        }
    )
    rs.write_run(record)
    assert _cells(summarise_run_dir(rs.run_dir))[("qrng", CARD)].planned == 60
    assert _cells(summarise(rs))[("qrng", CARD)].planned == 60  # the larger of the two


def test_the_summary_reads_a_run_that_is_still_being_written(store: Store) -> None:
    rs = _new_run(store, state="running")
    for n in range(3):
        _generate(rs, "qrng", n)
    _flag(rs, _ref("qrng", 0), "empty_output")
    # The lines a writer is in the middle of.
    with open(rs.arm_card("qrng", CARD).generations_jsonl, "a", encoding="utf-8") as fh:
        fh.write('{"sample_ref": "qrng/eval_awareness/p3/0", "finish_re')
    with open(rs.run_dir.flags_jsonl, "a", encoding="utf-8") as fh:
        fh.write('{"sample_ref": "qrng/eval_aw')
    with open(rs.run_dir.taints_jsonl, "a", encoding="utf-8") as fh:
        fh.write('{"sample_ref": ')
    summary = summarise(rs)
    cell = _cells(summary)[("qrng", CARD)]
    assert (cell.completed, cell.errored, cell.flags) == (3, 0, {"empty_output": 1})
    assert [c.kind for c in summary.cautions] == ["run_not_complete", "flag_share"]


def test_an_arm_that_is_not_in_the_study_is_named_as_such(store: Store) -> None:
    rs = _new_run(store, state="complete")
    _generate(rs, "qrng", 0)
    _generate(rs, "stray", 0)
    summary = summarise(rs)
    assert [cell.arm_id for cell in summary.cells] == ["qrng", "stray"]
    assert _cells(summary)[("stray", CARD)].role == UNKNOWN_ROLE
    # Without the study every arm is unknown and the pairs are listed by name.
    bare = summarise_run_dir(rs.run_dir)
    assert {cell.role for cell in bare.cells} == {UNKNOWN_ROLE}


def test_the_summary_carries_the_catalogue_text_for_every_flag_kind(store: Store) -> None:
    """A page that shows the table prints these, so it needs no sentence of its own."""
    summary = summarise(_new_run(store, state="complete"))
    assert [legend.kind for legend in summary.flag_kinds] == list(rc.FLAG_KINDS)
    for legend in summary.flag_kinds:
        spec = rc.FLAG_KINDS[legend.kind]
        assert (legend.phase, legend.label, legend.sentence, legend.cautions) == (
            spec.phase,
            spec.label,
            spec.sentence,
            spec.cautions,
        )
    # The summary is a contract: it survives its own JSON.
    assert HealthSummary.model_validate(json.loads(summary.model_dump_json())) == summary


def test_summarising_changes_nothing_in_the_run_folder(store: Store) -> None:
    rs = _new_run(store, state="paused")
    _generate(rs, "qrng", 0)
    _flag(rs, _ref("qrng", 0), "truncated")
    before = {
        path.relative_to(rs.run_dir.path).as_posix(): path.read_bytes()
        for path in rs.run_dir.path.rglob("*")
        if path.is_file()
    }
    summarise(rs)
    after = {
        path.relative_to(rs.run_dir.path).as_posix(): path.read_bytes()
        for path in rs.run_dir.path.rglob("*")
        if path.is_file()
    }
    assert after == before


def _append(path: Path, *rows: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_the_summary_does_not_refuse_a_folder_for_a_line_it_does_not_know(store: Store) -> None:
    """A taint of an unknown kind, a row under a name that is no id, a state from another harness.

    Each of these used to leave ``summarise`` as a ValidationError, so the run
    page had no table at all. None is written by this harness; a later or an
    earlier one, or a hand, may have.
    """
    rs = _new_run(store, state="complete")
    for n in range(4):
        _generate(rs, "qrng", n)
    rs.append_taint(Taint(sample_ref=_ref("qrng", 0), kind="engine_fallback", at=NOW))
    at = NOW.isoformat()
    _append(
        rs.run_dir.taints_jsonl,
        {"sample_ref": _ref("qrng", 1), "kind": "cosmic_ray", "at": at},
        {"sample_ref": _ref("qrng", 2), "kind": "Another Kind!", "at": at},
        {"window_id": "w9", "kind": "cosmic_ray", "at": at},
        {"sample_ref": f"Not An Arm/{CARD}/p0/0", "kind": "stream_error", "at": at},
        {"sample_ref": "qrng/Not A Card/p0/0", "kind": "stream_error", "at": at},
    )
    _flag(rs, _ref("qrng", 3), "repetition")
    _append(
        rs.run_dir.flags_jsonl,
        {"sample_ref": "x/y/p0/0", "kind": "repetition", "arm_id": "Not An Arm", "card_id": CARD},
        {"sample_ref": _ref("qrng", 0), "kind": "repetition", "arm_id": "qrng", "card_id": "A B"},
        {"sample_ref": _ref("qrng", 0), "kind": "a_kind_of_tomorrow", "arm_id": "qrng"},
    )

    summary = summarise(rs)
    assert [(cell.arm_id, cell.card_id) for cell in summary.cells] == [("qrng", CARD)]
    cell = summary.cells[0]
    assert cell.taints == {"engine_fallback": 1, "other": 2}
    assert cell.flags == {"repetition": 1}
    # Every taint line counts for the run, whatever it names: the analysis leaves those samples out.
    assert summary.taints == {"engine_fallback": 1, "other": 3, "stream_error": 2}
    assert summary.tainted_samples == 5
    assert HealthSummary.model_validate(json.loads(summary.model_dump_json())) == summary


def test_a_run_in_a_state_this_harness_does_not_know_is_still_summarised(store: Store) -> None:
    rs = _new_run(store, state="paused")
    _generate(rs, "qrng", 0)
    record = json.loads(rs.run_dir.run_json.read_text(encoding="utf-8"))
    record["state"] = "hibernating"
    rs.run_dir.run_json.write_text(json.dumps(record), encoding="utf-8", newline="\n")

    summary = summarise_run_dir(rs.run_dir)
    assert summary.state == "other" and summary.cells[0].completed == 1
    (caution,) = summary.cautions
    assert caution.kind == "run_not_complete"
    assert caution.text == (
        "The run is in a state this harness does not know, not complete: the counts describe "
        "what is stored so far."
    )
    assert "hibernating" not in summary.model_dump_json()
    # A known state is reported as it is.
    record["state"] = "aborted"
    rs.run_dir.run_json.write_text(json.dumps(record), encoding="utf-8", newline="\n")
    known = summarise_run_dir(rs.run_dir)
    assert known.state == "aborted" and "the state aborted, not complete" in known.cautions[0].text
