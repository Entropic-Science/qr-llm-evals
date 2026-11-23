"""Store contract tests (spec D-4, §2.5, §4.5, §5.2 *Resume*; AGENTS.md invariant 4).

Every test works in a ``tmp_path`` data root. The last test AST-scans the
store package so no delete call can be introduced without failing the gate.
"""

from __future__ import annotations

import ast
import io
import json
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zstandard
from pydantic import ValidationError

from qr_llm_evals.cards.result_checks import STOP_RULES
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.store import (
    SCHEMA_VERSION,
    DataRoot,
    GenerationRow,
    Index,
    JsonlWriter,
    JudgeRow,
    Run,
    Store,
    Taint,
    completed_refs,
    extract_bundle,
    iter_jsonl,
    prereg_sha256,
    quarantine,
    read_bundle,
    safe_segment,
    strip_provider_metadata,
    write_frozen_study,
)
from qr_llm_evals.store import bundle as bundle_module
from qr_llm_evals.store.bundle import derive_study_id, load_frozen_study
from qr_llm_evals.store.paths import STUDIES_DIR
from qr_llm_evals.store.schema import Halt, Provenance, ResultFlag, Timing

STORE_SRC = Path(__file__).resolve().parents[1] / "src" / "qr_llm_evals" / "store"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
H = "a" * 64


def _study() -> StudyConfig:
    return StudyConfig.model_validate(
        {
            "title": "Study 1",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [
                {
                    "id": "qr-llm",
                    "family": "qr_llm",
                    "provider": "qr_server",
                    "served_name": "qr-llm",
                }
            ],
            "arms": [
                {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"},
                {
                    "id": "prng7",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "prng_seeded",
                    "study_seed": 7,
                },
            ],
            "cards": [{"card_id": "creative_diversity"}],
            "judge": {"model": "claude-sonnet-4"},
            "narrative_model": {
                "id": "qr-llm",
                "family": "qr_llm",
                "provider": "qr_server",
                "served_name": "qr-llm",
            },
            "expectations": "We expect no difference.\r\nSecond line.",
        }
    )


def _run(study_id: str, run_id: str = "run1") -> Run:
    return Run(
        run_id=run_id,
        study_id=study_id,
        kind="pilot",
        created_at=NOW,
        key_name="jachym",
        host_token_hash=H,
        config_hash=config_hash(_study()),
    )


def _gen(arm: str, prompt: str, finish: str = "stop", **extra: object) -> GenerationRow:
    ref = f"{arm}/creative_diversity/{prompt}/0"
    prov = Provenance(
        model_id="qr-llm",
        served_name="qr-llm",
        sampling="creative",
        envelope_hash=H,
        source_label="quantum" if arm == "qrng" else "seeded_prng",
        quantum=arm == "qrng",
        determinism_tier="none",
        pipeline_pinned=True,
    )
    return GenerationRow.model_validate(
        {
            "sample_ref": ref,
            "arm_id": arm,
            "card_id": "creative_diversity",
            "prompt_id": prompt,
            "epoch": 0,
            "messages_hash": H,
            "output": "hello",
            "finish_reason": finish,
            "provenance": prov,
            "timing": Timing(started=NOW),
            "request_id": ref,
            **extra,
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "data")
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture
def frozen(store: Store) -> str:
    return store.freeze_study(_study(), "# Prereg\r\n\r\nbody\r\n").study_id


# -- paths ---------------------------------------------------------------------


def test_layout_matches_spec(tmp_path: Path) -> None:
    root = DataRoot(tmp_path)
    ac = root.study("s1").run("r1").arm_card("qrng", "creative_diversity")
    base = tmp_path / STUDIES_DIR / "s1" / "runs" / "r1"
    assert ac.generations_jsonl == base / "arms/qrng/cards/creative_diversity/generations.jsonl"
    assert ac.tier0_jsonl.name == "tier0.jsonl"
    assert ac.judge_jsonl("j1") == ac.path / "judge" / "j1.jsonl"
    assert ac.scores_jsonl("rouge") == ac.path / "scores" / "rouge.jsonl"
    assert ac.inspect_log("novelty") == ac.path / "inspect" / "novelty.eval"
    rd = root.study("s1").run("r1")
    assert rd.session_json("sess1") == base / "sessions" / "sess1.json"
    assert rd.analysis_dir("a1") == base / "analysis" / "a1"
    assert root.quarantine_dir == tmp_path / "_quarantine"


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "a\\b", "", ".hidden", "x" * 129])
def test_safe_segment_rejects_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_segment(bad)


# -- index ---------------------------------------------------------------------


def test_index_migrates_once_and_uses_wal(tmp_path: Path) -> None:
    db = tmp_path / "index.sqlite"
    with Index(db) as idx:
        assert idx.schema_version == SCHEMA_VERSION
    with Index(db) as idx:  # reopening applies nothing new
        assert idx.schema_version == SCHEMA_VERSION
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {
        "studies",
        "runs",
        "sessions",
        "windows",
        "observers",
        "questionnaires",
        "taints",
        "progress",
        "schema_migrations",
    } <= tables


# -- artifacts -----------------------------------------------------------------


def test_jsonl_writer_appends_and_reader_skips_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    with JsonlWriter(path) as w:
        w.append({"a": 1})
        w.append({"b": "ü"})
    with JsonlWriter(path) as w:  # reopening appends, never truncates
        w.append({"c": 3})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"truncated": ')
    assert path.read_bytes().count(b"\r") == 0
    assert list(iter_jsonl(path)) == [{"a": 1}, {"b": "ü"}, {"c": 3}]
    with pytest.raises(ValueError, match="malformed"):
        list(iter_jsonl(path, strict=True))


def test_completed_refs_counts_only_finished_rows(tmp_path: Path) -> None:
    path = tmp_path / "generations.jsonl"
    with JsonlWriter(path) as w:
        w.append(_gen("qrng", "p1", "stop"))
        w.append(_gen("qrng", "p2", "length"))
        w.append(_gen("qrng", "p3", "abort"))
        w.append(_gen("qrng", "p4", "error"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"sample_ref": "qrng/creative_diversity/p5/0", "finish_re')
    assert completed_refs(path) == {
        "qrng/creative_diversity/p1/0",
        "qrng/creative_diversity/p2/0",
    }


# -- frozen study --------------------------------------------------------------


def test_freeze_study_writes_bundle_root(store: Store, frozen: str) -> None:
    sd = store.study(frozen)
    for p in (
        sd.study_json,
        sd.prereg_md,
        sd.prereg_sha256,
        sd.expectations_md,
        sd.licence_data,
        sd.readme_md,
    ):
        assert p.is_file(), p
    prereg = sd.prereg_md.read_bytes()
    assert b"\r" not in prereg and prereg == b"# Prereg\n\nbody\n"
    digest, name = sd.prereg_sha256.read_text(encoding="utf-8").split()
    assert name == "prereg.md" and digest == prereg_sha256(prereg.decode("utf-8"))
    meta = json.loads(sd.study_json.read_text(encoding="utf-8"))
    assert meta["config_hash"] == config_hash(_study()) and meta["prereg_hash"] == digest
    assert sd.expectations_md.read_bytes() == b"We expect no difference.\nSecond line.\n"
    assert "Attribution 4.0 International" in sd.licence_data.read_text(encoding="utf-8")
    assert frozen == derive_study_id(_study())
    assert load_frozen_study(sd) == _study()
    assert store.index.get_study(frozen)["config_hash"] == meta["config_hash"]  # type: ignore[index]


def test_refreeze_same_config_is_idempotent_but_different_config_refused(
    store: Store, frozen: str
) -> None:
    store.freeze_study(_study(), "# Prereg\n\nbody\n")  # same bytes: fine
    other = _study().model_copy(update={"title": "Study 1b"})
    with pytest.raises(FileExistsError, match="refusing"):
        write_frozen_study(store.root, other, "x", study_id=frozen)
    with pytest.raises(FileExistsError, match="prereg"):
        store.freeze_study(_study(), "# Different prereg\n")


def test_readme_names_the_run_order_file_for_a_designed_study_only(
    store: Store, frozen: str
) -> None:
    """Only a study with a design block has a first-full-run rule and a run-order.json."""
    legacy = store.study(frozen).readme_md.read_text(encoding="utf-8")
    assert "run-order.json" not in legacy and "design block" not in legacy

    fixture = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"
    designed = StudyConfig.model_validate(
        json.loads((fixture / "study1.designed.json").read_text(encoding="utf-8"))
    )
    assert designed.design is not None
    readme = store.freeze_study(designed, "# Prereg\n").readme_md.read_text(encoding="utf-8")
    assert "`runs/<run_id>/run-order.json`" in readme
    assert "Only a study's first\nfull run is confirmatory" in readme
    # The two paragraphs for designed studies are the only difference between the two.
    paragraph = bundle_module._RUN_ORDER_NOTE
    assert paragraph in readme
    checks = bundle_module._RESULT_CHECKS_NOTE
    assert checks in readme

    def without_ids(text: str) -> list[str]:
        return [line for line in text.splitlines() if "`s_" not in line and "(`" not in line]

    assert without_ids(readme.replace(paragraph, "").replace(checks, "")) == without_ids(legacy)


def test_readme_names_the_result_check_records_where_its_text_is_not_pinned(
    store: Store, frozen: str
) -> None:
    """The README of a study without a design block is frozen text, so only the other names them."""
    legacy = store.study(frozen).readme_md.read_text(encoding="utf-8")
    assert "flags.jsonl" not in legacy and "halts" not in legacy

    fixture = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"
    designed = StudyConfig.model_validate(
        json.loads((fixture / "study1.designed.json").read_text(encoding="utf-8"))
    )
    readme = store.freeze_study(designed, "# Prereg\n").readme_md.read_text(encoding="utf-8")
    assert "`runs/<run_id>/flags.jsonl`" in readme
    assert "A flag never excludes a sample" in readme
    assert "`run.json` lists the stop under `halts`" in readme


# -- runs ----------------------------------------------------------------------


def test_run_lifecycle_and_resume_set(store: Store, frozen: str) -> None:
    rs = store.create_run(_run(frozen))
    with pytest.raises(FileExistsError):
        store.create_run(_run(frozen))
    with pytest.raises(ValueError, match="does not belong"):
        rs.write_run(_run(frozen, "other"))

    rs.append_generation(_gen("qrng", "p1"))
    rs.append_generation(_gen("qrng", "p2", "abort"))
    rs.append_generation(_gen("prng7", "p1"))
    rs.append_tier0("qrng", "creative_diversity", [{"sample_ref": "x", "token_index": 0}])
    rs.append_judge(
        "qrng",
        "creative_diversity",
        JudgeRow(
            sample_ref="qrng/creative_diversity/p1/0",
            judge_id="j",
            model="m",
            rubric_hash=H,
            score=0.5,
            at=NOW,
        ),
    )
    rs.append_judge(
        "qrng",
        "creative_diversity",
        JudgeRow(
            sample_ref="qrng/creative_diversity/p2/0",
            judge_id="j",
            model="m",
            rubric_hash=H,
            score=None,
            at=NOW,
        ),
    )
    rs.set_progress("qrng", "creative_diversity", done=1, total=2, aborted=1)

    resumed = store.run(frozen, "run1")
    assert resumed.completed_refs("qrng", "creative_diversity") == {"qrng/creative_diversity/p1/0"}
    assert resumed.completed_refs("prng7", "creative_diversity") == {
        "prng7/creative_diversity/p1/0"
    }
    assert resumed.completed_refs("qrng", "nonexistent_card") == set()
    assert resumed.completed_judge_refs("qrng", "creative_diversity", "j") == {
        "qrng/creative_diversity/p1/0"
    }
    assert [g.prompt_id for g in resumed.generations("qrng", "creative_diversity")] == ["p1", "p2"]
    assert resumed.progress().by_arm["qrng"].by_card["creative_diversity"].done == 1
    assert store.find_run("run1") is not None and store.find_run("nope") is None
    assert [r.run_id for r in store.list_runs(study_id=frozen)] == ["run1"]
    assert store.index.count_active_runs("jachym") == 1


def test_taints_and_sessions_are_written_and_indexed_then_reindexed(
    store: Store, frozen: str
) -> None:
    rs = store.create_run(_run(frozen))
    rs.append_taint(
        Taint(sample_ref="qrng/creative_diversity/p1/0", kind="engine_fallback", at=NOW)
    )
    rs.write_session(
        {
            "session_id": "sess1",
            "run_id": "run1",
            "index": 0,
            "display": "single",
            "target_s": 1800,
            "host_observer_id": "obs1",
            "windows": [
                {"window_id": "w1", "kind": "supervised", "start": NOW.isoformat(), "end": None}
            ],
        }
    )
    rs.append_observer(
        {"observer_id": "obs1", "run_id": "run1", "display_name": "J", "joined_at": NOW.isoformat()}
    )
    rs.append_questionnaire(
        {
            "observer_id": "obs1",
            "session_id": "sess1",
            "phase": "pre",
            "items_hash": H,
            "answers": {"q1": 3},
            "at": NOW.isoformat(),
        }
    )
    with pytest.raises(ValueError, match="belong"):
        rs.write_session({"session_id": "s2", "run_id": "other"})

    def snapshot() -> tuple[object, ...]:
        idx = store.index
        return (
            idx.list_taints("run1"),
            idx.list_sessions("run1"),
            idx.list_windows("run1"),
            idx.list_observers("run1"),
            idx.list_questionnaires("run1"),
        )

    before = snapshot()
    assert len(rs.taints()) == 1 and rs.read_session("sess1")["session_id"] == "sess1"  # type: ignore[index]
    assert before[2][0]["end"] is None  # type: ignore[index]
    assert store.reindex_run(frozen, "run1").run_id == "run1"
    assert snapshot() == before


# -- result checks -------------------------------------------------------------

#: The keys of ``run.json`` before result checks. A run that was not stopped by
#: a check and has no checks version must write exactly these.
RUN_JSON_KEYS = {
    "run_id",
    "study_id",
    "kind",
    "state",
    "created_at",
    "key_name",
    "owner_id",
    "host_token_hash",
    "prereg_hash",
    "config_hash",
    "prereg_stale",
    "readiness",
    "tier0_source",
    "determinism_tier",
    "progress",
    "taints",
    "withdrawn",
    "needs_keys",
    "keys_present",
}
JUDGE_ROW_KEYS = {
    "sample_ref",
    "judge_id",
    "model",
    "rubric_hash",
    "score",
    "fields",
    "raw",
    "usage",
    "at",
}
REF = "qrng/creative_diversity/p1/0"


def _halt(rule: str = "generation_errors", **extra: object) -> Halt:
    fields: dict[str, object] = {
        "rule": rule,
        "scope": "arm",
        "scope_id": "qrng",
        "phase": "generation",
        "at": NOW,
        "sentence": "8 generations in a row on arm qrng ended in an error.",
        "recovery": STOP_RULES.get(rule, STOP_RULES["generation_errors"]).recovery,
        "counts": {"in_a_row": 8},
        "causes": {"HTTP 500": 8},
        "sample_refs": [REF],
    }
    return Halt.model_validate({**fields, **extra})


def _judge_row(prompt: str, **extra: object) -> JudgeRow:
    return JudgeRow.model_validate(
        {
            "sample_ref": f"qrng/creative_diversity/{prompt}/0",
            "judge_id": "j",
            "model": "m",
            "rubric_hash": H,
            "at": NOW,
            **extra,
        }
    )


def test_records_that_do_not_use_the_new_fields_keep_the_keys_they_always_had(
    store: Store, frozen: str
) -> None:
    """A run no check stopped, and a judge row without a status, write no new key."""
    rs = store.create_run(_run(frozen))
    on_disk = json.loads(rs.run_dir.run_json.read_text(encoding="utf-8"))
    assert set(on_disk) == RUN_JSON_KEYS
    assert set(store.index.get_run("run1")) == RUN_JSON_KEYS  # type: ignore[arg-type]
    # A file written by the harness before result checks loads, and is written back the same.
    before = rs.run_dir.run_json.read_bytes()
    loaded = rs.read_run()
    assert loaded.halts == [] and loaded.checks_version is None and loaded.open_halts() == []
    rs.write_run(loaded)
    assert rs.run_dir.run_json.read_bytes() == before

    rs.append_judge("qrng", "creative_diversity", _judge_row("p1", score=0.5))
    rs.append_judge(
        "qrng",
        "creative_diversity",
        _judge_row("p2", status="unreadable", stop_reason="max_tokens", reader_version=1),
    )
    path = rs.arm_card("qrng", "creative_diversity").judge_jsonl("j")
    old_style, new_style = list(iter_jsonl(path))
    assert set(old_style) == JUDGE_ROW_KEYS
    assert set(new_style) == JUDGE_ROW_KEYS | {"status", "stop_reason", "reader_version"}
    rows = rs.judge_rows("qrng", "creative_diversity", "j")
    assert (rows[0].status, rows[0].stop_reason, rows[0].reader_version) == (None, None, None)
    assert (rows[1].status, rows[1].stop_reason, rows[1].reader_version) == (
        "unreadable",
        "max_tokens",
        1,
    )

    # A run that started under the checks carries its version, and still no empty halt list.
    rs.write_run(loaded.model_copy(update={"checks_version": 1}))
    on_disk = json.loads(rs.run_dir.run_json.read_text(encoding="utf-8"))
    assert set(on_disk) == RUN_JSON_KEYS | {"checks_version"} and on_disk["checks_version"] == 1


def test_a_judge_row_says_whether_a_reply_came_and_cannot_contradict_itself() -> None:
    assert _judge_row("p1", score=1.0, status="ok").status == "ok"
    with pytest.raises(ValidationError, match="needs a score"):
        _judge_row("p1", status="ok")
    for status in ("failed", "unreadable", "out_of_range"):
        assert _judge_row("p1", status=status).score is None
        with pytest.raises(ValidationError, match="has no score"):
            _judge_row("p1", score=1.0, status=status)
    with pytest.raises(ValidationError):
        _judge_row("p1", status="maybe")


def test_a_judge_reply_that_was_received_is_not_asked_again(store: Store, frozen: str) -> None:
    """Only a call without a reply, and an old row without a score, is asked again on resume."""
    rs = store.create_run(_run(frozen))
    for row in (
        _judge_row("scored", score=0.0),  # a row from before rows had a status
        _judge_row("old_null"),  # same, without a score: asked again, as it always was
        _judge_row("ok", score=1.0, status="ok"),
        _judge_row("unreadable", status="unreadable", raw="I cannot say."),
        _judge_row("out_of_range", status="out_of_range", raw="SCORE: 7"),
        _judge_row("failed", status="failed"),
        _judge_row("retried", status="failed"),
        _judge_row("retried", score=1.0, status="ok"),
    ):
        rs.append_judge("qrng", "creative_diversity", row)
    done = rs.completed_judge_refs("qrng", "creative_diversity", "j")
    assert {ref.split("/")[2] for ref in done} == {
        "scored",
        "ok",
        "unreadable",
        "out_of_range",
        "retried",
    }


def test_flags_are_written_beside_the_taints_and_are_not_taints(store: Store, frozen: str) -> None:
    rs = store.create_run(_run(frozen))
    assert rs.flags() == [] and rs.append_flags([]) == 0
    assert not rs.run_dir.flags_jsonl.exists()  # nothing flagged, no file
    flag = ResultFlag(
        sample_ref=REF,
        kind="repetition",
        phase="generation",
        card_id="creative_diversity",
        arm_id="qrng",
        detail="a unit of 4 characters repeated 300 times",
        count=300,
        at=NOW,
    )
    truncated = flag.model_copy(update={"kind": "truncated", "detail": "", "count": None})
    assert rs.append_flags([flag, truncated]) == 2
    rs.append_flag(flag.model_copy(update={"kind": "judge_cut", "phase": "judge", "count": None}))

    assert rs.run_dir.flags_jsonl == rs.run_dir.taints_jsonl.with_name("flags.jsonl")
    assert [f.kind for f in rs.flags()] == ["repetition", "truncated", "judge_cut"]
    assert rs.flags()[0] == flag
    rows = list(rs.iter_flag_rows())
    assert rows[0]["count"] == 300 and "count" not in rows[1]
    assert set(rows[1]) == {"sample_ref", "kind", "phase", "card_id", "arm_id", "detail", "at"}
    # A flag is not an exclusion: no taint was written, in the file or in the index.
    assert rs.taints() == [] and not rs.run_dir.taints_jsonl.exists()
    assert store.index.list_taints("run1") == []
    # Flags are appended only: a second write adds lines.
    rs.append_flag(flag)
    assert len(rs.flags()) == 4

    with pytest.raises(ValidationError, match="belongs to phase 'generation'"):
        ResultFlag.model_validate({**flag.model_dump(), "phase": "judge"})
    with pytest.raises(ValidationError):
        ResultFlag.model_validate({**flag.model_dump(), "kind": "weird"})
    with pytest.raises(ValidationError):  # room for harness text, not for an upstream error body
        ResultFlag.model_validate({**flag.model_dump(), "detail": "x" * 301})


def test_halts_are_kept_in_run_json_and_stamped_when_the_run_is_resumed(
    store: Store, frozen: str
) -> None:
    rs = store.create_run(_run(frozen))
    later = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
    before = rs.run_dir.run_json.read_bytes()
    assert rs.stamp_resumed(later).halts == []  # nothing open: nothing to stamp, nothing written
    assert rs.run_dir.run_json.read_bytes() == before

    first = rs.append_halt(_halt())
    assert [h.rule for h in first.halts] == ["generation_errors"]
    assert rs.read_run().open_halts() == first.halts
    on_disk = json.loads(rs.run_dir.run_json.read_text(encoding="utf-8"))
    assert on_disk["halts"][0]["resumed_at"] is None
    assert on_disk["halts"][0]["sample_refs"] == [REF]
    assert store.list_runs()[0].halts == first.halts  # the index holds the same record

    resumed = rs.stamp_resumed(later)
    assert resumed.open_halts() == [] and resumed.halts[0].resumed_at == later
    second = rs.append_halt(_halt("empty_outputs", scope="card", scope_id="creative_diversity"))
    assert [h.rule for h in second.halts] == ["generation_errors", "empty_outputs"]
    assert [h.rule for h in second.open_halts()] == ["empty_outputs"]
    # Stamping never touches a halt that was resumed before.
    much_later = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    final = rs.stamp_resumed(much_later)
    assert [h.resumed_at for h in final.halts] == [later, much_later]
    assert rs.read_run() == final
    # The pure forms the runner uses on the record it holds give the same result.
    held = _run(frozen).with_halt(_halt())
    assert held.with_halts_resumed(later).halts[0].resumed_at == later
    assert held.halts[0].resumed_at is None  # the record it was made from is not changed


def test_a_halt_names_its_arm_or_card_and_at_most_three_samples() -> None:
    assert _halt(scope="run", scope_id=None).scope_id is None
    assert _halt("unfinished", scope="phase", scope_id=None).phase == "generation"
    with pytest.raises(ValidationError, match="scope_id"):
        _halt(scope="arm", scope_id=None)
    with pytest.raises(ValidationError, match="scope_id"):
        _halt(scope="run", scope_id="qrng")
    with pytest.raises(ValidationError):
        _halt(sample_refs=[REF, REF, REF, REF])
    with pytest.raises(ValidationError):
        _halt(rule="made_up_rule")


# -- bundle --------------------------------------------------------------------


def test_bundle_round_trip_strips_provider_metadata(
    store: Store, frozen: str, tmp_path: Path
) -> None:
    rs = store.create_run(_run(frozen))
    rs.append_generation(_gen("qrng", "p1"))
    rs.append_score(
        "qrng",
        "creative_diversity",
        "external",
        {
            "sample_ref": "qrng/creative_diversity/p1/0",
            "request_id": "qrng/creative_diversity/p1/0",
            "provider_request_id": "req_123",
            "response_headers": {"x-request-id": "abc", "cf-ray": "zzz"},
            "nested": {"X-Request-Id": "abc", "score": 1.0},
        },
    )
    (rs.run_dir.path / "notes.tmp").write_text("scratch", encoding="utf-8")

    members = read_bundle(store.export_bundle(frozen))
    names = set(members)
    assert f"{frozen}/prereg.md" in names and f"{frozen}/LICENSE-data" in names
    assert f"{frozen}/runs/run1/run.json" in names
    assert not any(n.endswith((".sqlite", ".tmp")) for n in names)
    assert members[f"{frozen}/prereg.md"] == store.study(frozen).prereg_md.read_bytes()

    score = json.loads(
        members[f"{frozen}/runs/run1/arms/qrng/cards/creative_diversity/scores/external.jsonl"]
    )
    assert score == {
        "sample_ref": "qrng/creative_diversity/p1/0",
        "request_id": "qrng/creative_diversity/p1/0",
        "nested": {"score": 1.0},
    }
    gen = json.loads(
        members[f"{frozen}/runs/run1/arms/qrng/cards/creative_diversity/generations.jsonl"]
    )
    assert gen["request_id"] == gen["sample_ref"]  # the harness join key survives

    dest = store.write_bundle(frozen, tmp_path / "out" / f"{frozen}.tar.zst")
    assert read_bundle(dest) == members
    with pytest.raises(FileNotFoundError):
        list(store.export_bundle("s_nope"))


def test_bundle_carries_the_flags_and_the_halts(store: Store, frozen: str) -> None:
    rs = store.create_run(_run(frozen))
    rs.append_flag(
        ResultFlag(
            sample_ref=REF,
            kind="empty_output",
            phase="generation",
            card_id="creative_diversity",
            arm_id="qrng",
            detail="a reply of 0 characters with no visible text",
            at=NOW,
        )
    )
    rs.append_halt(_halt())
    members = read_bundle(store.export_bundle(frozen))
    assert members[f"{frozen}/runs/run1/flags.jsonl"] == rs.run_dir.flags_jsonl.read_bytes()
    exported = json.loads(members[f"{frozen}/runs/run1/run.json"])
    assert exported["halts"][0]["rule"] == "generation_errors"
    assert exported["halts"][0]["causes"] == {"HTTP 500": 8}


def test_bundle_exports_the_rows_the_tolerant_reader_sees(
    store: Store, frozen: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Large JSONL files are streamed, not parsed whole; the rows must not change."""
    monkeypatch.setattr(bundle_module, "_WRITE_BLOCK", 64)  # many blocks from a small file
    rs = store.create_run(_run(frozen))
    card = rs.arm_card("qrng", "creative_diversity")
    plain = '{"sample_ref": "a/0", "u": 0.1234567890123456, "text": "ž – \\"x_ray\\"", "n": null}'
    raw = "\n".join(
        [
            plain,
            "",
            '{"sample_ref": "a/1", "X-Request-Id": "abc", "nested": {"headers": {"a": 1}, "v": 2}}',
            # "x_trace" with its first letter as a JSON escape (chr(92) is a backslash)
            f'{{"sample_ref": "a/2", "{chr(92)}u0078_trace": "hidden", "v": 3}}',
            '{"sample_ref": "a/3", "tok{"sample_ref": "a/3", "v": 4}',  # a crash, then a resume
            "[1, 2, 3]",
            *(f'{{"sample_ref": "b/{i}", "token_index": {i}}}' for i in range(40)),
            '{"sample_ref": "a/4", "trunc',  # the line a killed writer left behind
        ]
    )
    card.tier0_jsonl.parent.mkdir(parents=True, exist_ok=True)
    card.tier0_jsonl.write_bytes(raw.encode("utf-8"))
    (card.path / "zz_after.txt").write_text("the member after the streamed one", encoding="utf-8")

    members = read_bundle(store.export_bundle(frozen))
    base = f"{frozen}/runs/run1/arms/qrng/cards/creative_diversity"
    exported = members[f"{base}/tier0.jsonl"].decode("utf-8").splitlines()
    expected = [strip_provider_metadata(row) for row in iter_jsonl(card.tier0_jsonl)]
    assert [json.loads(line) for line in exported] == expected
    assert len(expected) == 43 and expected[1] == {"sample_ref": "a/1", "nested": {"v": 2}}
    assert expected[2] == {"sample_ref": "a/2", "v": 3}
    assert exported[0] == plain  # a row with nothing to strip is copied byte for byte
    assert members[f"{base}/zz_after.txt"] == b"the member after the streamed one"


def test_extract_bundle_unpacks_a_download_and_refuses_unsafe_archives(
    store: Store, frozen: str, tmp_path: Path
) -> None:
    rs = store.create_run(_run(frozen))
    rs.append_generation(_gen("qrng", "p1"))
    dest = store.write_bundle(frozen, tmp_path / "download" / f"{frozen}.tar.zst")

    study_dir = extract_bundle(dest, tmp_path / "unpacked")
    assert study_dir == (tmp_path / "unpacked" / frozen).resolve()
    files = {
        f"{frozen}/{path.relative_to(study_dir).as_posix()}": path.read_bytes()
        for path in study_dir.rglob("*")
        if path.is_file()
    }
    assert files == read_bundle(dest)
    with pytest.raises(FileExistsError):  # never writes over an earlier copy
        extract_bundle(dest, tmp_path / "unpacked")

    def archive(name: str, kind: bytes = tarfile.REGTYPE) -> Path:
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.type, info.size = kind, (2 if kind == tarfile.REGTYPE else 0)
            info.linkname = "study.json"
            tar.addfile(info, io.BytesIO(b"{}") if kind == tarfile.REGTYPE else None)
        path = tmp_path / f"bad{len(list(tmp_path.glob('bad*')))}.tar.zst"
        path.write_bytes(zstandard.ZstdCompressor().compress(raw.getvalue()))
        return path

    with pytest.raises(ValueError, match="unsafe member path"):
        extract_bundle(archive("../outside.json"), tmp_path / "a")
    assert not (tmp_path / "outside.json").exists()
    with pytest.raises(ValueError, match="not an ordinary file"):
        extract_bundle(archive("s_x/link", tarfile.SYMTYPE), tmp_path / "b")
    with pytest.raises(ValueError, match="not a study bundle"):
        extract_bundle(archive("s_x/notes.json"), tmp_path / "c")


def test_strip_provider_metadata_is_pure() -> None:
    src = {"headers": {"a": 1}, "keep": [{"x-ratelimit": 1, "v": 2}], "request_id": "r"}
    assert strip_provider_metadata(src) == {"keep": [{"v": 2}], "request_id": "r"}
    assert "headers" in src  # input untouched


# -- quarantine / withdraw -----------------------------------------------------


def test_quarantine_moves_never_deletes(store: Store, frozen: str, tmp_path: Path) -> None:
    rs = store.create_run(_run(frozen))
    rs.append_generation(_gen("qrng", "p1"))
    src = rs.arm_card("qrng", "creative_diversity").generations_jsonl
    original = src.read_bytes()

    dest = store.quarantine(src, "engine degraded mid-card", now=NOW)
    assert not src.exists() and dest.read_bytes() == original
    assert dest.relative_to(store.root.quarantine_dir / "2026-09-14").as_posix() == (
        f"studies/{frozen}/runs/run1/arms/qrng/cards/creative_diversity/generations.jsonl"
    )
    log = list(iter_jsonl(store.root.quarantine_dir / "log.jsonl"))
    assert log[0]["reason"] == "engine degraded mid-card" and log[0]["to"].startswith(
        "_quarantine/"
    )
    assert (dest.with_name(dest.name + ".quarantined.json")).is_file()

    # a second quarantine the same day keeps both copies
    rs.append_generation(_gen("qrng", "p2"))
    dest2 = store.quarantine(src, "again", now=NOW)
    assert dest2 != dest and dest.exists() and dest2.exists()

    with pytest.raises(ValueError, match="already quarantined"):
        store.quarantine(dest, "twice", now=NOW)
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        quarantine(store.root, outside, "nope")
    with pytest.raises(ValueError, match="reason"):
        store.quarantine(rs.run_dir.run_json, "  ")
    assert rs.completed_refs("qrng", "creative_diversity") == set()  # resume sees the move


def test_withdraw_flags_and_banners_but_keeps_artifacts(store: Store, frozen: str) -> None:
    rs = store.create_run(_run(frozen))
    rs.append_generation(_gen("qrng", "p1"))
    run = store.withdraw("run1", "participant asked", now=NOW)
    assert run.withdrawn is True
    assert rs.read_run().withdrawn is True
    assert store.index.get_run("run1")["withdrawn"] is True  # type: ignore[index]
    banner = rs.run_dir.withdrawn_md.read_text(encoding="utf-8")
    assert "participant asked" in banner and "withdrawn" in banner
    assert rs.arm_card("qrng", "creative_diversity").generations_jsonl.is_file()
    assert store.withdraw("run1", "again").withdrawn is True  # idempotent
    members = read_bundle(store.export_bundle(frozen))
    assert f"{frozen}/runs/run1/WITHDRAWN.md" in members  # withdrawn runs stay in the bundle


# -- invariant 4 pin -----------------------------------------------------------

DELETE_CALLS: frozenset[str] = frozenset(
    {"os.remove", "os.unlink", "os.rmdir", "os.removedirs", "shutil.rmtree", "send2trash"}
)
DELETE_METHODS: frozenset[str] = frozenset({"unlink", "rmdir", "rmtree", "remove"})


def test_store_package_never_deletes() -> None:
    """No ``os.remove``/``shutil.rmtree``/``Path.unlink`` anywhere in the store package."""
    offenders: list[str] = []
    for path in sorted(STORE_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            dotted = ast.unparse(func)
            if dotted in DELETE_CALLS or (
                isinstance(func, ast.Attribute) and func.attr in DELETE_METHODS
            ):
                offenders.append(f"{path.name}:{node.lineno}: {dotted}(...)")
    assert not offenders, "store must never delete artifacts:\n" + "\n".join(offenders)
