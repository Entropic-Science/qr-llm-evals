"""Tier-4 derived source-guessing and observer-card contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from qr_llm_evals.cards import load_cards, validate_registry
from qr_llm_evals.runner.tasks import load_task_module


def _module(card_id: str) -> Any:
    return load_task_module(load_cards()[card_id]).module


class StubDerivedStore:
    def __init__(self, root: Path) -> None:
        self.run_id = "run-observer"
        self.run_dir = SimpleNamespace(
            path=root, arms_dir=root / "arms", sessions_dir=root / "sessions"
        )
        self._rows: dict[tuple[str, str], list[Any]] = {}

    def generations(self, arm_id: str, card_id: str) -> list[Any]:
        return self._rows.get((arm_id, card_id), [])


def test_observer_cards_are_implemented() -> None:
    cards = validate_registry()
    for card_id in ("source_guessing", "target_hit_ranking", "self_reported_coincidence"):
        assert cards[card_id].status == "implemented" and cards[card_id].selectable


def test_source_guessing_builds_three_variants_from_stub_store(tmp_path: Path) -> None:
    store = StubDerivedStore(tmp_path)
    store._rows[("q", "creative_diversity")] = [
        SimpleNamespace(
            sample_ref="q/creative/p/0",
            output="A blue orchard.",
            finish_reason="stop",
            provenance=SimpleNamespace(source_label="quantum"),
        )
    ]
    store._rows[("p", "creative_diversity")] = [
        SimpleNamespace(
            sample_ref="p/creative/p/0",
            output="A red harbour.",
            finish_reason="stop",
            provenance=SimpleNamespace(source_label="seeded_prng"),
        )
    ]
    task = _module("source_guessing")
    items = task.build_items(store, ["q", "p"])
    assert len(items) == 6
    assert {item["variant"] for item in items} == set(task.VARIANTS)
    assert {item["source"] for item in items} == {"qrng", "prng"}
    assert all(
        "source" not in task.build_messages(item)[-1]["content"].casefold().split("completion")[0]
        for item in items
    )
    endpoint = task.endpoints(
        [
            {"guess": "qrng", "source": "qrng", "variant": "self_judgement"},
            {"guess": "prng", "source": "prng", "variant": "cross_judgement"},
        ]
    )
    assert endpoint["guess_accuracy"] == 1.0


def test_target_judge_order_is_randomised_and_seed_recorded(tmp_path: Path) -> None:
    store = StubDerivedStore(tmp_path)
    store.run_dir.sessions_dir.mkdir(parents=True)
    session = {
        "session_id": "s1",
        "target": {
            "theme": "blue whale",
            "decoys": ["red kite", "oak tree", "glass bridge", "silver bell"],
            "draw_source": "secrets",
        },
    }
    (store.run_dir.sessions_dir / "s1.json").write_text(json.dumps(session), encoding="utf-8")
    store._rows[("q", "creative_diversity")] = [
        SimpleNamespace(output="The sea carried a vast song.", session_id="s1")
    ]
    task = _module("target_hit_ranking")
    first = task.build_items(store, "q")[0]
    second = task.build_items(store, "q")[0]
    assert first["blind_seed"] == second["blind_seed"]
    assert first["candidates"] == second["candidates"]
    prompt = task.judge_prompt({"item": first})
    assert first["target_label"] not in prompt.split("<candidates>")[0]
    assert "target" not in prompt.casefold()
    assert task.parse_ranking("RANKING: A>C>B>E>D") == ["A", "C", "B", "E", "D"]


def test_fresh_context_coincidence_prompt_and_observer_wcs() -> None:
    task = _module("self_reported_coincidence")
    messages = task.build_messages({"transcript": "Earlier output", "prompt_id": "x"})
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert task.parse_claim("CLAIM: yes\nA specific connection.") == 1
    assert task.observer_wcs_score({"answers": {"wcs_1": 3, "wcs_2": 5, "state": 1}}) == 4.0
