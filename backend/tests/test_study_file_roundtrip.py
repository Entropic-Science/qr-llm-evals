"""A study file saved by the browser loads on the server as the same study.

The web app writes a study as YAML with its own small serializer
(``apps/web/src/lib/study-file.ts``) and the server reads it back with PyYAML
in ``POST /config/parse``. The two committed files under
``packages/contracts/fixtures`` are that serializer's output for the Study 1
template and for its designed form; the web tests compare the serializer with
them byte for byte, and these tests load the same bytes on the server side.
Together they show that saving and loading a study changes nothing: the loaded
config hashes to the pinned value.

The app saves a study as it stands, so a file may hold a draft. The second half
checks that such a file loads with its problems listed, that a malformed file
is still refused, that nothing but ``/config/parse`` accepts a draft, and that
a study written as JSON is read as JSON.
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
from qr_llm_evals.api.schema import LaunchRequest
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.config import load_settings
from qr_llm_evals.models.schema import StudyConfig, config_hash, read_draft
from test_schemas import (
    STUDY1_CONFIG_HASH,
    STUDY1_DESIGNED_CONFIG_HASH,
    STUDY1_DESIGNED_PLAN_HASH,
)

FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"
HEADER = "# Study file for evals.entropic.science\n"

#: (study file, the JSON fixture it was written from, pinned config hash)
STUDY_FILES = [
    pytest.param("study1.study.yaml", "study1.json", STUDY1_CONFIG_HASH, id="study1"),
    pytest.param(
        "study1.designed.study.yaml",
        "study1.designed.json",
        STUDY1_DESIGNED_CONFIG_HASH,
        id="study1-designed",
    ),
]


def _text(name: str) -> str:
    raw = (FIXTURES / name).read_bytes()
    # The writer emits LF only and exactly one final newline; a checkout must not change that.
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    return raw.decode("utf-8")


def _null_paths(value: Any, path: str = "") -> list[str]:
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in ([f"{path}.{key}"] if item is None else _null_paths(item, f"{path}.{key}"))
        ]
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in _null_paths(item, f"{path}[{index}]")
        ]
    return []


@pytest.mark.parametrize(("name", "source", "pinned"), STUDY_FILES)
def test_study_file_loads_as_the_pinned_study(name: str, source: str, pinned: str) -> None:
    text = _text(name)
    assert text.startswith(HEADER)
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)

    config = StudyConfig.model_validate(loaded)
    assert config_hash(config) == pinned

    # It is the study of the JSON fixture, field for field, once the defaults the file leaves
    # out are filled back in.
    fixture = json.loads((FIXTURES / source).read_text(encoding="utf-8"))
    assert config == StudyConfig.model_validate(fixture)
    assert config.model_dump(mode="json") == fixture

    # The file writes no nulls, and a quoted scalar stays the text it was: PyYAML would
    # otherwise read the timestamp as a datetime and a bare `null` as None.
    assert _null_paths(loaded) == []
    assert loaded["created_at"] == fixture["created_at"]
    assert isinstance(loaded["created_at"], str)


def test_legacy_study_file_has_no_design_block() -> None:
    loaded = yaml.safe_load(_text("study1.study.yaml"))
    assert "design" not in loaded
    assert StudyConfig.model_validate(loaded).design is None


def test_designed_study_file_registers_the_pinned_plan() -> None:
    loaded = yaml.safe_load(_text("study1.designed.study.yaml"))
    assert loaded["design"]["claim"] == "difference"
    assert loaded["seeds"] == [1, 7, 42, 1001]
    config = StudyConfig.model_validate(loaded)
    assert config.design is not None and len(config.design.confirmatory) == 3
    # The same hypotheses, margins and sample sizes as the study it was saved from.
    plan = StatisticalPlan.from_config(config, load_cards())
    assert plan.plan_hash == STUDY1_DESIGNED_PLAN_HASH


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


@pytest.mark.parametrize(("name", "source", "pinned"), STUDY_FILES)
async def test_config_parse_reads_the_saved_file(
    client: httpx.AsyncClient, name: str, source: str, pinned: str
) -> None:
    """The route the web app's "Load study file" calls returns the study that was saved."""
    parsed = await client.post("/api/config/parse", json={"text": _text(name)})
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["config_hash"] == pinned
    assert body["unknown_cards"] == []
    # A study that can be launched has no problems.
    assert body["problems"] == []
    fixture = json.loads((FIXTURES / source).read_text(encoding="utf-8"))
    # A legacy config is returned without a `design` key, a designed one with its block.
    assert body["config"] == fixture


# ── a draft saved with problems loads back ────────────────────────────────────


def _designed() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(_text("study1.designed.study.yaml"))
    return loaded


def _untick_confirmatory_eval(config: dict[str, Any]) -> None:
    config["cards"] = [c for c in config["cards"] if c["card_id"] != "tier0_pit_residuals"]


def _claim_equivalence_without_control(config: dict[str, Any]) -> None:
    config["design"]["claim"] = "difference_or_equivalence"


def _drop_a_seed(config: dict[str, Any]) -> None:
    config["seeds"] = [seed for seed in config["seeds"] if seed != 7]


def _point_an_arm_at_a_missing_model(config: dict[str, Any]) -> None:
    config["arms"][0]["model"] = "no-such-model"


def _two_things_at_once(config: dict[str, Any]) -> None:
    _untick_confirmatory_eval(config)
    _claim_equivalence_without_control(config)


#: (what the setup did to the study, the problems the file then loads with)
DRAFTS = [
    pytest.param(
        _untick_confirmatory_eval,
        ["design.confirmatory names card 'tier0_pit_residuals', which is not selected"],
        id="untick_confirmatory_eval",
    ),
    pytest.param(
        _claim_equivalence_without_control,
        ["design.claim 'difference_or_equivalence' requires design.sensitivity_epsilon"],
        id="claim_equivalence_without_control",
    ),
    pytest.param(
        _drop_a_seed,
        [
            "arm 'prng_seed_7' study_seed 7 not in seeds",
        ],
        id="drop_a_seed",
    ),
    pytest.param(
        _point_an_arm_at_a_missing_model,
        ["arm 'qrng_supervised' references unknown model 'no-such-model'"],
        id="missing_model",
    ),
    pytest.param(
        _two_things_at_once,
        [
            "design.claim 'difference_or_equivalence' requires design.sensitivity_epsilon",
            "design.confirmatory names card 'tier0_pit_residuals', which is not selected",
        ],
        id="two_things_at_once",
    ),
]


@pytest.mark.parametrize(("edit", "problems"), DRAFTS)
async def test_config_parse_loads_a_draft_with_its_problems(
    client: httpx.AsyncClient, edit: Any, problems: list[str]
) -> None:
    """A file the app saved with a problem is a study the app can open again."""
    config = _designed()
    edit(config)
    # This is a draft: the study as such is refused, with these problems.
    with pytest.raises(ValueError) as refused:
        StudyConfig.model_validate(config)
    assert all(problem in str(refused.value) for problem in problems)

    parsed = await client.post("/api/config/parse", json={"text": yaml.safe_dump(config)})
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["problems"] == problems
    assert set(body) == {"config", "config_hash", "unknown_cards", "problems"}
    # The study comes back as it was saved, defaults filled in, with its hash.
    study, found = read_draft(config)
    assert found == problems
    assert body["config"] == study.model_dump(mode="json")
    assert body["config_hash"] == config_hash(study)
    assert body["config"]["design"] == study.model_dump(mode="json")["design"]

    # Repaired, the same file loads without problems and is the study again.
    again = await client.post(
        "/api/config/parse", json={"text": _text("study1.designed.study.yaml")}
    )
    assert again.json()["problems"] == []


#: (what is wrong with the file, part of the refusal)
MALFORMED = [
    pytest.param(lambda c: c.update(title=""), "title: String should have", id="empty_title"),
    pytest.param(lambda c: c.update(cards=[]), "cards: List should have", id="no_evals"),
    pytest.param(lambda c: c.update(arms=[]), "arms: List should have", id="no_arms"),
    pytest.param(
        lambda c: c["arms"][2].pop("study_seed"), "requires study_seed", id="arm_without_seed"
    ),
    pytest.param(lambda c: c.update(from_the_future=True), "from_the_future", id="unknown_setting"),
    pytest.param(
        lambda c: c["design"].update(confirmatory=[]), "design.confirmatory", id="no_outcome_list"
    ),
    pytest.param(lambda c: c["design"].update(claim="anything"), "design.claim", id="bad_claim"),
]


@pytest.mark.parametrize(("edit", "message"), MALFORMED)
async def test_config_parse_still_refuses_a_malformed_file(
    client: httpx.AsyncClient, edit: Any, message: str
) -> None:
    config = _designed()
    edit(config)
    refused = await client.post("/api/config/parse", json={"text": yaml.safe_dump(config)})
    assert refused.status_code == 400, refused.text
    error = refused.json()["error"]
    assert error["param"] == "text"
    assert "not a valid study configuration" in error["message"]
    assert message in error["message"]


async def test_a_draft_is_accepted_nowhere_else(client: httpx.AsyncClient) -> None:
    """Only the file reader takes a draft: nothing renders, estimates or launches one."""
    config = _designed()
    _untick_confirmatory_eval(config)
    draft, problems = read_draft(config)
    assert problems
    body = draft.model_dump(mode="json")
    refused = await client.post("/api/prereg/render", json={"config": body})
    assert refused.status_code == 400, refused.text
    assert "not selected" in refused.json()["error"]["message"]
    # A launch validates its body the same way, before anything is frozen.
    with pytest.raises(ValueError, match="not selected"):
        LaunchRequest.model_validate({"config": body, "kind": "pilot"})
    # Read back strictly, the draft a route handed out is refused like any other input.
    with pytest.raises(ValueError, match="not selected"):
        StudyConfig.model_validate(body)
    with pytest.raises(ValueError, match="not selected"):
        StudyConfig.model_validate_json(draft.model_dump_json())


# ── JSON is read as JSON ─────────────────────────────────────────────────────


async def test_config_parse_reads_json_as_json(client: httpx.AsyncClient) -> None:
    fixture = json.loads((FIXTURES / "study1.designed.json").read_text(encoding="utf-8"))

    async def parse(text: str) -> httpx.Response:
        return await client.post("/api/config/parse", json={"text": text})

    # Indented with tabs, as some editors save JSON. YAML does not allow a tab there.
    tabbed = await parse(json.dumps(fixture, indent="\t"))
    assert tabbed.status_code == 200, tabbed.text
    assert tabbed.json()["config_hash"] == STUDY1_DESIGNED_CONFIG_HASH

    # Python's default JSON output writes a character outside the basic plane
    # as an escaped surrogate pair, and a raw next-line character (U+0085) is
    # legal inside a JSON string. Both come back as the text they are.
    awkward = {**fixture, "title": "Sampling \U0001f3b2 study", "expectations": "a\u0085b"}
    for text in (json.dumps(awkward), json.dumps(awkward, ensure_ascii=False)):
        parsed = await parse(text)
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["config"]["title"] == "Sampling \U0001f3b2 study"
        assert parsed.json()["config"]["expectations"] == "a\u0085b"
        assert parsed.json()["config_hash"] == config_hash(StudyConfig.model_validate(awkward))

    # A byte-order mark and leading space before the brace change nothing.
    marked = await parse("\ufeff\n " + json.dumps(fixture))
    assert marked.status_code == 200 and marked.json()["config_hash"] == (
        STUDY1_DESIGNED_CONFIG_HASH
    )

    # Text that opens with a brace and is not JSON is still read as YAML.
    flow = await parse("{title: only a title}")
    assert flow.status_code == 400
    assert "not a valid study configuration" in flow.json()["error"]["message"]
    assert "authors" in flow.json()["error"]["message"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        # PyYAML builds a date from a plain scalar that looks like one, and this is no date.
        pytest.param(
            "title: x\ncreated_at: 2026-11-31\n", "a value that cannot be read", id="bad_date"
        ),
        pytest.param("[" * 3000, "nested too deeply", id="deep_yaml"),
        pytest.param('{"a":' * 3000 + "1" + "}" * 3000, "nested too deeply", id="deep_json"),
        pytest.param('{"title": "x"', "not valid YAML or JSON", id="neither"),
    ],
)
async def test_config_parse_answers_400_for_text_no_parser_reads(
    client: httpx.AsyncClient, text: str, message: str
) -> None:
    """Never a 500: the page reports a 5xx as a server that could not be reached."""
    refused = await client.post("/api/config/parse", json={"text": text})
    assert refused.status_code == 400, refused.text
    assert message in refused.json()["error"]["message"]
    assert refused.json()["error"]["param"] == "text"
