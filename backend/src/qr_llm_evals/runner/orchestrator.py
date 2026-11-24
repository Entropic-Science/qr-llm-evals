"""The thin runner: ``Study → Arm → Card → items × epochs`` (spec §2.1, §2.5).

Per sample: the card's task module builds the messages, ``models.envelope``
builds the request body (the harness owns every ``qr_*`` key), the client
streams it, and the worker appends the sample's taints, **one**
``GenerationRow``, its Tier 0 records, a score row and its result-check flags
— under one lock so the artifact files of an ``(arm, card)`` are consistent
even when several workers finish at once. Events go to the injected
:class:`~qr_llm_evals.runner.events.EventSink`; the sink is the *only*
difference between a viewed and a headless run.

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

Taints (AGENTS.md invariant 2) exclude a sample from the clean analysis:

* ``stream_error``: an HTTP or transport error, a frame that is not valid
  JSON, or a stream that ended without a finish reason. The row's finish
  reason is ``error``; it has no score and is tried again on resume. An
  attempt that did not complete writes this taint and no other.
* ``engine_fallback``: the engine's ``fallback_count`` rose during the sample,
  or one of the sample's token records says the engine drew from its fallback
  source.
* ``qrng_degraded``: on a QRNG arm, the entropy health endpoint reported the
  source degraded around the sample, or could not be reached.
* ``stateless_row``: the sample's token records could not be built from what
  the engine and the API returned.

A tainted sample is never labelled quantum.

Result checks (``cards/result_checks.py``): every completed sample is looked
at once and what is found is written to ``flags.jsonl``. A flag excludes
nothing. Every dispatched sample is also fed to the run's ``Breaker``. When a
stop rule fires the Breaker latches: the loops take no more work, samples in
flight finish, one ``Halt`` is stored in ``run.json``, one ``check.stopped``
event is emitted and the orchestrator returns with the run ``paused``. Nothing
is removed and nothing resumes by itself. The host's resume builds a new
orchestrator, which stamps the halt as resumed, plans again from the store (so
a sample without a completed row is tried again) and builds a new Breaker from
the stored halts.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

import httpx

from qr_llm_evals.cards import result_checks as rc
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.client.cancel import CancelToken
from qr_llm_evals.client.openai_client import Completion, OpenAIClient, UpstreamError
from qr_llm_evals.client.profiles import ProviderTarget, target_for
from qr_llm_evals.client.sse import ChatChunk, MalformedFrameError
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.envelope import (
    TOP_LOGPROBS,
    EnvelopeError,
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
from qr_llm_evals.runner.judge_phase import (
    JudgePhase,
    NeedsKeysError,
    judge_id_for,
    judge_preflight,
    needs_study_judge,
)
from qr_llm_evals.runner.progress import ProgressTracker
from qr_llm_evals.runner.tasks import (
    LoadedTask,
    effective_k,
    effective_limit,
    load_task_module,
    messages_hash,
    prompt_preview,
    select_items,
    told_conditions,
)
from qr_llm_evals.store.artifacts import encode_line, iter_jsonl
from qr_llm_evals.store.schema import (
    FinishReason,
    GenerationRow,
    GenerationWindow,
    Halt,
    Provenance,
    ResultFlag,
    Run,
    RunState,
    Taint,
    Timing,
    ToldCondition,
    Usage,
)
from qr_llm_evals.store.store import RunStore
from qr_llm_evals.tier0.acquire import (
    AcquisitionError,
    MissingLogprobError,
    build_records,
    engine_log_path,
)
from qr_llm_evals.tier0.schema import Tier0Record

logger = logging.getLogger("qr_llm_evals.runner")

ENTROPY_HEALTH_PATH = "/health/entropy"
#: ``sample.delta`` events are coalesced to at most this rate per stream (spec §4.8).
DELTA_MIN_INTERVAL_S = 0.05
#: Scorer id under ``scores/`` for the task module's own ``score()``.
TASK_SCORER = "task"
#: A flag's detail is harness-written and short; the record refuses more.
FLAG_DETAIL_MAX = 300

Clock = Callable[[], datetime]
Perf = Callable[[], float]
#: Supervised-mode hook: ``(job) -> (window, session_id)`` evaluated at dispatch.
WindowTagger = Callable[["SampleJob"], tuple[GenerationWindow, str | None]]


def _default_tagger(job: SampleJob) -> tuple[GenerationWindow, str | None]:
    return "unsupervised", None


class CheckStopped(RunControlError):
    """A result check stopped the run while an Inspect card was being generated.

    Raised into the Inspect bridge, which closes its child process and keeps
    every artifact. The orchestrator turns it into a pause, never an abort.
    """


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
    #: What the result checks found. A flag excludes nothing.
    flags: list[ResultFlag] = field(default_factory=list)
    #: The harness's own name for why the sample did not complete, or why its
    #: scorer failed: an HTTP status or a class name, never upstream text.
    cause: str | None = None


class DispatchHooks(Protocol):
    """Top-layer admission policy; None means wait without consuming engine capacity."""

    def admit(self, job: SampleJob) -> tuple[GenerationWindow, str | None] | None: ...

    def finished(self, job: SampleJob, outcome: SampleOutcome | None) -> None: ...

    def stopped(self) -> None:
        """A result check stopped the run: end what depends on it, as a host pause would."""
        ...


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


def _asked_for_token_log(body: Mapping[str, Any]) -> bool:
    """True when this request asked the engine for its per-token log.

    Only a qr-server arm that is not greedy asks. Deciding from the request,
    not from the deployment setting, keeps a greedy or provider arm from being
    flagged for a log it never asked for.
    """
    xargs = body.get("vllm_xargs")
    return isinstance(xargs, Mapping) and xargs.get("qr_token_log") is True


def _probe_bad(status: EntropyStatus | None) -> bool:
    return status is None or status.currently_degraded or not status.rpc_ok


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
        # A run that was never started gets the version of the checks it starts
        # under. A run made before result checks keeps none, so its summary can
        # say that its earlier samples were never looked at.
        self._first_start = self.run_record.state in ("created", "ready")
        # One Breaker per orchestrator, built from the stored halts: which
        # one-shot rules are spent survives a resume and a restart, every
        # count starts again.
        self._breaker = rc.Breaker(
            qrng_arms=[arm.id for arm in study.arms if arm.source == "qrng"],
            judged_cards=[card.id for card in self.cards.values() if card.judge.kind == "llm"],
            halts=self.run_record.halts,
        )
        self._halt: Halt | None = None
        self._halt_lock = threading.Lock()
        #: The last few samples that were tried and have no completed row.
        self._incomplete_refs: deque[str] = deque(maxlen=rc.HALT_SAMPLE_REFS)
        #: Samples of this orchestrator that ended on a finish reason the harness
        #: does not map, by arm and card. They are not failed generations.
        self._unmapped: dict[tuple[str, str], set[str]] = {}
        self._unmapped_lock = threading.Lock()
        #: ``(sample_ref, cause)`` of an Inspect sample that did not complete.
        self._inspect_incomplete: tuple[str, str | None] | None = None
        # Jobs left per (arm, card) and arms left per card in the phase that is
        # running: a card's queue has ended when every arm is through it.
        self._cards_lock = threading.Lock()
        self._jobs_left: dict[tuple[str, str], int] = {}
        self._arms_left: dict[str, int] = {}

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

    # ── result checks: the stop latch ─────────────────────────────────────

    @property
    def stopped(self) -> bool:
        """True once a result check stopped the run. The loops take no more work."""
        return self._breaker.tripped is not None

    @property
    def halt(self) -> Halt | None:
        """The halt this orchestrator stored, if a check stopped it."""
        return self._halt

    def _note_trip(self, trip: rc.Trip | None) -> None:
        """Store the halt of a rule that fired and tell the observers. Once.

        Called from whichever thread fed the Breaker. The halt is written at
        once, so it is on disk while samples in flight are still finishing;
        the state changes to ``paused`` when the orchestrator returns.
        """
        if trip is None:
            return
        with self._halt_lock:
            if self._halt is not None:
                return
            halt = Halt(at=self.clock(), **trip.halt_fields())
            self._halt = halt
        with self._state_lock:
            # The record this orchestrator holds is the one it writes: a halt
            # appended to a fresh read would be lost at the next state change.
            self.run_record = self.run_record.with_halt(halt)
            with self._store_lock:
                self.store.write_run(self.run_record)
        logger.warning(
            "run %s stopped by a result check: %s",
            self.store.run_id,
            halt.rule,
            extra={"event": "run.check_stopped", "run_id": self.store.run_id, "rule": halt.rule},
        )
        self._emit("check.stopped", run_id=self.store.run_id, **halt.model_dump(mode="json"))
        hooks = self.dispatch_hooks
        if hooks is not None:
            try:
                hooks.stopped()
            except Exception:  # noqa: BLE001 - a session that will not stop must not undo the halt
                logger.warning("dispatch hook raised on a check stop; ignoring", exc_info=True)

    def _may_go_on(self) -> bool:
        """False once anything has ended the run's generation."""
        return (
            not self.control.aborted
            and self._first_error is None
            and self._needs_keys is None
            and not self.stopped
        )

    def _wait_while_paused(self) -> bool:
        """Wait at a boundary while the host has paused; False when no more work is taken."""
        return self.control.wait_while_paused(stop=lambda: self.stopped) and not self.stopped

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
            max_tokens = effective_limit(card, overrides)
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
                            max_tokens=max_tokens,
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
        jobs_left: dict[tuple[str, str], int] = {}
        for arm in arms:
            for job in plans[arm.id]:
                key = (arm.id, job.card.id)
                jobs_left[key] = jobs_left.get(key, 0) + 1
        with self._cards_lock:
            self._jobs_left = jobs_left
            self._arms_left = {
                card.id: sum(1 for arm in arms if jobs_left.get((arm.id, card.id), 0) > 0)
                for card in cards
            }
            ended = [card.id for card in cards if self._arms_left[card.id] == 0]
        # A card with nothing left to generate has reached the end of its queue.
        for card_id in ended:
            self._note_trip(self._breaker.end_of_card(card_id))
        if self.stopped:
            return
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

    def _job_done(self, job: SampleJob, outcome: SampleOutcome | None) -> None:
        """Count a dispatched job off its arm and card; say so when the card's queue has ended."""
        if outcome is None or outcome.generation is None:
            return  # never dispatched (abort, missing key or a stop): the queue has not ended
        with self._cards_lock:
            key = (job.arm.id, job.card.id)
            left = self._jobs_left.get(key, 0) - 1
            self._jobs_left[key] = left
            if left != 0:
                return
            self._arms_left[job.card.id] = self._arms_left.get(job.card.id, 0) - 1
            card_ended = self._arms_left[job.card.id] == 0
        if card_ended:
            self._note_trip(self._breaker.end_of_card(job.card.id))

    # ── refilling the Breaker's windows from the store ────────────────────

    def _seed_scored(self, arms: Sequence[Arm], cards: Sequence[EvalCard]) -> None:
        """Give the Breaker the scored samples already stored for each unjudged card.

        ``nothing_parsed`` reads a card's scored samples from its first up to
        the first one that parsed. Counting them from the stored rows means a
        resume or a restart does not move that window. Rows of different arms
        are put in the order their generations finished. Of each arm the rows
        up to its first parsed one are read: a later row cannot come before
        the sample that ends the window.
        """
        for card in cards:
            if card.judge.kind == "llm" or self._breaker.spent("nothing_parsed", card.id):
                continue
            found: list[tuple[str, int, int, str, str, bool]] = []
            for arm_index, arm in enumerate(arms):
                files = self.store.arm_card(arm.id, card.id)
                scored: list[tuple[str, bool]] = []
                for row in iter_jsonl(files.scores_jsonl(TASK_SCORER)):
                    ref = row.get("sample_ref")
                    if not isinstance(ref, str) or "error" in row:
                        continue  # a sample whose scorer failed is not a scored sample
                    parsed = rc.unparsed(card.id, row) is None
                    scored.append((ref, parsed))
                    if parsed:
                        break
                if not scored:
                    continue
                wanted = {ref for ref, _ in scored}
                meta: dict[str, tuple[str, str]] = {}
                for gen in iter_jsonl(files.generations_jsonl):
                    ref = gen.get("sample_ref")
                    if ref in wanted and gen.get("finish_reason") in rc.COMPLETED_FINISH_REASONS:
                        timing = gen.get("timing") or {}
                        meta[ref] = (str(timing.get("finished") or ""), str(gen.get("prompt_id")))
                        if len(meta) == len(wanted):
                            break
                for index, (ref, parsed) in enumerate(scored):
                    finished, prompt_id = meta.get(ref, ("", ref))
                    found.append((finished, arm_index, index, ref, prompt_id, parsed))
            # The Breaker takes what still counts and ignores the rest.
            for _, _, _, ref, prompt_id, parsed in sorted(found):
                self._breaker.seed_scored(card.id, prompt_id, ref, parsed=parsed)

    def _seed_judged(self, arm_ids: Sequence[str], cards: Sequence[EvalCard]) -> None:
        """Give the Breaker the first judge replies already stored for each judged card."""
        for card in cards:
            judge_id = judge_id_for(card, self.study.judge)
            found: list[tuple[str, int, int, str, bool]] = []
            for arm_index, arm_id in enumerate(arm_ids):
                path = self.store.arm_card(arm_id, card.id).judge_jsonl(judge_id)
                kept = 0
                for row in iter_jsonl(path):
                    ref = row.get("sample_ref")
                    status = rc.stored_judge_status(row)
                    if not isinstance(ref, str) or status not in rc.FINAL_JUDGE_STATUSES:
                        continue  # no reply came for this row
                    found.append(
                        (str(row.get("at") or ""), arm_index, kept, ref, status != "unreadable")
                    )
                    kept += 1
                    if kept >= rc.NOTHING_JUDGED_FIRST:
                        break
            for _, _, _, ref, readable in sorted(found)[: rc.NOTHING_JUDGED_FIRST]:
                self._breaker.seed_judged(card.id, ref, readable=readable)

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

    def _note_envelope_change(self, arms: list[Arm]) -> None:
        """On a resume, say in the log when an arm's envelope is not the one readiness accepted.

        The harness builds the envelope, not the frozen study, so a run that
        started under one version of the harness and is resumed under another
        can send a different one (a pin the harness began to send, for one).
        The run goes on. Each generation row holds the hash of the envelope it
        was sent with, and that is what tells the two parts of such a run apart.
        """
        if self._first_start:
            return
        try:
            report = self.store.read_readiness()
        except (OSError, ValueError):
            return
        if report is None:
            return
        ctx = SampleContext("dry_run", "dry_run", 0, 1)
        for arm in arms:
            recorded = report.arms[arm.id].resolved_qr_args if arm.id in report.arms else {}
            if not recorded:
                continue  # a provider arm, or an arm readiness built no envelope for
            try:
                current = build_envelope(arm, ctx, conditioning=self.study.conditioning)
            except EnvelopeError:
                continue
            # The seed is derived for each request; every other key is the arm's.
            changed = sorted(
                key
                for key in (set(current) | set(recorded)) - {"qr_seed"}
                if key not in current or key not in recorded or current[key] != recorded[key]
            )
            if changed:
                logger.warning(
                    "run %s, arm %s: the sampling envelope differs from the one this run's "
                    "readiness check recorded, on %s; the run goes on with the current "
                    "envelope, and each generation row records the hash of the one it was "
                    "sent with",
                    self.store.run_id,
                    arm.id,
                    changed,
                )

    def _start_fields(self) -> dict[str, Any]:
        """What a starting orchestrator records beside the state ``running``.

        The host started or resumed this run, so every halt still open is
        stamped as resumed. This is the only place that stamp is written: a
        resume that only lifts a host or quota pause never gets here.
        """
        fields: dict[str, Any] = {}
        if self.run_record.open_halts():
            fields["halts"] = self.run_record.with_halts_resumed(self.clock()).halts
        if self._first_start and self.run_record.checks_version is None:
            fields["checks_version"] = rc.CHECKS_VERSION
        return fields

    def _judge_preflight(self) -> None:
        """Before the first generation: ask the study's judge one small thing.

        A key, a model name or a setting the provider refuses would otherwise
        show only after every sample was generated. Skipped when none of the
        study's evals is scored by the study judge or the run holds no judge key.

        Only a status that refuses the request stops the run. Whatever else
        happens to this one request (no answer, an answer that cannot be read,
        an error nobody expected) says nothing about the key or the settings:
        the run starts, and a judge that really cannot be used is caught in
        the judge phase. Nothing here may leave the run ``running`` with no
        thread behind it.
        """
        selected = [self.cards[s.card_id] for s in self.study.cards if s.card_id in self.cards]
        if not needs_study_judge(selected):
            return
        try:
            status = judge_preflight(
                self.study.judge,
                self.secrets,
                run_id=self.store.run_id,
                transport=self._judge_transport,
            )
        except Exception as err:  # noqa: BLE001 - inconclusive, never fatal
            # The message may quote the answer; the log names the class only.
            logger.warning(
                "run %s: the judge request before the first generation failed with %s; "
                "the run starts without its answer",
                self.store.run_id,
                type(err).__name__,
            )
            return
        self._note_trip(self._breaker.judge_preflight(status))

    def run_generation(self) -> Run:
        """Generate every pending sample of every available arm; returns the updated run."""
        if not self.control.started:
            raise RunControlError("run_generation() needs an explicit RunControl.start()")
        arms = self.runnable_arms()
        cards = self.thin_cards()
        base_cards = [card for card in cards if card.dataset.scheme != "derived"]
        derived_cards = [card for card in cards if card.dataset.scheme == "derived"]
        self._record_request_settings()
        self._note_envelope_change(arms)
        self._set_state("running", **self._start_fields())
        if not self._wait_while_paused():
            return self._finish_generation()
        self._judge_preflight()
        if self.stopped:
            return self._finish_generation()
        self._seed_scored(arms, cards)

        self._run_thin_phase(arms, base_cards)
        # A top-layer composition root injects the optional engine, preserving
        # the no-layer-2-imports rule. Never silently complete selected Inspect cards.
        if self._may_go_on():
            for selection in self.study.cards:
                card = self.cards[selection.card_id]
                if card.engine != "inspect" or card.status != "implemented":
                    continue
                if self._inspect_executor is None:
                    self._set_state("aborted")
                    raise RuntimeError("selected Inspect card requires an Inspect executor")
                for arm in arms:
                    self._inspect_incomplete = None
                    try:
                        self._inspect_executor(self, arm, card)
                    except NeedsKeysError as err:
                        self._needs_keys = err
                        break
                    except Exception as err:  # noqa: BLE001 - a child failure, or a stop
                        incomplete = self._inspect_incomplete
                        if incomplete is not None and not self.control.aborted:
                            # Inspect cannot go on past a sample without a reply.
                            # That is a stop the host can resume, not an abort.
                            ref, cause = incomplete
                            self._note_trip(self._breaker.inspect_incomplete(arm.id, ref, cause))
                        if not self.stopped:
                            self._first_error = err
                        break
                    if self.stopped:
                        break
                if not self._may_go_on():
                    break
        if self._may_go_on():
            self._run_thin_phase(arms, derived_cards)
        if self._may_go_on():
            self._note_trip(
                self._breaker.end_of_phase(
                    "generation", self._generation_cells(), tuple(self._incomplete_refs)
                )
            )
        return self._finish_generation()

    def _generation_cells(self) -> list[rc.PhaseCell]:
        """Planned and completed samples of every arm and card this orchestrator planned.

        A sample that ended on a finish reason of the provider the harness
        does not map (a content filter, a tool call) is not a failed
        generation for the stop rules, so it is not one of the planned samples
        that ``unfinished`` may find missing: stopping for it would make how a
        reply ended select which samples are generated again.
        """
        with self._unmapped_lock:
            unmapped = {key: len(refs) for key, refs in self._unmapped.items()}
        cells: list[rc.PhaseCell] = []
        for arm_id, arm in self.progress.snapshot().by_arm.items():
            for card_id, cell in arm.by_card.items():
                planned = cell.total - unmapped.get((arm_id, card_id), 0)
                if planned > 0:
                    cells.append(rc.PhaseCell(arm_id, card_id, planned, min(cell.done, planned)))
        return cells

    def dispatch_inspect(
        self, arm: Arm, card: EvalCard, ctx: SampleContext, messages: list[dict[str, str]]
    ) -> GenerationRow:
        """Inspect IPC adapter to the *same* dispatch/row/Tier-0 path as thin jobs.

        Raises :class:`CheckStopped` when a result check has stopped the run,
        and when this sample did not complete: Inspect cannot go on without
        it, so the bridge closes its child and the run pauses.
        """
        if not self.control.started:
            raise RunControlError("Inspect dispatch needs explicit start")
        if self.stopped:
            raise CheckStopped("a result check stopped the run")
        if self.control.paused:
            self._announce_paused()
        if not self._wait_while_paused():
            if self.stopped:
                raise CheckStopped("a result check stopped the run")
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
        if outcome.completed and row is not None:
            self._inspect_rows[key][ref] = row
            return row
        if row is not None and not outcome.aborted:
            self._inspect_incomplete = (ref, outcome.cause)
            raise CheckStopped("an Inspect sample did not complete")
        if self.stopped:
            raise CheckStopped("a result check stopped the run")
        if row is None:
            raise RuntimeError("Inspect dispatch returned no generation row")
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
        elif self.stopped:
            # A stop is a pause with a stored reason: the halt is in run.json
            # already, and only a host resume goes on from here.
            self._set_state("paused")
        return self.run_record

    def run_judge(self) -> dict[str, int]:
        """Judge phase for every judged thin card (resumable by ``sample_ref``)."""
        judged_cards = [card for card in self.thin_cards() if card.judge.kind == "llm"]
        # As in generation: a derived card comes after the cards it reads from.
        judged_cards.sort(key=lambda card: card.dataset.scheme == "derived")
        arm_ids = [a.id for a in self.runnable_arms()]
        self._seed_judged(arm_ids, judged_cards)
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
            breaker=self._breaker,
            on_trip=self._note_trip,
        )
        totals = {"judged": 0, "skipped": 0, "failed": 0}
        for card in judged_cards:
            if self.stopped or self.control.aborted:
                break
            counts = phase.judge_card(arm_ids, card, self._task_for(card))
            for k, v in counts.items():
                totals[k] += v
        if not self.stopped and not self.control.aborted:
            cells, missing = self._judge_cells(arm_ids, judged_cards)
            self._note_trip(self._breaker.end_of_phase("judge", cells, missing))
        return totals

    def _judge_cells(
        self, arm_ids: Sequence[str], cards: Sequence[EvalCard]
    ) -> tuple[list[rc.PhaseCell], tuple[str, ...]]:
        """For every arm and judged card: completed samples, and those the judge replied to."""
        cells: list[rc.PhaseCell] = []
        missing: list[str] = []
        for card in cards:
            judge_id = judge_id_for(card, self.study.judge)
            for arm_id in arm_ids:
                completed = self.store.completed_refs(arm_id, card.id)
                replied = self.store.completed_judge_refs(arm_id, card.id, judge_id) & completed
                cells.append(rc.PhaseCell(arm_id, card.id, len(completed), len(replied)))
                if len(missing) < rc.HALT_SAMPLE_REFS:
                    missing.extend(sorted(completed - replied))
        return cells, tuple(missing[: rc.HALT_SAMPLE_REFS])

    def run(self) -> Run:
        """Generation, then the judge phase, then ``complete``.

        Ends ``paused`` when a result check stopped the run, ``needs_keys``
        when a key is missing and ``aborted`` when the host aborted.
        """
        run = self.run_generation()
        if run.state != "running":
            return run
        try:
            self.run_judge()
        except NeedsKeysError as err:
            logger.info("judge phase needs keys: %s", err)
            self._set_state("needs_keys", needs_keys=True)
            return self.run_record
        except Exception as err:  # noqa: BLE001 - stored as a stop; never left running
            # The message may quote a reply or a path. The halt names the class only.
            logger.exception("judge phase failed for run %s", self.store.run_id)
            self._note_trip(self._breaker.judge_phase_error(type(err).__name__))
        if self.control.aborted:
            self._set_state("aborted")
        elif self.stopped:
            self._set_state("paused")
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
                if not self._may_go_on():
                    break
                if self.control.paused:
                    if not paused_announced:
                        paused_announced = True
                        self._announce_paused()
                    if not self._wait_while_paused():
                        break
                    paused_announced = False
                    self._announce_resumed()
                    continue
                arm_slots.acquire()
                if self.control.aborted or self.control.paused or self.stopped:
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
        outcome: SampleOutcome | None = None
        try:
            outcome = self.dispatch(job)
            # A job a check stop kept from being sent was not aborted: it is
            # still to do, and the next plan picks it up.
            if outcome.generation is not None or self.control.aborted:
                self.progress.record(
                    job.arm.id, job.card.id, completed=outcome.completed, aborted=outcome.aborted
                )
        except NeedsKeysError as err:
            self._needs_keys = err
            self._emit(
                "error",
                sample_ref=job.sample_ref,
                message=self.secrets.redact(str(err)),
                code="needs_keys",
            )
        except Exception as err:  # noqa: BLE001 - recorded, run stops submitting
            logger.exception("sample %s failed", job.sample_ref)
            if self._first_error is None:
                self._first_error = err
            self._emit(
                "error",
                sample_ref=job.sample_ref,
                message=self.secrets.redact(f"{type(err).__name__}: {err}"),
            )
        finally:
            arm_slots.release()
            self._job_done(job, outcome)

    # ── one sample ────────────────────────────────────────────────────────

    def dispatch(self, job: SampleJob) -> SampleOutcome:
        """Generate one sample and persist its taints, row, Tier 0 records, score and flags.

        Thin and Inspect samples both pass through here, so the result checks
        and the Breaker see every sample.
        """
        while self._may_go_on():
            if self.control.paused:
                self._announce_paused()
                if not self._wait_while_paused():
                    break
                self._announce_resumed()
            if not self._inflight.acquire(timeout=0.05):
                continue
            admitted = False
            outcome = None
            try:
                if self.control.aborted or self.control.paused or self.stopped:
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
            conditioning=self.study.conditioning,
        )
        envelope: Mapping[str, Any] = (
            build_envelope(arm, ctx, qr_overrides, conditioning=self.study.conditioning)
            if model.provider == "qr_server"
            else {}
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
        # The harness's own name for what went wrong. Flags and halts carry
        # this, never the upstream text that the taint's detail may hold.
        cause: str | None = None
        completion: Completion
        with self.control.in_flight() as cancel:
            try:
                completion = client.generate(
                    body, on_delta=lambda c: coalescer.absorb(c, self.clock), cancel=cancel
                )
            except UpstreamError as err:
                completion = Completion(finish_reason="error")
                error_text = f"{type(err).__name__}: {err}"
                cause = f"HTTP {err.status}"
            except httpx.HTTPError as err:
                completion = Completion(finish_reason="error")
                error_text = f"{type(err).__name__}: {err}"
                cause = type(err).__name__
            except MalformedFrameError as err:
                completion = Completion(finish_reason="error")
                error_text = str(err)
                cause = rc.CAUSE_MALFORMED_FRAME
        coalescer.flush()
        finished = self.clock()
        elapsed = self.perf() - t0
        after = self._probe.probe() if is_qr else None

        cancelled = completion.cancelled or cancel.cancelled
        if error_text is None and not cancelled and completion.finish_reason is None:
            # The engine never said why it stopped, so what arrived cannot be
            # told from a reply that was cut off. Not a completed sample.
            error_text = "the stream ended without a finish reason"
            cause = rc.CAUSE_NO_FINISH_REASON
        finish_reason = self._finish_reason(completion, cancel, error_text)
        completed = finish_reason in ("stop", "length")
        if finish_reason == "error" and cause is None:
            cause = rc.CAUSE_UNMAPPED_FINISH
        if error_text is not None:
            # The text may quote an upstream error body, and a body may repeat
            # the key it was sent. It is stored and sent as an event, so the
            # run's keys are taken out first (AGENTS.md invariant 3).
            error_text = self.secrets.redact(error_text)
            taints.append(
                Taint(sample_ref=sample_ref, kind="stream_error", detail=error_text, at=finished)
            )

        # Token records come before the provenance: a record that says the
        # engine drew from its fallback source must withhold the quantum label.
        findings: list[rc.Finding] = []
        tier0_records: list[Tier0Record] = []
        if completed:
            findings.extend(rc.generation_findings(completion.text, finish_reason))
            tier0_records = self._tier0(
                job, completion, body, finished, session_id, window, taints, findings
            )
        fallback_delta, degraded, last_source = self._entropy_verdict(
            arm, before, after, sample_ref, finished, taints, completed=completed
        )
        fallback_delta = self._token_fallback(
            tier0_records, fallback_delta, sample_ref, finished, taints
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
        score_row: dict[str, Any] | None = None
        if completed:
            score_row, score_cause = self._score(job, completion, tier0_records, findings)
            cause = cause or score_cause
        flags = [
            ResultFlag(
                sample_ref=sample_ref,
                kind=finding.kind,
                phase=finding.phase,
                card_id=card.id,
                arm_id=arm.id,
                detail=self.secrets.redact(finding.detail)[:FLAG_DETAIL_MAX],
                count=finding.count,
                at=finished,
            )
            for finding in findings
        ]
        # Everything of the sample is encoded once before anything is appended,
        # so a value that cannot be written leaves no half-written sample. The
        # token records and the score row were checked where they were built.
        for record in (*taints, row, *flags):
            encode_line(record)

        with self._store_lock:
            # Taints first: a crash after this point cannot leave a completed
            # row whose taint is missing.
            for taint in taints:
                self.store.append_taint(taint)
            self.store.append_generation(row)
            if tier0_records:
                self.store.append_tier0(arm.id, card.id, tier0_records)
            if score_row is not None:
                self.store.append_score(arm.id, card.id, TASK_SCORER, score_row)
            self.store.append_flags(flags)
            # Fed under the same lock, so the Breaker sees samples in stored order.
            trip = self._breaker.feed_generation(
                rc.GenerationOutcome(
                    arm_id=arm.id,
                    card_id=card.id,
                    prompt_id=ctx.prompt_id,
                    sample_ref=sample_ref,
                    finish_reason=finish_reason,
                    taints=frozenset(taint.kind for taint in taints),
                    flags=frozenset(flag.kind for flag in flags),
                    has_reasoning=bool(completion.thinking),
                    probe_before_bad=is_qr and arm.source == "qrng" and _probe_bad(before),
                    cause=cause,
                )
            )
        if finish_reason == "error":
            if error_text is None:
                # The provider ended the reply for a reason the harness does not
                # map. No taint, and not a sample ``unfinished`` counts as missing.
                with self._unmapped_lock:
                    self._unmapped.setdefault((arm.id, card.id), set()).add(sample_ref)
            else:
                self._incomplete_refs.append(sample_ref)
        for taint in taints:
            self._emit("taint", **taint.model_dump(mode="json"))
        self._emit(
            "sample.finished",
            sample_ref=sample_ref,
            finish_reason=finish_reason,
            tokens=usage.completion_tokens,
            tps=tps,
            provenance=provenance.model_dump(mode="json"),
            flags=[
                {"kind": flag.kind, "detail": flag.detail, "count": flag.count} for flag in flags
            ],
        )
        self._note_trip(trip)
        return SampleOutcome(
            finish_reason=finish_reason,
            completed=completed,
            aborted=finish_reason == "abort",
            taints=taints,
            generation=row,
            flags=flags,
            cause=cause,
        )

    @staticmethod
    def _finish_reason(
        completion: Completion, cancel: CancelToken, error_text: str | None
    ) -> FinishReason:
        """``stop`` and ``length`` are the two ends of a completed reply.

        An upstream reason the harness does not map (a content filter, a tool
        call) is stored as ``error`` without a taint, as it always was.
        """
        if error_text is not None:
            return "error"
        if completion.cancelled or cancel.cancelled:
            return "abort"
        if completion.finish_reason == "length":
            return "length"
        if completion.finish_reason == "stop":
            return "stop"
        return "error"

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
        *,
        completed: bool = True,
    ) -> tuple[int, bool, str | None]:
        """``(fallback_delta, degraded, last_source)`` plus the taints they imply.

        An attempt that did not complete gets the numbers for its row and no
        entropy taint. The row is out of the analysis for having no completed
        reply, and a taint left behind by the failed attempt would go on
        excluding the sample after it was tried again and succeeded.
        """
        if before is None and after is None:
            if arm.source == "qrng":
                if completed:
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
        if delta > 0 and completed:
            taints.append(
                Taint(
                    sample_ref=sample_ref,
                    kind="engine_fallback",
                    detail=f"engine fallback_count rose by {delta} during the sample",
                    at=at,
                )
            )
        if degraded and arm.source == "qrng" and completed:
            taints.append(
                Taint(
                    sample_ref=sample_ref,
                    kind="qrng_degraded",
                    detail="engine reported currently_degraded during the sample",
                    at=at,
                )
            )
        return delta, degraded, last_source

    @staticmethod
    def _token_fallback(
        records: Sequence[Tier0Record],
        fallback_delta: int,
        sample_ref: str,
        at: datetime,
        taints: list[Taint],
    ) -> int:
        """Taint a sample whose own token records say the engine fell back (invariant 2).

        The health counter is one number for the whole engine and can miss a
        fallback (a restart between the two probes, one probe that failed).
        The token log is per request. Returns the fallback count the
        provenance records, which is what withholds the quantum label.
        """
        fallen = sum(1 for record in records if record.is_fallback)
        if fallen == 0:
            return fallback_delta
        detail = (
            f"{fallen} of {len(records)} token records say the engine drew from its fallback source"
        )
        for index, taint in enumerate(taints):
            if taint.kind == "engine_fallback":  # one taint of a kind per sample
                taints[index] = taint.model_copy(update={"detail": f"{taint.detail}; {detail}"})
                break
        else:
            taints.append(
                Taint(sample_ref=sample_ref, kind="engine_fallback", detail=detail, at=at)
            )
        return max(fallback_delta, fallen)

    def _expects_engine_log(self) -> bool:
        """True when the run's record says its token records come from the engine log."""
        return (self._tier0_source or self.run_record.tier0_source) == "engine_log"

    def _tier0(
        self,
        job: SampleJob,
        completion: Completion,
        body: Mapping[str, Any],
        finished: datetime,
        session_id: str | None,
        window: GenerationWindow,
        taints: list[Taint],
        findings: list[rc.Finding],
    ) -> list[Tier0Record]:
        """Token records of a completed sample, with the taint or flags they imply.

        A sample that asked for a token log and has none, in a run recorded as
        ``engine_log``, gets no records and a ``tier0_missing`` flag: records
        from the API's log-probabilities would use another kind of token id
        than the rest of the run. A token without a log-probability does the
        same. Whatever else is wrong with the sources is a ``stateless_row``
        taint. In every case the reply, its score and its judgement stand.
        """
        asked = _asked_for_token_log(body)
        log_path: Path | None = None
        if asked and self.config.token_log_dir:
            candidate = engine_log_path(Path(self.config.token_log_dir), job.sample_ref)
            # The engine creates the file when the request starts and flushes a
            # row per token, so it is normally complete by now; the short wait
            # covers a slow shared filesystem.
            if _wait_for_file(candidate, self.config.token_log_wait_s):
                log_path = candidate
        if asked and log_path is None and self._expects_engine_log():
            findings.append(
                rc.Finding("tier0_missing", "the request asked for a token log and none arrived")
            )
            return []
        if not completion.logprobs and log_path is None:
            return []
        try:
            records = build_records(
                job.sample_ref,
                completion.logprobs,
                log_path,
                timestamp_ns=int(finished.timestamp() * 1_000_000_000),
                session_id=session_id,
                window=window,
            )
        except MissingLogprobError as err:
            findings.append(rc.Finding("tier0_missing", str(err)))
            return []
        except AcquisitionError as err:
            taints.append(
                Taint(
                    sample_ref=job.sample_ref,
                    kind="stateless_row",
                    detail=self.secrets.redact(str(err)),
                    at=finished,
                )
            )
            return []
        outside = rc.u_outside_bracket((r.u, r.cdf_lo, r.cdf_hi) for r in records)
        if outside is not None:
            findings.append(outside)
        return records

    def _score(
        self,
        job: SampleJob,
        completion: Completion,
        tier0_records: Sequence[Tier0Record],
        findings: list[rc.Finding],
    ) -> tuple[dict[str, Any], str | None]:
        """The sample's score row, and the scorer's failure by name when it failed.

        A scorer that raises, and a score that cannot be written (NaN, an
        infinity, a value JSON has no form for), leave an ``error`` row and a
        ``score_error`` flag. The generation is kept either way.
        """
        ref = job.sample_ref
        sample = {
            "sample_ref": ref,
            "item": job.item,
            "told": job.told,
            "text": completion.text,
            "thinking": completion.thinking or None,
            "tier0": [r.model_dump(mode="json") for r in tier0_records],
        }
        try:
            scores = job.task.score(sample)
        except Exception as err:  # noqa: BLE001 - a scorer bug must not lose the generation
            logger.warning("score() failed for %s: %s", ref, err)
            name = type(err).__name__
            findings.append(rc.Finding("score_error", name))
            return {"sample_ref": ref, "error": self.secrets.redact(f"{name}: {err}")}, name
        row: dict[str, Any] = {"sample_ref": ref, **scores}
        bad = rc.non_finite_fields(row)
        if bad:
            names = ", ".join(bad)
            logger.warning("score() gave a value that is not finite for %s: %s", ref, names)
            findings.append(rc.Finding("score_error", f"a value that is not finite in {names}"))
            return {
                "sample_ref": ref,
                "error": f"NonFiniteScore: {names}",
            }, rc.CAUSE_NON_FINITE_SCORE
        try:
            encode_line(row)
        except (TypeError, ValueError) as err:
            name = type(err).__name__
            logger.warning("score() gave a row that cannot be written for %s: %s", ref, name)
            findings.append(rc.Finding("score_error", "a value that cannot be written as JSON"))
            return {
                "sample_ref": ref,
                "error": f"UnwritableScore: {name}",
            }, rc.CAUSE_UNWRITABLE_SCORE
        unparsed = rc.unparsed(job.card.id, row)
        if unparsed is not None:
            findings.append(unparsed)
        return row, None


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
    "CheckStopped",
    "DispatchHooks",
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
