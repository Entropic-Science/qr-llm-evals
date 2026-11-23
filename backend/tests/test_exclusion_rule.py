"""The exclusion rule of the clean analysis, as both loads apply it.

A ``stream_error`` taint is written for an attempt that ended in an error. When
the run is resumed the sample is generated again, and the taint of the failed
attempt must not keep the completed reply out. Every other taint, and an error
that was never tried again, excludes as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qr_llm_evals.analysis.endpoints import (
    AnalysisData,
    exclude_tainted,
    excluded_refs,
    load_data,
)

CARD = "self_referential_probe"
QUANTUM = "qrng"
SEEDED = "prng_seed_7"
PROMPTS = ("p0", "p1", "p2")
EPOCHS = 2
TOKENS = 3


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _ref(arm: str, prompt: str, epoch: int) -> str:
    return f"{arm}/{CARD}/{prompt}/{epoch}"


def _tokens(ref: str) -> list[dict[str, Any]]:
    return [
        {
            "sample_ref": ref,
            "token_index": index,
            "token_id": index,
            "logprob": -0.7,
            "rank_raw": 0,
            "entropy_raw_top20": 0.6,
            "u": 0.1,
            "cdf_lo": 0.0,
            "cdf_hi": 0.2,
            "timestamp_ns": 1,
        }
        for index in range(TOKENS)
    ]


def _run(
    root: Path,
    *,
    failed_first: tuple[str, ...] = (),
    never_completed: tuple[str, ...] = (),
    taints: list[dict[str, Any]],
) -> Path:
    """A run of two arms. ``failed_first``: refs with an error row before the completed row.

    ``never_completed``: refs whose only row is the error. They still get a score
    row and token records here, which a real run never writes, so that a test can
    see them being dropped.
    """
    run_dir = root / "runs" / "full"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"tier0_source": "engine_log"}), encoding="utf-8")
    for arm in (QUANTUM, SEEDED):
        card = run_dir / "arms" / arm / "cards" / CARD
        generations: list[dict[str, Any]] = []
        scores: list[dict[str, Any]] = []
        judge: list[dict[str, Any]] = []
        tokens: list[dict[str, Any]] = []
        for prompt in PROMPTS:
            for epoch in range(EPOCHS):
                ref = _ref(arm, prompt, epoch)
                if ref in failed_first or ref in never_completed:
                    generations.append({"sample_ref": ref, "finish_reason": "error"})
                if ref not in never_completed:
                    generations.append({"sample_ref": ref, "finish_reason": "stop"})
                scores.append({"sample_ref": ref, "prompt_id": prompt})
                judge.append({"sample_ref": ref, "score": 1.0})
                tokens.extend(_tokens(ref))
        _jsonl(card / "generations.jsonl", generations)
        _jsonl(card / "scores" / "task.jsonl", scores)
        _jsonl(card / "judge" / "study_judge.jsonl", judge)
        _jsonl(card / "tier0.jsonl", tokens)
    _jsonl(run_dir / "taints.jsonl", taints)
    return run_dir


def _refs(data: AnalysisData, arm: str) -> set[str]:
    return {row["sample_ref"] for row in data.scores[CARD][arm]}


def _token_refs(data: AnalysisData, arm: str) -> list[str]:
    return [record.sample_ref for record in data.tier0[CARD].get(arm, [])]


def _both_loads(run_dir: Path) -> tuple[AnalysisData, AnalysisData]:
    """The clean load, and the clean view of the load that leaves nothing out."""
    direct = load_data(run_dir)
    view = exclude_tainted(load_data(run_dir, include_tainted=True))
    assert view.scores == direct.scores
    for arm in (QUANTUM, SEEDED):
        assert _token_refs(view, arm) == _token_refs(direct, arm)
    return direct, view


def test_a_retried_error_is_analysed_and_both_loads_agree(tmp_path: Path) -> None:
    """The error was tried again and the sample completed: its reply is a clean one."""
    retried = _ref(QUANTUM, "p1", 0)
    run_dir = _run(
        tmp_path,
        failed_first=(retried,),
        taints=[{"sample_ref": retried, "kind": "stream_error"}],
    )
    direct, _ = _both_loads(run_dir)
    assert retried in _refs(direct, QUANTUM)
    assert len(_refs(direct, QUANTUM)) == len(PROMPTS) * EPOCHS
    assert _token_refs(direct, QUANTUM).count(retried) == TOKENS
    assert direct.completed >= {retried}


def test_an_error_that_was_never_retried_is_excluded_as_before(tmp_path: Path) -> None:
    lost = _ref(QUANTUM, "p1", 0)
    run_dir = _run(
        tmp_path,
        never_completed=(lost,),
        taints=[{"sample_ref": lost, "kind": "stream_error"}],
    )
    direct, _ = _both_loads(run_dir)
    assert lost not in _refs(direct, QUANTUM)
    assert len(_refs(direct, QUANTUM)) == len(PROMPTS) * EPOCHS - 1
    assert lost not in _token_refs(direct, QUANTUM)
    assert lost not in direct.completed
    # The seeded arm is untouched.
    assert len(_refs(direct, SEEDED)) == len(PROMPTS) * EPOCHS


@pytest.mark.parametrize("kind", ["engine_fallback", "qrng_degraded", "stateless_row"])
def test_a_retried_sample_with_another_taint_stays_out(tmp_path: Path, kind: str) -> None:
    """The retry completed, and the completed attempt was tainted for something else."""
    retried = _ref(QUANTUM, "p2", 1)
    run_dir = _run(
        tmp_path,
        failed_first=(retried,),
        taints=[
            {"sample_ref": retried, "kind": "stream_error"},
            {"sample_ref": retried, "kind": kind},
        ],
    )
    direct, _ = _both_loads(run_dir)
    assert retried not in _refs(direct, QUANTUM)
    assert retried not in _token_refs(direct, QUANTUM)


@pytest.mark.parametrize("kind", ["engine_fallback", "qrng_degraded", "stateless_row"])
def test_no_other_taint_is_lifted_by_a_completed_row(tmp_path: Path, kind: str) -> None:
    tainted = _ref(QUANTUM, "p0", 1)
    run_dir = _run(tmp_path, taints=[{"sample_ref": tainted, "kind": kind}])
    direct, _ = _both_loads(run_dir)
    assert tainted in direct.completed
    assert tainted not in _refs(direct, QUANTUM)


def test_the_load_that_leaves_nothing_out_keeps_every_completed_sample(tmp_path: Path) -> None:
    retried, fallback = _ref(QUANTUM, "p1", 0), _ref(QUANTUM, "p0", 0)
    run_dir = _run(
        tmp_path,
        failed_first=(retried,),
        taints=[
            {"sample_ref": retried, "kind": "stream_error"},
            {"sample_ref": fallback, "kind": "engine_fallback"},
        ],
    )
    everything = load_data(run_dir, include_tainted=True)
    assert len(_refs(everything, QUANTUM)) == len(PROMPTS) * EPOCHS
    assert _refs(everything, QUANTUM) - _refs(exclude_tainted(everything), QUANTUM) == {fallback}


def test_the_rule_reads_the_kind_and_the_completed_rows(tmp_path: Path) -> None:
    run_dir = tmp_path
    _jsonl(
        run_dir / "taints.jsonl",
        [
            {"sample_ref": "a/c/p/0", "kind": "stream_error"},
            {"sample_ref": "a/c/p/1", "kind": "stream_error"},
            {"sample_ref": "a/c/p/2", "kind": "engine_fallback"},
            {"sample_ref": "a/c/p/3"},  # a row without a kind excludes
            {"window_id": "w1", "kind": "qrng_degraded"},  # names no sample
        ],
    )
    completed = {"a/c/p/0", "a/c/p/2", "a/c/p/3"}
    assert excluded_refs(run_dir, completed) == {"a/c/p/1", "a/c/p/2", "a/c/p/3"}
    # Without the completed rows every tainted sample is left out, as before the rule.
    assert excluded_refs(run_dir, frozenset()) == {"a/c/p/0", "a/c/p/1", "a/c/p/2", "a/c/p/3"}
    assert excluded_refs(tmp_path / "no_such_run", completed) == set()


def test_data_built_without_a_load_excludes_every_tainted_sample(tmp_path: Path) -> None:
    """``AnalysisData`` made by hand knows no completed rows, so nothing is lifted."""
    ref = "a/c/p/0"
    _jsonl(tmp_path / "taints.jsonl", [{"sample_ref": ref, "kind": "stream_error"}])
    data = AnalysisData(
        run_dir=tmp_path,
        tier0_source=None,
        scores={"c": {"a": [{"sample_ref": ref}, {"sample_ref": "a/c/p/1"}]}},
        tier0={},
        questionnaires=[],
    )
    assert exclude_tainted(data).scores == {"c": {"a": [{"sample_ref": "a/c/p/1"}]}}
