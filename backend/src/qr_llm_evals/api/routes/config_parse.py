"""``POST /config/parse``: turn a study file's text into a full ``StudyConfig``.

Study files are written by hand in YAML and leave defaults out. The web wizard
edits a configuration with every field present, so it sends the file's text
here and gets back the validated configuration in full form, with its hash.
Nothing is stored and no engine is called. The route is open and rate-limited
like ``/estimates``.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Request
from pydantic import ValidationError

from qr_llm_evals.api.deps import State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import enforce
from qr_llm_evals.api.schema import ConfigParseRequest, ConfigParseResponse
from qr_llm_evals.models.schema import StudyConfig, config_hash

router = APIRouter(tags=["config"])


@router.post("/config/parse", response_model=ConfigParseResponse)
async def parse_config(
    body: ConfigParseRequest, request: Request, state: State
) -> ConfigParseResponse:
    enforce(state.limiter, request, scope="config-parse")
    try:
        raw = yaml.safe_load(body.text)  # JSON is YAML, so one parser reads both
    except yaml.YAMLError as err:
        raise ApiError(400, f"the file is not valid YAML or JSON: {err}", param="text") from err
    if not isinstance(raw, dict):
        raise ApiError(400, "the file does not hold a study configuration", param="text")
    try:
        config = StudyConfig.model_validate(raw)
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the file"
        raise ApiError(
            400,
            f"not a valid study configuration: {where}: {first['msg']} "
            f"({err.error_count()} problem(s) in all)",
            param="text",
        ) from err
    unknown = sorted(c.card_id for c in config.cards if c.card_id not in state.cards)
    return ConfigParseResponse(
        config=config, config_hash=config_hash(config), unknown_cards=unknown
    )


__all__ = ["router"]
