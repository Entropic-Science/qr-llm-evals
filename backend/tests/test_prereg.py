"""S12: frozen plan, deterministic bytes, honest draft rendering and API parity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError

from qr_llm_evals.analysis.plan import StatisticalPlan, plan_hash
from qr_llm_evals.api import create_app
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.config import load_settings
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.prereg import load_renderer, prereg_hash, render
from qr_llm_evals.store.schema import ReadinessReport

FIXTURES = Path(__file__).parent / "fixtures"
SECTIONS = (
    "Plain-language summary",
    "Hypotheses H0–H6",
    "Arms and equivalence",
    "Models and exact settings",
    "Selected eval cards",
    "Statistical plan",
    "Budget and expected wall-clock",
    "Stopping and exclusion rules",
    "Researcher expectations",
    "Observer belief item",
    "Data sharing",
    "Validity limits",
    "Hashes and frozen inputs",
)


def study1() -> StudyConfig:
    return StudyConfig.model_validate(
        yaml.safe_load((REPO_ROOT / "studies/study1.yaml").read_text(encoding="utf-8"))
    )


def small_study() -> StudyConfig:
    data = study1().model_dump(mode="json")
    data.update(
        title="Prereg golden fixture",
        authors=["Fixture author"],
        contact="fixture@example.invalid",
        cards=[{"card_id": "stub_free"}],
        expectations="No directional effect expected.",
    )
    data["arms"] = [a for a in data["arms"] if a["source"] != "prng_epsilon"]
    return StudyConfig.model_validate(data)


def readiness(config: StudyConfig, source: str = "api_logprobs") -> ReadinessReport:
    return ReadinessReport.model_validate(
        {
            "at": "2026-09-14T00:00:00Z",
            "entropy": {"rpc_ok": True, "currently_degraded": False, "fallback_count": 0},
            "arms": {
                a.id: {"available": a.availability == "available", "reason": a.unavailable_reason}
                for a in config.arms
            },
            "equivalence_ok": True,
            "determinism_tier": "sampling_deterministic",
            "tier0_source": source,
            "epsilon_supported": False,
        }
    )


def test_deterministic_and_golden_bytes() -> None:
    config = small_study()
    renderer = load_renderer(
        cards=load_cards(FIXTURES / "registry/evals"), repo_root=FIXTURES / "registry"
    )
    document = renderer.render(config)
    assert document == renderer.render(StudyConfig.model_validate_json(config.model_dump_json()))
    assert document.encode("utf-8") == (FIXTURES / "prereg/golden.md").read_bytes()
    assert "built-in method" not in document
    assert "\r" not in document
    assert prereg_hash(document) == hashlib.sha256(document.encode("utf-8")).hexdigest()
    assert prereg_hash(document.replace("\n", "\r\n")) == prereg_hash(document)
    changed = config.model_copy(update={"title": "Changed"})
    assert prereg_hash(renderer.render(changed)) != prereg_hash(document)
    assert config_hash(changed) != config_hash(config)


def test_study1_sections_and_plan_roundtrip() -> None:
    config = study1()
    document = render(config, readiness(config))
    offsets = [document.index(f"## {section}\n") for section in SECTIONS]
    assert offsets == sorted(offsets)
    blocks = re.findall(r"^```json\n(.*?)\n```$", document, re.M | re.S)
    raw = next(block for block in blocks if '"endpoints":' in block)
    plan = StatisticalPlan.model_validate_json(raw)
    expected = StatisticalPlan.from_config(config, load_cards())
    assert plan == expected
    assert plan_hash(plan) == expected.plan_hash
    assert expected.plan_hash in document and config_hash(config) in document
    assert [e.card_id for e in plan.endpoints if e.role == "primary"] == [
        "tier0_pit_residuals",
        "tier0_mmd_hamming",
        "unpredictabench",
        "novelty_bench",
    ]
    assert {e.correction for e in plan.endpoints if e.role == "primary"} == {"holm"}
    assert {e.correction for e in plan.endpoints if e.role == "secondary_confirmatory"} == {"bh"}
    assert len([c for c in plan.contrasts if c.kind == "seed_vs_seed"]) == 3
    source = next(c for c in plan.contrasts if c.kind == "source")
    assert source.a == ("qrng_unsupervised",)
    assert source.b == ("prng_seed_1", "prng_seed_7", "prng_seed_42")
    assert len([c for c in plan.contrasts if c.kind == "supervised"]) == 1
    assert not any(c.kind == "epsilon" for c in plan.contrasts)
    assert next(e for e in plan.endpoints if e.endpoint == "distinct_k").sesoi == 0.25
    assert (
        next(e for e in plan.endpoints if e.endpoint == "mmd_hamming").equivalence
        == "mmd_bootstrap"
    )
    with pytest.raises(ValidationError):
        plan.alpha = 0.1  # type: ignore[misc]
    assert isinstance(plan.endpoints, tuple)


def test_epsilon_and_unavailable_arms() -> None:
    data = study1().model_dump(mode="json")
    for arm in data["arms"]:
        if arm["source"] == "prng_epsilon":
            arm.update(availability="available", unavailable_reason=None)
    config = StudyConfig.model_validate(data)
    plan = StatisticalPlan.from_config(config, load_cards())
    assert [c.epsilon for c in plan.contrasts if c.kind == "epsilon"] == [0.01, 0.03, 0.1]
    assert "epsilon support not demonstrated" in render(config)
    data["arms"][0].update(availability="unavailable", unavailable_reason="fixture offline")
    plan = StatisticalPlan.from_config(StudyConfig.model_validate(data), load_cards())
    assert not any(c.kind == "supervised" for c in plan.contrasts)


def test_honest_readiness_and_missing_assets() -> None:
    config = study1()
    doc = render(config, readiness(config))
    assert "pit\\_ks\\_two\\_sample: unavailable" in doc
    assert "mmd\\_hamming: available under supplied readiness" in doc
    assert "mean\\_entropy: unavailable" in doc
    assert "mean\\_logprob: available under supplied readiness" in doc
    assert "UNPINNED" in doc and "unavailable" in doc.lower()
    assert "Readiness not supplied" in render(config)
    engine_doc = render(config, readiness(config, "engine_log"))
    assert "pit\\_ks\\_two\\_sample: available under supplied readiness" in engine_doc
    assert prereg_hash(doc) != prereg_hash(engine_doc)


def test_renderer_is_pure_after_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    config = study1()
    renderer = load_renderer()
    before = config.model_dump_json()

    def no_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("renderer performed I/O")

    monkeypatch.setattr(Path, "read_text", no_read)
    assert render(config, renderer=renderer) == renderer.render(config)
    assert config.model_dump_json() == before


def test_card_edits_change_plan_hash_and_bad_overrides_rejected() -> None:
    config = study1()
    cards = load_cards()
    original = StatisticalPlan.from_config(config, cards)
    cards["novelty_bench"] = cards["novelty_bench"].model_copy(
        update={"sesoi": {"distinct_k": 0.3}}
    )
    assert StatisticalPlan.from_config(config, cards).plan_hash != original.plan_hash
    data = config.model_dump(mode="json")
    data["cards"][4]["overrides"]["k"] = 1000
    with pytest.raises(ValueError, match="k_bounds"):
        StatisticalPlan.from_config(StudyConfig.model_validate(data), cards)


def test_no_cross_model_pooling_and_no_endpoint_name_collision() -> None:
    data = study1().model_dump(mode="json")
    data["models"].append(
        {"id": "external", "served_name": "external", "family": "external", "provider": "custom"}
    )
    data["arms"].append(
        {"id": "provider", "model": "external", "source": "provider", "sampling": "creative"}
    )
    plan = StatisticalPlan.from_config(StudyConfig.model_validate(data), load_cards())
    assert all("provider" not in (*c.a, *c.b) for c in plan.contrasts)
    target = next(
        e for e in plan.endpoints if e.card_id == "target_hit_ranking" and e.endpoint == "mean_rank"
    )
    assert target.requires_tier0 == "none"
    assert target.test == "blinded_rank_vs_chance"
    assert hashlib.sha256(plan.cards_json.encode("utf-8")).hexdigest() == plan.cards_hash


def test_asset_snapshots_hash_lf_and_disclose_mismatch(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "evals/_pools").mkdir(parents=True)
    questionnaire = "belief: fixture\n"
    pool = "targets: [fixture]\n"
    (tmp_path / "config/questionnaires.yaml").write_text(
        questionnaire, encoding="utf-8", newline="\r\n"
    )
    (tmp_path / "evals/_pools/targets_200.yaml").write_text(pool, encoding="utf-8", newline="\n")
    data = small_study().model_dump(mode="json")
    data["supervised"].update(
        questionnaires_hash=prereg_hash(questionnaire), target_pool_hash=prereg_hash(pool)
    )
    config = StudyConfig.model_validate(data)
    renderer = load_renderer(cards=load_cards(FIXTURES / "registry/evals"), repo_root=tmp_path)
    assert renderer.asset_hashes["questionnaires"] == prereg_hash(questionnaire)
    assert "declared hash does not match" not in renderer.render(config)
    (tmp_path / "config/questionnaires.yaml").write_text("belief: changed\n", encoding="utf-8")
    changed = load_renderer(cards=load_cards(FIXTURES / "registry/evals"), repo_root=tmp_path)
    assert "declared hash does not match" in changed.render(config)
    assert prereg_hash(changed.render(config)) != prereg_hash(renderer.render(config))


def test_user_text_cannot_inject_jinja_headings_or_json_fences() -> None:
    config = study1().model_copy(
        update={
            "expectations": "{{ 7 * 7 }}\n## injected\n<script>alert(1)</script>\n```json\n{}\n```"
        }
    )
    doc = render(config)
    assert "\n## injected\n" not in doc
    assert "<script>" not in doc.split("### Frozen study config")[0]
    assert "\\{\\{ 7 \\* 7 \\}\\}" in doc
    assert "````json\n" in doc
    with pytest.raises(ValueError, match="expectations"):
        render(config, expectations="different")


async def test_route_open_rate_limited_and_export_launch_hash_parity(tmp_path: Path) -> None:
    keys = tmp_path / "keys.json"
    key = "qrk_fixture_launch"
    keys.write_text(
        json.dumps([{"name": "fixture", "key_sha256": hashlib.sha256(key.encode()).hexdigest()}]),
        encoding="utf-8",
    )
    settings = load_settings(
        {
            "QR_EVALS_ALLOW_KEY_LAUNCH": "1",
            "QR_EVALS_DATA_DIR": str(tmp_path / "data"),
            "QR_EVALS_KEYS_FILE": str(keys),
        }
    )
    app = create_app(
        settings,
        cards=load_cards(FIXTURES / "registry/evals"),
        repo_root=FIXTURES / "registry",
        limiter=TokenBucketLimiter(burst=1, rate_per_s=0.001, clock=lambda: 0.0),
    )
    config = small_study()
    body = {
        "config": config.model_dump(mode="json"),
        "readiness": readiness(config).model_dump(mode="json"),
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post("/api/prereg/render", json=body)
        assert response.status_code == 200, response.text
        exported = response.json()
        assert exported["prereg_hash"] == prereg_hash(exported["markdown"])
        assert exported["plan_hash"] == StatisticalPlan.model_validate(exported["plan"]).plan_hash
        limited = await client.post("/api/prereg/render", json=body)
        assert limited.status_code == 429 and "retry-after" in limited.headers
        assert app.state.qr.store.list_runs() == []
        launched = await client.post(
            "/api/runs",
            json={
                **body,
                "prereg_hash": exported["prereg_hash"],
                "provider_keys": {"anthropic": "sk-ant-" + "api03-prereg-judge-0123456789"},
            },
            headers={"Authorization": f"Bearer {key}"},
        )
        assert launched.status_code == 201, launched.text
        assert launched.json()["prereg_stale"] is False
        frozen = app.state.qr.store.study(launched.json()["study_id"])
        assert frozen.prereg_md.read_bytes() == exported["markdown"].encode("utf-8")


async def test_route_rejects_unknown_cards_and_mismatched_expectations(tmp_path: Path) -> None:
    app = create_app(
        load_settings(
            {"QR_EVALS_ALLOW_KEY_LAUNCH": "1", "QR_EVALS_DATA_DIR": str(tmp_path / "data")}
        ),
        limiter=TokenBucketLimiter(burst=10),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        body = {"config": study1().model_dump(mode="json"), "expectations": "not frozen"}
        assert (await client.post("/api/prereg/render", json=body)).status_code == 400
        body.pop("expectations")
        body["config"]["cards"] = [{"card_id": "does_not_exist"}]
        assert (await client.post("/api/prereg/render", json=body)).status_code == 400
