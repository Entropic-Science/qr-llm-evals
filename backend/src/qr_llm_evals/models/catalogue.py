"""The model catalogue: which targets a study may pick (spec §2.2, §4.1 ModelRef).

``config/models.yaml`` lists the shipped targets (the qr-llm lane on the
box); dynamic ids are parsed at run time::

    qr-llm                                  catalogue row (family qr_llm, provider qr_server)
    openai:<model>                          external, provider openai
    anthropic_compat:<model>                external, provider anthropic_compat
    custom:<base_url>#<model>               external, provider custom

Only catalogue rows with ``pipeline_pinned: true`` can host a QRNG / seeded
arm: the harness owns the ``qr_*`` envelope there (spec D-1). Everything else
is a ``provider`` source with label ``provider_prng``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qr_llm_evals.models.schema import ModelRef, Provider

#: ``backend/src/qr_llm_evals/models/catalogue.py`` → repository root (editable install).
REPO_ROOT: Path = Path(__file__).resolve().parents[4]
DEFAULT_MODELS_FILE: Path = REPO_ROOT / "config" / "models.yaml"

#: The Study 1 target id (``config/models.yaml``).
QR_LLM_MODEL_ID = "qr-llm"
DEFAULT_TPS = 400.0
DEFAULT_READING_TPS = 45.0

#: ``<prefix>:`` forms understood by :func:`parse_model_id`.
DYNAMIC_PREFIXES: dict[str, Provider] = {
    "openai": "openai",
    "anthropic_compat": "anthropic_compat",
    "custom": "custom",
}


class CatalogueEntry(BaseModel):
    """One ``config/models.yaml`` row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    family: str
    provider: Provider
    served_name: str = Field(min_length=1)
    checkpoint: str | None = None
    tps: float = Field(default=DEFAULT_TPS, gt=0)
    pipeline_pinned: bool = False
    description: str = ""

    def to_model_ref(self) -> ModelRef:
        return ModelRef(
            id=self.id,
            family="qr_llm" if self.family == "qr_llm" else "external",
            provider=self.provider,
            served_name=self.served_name,
            checkpoint=self.checkpoint,
        )


class CatalogueError(ValueError):
    """``config/models.yaml`` is malformed or a model id is unknown."""


class ModelCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[CatalogueEntry, ...]
    default_tps: float = Field(default=DEFAULT_TPS, gt=0)
    reading_tps: float = Field(default=DEFAULT_READING_TPS, gt=0)

    def ids(self) -> list[str]:
        return [e.id for e in self.entries]

    def entry(self, model_id: str) -> CatalogueEntry | None:
        return next((e for e in self.entries if e.id == model_id), None)

    def model_refs(self) -> list[ModelRef]:
        return [e.to_model_ref() for e in self.entries]

    def tps_by_model(self) -> dict[str, float]:
        """The ``EstimateAssumptions.tps_by_model`` default for the SPA / prereg."""
        return {e.id: e.tps for e in self.entries}

    def resolve(self, model_id: str) -> ModelRef:
        """Catalogue row or dynamic id → :class:`ModelRef`; raises :class:`CatalogueError`."""
        entry = self.entry(model_id)
        if entry is not None:
            return entry.to_model_ref()
        return parse_model_id(model_id)

    def is_pinned(self, model_id: str) -> bool:
        """True iff the harness owns the ``qr_*`` envelope on this target (spec D-1)."""
        entry = self.entry(model_id)
        return entry is not None and entry.pipeline_pinned

    def tps_for(self, model_id: str) -> float:
        entry = self.entry(model_id)
        return entry.tps if entry is not None else self.default_tps


def parse_model_id(model_id: str) -> ModelRef:
    """Parse a dynamic external id (see module docstring); raises :class:`CatalogueError`."""
    prefix, sep, rest = model_id.partition(":")
    if not sep or prefix not in DYNAMIC_PREFIXES or not rest:
        forms = ", ".join(f"{p}:<model>" for p in DYNAMIC_PREFIXES)
        raise CatalogueError(f"unknown model id {model_id!r}; expected a catalogue id or {forms}")
    provider = DYNAMIC_PREFIXES[prefix]
    if provider == "custom":
        base_url, hash_sep, served = rest.partition("#")
        if not hash_sep or not base_url or not served:
            raise CatalogueError(f"custom model id must be custom:<base_url>#<model>: {model_id!r}")
        served_name = served
    else:
        served_name = rest
    return ModelRef(id=model_id, family="external", provider=provider, served_name=served_name)


def custom_base_url(model: ModelRef) -> str | None:
    """The ``<base_url>`` of a ``custom:<base_url>#<model>`` id, else ``None``."""
    if model.provider != "custom":
        return None
    _prefix, _sep, rest = model.id.partition(":")
    base_url, _hash, _served = rest.partition("#")
    return base_url or None


def load_catalogue(path: Path | None = None) -> ModelCatalogue:
    """Parse ``config/models.yaml``; ids must be unique."""
    file = path if path is not None else DEFAULT_MODELS_FILE
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        raise CatalogueError(f"{file.name}: expected a top-level 'models' list")
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise CatalogueError(f"{file.name}: 'defaults' must be a mapping")
    default_tps = float(defaults.get("tps", DEFAULT_TPS))
    entries: list[CatalogueEntry] = []
    for body in raw["models"]:
        if not isinstance(body, dict):
            raise CatalogueError(f"{file.name}: every model must be a mapping")
        try:
            entries.append(CatalogueEntry.model_validate({"tps": default_tps, **body}))
        except ValidationError as err:
            raise CatalogueError(f"{file.name}: {err}") from err
    ids = [e.id for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise CatalogueError(f"{file.name}: duplicate model ids {dupes}")
    return ModelCatalogue(
        entries=tuple(entries),
        default_tps=default_tps,
        reading_tps=float(defaults.get("reading_tps", DEFAULT_READING_TPS)),
    )


__all__ = [
    "DEFAULT_MODELS_FILE",
    "DEFAULT_READING_TPS",
    "DEFAULT_TPS",
    "DYNAMIC_PREFIXES",
    "QR_LLM_MODEL_ID",
    "REPO_ROOT",
    "CatalogueEntry",
    "CatalogueError",
    "ModelCatalogue",
    "custom_base_url",
    "load_catalogue",
    "parse_model_id",
]
