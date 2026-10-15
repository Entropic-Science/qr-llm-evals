"""One spawned Inspect process per (arm, card), with a memory-only RPC channel.

The child has no provider credentials and never calls an inference endpoint.
All paid generation goes through the parent's supplied dispatch callback. Only
allowlisted sample-end log fields become progress events; raw errors, headers,
model arguments and child tracebacks are never forwarded to the event bus.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.models.envelope import SampleContext
from qr_llm_evals.models.schema import Arm
from qr_llm_evals.store.schema import GenerationRow


class Connection(Protocol):
    def send_bytes(self, buf: bytes) -> None: ...
    def recv_bytes(self) -> bytes: ...
    def close(self) -> None: ...


Dispatch = Callable[[SampleContext, list[dict[str, str]]], GenerationRow]
Grader = Callable[[list[dict[str, str]]], str]
Progress = Callable[[str, int], None]


class InspectProcessError(RuntimeError):
    """The child failed; existing paid artifacts are retained for resume."""


@dataclass(frozen=True)
class InspectResult:
    logs: tuple[Path, ...]
    completed: int


def _send(connection: Connection, message: dict[str, Any]) -> None:
    connection.send_bytes(json.dumps(message, ensure_ascii=False).encode("utf-8"))


def _recv(connection: Connection) -> dict[str, Any]:
    value = json.loads(connection.recv_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise InspectProcessError("invalid Inspect IPC message")
    return value


def _child(connection: Connection, payload: dict[str, Any]) -> None:
    # Optional dependencies are imported only in the isolated child.
    try:
        from inspect_ai import Epochs, eval
        from inspect_ai.dataset import MemoryDataset, Sample
        from inspect_ai.hooks import Hooks, SampleEnd, hooks
        from inspect_ai.model import GenerateConfig, get_model

        from qr_llm_evals.inspect_bridge.adapter import inspect_task
        from qr_llm_evals.inspect_bridge.provider import Runtime, bind_runtime

        lock = threading.Lock()

        def rpc(message: dict[str, Any]) -> dict[str, Any]:
            with lock:
                _send(connection, message)
                response = _recv(connection)
            if response.get("kind") != "ok":
                raise InspectProcessError("harness dispatch failed")
            return response

        def dispatch(ctx: SampleContext, messages: list[dict[str, str]]) -> GenerationRow:
            # Send only the pairing key, never arm/envelope values supplied by a task.
            response = rpc(
                {
                    "kind": "generate",
                    "prompt_id": ctx.prompt_id,
                    "epoch": ctx.epoch,
                    "messages": messages,
                }
            )
            return GenerationRow.model_validate(response["row"])

        def grader(messages: list[dict[str, str]]) -> str:
            return str(rpc({"kind": "grade", "messages": messages})["text"])

        @hooks("qrllm-progress", "Harness progress from Inspect sample-end log events")
        class ProgressHooks(Hooks):
            async def on_sample_end(self, data: SampleEnd) -> None:
                if data.sample.error is None:
                    # Same lock as RPC, otherwise a concurrent send can corrupt the pipe.
                    with lock:
                        _send(
                            connection,
                            {
                                "kind": "sample_end",
                                "prompt_id": str(data.sample.id),
                                "epoch": data.sample.epoch - 1,
                            },
                        )

        card = EvalCard.model_validate(payload["card"])
        if card.k is None:
            raise ValueError("Inspect card requires k")
        arm = Arm.model_validate(payload["arm"])
        samples = payload.get("samples")
        dataset = MemoryDataset([Sample(**s) for s in samples]) if samples is not None else None
        task = inspect_task(card, dataset=dataset)
        with bind_runtime(Runtime(arm, dispatch, grader)):
            judge = get_model(
                f"qrgrader/{payload['judge_model']}", config=GenerateConfig(temperature=0)
            )
            logs = eval(
                task,
                model=f"qrllm/{arm.id}",
                model_roles={"grader": judge},
                log_dir=payload["log_dir"],
                log_format="eval",
                display="none",
                epochs=Epochs(card.k, "mean"),
                limit=card.dataset.items,
                sample_shuffle=False,
                max_samples=1,
                max_connections=1,
                max_retries=0,
                retry_on_error=0,
                fail_on_error=True,
                log_realtime=True,
                ctl_server=False,
            )
        if not logs or any(log.status != "success" for log in logs):
            raise InspectProcessError("Inspect task failed")
        _send(connection, {"kind": "complete", "logs": [log.location for log in logs]})
    except BaseException:
        # Do not send exception text: third-party tracebacks can contain credentials.
        with contextlib.suppress(OSError, EOFError):
            _send(connection, {"kind": "failed"})
        raise SystemExit(1) from None
    finally:
        connection.close()


def run_inspect_card(
    card: EvalCard,
    arm: Arm,
    log_dir: Path,
    dispatch: Dispatch,
    *,
    judge_model: str,
    grader: Grader | None = None,
    on_sample_end: Progress | None = None,
    should_abort: Callable[[], bool] = lambda: False,
    timeout_s: float = 86400,
    samples: list[dict[str, Any]] | None = None,
) -> InspectResult:
    """Run synchronously; caller gates start/pause in dispatch, abort closes the child.

    ``samples`` supplies an offline dataset for contract tests. Production uses
    the pinned dataset. No secrets, callbacks, transports or stores are pickled.
    """
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if card.k is None:
        raise ValueError("Inspect card requires k")
    log_dir.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    payload = {
        "card": card.model_dump(mode="json"),
        "arm": arm.model_dump(mode="json"),
        "log_dir": str(log_dir.resolve()),
        "judge_model": judge_model,
        "samples": samples,
    }
    process = context.Process(target=_child, args=(child, payload), daemon=False)
    seen: set[tuple[str, int]] = set()
    deadline = time.monotonic() + timeout_s
    process.start()
    child.close()
    try:
        while True:
            if should_abort():
                raise InspectProcessError("Inspect run aborted; artifacts retained")
            if time.monotonic() > deadline:
                raise InspectProcessError("Inspect subprocess timed out; artifacts retained")
            if not parent.poll(0.1):
                if not process.is_alive():
                    raise InspectProcessError("Inspect subprocess exited before completion")
                continue
            message = _recv(parent)
            kind = message.get("kind")
            if kind in {"generate", "sample_end"}:
                prompt_id, epoch = str(message["prompt_id"]), int(message["epoch"])
                if not prompt_id or any(c in prompt_id for c in "/\\|") or not 0 <= epoch < card.k:
                    raise InspectProcessError("invalid Inspect sample key")
            if kind == "generate":
                ctx = SampleContext(card.id, prompt_id, epoch, card.max_tokens, card.thinking)
                row = dispatch(ctx, message["messages"])
                _send(parent, {"kind": "ok", "row": row.model_dump(mode="json")})
            elif kind == "grade":
                if grader is None:
                    raise InspectProcessError("Inspect task requires a harness grader")
                _send(parent, {"kind": "ok", "text": grader(message["messages"])})
            elif kind == "sample_end":
                key = (prompt_id, epoch)
                if key not in seen:
                    seen.add(key)
                    if on_sample_end is not None:
                        on_sample_end(*key)
            elif kind == "complete":
                if len(seen) != card.dataset.items * card.k:
                    raise InspectProcessError("Inspect completed with an incomplete sample set")
                paths = tuple(Path(p).resolve() for p in message["logs"])
                if not paths or any(
                    not p.is_relative_to(log_dir.resolve())
                    or p.suffix != ".eval"
                    or not p.is_file()
                    for p in paths
                ):
                    raise InspectProcessError("Inspect did not retain valid eval logs")
                process.join(timeout=10)
                if process.exitcode != 0:
                    raise InspectProcessError("Inspect subprocess did not exit successfully")
                return InspectResult(paths, len(seen))
            else:
                raise InspectProcessError("Inspect subprocess failed; artifacts retained")
    except (EOFError, OSError) as error:
        raise InspectProcessError("Inspect IPC closed unexpectedly; artifacts retained") from error
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=10)
        parent.close()
        process.close()
