"""Supervised mode: session state machine, timers, boundary rule, groups, questionnaires (spec §2.9).

Layer 2: may import layers 0–1. Timers never auto-start a session (AGENTS.md invariant 6).
"""

from __future__ import annotations

from qr_llm_evals.supervised.boundary import BoundaryEstimator, should_dispatch
from qr_llm_evals.supervised.groups import PresenceTracker, new_join_code
from qr_llm_evals.supervised.questionnaires import QuestionnaireGate, load_questionnaires
from qr_llm_evals.supervised.schema import (
    Intention,
    Observer,
    ObserverPresence,
    Questionnaire,
    Session,
    TargetDraw,
    Window,
)
from qr_llm_evals.supervised.session import SessionController, SessionRegistry
from qr_llm_evals.supervised.targets import draw_target, load_target_pool

__all__ = [
    "BoundaryEstimator",
    "Intention",
    "Observer",
    "ObserverPresence",
    "PresenceTracker",
    "Questionnaire",
    "QuestionnaireGate",
    "Session",
    "SessionController",
    "SessionRegistry",
    "TargetDraw",
    "Window",
    "draw_target",
    "load_questionnaires",
    "load_target_pool",
    "new_join_code",
    "should_dispatch",
]
