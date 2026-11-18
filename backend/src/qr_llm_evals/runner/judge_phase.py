"""Generic judge phase for thin cards (spec §2.5, D-9).

After generation, every card with ``judge.kind == "llm"`` is judged sample by
sample. The judge is either the **study judge** (Anthropic Messages API at
temperature 0, ``card.judge.model == "study_judge"``) or a provider model named
on the card, called through the OpenAI-compatible client at temperature 0 —
never through an experimental sampler.

Rows go to ``judge/<judge_id>.jsonl`` keyed by ``sample_ref``; a row with
``score: null`` records a failed call and is re-runnable, so the phase resumes
via ``RunStore.completed_judge_refs`` exactly like generation does.

Scores are parsed from the judge text by :func:`parse_score`: a ``SCORE: <n>``
line, a JSON object with a ``score`` field, or the last bare number. Cards
needing anything richer keep the raw text (``JudgeRow.raw``) and parse in
their analysis step.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.cards.schema import STUDY_JUDGE, EvalCard
from qr_llm_evals.client.anthropic_judge import AnthropicJudgeClient, JudgeError, JudgeRequest
from qr_llm_evals.client.openai_client import OpenAIClient, UpstreamError
from qr_llm_evals.client.profiles import target_for
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import JudgeSpec, ModelRef, StudyConfig
from qr_llm_evals.runner.events import EventSink, NullEventSink, RunEvent
from qr_llm_evals.runner.tasks import LoadedTask
from qr_llm_evals.store.schema import GenerationRow, JudgeRow, Usage
from qr_llm_evals.store.store import RunStore

JUDGE_MAX_TOKENS = 1024
_SCORE_LINE = re.compile(r"(?im)^\s*score\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*$")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class NeedsKeysError(RuntimeError):
    """A BYO key the phase needs is not in the run's ``SecretBag``."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"missing {provider!r} key; the run needs keys re-supplied")
        self.provider = provider


def parse_score(text: str) -> float | None:
    """``SCORE: 7`` → 7.0; ``{"score": 0.5}`` → 0.5; else the last number in the text."""
    m = _SCORE_LINE.search(text)
    if m:
        return float(m.group(1))
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("score"), int | float):
            return float(obj["score"])
    numbers = _NUMBER.findall(text)
    return float(numbers[-1]) if numbers else None


def rubric_text(card: EvalCard, repo_root: Path | None = None) -> str:
    """The frozen rubric (LF-normalised); required for judged cards."""
    if not card.judge.rubric:
        raise ValueError(f"card {card.id!r}: judge.rubric is required for an LLM judge")
    path = (repo_root if repo_root is not None else REPO_ROOT) / card.judge.rubric
    if not path.is_file():
        raise FileNotFoundError(f"card {card.id!r}: rubric {path} does not exist")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def rubric_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def judge_id_for(card: EvalCard, spec: JudgeSpec) -> str:
    """``study_judge`` or a slug of the card's own judge model (the JSONL file name)."""
    model = card.judge.model or STUDY_JUDGE
    if model == STUDY_JUDGE:
        return STUDY_JUDGE
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("_")[:120] or "judge"


@dataclass(frozen=True, slots=True)
class JudgeReply:
    """What a judge call returns: its text and the tokens it cost."""

    text: str
    usage: Usage


def _count(value: object) -> int:
    """A provider's token count, or 0 when it is missing or not a non-negative int."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass(frozen=True, slots=True)
class JudgeCall:
    """A judge, resolved: how to call it and what to record."""

    judge_id: str
    model: str
    call: Callable[[str, str], JudgeReply]  # (system, user) -> text + usage


def _anthropic_call(
    spec: JudgeSpec, secrets: SecretBag, run_id: str, transport: httpx.BaseTransport | None
) -> JudgeCall:
    key = secrets.get("anthropic")
    if not key:
        raise NeedsKeysError("anthropic")
    client = AnthropicJudgeClient(key, model=spec.model, transport=transport, sleep=False)

    def call(system: str, user: str) -> JudgeReply:
        resp = client.judge(
            JudgeRequest(system=system, user=user, judge_run_id=run_id, max_tokens=JUDGE_MAX_TOKENS)
        )
        return JudgeReply(
            resp.text,
            Usage(
                prompt_tokens=_count(resp.usage.get("input_tokens")),
                completion_tokens=_count(resp.usage.get("output_tokens")),
            ),
        )

    return JudgeCall(judge_id=STUDY_JUDGE, model=spec.model, call=call)


def _provider_call(
    card: EvalCard,
    spec: JudgeSpec,
    study: StudyConfig,
    secrets: SecretBag,
    *,
    vllm_origin: str,
    transport: httpx.BaseTransport | None,
) -> JudgeCall:
    """A card-named judge model: ``<ModelRef.id>`` from the study or ``<provider>:<served>``."""
    model_name = card.judge.model or ""
    ref: ModelRef | None = next((m for m in study.models if m.id == model_name), None)
    if ref is None:
        provider, _, served = model_name.partition(":")
        if not served:
            raise ValueError(
                f"card {card.id!r}: judge.model {model_name!r} is neither a study model id "
                "nor '<provider>:<served_name>'"
            )
        ref = ModelRef(
            id=model_name,
            family="qr_llm" if provider == "qr_server" else "external",
            provider=provider,  # type: ignore[arg-type]
            served_name=served,
        )
    target = target_for(ref.provider, vllm_origin=vllm_origin)
    api_key = None
    if target.needs_key:
        api_key = secrets.get(target.secret_name or "")
        if not api_key:
            raise NeedsKeysError(target.secret_name or ref.provider)
    client = OpenAIClient(
        target.base_url, api_key=api_key, extra_headers=target.extra_headers, transport=transport
    )

    def call(system: str, user: str) -> JudgeReply:
        body = {
            "model": ref.served_name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": JUDGE_MAX_TOKENS,
        }
        completion = client.complete(body)
        raw = completion.usage or {}
        return JudgeReply(
            completion.text,
            Usage(
                prompt_tokens=_count(raw.get("prompt_tokens")),
                completion_tokens=_count(raw.get("completion_tokens")),
            ),
        )

    return JudgeCall(judge_id=judge_id_for(card, spec), model=ref.served_name, call=call)


def resolve_judge(
    card: EvalCard,
    study: StudyConfig,
    secrets: SecretBag,
    *,
    run_id: str,
    vllm_origin: str,
    transport: httpx.BaseTransport | None = None,
) -> JudgeCall:
    if (card.judge.model or STUDY_JUDGE) == STUDY_JUDGE:
        return _anthropic_call(study.judge, secrets, run_id, transport)
    return _provider_call(
        card, study.judge, study, secrets, vllm_origin=vllm_origin, transport=transport
    )


def sample_for_judge(row: GenerationRow, item: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "sample_ref": row.sample_ref,
        "item": dict(item or {"prompt_id": row.prompt_id}),
        "told": row.told_condition,
        "text": row.output,
        "thinking": row.thinking,
    }


class JudgePhase:
    """Judge every completed generation of a judged card; resumable by ``sample_ref``."""

    def __init__(
        self,
        run_store: RunStore,
        study: StudyConfig,
        secrets: SecretBag,
        *,
        vllm_origin: str,
        sink: EventSink | None = None,
        transport: httpx.BaseTransport | None = None,
        repo_root: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self._store = run_store
        self._study = study
        self._secrets = secrets
        self._vllm_origin = vllm_origin
        self._sink = sink if sink is not None else NullEventSink()
        self._transport = transport
        self._repo_root = repo_root
        self._clock = clock or (lambda: datetime.now(UTC))
        self._should_stop = should_stop or (lambda: False)
        self._lock = threading.Lock()

    def judge_card(
        self, arm_ids: Iterable[str], card: EvalCard, task: LoadedTask
    ) -> dict[str, int]:
        """Judge the missing samples of ``card`` for every arm; returns counters."""
        if card.judge.kind != "llm":
            return {"judged": 0, "skipped": 0, "failed": 0}
        judge = resolve_judge(
            card,
            self._study,
            self._secrets,
            run_id=self._store.run_id,
            vllm_origin=self._vllm_origin,
            transport=self._transport,
        )
        system = rubric_text(card, self._repo_root)
        r_hash = rubric_hash(system)
        items = {str(i["prompt_id"]): i for i in task.build_items()}
        judged = skipped = failed = 0
        for arm_id in arm_ids:
            done = self._store.completed_judge_refs(arm_id, card.id, judge.judge_id)
            for row in self._store.generations(arm_id, card.id):
                if row.finish_reason not in ("stop", "length"):
                    continue
                if row.sample_ref in done:
                    skipped += 1
                    continue
                if self._should_stop():
                    return {"judged": judged, "skipped": skipped, "failed": failed}
                sample = sample_for_judge(row, items.get(row.prompt_id))
                user = task.judge_prompt(sample)
                score: float | None = None
                usage = Usage()
                try:
                    reply = judge.call(system, user)
                    text, usage = reply.text, reply.usage
                    score = parse_score(text)
                except (JudgeError, UpstreamError, httpx.HTTPError) as err:
                    text = f"error: {type(err).__name__}: {err}"
                if score is None:
                    failed += 1
                else:
                    judged += 1
                with self._lock:
                    self._store.append_judge(
                        arm_id,
                        card.id,
                        JudgeRow(
                            sample_ref=row.sample_ref,
                            judge_id=judge.judge_id,
                            model=judge.model,
                            rubric_hash=r_hash,
                            score=score,
                            raw=text,
                            usage=usage,
                            at=self._clock(),
                        ),
                    )
                done.add(row.sample_ref)
            self._sink.emit(
                RunEvent(
                    "analysis.progress",
                    {
                        "phase": "judge",
                        "arm_id": arm_id,
                        "card_id": card.id,
                        "judged": judged,
                        "skipped": skipped,
                        "failed": failed,
                    },
                )
            )
        return {"judged": judged, "skipped": skipped, "failed": failed}


__all__ = [
    "JUDGE_MAX_TOKENS",
    "JudgeCall",
    "JudgePhase",
    "NeedsKeysError",
    "judge_id_for",
    "parse_score",
    "resolve_judge",
    "rubric_hash",
    "rubric_text",
    "sample_for_judge",
]
