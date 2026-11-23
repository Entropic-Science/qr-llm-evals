"""psychobench: the battery's shape, one statement per prompt, keyed ratings, stability.

The statements here are synthetic: upstream's item texts are not licensed for
this repository and are read only at run time.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.runner.tasks import item_problems, load_task_module


@pytest.fixture(scope="module")
def task() -> Any:
    return load_task_module(load_cards()["psychobench"]).module


def _entry(name: str, n: int, low: int = 1, high: int = 5) -> dict[str, Any]:
    half = n // 2
    return {
        "name": name,
        "questions": {str(i): f"{name} statement {i}." for i in range(1, n + 1)},
        "inner_setting": (
            f"You are a helpful assistant who can only reply numbers from {low} to {high} "
            'in every statement. Format: "statement index: score".'
        ),
        "prompt": f"You can only reply numbers from {low} to {high}. Score them one by one:",
        "scale": low + high,
        "reverse": [2],
        "categories": [
            {"cat_name": "First", "cat_questions": list(range(1, half + 1))},
            {"cat_name": "Second", "cat_questions": list(range(half + 1, n))},
        ],
    }


def _file(task: Any) -> list[dict[str, Any]]:
    entries = [_entry(name, n) for name, n in task.INSTRUMENTS.items()]
    # The excluded instruments are in upstream's file and must be ignored.
    return [*entries, _entry("EPQ-R", 100, 0, 1), _entry("BSRI", 60, 1, 7), _entry("16P", 60, 1, 7)]


@pytest.fixture
def items(task: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    raw = json.dumps(_file(task)).encode("utf-8")
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(raw).hexdigest())
    parsed: list[dict[str, Any]] = task.parse_dataset(raw)
    return parsed


def test_card_is_implemented_and_pinned() -> None:
    card = load_cards()["psychobench"]
    assert card.status == "implemented" and card.selectable
    module = load_task_module(card).module
    assert card.dataset.revision == module.DATASET_REVISION
    assert module.DATASET_REVISION in module.DATASET_URL
    assert card.dataset.items == module.ITEM_COUNT == sum(module.INSTRUMENTS.values()) == 352


def test_dataset_with_other_bytes_is_refused(task: Any) -> None:
    with pytest.raises(ValueError, match="sha256"):
        task.parse_dataset(json.dumps(_file(task)).encode("utf-8"))


def test_battery_shape(task: Any, items: list[dict[str, Any]]) -> None:
    assert len(items) == 352 and not item_problems(items)
    assert [item["instrument"] for item in items[:11]] == ["Empathy"] * 10 + ["BFI"]
    assert {item["instrument"] for item in items} == set(task.INSTRUMENTS)
    first, second, last = items[0], items[1], items[9]
    assert first["prompt_id"] == "pb_empathy_001" and first["subscale"] == "First"
    assert (first["reverse"], second["reverse"]) == (False, True)
    assert (first["low"], first["high"]) == (1, 5)
    # A statement outside every subscale is a filler: administered, not scored into one.
    assert last["subscale"] is None
    assert next(i for i in items if i["instrument"] == "ECR-R")["prompt_id"] == "pb_ecr_r_001"


def test_inconsistent_scale_is_refused(task: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _file(task)
    entries[0]["scale"] = 9
    raw = json.dumps(entries).encode("utf-8")
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="Empathy: rating range and reverse-keying scale"):
        task.parse_dataset(raw)


def test_one_statement_per_prompt(task: Any, items: list[dict[str, Any]]) -> None:
    messages = task.build_messages(items[1], "told_qrng")
    assert messages == task.build_messages(items[1], None)
    assert messages == [
        {"role": "system", "content": items[1]["system"]},
        {
            "role": "user",
            "content": "You can only reply numbers from 1 to 5. Score them one by one:\n"
            "1. Empathy statement 2.",
        },
    ]


@pytest.mark.parametrize(
    ("text", "rating"),
    [
        ("1: 4", 4),
        ("4", 4),
        (" Statement 1: 3.\n", 3),
        ("1: 1", 1),
        ("1", 1),
        ("1:4", 4),
        ("I would say 4", None),
        ("1: 4, because I care", None),
        ("4 or 5", None),
        ("", None),
    ],
)
def test_parse_rating(task: Any, text: str, rating: int | None) -> None:
    assert task.parse_rating(text) == rating


def test_score_keys_and_normalises(task: Any, items: list[dict[str, Any]]) -> None:
    plain, reverse = items[0], items[1]
    assert task.score({"item": plain, "text": "1: 4"}) == {
        "parse_status": "parsed",
        "rating": 4,
        "rating_keyed": 4,
        "rating_norm": 0.75,
        "instrument": "Empathy",
        "statement": 1,
        "subscale": "First",
    }
    keyed = task.score({"item": reverse, "text": "1: 4"})
    assert (keyed["rating"], keyed["rating_keyed"], keyed["rating_norm"]) == (4, 2, 0.25)
    out = task.score({"item": plain, "text": "1: 7"})
    assert (out["parse_status"], out["rating"], out["rating_norm"]) == ("out_of_range", None, None)
    assert task.score({"item": plain, "text": "It depends."})["parse_status"] == "unparseable"
    # Without an item there is no range to check a rating against.
    assert task.score({"item": {}, "text": "Yes. A"})["rating_norm"] is None


def test_endpoints_measure_stability_across_administrations(task: Any) -> None:
    def rows(epoch: int, ratings: list[int], instrument: str = "LMS") -> list[dict[str, Any]]:
        return [
            {
                "sample_ref": f"arm/psychobench/pb_{instrument}_{index:03d}/{epoch}",
                "instrument": instrument,
                "statement": index,
                "rating_keyed": rating,
                "rating_norm": (rating - 1) / 4,
            }
            for index, rating in enumerate(ratings, 1)
        ]

    same = [1, 2, 3, 4, 5, 4, 3, 2, 1]
    stable = task.endpoints(rows(0, same) + rows(1, same) + rows(2, same))
    assert stable["test_retest_reliability"] == pytest.approx(1.0)
    assert stable["item_variance"] == 0.0
    assert stable["complete_administrations"] == {"LMS": 3}

    flipped = [6 - rating for rating in same]
    unstable = task.endpoints(rows(0, same) + rows(1, flipped))
    assert unstable["test_retest_reliability"] == pytest.approx(-1.0)
    assert unstable["item_variance"] > 0.0

    # An incomplete administration and a flat one give no correlation.
    partial = task.endpoints(rows(0, same) + rows(1, same[:5]) + rows(2, [3] * 9))
    assert partial["complete_administrations"] == {"LMS": 2}
    assert partial["n_instruments"] == 0 and math.isnan(partial["test_retest_reliability"])
    assert math.isnan(task.endpoints([])["item_variance"])
