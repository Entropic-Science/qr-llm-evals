"""The per-token ``top_logprobs`` depth is a deployment setting, not a hard 20.

Covers ``QR_EVALS_TOP_LOGPROBS`` parsing, the request bodies, the run's
``request_settings.json`` snapshot, and the top-k coverage diagnostic that tells
an operator whether the configured depth is deep enough.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.config import DEFAULT_TOP_LOGPROBS, MAX_TOP_LOGPROBS, load_settings
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.envelope import (
    TOP_LOGPROBS,
    EnvelopeError,
    SampleContext,
    build_request_body,
    check_top_logprobs,
)
from qr_llm_evals.models.providers import ProviderProfile
from qr_llm_evals.models.schema import Arm, ModelRef, StudyConfig, config_hash
from qr_llm_evals.runner import NullEventSink, Orchestrator, RunControl, RunnerConfig
from qr_llm_evals.store import Run, Store
from qr_llm_evals.tier0.endpoints import t0c_table, topk_coverage
from qr_llm_evals.tier0.schema import Tier0Record
from stub_engine import StubEngine, StubJudge

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "registry"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
MODEL = ModelRef(id="qr-llm", family="qr_llm", provider="qr_server", served_name="qr-llm")
ARM = Arm(id="qrng", model="qr-llm", sampling="creative", source="qrng")
CTX = SampleContext(card_id="c", prompt_id="p", epoch=0, max_tokens=8)
MESSAGES = [{"role": "user", "content": "hi"}]


def test_default_depth_is_twenty_and_configurable() -> None:
    assert DEFAULT_TOP_LOGPROBS == TOP_LOGPROBS == 20
    assert load_settings({}).top_logprobs == 20
    assert load_settings({"QR_EVALS_TOP_LOGPROBS": "100"}).top_logprobs == 100
    assert load_settings({"QR_EVALS_TOP_LOGPROBS": "0"}).top_logprobs == 0


@pytest.mark.parametrize("raw", ["-1", str(MAX_TOP_LOGPROBS + 1), "twenty"])
def test_out_of_range_depth_fails_at_boot(raw: str) -> None:
    with pytest.raises(SystemExit, match="QR_EVALS_TOP_LOGPROBS"):
        load_settings({"QR_EVALS_TOP_LOGPROBS": raw})


def test_request_body_uses_the_configured_depth() -> None:
    default = build_request_body(ARM, MODEL, CTX, MESSAGES)
    deep = build_request_body(ARM, MODEL, CTX, MESSAGES, top_logprobs=100)
    assert default["top_logprobs"] == 20
    assert deep["top_logprobs"] == 100 and deep["logprobs"] is True
    # The depth never touches the engine envelope, so arm equivalence is unaffected.
    assert deep["vllm_xargs"] == default["vllm_xargs"]


def test_provider_depth_is_still_capped_by_profile() -> None:
    arm = Arm(id="ext", model="custom:x#m", sampling="creative", source="provider")
    model = ModelRef(id="custom:x#m", family="external", provider="custom", served_name="m")
    profile = ProviderProfile(
        id="capped",
        accepted_params=("temperature", "logprobs", "top_logprobs"),
        logprobs_honoured=True,
        top_logprobs_cap=5,
    )
    body = build_request_body(arm, model, CTX, MESSAGES, profile=profile, top_logprobs=100)
    assert body["top_logprobs"] == 5


@pytest.mark.parametrize("bad", [-1, MAX_TOP_LOGPROBS + 1, True, 2.5])
def test_check_top_logprobs_rejects_invalid_depths(bad: object) -> None:
    with pytest.raises(EnvelopeError):
        check_top_logprobs(bad)  # type: ignore[arg-type]


def _record(rank: int, index: int = 0, ref: str = "a/c/p/0") -> Tier0Record:
    return Tier0Record(
        sample_ref=ref,
        token_index=index,
        token_id=index,
        logprob=-0.5,
        rank_raw=rank,
        entropy_raw_top20=0.5,
        timestamp_ns=0,
    )


def test_topk_coverage_counts_tokens_inside_the_returned_list() -> None:
    records = [_record(r, i) for i, r in enumerate([0, 1, 20, 3])]
    assert topk_coverage(records, 20) == pytest.approx(0.75)
    assert topk_coverage(records, 50) == 1.0
    assert topk_coverage(records, 0) is None
    assert topk_coverage([], 20) is None


def test_t0c_table_reports_coverage_when_depth_known() -> None:
    records = {"qrng": [_record(r, i) for i, r in enumerate([0, 5, 20])]}
    table = t0c_table(records, tier0_source="api_logprobs", top_k=20)
    assert table["arms"]["qrng"]["topk_coverage"] == pytest.approx(2 / 3)
    assert table["labels"]["top_logprobs"] == 20
    assert "topk_coverage" not in t0c_table(records, tier0_source="api_logprobs")["arms"]["qrng"]


def _study() -> StudyConfig:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    return StudyConfig.model_validate(
        {
            "title": "Depth test study",
            "authors": ["Entropic Science"],
            "contact": "hello@entropic.science",
            "created_at": NOW.isoformat(),
            "models": [model],
            "arms": [{"id": "qrng", "model": "qr-llm", "sampling": "creative", "source": "qrng"}],
            "cards": [{"card_id": "stub_free"}],
            "judge": {"model": "claude-sonnet-4-6"},
            "narrative_model": model,
        }
    )


def test_run_sends_and_records_the_configured_depth(tmp_path: Path) -> None:
    store = Store(tmp_path)
    study = _study()
    study_dir = store.freeze_study(study, "# prereg\n")
    run_store = store.create_run(
        Run(
            run_id="run1",
            study_id=study_dir.study_id,
            kind="pilot",
            created_at=NOW,
            key_name="test",
            host_token_hash="b" * 64,
            config_hash=config_hash(study),
        )
    )
    engine = StubEngine()
    control = RunControl()
    control.start()
    record = Orchestrator(
        study,
        load_cards(FIXTURES / "evals"),
        run_store,
        RunnerConfig(vllm_origin="http://stub", repo_root=FIXTURES, top_logprobs=64),
        secrets=SecretBag({"anthropic": "test-judge-key"}),
        sink=NullEventSink(),
        control=control,
        transport=engine.transport,
        judge_transport=StubJudge().transport,
        clock=lambda: NOW,
        perf=lambda: 0.0,
    ).run()
    assert record.state == "complete"
    assert engine.requests and {body["top_logprobs"] for body in engine.requests} == {64}
    snapshot = json.loads((run_store.run_dir.path / "request_settings.json").read_text())
    assert snapshot == {"schema_version": 1, "top_logprobs": 64}
    assert run_store.read_request_settings() == snapshot
