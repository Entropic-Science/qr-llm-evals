"""``POST /config/parse``: turn a study file's text into a full ``StudyConfig``.

Study files are written by hand in YAML and leave defaults out. The web wizard
edits a configuration with every field present, so it sends the file's text
here and gets back the configuration in full form, with its hash. Nothing is
stored and no engine is called. The route is open and rate-limited like
``/estimates``.

The setup page saves a study as it stands, problems included, so a file may
hold a draft. A file that is well formed and only breaks rules spanning
several fields is therefore answered with the study and the list of those
``problems``; the page loads it and shows what is left to fix. Anything
malformed is a 400. Launching validates in full, as before.
"""

from __future__ import annotations

import json
from typing import Any

import yaml
from fastapi import APIRouter, Request
from pydantic import ValidationError

from qr_llm_evals.api.deps import State
from qr_llm_evals.api.errors import ApiError
from qr_llm_evals.api.ratelimit import enforce
from qr_llm_evals.api.schema import ConfigParseRequest, ConfigParseResponse
from qr_llm_evals.models.schema import config_hash, read_draft

router = APIRouter(tags=["config"])

#: A byte-order mark an editor may have put before the text.
_BOM = "\ufeff"


def _load(text: str) -> Any:
    """The value a study file holds, read as JSON when it is JSON and as YAML otherwise.

    PyYAML reads YAML 1.1, in which JSON is not always YAML: it refuses a tab
    used for indentation and an escaped surrogate pair, and it turns a raw
    next-line character inside a string into a space. So text that opens like
    a JSON object goes to the JSON parser first, and only what that parser
    refuses (a YAML flow mapping, say) is read as YAML.
    """
    text = text.removeprefix(_BOM)
    if text.lstrip().startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return yaml.safe_load(text)


@router.post("/config/parse", response_model=ConfigParseResponse)
async def parse_config(
    body: ConfigParseRequest, request: Request, state: State
) -> ConfigParseResponse:
    enforce(state.limiter, request, scope="config-parse")
    try:
        raw = _load(body.text)
    except yaml.YAMLError as err:
        raise ApiError(400, f"the file is not valid YAML or JSON: {err}", param="text") from err
    except ValueError as err:
        # PyYAML builds dates itself: ``2026-11-31`` is a timestamp it cannot construct.
        raise ApiError(
            400, f"the file holds a value that cannot be read: {err}", param="text"
        ) from err
    except RecursionError as err:
        raise ApiError(400, "the file is nested too deeply to be read", param="text") from err
    if not isinstance(raw, dict):
        raise ApiError(400, "the file does not hold a study configuration", param="text")
    try:
        config, problems = read_draft(raw)
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the file"
        raise ApiError(
            400,
            f"not a valid study configuration: {where}: {first['msg']} "
            f"({err.error_count()} problem(s) in all)",
            param="text",
        ) from err
    except RecursionError as err:
        raise ApiError(400, "the file is nested too deeply to be read", param="text") from err
    unknown = sorted(c.card_id for c in config.cards if c.card_id not in state.cards)
    # Built without validation: validating the response would run the cross-field
    # checks on the study again, strictly, and refuse the draft this route exists
    # to return. Every value here was validated or computed just above.
    return ConfigParseResponse.model_construct(
        config=config, config_hash=config_hash(config), unknown_cards=unknown, problems=problems
    )


__all__ = ["router"]
