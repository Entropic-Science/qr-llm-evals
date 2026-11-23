"""Shared set-up for the tests of result checks in a run (flags, taints, stops).

A study on the stub engine with one quantum arm and any number of seeded
arms, the stub card in any shape a test needs (more prompts, no judge,
another id), and an orchestrator with a fixed clock so artifacts are
reproducible byte for byte.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from qr_llm_evals.cards import EvalCard, load_cards
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.runner import NullEventSink, Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.runner.tasks import LoadedTask
from qr_llm_evals.store import Run, RunStore, Store
from qr_llm_evals.store.artifacts import iter_jsonl
from stub_engine import StubEngine, StubJudge

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "registry"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
H = "b" * 64
CARD = "stub_free"
JUDGE_KEY = "sk-ant-test-JUDGE-KEY-0123456789"


def study(card_id: str = CARD, *, seeded: int = 1) -> StudyConfig:
    """One quantum arm and ``seeded`` seeded pseudo-random arms on one card."""
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    arms: list[dict[str, Any]] = [
        {"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"}
    ]
    for index in range(seeded):
        seed = 7 + index
        arms.append(
            {
                "id": f"prng_seed_{seed}",
                "model": "qr-llm",
                "sampling": "creative",
                "source": "prng_seeded",
                "study_seed": seed,
            }
        )
    return StudyConfig.model_validate(
        {
            "title": "Result checks test study",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [model],
            "arms": arms,
            "seeds": [7 + index for index in range(max(1, seeded))],
            "cards": [{"card_id": card_id}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": model,
        }
    )


def card(
    *, card_id: str = CARD, items: int | None = None, k: int | None = None, judged: bool = True
) -> EvalCard:
    """The stub card: 2 prompts, k 2 and three told cells unless told otherwise."""
    base = load_cards(FIXTURES / "evals")[CARD]
    update: dict[str, Any] = {"id": card_id}
    if items is not None:
        update["dataset"] = base.dataset.model_copy(update={"items": items})
    if k is not None:
        update["k"] = k
    if not judged:
        update["judge"] = type(base.judge)()  # the card default: no judge
    return base.model_copy(update=update)


def log_row(index: int, **over: Any) -> dict[str, Any]:
    """One healthy line of the engine's per-token log, as qr-sampler writes it."""
    row: dict[str, Any] = {
        "token_index": index,
        "token_id": 100 + index,
        "timestamp_ns": 1_000 + index,
        "u_value": 0.25,
        "cdf_lo": 0.0,
        "cdf_hi": 0.5,
        "token_rank": 0,
        "token_prob": 0.5,
        "temperature_used": 1.0,
        "min_p_used": 0.0,
        "num_candidates": 2,
        "entropy_is_fallback": False,
    }
    if index == 0:
        row["entropy_source_used"] = "quantum_grpc"
        row["top_entries"] = [[100, 0.5], [7, 0.5]]
    row.update(over)
    return row


def engine_writes_logs(
    engine: StubEngine,
    log_dir: Path,
    rows_for: Callable[[str], list[dict[str, Any]] | None] | None = None,
) -> None:
    """Make the stub engine write a token log for each request, like the real sampler.

    ``rows_for(sample_ref)`` gives the lines of one request's log, or ``None``
    for no file at all. Without it every request gets a healthy log.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    def on_request(body: dict[str, Any]) -> None:
        ref = str(body["vllm_xargs"]["qr_request_id"])
        rows = [log_row(i) for i in range(engine.n_tokens)] if rows_for is None else rows_for(ref)
        if rows is None:
            return
        lines = "".join(json.dumps(row) + "\n" for row in rows)
        path = log_dir / (ref.replace("/", "__") + ".jsonl")
        path.write_text(lines, encoding="utf-8", newline="\n")

    engine.on_request = on_request


def log_config(log_dir: Path, max_inflight: int = 1) -> RunnerConfig:
    return RunnerConfig(
        vllm_origin="http://stub",
        max_inflight=max_inflight,
        repo_root=FIXTURES,
        token_log_dir=str(log_dir),
        token_log_wait_s=0.0,
    )


def run_store(root: Path, config: StudyConfig, run_id: str = "run1") -> RunStore:
    store = Store(root)
    study_dir = store.freeze_study(config, "# prereg\n")
    return store.create_run(
        Run(
            run_id=run_id,
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash=H,
            config_hash=config_hash(config),
        )
    )


def reopen(rs: RunStore) -> RunStore:
    """The same run through a new store, as after a backend restart."""
    return Store(rs.store.root.path).run(rs.study_id, rs.run_id)


def orchestrator(
    rs: RunStore,
    config: StudyConfig,
    cards: Mapping[str, EvalCard],
    engine: StubEngine | Any,
    *,
    judge: StubJudge | Any | None = None,
    sink: Any | None = None,
    control: RunControl | None = None,
    max_inflight: int = 1,
    secrets: SecretBag | None = None,
    started: bool = True,
    **kwargs: Any,
) -> Orchestrator:
    ctl = control or RunControl()
    if started:
        ctl.start()
    runner_config = kwargs.pop("runner_config", None) or RunnerConfig(
        vllm_origin="http://stub", max_inflight=max_inflight, repo_root=FIXTURES
    )
    return Orchestrator(
        config,
        cards,
        rs,
        runner_config,
        secrets=secrets if secrets is not None else SecretBag({"anthropic": JUDGE_KEY}),
        sink=sink or NullEventSink(),
        control=ctl,
        transport=engine.transport,
        judge_transport=(judge or StubJudge()).transport,
        clock=kwargs.pop("clock", lambda: NOW),
        perf=lambda: 0.0,
        **kwargs,
    )


def with_task(orch: Orchestrator, the_card: EvalCard, **functions: Callable[..., Any]) -> None:
    """Replace functions of the card's task module for this orchestrator only."""
    real = orch._task_for(the_card)
    module = ModuleType("scripted_task")
    for name in ("build_items", "build_messages", "score", "judge_prompt", "judge_fields"):
        if hasattr(real.module, name):
            setattr(module, name, getattr(real.module, name))
    for name, fn in functions.items():
        setattr(module, name, fn)
    orch._tasks[the_card.id] = LoadedTask(the_card, module, real.path)


def flags(rs: RunStore) -> list[dict[str, Any]]:
    return list(iter_jsonl(rs.run_dir.flags_jsonl))


def flag_kinds(rs: RunStore) -> Counter[str]:
    return Counter(str(row["kind"]) for row in flags(rs))


def taint_kinds(rs: RunStore, ref: str | None = None) -> Counter[str]:
    return Counter(t.kind for t in rs.taints() if ref is None or t.sample_ref == ref)


def arm_ids(config: StudyConfig) -> list[str]:
    return [arm.id for arm in config.arms]


def finishes(rs: RunStore, config: StudyConfig, card_id: str = CARD) -> Counter[str]:
    return Counter(
        row.finish_reason for arm in arm_ids(config) for row in rs.generations(arm, card_id)
    )


def never_completed(rs: RunStore, config: StudyConfig, card_id: str = CARD) -> list[str]:
    """Samples that were tried and still have no completed row."""
    missing: list[str] = []
    for arm in arm_ids(config):
        done = rs.completed_refs(arm, card_id)
        tried = {row.sample_ref for row in rs.generations(arm, card_id)}
        missing.extend(sorted(tried - done))
    return missing


def run_text(rs: RunStore) -> str:
    """Everything a flag or a halt of the run says, as one string."""
    return rs.run_dir.run_json.read_text(encoding="utf-8") + (
        rs.run_dir.flags_jsonl.read_text(encoding="utf-8")
        if rs.run_dir.flags_jsonl.is_file()
        else ""
    )
