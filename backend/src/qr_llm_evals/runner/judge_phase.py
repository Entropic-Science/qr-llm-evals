"""Generic judge phase for thin cards (spec §2.5, D-9).

After generation, every card with ``judge.kind == "llm"`` is judged sample by
sample. The judge is either the **study judge** (Anthropic Messages API at
temperature 0, ``card.judge.model == "study_judge"``) or a provider model named
on the card, called through the OpenAI-compatible client at temperature 0 —
never through an experimental sampler.

Rows go to ``judge/<judge_id>.jsonl`` keyed by ``sample_ref``. Every row has a
``status``:

* ``ok``: a score was read;
* ``failed``: no reply came (an HTTP or transport error, or the card's own
  ``judge_prompt`` / ``judge_fields`` raised). Only these are asked again when
  the run is resumed (``RunStore.completed_judge_refs``);
* ``unreadable`` and ``out_of_range``: a reply came and gave no score the
  rubric allows. **A reply that was received is final**: the row keeps the raw
  reply, has no score and is not asked again, so what the harness's own reader
  finds in a reply never selects which samples get a second draw from the
  judge. There is one exception, and it is the last case of ``failed`` above:
  a card that ships its own ``judge_fields`` and whose function raises on a
  reply gets a ``failed`` row, which is asked again. The cards of the registry
  return nothing for a reply they cannot read, which is ``unreadable`` and final.

Scores are read by the catalogue's reader
(:func:`qr_llm_evals.cards.result_checks.read_judge_score`): the last
``SCORE: <n>`` line, else the first JSON object with a numeric ``score``, and
nothing else. A card whose rubric asks for anything richer ships
``judge_fields(sample, raw)`` in its task module: the phase calls it instead,
keeps its ``score`` as the row's score and stores the other named values in
``JudgeRow.fields``.

What goes wrong with a judgement is also written as a flag (``flags.jsonl``)
and fed to the run's ``Breaker``, which decides whether the judge phase stops.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.cards.schema import STUDY_JUDGE, EvalCard
from qr_llm_evals.client.anthropic_judge import AnthropicJudgeClient, JudgeError, JudgeRequest
from qr_llm_evals.client.openai_client import OpenAIClient
from qr_llm_evals.client.profiles import target_for
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import JudgeSpec, ModelRef, StudyConfig
from qr_llm_evals.runner.events import EventSink, NullEventSink, RunEvent
from qr_llm_evals.runner.tasks import LoadedTask
from qr_llm_evals.store.artifacts import encode_line
from qr_llm_evals.store.schema import GenerationRow, JudgeRow, ResultFlag, Usage
from qr_llm_evals.store.store import RunStore


def judge_client(
    spec: JudgeSpec,
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: bool = True,
) -> AnthropicJudgeClient:
    """The one place a study's judge settings become a client.

    The judge phase, the Inspect grader and the "test judge" route all build
    their client here, so they cannot drift apart.
    """
    return AnthropicJudgeClient(
        api_key,
        model=spec.model,
        max_tokens=spec.max_tokens,
        temperature=spec.temperature,
        thinking="disabled" if spec.thinking == "disabled" else None,
        transport=transport,
        sleep=sleep,
    )


def needs_study_judge(cards: Iterable[EvalCard]) -> bool:
    """True iff some card is scored by the study's shared (Anthropic) judge."""
    return any(
        card.judge.kind == "llm" and (card.judge.model or STUDY_JUDGE) == STUDY_JUDGE
        for card in cards
    )


#: The reader lives in the catalogue so the documents can name it; kept importable from here.
parse_score = rc.parse_score

#: The one request made before the first generation (:func:`judge_preflight`).
PREFLIGHT_SYSTEM = "This is a connection check before a run starts."
PREFLIGHT_USER = "Reply with the single word OK."


class NeedsKeysError(RuntimeError):
    """A BYO key the phase needs is not in the run's ``SecretBag``."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"missing {provider!r} key; the run needs keys re-supplied")
        self.provider = provider


def judge_preflight(
    spec: JudgeSpec,
    secrets: SecretBag,
    *,
    run_id: str,
    transport: httpx.BaseTransport | None = None,
) -> int | None:
    """One small request with the study's judge settings; the HTTP status when it is refused.

    Made before the first generation so that a key, a model name or a setting
    the provider refuses is found before any sample is paid for. Returns
    nothing when the run holds no judge key, when the judge answered, and when
    the request gave no usable answer for any other reason: it could not be
    delivered, the answer could not be read, or something failed that nobody
    expected. None of those is a refusal, so none of them may stop a run or
    leave this function as an exception.
    """
    key = secrets.get("anthropic")
    if not key:
        return None
    try:
        client = judge_client(spec, key, transport=transport, sleep=False)
        client.judge(
            JudgeRequest(system=PREFLIGHT_SYSTEM, user=PREFLIGHT_USER, judge_run_id=run_id)
        )
    except JudgeError as err:
        status = err.status
        return status if isinstance(status, int) and not isinstance(status, bool) else None
    except Exception:  # noqa: BLE001 - inconclusive: the judge phase finds a judge that is down
        return None
    return None


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
    """What a judge call returns: its text, the tokens it cost and why it stopped."""

    text: str
    usage: Usage
    #: The judge's own stop reason (``end_turn``, ``max_tokens``, ``stop``, ...), when it gave one.
    stop_reason: str | None = None


def _count(value: object) -> int:
    """A provider's token count, or 0 when it is missing or not a non-negative int."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass(frozen=True, slots=True)
class JudgeCall:
    """A judge, resolved: how to call it and what to record."""

    judge_id: str
    model: str
    call: Callable[[str, str], JudgeReply]  # (system, user) -> text + usage
    #: The judge runs on the shared engine (a ``qr_server`` judge model).
    on_engine: bool = False


def _anthropic_call(
    spec: JudgeSpec, secrets: SecretBag, run_id: str, transport: httpx.BaseTransport | None
) -> JudgeCall:
    key = secrets.get("anthropic")
    if not key:
        raise NeedsKeysError("anthropic")
    client = judge_client(spec, key, transport=transport, sleep=False)

    def call(system: str, user: str) -> JudgeReply:
        resp = client.judge(JudgeRequest(system=system, user=user, judge_run_id=run_id))
        return JudgeReply(
            resp.text,
            Usage(
                prompt_tokens=_count(resp.usage.get("input_tokens")),
                completion_tokens=_count(resp.usage.get("output_tokens")),
            ),
            resp.stop_reason if isinstance(resp.stop_reason, str) else None,
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
            "max_tokens": spec.max_tokens,
        }
        completion = client.complete(body)
        raw = completion.usage or {}
        return JudgeReply(
            completion.text,
            Usage(
                prompt_tokens=_count(raw.get("prompt_tokens")),
                completion_tokens=_count(raw.get("completion_tokens")),
            ),
            completion.finish_reason,
        )

    return JudgeCall(
        judge_id=judge_id_for(card, spec),
        model=ref.served_name,
        call=call,
        on_engine=ref.provider == "qr_server",
    )


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


@dataclass(slots=True)
class _Judgement:
    """What became of one judge call, before it is written."""

    status: rc.JudgeStatus
    raw: str
    score: float | None = None
    fields: dict[str, float | None] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    #: The HTTP status of a failed call, when there was one.
    http_status: int | None = None
    #: The harness's own name for what went wrong; never upstream text.
    cause: str | None = None
    findings: list[rc.Finding] = field(default_factory=list)


def _failed(
    err: BaseException, *, raw: str | None = None, usage: Usage | None = None
) -> _Judgement:
    """A judgement without a reply. The flag names the status or the class, never the message."""
    status = getattr(err, "status", None)
    http_status = status if isinstance(status, int) and not isinstance(status, bool) else None
    cause = f"HTTP {http_status}" if http_status is not None else type(err).__name__
    return _Judgement(
        status="failed",
        raw=raw if raw is not None else f"error: {type(err).__name__}: {err}",
        usage=usage or Usage(),
        http_status=http_status,
        cause=cause,
        findings=[rc.Finding("judge_failed", cause)],
    )


def _finite(fields: Mapping[str, float | None]) -> bool:
    return all(value is None or math.isfinite(value) for value in fields.values())


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
        breaker: rc.Breaker | None = None,
        on_trip: Callable[[rc.Trip], None] | None = None,
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
        self._breaker = breaker
        self._on_trip = on_trip
        self._lock = threading.Lock()

    def _stopping(self) -> bool:
        """True once the host aborted or a result check stopped the run: take no more work."""
        if self._should_stop():
            return True
        return self._breaker is not None and self._breaker.tripped is not None

    def _judge_one(
        self,
        judge: JudgeCall,
        system: str,
        card: EvalCard,
        task: LoadedTask,
        sample: Mapping[str, Any],
    ) -> _Judgement:
        """Ask the judge about one sample and read the reply. Never raises."""
        try:
            user = task.judge_prompt(sample)
        except Exception as err:  # noqa: BLE001 - one sample's prompt must not end the phase
            return _failed(err)
        try:
            reply = judge.call(system, user)
        except Exception as err:  # noqa: BLE001 - whatever went wrong, no reply came
            return _failed(err)
        text, usage = reply.text, reply.usage
        stop_reason = reply.stop_reason[:64] if isinstance(reply.stop_reason, str) else None
        findings: list[rc.Finding] = []
        cut = rc.judge_cut(stop_reason)
        if cut is not None:
            findings.append(cut)
        score: float | None = None
        fields: dict[str, float | None] = {}
        status: rc.JudgeStatus
        if task.has_judge_fields:
            try:
                read = task.judge_fields(sample, text)
            except Exception as err:  # noqa: BLE001 - the card could not read its own format
                # Asked again on resume: the reply is kept, but nothing was read from it.
                failed = _failed(err, raw=text, usage=usage)
                failed.stop_reason = stop_reason
                failed.findings = findings + failed.findings
                return failed
            read = dict(read or {})
            score = read.pop("score", None)
            if score is None or not math.isfinite(score) or not _finite(read):
                score, status = None, "unreadable"
            else:
                fields, status = read, "ok"
        else:
            try:
                reading = rc.read_judge_score(card.id, text)
            except Exception:  # noqa: BLE001 - a reply that was received is final
                # The reader is written never to raise. If it does all the
                # same, the reply is kept as one no score could be read from:
                # leaving here with the error would stop the phase with no row
                # written, and every resume would ask this sample again and
                # stop again.
                reading = rc.JudgeReading(None, "unreadable")
            score, status = reading.score, reading.status
            if score is not None and not math.isfinite(score):
                score, status = None, "unreadable"  # a judge row cannot hold it
            if status == "out_of_range":
                findings.append(
                    rc.Finding(
                        "judge_out_of_range",
                        f"the reply gave {reading.read:g}, which the rubric does not allow",
                    )
                )
        if status == "unreadable":
            findings.append(
                rc.Finding(
                    "judge_unreadable",
                    f"a reply of {len(text)} characters from which no score could be read",
                )
            )
        return _Judgement(
            status=status,
            raw=text,
            score=score,
            fields=fields,
            usage=usage,
            stop_reason=stop_reason,
            findings=findings,
        )

    def judge_card(
        self, arm_ids: Iterable[str], card: EvalCard, task: LoadedTask
    ) -> dict[str, int]:
        """Judge the missing samples of ``card`` for every arm; returns counters.

        ``judged`` counts the judgements that gave a score, ``failed`` the ones
        that gave none (no reply, or a reply without a score the rubric
        allows), ``skipped`` the samples that needed no call.
        """
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
        arm_list = list(arm_ids)
        # A derived card builds its items from the run's own records, one arm at a
        # time, so the judge needs the same context the generation phase gave it.
        derived = card.dataset.scheme == "derived"
        items = {} if derived else {str(i["prompt_id"]): i for i in task.build_items()}
        judged = skipped = failed = 0
        for arm_id in arm_list:
            if derived:
                items = {
                    str(i["prompt_id"]): i
                    for i in task.build_items(
                        run_store=self._store, arm_id=arm_id, arm_ids=arm_list
                    )
                }
            done = self._store.completed_judge_refs(arm_id, card.id, judge.judge_id)
            for row in self._store.generations(arm_id, card.id):
                if row.finish_reason not in ("stop", "length"):
                    continue
                if row.sample_ref in done:
                    skipped += 1
                    continue
                if self._stopping():
                    return {"judged": judged, "skipped": skipped, "failed": failed}
                sample = sample_for_judge(row, items.get(row.prompt_id))
                result = self._judge_one(judge, system, card, task, sample)
                if result.status == "ok":
                    judged += 1
                else:
                    failed += 1
                at = self._clock()
                judge_row = JudgeRow(
                    sample_ref=row.sample_ref,
                    judge_id=judge.judge_id,
                    model=judge.model,
                    rubric_hash=r_hash,
                    score=result.score,
                    fields=result.fields,
                    raw=result.raw,
                    usage=result.usage,
                    at=at,
                    status=result.status,
                    stop_reason=result.stop_reason,
                    reader_version=(
                        rc.JUDGE_READER_VERSION
                        if result.status in rc.FINAL_JUDGE_STATUSES
                        else None
                    ),
                )
                flags = [
                    ResultFlag(
                        sample_ref=row.sample_ref,
                        kind=finding.kind,
                        phase=finding.phase,
                        card_id=card.id,
                        arm_id=arm_id,
                        detail=finding.detail,
                        count=finding.count,
                        at=at,
                    )
                    for finding in result.findings
                ]
                # Everything is encoded once before anything is appended.
                for record in (judge_row, *flags):
                    encode_line(record)
                trip: rc.Trip | None = None
                with self._lock:
                    self._store.append_judge(arm_id, card.id, judge_row, on_engine=judge.on_engine)
                    self._store.append_flags(flags)
                    if self._breaker is not None:
                        trip = self._breaker.feed_judgement(
                            rc.JudgeOutcome(
                                arm_id=arm_id,
                                card_id=card.id,
                                sample_ref=row.sample_ref,
                                status=result.status,
                                http_status=result.http_status,
                                cause=result.cause,
                            )
                        )
                if trip is not None and self._on_trip is not None:
                    self._on_trip(trip)
                if result.status in rc.FINAL_JUDGE_STATUSES:
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
    "PREFLIGHT_SYSTEM",
    "PREFLIGHT_USER",
    "judge_client",
    "judge_preflight",
    "needs_study_judge",
    "JudgeCall",
    "JudgePhase",
    "JudgeReply",
    "NeedsKeysError",
    "judge_id_for",
    "parse_score",
    "resolve_judge",
    "rubric_hash",
    "rubric_text",
    "sample_for_judge",
]
