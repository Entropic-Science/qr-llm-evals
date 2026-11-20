"""Card admission, estimates and docs/evals.md drift (spec §5.2 'Card admission',
'UI ↔ docs consistency', 'Estimates parity' backend side).

- every ``evals/*.yaml`` parses and passes the admission validator;
- every fixture under ``fixtures/cards_rejected/`` fails with the message on its
  ``# expect:`` first line;
- the estimate formulas (spec §4.2) on hand-built cards, including told/actual
  cells, overrides and the ``assumptions`` payload;
- ``docs/evals.md`` equals a fresh render of the registry.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from qr_llm_evals.cards import (
    DEFAULT_REGISTRY_DIR,
    REPO_ROOT,
    TOLD_CELLS,
    CardAdmissionError,
    CardLoadError,
    CardOverrides,
    EstimateAssumptions,
    EvalCard,
    admission_problems,
    check_card_file,
    estimate_selection,
    load_card,
    load_cards,
    tokens_per_arm,
    validate_card,
)
from qr_llm_evals.cards.loader import iter_card_paths

FIXTURES = Path(__file__).parent / "fixtures" / "cards_rejected"
DOC_PATH = REPO_ROOT / "docs" / "evals.md"
GEN_SCRIPT = REPO_ROOT / "scripts" / "gen_evals_doc.py"

#: PRD §7.2 — the Study 1 (v1) set. These must exist; the eval steps flip them
#: to ``implemented`` as task modules land.
V1_IDS: frozenset[str] = frozenset(
    {
        "tier0_pit_residuals",
        "tier0_mmd_hamming",
        "tier0_logprob_endpoints",
        "unpredictabench",
        "novelty_bench",
        "creative_diversity",
        "semantic_entropy_sqa",
        "self_referential_probe",
        "sentience_statements",
        "introspection_scales",
        "altered_state_meq",
        "source_guessing",
        "moralchoice_consistency",
        "thurstonian_self_outcomes",
        "opinion_quantum_sampling",
        "stream_deviation_windows",
        "target_hit_ranking",
        "self_reported_coincidence",
    }
)
#: PRD §7.2 — registry cards documented but not implemented in v1.
PLANNED_ONLY_IDS: frozenset[str] = frozenset(
    {
        "psychobench",
        "sage_consistency",
        "eval_awareness",
        "mask",
        "darkbench",
        "xstest",
        "strong_reject",
        "deception_bench",
        "sycophancy_ays",
        "aime2026",
        "gpqa_diamond",
        "livecodebench",
        "ifbench",
    }
)
#: PRD §7.1 primaries must be registry cards.
PRIMARY_CARD_IDS = ("tier0_pit_residuals", "tier0_mmd_hamming", "unpredictabench", "novelty_bench")


def _card(**overrides: Any) -> EvalCard:
    """A minimal admitted thin card; override any field."""
    base: dict[str, Any] = {
        "id": "fixture_card",
        "title": "Fixture",
        "purpose": "Fixture card.",
        "tier": 1,
        "family": "diversity",
        "engine": "thin",
        "task_module": "evals/fixture_card/task.py",
        "dataset": {"source": "hf:example/dataset", "revision": "abc", "items": 100},
        "generation": "free",
        "k": 20,
        "k_bounds": (10, 50),
        "max_tokens": 600,
        "est_tokens_per_sample": 400,
        "est_gen_tokens_per_arm": 800_000,
        "sensitivity": "high",
        "endpoints": {"primary": ["distinct_k"]},
        "paired_stat": "permutation_within_prompt",
        "status": "planned",
        "selectable": False,
    }
    base.update(overrides)
    return EvalCard.model_validate(base)


# ── registry ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def registry() -> dict[str, EvalCard]:
    return load_cards()


def test_registry_loads_every_file(registry: dict[str, EvalCard]) -> None:
    paths = iter_card_paths(DEFAULT_REGISTRY_DIR)
    assert paths, "no cards in evals/"
    assert {p.stem for p in paths} == set(registry)


@pytest.mark.parametrize("path", iter_card_paths(DEFAULT_REGISTRY_DIR), ids=lambda p: p.stem)
def test_registry_card_is_admitted(path: Path) -> None:
    card = load_card(path)
    problems = admission_problems(card)
    assert not problems, f"{card.id}:\n  " + "\n  ".join(problems)


def test_v1_set_and_planned_set_present(registry: dict[str, EvalCard]) -> None:
    missing = (V1_IDS | PLANNED_ONLY_IDS) - set(registry)
    assert not missing, f"registry is missing PRD §7.2 cards: {sorted(missing)}"
    for cid in PLANNED_ONLY_IDS:
        assert registry[cid].status == "planned", cid
        assert registry[cid].selectable is False, cid
    for cid in PRIMARY_CARD_IDS:
        assert registry[cid].tier in (0, 1), cid


def test_planned_cards_are_never_selectable(registry: dict[str, EvalCard]) -> None:
    for card in registry.values():
        if card.status == "planned":
            assert not card.selectable, card.id


def test_tier0_cards_generate_nothing(registry: dict[str, EvalCard]) -> None:
    for card in registry.values():
        if card.tier == 0:
            assert card.k is None and card.est_gen_tokens_per_arm == 0, card.id
            assert card.endpoints.requires_tier0 != "none", card.id


def test_min_p_exception_is_declared_on_thurstonian(registry: dict[str, EvalCard]) -> None:
    assert any("min_p 0" in e for e in registry["thurstonian_self_outcomes"].exceptions)


def test_told_actual_cards_multiply_by_told_cells(registry: dict[str, EvalCard]) -> None:
    told = [c for c in registry.values() if c.told_actual]
    assert {c.id for c in told} == {"self_referential_probe", "opinion_quantum_sampling"}
    for c in told:
        assert (
            c.est_gen_tokens_per_arm
            == c.dataset.items * (c.k or 1) * c.est_tokens_per_sample * TOLD_CELLS
        )


# ── rejected fixtures ───────────────────────────────────────────────────────


def _expected(path: Path) -> str:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# expect: "), (
        f"{path.name}: first line must be '# expect: <substring>'"
    )
    return first.removeprefix("# expect: ").strip()


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.yaml")), ids=lambda p: p.stem)
def test_rejected_fixture_fails_with_expected_message(path: Path) -> None:
    expected = _expected(path)
    problems = check_card_file(path)
    assert problems, f"{path.name} was admitted but should be rejected"
    assert any(expected in p for p in problems), (
        f"{path.name}: expected a problem containing {expected!r}, got:\n  " + "\n  ".join(problems)
    )


def test_rejected_fixture_set_covers_spec_list() -> None:
    names = {p.stem for p in FIXTURES.glob("*.yaml")}
    assert {"forced_choice", "k_5", "judge_t07", "missing_revision", "wrong_arithmetic"} <= names


def test_validate_card_raises_with_all_problems() -> None:
    card = _card(
        k=5, est_gen_tokens_per_arm=1, judge={"kind": "llm", "model": "x", "temperature": 0.5}
    )
    with pytest.raises(CardAdmissionError) as err:
        validate_card(card)
    msgs = err.value.problems
    assert any(m.startswith("k:") for m in msgs)
    assert any(m.startswith("est_gen_tokens_per_arm:") for m in msgs)
    assert any(m.startswith("judge.temperature:") for m in msgs)
    assert any(m.startswith("judge.different_from_target:") for m in msgs)


def test_implemented_card_needs_existing_task_module(tmp_path: Path) -> None:
    card = _card(status="implemented", selectable=True)
    problems = admission_problems(card, repo_root=tmp_path)
    assert any("does not exist but status is implemented" in p for p in problems)
    (tmp_path / "evals" / "fixture_card").mkdir(parents=True)
    (tmp_path / "evals" / "fixture_card" / "task.py").write_text("", encoding="utf-8")
    assert admission_problems(card, repo_root=tmp_path) == []


def test_loader_rejects_duplicate_and_mismatched_ids(tmp_path: Path) -> None:
    src = (DEFAULT_REGISTRY_DIR / "novelty_bench.yaml").read_text(encoding="utf-8")
    (tmp_path / "novelty_bench.yaml").write_text(src, encoding="utf-8")
    (tmp_path / "other.yaml").write_text(src, encoding="utf-8")
    with pytest.raises(CardLoadError, match="file name 'other' must equal id"):
        load_cards(tmp_path)


# ── estimates (spec §4.2) ───────────────────────────────────────────────────


def test_tokens_per_arm_formula() -> None:
    assert tokens_per_arm(_card()) == 100 * 20 * 400
    told = _card(tier=4, family="self_report", told_actual=True, est_gen_tokens_per_arm=2_400_000)
    assert tokens_per_arm(told) == 100 * 20 * 400 * TOLD_CELLS
    derived = _card(
        dataset={"source": "derived:tier0_log", "items": 0},
        k=None,
        k_bounds=None,
        max_tokens=0,
        est_tokens_per_sample=0,
        est_gen_tokens_per_arm=0,
        endpoints={"primary": ["x"], "requires_tier0": "engine_log"},
    )
    assert tokens_per_arm(derived) == 0


def test_tokens_per_arm_overrides_respect_bounds() -> None:
    card = _card()
    assert tokens_per_arm(card, CardOverrides(k=10)) == 100 * 10 * 400
    assert tokens_per_arm(card, CardOverrides(items=50)) == 50 * 20 * 400
    with pytest.raises(ValueError, match="outside k_bounds"):
        tokens_per_arm(card, CardOverrides(k=5))
    with pytest.raises(ValueError, match="outside"):
        tokens_per_arm(card, CardOverrides(items=101))


def test_estimate_selection_numbers_and_assumptions() -> None:
    a = _card(id="a")  # 800k
    b = _card(id="b", k=100, est_tokens_per_sample=50, est_gen_tokens_per_arm=500_000)  # 500k
    est = estimate_selection(
        [a, b],
        "qr-llm",
        EstimateAssumptions(
            tps_by_model={"qr-llm": 400.0}, reading_tps=45, tiled_streams=4, session_min=30
        ),
    )
    total = 1_300_000
    assert est.tokens_per_arm == total
    assert math.isclose(est.time_unsupervised_h, total / 400 / 3600)
    assert math.isclose(est.observer_hours_single, total / 45 / 3600)
    # tiled: min(4 × 45, 400) = 180 tok/s
    assert math.isclose(est.observer_hours_tiled, total / 180 / 3600)
    assert est.sessions_single == math.ceil(est.observer_hours_single * 60 / 30)
    assert est.sessions_tiled == math.ceil(est.observer_hours_tiled * 60 / 30)
    assert [c.tokens_per_arm for c in est.per_card] == [800_000, 500_000]
    # FR-8: every estimate carries its assumptions
    assert est.assumptions["tps_model"] == 400.0
    assert est.assumptions["reading_tps"] == 45
    assert est.assumptions["tiled_effective_tps"] == 180
    assert est.assumptions["told_cells"] == TOLD_CELLS
    assert "tokens_per_arm" in est.assumptions["formula"]
    assert est.assumptions["caveats"]


def test_estimate_tiled_is_capped_by_model_throughput() -> None:
    est = estimate_selection(
        [_card()],
        "slow",
        EstimateAssumptions(tps_by_model={"slow": 100.0}, reading_tps=45, tiled_streams=4),
    )
    assert est.assumptions["tiled_effective_tps"] == 100.0
    assert math.isclose(est.observer_hours_tiled, est.time_unsupervised_h)


def test_estimate_refuses_unknown_model() -> None:
    with pytest.raises(ValueError, match="no throughput assumption"):
        estimate_selection([_card()], "mystery", EstimateAssumptions(tps_by_model={}))


def test_study1_wall_clock_matches_prd_order_of_magnitude(registry: dict[str, EvalCard]) -> None:
    """PRD §7.3: ≈ 5M tokens/arm, ≈ 30 observer-hours single-stream, ≈ 8 h tiled at 4 streams."""
    est = estimate_selection([registry[i] for i in sorted(V1_IDS)], "qr-llm")
    assert 4_500_000 <= est.tokens_per_arm <= 5_500_000
    assert 25 <= est.observer_hours_single <= 35
    assert 6 <= est.observer_hours_tiled <= 10


# ── docs/evals.md drift ─────────────────────────────────────────────────────


def _gen_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_evals_doc", GEN_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evals_doc_is_up_to_date(registry: dict[str, EvalCard]) -> None:
    gen = _gen_module()
    rendered = gen.render(registry).encode("utf-8")
    assert DOC_PATH.exists(), "docs/evals.md missing; run python scripts/gen_evals_doc.py"
    committed = DOC_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert committed == rendered, "docs/evals.md is stale; run python scripts/gen_evals_doc.py"


def test_evals_doc_lists_every_card_with_status(registry: dict[str, EvalCard]) -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for card in registry.values():
        assert f"`{card.id}`" in text, card.id
        assert card.status in text
    assert "GENERATED FILE" in text.splitlines()[0]


def test_gen_evals_doc_check_cli_passes() -> None:
    gen = _gen_module()
    assert gen.main(["--check"]) == 0


def test_k_below_10_needs_a_derived_card_and_a_declared_exception() -> None:
    low_k: dict[str, Any] = {"k": 1, "k_bounds": [1, 4]}
    derived: dict[str, Any] = {"source": "derived:creative_diversity", "items": 10}
    declared = ["k below 10: the reply is the unit"]

    def k_problems(**overrides: Any) -> list[str]:
        return [p for p in admission_problems(_card(**overrides)) if p.startswith("k")]

    assert not k_problems(dataset=derived, exceptions=declared, **low_k)
    assert k_problems(dataset=derived, **low_k)  # not declared
    assert k_problems(exceptions=declared, **low_k)  # not a derived card
