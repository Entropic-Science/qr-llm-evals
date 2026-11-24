"""S12: frozen plan, deterministic bytes, honest draft rendering and API parity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
import yaml
from pydantic import ValidationError

from qr_llm_evals.analysis.decisions import NOT_CONFIRMATORY_PREFIX
from qr_llm_evals.analysis.plan import StatisticalPlan, plan_hash
from qr_llm_evals.api import create_app
from qr_llm_evals.api.ratelimit import TokenBucketLimiter
from qr_llm_evals.cards import load_cards
from qr_llm_evals.cards.design import INCONCLUSIVE_REASON_TEXT, VERDICT_RULES
from qr_llm_evals.cards.design_schema import StudyVerdict
from qr_llm_evals.cards.endpoint_meta import ENDPOINT_META
from qr_llm_evals.cards.loader import REPO_ROOT
from qr_llm_evals.config import load_settings
from qr_llm_evals.models.schema import StudyConfig, config_hash
from qr_llm_evals.prereg import Renderer, load_renderer, prereg_hash, render
from qr_llm_evals.prereg.render import _design_text
from qr_llm_evals.store.schema import ReadinessReport

FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT_FIXTURES = REPO_ROOT / "packages/contracts/fixtures"
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
#: The second-level headings of a designed study's document, in order.
DESIGNED_SECTIONS = (
    "Plain-language summary",
    "Hypotheses",
    "Decision rules",
    "Sample size",
    "Arms and equivalence",
    "Models and exact settings",
    "Selected eval cards",
    "Statistical plan",
    "Budget and expected wall-clock",
    "Stopping and exclusion rules",
    "Result checks",
    "Researcher expectations",
    "Observer belief item",
    "Data sharing",
    "Validity limits",
    "Hashes and frozen inputs",
)
PROBE = ("self_referential_probe", "experience_claim_rate")


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


def test_a_legacy_document_shows_conditioning_in_its_envelopes_only() -> None:
    """The legacy template is frozen (its hash is in ``golden.md``); the envelopes still say it."""
    config = small_study()
    renderer = load_renderer(
        cards=load_cards(FIXTURES / "registry/evals"), repo_root=FIXTURES / "registry"
    )
    legacy = renderer.render(config)
    assert "qr_conditioning" not in legacy
    data = config.model_dump(mode="json")
    hashed = renderer.render(StudyConfig.model_validate({**data, "conditioning": {}}))
    quantum = sum(a.source == "qrng" for a in config.arms)
    assert hashed.count('"qr_conditioning_mode":"hash_truncate"') == quantum > 0
    raw = renderer.render(StudyConfig.model_validate({**data, "conditioning": {"mode": "raw"}}))
    assert "qr_conditioning" not in raw
    assert len({prereg_hash(doc) for doc in (legacy, hashed, raw)}) == 3


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


# ── studies with a design block ──────────────────────────────────────────────


def designed_data() -> dict[str, Any]:
    """Study 1 with its recommended design block, as the setup pages start from it."""
    data: dict[str, Any] = json.loads(
        (CONTRACT_FIXTURES / "study1.designed.json").read_text(encoding="utf-8")
    )
    return data


def designed() -> StudyConfig:
    return StudyConfig.model_validate(designed_data())


def design_case(name: str) -> StudyConfig:
    """One of the whole studies the web app's design code is checked against."""
    cases = json.loads((CONTRACT_FIXTURES / "design.json").read_text(encoding="utf-8"))["cases"]
    return StudyConfig.model_validate(next(c["config"] for c in cases if c["name"] == name))


def _section(document: str, title: str) -> str:
    """One second-level section of a rendered document, from its heading to the next."""
    start = document.index(f"\n## {title}\n")
    end = document.find("\n## ", start + 1)
    return document[start:] if end == -1 else document[start:end]


def _columns(line: str) -> int:
    """Columns of one table row: the pipes that the escaping left standing, less one."""
    return len(re.findall(r"(?<!\\)\|", line)) - 1


def test_designed_golden_bytes() -> None:
    # After an intended change to the design template, the registry or the
    # fixture, write ``load_renderer().render(designed())`` to the golden file
    # as UTF-8 with LF line endings. The legacy golden is never rewritten.
    config = designed()
    renderer = load_renderer()
    document = renderer.render(config)
    assert document == renderer.render(StudyConfig.model_validate_json(config.model_dump_json()))
    assert document.encode("utf-8") == (FIXTURES / "prereg/golden_designed.md").read_bytes()
    assert "\r" not in document
    assert prereg_hash(document) == hashlib.sha256(document.encode("utf-8")).hexdigest()
    # The document names the template it was rendered from, and a legacy study
    # on the same renderer still gets the legacy one.
    design_hash = prereg_hash(renderer.design_template)
    legacy_hash = prereg_hash(renderer.template)
    assert design_hash != legacy_hash
    assert design_hash in document and legacy_hash not in document
    legacy = renderer.render(study1())
    assert legacy_hash in legacy and design_hash not in legacy
    assert "## Hypotheses H0–H6\n" in legacy and "## Decision rules\n" not in legacy
    assert "## Hypotheses H0–H6\n" not in document
    changed = config.model_copy(update={"title": "Changed"})
    assert prereg_hash(renderer.render(changed)) != prereg_hash(document)
    with pytest.raises(ValueError, match="design template"):
        Renderer(renderer.template, renderer.cards, renderer.asset_hashes).render(config)


def test_designed_section_order() -> None:
    config = designed()
    document = render(config, readiness(config))
    # A fenced JSON block is a single line, so every heading found is a real one.
    assert re.findall(r"^## (.+)$", document, re.M) == list(DESIGNED_SECTIONS)
    assert re.findall(r"^### (.+)$", document, re.M) == [
        "Controls",
        "Study verdict",
        "Frozen study config",
        "Supplied readiness snapshot",
    ]
    assert (
        document.index("\n## Decision rules\n")
        < document.index("\n### Controls\n")
        < document.index("\n### Study verdict\n")
        < document.index("\n## Sample size\n")
    )

    # The embedded plan is the executable contract, and it carries the design.
    blocks = re.findall(r"^```json\n(.*?)\n```$", document, re.M | re.S)
    raw = next(block for block in blocks if '"endpoints":' in block)
    plan = StatisticalPlan.model_validate_json(raw)
    expected = StatisticalPlan.from_config(config, load_cards())
    assert plan == expected and plan.design is not None
    assert plan_hash(plan) == expected.plan_hash
    assert expected.plan_hash in document and config_hash(config) in document

    # Every hypothesis: statement, outcome, comparison, test and sidedness.
    hypotheses = _section(document, "Hypotheses")
    for hypothesis in plan.design.hypotheses:
        assert f"- **Statement:** {_design_text(hypothesis.statement)}\n" in hypotheses
        assert (
            f"- **Outcome:** endpoint {_design_text(hypothesis.endpoint)} of eval card "
            f"{_design_text(hypothesis.card_id)}.\n"
        ) in hypotheses
    assert hypotheses.count("- **Comparison:** qrng\\_unsupervised (quantum) against ") == 3
    assert hypotheses.count("the pooled tokens of prng\\_seed\\_1, prng\\_seed\\_7,") == 2
    assert hypotheses.count("the per\\-prompt mean of prng\\_seed\\_1, prng\\_seed\\_7,") == 1
    assert hypotheses.count("; two\\-sided.\n") == 2
    assert hypotheses.count("; one\\-sided, upper tail") == 1
    assert "Kolmogorov–Smirnov" in hypotheses and "maximum mean discrepancy" in hypotheses
    assert "- **Equivalence margin:** ±0.05, the smallest effect of interest" in hypotheses
    assert hypotheses.count("- **Equivalence margin:** none.\n") == 2
    # Study 1 registers a claim of difference only, so nothing can come out equivalent.
    assert hypotheses.count("equivalent \\(the study registers no equivalence claim\\)") == 3
    summary = _section(document, "Plain-language summary")
    assert "3 confirmatory hypotheses: 3 outcomes, tested in 1 model and sampling" in summary
    assert "This design cannot show that the two sources are the same." in summary

    # The decision matrix, with the sentences the analysis will print.
    rules = _section(document, "Decision rules")
    rows = [line for line in rules.splitlines() if re.match(r"\| \d+ \|", line)]
    assert [_columns(row) for row in rows] == [5] * len(VERDICT_RULES)
    for rule, row in zip(VERDICT_RULES, rows, strict=True):
        assert row.startswith(f"| {rule.number} | {_design_text(rule.condition)} | ")
        assert f" | {_design_text(rule.verdict)} | " in row
        assert row.endswith(f" | {_design_text(rule.wording)} |")
        for reason in rule.reasons:
            assert _design_text(reason) in row
    for reason, text in INCONCLUSIVE_REASON_TEXT.items():
        assert f"- {_design_text(reason)}: {_design_text(text)}\n" in rules
    # The roll-up and its prefix are those of the analysis.
    for verdict in get_args(StudyVerdict):
        assert f"`{verdict}`" in rules
    assert f'"{NOT_CONFIRMATORY_PREFIX}"' in rules
    assert "prng\\_seed\\_1 vs prng\\_seed\\_7; prng\\_seed\\_1 vs prng\\_seed\\_42; " in rules
    assert "- **Positive control:** None registered.\n" in rules
    assert "no hypothesis can resolve to equivalent" in rules
    # The study as a whole cannot return a null, and the document says so.
    assert (
        "In this study verdict 4 cannot occur, because the following cannot resolve to "
        "equivalent: H1, H2, H3."
    ) in rules
    assert "and the reason recorded is then negative\\_control\\_cannot\\_fail." in rules
    assert "(9 in all)" in rules and "(3 in this study)" in rules

    # Sample sizes: a rating per hypothesis, and what the numbers do not promise.
    sizes = _section(document, "Sample size")
    table = [line for line in sizes.splitlines() if line.startswith("| H")]
    assert [_columns(line) for line in table] == [7, 7, 7]
    assert [line.split(" | ")[4] for line in table] == ["unknown", "unknown", "sufficient"]
    assert sizes.count("Unknown: no closed-form power for this test.") == 2
    # At k = 24, with the told cells of a seeded run counted once: all 30 prompts are needed.
    assert "| 30 prompts | sufficient | 0.049 in the units of the outcome; 81.4% power" in sizes
    assert table[2].endswith(" | 30 |")
    assert "counts the told cells of a seeded run as one value" in sizes
    # The control has a floor of its own, stated with the family it is corrected over.
    assert (
        "It is corrected over 9 tests, so a seed pair fails only at a p-value below α / 9."
    ) in sizes
    assert "a seed pair of a paired test on fewer than 10 prompts counts as a control" in sizes
    assert "Every paired hypothesis of this study plans at least that many." in sizes
    # What the two tests on pooled tokens cannot say.
    assert "The following are tests on pooled tokens: H1, H2." in sizes
    assert "has no margin and no effect\\-size threshold" not in sizes  # prose, not escaped text
    assert "Such a test has no margin and no effect-size threshold." in sizes
    assert (
        "\nWorst-case variance, no prompt-by-source heterogeneity; true power may be lower.\n"
    ) in sizes

    # The fixed rules, with the numbers of this study.
    fixed = _section(document, "Statistical plan")
    assert "- **Difference test of H2:** " in fixed and "one\\-sided, upper tail" in fixed
    assert "- **Equivalence test of H3:** " in fixed
    assert "In this study that is 3 tests, one per hypothesis." in fixed
    assert "Holm–Bonferroni over 1 test, one per hypothesis" in fixed
    assert "Holm–Bonferroni over 9 tests: every seed" in fixed
    assert "- **Confidence intervals:** 95% for the mean difference" in fixed
    assert (
        "10000 permutations per permutation test. That is the largest of three numbers: 10000; "
        "20 times the number of difference tests divided by α, so that the smallest p\\-value a "
        "difference test can return is at most a twentieth of its corrected level; and 2 times "
        "the number of seed\\-pair tests divided by α, so that the smallest p\\-value a "
        "seed\\-pair test can return is at most half of its corrected level."
    ) in fixed
    assert "analysis random seed 42." in fixed
    assert _design_text(expected.margin_rule) in fixed
    stopping = _section(document, "Stopping and exclusion rules")
    assert _design_text(expected.stopping_rule) in stopping
    assert "pilot runs never are" in stopping
    assert _design_text(expected.exclusion_rule) in stopping
    cards = _section(document, "Selected eval cards")
    assert "- **Confirmatory endpoints:** experience\\_claim\\_rate \\(H3\\)\n" in cards
    assert "none; every endpoint of this card is exploratory" in cards


def test_designed_controls_claims_and_levels() -> None:
    # A claim of absence, with a declared margin and a positive control that runs.
    config = design_case("equivalence_claim")
    plan = StatisticalPlan.from_config(config, load_cards())
    assert plan.design is not None and plan.design.sensitivity_epsilon == 0.1
    document = render(config)
    assert "The study registers two claims." in _section(document, "Plain-language summary")
    rules = _section(document, "Decision rules")
    # ε is printed the way the result sentence of the analysis prints it.
    assert "deliberately biased by ε = 0.10 " in rules
    assert (
        "- **Positive control:** prng\\_epsilon\\_010, biased by ε = 0.10, against "
        "prng\\_seed\\_1, prng\\_seed\\_7, prng\\_seed\\_42.\n"
    ) in rules
    assert "- **The claim includes equivalence** in this study.\n" in rules
    hypotheses = _section(document, "Hypotheses")
    assert "- **Equivalence margin:** ±0.05, declared in this study.\n" in hypotheses
    assert hypotheses.count("- **Verdicts it cannot reach:** none.\n") == 2
    assert "equivalent \\(this test has no equivalence margin\\)" in hypotheses
    assert "more than the dataset of the eval holds" in _section(document, "Sample size")
    assert "Holm–Bonferroni over 2 tests, one per hypothesis" in document

    # The same claim with the biased arm switched off: the control is absent.
    document = render(design_case("control_switched_off"))
    assert (
        "- **Positive control:** Registered at ε = 0.03, but its arm "
        "\\(prng\\_epsilon\\_003\\) is not available in this configuration."
    ) in _section(document, "Decision rules")
    assert "equivalent \\(no positive control is available\\)" in document
    assert "standard deviations of the per\\-prompt difference" in document

    # Too few prompts: said where the sample size is rated, and on the hypothesis.
    document = render(design_case("below_floor"))
    assert "| insufficient | None: 6 prompts cannot reach significance" in document
    assert "effect \\(too few prompts to reach significance at any effect size\\)" in document

    # A stricter level moves the intervals, the permutations and every sentence
    # that names alpha. Two (model, sampling) comparisons stay apart.
    data = design_case("two_cells").model_dump(mode="json")
    data["design"]["alpha"] = 0.005
    config = StudyConfig.model_validate(data)
    plan = StatisticalPlan.from_config(config, load_cards())
    # Six hypotheses: 20 x 6 / 0.005. The twelve seed pairs (three and one, for three
    # outcomes) ask for 2 x 12 / 0.005 = 4 800, which that covers.
    assert plan.permutations == 24000
    document = render(config)
    assert "6 confirmatory hypotheses: 3 outcomes, tested in 2 model and sampling" in document
    assert "- **alpha** is 0.005, the significance level of the study.\n" in document
    assert "99.5% for the mean difference of a paired test; 99% for an equivalence" in document
    assert "24000 permutations per permutation test" in document
    rules = _section(document, "Decision rules")
    assert "**qr\\-llm · creative** (H1, H3, H5)\n" in rules
    assert "**qr\\-llm · t1\\_minp** (H2, H4, H6)\n" in rules


def test_designed_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    nasty = "{{ 7 * 7 }}\n## injected\n<script>alert(1)</script> a|b it's\n```json\n{}\n```"
    data = designed_data()
    # A model id is free text. It reaches every hypothesis statement, the
    # heading of each hypothesis, the controls and the sample-size table.
    model = f"model {nasty}"
    data["models"][0]["id"] = model
    for arm in data["arms"]:
        arm["model"] = model
    data["estimates"]["tps_by_model"] = {model: 400.0}
    data["models"][0]["served_name"] = nasty
    data.update(title=nasty, authors=[nasty], contact=nasty, expectations=nasty)
    switched_off = next(a for a in data["arms"] if a["availability"] == "unavailable")
    switched_off["unavailable_reason"] = nasty
    # A label comes from the registry table. It is escaped all the same.
    label = f"label {nasty}"
    monkeypatch.setitem(
        ENDPOINT_META, PROBE, ENDPOINT_META[PROBE].model_copy(update={"label": label})
    )
    config = StudyConfig.model_validate(data)
    document = render(config)

    plan = StatisticalPlan.from_config(config, load_cards())
    assert plan.design is not None
    probe = plan.design.hypotheses[2]
    assert probe.label == label and probe.model == model and model in probe.statement

    # Nothing made a heading, and no table gained or lost a column.
    assert re.findall(r"^## (.+)$", document, re.M) == list(DESIGNED_SECTIONS)
    assert "\n## injected" not in document
    for title, columns in (("Decision rules", 5), ("Sample size", 7), ("Arms and equivalence", 9)):
        table = [line for line in _section(document, title).splitlines() if line.startswith("| ")]
        assert table and {_columns(line) for line in table} == {columns}

    # Outside the fenced JSON no markup and no template expression survives.
    prose = re.sub(r"^(`{3,})json\n.*?\n\1$", "", document, flags=re.M | re.S)
    assert "```" not in prose and "<script>" not in prose and "{{" not in prose
    escaped = _design_text(nasty)
    assert escaped == (
        "\\{\\{ 7 \\* 7 \\}\\}<br>\\#\\# injected<br>&lt;script&gt;alert\\(1\\)&lt;/script&gt; "
        "a\\|b it&#x27;s<br>\\`\\`\\`json<br>\\{\\}<br>\\`\\`\\`"
    )
    for title in (
        "Plain-language summary",  # the model id, in a validity warning
        "Hypotheses",
        "Decision rules",
        "Sample size",
        "Arms and equivalence",
        "Models and exact settings",
        "Researcher expectations",
    ):
        assert escaped in _section(prose, title), title
    assert document.startswith(f"# {escaped} — pre-registration\n")
    assert f"- **Authors:** {escaped}\n- **Contact:** {escaped}\n" in document

    # Each new path carries the escaped text: the statement, the heading of the
    # hypothesis, the controls of its comparison and its sample-size row.
    hypotheses = _section(prose, "Hypotheses")
    assert f"- **Statement:** {_design_text(probe.statement)}\n" in hypotheses
    assert f"**H3: {_design_text(label)}, {_design_text(model)} · creative**\n" in hypotheses
    controls = _section(prose, "Decision rules")
    assert f"**{_design_text(model)} · creative** (H1, H2, H3)\n" in controls
    sizes = _section(prose, "Sample size")
    assert (
        f"| H3 | {_design_text(label)} | {_design_text(model)} · creative | 30 prompts |" in sizes
    )
    # The embedded JSON is fenced past the backticks it contains, and an
    # apostrophe keeps its entity whole.
    assert "````json\n" in document
    assert "&\\#x27;" not in document
    with pytest.raises(ValueError, match="expectations"):
        render(config, expectations="different")


# ── what a designed document may not promise ─────────────────────────────────


def _prose(document: str) -> str:
    """The document without its fenced JSON, which carries the registry's own card text."""
    return re.sub(r"^(`{3,})json\n.*?\n\1$", "", document, flags=re.M | re.S)


def _card_block(document: str, card_id: str) -> str:
    """The paragraph and list of one card in "Selected eval cards"."""
    cards = _section(document, "Selected eval cards")
    start = cards.index(f"**{_design_text(card_id)}:**")
    end = cards.find("\n**", start + 2)
    return cards[start : end if end != -1 else None]


def test_designed_document_promises_no_outcome_the_harness_does_not_compute() -> None:
    """Invariant 10: no pre-registration text may describe a feature that does not exist.

    The legacy endpoint list calls every endpoint of an implemented card
    "available under supplied readiness". The harness reads one value per
    reply, so an outcome that needs several replies scored together is never
    computed, and a designed document that registered it as available and as
    "reported with an effect size and an interval" promised what does not exist.
    """
    config = designed()
    registry = load_cards()
    for document in (render(config), render(config, readiness(config))):
        prose = _prose(document)
        assert "available under supplied readiness" not in _section(prose, "Selected eval cards")
        not_computed = [
            (card_id, endpoint)
            for card_id in (selection.card_id for selection in config.cards)
            for endpoint in registry[card_id].endpoints.primary
            if registry[card_id].status == "implemented"
            and not ENDPOINT_META[(card_id, endpoint)].executable
        ]
        # The seven outcomes that need grouped scoring are among them.
        assert {
            ("unpredictabench", "ks100_pass_rate"),
            ("novelty_bench", "distinct_k"),
            ("creative_diversity", "vendi_embed"),
            ("semantic_entropy_sqa", "semantic_entropy"),
            ("sentience_statements", "cross_negation_consistency"),
            ("thurstonian_self_outcomes", "cycle_rate"),
            ("moralchoice_consistency", "action_entropy"),
        } <= set(not_computed)
        for card_id, endpoint in not_computed:
            block = _card_block(prose, card_id)
            line = next(line for line in block.splitlines() if line.startswith("- **Endpoints:**"))
            sentence = next(
                part for part in line.split(". ") if _design_text(endpoint) in part.split(": ")[-1]
            )
            # Listed as not computed, or as unavailable where the supplied readiness
            # reports no token records for it. Never under a heading that says it is reported.
            assert sentence.removeprefix("- **Endpoints:** ").startswith(
                ("Not computed by the harness and reported as unavailable: ", "Unavailable, ")
            ), (card_id, endpoint, sentence)
        # No sentence says that every exploratory endpoint is reported.
        assert "It is reported with an effect size and an interval" not in prose
        assert "Exploratory results are reported with effect sizes and intervals" not in prose
        assert (
            "Where the harness computes it, it is reported with an effect size and an interval; "
            "an endpoint the harness does not compute is reported as unavailable."
        ) in _section(prose, "Hypotheses")
        assert "Where the harness computes an exploratory endpoint" in prose
    # A confirmatory endpoint is listed as that, and a computed headline one as exploratory.
    block = _card_block(_prose(render(config)), "self_referential_probe")
    assert "- **Endpoints:** Confirmatory: experience\\_claim\\_rate \\(H3\\)." in block
    block = _card_block(_prose(render(config)), "altered_state_meq")
    assert "- **Endpoints:** Exploratory: meq30\\_total." in block
    assert "Exploratory, reported only where the analysis has a value for it: " in block

    # Without a readiness report nothing about token records is called available;
    # with one, nothing is called unverified.
    unverified = "which no readiness report has verified"
    without, supplied = render(config), render(config, readiness(config))
    assert unverified in _card_block(_prose(without), "tier0_pit_residuals")
    assert unverified not in _card_block(_prose(without), "novelty_bench")
    assert unverified not in supplied
    # The readiness of this test reports API logprobs only. The KS test needs the token
    # log of the engine, so the document says that its hypothesis would be unavailable.
    assert (
        "- **Endpoints:** Confirmatory, but the supplied readiness reports no token records it "
        "can use, so it would resolve to unavailable: pit\\_ks\\_two\\_sample \\(H1\\)."
    ) in _card_block(_prose(supplied), "tier0_pit_residuals")
    assert "- **Endpoints:** Confirmatory: mmd\\_hamming \\(H2\\)." in _card_block(
        _prose(supplied), "tier0_mmd_hamming"
    )


def test_designed_document_does_not_contradict_itself_about_roles() -> None:
    """The card purposes were written for the built-in plan: "a study primary", "(H5)".

    Printed in a designed document they stood one line above "Confirmatory
    endpoints: none" and named hypotheses this document numbers differently.
    """
    registry = load_cards()
    purposes = " ".join(registry[selection.card_id].purpose for selection in designed().cards)
    assert "study primary" in purposes and "Secondary confirmatory" in purposes  # the source
    for config in (designed(), design_case("two_cells"), design_case("equivalence_claim")):
        prose = _prose(render(config))
        for phrase in ("study primary", "primary.", "secondary confirmatory"):
            assert phrase not in prose.lower(), phrase
        cards = _section(prose, "Selected eval cards")
        # A hypothesis id appears in the card list only where the plan put it.
        assert plan_ids(cards) <= {
            h.id for h in (StatisticalPlan.from_config(config, registry).design or _none).hypotheses
        }
        # Each card is named by its title.
        assert (
            "**self\\_referential\\_probe:** T4\\-a Self\\-referential experience probe\n" in cards
        )
        # The permutation count of a card's task is not the plan's, and is not printed as if it were.
        assert "permutations&#x27;: 2000" not in prose
        assert "- **Task arguments:** \\{\\}" in _card_block(prose, "tier0_mmd_hamming")
        assert "whatever the task arguments of a card say" in cards
        # The embedded snapshots still carry the registry text, and the document says how to read it.
        assert "study primary" in render(config)
        assert "that does not apply to this study" in _section(prose, "Statistical plan")


class _NoDesign:
    hypotheses: tuple[Any, ...] = ()


_none = _NoDesign()


def plan_ids(text: str) -> set[str]:
    """Every hypothesis id (H1, H2, ...) that a text names."""
    return set(re.findall(r"\bH\d+\b", text))


def test_designed_document_says_what_the_design_cannot_conclude() -> None:
    # A claim of absence with two tests on pooled tokens: the study cannot return a null.
    document = render(design_case("equivalence_claim"))
    rules = _section(document, "Decision rules")
    assert (
        "In this study verdict 4 cannot occur, because the following cannot resolve to "
        "equivalent: H1, H2."
    ) in rules
    assert "A hypothesis that has a margin can still resolve to equivalent on its own." in rules
    # 'Equivalent' rests on a positive control that the ratings do not cover.
    assert "The sample-size ratings do not cover this comparison." in rules
    assert (
        "so whether the control will be detected is not rated. Where it is not detected, the "
        "hypothesis is inconclusive, not equivalent."
    ) in rules
    # One seeded run of k = 24 against three, at the uncorrected level, on 30 prompts: 0.060.
    # MEQ-30 (k = 10, 50 prompts): 0.5 * sqrt(1/10 + 1/30) x 2.801585 / sqrt(50) = 0.072.
    assert (
        "- **Shift the positive control must produce:** H3: by at least 0.060; "
        "H4: by at least 0.072\n"
    ) in rules
    sizes = _section(document, "Sample size")
    assert "The following are tests on pooled tokens: H1, H2." in sizes
    assert "Any corrected p-value below alpha resolves to effect, however small" in sizes
    # A study without a positive control prints no shift, and one whose control is switched off
    # cannot reach 'equivalent' at all.
    for config in (designed(), design_case("control_switched_off")):
        assert "Shift the positive control must produce" not in render(config)

    # Every outcome can be equivalent: verdict 4 is within reach and nothing says otherwise.
    data = design_case("equivalence_claim").model_dump(mode="json")
    data["design"]["confirmatory"] = [
        ref for ref in data["design"]["confirmatory"] if not ref["card_id"].startswith("tier0_")
    ]
    document = render(StudyConfig.model_validate(data))
    assert "verdict 4 cannot occur" not in document
    assert "tests on pooled tokens" not in _section(document, "Sample size")

    # Arms run one after another, and the document says what that leaves open.
    assert "Arms are not randomised against each other in time" in _section(
        render(designed()), "Validity limits"
    )


def test_designed_document_states_the_control_floor_and_the_way_out() -> None:
    # Eight prompts: enough for the difference test, fewer than the control is asked for.
    document = render(design_case("control_below_floor"))
    sizes = _section(document, "Sample size")
    # The document says what is true one prompt below the floor too: the control is not
    # certain to be able to fail, and the analysis does not take it. It does not say that
    # the control cannot fail.
    assert (
        "It is corrected over 9 tests, so a seed pair fails only at a p-value below α / 9. A "
        "paired test on n prompts has no exact p-value below 2 / 2ⁿ. From 10 prompts on, that "
        "smallest p-value is at most half the level a seed pair must reach. With fewer prompts "
        "it is not certain that two seeds that differ as much as the outcome allows would fail "
        "the control, so the harness does not accept such a control: a seed pair of a paired "
        "test on fewer than 10 prompts counts as a control that could not have failed, and the "
        "hypothesis is invalid (row 3)."
    ) in sizes
    assert "control cannot fail" not in document
    assert "would still pass" not in document
    assert (
        "In this study the following plan fewer prompts than that and can only resolve to "
        "invalid: H3."
    ) in sizes
    assert (
        "| 8 prompts | insufficient | None: with 8 prompts it is not certain that the "
        "seed\\-against\\-seed control can fail, so the analysis does not count it as passed "
        "and the hypothesis can only resolve to invalid. At least 10 prompts are needed. |"
    ) in sizes
    hypotheses = _section(document, "Hypotheses")
    assert (
        "effect \\(fewer prompts than the seed\\-against\\-seed control is asked for, "
        "so the hypothesis is invalid\\)"
    ) in hypotheses
    assert (
        "So does a control that is not accepted as one that could have failed. That is a seed "
        "pair whose paired test had fewer prompts than the floor stated in the Sample size "
        "section, or whose permutation test ran too few permutations to return a p-value below "
        "the corrected level."
    ) in _section(document, "Decision rules")
    # Seven seeds at alpha 0.005: the seed pairs decide the permutations, 2 x 63 / 0.005.
    document = render(design_case("seven_seeds"))
    assert "25200 permutations per permutation test. That is the largest of" in document
    assert "It is corrected over 63 tests" in _section(document, "Sample size")
    # Five seeds at the same level: the difference tests decide them, 20 x 3 / 0.005.
    document = render(design_case("five_seeds_alpha_0_005"))
    assert "12000 permutations per permutation test. That is the largest of" in document
    assert "Holm–Bonferroni over 30 tests: every seed" in document

    # Where the eval has no more prompts, the table names the repetitions that would do,
    # or says that none within the eval's bounds does.
    sizes = _section(render(design_case("fewer_repetitions")), "Sample size")
    assert (
        "| 81, more than the dataset of the eval holds. With k = 27, its 30 prompts are enough |"
    ) in sizes
    assert (
        "| 101, more than the dataset of the eval holds. No number of repetitions the eval "
        "allows makes its 30 prompts enough |"
    ) in sizes


def test_designed_document_says_when_a_hypothesis_plans_no_data() -> None:
    """A test on pooled tokens in a study with no eval on a prompt list of its own.

    The completeness condition divides by the planned generations. With none
    planned it cannot be computed and is not applied, so the document may not
    print "0 generations per arm" and leave row 2 standing as if it held.
    """
    document = render(design_case("no_planned_data"))
    hypotheses = _section(document, "Hypotheses")
    sentence = (
        "- **Planned data:** 0 generations per arm, counted on the selected evals that run on a "
        "prompt list fixed at registration; an eval derived from the replies of another eval is "
        "not counted. This study selects no eval that is counted, so this hypothesis plans no "
        "data: the share of planned data that was analysed cannot be computed, and row 2 of the "
        "decision matrix does not apply to it.\n"
    )
    assert hypotheses.count(sentence) == 2
    # A study that plans its token-stream tests says nothing of the kind.
    assert "plans no data" not in render(designed())


def test_a_designed_document_states_the_conditioning_the_study_carries() -> None:
    renderer = load_renderer()
    data = designed_data()
    assert "Entropy conditioning" not in renderer.render(StudyConfig.model_validate(data))

    hashed = renderer.render(StudyConfig.model_validate({**data, "conditioning": {}}))
    arms = _section(hashed, "Arms and equivalence")
    assert "Entropy conditioning: hashed." in arms
    assert "blocks of 128 bytes" in arms and "`sha256`" in arms
    assert "first 16 bytes of each digest" in arms
    assert "so 8 raw device bytes are read for every byte served" in arms
    assert '"qr_conditioning_keep_bytes":16' in hashed

    raw = renderer.render(StudyConfig.model_validate({**data, "conditioning": {"mode": "raw"}}))
    assert "Entropy conditioning: raw." in _section(raw, "Arms and equivalence")
    assert "qr_conditioning" not in raw
