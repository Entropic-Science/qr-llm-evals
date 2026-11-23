"""ifbench: pinned items, strict and loose verification, and an honest "unavailable".

The verifier is an optional install (the ``[ifbench]`` extra plus NLTK data),
so the scoring logic is tested against a small stand-in registry and the real
verifier only where it is installed from the pinned commit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from qr_llm_evals.cards import load_cards
from qr_llm_evals.runner.tasks import load_task_module


class _Keyword:
    """Stand-in checker: the reply must contain ``keyword``."""

    def __init__(self, instruction_id: str) -> None:
        self.id = instruction_id
        self.keyword = ""

    def build_description(self, *, keyword: str = "") -> None:
        self.keyword = keyword

    def get_instruction_args(self) -> dict[str, Any]:
        return {"keyword": self.keyword}

    def check_following(self, value: str) -> bool:
        return self.keyword in value


class _StartsWithPrompt:
    """Stand-in checker that takes the prompt in a second ``build_description`` call."""

    def __init__(self, instruction_id: str) -> None:
        self.prompt: str | None = None
        self.calls: list[dict[str, Any]] = []

    def build_description(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        self.prompt = kwargs.get("prompt", self.prompt)

    def get_instruction_args(self) -> dict[str, Any]:
        return {"prompt": self.prompt}

    def check_following(self, value: str) -> bool:
        return self.prompt is not None and value.startswith(self.prompt[:5])


REGISTRY: Mapping[str, Any] = {"test:keyword": _Keyword, "test:repeat": _StartsWithPrompt}
ITEM = {
    "prompt_id": "ifb_0",
    "prompt": "Write about rivers. Include the keyword delta.",
    "instruction_id_list": ["test:keyword"],
    "kwargs": [{"keyword": "delta"}],
}


@pytest.fixture(scope="module")
def task() -> Any:
    return load_task_module(load_cards()["ifbench"]).module


@pytest.fixture
def fake_verifier(task: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task, "_load_registry", lambda: REGISTRY)


def _raw(rows: list[dict[str, Any]]) -> bytes:
    return "\n".join(json.dumps(row) for row in rows).encode("utf-8")


def _rows(n: int = 300) -> list[dict[str, Any]]:
    return [
        {
            "key": str(i),
            "prompt": f"Prompt {i}",
            "instruction_id_list": ["test:keyword"],
            "kwargs": [{"keyword": "delta", "N": None, "letter": None}],
        }
        for i in range(n)
    ]


def test_card_is_implemented_and_pinned() -> None:
    card = load_cards()["ifbench"]
    assert card.status == "implemented" and card.selectable and card.engine == "thin"
    module = load_task_module(card).module
    assert card.dataset.source == "git:github.com/allenai/IFBench"
    assert card.dataset.revision == module.VERIFIER_COMMIT
    assert card.dataset.items == module.ITEM_COUNT
    assert module.VERIFIER_COMMIT in module.DATASET_URL


def test_the_extra_pins_the_same_commit(task: Any) -> None:
    pyproject = load_task_module(load_cards()["ifbench"]).path.parents[2] / "pyproject.toml"
    assert f"allenai/IFBench@{task.VERIFIER_COMMIT}" in pyproject.read_text(encoding="utf-8")


def test_dataset_is_checked_and_unused_arguments_are_dropped(
    task: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _raw(_rows())
    with pytest.raises(ValueError, match="sha256"):
        task.parse_dataset(raw)
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(raw).hexdigest())
    items = task.parse_dataset(raw)
    assert [item["prompt_id"] for item in items] == [f"ifb_{i}" for i in range(300)]
    assert items[0] == {
        "prompt_id": "ifb_0",
        "prompt": "Prompt 0",
        "instruction_id_list": ["test:keyword"],
        "kwargs": [{"keyword": "delta"}],
    }
    misaligned = _rows()
    misaligned[3]["kwargs"] = []
    bad = _raw(misaligned)
    monkeypatch.setattr(task, "DATASET_SHA256", task.hashlib.sha256(bad).hexdigest())
    with pytest.raises(ValueError, match="do not align"):
        task.parse_dataset(bad)


def test_message_is_the_prompt_alone(task: Any) -> None:
    assert task.build_messages(ITEM, "told_qrng") == [{"role": "user", "content": ITEM["prompt"]}]


def test_loose_variants_are_upstreams_eight(task: Any) -> None:
    text = "Sure, here it is:\n**delta** rises\nHope that helps!"
    assert task.loose_variants(text) == [
        text,
        "Sure, here it is:\ndelta rises\nHope that helps!",
        "**delta** rises\nHope that helps!",
        "Sure, here it is:\n**delta** rises",
        "**delta** rises",
        "delta rises\nHope that helps!",
        "Sure, here it is:\ndelta rises",
        "delta rises",
    ]


def test_strict_and_loose_scores(task: Any, fake_verifier: None) -> None:
    assert task.score({"item": ITEM, "text": "The delta widens."}) == {
        "verifier_status": "available",
        "pass_at_1": 1,
        "strict_accuracy": 1,
        "instruction_pass_rate": 1.0,
        "n_instructions": 1,
    }
    # Only the reading without asterisks contains the keyword.
    loose_only = task.score({"item": ITEM, "text": "The del*ta widens."})
    assert (loose_only["pass_at_1"], loose_only["strict_accuracy"]) == (1, 0)
    missed = task.score({"item": ITEM, "text": "The estuary widens."})
    assert (missed["pass_at_1"], missed["strict_accuracy"]) == (0, 0)
    # An empty reply follows nothing, even a constraint an empty string would satisfy.
    blank = {**ITEM, "kwargs": [{"keyword": ""}]}
    assert task.score({"item": blank, "text": "  \n"})["pass_at_1"] == 0


def test_two_constraints_and_the_prompt_argument(task: Any, fake_verifier: None) -> None:
    item = {
        "prompt_id": "ifb_1",
        "prompt": "Repeat this request, then include the keyword delta.",
        "instruction_id_list": ["test:repeat", "test:keyword"],
        "kwargs": [{}, {"keyword": "delta"}],
    }
    both = task.score({"item": item, "text": "Repeat this request. delta"})
    assert (both["pass_at_1"], both["instruction_pass_rate"], both["n_instructions"]) == (1, 1.0, 2)
    one = task.score({"item": item, "text": "Repeat this request."})
    assert (one["pass_at_1"], one["instruction_pass_rate"]) == (0, 0.5)
    checker = task._checkers(item, REGISTRY)[0]
    assert checker.calls == [{}, {"prompt": item["prompt"]}]


def test_missing_verifier_is_reported_not_guessed(
    task: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> Mapping[str, Any]:
        raise task.VerifierUnavailable("ifbench is not installed; this card needs abc")

    monkeypatch.setattr(task, "_load_registry", unavailable)
    assert task.score({"item": ITEM, "text": "The delta widens."}) == {
        "verifier_status": "unavailable: ifbench is not installed; this card needs abc",
        "pass_at_1": None,
        "strict_accuracy": None,
        "instruction_pass_rate": None,
        "n_instructions": None,
    }
    assert task.score({"item": {}, "text": "Yes. A"})["verifier_status"] == "unavailable: no item"


def test_a_verifier_from_another_commit_is_refused(
    task: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task._load_registry.cache_clear()
    monkeypatch.setattr(task, "_installed_commit", lambda: "0" * 40)
    with pytest.raises(task.VerifierUnavailable, match="installed from 0000"):
        task._load_registry()
    monkeypatch.setattr(task, "_installed_commit", lambda: None)
    with pytest.raises(task.VerifierUnavailable, match="not installed"):
        task._load_registry()


def test_real_verifier_when_installed(task: Any) -> None:
    task._load_registry.cache_clear()
    try:
        registry = task._load_registry()
    except task.VerifierUnavailable as reason:
        pytest.skip(str(reason))
    assert len(registry) >= 58
    item = {
        "prompt_id": "ifb_x",
        "prompt": "Describe a river.",
        "instruction_id_list": ["count:keywords_multiple"],
        "kwargs": [
            {
                "keyword1": "kaleidoscope",
                "keyword2": "nebula",
                "keyword3": "whisper",
                "keyword4": "labyrinth",
                "keyword5": "paradox",
            }
        ],
    }
    assert task.score({"item": item, "text": "A river."})["pass_at_1"] == 0


def test_endpoints_group_the_replies_of_a_prompt(task: Any) -> None:
    def row(prompt: str, epoch: int, loose: int | None, strict: int | None) -> dict[str, Any]:
        return {
            "sample_ref": f"arm/ifbench/{prompt}/{epoch}",
            "pass_at_1": loose,
            "strict_accuracy": strict,
        }

    rows = [
        *(row("ifb_0", e, 1, 1) for e in range(4)),
        row("ifb_1", 0, 1, 0),
        row("ifb_1", 1, 1, 1),
        row("ifb_1", 2, 0, 0),
        row("ifb_1", 3, 0, 0),
        row("ifb_2", 0, None, None),  # unverified replies are left out, not counted as failures
    ]
    out = task.endpoints(rows)
    assert out["n_prompts"] == 2
    assert out["constraint_pass_entropy_by_prompt"] == {"ifb_0": 0.0, "ifb_1": 1.0}
    assert out["constraint_pass_entropy"] == pytest.approx(0.5)
    assert out["pass_at_1"] == pytest.approx(0.75)
    assert out["strict_accuracy"] == pytest.approx(0.625)
