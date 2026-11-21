"""The thin runner: ``Study → Arm → Card → items × epochs`` (spec §2.1, §2.5).

Per sample: the card's task module builds the messages, ``models.envelope``
builds the request body (the harness owns every ``qr_*`` key), the client
streams it, and the worker appends **one** ``GenerationRow`` plus the sample's
Tier 0 records, a score row and any taints — under one lock so the artifact
files of an ``(arm, card)`` are consistent even when several workers finish at
once. Events go to the injected :class:`~qr_llm_evals.runner.events.EventSink`;
the sink is the *only* difference between a viewed and a headless run.

Concurrency: one dispatcher thread per arm runs a sliding window over that
arm's jobs; a global ``BoundedSemaphore(max_inflight)`` caps in-flight
requests across arms (``QR_EVALS_MAX_INFLIGHT`` ≤ engine ``--max-num-seqs``
minus a reserve). Because each ``(arm, card)`` writes its own files, cross-arm
scheduling never changes any file's bytes; within an arm a window of 1 gives
dispatch order, a wider window completion order — readers join on
``sample_ref``, never row order.

Control (:class:`~qr_llm_evals.runner.control.RunControl`): dispatch needs an
explicit ``start``; pause gates new dispatch at the item boundary; abort trips
every in-flight :class:`~qr_llm_evals.client.cancel.CancelToken`, closing the
streams. Resume skips ``RunStore.completed_refs``.

Taints (AGENTS.md invariant 2): the entropy health endpoint is probed before
and after every qr-server sample; ``fallback_count`` delta > 0 ⇒
``engine_fallback``; ``currently_degraded`` (or an unreachable probe) on a QRNG
arm ⇒ ``qrng_degraded``; a stream error ⇒ ``stream_error``; a Tier 0
acquisition mismatch ⇒ ``stateless_row``. A tainted sample is never labelled
quantum.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

import httpx

from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.client.cancel import CancelToken
from qr_llm_evals.client.openai_client import Completion, OpenAIClient, UpstreamError
from qr_llm_evals.client.profiles import ProviderTarget, target_for
from qr_llm_evals.client.sse import ChatChunk
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.envelope import (
    TOP_LOGPROBS,
    SampleContext,
    build_envelope,
    build_request_body,
    check_top_logprobs,
    request_seed,
)
from qr_llm_evals.models.provenance import provenance_fields
from qr_llm_evals.models.providers import profile_for
from qr_llm_evals.models.schema import Arm, CardOverrides, ModelRef, StudyConfig
from qr_llm_evals.runner.control import RunControl, RunControlError
from qr_llm_evals.runner.events import EventSink, NullEventSink, RunEvent
from qr_llm_evals.runner.judge_phase import JudgePhase, NeedsKeysError
from qr_llm_evals.runner.progress import ProgressTracker
from qr_llm_evals.runner.tasks import (
    LoadedTask,
    effective_k,
    load_task_module,
    messages_hash,
    prompt_preview,
    select_items,
    told_conditions,
)
from qr_llm_evals.store.schema import (
    FinishReason,
    GenerationRow,
    GenerationWindow,
    Provenance,
    Run,
    RunState,
    Taint,
    Timing,
    ToldCondition,
    Usage,
)
from qr_llm_evals.store.store import RunStore
from qr_llm_evals.tier0.acquire import AcquisitionError, build_records, engine_log_path

logger = logging.getLogger("qr_llm_evals.runner")

ENTROPY_HEALTH_PATH = "/health/entropy"
#: ``sample.delta`` events are coalesced to at most this rate per stream (spec §4.8).
DELTA_MIN_INTERVAL_S = 0.05
#: Scorer id under ``scores/`` for the task module's own ``score()``.
TASK_SCORER = "task"

Clock = Callable[[], datetime]
Perf = Callable[[], float]
#: Supervised-mode hook: ``(job) -> (window, session_id)`` evaluated at dispatch.
WindowTagger = Callable[["SampleJob"], tuple[GenerationWindow, str | None]]


def _default_tagger(job: SampleJob) -> tuple[GenerationWindow, str | None]:
    return "unsupervised", None


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """The process-level knobs the runner needs (from ``Settings`` + readiness)."""

    vllm_origin: str
    max_inflight: int = 3
    priority: int = 0
    token_log_dir: str | None = None
    #: How long to wait for a sample's engine token log before using API logprobs.
    token_log_wait_s: float = 0.5
    determinism_tier: str = "none"
    repo_root: Path | None = None
    #: Per-token ``top_logprobs`` depth (``Settings.top_logprobs``); not a hard 20.
    top_logprobs: int = TOP_LOGPROBS


@dataclass(frozen=True, slots=True)
class EntropyStatus:
    rpc_ok: bool
    currently_degraded: bool
    fallback_count: int
    source_type: str | None = None


class EntropyProbe:
    """``GET /health/entropy`` on the shared engine; ``None`` when unreachable."""

    def __init__(
        self, origin: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 2.0
    ) -> None:
        self.url = origin.rstrip("/") + ENTROPY_HEALTH_PATH
        self._transport = transport
        self._timeout = timeout

    def probe(self) -> EntropyStatus | None:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                resp = client.get(self.url)
            if resp.status_code >= 400:
                return None
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        sampler = data.get("sampler")
        sampler = sampler if isinstance(sampler, dict) else {}
        # Deployed qr-sampler uses last_source_used and nested sampler health.
        source = data.get("last_source_used") or data.get("source_type")
        return EntropyStatus(
            rpc_ok=bool(data.get("rpc_ok", True)),
            currently_degraded=bool(
                data.get("currently_degraded", False) or sampler.get("currently_degraded", False)
            ),
            fallback_count=int(data.get("fallback_count", 0) or 0),
            source_type=None if source is None else str(source),
        )


@dataclass(frozen=True, slots=True)
class SampleJob:
    arm: Arm
    model: ModelRef
    card: EvalCard
    task: LoadedTask
    item: dict[str, Any]
    epoch: int
    told: ToldCondition | None
    ctx: SampleContext

    @property
    def sample_ref(self) -> str:
        return self.ctx.sample_ref(self.arm.id)


@dataclass(slots=True)
class SampleOutcome:
    finish_reason: FinishReason
    completed: bool
    aborted: bool
    taints: list[Taint] = field(default_factory=list)
    generation: GenerationRow | None = None


class DispatchHooks(Protocol):
    """Top-layer admission policy; None means wait without consuming engine capacity."""

    def admit(self, job: SampleJob) -> tuple[GenerationWindow, str | None] | None: ...

    def finished(self, job: SampleJob, outcome: SampleOutcome | None) -> None: ...


class _DeltaCoalescer:
    """Buffers ``sample.delta`` text/thinking and flushes at ≤ 1/``DELTA_MIN_INTERVAL_S`` Hz."""

    def __init__(self, emit: Callable[[RunEvent], None], sample_ref: str, perf: Perf) -> None:
        self._emit = emit
        self._ref = sample_ref
        self._perf = perf
        self._text: list[str] = []
        self._thinking: list[str] = []
        self._last = perf()
        self.first_token_at: datetime | None = None

    def absorb(self, chunk: ChatChunk, clock: Clock) -> None:
        if chunk.is_control:
            return
        if self.first_token_at is None:
            self.first_token_at = clock()
        if chunk.delta_text:
            self._text.append(chunk.delta_text)
        if chunk.delta_thinking:
            self._thinking.append(chunk.delta_thinking)
        now = self._perf()
        if now - self._last >= DELTA_MIN_INTERVAL_S:
            self.flush(now)

    def flush(self, now: float | None = None) -> None:
        if not self._text and not self._thinking:
            return
        data: dict[str, Any] = {"sample_ref": self._ref}
        if self._text:
            data["text"] = "".join(self._text)
        if self._thinking:
            data["thinking"] = "".join(self._thinking)
        self._text.clear()
        self._thinking.clear()
        self._last = now if now is not None else self._perf()
        self._emit(RunEvent("sample.delta", data))


def _qr_overrides(card: EvalCard, item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Card-level ``task_args.qr_overrides`` merged with an item's own ``qr_overrides``.

    An item may set sampling knobs for itself (the thesis's guesser warm-up asks for
    temperature 0.5 and 1.5 replies within one card). Both pass through
    :func:`~qr_llm_evals.models.envelope.validate_overrides`, so neither can touch the
    source, seed, amplifier or any key the harness owns.
    """
    merged = {
        **dict(card.task_args.get("qr_overrides") or {}),
        **dict(item.get("qr_overrides") or {}),
    }
    return merged or None


def _wait_for_file(path: Path, timeout_s: float) -> bool:
    """True once ``path`` exists, polling for at most ``timeout_s`` seconds."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    while not path.is_file():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


class Orchestrator:
    """Runs one :class:`Run` of a frozen study on the thin engine."""

    def __init__(
        self,
        study: StudyConfig,
        cards: Mapping[str, EvalCard],
        run_store: RunStore,
        config: RunnerConfig,
        *,
        secrets: SecretBag | None = None,
        sink: EventSink | None = None,
        control: RunControl | None = None,
        transport: httpx.BaseTransport | None = None,
        judge_transport: httpx.BaseTransport | None = None,
        entropy_probe: EntropyProbe | None = None,
        tagger: WindowTagger | None = None,
        dispatch_hooks: DispatchHooks | None = None,
        clock: Clock | None = None,
        perf: Perf | None = None,
        tier0_source: str | None = None,
        inspect_executor: Callable[[Orchestrator, Arm, EvalCard], None] | None = None,
    ) -> None:
        self.study = study
        self.cards = dict(cards)
        self.store = run_store
        self.config = config
        self.secrets = secrets if secrets is not None else SecretBag()
        self.sink = sink if sink is not None else NullEventSink()
        self.control = control if control is not None else RunControl()
        self._transport = transport
        self._judge_transport = judge_transport
        self._probe = (
            entropy_probe
            if entropy_probe is not None
            else EntropyProbe(config.vllm_origin, transport=transport)
        )
        self.tagger = tagger or _default_tagger
        # Replaced by the run's recorded depth once generation starts.
        self._top_logprobs = config.top_logprobs
        self.dispatch_hooks = dispatch_hooks
        self.clock: Clock = clock or (lambda: datetime.now(UTC))
        self.perf: Perf = perf or time.perf_counter
        self._tier0_source = tier0_source
        self._inspect_executor = inspect_executor
        self._store_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._inflight = threading.BoundedSemaphore(max(1, config.max_inflight))
        self._clients: dict[str, OpenAIClient] = {}
        self._tasks: dict[str, LoadedTask] = {}
        self._inspect_rows: dict[tuple[str, str], dict[str, GenerationRow]] = {}
        self._needs_keys: NeedsKeysError | None = None
        self._first_error: BaseException | None = None
        self.progress = ProgressTracker(run_store, self.sink, perf=self.perf)
        self.run_record: Run = run_store.read_run()

    # ── events / state ────────────────────────────────────────────────────

    def _emit(self, type_: str, **data: Any) -> None:
        try:
            self.sink.emit(RunEvent(type_, data))
        except Exception:  # noqa: BLE001 - a broken observer must never change results
            logger.warning("event sink raised on %s; ignoring", type_, exc_info=True)

    def _set_state(self, state: RunState, **extra: Any) -> None:
        with self._state_lock:
            self.run_record = self.run_record.model_copy(
                update={"state": state, "progress": self.progress.snapshot(), **extra}
            )
            with self._store_lock:
                self.store.write_run(self.run_record)
        self._emit(
            "run.state", run_id=self.run_record.run_id, state=state, at=self.clock().isoformat()
        )

    # ── resolution helpers ────────────────────────────────────────────────

    def _model_for(self, arm: Arm) -> ModelRef:
        for m in self.study.models:
            if m.id == arm.model:
                return m
        raise ValueError(f"arm {arm.id!r} references unknown model {arm.model!r}")

    def _task_for(self, card: EvalCard) -> LoadedTask:
        task = self._tasks.get(card.id)
        if task is None:
            task = load_task_module(card, self.config.repo_root)
            self._tasks[card.id] = task
        return task

    def _target_for(self, model: ModelRef) -> ProviderTarget:
        base_url = None
        if model.provider == "custom" and model.id.startswith("custom:"):
            base_url = model.id[len("custom:") :].split("#", 1)[0]
        return target_for(model.provider, base_url, vllm_origin=self.config.vllm_origin)

    def _client_for(self, model: ModelRef) -> OpenAIClient:
        client = self._clients.get(model.id)
        if client is None:
            target = self._target_for(model)
            api_key = None
            if target.needs_key:
                api_key = self.secrets.get(target.secret_name or "")
                if not api_key:
                    raise NeedsKeysError(target.secret_name or model.provider)
            client = OpenAIClient(
                target.base_url,
                api_key=api_key,
                extra_headers=target.extra_headers,
                transport=self._transport,
            )
            self._clients[model.id] = client
        return client

    def _overrides_for(self, card_id: str) -> CardOverrides:
        for sel in self.study.cards:
            if sel.card_id == card_id:
                return sel.overrides
        return CardOverrides()

    # ── planning ──────────────────────────────────────────────────────────

    def runnable_arms(self) -> list[Arm]:
        return [a for a in self.study.arms if a.availability == "available"]

    def thin_cards(self) -> list[EvalCard]:
        """Study cards in study order that the thin runner generates for."""
        out: list[EvalCard] = []
        for sel in self.study.cards:
            card = self.cards.get(sel.card_id)
            if card is None:
                raise ValueError(f"study selects unknown card {sel.card_id!r}")
            if card.engine == "thin" and card.generates and card.status == "implemented":
                out.append(card)
        return out

    def plan_arm(self, arm: Arm, cards: Sequence[EvalCard] | None = None) -> list[SampleJob]:
        """Every job of one arm in dispatch order, minus the already completed refs."""
        model = self._model_for(arm)
        jobs: list[SampleJob] = []
        for card in cards if cards is not None else self.thin_cards():
            task = self._task_for(card)
            overrides = self._overrides_for(card.id)
            items = select_items(
                task.build_items(
                    run_store=self.store,
                    arm_id=arm.id,
                    arm_ids=[candidate.id for candidate in self.runnable_arms()],
                ),
                card,
                overrides,
            )
            k = effective_k(card, overrides)
            tolds = told_conditions(card)
            total = len(items) * k * len(tolds)
            done = self.store.completed_refs(arm.id, card.id)
            self.progress.set_total(arm.id, card.id, total, done=0)
            n_done = 0
            for item in items:
                for epoch in range(k):
                    for told in tolds:
                        ctx = SampleContext(
                            card_id=card.id,
                            prompt_id=str(item["prompt_id"]),
                            epoch=epoch,
                            max_tokens=card.max_tokens,
                            thinking=card.thinking,
                            told_condition=told,
                        )
                        if ctx.sample_ref(arm.id) in done:
                            n_done += 1
                            continue
                        jobs.append(SampleJob(arm, model, card, task, dict(item), epoch, told, ctx))
            if n_done:
                self.progress.set_total(arm.id, card.id, total, done=n_done)
        return jobs

    def _run_thin_phase(self, arms: Sequence[Arm], cards: Sequence[EvalCard]) -> None:
        """Plan at phase start, then run all arms; derived phases therefore see stored prerequisites."""
        plans = {arm.id: self.plan_arm(arm, cards) for arm in arms}
        window = self.arm_window(len(arms))
        threads = [
            threading.Thread(
                target=self._arm_loop,
                args=(arm, plans[arm.id], window),
                name=f"arm-{arm.id}",
                daemon=True,
            )
            for arm in arms
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    # ── the run ───────────────────────────────────────────────────────────

    def _record_request_settings(self) -> None:
        """Write ``request_settings.json`` once; a resume keeps and uses the first snapshot.

        The analysis reads ``top_logprobs`` from it to label API-logprob endpoints
        and to report how many sampled tokens fell inside the returned top-k. A
        run never mixes depths: if the deployment setting changed since the run
        started, the run goes on with the depth it recorded.
        """
        depth = check_top_logprobs(self.config.top_logprobs)
        with self._store_lock:
            existing = self.store.read_request_settings()
            if existing is None:
                self.store.write_request_settings({"schema_version": 1, "top_logprobs": depth})
            elif existing.get("top_logprobs") != depth:
                recorded = check_top_logprobs(existing.get("top_logprobs"))  # type: ignore[arg-type]
                logger.warning(
                    "run %s started with top_logprobs=%s; the deployment now says %s; "
                    "the run keeps %s",
                    self.store.run_id,
                    recorded,
                    depth,
                    recorded,
                )
                depth = recorded
        self._top_logprobs = depth

    def run_generation(self) -> Run:
        """Generate every pending sample of every available arm; returns the updated run."""
        if not self.control.started:
            raise RunControlError("run_generation() needs an explicit RunControl.start()")
        arms = self.runnable_arms()
        cards = self.thin_cards()
        base_cards = [card for card in cards if card.dataset.scheme != "derived"]
        derived_cards = [card for card in cards if card.dataset.scheme == "derived"]
        self._record_request_settings()
        self._set_state("running")
        if not self.control.wait_while_paused():
            return self._finish_generation()

        self._run_thin_phase(arms, base_cards)
        # A top-layer composition root injects the optional engine, preserving
        # the no-layer-2-imports rule. Never silently complete selected Inspect cards.
        if not self.control.aborted and self._first_error is None and self._needs_keys is None:
            for selection in self.study.cards:
                card = self.cards[selection.card_id]
                if card.engine != "inspect" or card.status != "implemented":
                    continue
                if self._inspect_executor is None:
                    self._set_state("aborted")
                    raise RuntimeError("selected Inspect card requires an Inspect executor")
                for arm in arms:
                    try:
                        self._inspect_executor(self, arm, card)
                    except NeedsKeysError as err:
                        self._needs_keys = err
                        break
                    except Exception as err:
                        self._first_error = err
                        break
                if self._first_error is not None or self._needs_keys is not None:
                    break
        if not self.control.aborted and self._first_error is None and self._needs_keys is None:
            self._run_thin_phase(arms, derived_cards)
        return self._finish_generation()

    def dispatch_inspect(
        self, arm: Arm, card: EvalCard, ctx: SampleContext, messages: list[dict[str, str]]
    ) -> GenerationRow:
        """Inspect IPC adapter to the *same* dispatch/row/Tier-0 path as thin jobs."""
        if not self.control.started:
            raise RunControlError("Inspect dispatch needs explicit start")
        if self.control.paused:
            self._announce_paused()
        if not self.control.wait_while_paused():
            raise RunControlError("Inspect run aborted")
        self._announce_resumed()
        if ctx.card_id != card.id:
            raise ValueError("Inspect card/context mismatch")
        ref = ctx.sample_ref(arm.id)
        key = (arm.id, card.id)
        if key not in self._inspect_rows:
            self._inspect_rows[key] = {
                row.sample_ref: row
                for row in self.store.generations(*key)
                if row.finish_reason in ("stop", "length")
            }
        row = self._inspect_rows[key].get(ref)
        if row is not None:
            if row.messages_hash != messages_hash(messages):
                raise ValueError("Inspect resume prompt differs from stored generation")
            return row
        module = ModuleType("inspect_messages")
        module.build_messages = lambda item, told: messages  # type: ignore[attr-defined]
        module.score = lambda sample: {}  # type: ignore[attr-defined]
        task = LoadedTask(card, module, Path("<inspect>"))
        job = SampleJob(
            arm,
            self._model_for(arm),
            card,
            task,
            {"prompt_id": ctx.prompt_id},
            ctx.epoch,
            None,
            ctx,
        )
        outcome = self.dispatch(job)
        row = outcome.generation
        if row is None:
            raise RuntimeError("Inspect dispatch returned no generation row")
        if outcome.completed:
            self._inspect_rows[key][ref] = row
        return row

    def _finish_generation(self) -> Run:
        self.progress.flush()
        if self._first_error is not None:
            self._set_state("aborted")
            raise self._first_error
        if self._needs_keys is not None:
            self._set_state("needs_keys", needs_keys=True)
            return self.run_record
        if self.control.aborted:
            self._set_state("aborted")
        return self.run_record

    def run_judge(self) -> dict[str, int]:
        """Judge phase for every judged thin card (resumable by ``sample_ref``)."""
        phase = JudgePhase(
            self.store,
            self.study,
            self.secrets,
            vllm_origin=self.config.vllm_origin,
            sink=self.sink,
            transport=self._judge_transport,
            repo_root=self.config.repo_root,
            clock=self.clock,
            should_stop=lambda: self.control.aborted,
        )
        totals = {"judged": 0, "skipped": 0, "failed": 0}
        arm_ids = [a.id for a in self.runnable_arms()]
        for card in self.thin_cards():
            if card.judge.kind != "llm":
                continue
            counts = phase.judge_card(arm_ids, card, self._task_for(card))
            for k, v in counts.items():
                totals[k] += v
        return totals

    def run(self) -> Run:
        """Generation, then the judge phase, then ``complete`` (or ``aborted`` / ``needs_keys``)."""
        run = self.run_generation()
        if run.state != "running":
            return run
        try:
            self.run_judge()
        except NeedsKeysError as err:
            logger.info("judge phase needs keys: %s", err)
            self._set_state("needs_keys", needs_keys=True)
            return self.run_record
        if self.control.aborted:
            self._set_state("aborted")
        else:
            self._set_state("complete")
        return self.run_record

    # ── per-arm sliding window ────────────────────────────────────────────

    def arm_window(self, n_arms: int) -> int:
        """Per-arm sliding window: the global budget shared evenly, at least 1."""
        return max(1, self.config.max_inflight // max(1, n_arms))

    def _arm_loop(self, arm: Arm, jobs: Sequence[SampleJob], window: int = 1) -> None:
        queue = list(jobs)
        in_flight: list[threading.Thread] = []
        arm_slots = threading.BoundedSemaphore(window)
        paused_announced = False
        try:
            while queue:
                if self.control.aborted or self._first_error is not None or self._needs_keys:
                    break
                if self.control.paused:
                    if not paused_announced:
                        paused_announced = True
                        self._announce_paused()
                    if not self.control.wait_while_paused():
                        break
                    paused_announced = False
                    self._announce_resumed()
                    continue
                arm_slots.acquire()
                if self.control.aborted or self.control.paused:
                    arm_slots.release()
                    continue
                job = queue.pop(0)
                worker = threading.Thread(
                    target=self._worker,
                    args=(job, arm_slots),
                    name=f"sample-{job.sample_ref}",
                    daemon=True,
                )
                in_flight.append(worker)
                worker.start()
                in_flight = [t for t in in_flight if t.is_alive()]
        finally:
            for t in in_flight:
                t.join()

    def _announce_paused(self) -> None:
        with self._state_lock:
            already = self.run_record.state == "paused"
        if not already:
            self._set_state("paused")

    def _announce_resumed(self) -> None:
        with self._state_lock:
            already = self.run_record.state == "running"
        if not already and not self.control.aborted:
            self._set_state("running")

    def _worker(self, job: SampleJob, arm_slots: threading.BoundedSemaphore) -> None:
        try:
            outcome = self.dispatch(job)
            self.progress.record(
                job.arm.id, job.card.id, completed=outcome.completed, aborted=outcome.aborted
            )
        except NeedsKeysError as err:
            self._needs_keys = err
            self._emit("error", sample_ref=job.sample_ref, message=str(err), code="needs_keys")
        except Exception as err:  # noqa: BLE001 - recorded, run stops submitting
            logger.exception("sample %s failed", job.sample_ref)
            if self._first_error is None:
                self._first_error = err
            self._emit("error", sample_ref=job.sample_ref, message=f"{type(err).__name__}: {err}")
        finally:
            arm_slots.release()

    # ── one sample ────────────────────────────────────────────────────────

    def dispatch(self, job: SampleJob) -> SampleOutcome:
        """Generate one sample and persist its row, Tier 0 records, score and taints."""
        while not self.control.aborted and self._first_error is None and self._needs_keys is None:
            if self.control.paused:
                self._announce_paused()
                if not self.control.wait_while_paused():
                    break
                self._announce_resumed()
            if not self._inflight.acquire(timeout=0.05):
                continue
            admitted = False
            outcome = None
            try:
                if self.control.aborted or self.control.paused:
                    continue
                tags = (
                    self.dispatch_hooks.admit(job)
                    if self.dispatch_hooks is not None
                    else self.tagger(job)
                )
                if tags is not None:
                    admitted = True
                    outcome = self._dispatch(job, tags)
                    return outcome
            finally:
                try:
                    if admitted and self.dispatch_hooks is not None:
                        self.dispatch_hooks.finished(job, outcome)
                finally:
                    self._inflight.release()
            time.sleep(0.05)
        return SampleOutcome(finish_reason="abort", completed=False, aborted=True)

    def _dispatch(self, job: SampleJob, tags: tuple[GenerationWindow, str | None]) -> SampleOutcome:
        arm, model, card, ctx = job.arm, job.model, job.card, job.ctx
        sample_ref = job.sample_ref
        window, session_id = tags
        client = self._client_for(model)
        messages = job.task.build_messages(job.item, job.told)
        qr_overrides = _qr_overrides(card, job.item) if model.provider == "qr_server" else None
        profile = profile_for(model.provider) if model.provider != "qr_server" else None
        body = build_request_body(
            arm,
            model,
            ctx,
            messages,
            priority=self.config.priority,
            overrides=qr_overrides,
            profile=profile,
            top_logprobs=self._top_logprobs,
            token_log=bool(self.config.token_log_dir),
        )
        envelope: Mapping[str, Any] = (
            build_envelope(arm, ctx, qr_overrides) if model.provider == "qr_server" else {}
        )
        qr_seed = request_seed(arm, ctx) if model.provider == "qr_server" else None
        is_qr = model.provider == "qr_server"

        self._emit(
            "sample.started",
            sample_ref=sample_ref,
            arm_id=arm.id,
            card_id=card.id,
            prompt_preview=prompt_preview(messages),
            window=window,
        )
        before = self._probe.probe() if is_qr else None
        started = self.clock()
        t0 = self.perf()
        coalescer = _DeltaCoalescer(self.sink.emit, sample_ref, self.perf)
        taints: list[Taint] = []
        error_text: str | None = None
        completion: Completion
        with self.control.in_flight() as cancel:
            try:
                completion = client.generate(
                    body, on_delta=lambda c: coalescer.absorb(c, self.clock), cancel=cancel
                )
            except (UpstreamError, httpx.HTTPError) as err:
                completion = Completion(finish_reason="error")
                error_text = f"{type(err).__name__}: {err}"
        coalescer.flush()
        finished = self.clock()
        elapsed = self.perf() - t0
        after = self._probe.probe() if is_qr else None

        finish_reason = self._finish_reason(completion, cancel, error_text)
        if error_text is not None:
            taints.append(
                Taint(sample_ref=sample_ref, kind="stream_error", detail=error_text, at=finished)
            )
        fallback_delta, degraded, last_source = self._entropy_verdict(
            arm, before, after, sample_ref, finished, taints
        )
        provenance = Provenance.model_validate(
            provenance_fields(
                arm,
                model,
                envelope,
                qr_seed=qr_seed,
                pipeline_pinned=is_qr,
                determinism_tier=self.config.determinism_tier,
                engine_fallback_delta=fallback_delta,
                engine_degraded=degraded,
                engine_last_source=last_source,
            )
        )
        usage = self._usage(completion)
        tps = usage.completion_tokens / elapsed if elapsed > 0 and usage.completion_tokens else None
        row = GenerationRow(
            sample_ref=sample_ref,
            arm_id=arm.id,
            card_id=card.id,
            prompt_id=ctx.prompt_id,
            epoch=ctx.epoch,
            told_condition=job.told,
            messages_hash=messages_hash(messages),
            output=completion.text,
            thinking=completion.thinking or None,
            finish_reason=finish_reason,
            usage=usage,
            provenance=provenance,
            timing=Timing(
                started=started,
                first_token=coalescer.first_token_at,
                finished=finished,
                tps=tps,
            ),
            session_id=session_id,
            window=window,
            qr_seed=qr_seed,
            request_id=str(body.get("request_id") or sample_ref),
        )
        completed = finish_reason in ("stop", "length")
        tier0_records = self._tier0(
            job, completion, finished, session_id, window, taints, completed
        )
        score_row: dict[str, Any] | None = None
        if completed:
            score_row = self._score(job, completion, tier0_records)

        with self._store_lock:
            self.store.append_generation(row)
            if tier0_records:
                self.store.append_tier0(arm.id, card.id, tier0_records)
            if score_row is not None:
                self.store.append_score(arm.id, card.id, TASK_SCORER, score_row)
            for taint in taints:
                self.store.append_taint(taint)
        for taint in taints:
            self._emit("taint", **taint.model_dump(mode="json"))
        self._emit(
            "sample.finished",
            sample_ref=sample_ref,
            finish_reason=finish_reason,
            tokens=usage.completion_tokens,
            tps=tps,
            provenance=provenance.model_dump(mode="json"),
        )
        return SampleOutcome(
            finish_reason=finish_reason,
            completed=completed,
            aborted=finish_reason == "abort",
            taints=taints,
            generation=row,
        )

    @staticmethod
    def _finish_reason(
        completion: Completion, cancel: CancelToken, error_text: str | None
    ) -> FinishReason:
        if error_text is not None:
            return "error"
        if completion.cancelled or cancel.cancelled:
            return "abort"
        if completion.finish_reason == "length":
            return "length"
        if (
            completion.finish_reason == "stop"
            or completion.finish_reason is None
            and completion.text
        ):
            return "stop"
        return "stop" if completion.finish_reason in (None, "stop") else "error"

    @staticmethod
    def _usage(completion: Completion) -> Usage:
        raw = completion.usage or {}
        details = raw.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        completion_tokens = raw.get("completion_tokens")
        if not isinstance(completion_tokens, int):
            completion_tokens = len(completion.logprobs)
        return Usage(
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(completion_tokens),
            reasoning_tokens=int(reasoning) if isinstance(reasoning, int) else None,
        )

    def _entropy_verdict(
        self,
        arm: Arm,
        before: EntropyStatus | None,
        after: EntropyStatus | None,
        sample_ref: str,
        at: datetime,
        taints: list[Taint],
    ) -> tuple[int, bool, str | None]:
        """``(fallback_delta, degraded, last_source)`` plus the taints they imply."""
        if before is None and after is None:
            if arm.source == "qrng":
                taints.append(
                    Taint(
                        sample_ref=sample_ref,
                        kind="qrng_degraded",
                        detail="entropy health probe unavailable; quantum claim withheld",
                        at=at,
                    )
                )
                return 0, True, None
            return 0, False, None
        delta = 0
        if before is not None and after is not None:
            delta = max(0, after.fallback_count - before.fallback_count)
        degraded = any(s.currently_degraded or not s.rpc_ok for s in (before, after) if s)
        latest = after if after is not None else before
        last_source = latest.source_type if latest is not None else None
        if delta > 0:
            taints.append(
                Taint(
                    sample_ref=sample_ref,
                    kind="engine_fallback",
                    detail=f"engine fallback_count rose by {delta} during the sample",
                    at=at,
                )
            )
        if degraded and arm.source == "qrng":
            taints.append(
                Taint(
                    sample_ref=sample_ref,
                    kind="qrng_degraded",
                    detail="engine reported currently_degraded during the sample",
                    at=at,
                )
            )
        return delta, degraded, last_source

    def _tier0(
        self,
        job: SampleJob,
        completion: Completion,
        finished: datetime,
        session_id: str | None,
        window: GenerationWindow,
        taints: list[Taint],
        completed: bool,
    ) -> list[Any]:
        if not completed or not completion.logprobs and not self.config.token_log_dir:
            return []
        log_path: Path | None = None
        if self.config.token_log_dir:
            candidate = engine_log_path(Path(self.config.token_log_dir), job.sample_ref)
            # The engine creates the file when the request starts and flushes a
            # row per token, so it is normally complete by now; the short wait
            # covers a slow shared filesystem.
            if _wait_for_file(candidate, self.config.token_log_wait_s):
                log_path = candidate
        try:
            return build_records(
                job.sample_ref,
                completion.logprobs,
                log_path,
                timestamp_ns=int(finished.timestamp() * 1_000_000_000),
                session_id=session_id,
                window=window,
            )
        except AcquisitionError as err:
            taints.append(
                Taint(sample_ref=job.sample_ref, kind="stateless_row", detail=str(err), at=finished)
            )
            return []

    def _score(
        self, job: SampleJob, completion: Completion, tier0_records: Sequence[Any]
    ) -> dict[str, Any] | None:
        sample = {
            "sample_ref": job.sample_ref,
            "item": job.item,
            "told": job.told,
            "text": completion.text,
            "thinking": completion.thinking or None,
            "tier0": [r.model_dump(mode="json") for r in tier0_records],
        }
        try:
            scores = job.task.score(sample)
        except Exception as err:  # noqa: BLE001 - a scorer bug must not lose the generation
            logger.warning("score() failed for %s: %s", job.sample_ref, err)
            return {"sample_ref": job.sample_ref, "error": f"{type(err).__name__}: {err}"}
        return {"sample_ref": job.sample_ref, **scores}


def run_headless(
    study: StudyConfig,
    cards: Mapping[str, EvalCard],
    run_store: RunStore,
    config: RunnerConfig,
    *,
    secrets: SecretBag | None = None,
    transport: httpx.BaseTransport | None = None,
    judge_transport: httpx.BaseTransport | None = None,
    **kwargs: Any,
) -> Run:
    """Convenience: start a fresh control, run generation + judge, no events."""
    control = RunControl()
    control.start()
    orch = Orchestrator(
        study,
        cards,
        run_store,
        config,
        secrets=secrets,
        sink=NullEventSink(),
        control=control,
        transport=transport,
        judge_transport=judge_transport,
        **kwargs,
    )
    return orch.run()


def iter_sample_refs(jobs: Iterable[SampleJob]) -> list[str]:
    return [j.sample_ref for j in jobs]


__all__ = [
    "DELTA_MIN_INTERVAL_S",
    "ENTROPY_HEALTH_PATH",
    "TASK_SCORER",
    "EntropyProbe",
    "EntropyStatus",
    "Orchestrator",
    "RunnerConfig",
    "SampleJob",
    "SampleOutcome",
    "WindowTagger",
    "iter_sample_refs",
    "run_headless",
]
