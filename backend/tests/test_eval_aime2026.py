"""aime2026: pinned items, the upstream prompt, and the answer-extraction rule."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.runner.tasks import load_task_module


@pytest.fixture(scope="module")
def task() -> Any:
    return load_task_module(load_cards()["aime2026"]).module


def test_card_is_implemented_and_pinned() -> None:
    card = load_cards()["aime2026"]
    assert card.status == "implemented" and card.selectable and card.engine == "thin"
    module = load_task_module(card).module
    assert card.dataset.revision == module.DATASET_REVISION
    assert card.dataset.items == module.ITEM_COUNT
    assert module.DATASET_REVISION in module.DATASET_URL


def test_dataset_with_other_bytes_is_refused(task: Any) -> None:
    raw = "\n".join(json.dumps({"id": i, "problem": "p", "answer": i}) for i in range(1, 31))
    with pytest.raises(ValueError, match="sha256"):
        task.parse_dataset(raw.encode("utf-8"))


def test_items_are_ordered_by_problem_number(task: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": i, "problem": f"Problem {i}", "answer": 100 + i} for i in range(30, 0, -1)]
    raw = "\n".join(json.dumps(row) for row in rows).encode("utf-8")
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(raw).hexdigest())
    items = task.parse_dataset(raw)
    assert [item["prompt_id"] for item in items] == [f"aime26_{i:02d}" for i in range(1, 31)]
    assert items[0] == {"prompt_id": "aime26_01", "problem": "Problem 1", "answer": "101"}


def test_messages_use_the_upstream_template_and_ignore_told(task: Any) -> None:
    item = {"prompt_id": "aime26_01", "problem": "Find $x$ if {x} = 2.", "answer": "2"}
    messages = task.build_messages(item, "told_qrng")
    assert messages == task.build_messages(item, None)
    assert [m["role"] for m in messages] == ["user"]
    content = messages[0]["content"]
    assert content.startswith("Solve the following math problem step by step.\n")
    assert "\n\nFind $x$ if {x} = 2.\n\n" in content
    assert content.endswith("you do not need to use a \\boxed command.")


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("Work.\nANSWER: 277", "277"),
        ("ANSWER: 070", "70"),
        ("So we get\n$$\n\\boxed{70}\n$$", "70"),
        ("ANSWER: **1,024**", "1024"),
        ("ANSWER: \\$62.", "62"),
        ("The answer is 12 or maybe 13", "13"),
        ("ANSWER: 277\n---", "277"),
        ("ANSWER: (441)", "441"),
        ("I cannot solve this.", None),
        ("", None),
    ],
)
def test_extract_answer(task: Any, text: str, answer: str | None) -> None:
    assert task.extract_answer(text) == answer


def test_score_per_reply(task: Any) -> None:
    item = {"prompt_id": "aime26_01", "problem": "p", "answer": "277"}
    assert task.score({"item": item, "text": "steps\nANSWER: 277"}) == {
        "parse_status": "parsed",
        "answer": "277",
        "pass_at_1": 1,
    }
    assert task.score({"item": item, "text": "ANSWER: 27"})["pass_at_1"] == 0
    # "2770" ends with the digits of the target but is a different number.
    assert task.score({"item": item, "text": "ANSWER: 2770"})["pass_at_1"] == 0
    assert task.score({"item": item, "text": "no idea"}) == {
        "parse_status": "unparseable",
        "answer": None,
        "pass_at_1": 0,
    }
    # Without an item there is nothing to grade against.
    assert task.score({"item": {}, "text": "Yes. A"})["pass_at_1"] is None


def test_matches_inspect_scorer_when_installed(task: Any) -> None:
    common = pytest.importorskip("inspect_ai.scorer._common")
    for text in ("ANSWER: 277", "x\n$$\n\\boxed{70}\n$$", "ANSWER: 1,024", "no number", "= 5."):
        line = task.re.sub(r"\\boxed\{(.+)\}", r"\1", task._last_substantive_line(text))
        for target in ("277", "70", "1024", "5"):
            _, correct = common.match_str(value=line, target=target, location="end", numeric=True)
            ours = task.score({"item": {"answer": target}, "text": text})["pass_at_1"]
            assert bool(ours) == correct, (text, target)


def test_endpoints_group_the_replies_of_a_prompt(task: Any) -> None:
    def row(prompt: str, epoch: int, answer: str | None, ok: int) -> dict[str, Any]:
        return {"sample_ref": f"arm/aime2026/{prompt}/{epoch}", "answer": answer, "pass_at_1": ok}

    rows = [
        *(row("aime26_01", e, "277", 1) for e in range(4)),  # unanimous and right
        row("aime26_02", 0, "62", 1),
        row("aime26_02", 1, "62", 1),
        row("aime26_02", 2, "61", 0),
        row("aime26_02", 3, None, 0),
        row("aime26_03", 0, "1", 0),  # a tie is not a majority
        row("aime26_03", 1, "79", 1),
    ]
    out = task.endpoints(rows)
    assert out["n_prompts"] == 3
    assert out["answer_entropy_by_prompt"] == {
        "aime26_01": 0.0,
        "aime26_02": 1.5,
        "aime26_03": 1.0,
    }
    assert out["answer_entropy"] == pytest.approx(2.5 / 3)
    assert out["pass_at_1"] == pytest.approx((1 + 0.5 + 0.5) / 3)
    assert out["maj_at_k"] == pytest.approx(2 / 3)
    assert math.isnan(task.endpoints([])["answer_entropy"])
