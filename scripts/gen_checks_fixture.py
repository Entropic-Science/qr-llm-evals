"""Writes apps/web/src/components/run/checks-fixture.json from the backend's own code.

Every halt is what ``cards.result_checks.Breaker`` returns when the rule fires,
stored as a ``store.schema.Halt``. Every summary is what
``store.health.summarise`` reads back from a run folder written with the store's
own writers. Nothing in the file is typed by hand, so the web tests run against
sentences and counts the harness really produces.

Run from the repo root with the venv python:

    python scripts/gen_checks_fixture.py [out.json]
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.store import Store, summarise
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
JUDGED = "eval_awareness"
PLAIN = "psychobench"
ARMS = ("qrng", "prng7", "bent")


def ref(arm: str, n: int, card: str = JUDGED) -> str:
    return f"{arm}/{card}/p{n}/0"


# ── halts: one for every rule and clause, from the Breaker ────────────────────


def breaker() -> rc.Breaker:
    return rc.Breaker(qrng_arms=["qrng"], judged_cards=[JUDGED], halts=[])


def gen(
    arm: str, n: int, card: str = PLAIN, finish: str = "stop", **kw: Any
) -> rc.GenerationOutcome:
    return rc.GenerationOutcome(
        arm_id=arm,
        card_id=card,
        prompt_id=f"p{n % 5}",
        sample_ref=f"{arm}/{card}/p{n % 5}/{n // 5}",
        finish_reason=finish,
        taints=frozenset(kw.pop("taints", ())),
        flags=frozenset(kw.pop("flags", ())),
        **kw,
    )


def judged(arm: str, n: int, status: str, **kw: Any) -> rc.JudgeOutcome:
    return rc.JudgeOutcome(
        arm_id=arm,
        card_id=JUDGED,
        sample_ref=ref(arm, n),
        status=status,
        **kw,  # type: ignore[arg-type]
    )


def first_trip(steps: Any) -> rc.Trip:
    for trip in steps:
        if trip is not None:
            return trip
    raise AssertionError("the rule did not fire")


def trips() -> dict[str, rc.Trip]:
    out: dict[str, rc.Trip] = {}

    b = breaker()
    causes = ["HTTP 502"] * 6 + ["ReadTimeout"] * 2
    out["generation_errors/arm"] = first_trip(
        b.feed_generation(gen("qrng", n, finish="error", taints=["stream_error"], cause=causes[n]))
        for n in range(8)
    )

    b = breaker()

    def mixed() -> Any:
        n = 0
        while True:
            for arm in ("qrng", "prng7"):
                failed = n % 4 != 3
                yield b.feed_generation(
                    gen(
                        arm,
                        n,
                        finish="error" if failed else "stop",
                        taints=["stream_error"] if failed else [],
                        cause="HTTP 502" if failed else None,
                    )
                )
            n += 1

    out["generation_errors/run"] = first_trip(mixed())

    b = breaker()
    out["entropy_degraded/tainted"] = first_trip(
        b.feed_generation(gen("qrng", n, taints=["engine_fallback" if n % 2 else "qrng_degraded"]))
        for n in range(10)
    )
    b = breaker()
    out["entropy_degraded/probe"] = first_trip(
        b.feed_generation(gen("qrng", n, probe_before_bad=True)) for n in range(3)
    )
    b = breaker()
    out["tier0_failed/arm"] = first_trip(
        b.feed_generation(
            gen("prng7", n, taints=["stateless_row"])
            if n < 4
            else gen("prng7", n, flags=["tier0_missing"])
        )
        for n in range(10)
    )
    b = breaker()
    out["scorer_errors/card"] = first_trip(
        b.feed_generation(gen("qrng", n, flags=["score_error"], cause="KeyError"))
        for n in range(10)
    )
    b = breaker()
    out["empty_outputs/card"] = first_trip(
        b.feed_generation(gen(ARMS[n % 3], n, card=JUDGED, flags=["empty_output"]))
        for n in range(20)
    )
    b = breaker()
    out["nothing_parsed/first"] = first_trip(
        b.feed_generation(gen(ARMS[n % 3], n, flags=["unparsed"])) for n in range(30)
    )
    b = breaker()
    for n in range(12):
        assert b.feed_generation(gen(ARMS[n % 3], n, flags=["unparsed"])) is None
    out["nothing_parsed/queue_end"] = first_trip([b.end_of_card(PLAIN)])

    b = breaker()
    out["unfinished/generation"] = first_trip(
        [
            b.end_of_phase(
                "generation",
                [
                    rc.PhaseCell("qrng", JUDGED, 40, 31),
                    rc.PhaseCell("prng7", JUDGED, 40, 40),
                    rc.PhaseCell("bent", PLAIN, 40, 0),
                ],
                sample_refs=[ref("qrng", 31), ref("qrng", 32), ref("bent", 0, PLAIN)],
            )
        ]
    )
    b = breaker()
    out["unfinished/judge"] = first_trip(
        [
            b.end_of_phase(
                "judge",
                [rc.PhaseCell("qrng", JUDGED, 40, 28), rc.PhaseCell("prng7", JUDGED, 40, 40)],
                sample_refs=[ref("qrng", 3), ref("qrng", 9)],
            )
        ]
    )
    b = breaker()
    out["judge_rejected/preflight"] = first_trip([b.judge_preflight(400)])
    b = breaker()
    out["judge_rejected/judge"] = first_trip(
        [b.feed_judgement(judged("qrng", 0, "failed", http_status=401, cause="HTTP 401"))]
    )
    b = breaker()
    out["judge_unusable/streak"] = first_trip(
        b.feed_judgement(
            judged(
                "qrng", n, "failed", http_status=529, cause="HTTP 529" if n < 7 else "ReadTimeout"
            )
        )
        for n in range(10)
    )
    b = breaker()
    out["judge_unusable/share"] = first_trip(
        b.feed_judgement(
            judged("qrng", n, "ok")
            if n % 3 == 2
            else judged("qrng", n, "failed", http_status=500, cause="HTTP 500")
        )
        for n in range(40)
    )
    b = breaker()
    out["nothing_judged/card"] = first_trip(
        b.feed_judgement(judged(ARMS[n % 3], n, "unreadable")) for n in range(30)
    )
    b = breaker()
    out["judge_phase_error/run"] = first_trip([b.judge_phase_error("KeyError")])

    missing = set(rc.STOP_RULES) - {key.split("/")[0] for key in out}
    assert not missing, missing
    return out


def halt_of(trip: rc.Trip, at: datetime, resumed_at: datetime | None = None) -> Halt:
    return Halt(at=at, resumed_at=resumed_at, **trip.halt_fields())


# ── summaries: run folders written with the store, read back by summarise() ───


def study() -> StudyConfig:
    model = {"id": "qr-llm", "family": "qr_llm", "provider": "qr_server", "served_name": "qr-llm"}
    return StudyConfig.model_validate(
        {
            "title": "Checks fixture",
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
            "cards": [{"card_id": JUDGED}, {"card_id": PLAIN}],
            "judge": {"model": "claude-sonnet-4"},
            "narrative_model": model,
            "expectations": "None.",
        }
    )


def new_run(store: Store, run_id: str, **fields: Any) -> RunStore:
    cfg = study()
    study_id = store.freeze_study(cfg, "# Prereg\n").study_id
    run = Run.model_validate(
        {
            "run_id": run_id,
            "study_id": study_id,
            "kind": "full",
            "created_at": NOW,
            "key_name": "k",
            "host_token_hash": H,
            "config_hash": config_hash(cfg),
            **fields,
        }
    )
    return store.create_run(run)


def generate(rs: RunStore, arm: str, n: int, finish: str = "stop", card: str = JUDGED) -> str:
    sample = ref(arm, n, card)
    quantum = arm == "qrng"
    rs.append_generation(
        GenerationRow(
            sample_ref=sample,
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
            request_id=sample,
        )
    )
    return sample


def flag(rs: RunStore, sample: str, kind: str) -> None:
    arm, card = sample.split("/")[:2]
    rs.append_flag(
        ResultFlag(
            sample_ref=sample,
            kind=kind,  # type: ignore[arg-type]
            phase=rc.FLAG_PHASE[kind],
            card_id=card,
            arm_id=arm,
            at=NOW,
        )
    )


def judge(rs: RunStore, sample: str, **fields: Any) -> None:
    arm, card = sample.split("/")[:2]
    rs.append_judge(
        arm,
        card,
        JudgeRow.model_validate(
            {
                "sample_ref": sample,
                "judge_id": "study_judge",
                "model": "m",
                "rubric_hash": H,
                "at": NOW,
                **fields,
            }
        ),
    )


def progress(total: int) -> Progress:
    return Progress(
        by_arm={
            arm: ArmProgress(
                by_card={card: CardProgress(done=0, total=total) for card in (JUDGED, PLAIN)}
            )
            for arm in ARMS
        }
    )


def summaries(all_trips: dict[str, rc.Trip]) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="rc-web-fixture-"))
    out: dict[str, Any] = {}
    try:
        # A finished run that was stopped twice and resumed twice.
        store = Store(root / "complete")
        rs = new_run(
            store,
            "run-checked",
            state="complete",
            checks_version=rc.CHECKS_VERSION,
            progress=progress(40),
            halts=[
                halt_of(
                    all_trips["generation_errors/arm"],
                    NOW + timedelta(minutes=5),
                    NOW + timedelta(minutes=20),
                ),
                halt_of(
                    all_trips["unfinished/generation"],
                    NOW + timedelta(hours=2),
                    NOW + timedelta(hours=3),
                ),
            ],
        )
        for arm in ARMS:
            for card in (JUDGED, PLAIN):
                for n in range(40):
                    if arm == "qrng" and card == JUDGED and n >= 38:
                        bad = generate(rs, arm, n, "error", card)
                        rs.append_taint(Taint(sample_ref=bad, kind="stream_error", at=NOW))
                    else:
                        generate(rs, arm, n, "stop", card)
        rs.append_taint(Taint(sample_ref=ref("qrng", 0), kind="engine_fallback", at=NOW))
        rs.append_taint(Taint(sample_ref=ref("prng7", 4, PLAIN), kind="stateless_row", at=NOW))
        for n in range(30):
            flag(rs, ref("qrng", n), "truncated")
        for n in range(31):
            flag(rs, ref("prng7", n), "truncated")
        for arm in ARMS:
            for n in range(40):
                flag(rs, ref(arm, n, PLAIN), "truncated")
        for n in range(3):
            flag(rs, ref("qrng", n), "repetition")
        flag(rs, ref("prng7", 0), "repetition")
        flag(rs, ref("qrng", 7), "empty_output")
        flag(rs, ref("bent", 2, PLAIN), "unparsed")
        for n in range(34):
            judge(rs, ref("qrng", n), score=1.0, status="ok", reader_version=1)
        for n in (34, 35):
            judge(rs, ref("qrng", n), status="unreadable", reader_version=1)
            flag(rs, ref("qrng", n), "judge_unreadable")
        judge(rs, ref("qrng", 36), status="out_of_range", reader_version=1)
        flag(rs, ref("qrng", 36), "judge_out_of_range")
        judge(rs, ref("qrng", 37), status="failed")
        flag(rs, ref("qrng", 37), "judge_failed")
        for arm in ("prng7", "bent"):
            for n in range(40):
                judge(rs, ref(arm, n), score=0.0, status="ok", reader_version=1)
        out["complete"] = summarise(rs).model_dump(mode="json")
        store.close()

        # A run a check has stopped and nobody has resumed yet.
        store = Store(root / "stopped")
        rs = new_run(
            store,
            "run-stopped",
            state="paused",
            checks_version=rc.CHECKS_VERSION,
            progress=progress(40),
            halts=[
                halt_of(
                    all_trips["generation_errors/arm"],
                    NOW + timedelta(minutes=5),
                    NOW + timedelta(minutes=20),
                ),
                halt_of(all_trips["empty_outputs/card"], NOW + timedelta(minutes=50)),
            ],
        )
        for arm in ARMS:
            for n in range(12):
                sample = generate(rs, arm, n)
                if n >= 5:
                    flag(rs, sample, "empty_output")
        out["stopped"] = summarise(rs).model_dump(mode="json")
        store.close()

        # A run from before the checks: no flag counts, judge rows without a status.
        store = Store(root / "legacy")
        rs = new_run(store, "run-legacy", state="complete", progress=progress(4))
        for arm in ARMS:
            for n in range(4):
                generate(rs, arm, n)
        judge(rs, ref("qrng", 0), score=1.0)
        judge(rs, ref("qrng", 1))
        rs.append_taint(Taint(sample_ref=ref("qrng", 2), kind="qrng_degraded", at=NOW))
        flag(rs, ref("qrng", 0), "repetition")  # not read: the run did not start under the checks
        out["legacy"] = summarise(rs).model_dump(mode="json")
        store.close()

        # A run that has stored nothing yet.
        store = Store(root / "fresh")
        rs = new_run(store, "run-fresh", state="ready", checks_version=rc.CHECKS_VERSION)
        out["fresh"] = summarise(rs).model_dump(mode="json")
        store.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return out


def main() -> None:
    target = Path(
        sys.argv[1] if len(sys.argv) > 1 else "apps/web/src/components/run/checks-fixture.json"
    )
    all_trips = trips()
    fixture = {
        "_written_by": "scripts/gen_checks_fixture.py; do not edit by hand",
        "checks_version": rc.CHECKS_VERSION,
        "rules": list(rc.STOP_RULES),
        "halts": {
            key: halt_of(trip, NOW + timedelta(minutes=5)).model_dump(mode="json")
            for key, trip in all_trips.items()
        },
        "summaries": summaries(all_trips),
    }
    target.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(fixture['halts'])} halts, {len(fixture['summaries'])} summaries)")


if __name__ == "__main__":
    main()
