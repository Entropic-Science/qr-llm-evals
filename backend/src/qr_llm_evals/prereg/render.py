"""Deterministic Jinja2 rendering with explicit, snapshot-able repository inputs.

``Renderer.render`` is pure: no clock, randomness, network, reads or writes.
``load_renderer`` is the I/O boundary; the convenience ``render`` loads the
repository inputs unless a previously loaded renderer is supplied.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel

from qr_llm_evals.cards.availability import CARD_ENDPOINTS, is_available
from qr_llm_evals.cards.estimates import CardOverrides, EstimateAssumptions, estimate_selection
from qr_llm_evals.cards.loader import REPO_ROOT, load_cards
from qr_llm_evals.cards.schema import EvalCard
from qr_llm_evals.cards.statistical_plan import StatisticalPlan
from qr_llm_evals.models.envelope import SampleContext, build_envelope
from qr_llm_evals.models.schema import StudyConfig, canonical_json, config_hash
from qr_llm_evals.prereg.hashes import normalize_lf, prereg_hash


def _text(value: object) -> str:
    """Keep user strings from creating Markdown headings, HTML or table cells."""
    text = html.escape(str(value), quote=True)
    for char in "\\`*_{}[]()#+-!|":
        text = text.replace(char, "\\" + char)
    return normalize_lf(text).replace("\n", "<br>")


def _fence(value: str, language: str = "json") -> str:
    # A user-controlled JSON string may itself contain Markdown backticks.
    longest = max(
        (len(part) for part in value.split("\n") if part and set(part) == {"`"}), default=0
    )
    fence = "`" * max(
        3, longest + 1, max((len(p) for p in re.findall(r"`+", value)), default=0) + 1
    )
    return f"{fence}{language}\n{value}\n{fence}"


@dataclass(frozen=True)
class Renderer:
    template: str
    cards: Mapping[str, EvalCard]
    asset_hashes: Mapping[str, str | None]

    def render(
        self,
        study_config: StudyConfig,
        readiness: BaseModel | None = None,
        expectations: str | None = None,
    ) -> str:
        if expectations is not None and expectations != study_config.expectations:
            raise ValueError("expectations must match the frozen StudyConfig.expectations")
        config = study_config
        plan = StatisticalPlan.from_config(config, self.cards)
        report = readiness.model_dump(mode="json") if readiness is not None else None
        tier0 = report["tier0_source"] if report else None
        warnings: list[str] = []
        for model in config.models:
            if not model.checkpoint:
                warnings.append(f"{model.id}: exact model checkpoint/revision is not frozen.")
        if config.judge.model.startswith("PIN_") or "latest" in config.judge.model.lower():
            warnings.append(
                "Judge model is unresolved; freeze an exact identifier before full run."
            )
        if config.judge.temperature not in (None, 0):
            warnings.append(
                f"Judge temperature is {config.judge.temperature}: its scores vary between "
                "repeated calls on the same reply."
            )
        if report is None:
            warnings.append(
                "Readiness not supplied: arm availability, entropy health and achieved determinism are unverified."
            )
        elif not report["equivalence_ok"]:
            warnings.append(
                "Readiness reports failed sampler equivalence; no equivalent-pipeline source claim is justified."
            )
        if report and (not report["entropy"]["rpc_ok"] or report["entropy"]["currently_degraded"]):
            warnings.append(
                "Readiness reports unhealthy/degraded quantum entropy; affected output cannot be labelled quantum."
            )
        assets = dict(self.asset_hashes)
        for name, expected in (
            (
                "questionnaires",
                config.supervised.questionnaires_hash if config.supervised else None,
            ),
            ("target_pool", config.supervised.target_pool_hash if config.supervised else None),
        ):
            actual = assets.get(name)
            if config.supervised and (not actual or not expected):
                warnings.append(f"{name}: unavailable or not frozen in the study config.")
            if expected and actual != expected:
                warnings.append(
                    f"{name}: declared hash does not match the supplied asset snapshot."
                )
        selected = [self.cards[s.card_id] for s in config.cards]
        overrides = {
            s.card_id: CardOverrides(k=s.overrides.k, items=s.overrides.items) for s in config.cards
        }
        assumptions = EstimateAssumptions.model_validate(config.estimates.model_dump())
        card_rows = []
        for card in selected:
            ov = overrides[card.id]
            revision = card.dataset.revision
            if card.dataset.scheme == "repo":
                revision = assets.get(f"dataset:{card.id}")
            if card.dataset.scheme != "derived" and not revision:
                warnings.append(
                    f"{card.id}: dataset revision/content hash unavailable; pin before full run."
                )
            status = (
                "implemented"
                if card.status == "implemented"
                else "unavailable: implementation planned"
            )
            endpoint_rows = []
            for endpoint in card.endpoints.all:
                available = card.status == "implemented"
                if card.id in CARD_ENDPOINTS:
                    available = available and is_available(endpoint, tier0)
                elif card.endpoints.requires_tier0 != "none":
                    available = available and (
                        tier0 == "engine_log" or tier0 == card.endpoints.requires_tier0
                    )
                endpoint_rows.append(
                    f"{endpoint}: {'available under supplied readiness' if available else 'unavailable'}"
                )
            card_rows.append(
                {
                    "card": card,
                    "k": ov.k if ov.k is not None else card.k,
                    "items": ov.items if ov.items is not None else card.dataset.items,
                    "revision": revision
                    or (
                        "derived from frozen run records"
                        if card.dataset.scheme == "derived"
                        else "UNPINNED"
                    ),
                    "status": status,
                    "endpoints": "; ".join(endpoint_rows),
                    "judge": config.judge.model
                    if card.judge.model == "study_judge"
                    else card.judge.model or "none",
                }
            )
        arms = []
        budget = []
        for arm in config.arms:
            result = report["arms"].get(arm.id) if report else None
            available = arm.availability == "available" and (result is None or result["available"])
            reason = arm.unavailable_reason or (result.get("reason") if result else None)
            if arm.source == "prng_epsilon" and (report is None or not report["epsilon_supported"]):
                available, reason = False, "epsilon support not demonstrated"
            status = (
                "available under supplied readiness"
                if available and result
                else "unverified"
                if available
                else "unavailable"
            )
            model = next(m for m in config.models if m.id == arm.model)
            envelope = (
                build_envelope(
                    arm, SampleContext(card_id="prereg", prompt_id="example", epoch=0, max_tokens=1)
                )
                if model.family == "qr_llm" and arm.source != "provider"
                else {}
            )
            arms.append(
                {
                    "arm": arm,
                    "status": status,
                    "reason": reason or "",
                    "envelope": canonical_json(envelope),
                }
            )
            estimate = estimate_selection(selected, arm.model, assumptions, overrides)
            row = {
                "arm": arm.id,
                "status": status,
                "tokens": estimate.tokens_per_arm,
                "generation_h": estimate.time_unsupervised_h,
                "observer_h": 0.0,
                "sessions": 0,
                "wall_h": estimate.time_unsupervised_h,
            }
            if arm.observer_mode == "supervised" and config.supervised:
                settings = config.supervised.settings
                streams = settings.streams if config.supervised.display == "tiled" else 1
                tps = min(streams * assumptions.reading_tps, assumptions.tps_by_model[arm.model])
                hours = estimate.tokens_per_arm / tps / 3600
                sessions = math.ceil(hours * 60 / settings.session_min)
                wall = (
                    sessions * settings.session_min
                    + max(0, sessions - 1) * settings.break_min
                    + config.supervised.intention_timer_min
                    + max(0, sessions - 1) * config.supervised.refocus_s / 60
                ) / 60
                row.update(observer_h=hours, sessions=sessions, wall_h=wall)
            budget.append(row)
        environment = Environment(
            undefined=StrictUndefined, autoescape=True, keep_trailing_newline=True
        )
        # All untrusted prose is escaped explicitly for Markdown, not HTML output.
        from markupsafe import Markup

        environment.filters["md"] = lambda value: Markup(_text(value))  # nosec B704 - escaped above
        environment.filters["jsonblock"] = lambda value: Markup(_fence(value))  # nosec B704 - fenced JSON
        markdown = environment.from_string(self.template).render(
            config=config,
            config_json=canonical_json(config),
            config_hash=config_hash(config),
            readiness_json=canonical_json(readiness) if readiness else "null",
            readiness=report,
            plan=plan,
            plan_json=plan.canonical_json(),
            cards=card_rows,
            arms=arms,
            budget=budget,
            base_tokens=sum(
                row["tokens"]
                for row, arm in zip(budget, config.arms, strict=True)
                if arm.availability == "available"
            ),
            assets=assets,
            warnings=warnings,
            expectations=config.expectations
            or "Not stated; record prior expectations before publishing.",
            template_hash=prereg_hash(self.template),
        )
        return normalize_lf(markdown)


def load_renderer(
    *, cards: Mapping[str, EvalCard] | None = None, repo_root: Path = REPO_ROOT
) -> Renderer:
    registry = dict(cards) if cards is not None else load_cards(repo_root / "evals")
    assets: dict[str, str | None] = {}
    paths = {
        "questionnaires": repo_root / "config/questionnaires.yaml",
        "target_pool": repo_root / "evals/_pools/targets_200.yaml",
    }
    for card in registry.values():
        if card.dataset.scheme == "repo":
            candidate = (repo_root / card.dataset.source.split(":", 1)[1]).resolve()
            if not candidate.is_relative_to(repo_root.resolve()):
                raise ValueError("dataset path escapes repository")
            paths[f"dataset:{card.id}"] = candidate
    for name, path in paths.items():
        if path.is_file():
            assets[name] = prereg_hash(path.read_text(encoding="utf-8"))
        elif path.is_dir():
            manifest = {
                p.relative_to(path).as_posix(): prereg_hash(p.read_text(encoding="utf-8"))
                for p in sorted(path.rglob("*"))
                if p.is_file()
            }
            assets[name] = config_hash(manifest) if manifest else None
        else:
            assets[name] = None
    template = (REPO_ROOT / "docs/prereg-template.md.j2").read_text(encoding="utf-8")
    return Renderer(template, MappingProxyType(registry), MappingProxyType(assets))


def render(
    study_config: StudyConfig,
    readiness: BaseModel | None = None,
    expectations: str | None = None,
    *,
    renderer: Renderer | None = None,
) -> str:
    """Render with explicit immutable inputs, or load the repository defaults."""
    return (renderer if renderer is not None else load_renderer()).render(
        study_config, readiness, expectations
    )
