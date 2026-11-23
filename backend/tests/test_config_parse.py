"""``POST /config/parse`` refuses a value no study can hold, and loads a draft.

The setup page saves a study as it stands, so a file may break a rule that
spans several fields; the route answers such a file with the study and its
``problems`` (``test_study_file_roundtrip.py``). A level that is not on its
grid is another matter: it is the value of one field, the page holds
``design.alpha`` and ``design.sensitivity_epsilon`` as closed sets, and it
could not show a study that carries anything else. Such a file is malformed
and is refused with the field and the values it may take.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from qr_llm_evals.api.app import create_app
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.config import load_settings
from qr_llm_evals.models.schema import (
    ALPHA_LEVELS,
    EPSILON_LEVELS,
    StudyConfig,
    StudyDesign,
    read_draft,
)

FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"
SCHEMA = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "schema"

ALPHA_MESSAGE = "design.alpha: Value error, design.alpha must be one of [0.05, 0.01, 0.005]"
EPSILON_MESSAGE = (
    "design.sensitivity_epsilon: Value error, design.sensitivity_epsilon must be one of "
    "[0.01, 0.03, 0.1, 0.5] or null"
)


def _designed() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (FIXTURES / "study1.designed.json").read_text(encoding="utf-8")
    )
    return loaded


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    keys_file = tmp_path / "api-keys.json"
    keys_file.write_text("[]", encoding="utf-8")
    settings = load_settings(
        {
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys_file),
            "QR_EVALS_VLLM_ORIGIN": "http://stub",
        }
    )
    app = create_app(settings, limiter=TokenBucketLimiter(rate_per_s=1000.0, burst=1000))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


#: (the design keys a hand-edited file sets, the refusal that names the field and its values)
OFF_GRID = [
    pytest.param({"alpha": 0.2}, ALPHA_MESSAGE, id="alpha_0_2"),
    pytest.param({"alpha": 0.049}, ALPHA_MESSAGE, id="alpha_near_a_level"),
    pytest.param({"alpha": 0}, ALPHA_MESSAGE, id="alpha_zero"),
    pytest.param({"sensitivity_epsilon": 0.2}, EPSILON_MESSAGE, id="epsilon_0_2"),
    pytest.param(
        {"claim": "difference_or_equivalence", "sensitivity_epsilon": 0.05},
        EPSILON_MESSAGE,
        id="epsilon_with_the_claim_that_needs_it",
    ),
]


@pytest.mark.parametrize(("design", "message"), OFF_GRID)
async def test_config_parse_refuses_a_level_that_is_not_on_its_grid(
    client: httpx.AsyncClient, design: dict[str, Any], message: str
) -> None:
    config = _designed()
    config["design"].update(design)
    for text in (yaml.safe_dump(config), json.dumps(config)):
        refused = await client.post("/api/config/parse", json={"text": text})
        assert refused.status_code == 400, refused.text
        error = refused.json()["error"]
        assert error["param"] == "text"
        assert error["message"] == (
            f"not a valid study configuration: {message} (1 problem(s) in all)"
        )
    # It is not a draft either: the read that collects cross-field problems raises.
    with pytest.raises(ValueError, match="must be one of"):
        read_draft(config)


@pytest.mark.parametrize(
    ("field", "value", "written", "message"),
    [
        ("alpha", float("nan"), ".nan", ALPHA_MESSAGE),
        ("alpha", float("inf"), ".inf", ALPHA_MESSAGE),
        ("sensitivity_epsilon", float("nan"), ".nan", EPSILON_MESSAGE),
    ],
)
async def test_config_parse_refuses_a_level_that_is_not_a_number(
    client: httpx.AsyncClient, field: str, value: float, written: str, message: str
) -> None:
    """``.nan`` used to come back as ``"alpha": null``, which no study and no response can hold."""
    config = _designed()
    config["design"][field] = value
    as_yaml = yaml.safe_dump(config)
    assert f"  {field}: {written}\n" in as_yaml
    # The JSON spelling of the same value: ``NaN`` and ``Infinity``, which the parser reads.
    as_json = json.dumps(config)
    assert f'"{field}": {"NaN" if written == ".nan" else "Infinity"}' in as_json
    for text in (as_yaml, as_json):
        refused = await client.post("/api/config/parse", json={"text": text})
        assert refused.status_code == 400, refused.text
        assert message in refused.json()["error"]["message"]


async def test_config_parse_answers_only_what_the_page_schema_holds(
    client: httpx.AsyncClient,
) -> None:
    """Every level the route lets through is one the contract's enum lists.

    The page parses the answer with the schema generated from ``StudyDesign``.
    A level outside the enum made it report "this page is out of date", so the
    two sets are tied here: what is on the grid loads, what is off it is a 400.
    """
    properties = json.loads((SCHEMA / "StudyConfig.json").read_text(encoding="utf-8"))["$defs"][
        "StudyDesign"
    ]["properties"]
    assert properties["alpha"]["enum"] == list(ALPHA_LEVELS)
    assert properties["sensitivity_epsilon"]["enum"] == [*EPSILON_LEVELS, None]

    for alpha in ALPHA_LEVELS:
        config = _designed()
        config["design"]["alpha"] = alpha
        loaded = await client.post("/api/config/parse", json={"text": json.dumps(config)})
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["config"]["design"]["alpha"] == alpha
        assert loaded.json()["problems"] == []

    # A level on the grid whose arm is missing is a rule across fields: still a draft.
    config = _designed()
    config["design"]["sensitivity_epsilon"] = 0.5
    draft = await client.post("/api/config/parse", json={"text": json.dumps(config)})
    assert draft.status_code == 200, draft.text
    assert draft.json()["config"]["design"]["sensitivity_epsilon"] == 0.5
    assert draft.json()["problems"] == [
        "cell qr-llm/creative: exactly one prng_epsilon arm at epsilon 0.5 is required, found []"
    ]


def test_a_level_off_its_grid_is_refused_with_the_other_fields_of_the_design() -> None:
    """The grid is a rule of the field, so it is reported where the field is."""
    for value in (0.2, float("nan"), float("inf"), -0.05):
        with pytest.raises(ValueError, match=r"design\.alpha must be one of"):
            StudyDesign.model_validate(
                {
                    "claim": "difference",
                    "confirmatory": [{"card_id": "a", "endpoint": "b"}],
                    "alpha": value,
                }
            )
    # The strict validation names both fields at once, and still names the cross-field rules
    # of a study whose levels are on the grid.
    config = _designed()
    config["design"].update(alpha=0.02, sensitivity_epsilon=0.2)
    with pytest.raises(ValueError) as refused:
        StudyConfig.model_validate(config)
    assert "design.alpha must be one of" in str(refused.value)
    assert "design.sensitivity_epsilon must be one of" in str(refused.value)
