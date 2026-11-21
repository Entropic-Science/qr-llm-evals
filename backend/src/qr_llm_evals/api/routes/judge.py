"""``POST /judge/check``: try the judge settings with the user's own key.

One short Messages API call with the model and parameters the study would
freeze, so a wrong key, a model id that does not exist, or a parameter the
model refuses shows up before a run is launched instead of after generation.

The key arrives in the body, is used for that one call and is dropped. It is
never stored, logged or echoed; the provider's error text is returned as it
came, and that text does not contain the key.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from qr_llm_evals.api.deps import CurrentUser, State
from qr_llm_evals.api.ratelimit import enforce
from qr_llm_evals.api.schema import JudgeCheckRequest, JudgeCheckResponse
from qr_llm_evals.client.anthropic_judge import JudgeError, JudgeRequest
from qr_llm_evals.runner.judge_phase import judge_client

log = logging.getLogger("qr_llm_evals.api.judge")

router = APIRouter(tags=["judge"])

CHECK_SYSTEM = "You are a connectivity check. Reply with the single word OK."
CHECK_USER = "Reply with OK."


@router.post("/judge/check", response_model=JudgeCheckResponse)
async def check_judge(
    body: JudgeCheckRequest, request: Request, user: CurrentUser, state: State
) -> JudgeCheckResponse:
    enforce(state.limiter, request, scope="judge-check")
    client = judge_client(body.judge, body.api_key, transport=state.judge_transport, sleep=False)
    client.max_retries = 0
    check = JudgeRequest(system=CHECK_SYSTEM, user=CHECK_USER, judge_run_id="judge-check")
    try:
        reply = await run_in_threadpool(client.judge, check)
    except JudgeError as err:
        log.info(
            "judge check refused",
            extra={"event": "judge.check", "user_id": user.user_id, "status": err.status},
        )
        return JudgeCheckResponse(ok=False, status=err.status, detail=err.excerpt)
    except httpx.HTTPError as err:
        return JudgeCheckResponse(
            ok=False,
            status=None,
            detail=f"could not reach the Anthropic API ({type(err).__name__})",
        )
    log.info(
        "judge check passed",
        extra={"event": "judge.check", "user_id": user.user_id, "status": 200},
    )
    return JudgeCheckResponse(
        ok=True,
        status=200,
        model=reply.model or body.judge.model,
        detail=(
            "The judge answered."
            if reply.text.strip()
            else "The judge answered with no text; raise the output limit."
        ),
    )


__all__ = ["router"]
