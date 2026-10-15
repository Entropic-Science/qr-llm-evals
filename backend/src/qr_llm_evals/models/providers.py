"""External provider profiles (spec §2.3): the OpenAI-compatible surface each
provider actually honours.

External targets go through the same httpx client as the qr-llm target but
with a different base URL and a :class:`ProviderProfile` that says which
sampling params are accepted, whether ``seed`` and ``logprobs`` are honoured
and the ``top_logprobs`` cap. Profiles ship in ``config/providers.yaml``;
an unknown base URL resolves to :data:`UNKNOWN_PROFILE_ID` ("OpenAI surface,
nothing else assumed").

Provider arms are never quantum (label ``provider_prng``) and never pinned
(``pipeline_pinned=false``); the profile only shapes the request body.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: ``backend/src/qr_llm_evals/models/providers.py`` → repository root (editable install).
REPO_ROOT: Path = Path(__file__).resolve().parents[4]
DEFAULT_PROVIDERS_FILE: Path = REPO_ROOT / "config" / "providers.yaml"

UNKNOWN_PROFILE_ID = "unknown"

SamplingParam = Literal[
    "temperature", "top_p", "min_p", "seed", "max_tokens", "stop", "logprobs", "top_logprobs"
]


class ProviderProfile(BaseModel):
    """What one OpenAI-compatible provider accepts (a ``config/providers.yaml`` row)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    base_url: str | None = None
    accepted_params: tuple[SamplingParam, ...] = ()
    seed_honoured: bool = False
    logprobs_honoured: bool = False
    top_logprobs_cap: int = Field(default=0, ge=0)
    notes: str | None = None

    def accepts(self, param: str) -> bool:
        return param in self.accepted_params

    @property
    def wants_logprobs(self) -> bool:
        return self.logprobs_honoured and self.accepts("logprobs")


#: The fallback when a provider / base URL matches no shipped profile.
UNKNOWN_PROFILE = ProviderProfile(
    id=UNKNOWN_PROFILE_ID,
    accepted_params=("temperature", "top_p", "max_tokens", "stop"),
    notes="Plain OpenAI surface, nothing else assumed.",
)


class ProviderProfileError(ValueError):
    """``config/providers.yaml`` is malformed or a provider has no profile."""


def load_provider_profiles(path: Path | None = None) -> dict[str, ProviderProfile]:
    """Parse ``config/providers.yaml`` into profiles keyed by id (always includes ``unknown``)."""
    file = path if path is not None else DEFAULT_PROVIDERS_FILE
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
        raise ProviderProfileError(f"{file.name}: expected a top-level 'profiles' mapping")
    profiles: dict[str, ProviderProfile] = {}
    for pid, body in raw["profiles"].items():
        if not isinstance(body, dict):
            raise ProviderProfileError(f"{file.name}: profile {pid!r} must be a mapping")
        try:
            profiles[str(pid)] = ProviderProfile.model_validate({"id": str(pid), **body})
        except ValidationError as err:
            raise ProviderProfileError(f"{file.name}: profile {pid!r}: {err}") from err
    profiles.setdefault(UNKNOWN_PROFILE_ID, UNKNOWN_PROFILE)
    return profiles


def _origin(url: str) -> str:
    return url.strip().rstrip("/").lower()


def profile_for(
    provider: str,
    base_url: str | None = None,
    profiles: dict[str, ProviderProfile] | None = None,
) -> ProviderProfile:
    """Resolve a ``ModelRef.provider`` (+ optional custom base URL) to a profile.

    ``custom`` providers match on base URL prefix; anything unmatched is the
    ``unknown`` profile. ``qr_server`` is not a provider profile (the qr-llm
    target has its own pinned envelope) and raises.
    """
    if provider == "qr_server":
        raise ProviderProfileError("qr_server is the pinned qr-llm target, not a provider profile")
    table = profiles if profiles is not None else load_provider_profiles()
    if provider in table and provider != UNKNOWN_PROFILE_ID:
        return table[provider]
    if base_url:
        wanted = _origin(base_url)
        for profile in table.values():
            if profile.base_url and wanted.startswith(_origin(profile.base_url)):
                return profile
    return table[UNKNOWN_PROFILE_ID]


__all__ = [
    "DEFAULT_PROVIDERS_FILE",
    "REPO_ROOT",
    "UNKNOWN_PROFILE",
    "UNKNOWN_PROFILE_ID",
    "ProviderProfile",
    "ProviderProfileError",
    "SamplingParam",
    "load_provider_profiles",
    "profile_for",
]
