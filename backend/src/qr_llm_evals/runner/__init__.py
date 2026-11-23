"""The thin runner: orchestrator, run control, event bus, resume, taints, judge phase (spec §2.5).

Layer 2: may import layer 0 (``cards``, ``client``, ``keys``, ``models``) and
layer 1 (``store``, ``tier0``); never ``api`` or ``analysis``.
"""

from __future__ import annotations

from qr_llm_evals.runner.control import RunControl, RunControlError
from qr_llm_evals.runner.events import (
    DEFAULT_QUEUE_SIZE,
    EVENT_TYPES,
    EventSink,
    ListEventSink,
    NullEventSink,
    RunEvent,
    RunEventBus,
    Subscription,
)
from qr_llm_evals.runner.judge_phase import JudgePhase, NeedsKeysError, parse_score
from qr_llm_evals.runner.orchestrator import (
    CheckStopped,
    EntropyProbe,
    EntropyStatus,
    Orchestrator,
    RunnerConfig,
    SampleJob,
    SampleOutcome,
    WindowTagger,
    run_headless,
)
from qr_llm_evals.runner.progress import ProgressTracker
from qr_llm_evals.runner.tasks import (
    LoadedTask,
    TaskModule,
    TaskModuleError,
    load_task_module,
    told_preamble,
)

__all__ = [
    "DEFAULT_QUEUE_SIZE",
    "EVENT_TYPES",
    "CheckStopped",
    "EntropyProbe",
    "EntropyStatus",
    "EventSink",
    "JudgePhase",
    "ListEventSink",
    "LoadedTask",
    "NeedsKeysError",
    "NullEventSink",
    "Orchestrator",
    "ProgressTracker",
    "RunControl",
    "RunControlError",
    "RunEvent",
    "RunEventBus",
    "RunnerConfig",
    "SampleJob",
    "SampleOutcome",
    "Subscription",
    "TaskModule",
    "TaskModuleError",
    "WindowTagger",
    "load_task_module",
    "parse_score",
    "run_headless",
    "told_preamble",
]
