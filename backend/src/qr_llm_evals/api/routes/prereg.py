"""Public, per-IP rate-limited Markdown export; no engine calls or persistence."""

from fastapi import APIRouter, Request

from qr_llm_evals.analysis.plan import StatisticalPlan
from qr_llm_evals.api.deps import State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import enforce
from qr_llm_evals.api.schema import PreregRenderRequest, PreregRenderResponse
from qr_llm_evals.models.schema import config_hash
from qr_llm_evals.prereg import prereg_hash

router = APIRouter(tags=["prereg"])


@router.post("/prereg/render", response_model=PreregRenderResponse)
def post_prereg(body: PreregRenderRequest, request: Request, state: State) -> PreregRenderResponse:
    enforce(state.limiter, request, scope="prereg")
    if body.expectations is not None and body.expectations != body.config.expectations:
        raise ApiError(400, "expectations must match config.expectations", param="expectations")
    try:
        markdown = state.prereg_renderer(body.config, body.readiness)
        plan = StatisticalPlan.from_config(body.config, state.cards)
    except ValueError as err:
        raise ApiError(400, str(err), param="config") from err
    return PreregRenderResponse(
        markdown=markdown,
        prereg_hash=prereg_hash(markdown),
        config_hash=config_hash(body.config),
        plan_hash=plan.plan_hash,
        plan=plan,
    )
