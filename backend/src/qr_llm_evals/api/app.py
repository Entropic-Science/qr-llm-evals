"""``create_app(settings, …)`` — the FastAPI application (spec §2.1, §4.7).

Everything the routes need is built here once and parked on
``app.state.qr`` as an :class:`~qr_llm_evals.api.deps.AppState`; tests build
the app with a ``tmp_path`` data dir, a ``FileKeyVerifier`` over a fixture
keys file and the stub engine's transports. Nothing is read from the
environment here — ``config.load_settings`` is the entrypoint's job.

Boot (lifespan):

1. open the store (creates the data root and the SQLite index);
2. flip every unfinished run on disk to ``needs_keys`` — a restart lost their
   ``SecretBag`` (D-14) and their orchestrator threads;
3. serve. On shutdown the buses close and the bags are cleared.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import metadata
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qr_llm_evals.accounts import ACCOUNTS_DB_NAME, Accounts
from qr_llm_evals.api.deps import AppState, PreregRenderer, TargetUniformDrawer
from qr_llm_evals.api.errors import install_handlers
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.api.registry import OrchestratorFactory, Runnable, RunRegistry, mark_orphans
from qr_llm_evals.api.routes import ROUTERS
from qr_llm_evals.api.session_runtime import SessionDispatch, SessionOrchestratorFactory
from qr_llm_evals.cards.loader import REPO_ROOT, load_cards
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.client.anthropic_judge import AnthropicJudgeClient, JudgeRequest
from qr_llm_evals.config import Settings
from qr_llm_evals.inspect_bridge.subprocess_runner import run_inspect_card
from qr_llm_evals.keys.secrets import SecretBag
from qr_llm_evals.keys.verify import KeyVerifier, build_verifier
from qr_llm_evals.models.catalogue import ModelCatalogue, load_catalogue
from qr_llm_evals.models.providers import ProviderProfile, load_provider_profiles
from qr_llm_evals.models.schema import Arm
from qr_llm_evals.prereg import load_renderer
from qr_llm_evals.runner.control import RunControl
from qr_llm_evals.runner.events import EventSink
from qr_llm_evals.runner.judge_phase import NeedsKeysError
from qr_llm_evals.runner.orchestrator import Orchestrator, RunnerConfig
from qr_llm_evals.runner.tasks import effective_k
from qr_llm_evals.store.schema import Run
from qr_llm_evals.store.store import RunStore, Store
from qr_llm_evals.supervised.session import SessionRegistry
from qr_llm_evals.supervised.targets import EngineTier0UniformDrawer

log = logging.getLogger("qr_llm_evals.api")

API_PREFIX = "/api"
TITLE = "qr-llm-evals"


def package_version() -> str:
    try:
        return metadata.version("qr-llm-evals")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def default_orchestrator_factory(
    settings: Settings,
    store: Store,
    cards: Mapping[str, EvalCard],
    *,
    repo_root: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    judge_transport: httpx.BaseTransport | None = None,
) -> OrchestratorFactory:
    """The production factory: real transports, ``RunnerConfig`` from settings."""
    sessions = SessionRegistry()

    def execute_inspect(orch: Orchestrator, arm: Arm, card: EvalCard) -> None:
        def grade(messages: list[dict[str, str]]) -> str:
            key = orch.secrets.get("anthropic")
            if key is None:
                raise NeedsKeysError("anthropic")
            if any(message["role"] not in {"system", "user"} for message in messages):
                raise ValueError("Inspect grader requires system/user messages")
            client = AnthropicJudgeClient(
                key, model=orch.study.judge.model, transport=judge_transport
            )
            return client.judge(
                JudgeRequest(
                    system="\n\n".join(m["content"] for m in messages if m["role"] == "system"),
                    user="\n\n".join(m["content"] for m in messages if m["role"] == "user"),
                    judge_run_id=orch.store.run_id,
                )
            ).text

        overrides = orch._overrides_for(card.id)
        k = effective_k(card, overrides)
        n = overrides.items if overrides.items is not None else card.dataset.items
        if n > card.dataset.items:
            raise ValueError("Inspect item override exceeds frozen dataset")
        effective = card.model_copy(
            update={
                "k": k,
                "task_args": {**card.task_args, "num_generations": k},
                "dataset": card.dataset.model_copy(update={"items": n}),
            }
        )
        orch.progress.set_total(arm.id, card.id, n * k)
        run_inspect_card(
            effective,
            arm,
            orch.store.arm_card(arm.id, card.id).inspect_dir,
            lambda ctx, messages: orch.dispatch_inspect(arm, effective, ctx, messages),
            judge_model=orch.study.judge.model,
            grader=grade,
            on_sample_end=lambda prompt_id, epoch: orch.progress.record(
                arm.id, card.id, completed=True
            ),
            should_abort=lambda: orch.control.aborted,
        )

    def build(
        run_store: RunStore, secrets: SecretBag, control: RunControl, sink: EventSink
    ) -> Runnable:
        run = run_store.read_run()
        study = store.load_study(run.study_id)
        config = RunnerConfig(
            vllm_origin=settings.vllm_origin,
            max_inflight=settings.max_inflight,
            priority=settings.priority,
            token_log_dir=settings.token_log_dir,
            determinism_tier=run.determinism_tier,
            repo_root=repo_root,
        )
        return Orchestrator(
            study,
            cards,
            run_store,
            config,
            secrets=secrets,
            sink=sink,
            control=control,
            transport=transport,
            judge_transport=judge_transport,
            tier0_source=run.tier0_source,
            inspect_executor=execute_inspect,
            dispatch_hooks=SessionDispatch(sessions, run.run_id),
        )

    return SessionOrchestratorFactory(build, sessions)


def create_app(
    settings: Settings,
    *,
    store: Store | None = None,
    verifier: KeyVerifier | None = None,
    accounts: Accounts | None = None,
    cards: Mapping[str, EvalCard] | None = None,
    catalogue: ModelCatalogue | None = None,
    providers: Mapping[str, ProviderProfile] | None = None,
    orchestrator_factory: OrchestratorFactory | None = None,
    prereg_renderer: PreregRenderer | None = None,
    target_uniform_drawer: TargetUniformDrawer | None = None,
    limiter: TokenBucketLimiter | None = None,
    engine_transport: httpx.AsyncBaseTransport | None = None,
    readiness_transport: httpx.BaseTransport | None = None,
    analysis_transport: httpx.BaseTransport | None = None,
    repo_root: Path | None = None,
    commit: str | None = None,
) -> FastAPI:
    """Build the app. Every collaborator is injectable; defaults are the real ones."""
    the_store = store if store is not None else Store.from_settings(settings)
    the_cards = dict(cards) if cards is not None else load_cards()
    the_catalogue = catalogue if catalogue is not None else load_catalogue()
    the_providers = dict(providers) if providers is not None else load_provider_profiles()
    the_verifier = verifier if verifier is not None else build_verifier(settings)
    the_accounts = (
        accounts
        if accounts is not None
        else Accounts(
            Path(settings.data_dir) / ACCOUNTS_DB_NAME,
            session_ttl=timedelta(hours=settings.session_ttl_hours),
        )
    )
    root = repo_root if repo_root is not None else REPO_ROOT
    factory = (
        orchestrator_factory
        if orchestrator_factory is not None
        else default_orchestrator_factory(settings, the_store, the_cards, repo_root=root)
    )
    state = AppState(
        settings=settings,
        store=the_store,
        verifier=the_verifier,
        accounts=the_accounts,
        cards=the_cards,
        catalogue=the_catalogue,
        providers=the_providers,
        orchestrator_factory=factory,
        registry=RunRegistry(),
        sessions=factory.sessions
        if isinstance(factory, SessionOrchestratorFactory)
        else SessionRegistry(),
        limiter=limiter if limiter is not None else TokenBucketLimiter(),
        prereg_renderer=prereg_renderer
        if prereg_renderer is not None
        else load_renderer(cards=the_cards, repo_root=root).render,
        engine_transport=engine_transport,
        readiness_transport=readiness_transport,
        analysis_transport=analysis_transport,
        repo_root=root,
        version=package_version(),
        commit=commit if commit is not None else settings.commit,
        target_uniform_drawer=(
            target_uniform_drawer
            if target_uniform_drawer is not None
            else (
                EngineTier0UniformDrawer(
                    the_store,
                    settings.vllm_origin,
                    Path(settings.token_log_dir),
                    priority=settings.priority,
                )
                if settings.token_log_dir is not None
                else None
            )
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        flipped = mark_orphans(the_store.list_runs(), lambda run: _write_run(the_store, run))
        if flipped:
            log.warning(
                "runs orphaned by restart now need keys",
                extra={"event": "api.boot.orphans", "run_ids": flipped},
            )
        log.info(
            "api ready",
            extra={
                "event": "api.boot.ok",
                "version": state.version,
                "commit": state.commit,
                "cards": len(the_cards),
                "models": len(the_catalogue.entries),
                "key_verify": the_verifier.mode,
                "data_dir": settings.data_dir,
            },
        )

        async def session_timers() -> None:
            while True:
                for controller in state.sessions.values():
                    controller.presence_changed(now=controller.perf())
                    controller.tick()
                await asyncio.sleep(0.5)

        timers = asyncio.create_task(session_timers())
        try:
            yield
        finally:
            timers.cancel()
            await asyncio.gather(timers, return_exceptions=True)
            for controller in state.sessions.values():
                controller.stop()
            await asyncio.to_thread(state.registry.close_all)
            the_store.close()
            the_accounts.close()

    app = FastAPI(
        title=TITLE,
        version=state.version,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.qr = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Host-Token", "X-Observer-Id"],
        expose_headers=["Retry-After"],
        max_age=600,
    )
    install_handlers(app)
    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    return app


def _write_run(store: Store, run: Run) -> None:
    store.run(run.study_id, run.run_id).write_run(run)


__all__ = ["API_PREFIX", "create_app", "default_orchestrator_factory", "package_version"]
