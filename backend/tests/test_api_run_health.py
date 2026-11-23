"""What the API serves about result checks: the health summary, the halts, the counts.

``GET /api/runs/{id}/health`` is the store's health summary, as open as the run
itself. Run payloads carry the halts and the checks version. The public counts
of tainted and failed samples come from the summary, and the arm ledger of an
analysis is counted from the same records. A halt can hold no key.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from qr_llm_evals.analysis.endpoints import excluded_refs, load_data
from qr_llm_evals.api import create_app
from qr_llm_evals.api.deps import sha256_hex
from qr_llm_evals.api.routes import run_health as run_health_module
from qr_llm_evals.api.routes.studies import _provenance
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.config import load_settings
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.keys.verify import FileKeyVerifier
from qr_llm_evals.logging_utils import REDACTED, _clear_registry_for_tests
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.runner.control import RunControl
from qr_llm_evals.runner.events import EventSink
from qr_llm_evals.store import HealthSummary, Store, summarise
from qr_llm_evals.store.health import MADE_BEFORE_RESULT_CHECKS
from qr_llm_evals.store.schema import (
    OTHER_CAUSE,
    GenerationRow,
    Halt,
    Provenance,
    ResultFlag,
    Run,
    Taint,
    Timing,
)
from qr_llm_evals.store.store import RunStore

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 22, 13, 0, tzinfo=UTC)
H = "a" * 64
CARD = "eval_awareness"
HOST_TOKEN = "host_test_token"
OWNER = "usr_private"
KEY_NAME = "launch-key-name"
SECRET = "sk-ant-" + "api03-supersecret-0123456789"
#: A key of no known shape: only the registry of live secrets can catch it.
ODD_SECRET = "customkey0123456789abcdef"


def _study() -> StudyConfig:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    return StudyConfig.model_validate(
        {
            "title": "Health over the API",
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
            ],
            "cards": [{"card_id": CARD}],
            "judge": {"model": "claude-sonnet-4"},
            "narrative_model": model,
            "expectations": "None.",
        }
    )


class Service:
    """One app over a store with one run, and the orchestrators the app asked for."""

    def __init__(self, tmp_path: Path, **run_fields: Any) -> None:
        settings = load_settings(
            {
                "QR_EVALS_ALLOW_KEY_LAUNCH": "1",
                "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
                "QR_EVALS_KEYS_FILE": str(tmp_path / "keys.json"),
                "QR_EVALS_VLLM_ORIGIN": "http://stub",
            }
        )
        Path(settings.keys_file).write_text("[]\n", encoding="utf-8")
        self.store = Store(settings.data_dir)
        study = _study()
        self.study_id = self.store.freeze_study(study, "# Prereg\n").study_id
        self.rs = self.store.create_run(
            Run.model_validate(
                {
                    "run_id": "run1",
                    "study_id": self.study_id,
                    "kind": "full",
                    "state": "paused",
                    "created_at": NOW,
                    "key_name": KEY_NAME,
                    "host_token_hash": sha256_hex(HOST_TOKEN),
                    "config_hash": config_hash(study),
                    "checks_version": rc.CHECKS_VERSION,
                    **run_fields,
                }
            )
        )
        #: What each orchestrator the app built saw when it was built.
        self.built: list[Run] = []
        self.app: FastAPI = create_app(
            settings,
            store=self.store,
            verifier=FileKeyVerifier(settings.keys_file),
            cards=load_cards(),
            orchestrator_factory=self._factory,
        )

    def _factory(
        self, run_store: RunStore, secrets: SecretBag, control: RunControl, sink: EventSink
    ) -> Any:
        service = self

        class Resumed:
            """Stands in for the orchestrator a resume starts after a check stop."""

            def __init__(self) -> None:
                service.built.append(run_store.read_run())

            def run(self) -> Run:
                run_store.stamp_resumed(LATER)
                done = run_store.read_run().model_copy(update={"state": "complete"})
                run_store.write_run(done)
                return done

        return Resumed()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )


@pytest.fixture
def service(tmp_path: Path) -> Iterator[Service]:
    run_health_module._CACHE.clear()
    found = Service(tmp_path)
    yield found
    found.store.close()
    _clear_registry_for_tests()


def _ref(arm: str, n: int) -> str:
    return f"{arm}/{CARD}/p{n}/0"


def _generate(rs: RunStore, arm: str, n: int, finish: str = "stop", **provenance: Any) -> str:
    ref = _ref(arm, n)
    quantum = arm == "qrng" and finish in ("stop", "length") and not provenance
    rs.append_generation(
        GenerationRow(
            sample_ref=ref,
            arm_id=arm,
            card_id=CARD,
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
                source_label="quantum" if arm == "qrng" else "seeded_prng",
                quantum=quantum,
                determinism_tier="none",
                pipeline_pinned=True,
                **provenance,
            ),
            timing=Timing(started=NOW),
            request_id=ref,
        )
    )
    return ref


def _taint(rs: RunStore, ref: str, kind: str) -> None:
    rs.append_taint(Taint(sample_ref=ref, kind=kind, at=NOW))  # type: ignore[arg-type]


def _halt(**extra: Any) -> Halt:
    fields: dict[str, Any] = {
        "rule": "generation_errors",
        "scope": "arm",
        "scope_id": "qrng",
        "phase": "generation",
        "at": NOW,
        "sentence": "8 generations in a row on arm qrng ended in an error.",
        "recovery": rc.STOP_RULES["generation_errors"].recovery,
        "counts": {"in_a_row": 8},
        "causes": {"HTTP 502": 5, "ReadTimeout": 3},
        "sample_refs": [_ref("qrng", 0)],
    }
    return Halt.model_validate({**fields, **extra})


def _fill(rs: RunStore) -> None:
    """A run with a retried error, a lost sample, a fallback, a cancelled sample and a flag."""
    for n in range(6):
        _generate(rs, "prng7", n)
    _generate(rs, "qrng", 0, finish="error")  # failed, then generated again
    _taint(rs, _ref("qrng", 0), "stream_error")
    _generate(rs, "qrng", 0)
    _generate(rs, "qrng", 1, finish="error")  # failed and never tried again
    _taint(rs, _ref("qrng", 1), "stream_error")
    _generate(rs, "qrng", 2, engine_fallback_delta=1)  # completed on a fallback
    _taint(rs, _ref("qrng", 2), "engine_fallback")
    _generate(rs, "qrng", 3, finish="abort")  # cancelled: the store writes "abort"
    _generate(rs, "qrng", 4)
    _generate(rs, "qrng", 5)
    rs.append_flag(
        ResultFlag(
            sample_ref=_ref("qrng", 4),
            kind="truncated",
            phase="generation",
            card_id=CARD,
            arm_id="qrng",
            at=NOW,
        )
    )
    rs.append_halt(_halt())


# ── GET /api/runs/{id}/health ─────────────────────────────────────────────────


async def test_the_health_route_serves_the_summary_to_anyone_who_can_see_the_run(
    service: Service,
) -> None:
    _fill(service.rs)
    async with service.client() as client:
        run = await client.get("/api/runs/run1")
        health = await client.get("/api/runs/run1/health")  # no key, no host token
    assert run.status_code == health.status_code == 200, health.text
    body = health.json()
    assert body == summarise(service.rs).model_dump(mode="json")
    summary = HealthSummary.model_validate(body)
    assert summary.checks_version == rc.CHECKS_VERSION and summary.note is None
    assert [halt.rule for halt in summary.halts] == ["generation_errors"]
    cells = {(cell.arm_id, cell.card_id): cell for cell in summary.cells}
    quantum = cells[("qrng", CARD)]
    assert (quantum.completed, quantum.errored) == (4, 2)
    assert quantum.taints == {"engine_fallback": 1, "stream_error": 2}
    assert quantum.flags == {"truncated": 1}
    assert {caution.kind for caution in summary.cautions} == {"run_not_complete", "open_halt"}
    # Nothing about who launched the run.
    assert KEY_NAME not in health.text and sha256_hex(HOST_TOKEN) not in health.text


async def test_health_of_an_unknown_run_is_a_404(service: Service) -> None:
    async with service.client() as client:
        missing = await client.get("/api/runs/run_absent/health")
    assert missing.status_code == 404
    assert missing.json()["error"]["param"] == "run_id"


async def test_a_run_made_before_result_checks_says_so(tmp_path: Path) -> None:
    run_health_module._CACHE.clear()
    old = Service(tmp_path, checks_version=None, state="complete")
    try:
        _generate(old.rs, "qrng", 0)
        async with old.client() as client:
            body = (await client.get("/api/runs/run1/health")).json()
            view = (await client.get("/api/runs/run1")).json()
    finally:
        old.store.close()
    assert body["checks_version"] is None
    assert body["note"] == MADE_BEFORE_RESULT_CHECKS
    assert [(cell["arm_id"], cell["completed"], cell["flags"]) for cell in body["cells"]] == [
        ("qrng", 1, None)
    ]
    # The run payload has both fields even when the file leaves them out.
    assert view["run"]["halts"] == [] and view["run"]["checks_version"] is None
    assert "halts" not in json.loads(old.rs.run_dir.run_json.read_text(encoding="utf-8"))


async def test_the_summary_is_read_once_until_a_file_of_the_run_changes(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fill(service.rs)
    reads: list[str] = []

    def counting(run_store: RunStore) -> HealthSummary:
        reads.append(run_store.run_id)
        return summarise(run_store)

    monkeypatch.setattr(run_health_module, "summarise", counting)
    async with service.client() as client:
        first = (await client.get("/api/runs/run1/health")).json()
        again = (await client.get("/api/runs/run1/health")).json()
        assert first == again and len(reads) == 1
        study = (await client.get(f"/api/studies/{service.study_id}")).json()
        assert study["health"]["run1"]["tainted_samples"] == 3 and len(reads) == 1

        # A new row, a new halt and a state change each show at once.
        _taint(service.rs, _ref("qrng", 5), "qrng_degraded")
        after_taint = (await client.get("/api/runs/run1/health")).json()
        assert after_taint["tainted_samples"] == 4 and len(reads) == 2
        service.rs.append_halt(_halt(rule="judge_unusable", scope="run", scope_id=None))
        after_halt = (await client.get("/api/runs/run1/health")).json()
        assert len(after_halt["halts"]) == 2 and len(reads) == 3
        service.rs.set_progress("qrng", CARD, done=4, total=9, aborted=1)
        after_plan = (await client.get("/api/runs/run1/health")).json()
        planned = {cell["arm_id"]: cell["planned"] for cell in after_plan["cells"]}
        assert planned["qrng"] == 9 and len(reads) == 4


# ── run payloads ──────────────────────────────────────────────────────────────


async def test_run_and_study_payloads_carry_the_halts_and_the_checks_version(
    service: Service,
) -> None:
    _fill(service.rs)
    async with service.client() as client:
        view = (await client.get("/api/runs/run1")).json()
        study = (await client.get(f"/api/studies/{service.study_id}")).json()
    expected = _halt().model_dump(mode="json")
    assert expected["resumed_at"] is None
    for run in (view["run"], study["runs"][0]):
        assert run["halts"] == [expected]
        assert run["checks_version"] == rc.CHECKS_VERSION
    public = study["runs"][0]
    assert not {"host_token_hash", "key_name", "owner_id", "keys_present"} & set(public)


# ── the public counts ─────────────────────────────────────────────────────────


async def test_the_study_route_counts_tainted_and_failed_samples_from_the_summary(
    service: Service,
) -> None:
    _fill(service.rs)
    summary = summarise(service.rs)
    async with service.client() as client:
        study = (await client.get(f"/api/studies/{service.study_id}")).json()
    assert study["health"] == {
        "run1": {
            "completed_samples": 10,
            "failed_samples": 2,
            "tainted_samples": 3,
            "taints": {"engine_fallback": 1, "stream_error": 2},
        }
    }
    assert study["health"]["run1"]["tainted_samples"] == summary.tainted_samples
    assert study["health"]["run1"]["taints"] == summary.taints
    assert study["health"]["run1"]["failed_samples"] == sum(c.errored for c in summary.cells)
    # The run record itself never held the taints: the counts are the summary's.
    assert study["runs"][0]["taints"] == []


async def test_the_arm_ledger_of_an_analysis_agrees_with_the_summary(service: Service) -> None:
    _fill(service.rs)
    output = service.rs.run_dir.analysis_dir("a_plan")
    output.mkdir(parents=True)
    (output / "plan.json").write_text("{}", encoding="utf-8")
    (output / "manifest.json").write_text("{}", encoding="utf-8")
    summary = summarise(service.rs)
    async with service.client() as client:
        index = (await client.get(f"/api/studies/{service.study_id}/analysis/a_plan/index")).json()
    rows = {(row["arm_id"], row["source_label"]): row for row in index["provenance"]}
    assert rows == {
        # Two clean quantum samples and the one whose failed attempt was tried again:
        # it completed as a quantum sample, carries the taint of the failed attempt
        # and is not excluded, exactly as the analysis treats it.
        ("qrng", "quantum"): {
            "arm_id": "qrng",
            "source_label": "quantum",
            "quantum": True,
            "samples": 3,
            "tainted_samples": 1,
            "excluded_samples": 0,
            "failed_samples": 0,
        },
        # The lost sample, the fallback and the cancelled one.
        ("qrng", "unverified"): {
            "arm_id": "qrng",
            "source_label": "unverified",
            "quantum": False,
            "samples": 3,
            "tainted_samples": 2,
            "excluded_samples": 2,
            "failed_samples": 2,
        },
        ("prng7", "seeded_prng"): {
            "arm_id": "prng7",
            "source_label": "seeded_prng",
            "quantum": False,
            "samples": 6,
            "tainted_samples": 0,
            "excluded_samples": 0,
            "failed_samples": 0,
        },
    }
    # "Excluded" is the analysis's own rule, applied to what the analysis loads.
    loaded = load_data(service.rs.run_dir.path)
    assert excluded_refs(service.rs.run_dir.path, loaded.completed) == {
        _ref("qrng", 1),
        _ref("qrng", 2),
    }
    ledger = index["provenance"]
    assert sum(row["tainted_samples"] for row in ledger) == summary.tainted_samples
    assert sum(row["failed_samples"] for row in ledger) == sum(c.errored for c in summary.cells)
    assert sum(row["samples"] for row in ledger) == sum(
        c.completed + c.errored for c in summary.cells
    )
    assert index["health"] == {
        "completed_samples": 10,
        "failed_samples": 2,
        "tainted_samples": 3,
        "taints": {"engine_fallback": 1, "stream_error": 2},
    }


def test_a_cancelled_sample_is_failed_and_never_a_clean_quantum_sample(tmp_path: Path) -> None:
    """The store writes ``abort``; the ledger used to look for ``aborted`` and missed it."""
    card = tmp_path / "arms" / "qrng" / "cards" / CARD
    card.mkdir(parents=True)
    quantum = {"source_label": "quantum", "quantum": True}
    rows = [
        {"sample_ref": "qrng/c/p0/0", "finish_reason": "abort", "provenance": quantum},
        {"sample_ref": "qrng/c/p1/0", "finish_reason": "stop", "provenance": quantum},
        # A token-record taint is in taints.jsonl only; the row itself looks clean.
        {"sample_ref": "qrng/c/p2/0", "finish_reason": "stop", "provenance": quantum},
    ]
    (card / "generations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (tmp_path / "taints.jsonl").write_text(
        json.dumps({"sample_ref": "qrng/c/p2/0", "kind": "stateless_row"}) + "\n", encoding="utf-8"
    )
    rows_by_label = {row["source_label"]: row for row in _provenance(tmp_path)}
    assert rows_by_label["quantum"]["samples"] == 1
    assert rows_by_label["unverified"] == {
        "arm_id": "qrng",
        "source_label": "unverified",
        "quantum": False,
        "samples": 2,
        "tainted_samples": 1,
        "excluded_samples": 1,
        "failed_samples": 1,
    }


# ── a halt holds no key (AGENTS.md invariant 3) ───────────────────────────────


def test_a_halt_keeps_harness_names_and_tallies_everything_else_as_other() -> None:
    halt = _halt(
        causes={
            "HTTP 502": 5,
            "ReadTimeout": 3,
            "httpx.ConnectError": 2,
            "engine_fallback": 1,
            "qrng/eval_awareness": 4,
            # The harness's own phrases for a stream that broke.
            "no finish reason": 2,
            "malformed stream frame": 1,
            "finish reason not mapped": 1,
            # Text that came from outside.
            "Unauthorized: invalid x-api-key": 2,
            "https://api.example.com/v1/messages?key=abc": 1,
            "invalid key abc123def456ghi789": 1,
        }
    )
    assert halt.causes == {
        "HTTP 502": 5,
        "ReadTimeout": 3,
        "httpx.ConnectError": 2,
        "engine_fallback": 1,
        "qrng/eval_awareness": 4,
        "no finish reason": 2,
        "malformed stream frame": 1,
        "finish reason not mapped": 1,
        OTHER_CAUSE: 4,
    }
    # Reading the stored halt back changes nothing.
    assert Halt.model_validate(halt.model_dump(mode="json")) == halt


def test_no_field_of_a_halt_can_hold_a_key() -> None:
    bag = SecretBag({"anthropic": SECRET, "custom": ODD_SECRET})
    try:
        halt = _halt(
            sentence=f"The judge refused the key {ODD_SECRET}.",
            recovery=f"Supply a key other than {SECRET}.",
            counts={ODD_SECRET: 1, "in_a_row": 8},
            causes={ODD_SECRET: 2, SECRET: 1, f"HTTP 401 {ODD_SECRET}": 1, "HTTP 401": 4},
            sample_refs=[f"qrng/{CARD}/{ODD_SECRET}/0"],
        )
    finally:
        bag.clear()
    stored = halt.model_dump_json()
    assert SECRET not in stored and ODD_SECRET not in stored
    assert halt.causes == {OTHER_CAUSE: 4, "HTTP 401": 4}
    assert halt.counts == {OTHER_CAUSE: 1, "in_a_row": 8}
    assert REDACTED in halt.sentence and REDACTED in halt.recovery


def test_a_key_of_a_known_shape_is_kept_out_even_when_no_run_holds_it() -> None:
    """After a restart no key is registered; the shapes of provider keys still are."""
    halt = _halt(sentence=f"refused {SECRET}", causes={SECRET: 1})
    assert SECRET not in halt.model_dump_json()


def test_a_stop_the_breaker_writes_passes_through_the_record_unchanged() -> None:
    breaker = rc.Breaker(qrng_arms=["qrng"])
    trip = None
    for n in range(rc.GENERATION_ERRORS_STREAK):
        trip = breaker.feed_generation(
            rc.GenerationOutcome(
                arm_id="qrng",
                card_id=CARD,
                prompt_id=f"p{n}",
                sample_ref=_ref("qrng", n),
                finish_reason="error",
                taints=frozenset({"stream_error"}),
                cause="HTTP 502",
            )
        )
    assert trip is not None
    halt = Halt(at=NOW, **trip.halt_fields())
    assert halt.model_dump(mode="json", exclude={"at", "resumed_at"}) == trip.halt_fields()


async def test_a_key_written_into_run_json_by_hand_is_not_served(service: Service) -> None:
    """Whatever wrote the file, the routes hand out the record, and the record holds no key."""
    bag = SecretBag({"custom": ODD_SECRET})
    path = service.rs.run_dir.run_json
    raw = json.loads(path.read_text(encoding="utf-8"))
    halt = _halt().model_dump(mode="json")
    halt["sentence"] = f"The judge refused {ODD_SECRET}."
    halt["causes"] = {f"401 for key {ODD_SECRET}": 1, ODD_SECRET: 2}
    raw["halts"] = [halt]
    path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        async with service.client() as client:
            answers = [
                await client.get("/api/runs/run1"),
                await client.get("/api/runs/run1/health"),
                await client.get(f"/api/studies/{service.study_id}"),
            ]
    finally:
        bag.clear()
    for answer in answers:
        assert answer.status_code == 200, answer.text
        assert ODD_SECRET not in answer.text
    assert answers[0].json()["run"]["halts"][0]["causes"] == {OTHER_CAUSE: 3}


# ── resume after a check stop ─────────────────────────────────────────────────


async def test_resume_after_a_check_stop_starts_a_new_orchestrator_that_sees_the_open_halt(
    service: Service,
) -> None:
    """The run is paused with an open halt and no thread: Resume builds a new orchestrator.

    The orchestrator is the one that stamps the halt; the routes only have to
    hand it the stored record and keep the halts through their own writes.
    """
    service.rs.append_halt(_halt())
    host = {"X-Host-Token": HOST_TOKEN}
    async with service.client() as client:
        refused = await client.post("/api/runs/run1/control", json={"action": "resume"})
        assert refused.status_code == 401 and service.built == []
        resumed = await client.post(
            "/api/runs/run1/control", json={"action": "resume"}, headers=host
        )
        assert resumed.status_code == 200, resumed.text
        live = service.app.state.qr.registry.get("run1")
        live.join(10.0)
        view = (await client.get("/api/runs/run1")).json()
    assert len(service.built) == 1
    assert [halt.resumed_at for halt in service.built[0].open_halts()] == [None]
    assert view["run"]["state"] == "complete"
    stamped = [halt["resumed_at"] for halt in view["run"]["halts"]]
    assert [datetime.fromisoformat(at) for at in stamped] == [LATER]


async def test_supplying_a_key_after_a_judge_stop_keeps_the_halt_and_the_pause(
    service: Service,
) -> None:
    """``judge_rejected`` asks for a key: the key route must not drop the halt or resume."""
    service.rs.append_halt(
        _halt(
            rule="judge_rejected",
            scope="run",
            scope_id=None,
            phase="judge",
            sentence="The judge answered a request with HTTP status 401.",
            recovery=rc.STOP_RULES["judge_rejected"].recovery,
            counts={"status": 401},
            causes={"HTTP 401": 1},
        )
    )
    host = {"X-Host-Token": HOST_TOKEN}
    async with service.client() as client:
        supplied = await client.post(
            "/api/runs/run1/keys", json={"provider_keys": {"anthropic": SECRET}}, headers=host
        )
        assert supplied.status_code == 200, supplied.text
        assert supplied.json()["state"] == "paused" and SECRET not in supplied.text
        view = (await client.get("/api/runs/run1")).json()
    assert service.built == []
    assert view["run"]["keys_present"]["anthropic"] is True
    assert [halt["rule"] for halt in view["run"]["halts"]] == ["judge_rejected"]
    assert view["run"]["halts"][0]["resumed_at"] is None
    assert SECRET not in service.rs.run_dir.run_json.read_text(encoding="utf-8")
