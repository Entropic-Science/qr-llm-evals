"""Legacy bytes are sacred: studies frozen before the design block must never move.

A ``StudyConfig`` without a ``design`` block is a legacy study. Its config
hash names its study directory, its plan hash names its analysis, and its
prereg hash is what was registered; a frozen ``study.json`` whose recomputed
hash differs no longer loads. These vectors were computed on ``main`` before
the design block existed. If one fails, the change under test is wrong: do not
recompute the pin.

The six ``prereg_hash`` pins are the one exception, and were recomputed once:
the pre-registration prints each arm's envelope, and the envelope gained
``qr_sample_count`` when the harness began to send the draw size. The config,
plan and cards hashes did not move, and a study registered before that keeps
the document and the hash it was registered with.

``bundle_mini`` pins the tables of three endpoints. ``bundle_wide`` is a second
legacy run that reaches the rest of what a legacy analysis computes: the MMD
permutation test, the paired permutation and paired binomial tests, the
told/actual decomposition, source guessing (chance test, outside observer,
privileged access), the stream-deviation windows, the one-sample PIT tests and
top-k coverage. Its data files were written once by a generator and are
committed as they are; its plan and the hash of every table were produced by
the code at the commit before the design block, on those files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import yaml

from qr_llm_evals.analysis.run import load_plan, run
from qr_llm_evals.cards import REPO_ROOT, load_cards
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.schema import StudyConfig, canonical_json, config_hash
from qr_llm_evals.prereg import load_renderer, prereg_hash
from qr_llm_evals.store import Store
from qr_llm_evals.store.schema import ReadinessReport

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE = FIXTURES / "bundle_mini"
WIDE = FIXTURES / "bundle_wide"

#: ``packages/contracts/fixtures/study1.json`` (the SPA's Study 1 template).
STUDY1_FIXTURE = {
    "config_hash": "2d17abd8a998e06ffa95c3bba98d248a8b49f2cd886e03a270cdb55ee5d9c4e7",
    "plan_hash": "78e2a5f28d408adbdf9db5a1cf3ce0b22400d81e3dae77d497da1008d83a9882",
    "cards_hash": "85b017938c945b18f037dbfc25857fbc882b17c4387404db0be4ae1edbe947e3",
    "prereg_hash": "1b0f478ce59b14a600644440707a20e7cb89121552e7d0561f76a985edc1e66b",
}
#: ``studies/study1.yaml`` (the operator's Study 1 file; it carries the ε arms).
STUDY1_YAML = {
    "config_hash": "186751d3585d6fe4798bd1bca529e343fb84f92454c237ee418ed31982608178",
    "plan_hash": "c16c6e9ecf0aaa5cbb3d7be3bd7b3720e115dac2a75923ea82e4c45dfc27237a",
    "cards_hash": "85b017938c945b18f037dbfc25857fbc882b17c4387404db0be4ae1edbe947e3",
    "prereg_hash": "2e915e060958cdb03fc6f9144388a821d4bb4ea29afcda22d067c646478bb163",
}
#: ``studies/thesis_prompt_or_physics/study.yaml``, rendered without readiness
#: as ``test_thesis_study.py`` renders it.
THESIS = {
    "config_hash": "54a89d70b3db8c4ec3a6f118f91e894d6e195d4a4f91f185762f0e1a4be82724",
    "plan_hash": "3d3ca5273b63e8900ed09aba5f9786618cc4b82a323c9b71c4468570146b789e",
    "cards_hash": "8d4fbae09028d24bc1940d4c296335e4097fdf1d0ff41100c547a7b4ba47094f",
    "prereg_hash": "48cede9444ad28800821018c44d5af5465f7698791e78cafceb146a8026d6025",
}
BUNDLE_MINI_PLAN_HASH = "c93ab9c741ab01fb6cf17c1ed3aca03bf28fa02f1267b28126559f65d36d12ae"
BUNDLE_MINI_ANALYSIS_ID = "a_c93ab9c741ab01fb"
LEGACY_TEMPLATE_HASH = "e864b0ab4aadce7ec00e46415dc75d50e49879c50a3625abc83876dc28c9f29e"

BUNDLE_WIDE_PLAN_HASH = "9e6d6959b77f0d46aa4109d72cfb2e75dd4ff40088c245267fc35cb79ea21084"
BUNDLE_WIDE_ANALYSIS_ID = "a_9e6d6959b77f0d46"
#: sha256 of every table file the analysis of ``bundle_wide`` writes.
BUNDLE_WIDE_TABLES = {
    "creative_diversity--distinct_1": "29398f58252b349e8610bee178d2b59675ff3b72b7419198f98f33720b5bc265",
    "creative_diversity--distinct_2": "0c5374217e0cc6137dfff9d078157627395991d4c7c1e573d15a515259182cb1",
    "creative_diversity--distinct_3": "acc299451ce59d0a185b7b1e05bd9d9fefa8f3eab3939ff08eb73a2ce9158f44",
    "creative_diversity--one_minus_mean_cosine": "34705b363b0d425c1a3c0726d644d41c45b5c0a521ec26a607ce1c53140c3c75",
    "creative_diversity--one_minus_self_bleu": "3a4305f119a84b840e191edb683d23b07f77a15b773ad50a29e116cb07907fd2",
    "creative_diversity--self_bleu": "9c7028abb543f6fb7585a5e0c1f13052ae1cb81fb95fa4782a22b1a4dc382d81",
    "creative_diversity--vendi_embed": "c41302e3b2e7a963684986e59429bdc41e50885d6c1aabcc0f936c3bcc78ea85",
    "self_referential_probe--experience_claim_rate": "f4ff1aebd55457e58a215a06e35c15b4848126c52b36a0d5c1e699f6294909e9",
    "self_referential_probe--semantic_entropy": "59909e197c59ee62373a36b3425fecdf53ed5defb9b57bad33874e165b120378",
    "self_referential_probe--told_main_effect": "aacf1fbf5eb5a2966d56635ac44ddf8bc12714294749addc55a3746a7a688a78",
    "self_referential_probe--vendi_embed": "4dc07109393dc93e1befa889c5fa1c0129ab580e625e360c8c7ee5d9a0a566f6",
    "source_guessing--accuracy_by_variant": "2033fd2dae5467bf1699f231a2a65927b0d5e3eb774c9b152090eb717e258a92",
    "source_guessing--d_prime": "3f01421c329221a872e13de812f8cc3c367766bc70dcd8e982f91066f32dde8c",
    "source_guessing--guess_accuracy": "e7f42168b0aaeef6dfaf8ac31e325e6b5816f13c4d959635c9b72a39c99d9abe",
    "stream_deviation_windows--cumulative_deviation": "0254dea47c386c40d687eabe5d0b62dfd34a43cd2a2c384445dfd1174b7e8d1d",
    "stream_deviation_windows--stouffer_z_supervised_vs_unsupervised": "db774f51b872d8d814cbe71d915f379b750d19d247d9c1dafc33ce76ae298dac",
    "stream_deviation_windows--window_permutation_p": "6c541ceb41981b0810b645c5ecc7ff55f0fbcc77ee049b4be4838850704eb828",
    "tier0_mmd_hamming--mmd_hamming": "4d5d9ed3d0466aaeaae4673850442c7663e45ae86b0798f010aba042539302e8",
    "tier0_mmd_hamming--mmd_hamming_seed_vs_seed": "f3929b7bd3da28f81348d4e0df4a00f839f08f6d6a83431dfee6290de9f44004",
    "tier0_pit_residuals--pit_anderson_darling": "5a1796e79c97e826265656bc08d6854b632ad5de7a702e483b75ee9627cd5ea8",
    "tier0_pit_residuals--pit_first_token_ks": "b57b3e1b3f80e1f4f18c480ed75e957d90f813ab6be8139bd8de11e1d38046eb",
    "tier0_pit_residuals--pit_ks_one_sample": "a9a0b4001c5d0f3e07296382d4c474c1d2834ec4c5b176d3f1d43938543a9405",
    "tier0_pit_residuals--pit_ks_two_sample": "7d67cdc753c199db524ee89d3c063b814d70995f1719725ccf489b0e5f0a0c6b",
    "tier0_pit_residuals--pit_lag1_autocorr": "a2fb7cd4840d308d32b26a450f07bae7648dc5b3a44ae3d6c1971ec809d9ff76",
    "tier0_pit_residuals--u_uniformity": "cfc8e66dbef4b7aa77409fcfbe6c47152494fb58d5bd53d1e099a4a13db867b0",
    "tier0_pit_residuals--u_vs_r_agreement": "f7eef1260d1d5c55d13b316236e2a72675784e2bae750224eda5c27a30a739d2",
    "tier0_topk_coverage": "257c100e2b610022aaac8302a92726d1a647219b6e7708bea58ab5871afc057d",
}

#: The readiness report a launch supplies, with a fixed time. Every launch from
#: the web app renders the pre-registration with one.
READINESS = {
    "at": "2026-09-14T12:00:00Z",
    "entropy": {"rpc_ok": True, "currently_degraded": False, "fallback_count": 0},
    "equivalence_ok": True,
    "determinism_tier": "sampling_deterministic",
    "tier0_source": "engine_log",
    "epsilon_supported": True,
}
#: Hash of the pre-registration of each legacy study rendered with ``READINESS``.
PREREG_WITH_READINESS = {
    "packages/contracts/fixtures/study1.json": (
        "6c1bc43437e7474ab86d7059ef573db8eb1ab8106c1800984cac68d479f7701b"
    ),
    "studies/study1.yaml": "d499604b4e9b27e2d8c4d8ea98442ae3a6e1ab37996fc4f4415fce37255a5db0",
    "studies/thesis_prompt_or_physics/study.yaml": (
        "38aeb4d01f32c8367e403737d8b23bf762431390706da52953049fe4438db6e0"
    ),
}
#: sha256 of ``README.md`` of the Study 1 fixture frozen with the prereg text below.
FROZEN_PREREG = "# Prereg\n\nbody\n"
FROZEN_README_SHA256 = "7955bd1a4baca7b322335c0a820b251ac52708a62e8da12f6acce186d32dd1b1"


def _json_study(relative: str) -> StudyConfig:
    return StudyConfig.model_validate(
        json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
    )


def _yaml_study(relative: str) -> StudyConfig:
    return StudyConfig.model_validate(
        yaml.safe_load((REPO_ROOT / relative).read_text(encoding="utf-8"))
    )


def _hashes(config: StudyConfig) -> dict[str, str]:
    """The four identities of a legacy study, each computed the way production does."""
    plan = StatisticalPlan.from_config(config, load_cards())
    # The plan digests the config on its own (it may not import models); the
    # two digests agreeing is part of what a frozen study relies on.
    assert plan.config_hash == config_hash(config)
    reloaded = StatisticalPlan.model_validate_json(plan.canonical_json())
    assert reloaded == plan and reloaded.plan_hash == plan.plan_hash
    assert "design" not in json.loads(canonical_json(config))
    return {
        "config_hash": config_hash(config),
        "plan_hash": plan.plan_hash,
        "cards_hash": plan.cards_hash,
        "prereg_hash": prereg_hash(load_renderer().render(config)),
    }


def test_study1_config_plan_cards_hashes_pinned() -> None:
    assert _hashes(_json_study("packages/contracts/fixtures/study1.json")) == STUDY1_FIXTURE
    assert _hashes(_yaml_study("studies/study1.yaml")) == STUDY1_YAML


def test_thesis_config_plan_prereg_hashes_pinned() -> None:
    assert _hashes(_yaml_study("studies/thesis_prompt_or_physics/study.yaml")) == THESIS


def test_bundle_mini_plan_roundtrip_and_analysis_id(tmp_path: Path) -> None:
    """An archived plan written before the change reloads to the same bytes and id."""
    archived = (BUNDLE / "prereg-plan.json").read_text(encoding="utf-8")
    plan = load_plan(BUNDLE / "prereg-plan.json")
    assert plan.canonical_json() + "\n" == archived
    assert plan.model_dump(mode="json") == json.loads(archived)
    assert plan.plan_hash == BUNDLE_MINI_PLAN_HASH

    bundle = tmp_path / "bundle_mini"
    shutil.copytree(BUNDLE, bundle)
    assert run(bundle, plan, run_id="run_mini") == BUNDLE_MINI_ANALYSIS_ID
    output = bundle / "runs" / "run_mini" / "analysis" / BUNDLE_MINI_ANALYSIS_ID
    assert (output / "plan.json").read_text(encoding="utf-8") == archived
    golden = {p.name: p.read_bytes() for p in sorted((BUNDLE / "golden" / "tables").glob("*.json"))}
    actual = {p.name: p.read_bytes() for p in sorted((output / "tables").glob("*.json"))}
    assert actual == golden


def test_legacy_template_hash_pinned() -> None:
    """Legacy studies render from this template; the prereg embeds its hash."""
    template = (REPO_ROOT / "docs" / "prereg-template.md.j2").read_text(encoding="utf-8")
    assert prereg_hash(template) == LEGACY_TEMPLATE_HASH
    renderer = load_renderer()
    assert renderer.template == template
    document = renderer.render(_yaml_study("studies/study1.yaml"))
    assert LEGACY_TEMPLATE_HASH in document


def test_bundle_wide_tables_pinned(tmp_path: Path) -> None:
    """Every table of a legacy run that reaches the other analysis paths keeps its bytes."""
    archived = (WIDE / "prereg-plan.json").read_text(encoding="utf-8")
    plan = load_plan(WIDE / "prereg-plan.json")
    assert plan.design is None
    assert plan.canonical_json() + "\n" == archived
    assert plan.plan_hash == BUNDLE_WIDE_PLAN_HASH

    bundle = tmp_path / "bundle_wide"
    shutil.copytree(WIDE, bundle)
    assert run(bundle, plan, run_id="run_wide") == BUNDLE_WIDE_ANALYSIS_ID
    output = bundle / "runs" / "run_wide" / "analysis" / BUNDLE_WIDE_ANALYSIS_ID
    written = {path.stem: path.read_bytes() for path in sorted((output / "tables").glob("*.json"))}
    assert {
        name: hashlib.sha256(content).hexdigest() for name, content in written.items()
    } == BUNDLE_WIDE_TABLES
    # A legacy run gets no decisions table and no run-order record.
    assert "decisions" not in written
    assert not (bundle / "runs" / "run_wide" / "run-order.json").exists()

    # The pins are worth something only while the run reaches these paths.
    tables = {name: json.loads(content) for name, content in written.items()}

    def computed(name: str) -> list[bool]:
        return [bool(row.get("available")) for row in tables[name]["comparisons"]]

    assert computed("tier0_mmd_hamming--mmd_hamming") == [True] * 5
    assert computed("tier0_pit_residuals--pit_ks_two_sample") == [True] * 5
    probe = tables["self_referential_probe--experience_claim_rate"]
    assert computed("self_referential_probe--experience_claim_rate") == [True] * 5
    assert probe["test"] == "paired_permutation" and probe["told_actual"]
    assert tables["self_referential_probe--told_main_effect"]["headline_effect"]["available"]
    guessing = tables["source_guessing--guess_accuracy"]
    assert guessing["test"] == "paired_binomial" and computed(
        "source_guessing--guess_accuracy"
    ) == [True, True, True, True, False]
    for block in ("chance_test", "outside_observer", "privileged_access"):
        assert guessing[block]["available"], block
    for name in (
        "stream_deviation_windows--cumulative_deviation",
        "stream_deviation_windows--stouffer_z_supervised_vs_unsupervised",
        "stream_deviation_windows--window_permutation_p",
        "tier0_pit_residuals--pit_ks_one_sample",
        "tier0_pit_residuals--pit_anderson_darling",
        "tier0_pit_residuals--u_uniformity",
    ):
        assert tables[name]["special"]["available"], name
    assert tables["tier0_topk_coverage"]["available"]


def test_legacy_prereg_with_a_readiness_report_pinned() -> None:
    """The document a launch freezes: rendered with a readiness report, as the web app does."""
    report = ReadinessReport.model_validate(READINESS)
    renderer = load_renderer()
    rendered = {
        relative: prereg_hash(
            renderer.render(
                _json_study(relative) if relative.endswith(".json") else _yaml_study(relative),
                report,
            )
        )
        for relative in PREREG_WITH_READINESS
    }
    assert rendered == PREREG_WITH_READINESS


def test_frozen_legacy_study_readme_pinned(tmp_path: Path) -> None:
    """The README of a study without a design block keeps the text it always had."""
    store = Store(tmp_path / "data")
    study_dir = store.freeze_study(
        _json_study("packages/contracts/fixtures/study1.json"), FROZEN_PREREG
    )
    assert study_dir.study_id == "s_" + STUDY1_FIXTURE["config_hash"][:12]
    readme = study_dir.readme_md.read_bytes()
    assert hashlib.sha256(readme).hexdigest() == FROZEN_README_SHA256
    assert b"run-order.json" not in readme
    store.close()
