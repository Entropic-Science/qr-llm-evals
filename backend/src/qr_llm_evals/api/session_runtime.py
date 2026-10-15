"""Compose session scheduling with runner hooks without cross-layer imports."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import datetime

from qr_llm_evals.api.registry import OrchestratorFactory, Runnable
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.models.schema import SupervisedConfig
from qr_llm_evals.runner.control import RunControl
from qr_llm_evals.runner.events import EventSink, RunEvent
from qr_llm_evals.runner.orchestrator import SampleJob, SampleOutcome
from qr_llm_evals.store.artifacts import iter_jsonl
from qr_llm_evals.store.schema import GenerationWindow
from qr_llm_evals.store.store import RunStore
from qr_llm_evals.supervised.boundary import BoundaryEstimator
from qr_llm_evals.supervised.groups import new_join_code
from qr_llm_evals.supervised.schema import Questionnaire, Session
from qr_llm_evals.supervised.session import SessionController, SessionRegistry


class SessionOrchestratorFactory:
    """Expose the production factory's registry to the app, including injected transports."""

    def __init__(self, build: OrchestratorFactory, sessions: SessionRegistry) -> None:
        self.build = build
        self.sessions = sessions

    def __call__(
        self, store: RunStore, secrets: SecretBag, control: RunControl, sink: EventSink
    ) -> Runnable:
        return self.build(store, secrets, control, sink)


class SessionDispatch:
    def __init__(self, sessions: SessionRegistry, run_id: str) -> None:
        self.sessions = sessions
        self.run_id = run_id
        self._lock = threading.Lock()
        self._leases: dict[str, tuple[SessionController, int]] = {}

    def admit(self, job: SampleJob) -> tuple[GenerationWindow, str | None] | None:
        if job.arm.observer_mode != "supervised":
            return "unsupervised", None
        with self._lock:
            for controller in reversed(self.sessions.for_run(self.run_id)):
                if controller.state != "running":
                    continue
                slot = controller.claim_slot(job.card.id)
                if slot is not None:
                    self._leases[job.sample_ref] = (controller, slot)
                    return "supervised", controller.session_id
            return None

    def finished(self, job: SampleJob, outcome: SampleOutcome | None) -> None:
        with self._lock:
            lease = self._leases.pop(job.sample_ref, None)
            if lease is None:
                return
            controller, slot = lease
            row = outcome.generation if outcome is not None else None
            if outcome is None or not outcome.completed:
                controller.stop()
            controller.generation_finished(
                slot,
                job.card.id,
                tokens=row.usage.completion_tokens if row is not None else 0,
                elapsed_s=(row.timing.finished - row.timing.started).total_seconds()
                if row is not None and row.timing.finished is not None
                else 0,
                dispatch_next=False,
            )


def restore_sessions(
    sessions: SessionRegistry,
    store: RunStore,
    config: SupervisedConfig,
    cards: Mapping[str, EvalCard],
    sink: EventSink,
    clock: Callable[[], datetime],
) -> None:
    """Recover durable questionnaire gates, never a running observer sitting.

    Open windows are closed at recovery time (an upper bound, not a claim of
    continuous observation). The next sitting requires new presence/pre/start.
    """
    for path in sorted(store.run_dir.sessions_dir.glob("*.json")):
        record = Session.model_validate_json(path.read_text(encoding="utf-8"))
        if sessions.get(record.session_id) is not None:
            continue
        now = clock()
        record = record.model_copy(
            update={
                "ended_at": record.ended_at or now,
                "windows": [w.model_copy(update={"end": w.end or now}) for w in record.windows],
            }
        )
        settings = getattr(config.settings_by_display, record.display)
        controller = SessionController(
            record,
            store,
            BoundaryEstimator(
                1.0,
                {
                    key: float(card.est_tokens_per_sample)
                    for key, card in cards.items()
                    if card.est_tokens_per_sample > 0
                },
            ),
            streams=settings.streams,
            break_s=settings.break_min * 60,
            refocus_s=config.refocus_s,
            join_code=new_join_code(),
            clock=clock,
            emit=lambda kind, data: sink.emit(RunEvent(kind, data)),
        )
        controller.state = "complete"
        for raw in iter_jsonl(store.run_dir.questionnaires_jsonl):
            questionnaire = Questionnaire.model_validate(raw)
            if questionnaire.session_id == record.session_id:
                controller.questionnaires.record(questionnaire)
        sessions.add(controller)
