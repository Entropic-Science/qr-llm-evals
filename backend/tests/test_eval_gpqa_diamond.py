"""gpqa_diamond: the fixed choice order, the upstream prompt and the answer parser.

Every row here is synthetic: the real questions carry a canary string and are
never written into this repository.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.runner.tasks import load_task_module

COLUMNS = [
    "Record ID",
    "Question",
    "Correct Answer",
    "Incorrect Answer 1",
    "Incorrect Answer 2",
    "Incorrect Answer 3",
    "High-level domain",
    "Subdomain",
]


def _rows(n: int = 198) -> list[dict[str, str]]:
    return [
        {
            "Record ID": f"rec{i:03d}",
            "Question": f"Question {i}?",
            "Correct Answer": f"right {i}",
            "Incorrect Answer 1": f"wrong {i}a",
            "Incorrect Answer 2": f"wrong {i}b",
            "Incorrect Answer 3": f"wrong {i}c",
            "High-level domain": "Physics",
            "Subdomain": "Optics and Acoustics",
        }
        for i in range(n)
    ]


def _csv(rows: list[dict[str, str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


@pytest.fixture(scope="module")
def task() -> Any:
    return load_task_module(load_cards()["gpqa_diamond"]).module


def test_card_is_implemented_and_pinned() -> None:
    card = load_cards()["gpqa_diamond"]
    assert card.status == "implemented" and card.selectable and card.engine == "thin"
    module = load_task_module(card).module
    assert card.dataset.revision == f"sha256:{module.DATASET_SHA256}"
    assert card.dataset.items == module.ITEM_COUNT


def test_dataset_with_other_bytes_is_refused(task: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _csv(_rows())
    with pytest.raises(ValueError, match="sha256"):
        task.parse_dataset(raw)
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(raw).hexdigest())
    items = task.parse_dataset(raw)
    assert len(items) == 198 and len({item["prompt_id"] for item in items}) == 198
    short = _csv(_rows(197))
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(short).hexdigest())
    with pytest.raises(ValueError, match="197 rows"):
        task.parse_dataset(short)


def test_choice_order_is_fixed_and_the_target_follows_it(task: Any) -> None:
    items = task.shuffled_items(_rows())
    assert items == task.shuffled_items(_rows())
    for index, item in enumerate(items):
        assert item["prompt_id"] == f"gpqa_rec{index:03d}"
        assert sorted(item["choices"]) == sorted(
            [f"right {index}", f"wrong {index}a", f"wrong {index}b", f"wrong {index}c"]
        )
        assert item["choices"]["ABCD".index(item["target"])] == f"right {index}"
    # The correct answer is first in the file; the shuffle must spread it out.
    assert {item["target"] for item in items} == set("ABCD")


def test_choice_order_matches_inspect_when_installed(task: Any) -> None:
    dataset = pytest.importorskip("inspect_ai.dataset")
    samples = [
        dataset.Sample(
            input=row["Question"],
            choices=[row[c] for c in COLUMNS[2:6]],
            target="A",
            id=row["Record ID"],
        )
        for row in _rows()
    ]
    upstream = dataset.MemoryDataset(samples)
    upstream.shuffle_choices(seed=task.SHUFFLE_SEED)
    ours = task.shuffled_items(_rows())
    assert [(s.choices, s.target) for s in upstream] == [(i["choices"], i["target"]) for i in ours]


def test_messages_use_the_upstream_template_and_ignore_told(task: Any) -> None:
    item = {
        "prompt_id": "gpqa_rec000",
        "question": "Which {one}?",
        "choices": ["w", "x", "y", "z"],
        "target": "C",
    }
    messages = task.build_messages(item, "told_prng")
    assert messages == task.build_messages(item, None)
    assert messages == [
        {
            "role": "user",
            "content": (
                "Answer the following multiple choice question. The last line of your response "
                "should be of the following format: 'ANSWER: $LETTER' (without quotes) where "
                "LETTER is one of A,B,C,D. Think step by step before answering.\n\n"
                "Which {one}?\n\nA) w\nB) x\nC) y\nD) z"
            ),
        }
    ]


CASES = [
    ("Reasoning.\nANSWER: C", "C"),
    ("ANSWER: b.", "B"),
    ("ANSWER: A\nOn reflection.\nANSWER: D", "D"),
    ("ANSWER: A,", "A"),
    ("So the answer: C.", "C"),
    # The loose rule reads everything after the colon, so trailing words spoil it.
    ("So the answer: C is right", None),
    ("**ANSWER: C**", "C"),
    ("ANSWER: A B", None),
    ("ANSWER: E", None),
    ("ANSWER: A and B", None),
    ("I think it is C", None),
    ("", None),
]


@pytest.mark.parametrize(("text", "answer"), CASES)
def test_parse_answer(task: Any, text: str, answer: str | None) -> None:
    assert task.parse_answer(text) == answer


def test_parser_matches_inspect_when_installed(task: Any) -> None:
    solver = pytest.importorskip("inspect_ai.solver._multiple_choice")
    model = pytest.importorskip("inspect_ai.model")
    state_module = pytest.importorskip("inspect_ai.solver._task_state")
    for text, _ in CASES:
        state = state_module.TaskState(
            model="mock/model",
            sample_id="s",
            epoch=1,
            input="q",
            messages=[],
            choices=["w", "x", "y", "z"],
            output=model.ModelOutput.from_content("mock/model", text),
        )
        upstream = solver.parse_answers(state, multiple_correct=False) or set()
        ours = task.parse_answer(text)
        assert upstream == ({ours} if ours else set()), text


def test_score_per_reply(task: Any) -> None:
    item = {"target": "C"}
    assert task.score({"item": item, "text": "x\nANSWER: C"}) == {
        "parse_status": "parsed",
        "answer": "C",
        "pass_at_1": 1,
    }
    assert task.score({"item": item, "text": "ANSWER: A"})["pass_at_1"] == 0
    assert task.score({"item": item, "text": "unsure"}) == {
        "parse_status": "unparseable",
        "answer": None,
        "pass_at_1": 0,
    }
    assert task.score({"item": {}, "text": "Yes. A"})["pass_at_1"] is None


def test_endpoints_group_the_replies_of_a_prompt(task: Any) -> None:
    def row(prompt: str, epoch: int, answer: str | None, ok: int) -> dict[str, Any]:
        return {
            "sample_ref": f"arm/gpqa_diamond/{prompt}/{epoch}",
            "answer": answer,
            "pass_at_1": ok,
        }

    rows = [
        *(row("gpqa_a", e, "C", 1) for e in range(4)),
        row("gpqa_b", 0, "A", 0),
        row("gpqa_b", 1, "A", 0),
        row("gpqa_b", 2, "B", 1),
        row("gpqa_b", 3, None, 0),
    ]
    out = task.endpoints(rows)
    assert out["answer_entropy_by_prompt"] == {"gpqa_a": 0.0, "gpqa_b": 1.5}
    assert out["answer_entropy"] == pytest.approx(0.75)
    assert out["pass_at_1"] == pytest.approx((1 + 0.25) / 2)
    # The majority answer of the second question is the wrong one.
    assert out["maj_at_k"] == pytest.approx(0.5)
