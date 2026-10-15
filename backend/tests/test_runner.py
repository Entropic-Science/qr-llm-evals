"""Thin-runner contract tests (spec §2.5, §5.2 *Headless vs viewer equivalence*, *Resume*,
*Provenance labelling*; AGENTS.md invariants 2 and 6).

Every test drives the runner against ``stub_engine.StubEngine`` (a deterministic
fake vLLM) in a ``tmp_path`` data root with an injected fixed clock, so
artifacts are reproducible byte for byte.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qr_llm_evals.cards import EvalCard, load_cards
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import CardOverrides, StudyConfig, config_hash
from qr_llm_evals.runner import (
    ListEventSink,
    NullEventSink,
    Orchestrator,
    RunControl,
    RunControlError,
    RunEvent,
    RunEventBus,
    RunnerConfig,
    parse_score,
)
from qr_llm_evals.runner.tasks import (
    TaskModuleError,
    effective_k,
    load_task_module,
    select_items,
    told_preamble,
)
from qr_llm_evals.store import Run, RunStore, Store
from stub_engine import StubEngine, StubJudge

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "registry"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
H = "b" * 64
ARMS = ("qrng", "prng_seed_7")
#: 3 items capped to 2 by the card × k 2 × 3 told cells.
PER_ARM_TOTAL = 2 * 2 * 3


def _study() -> StudyConfig:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    return StudyConfig.model_validate(
        {
            "title": "Runner test study",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [model],
            "arms": [
                {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"},
                {
                    "id": "prng_seed_7",
                    "model": "qr-llm",
                    "sampling": "creative",
                    "source": "prng_seeded",
                    "study_seed": 7,
                },
            ],
            "cards": [{"card_id": "stub_free"}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": model,
        }
    )


def _cards() -> dict[str, EvalCard]:
    return load_cards(FIXTURES / "evals")


def _run_store(root: Path, run_id: str = "run1") -> RunStore:
    store = Store(root)
    study = _study()
    study_dir = store.freeze_study(study, "# prereg\n")
    return store.create_run(
        Run(
            run_id=run_id,
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash=H,
            config_hash=config_hash(study),
        )
    )


def _config(max_inflight: int = 2) -> RunnerConfig:
    return RunnerConfig(vllm_origin="http://stub", max_inflight=max_inflight, repo_root=FIXTURES)


def _secrets() -> SecretBag:
    return SecretBag({"anthropic": "sk-ant-test"})


def _orchestrator(
    run_store: RunStore,
    engine: StubEngine,
    *,
    judge: StubJudge | None = None,
    sink: object | None = None,
    control: RunControl | None = None,
    max_inflight: int = 2,
    started: bool = True,
) -> Orchestrator:
    ctl = control or RunControl()
    if started:
        ctl.start()
    return Orchestrator(
        _study(),
        _cards(),
        run_store,
        _config(max_inflight),
        secrets=_secrets(),
        sink=sink or NullEventSink(),  # type: ignore[arg-type]
        control=ctl,
        transport=engine.transport,
        judge_transport=(judge or StubJudge()).transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    )


def _wait_until(pred: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def _artifact_bytes(rs: RunStore) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for arm in ARMS:
        d = rs.arm_card(arm, "stub_free")
        for path in (d.generations_jsonl, d.tier0_jsonl, d.scores_jsonl("task")):
            out[f"{arm}/{path.name}"] = path.read_bytes()
    return out


# ── task-module protocol ─────────────────────────────────────────────────────


def test_task_module_protocol_and_preambles() -> None:
    card = _cards()["stub_free"]
    task = load_task_module(card, FIXTURES)
    items = task.build_items()
    assert [i["prompt_id"] for i in items] == ["p1", "p2", "p3"]
    assert len(select_items(items, card)) == 2  # card.dataset.items caps
    assert effective_k(card) == 2
    with pytest.raises(TaskModuleError):
        effective_k(card, CardOverrides(k=50))
    plain = task.build_messages(items[0], None)[0]["content"]
    told = task.build_messages(items[0], "told_qrng")[0]["content"]
    assert told.startswith(told_preamble("told_qrng"))
    assert told.endswith(plain)
    assert "quantum" in told_preamble("told_qrng") and "pseudo-random" in told_preamble("told_prng")
    assert task.has_judge_prompt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Reasoning...\nSCORE: 7", 7.0),
        ('{"score": 0.25, "why": "x"}', 0.25),
        ("I'd give this a 3 out of 10", 10.0),
        ("no numbers here", None),
    ],
)
def test_parse_score(text: str, expected: float | None) -> None:
    assert parse_score(text) == expected


# ── control + events ─────────────────────────────────────────────────────────


def test_run_generation_requires_explicit_start(tmp_path: Path) -> None:
    orch = _orchestrator(_run_store(tmp_path), StubEngine(), started=False)
    with pytest.raises(RunControlError):
        orch.run_generation()


def test_event_bus_drop_oldest_and_resync() -> None:
    bus = RunEventBus(maxsize=3)
    sub = bus.subscribe()
    for i in range(5):
        bus.emit(RunEvent("sample.delta", {"i": i}))
    events = sub.drain()
    assert events[0].type == "resync" and events[0].data["dropped"] == 2
    assert [e.data["i"] for e in events[1:]] == [2, 3, 4]
    late = bus.subscribe()
    bus.emit(RunEvent("run.state", {"state": "running"}))
    assert late.drain()[0].type == "run.state"
    bus.close()
    assert bus.subscribe().closed


# ── the big contracts ────────────────────────────────────────────────────────


def test_headless_and_subscribed_runs_are_byte_identical(tmp_path: Path) -> None:
    headless = _orchestrator(_run_store(tmp_path / "a"), StubEngine())
    run_a = headless.run()
    bus = RunEventBus()
    sub = bus.subscribe()
    viewed = _orchestrator(_run_store(tmp_path / "b"), StubEngine(), sink=bus)
    run_b = viewed.run()

    assert run_a.state == run_b.state == "complete"
    assert _artifact_bytes(headless.store) == _artifact_bytes(viewed.store)
    types = {e.type for e in sub.drain()}
    assert {
        "run.state",
        "run.progress",
        "sample.started",
        "sample.delta",
        "sample.finished",
    } <= types
    for arm in ARMS:
        rows = headless.store.generations(arm, "stub_free")
        assert len(rows) == PER_ARM_TOTAL
        assert all(r.finish_reason == "stop" for r in rows)
        assert all(r.request_id == r.sample_ref for r in rows)
        tier0 = list(headless.store.iter_tier0_rows(arm, "stub_free"))
        assert len(tier0) == sum(r.usage.completion_tokens for r in rows)
    qrng = headless.store.generations("qrng", "stub_free")
    prng = headless.store.generations("prng_seed_7", "stub_free")
    assert all(r.provenance.quantum and r.provenance.source_label == "quantum" for r in qrng)
    assert all(
        not r.provenance.quantum and r.provenance.source_label == "seeded_prng" for r in prng
    )
    assert all(r.qr_seed is not None for r in prng) and all(r.qr_seed is None for r in qrng)
    # D-6: qr_seed = H(study_seed, card, prompt, epoch); told cells share a prompt's seed
    assert len({r.qr_seed for r in prng}) == 2 * 2
    assert run_a.progress.by_arm["qrng"].by_card["stub_free"].done == PER_ARM_TOTAL


def test_seeded_arm_is_replayable_across_runs(tmp_path: Path) -> None:
    a = _orchestrator(_run_store(tmp_path / "a"), StubEngine())
    a.run()
    b = _orchestrator(_run_store(tmp_path / "b"), StubEngine())
    b.run()
    out_a = {r.sample_ref: r.output for r in a.store.generations("prng_seed_7", "stub_free")}
    out_b = {r.sample_ref: r.output for r in b.store.generations("prng_seed_7", "stub_free")}
    assert out_a == out_b


def test_kill_and_resume_skips_completed_refs(tmp_path: Path) -> None:
    rs = _run_store(tmp_path)
    control = RunControl()
    seen = 0

    def kill_after_four(body: dict[str, object]) -> None:
        nonlocal seen
        seen += 1
        if seen == 4:
            control.abort("simulated kill")

    first = _orchestrator(
        rs, StubEngine(on_request=kill_after_four), control=control, max_inflight=1
    )
    run1 = first.run()
    assert run1.state == "aborted"
    completed_before = {arm: rs.completed_refs(arm, "stub_free") for arm in ARMS}
    total_before = sum(len(v) for v in completed_before.values())
    assert 0 < total_before < 2 * PER_ARM_TOTAL
    aborted_rows = [
        r for arm in ARMS for r in rs.generations(arm, "stub_free") if r.finish_reason == "abort"
    ]
    assert aborted_rows, "the in-flight sample was recorded as aborted, not lost"

    engine2 = StubEngine()
    resumed = _orchestrator(Store(tmp_path).run(rs.study_id, rs.run_id), engine2)
    run2 = resumed.run()
    assert run2.state == "complete"
    regenerated = set(engine2.request_ids)
    for arm in ARMS:
        assert not (regenerated & completed_before[arm]), "completed refs were regenerated"
        assert len(rs.completed_refs(arm, "stub_free")) == PER_ARM_TOTAL
        assert run2.progress.by_arm[arm].by_card["stub_free"].done == PER_ARM_TOTAL
    # the aborted ref was re-run and now has a completed row too
    for row in aborted_rows:
        assert row.sample_ref in regenerated


def test_taints_are_scoped_to_the_affected_samples(tmp_path: Path) -> None:
    fallback_ref = "qrng/stub_free/p1/0/told_qrng"
    degrade_ref = "qrng/stub_free/p2/1/told_nothing"
    engine = StubEngine(fallback_refs={fallback_ref}, degrade_refs={degrade_ref})
    orch = _orchestrator(_run_store(tmp_path), engine, max_inflight=1)
    run = orch.run()
    assert run.state == "complete"
    taints = orch.store.taints()
    assert {(t.sample_ref, t.kind) for t in taints} == {
        (fallback_ref, "engine_fallback"),
        (degrade_ref, "qrng_degraded"),
    }
    rows = {r.sample_ref: r for r in orch.store.generations("qrng", "stub_free")}
    assert rows[fallback_ref].provenance.engine_fallback_delta == 1
    assert rows[fallback_ref].provenance.quantum is False
    assert rows[degrade_ref].provenance.engine_degraded is True
    assert rows[degrade_ref].provenance.quantum is False
    clean = [r for ref, r in rows.items() if ref not in (fallback_ref, degrade_ref)]
    assert clean and all(r.provenance.quantum for r in clean)
    # the seeded arm is never quantum and its rows stay untainted by the qrng events
    assert all(not r.provenance.quantum for r in orch.store.generations("prng_seed_7", "stub_free"))


def test_stream_error_taints_and_is_rerunnable(tmp_path: Path) -> None:
    bad = "prng_seed_7/stub_free/p1/0/told_prng"
    orch = _orchestrator(_run_store(tmp_path), StubEngine(fail_refs={bad}), max_inflight=1)
    orch.run()
    row = next(r for r in orch.store.generations("prng_seed_7", "stub_free") if r.sample_ref == bad)
    assert row.finish_reason == "error" and row.output == ""
    assert {t.kind for t in orch.store.taints() if t.sample_ref == bad} == {"stream_error"}
    assert bad not in orch.store.completed_refs("prng_seed_7", "stub_free")


def test_stub_engine_rejects_unknown_qr_keys(tmp_path: Path) -> None:
    cards = _cards()
    card = cards["stub_free"].model_copy(update={"task_args": {"qr_overrides": {"qr_bogus": 1}}})
    control = RunControl()
    control.start()
    orch = Orchestrator(
        _study(),
        {"stub_free": card},
        _run_store(tmp_path),
        _config(1),
        secrets=_secrets(),
        control=control,
        transport=StubEngine().transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    )
    orch.run_generation()
    rows = orch.store.generations("qrng", "stub_free")
    assert rows and all(r.finish_reason == "error" for r in rows)
    assert all("400" in t.detail for t in orch.store.taints())


def test_pause_is_honoured_at_the_item_boundary(tmp_path: Path) -> None:
    control = RunControl()
    sink = ListEventSink()
    engine = StubEngine(chunk_delay_s=0.02)
    engine.on_request = lambda body: control.pause() if len(engine.requests) == 1 else None
    orch = _orchestrator(_run_store(tmp_path), engine, sink=sink, control=control, max_inflight=1)
    thread = threading.Thread(target=orch.run_generation, daemon=True)
    thread.start()
    _wait_until(lambda: any(e.data.get("state") == "paused" for e in sink.of_type("run.state")))
    _wait_until(lambda: len(sink.of_type("sample.finished")) == 1)
    time.sleep(0.2)  # paused: nothing new must be dispatched meanwhile
    assert len(sink.of_type("sample.started")) == 1
    assert orch.run_record.state == "paused"
    control.resume()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert len(sink.of_type("sample.finished")) == 2 * PER_ARM_TOTAL
    states = [e.data["state"] for e in sink.of_type("run.state")]
    assert states[:3] == ["running", "paused", "running"]


def test_abort_closes_in_flight_streams(tmp_path: Path) -> None:
    control = RunControl()
    engine = StubEngine(chunk_delay_s=0.05, n_tokens=40)
    engine.on_request = lambda body: (
        control.abort("host abort") if len(engine.requests) == 1 else None
    )
    orch = _orchestrator(_run_store(tmp_path), engine, control=control, max_inflight=1)
    t0 = time.monotonic()
    run = orch.run()
    assert time.monotonic() - t0 < 5.0, "abort must not wait for the stream to finish"
    assert run.state == "aborted"
    assert len(engine.requests) == 1
    ref = engine.request_ids[0]
    assert engine.closed_early == {ref}, "closing the stream is what aborts the engine sequence"
    rows = [r for arm in ARMS for r in orch.store.generations(arm, "stub_free")]
    assert [r.finish_reason for r in rows] == ["abort"]
    assert rows[0].sample_ref == ref
    assert control.in_flight_count == 0


def test_judge_phase_is_resumable_by_sample_ref(tmp_path: Path) -> None:
    failing = StubJudge(fail_first=2)
    orch = _orchestrator(_run_store(tmp_path), StubEngine(), judge=failing)
    run = orch.run()
    assert run.state == "complete"
    rows = [r for arm in ARMS for r in orch.store.judge_rows(arm, "stub_free", "study_judge")]
    assert len(rows) == 2 * PER_ARM_TOTAL
    failed = [r for r in rows if r.score is None]
    assert len(failed) == 2 and all(r.raw.startswith("error:") for r in failed)
    assert all(r.rubric_hash == rows[0].rubric_hash for r in rows)

    healthy = StubJudge()
    again = _orchestrator(
        Store(tmp_path).run(orch.store.study_id, orch.store.run_id), StubEngine(), judge=healthy
    )
    counts = again.run_judge()
    assert counts == {"judged": 2, "skipped": 2 * PER_ARM_TOTAL - 2, "failed": 0}
    assert len(healthy.calls) == 2
    for arm in ARMS:
        done = again.store.completed_judge_refs(arm, "stub_free", "study_judge")
        assert done == again.store.completed_refs(arm, "stub_free")


def test_missing_judge_key_parks_run_in_needs_keys(tmp_path: Path) -> None:
    control = RunControl()
    control.start()
    orch = Orchestrator(
        _study(),
        _cards(),
        _run_store(tmp_path),
        _config(2),
        secrets=SecretBag(),
        control=control,
        transport=StubEngine().transport,
        judge_transport=StubJudge().transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    )
    run = orch.run()
    assert run.state == "needs_keys" and run.needs_keys is True
    assert all(len(orch.store.completed_refs(arm, "stub_free")) == PER_ARM_TOTAL for arm in ARMS)


def test_window_tagger_hook_tags_rows_and_tier0(tmp_path: Path) -> None:
    orch = _orchestrator(_run_store(tmp_path), StubEngine(), max_inflight=1)
    orch.tagger = lambda job: (
        ("supervised", "sess1") if job.arm.id == "qrng" else ("unsupervised", None)
    )
    orch.run_generation()
    for row in orch.store.generations("qrng", "stub_free"):
        assert row.window == "supervised" and row.session_id == "sess1"
    for rec in orch.store.iter_tier0_rows("qrng", "stub_free"):
        assert rec["window"] == "supervised" and rec["session_id"] == "sess1"
    for row in orch.store.generations("prng_seed_7", "stub_free"):
        assert row.window == "unsupervised" and row.session_id is None
