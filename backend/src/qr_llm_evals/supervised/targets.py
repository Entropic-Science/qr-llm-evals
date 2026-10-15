"""Frozen T4-m target pool and provenance-labelled target draws."""

from __future__ import annotations

import hashlib
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from qr_llm_evals.client.openai_client import OpenAIClient
from qr_llm_evals.client.profiles import target_for
from qr_llm_evals.models.envelope import SampleContext, build_request_body
from qr_llm_evals.store.store import Store
from qr_llm_evals.supervised.schema import TargetDraw
from qr_llm_evals.tier0.acquire import build_records, engine_log_path


class TargetPool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(ge=1)
    targets: list[str] = Field(min_length=5)


@dataclass(frozen=True, slots=True)
class LoadedTargetPool:
    pool: TargetPool
    sha256: str


class TargetDrawUnavailable(RuntimeError):
    pass


class EngineTier0UniformDrawer:
    """Perform a dedicated one-token QRNG request and return its logged ``u``."""

    def __init__(
        self, store: Store, origin: str, token_log_dir: Path, *, priority: int = 0
    ) -> None:
        self.store = store
        self.origin = origin
        self.token_log_dir = token_log_dir
        self.priority = priority

    def __call__(self, run_id: str, session_id: str) -> float:
        run_store = self.store.find_run(run_id)
        if run_store is None:
            raise TargetDrawUnavailable(f"run {run_id!r} does not exist")
        study = self.store.load_study(run_store.study_id)
        arm = next(
            (
                candidate
                for candidate in study.arms
                if candidate.source == "qrng"
                and candidate.availability == "available"
                and candidate.observer_mode == "supervised"
                and candidate.sampling != "greedy"
            ),
            None,
        )
        if arm is None:
            raise TargetDrawUnavailable("study has no available stochastic supervised QRNG arm")
        model = next((candidate for candidate in study.models if candidate.id == arm.model), None)
        if model is None or model.provider != "qr_server":
            raise TargetDrawUnavailable("QRNG target draws require a qr-server model")
        ctx = SampleContext(
            card_id="target_draws",
            prompt_id=secrets.token_hex(12),
            epoch=0,
            max_tokens=1,
        )
        request_id = ctx.sample_ref(arm.id)
        body = build_request_body(
            arm,
            model,
            ctx,
            [{"role": "user", "content": "Return one token."}],
            priority=self.priority,
        )
        target = target_for("qr_server", None, vllm_origin=self.origin)
        completion = OpenAIClient(target.base_url).generate(body)
        path = engine_log_path(self.token_log_dir, request_id)
        # The stream may close a few milliseconds before the engine-log flush.
        for _ in range(50):
            if path.is_file():
                break
            time.sleep(0.01)
        if not path.is_file():
            raise TargetDrawUnavailable("dedicated QRNG generation produced no Tier-0 log")
        records = build_records(
            request_id,
            completion.logprobs,
            path,
            session_id=session_id,
            window="supervised",
        )
        if len(records) != 1 or records[0].u is None:
            raise TargetDrawUnavailable(
                "dedicated QRNG generation did not yield exactly one uniform"
            )
        record = records[0]
        if record.is_fallback or record.entropy_source_used not in {None, "qrng"}:
            raise TargetDrawUnavailable("target draw entropy was not a non-fallback QRNG sample")
        run_store.append_tier0(arm.id, "target_draws", records)
        uniform = record.u
        if uniform is None:
            raise TargetDrawUnavailable("Tier-0 uniform disappeared during validation")
        return uniform


def load_target_pool(path: Path) -> LoadedTargetPool:
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    parsed: Any = yaml.safe_load(raw.decode("utf-8"))
    loaded = TargetPool.model_validate(parsed)
    if len(set(loaded.targets)) != len(loaded.targets):
        raise ValueError("target pool entries must be unique")
    return LoadedTargetPool(loaded, hashlib.sha256(raw).hexdigest())


def _index_from_u(u: float, size: int) -> int:
    if not math.isfinite(u) or not 0 <= u <= 1:
        raise ValueError("Tier-0 uniform must be finite and in [0, 1]")
    return min(int(u * size), size - 1)


def draw_target(
    pool: TargetPool,
    *,
    tier0_source: str | None,
    qrng_uniform: Callable[[], float] | None = None,
) -> TargetDraw:
    """Draw one target and four decoys, without replacement.

    An ``engine_log`` run must supply a callable that performs the dedicated
    QRNG generation and returns its Tier-0 ``u``.  There is deliberately no
    silent pseudo-random substitute on that path.  Other acquisition modes
    use the CSPRNG and label the result ``secrets``.
    """
    remaining = list(pool.targets)
    chosen: list[str] = []
    use_qrng = tier0_source == "engine_log"
    if use_qrng and qrng_uniform is None:
        raise TargetDrawUnavailable("engine-log target draws require a Tier-0 QRNG uniform source")
    for _ in range(5):
        if use_qrng:
            if qrng_uniform is None:
                raise TargetDrawUnavailable("Tier-0 QRNG uniform source disappeared during draw")
            index = _index_from_u(qrng_uniform(), len(remaining))
        else:
            index = secrets.randbelow(len(remaining))
        chosen.append(remaining.pop(index))
    return TargetDraw(
        theme=chosen[0], decoys=chosen[1:], draw_source="qrng_tier0" if use_qrng else "secrets"
    )


@dataclass(frozen=True, slots=True)
class HiddenTargetDraw:
    """Server-side wrapper used for an unsupervised twin; no public serializer."""

    _draw: TargetDraw

    def analysis_record(self) -> TargetDraw:
        return self._draw


def draw_unsupervised_twin(
    pool: TargetPool,
    *,
    tier0_source: str | None,
    qrng_uniform: Callable[[], float] | None = None,
) -> HiddenTargetDraw:
    return HiddenTargetDraw(draw_target(pool, tier0_source=tier0_source, qrng_uniform=qrng_uniform))


__all__ = [
    "HiddenTargetDraw",
    "EngineTier0UniformDrawer",
    "LoadedTargetPool",
    "TargetDrawUnavailable",
    "TargetPool",
    "draw_target",
    "draw_unsupervised_twin",
    "load_target_pool",
]
