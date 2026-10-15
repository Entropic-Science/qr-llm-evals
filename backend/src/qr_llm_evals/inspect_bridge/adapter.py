"""Generation-only adaptation of the pinned inspect_evals/novelty_bench task.

Upstream forks k generations *inside* each epoch and imports reward models at
module import. We reproduce its pinned dataset/record mapping, replacing its
solver with one generation per epoch and deferring grouped scoring to analysis.
Loading the upstream scorer here would violate the metrics-only heavy-import
boundary and running the upstream solver would multiply the budget by k.
"""

from __future__ import annotations

from inspect_ai import Epochs, Task
from inspect_ai.dataset import Dataset, FieldSpec, hf_dataset
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import generate

from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.inspect_bridge.solver import qr_sample_context

INSPECT_EVALS_COMMIT = "360484a06383f9260279938262d78ed646ddbca1"
DATASET = "yimingzhang/novelty-bench"
DATASET_REVISION = "255584e0f4538d1f2acb4c1d287b4d3bb30fe2f9"


def inspect_task(card: EvalCard, *, dataset: Dataset | None = None) -> Task:
    """Build the registered task's generation adapter; dataset injection is for tests."""
    if card.engine != "inspect" or card.inspect_task != "inspect_evals/novelty_bench":
        raise ValueError("unsupported Inspect task")
    if card.k is None or card.task_args.get("num_generations") != card.k:
        raise ValueError("num_generations must match k (one generation per epoch)")
    if card.dataset.source != f"hf:{DATASET}" or card.dataset.revision != DATASET_REVISION:
        raise ValueError("NoveltyBench dataset differs from the pinned upstream revision")
    args = card.task_args
    if set(args) - {
        "num_generations",
        "dataset_split",
        "shuffle",
        "equivalence_threshold",
        "quality_model",
    }:
        raise ValueError("unsupported NoveltyBench task_args")
    if args.get("dataset_split", "curated") != "curated" or args.get("shuffle", False):
        raise ValueError("Study 1 requires the fixed, unshuffled curated split")
    if dataset is None:
        dataset = hf_dataset(
            path=DATASET,
            split="curated",
            revision=DATASET_REVISION,
            sample_fields=FieldSpec(input="prompt", id="id"),
            shuffle=False,
        )
    if len(dataset) < card.dataset.items:
        raise ValueError("dataset contains fewer items than the frozen card")
    return Task(
        name="novelty_bench",
        dataset=dataset[: card.dataset.items],
        solver=[qr_sample_context(card), generate(cache=False)],
        epochs=Epochs(card.k, "mean"),
        config=GenerateConfig(max_tokens=card.max_tokens, num_choices=1),
        metadata={
            "upstream_task": card.inspect_task,
            "inspect_evals_commit": INSPECT_EVALS_COMMIT,
            "dataset_revision": DATASET_REVISION,
            "task_args": args,
            "scoring": "deferred: group all epochs by prompt before distinct_k/utility_k/vendi_embed",
            "epoch_mapping": "harness epoch = Inspect epoch - 1",
        },
    )
