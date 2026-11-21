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
from qr_llm_evals.store.schema import Provenance, Timing

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
