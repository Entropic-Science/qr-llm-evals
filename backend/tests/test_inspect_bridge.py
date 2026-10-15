"""Offline bridge contracts; skip cleanly without the optional Inspect engine."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("inspect_ai", reason="install the [inspect] extra for bridge tests")

from inspect_ai import eval_async
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ChatMessageUser, GenerateConfig, get_model

from qr_llm_evals.cards import load_cards
from qr_llm_evals.inspect_bridge.adapter import DATASET_REVISION, inspect_task
from qr_llm_evals.inspect_bridge.provider import (
    QrGraderModelAPI,
    QrLlmModelAPI,
    Runtime,
    SampleContext,
    bind_runtime,
    sample_context,
)
from qr_llm_evals.inspect_bridge.subprocess_runner import InspectProcessError, run_inspect_card
from qr_llm_evals.models.envelope import SampleContext as EnvelopeContext
from stub_engine import StubEngine
from test_runner import _orchestrator, _run_store


def _card():
    card = load_cards()["novelty_bench"]
    return card.model_copy(
        update={
            "k": 2,
            "task_args": {**card.task_args, "num_generations": 2},
            "dataset": card.dataset.model_copy(update={"items": 1}),
        }
    )


@pytest.mark.parametrize("arm_index", [0, 1])
async def test_provider_rows_byte_equal_to_thin(tmp_path: Path, arm_index: int) -> None:
    thin_store, bridge_store = _run_store(tmp_path / "thin"), _run_store(tmp_path / "bridge")
    thin_engine, bridge_engine = StubEngine(), StubEngine()
    thin, bridge = (
        _orchestrator(thin_store, thin_engine),
        _orchestrator(bridge_store, bridge_engine),
    )
    arm = thin.study.arms[arm_index]
    job = thin.plan_arm(arm)[0]
    job = dataclasses.replace(job, told=None, ctx=dataclasses.replace(job.ctx, told_condition=None))
    messages = job.task.build_messages(job.item, None)
    thin.dispatch(job)
    ctx = SampleContext(**dataclasses.asdict(job.ctx), arm=arm)
    token = sample_context.set(ctx)
    try:
        with bind_runtime(Runtime(arm, lambda c, m: bridge.dispatch_inspect(arm, job.card, c, m))):
            result = await QrLlmModelAPI(arm.id).generate(
                [ChatMessageUser(content=messages[0]["content"])],
                [],
                "none",
                GenerateConfig(temperature=99, seed=999, extra_body={"qr_seed": 999}),
            )
    finally:
        sample_context.reset(token)
    assert result.completion == thin_store.generations(arm.id, job.card.id)[0].output
    for name in ("generations_jsonl", "tier0_jsonl"):
        assert (
            getattr(thin_store.arm_card(arm.id, job.card.id), name).read_bytes()
            == getattr(bridge_store.arm_card(arm.id, job.card.id), name).read_bytes()
        )


async def test_tiny_local_inspect_task_epochs_and_log(tmp_path: Path) -> None:
    card = _card()
    store = _run_store(tmp_path / "data")
    engine = StubEngine()
    orch = _orchestrator(store, engine)
    arm = orch.study.arms[1]
    dataset = MemoryDataset([Sample(id="p1", input="Write something unusual.")])
    task = inspect_task(card, dataset=dataset)
    with bind_runtime(Runtime(arm, lambda c, m: orch.dispatch_inspect(arm, card, c, m))):
        logs = await eval_async(
            task,
            model=f"qrllm/{arm.id}",
            log_dir=str(tmp_path / "logs"),
            log_format="eval",
            max_samples=2,
            model_roles={
                "grader": get_model("qrgrader/fixed", config=GenerateConfig(temperature=0))
            },
            ctl_server=False,
        )
    assert logs[0].status == "success"
    rows = store.generations(arm.id, card.id)
    assert sorted(r.epoch for r in rows) == [0, 1]
    assert len({r.qr_seed for r in rows}) == 2
    assert len(rows) == card.k  # never k squared
    assert sorted(s.metadata["qr_join_key"][2] for s in logs[0].samples) == [0, 1]


def test_subprocess_progress_resume_and_retained_log(tmp_path: Path) -> None:
    card = _card()
    store = _run_store(tmp_path / "data")
    orch = _orchestrator(store, StubEngine())
    arm = orch.study.arms[1]
    events = []
    kwargs = dict(
        judge_model="fixed",
        samples=[{"id": "p1", "input": "Write something unusual."}],
        on_sample_end=lambda prompt, epoch: events.append((prompt, epoch)),
        timeout_s=120,
    )

    def dispatch(c, m):
        return orch.dispatch_inspect(arm, card, c, m)

    log_dir = store.arm_card(arm.id, card.id).inspect_dir
    result = run_inspect_card(card, arm, log_dir, dispatch, **kwargs)
    assert result.completed == 2
    assert sorted(events) == [("p1", 0), ("p1", 1)]
    log = read_eval_log(str(result.logs[0]))
    assert log.status == "success" and len(log.samples) == 2
    assert log.eval.config.epochs == 2
    before = store.arm_card(arm.id, card.id).generations_jsonl.read_bytes()
    second = run_inspect_card(card, arm, log_dir, dispatch, **kwargs)
    assert second.logs != result.logs
    assert all(p.exists() for p in (*result.logs, *second.logs))
    assert before == store.arm_card(arm.id, card.id).generations_jsonl.read_bytes()
    assert b"sk-ant-test" not in b"".join(p.read_bytes() for p in log_dir.rglob("*") if p.is_file())


def _crash(connection: Any, payload: Any) -> None:
    connection.close()
    raise SystemExit(17)


def test_subprocess_crash_does_not_kill_parent(tmp_path: Path, monkeypatch) -> None:
    import qr_llm_evals.inspect_bridge.subprocess_runner as module

    store = _run_store(tmp_path / "data")
    orch = _orchestrator(store, StubEngine())
    monkeypatch.setattr(module, "_child", _crash)
    with pytest.raises(InspectProcessError):
        run_inspect_card(
            _card(),
            orch.study.arms[0],
            tmp_path / "logs",
            lambda c, m: pytest.fail("must not dispatch"),
            judge_model="fixed",
            timeout_s=30,
        )
    assert store.read_run().run_id == "run1"


def test_adapter_rejects_budget_and_revision_drift() -> None:
    card = _card()
    for changed in (
        card.model_copy(update={"task_args": {"num_generations": 20}}),
        card.model_copy(update={"dataset": card.dataset.model_copy(update={"revision": "main"})}),
    ):
        with pytest.raises(ValueError):
            inspect_task(changed, dataset=MemoryDataset([Sample(id="p1", input="hi")]))


async def test_missing_or_wrong_context_fails_closed(tmp_path: Path) -> None:
    orch = _orchestrator(_run_store(tmp_path), StubEngine())
    arm = orch.study.arms[0]
    with bind_runtime(Runtime(arm, lambda c, m: pytest.fail("must not dispatch"))):
        with pytest.raises(LookupError):
            await QrLlmModelAPI(arm.id).generate(
                [ChatMessageUser(content="hi")], [], "none", GenerateConfig()
            )
        token = sample_context.set(
            SampleContext(card_id="novelty_bench", prompt_id="p1", epoch=0, max_tokens=10, arm=arm)
        )
        try:
            with pytest.raises(ValueError, match="mismatch"):
                await QrLlmModelAPI("wrong").generate(
                    [ChatMessageUser(content="hi")], [], "none", GenerateConfig()
                )
        finally:
            sample_context.reset(token)


def test_resume_refuses_changed_prompt(tmp_path: Path) -> None:
    orch = _orchestrator(_run_store(tmp_path), StubEngine())
    card, arm = _card(), orch.study.arms[1]
    ctx = EnvelopeContext(card.id, "p1", 0, card.max_tokens)
    orch.dispatch_inspect(arm, card, ctx, [{"role": "user", "content": "original"}])
    with pytest.raises(ValueError, match="resume prompt"):
        orch.dispatch_inspect(arm, card, ctx, [{"role": "user", "content": "changed"}])


def test_card_is_pinned_and_budgeted() -> None:
    card = load_cards()["novelty_bench"]
    assert card.status == "implemented" and card.selectable
    assert card.k == 20 and card.est_gen_tokens_per_arm == 800000
    assert card.endpoints.primary == ["distinct_k"]
    assert card.dataset.revision == DATASET_REVISION


def test_orchestrator_requires_and_runs_inspect_executor(tmp_path: Path) -> None:
    card = _card()
    orch = _orchestrator(_run_store(tmp_path / "missing"), StubEngine())
    selection = orch.study.cards[0].model_copy(update={"card_id": card.id})
    orch.study = orch.study.model_copy(update={"cards": [selection]})
    orch.cards = {card.id: card}
    with pytest.raises(RuntimeError, match="requires an Inspect executor"):
        orch.run()
    assert orch.store.read_run().state == "aborted"

    orch = _orchestrator(_run_store(tmp_path / "wired"), StubEngine())
    orch.study = orch.study.model_copy(update={"cards": [selection]})
    orch.cards = {card.id: card}
    calls = []

    def execute(runner, arm, selected):
        calls.append((arm.id, selected.id))
        runner.dispatch_inspect(
            arm,
            selected,
            EnvelopeContext(selected.id, "p1", 0, selected.max_tokens),
            [{"role": "user", "content": "hi"}],
        )

    orch._inspect_executor = execute
    assert orch.run().state == "complete"
    assert calls == [(arm.id, card.id) for arm in orch.study.arms]


async def test_grader_is_fixed_temperature_zero(tmp_path: Path) -> None:
    orch = _orchestrator(_run_store(tmp_path), StubEngine())
    calls = []

    def grade(messages):
        calls.append(messages)
        return "score: 1"

    with bind_runtime(
        Runtime(orch.study.arms[0], lambda c, m: pytest.fail("target called"), grade)
    ):
        provider = QrGraderModelAPI("fixed")
        output = await provider.generate(
            [ChatMessageUser(content="grade this")], [], "none", GenerateConfig(temperature=0)
        )
        assert output.completion == "score: 1" and len(calls) == 1
        with pytest.raises(ValueError):
            await provider.generate(
                [ChatMessageUser(content="grade this")], [], "none", GenerateConfig(temperature=1)
            )
        assert len(calls) == 1
