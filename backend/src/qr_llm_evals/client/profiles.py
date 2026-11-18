"""Transport-side provider targets for the generation client (spec §2.3).

Two halves of "a provider profile" live in two layers on purpose:

* :class:`qr_llm_evals.models.providers.ProviderProfile` (``models``, layer 0)
  shapes the **request body** — which sampling params the provider accepts,
  whether ``seed``/``logprobs`` are honoured, the ``top_logprobs`` cap. That is
  what ``build_request_body`` consumes.
* :class:`ProviderTarget` (this module, ``client``, layer 0) is the **wire
  side** — base URL, which ``SecretBag`` slot holds the bearer key, and any
  provider-specific headers. That is what :class:`~qr_llm_evals.client.openai_client.OpenAIClient`
  consumes.

``client`` and ``models`` are both layer 0 and may not import each other, so
this module reads only the ``profiles.<id>.base_url`` rows of
``config/providers.yaml`` (the same file, the same ids) and adds the transport
facts. Unknown base URLs become a ``custom`` target: plain OpenAI surface with
a bearer key from the ``custom`` slot, nothing else assumed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: ``backend/src/qr_llm_evals/client/profiles.py`` → repository root (editable install).
REPO_ROOT: Path = Path(__file__).resolve().parents[4]
DEFAULT_PROVIDERS_FILE: Path = REPO_ROOT / "config" / "providers.yaml"

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
QR_SERVER_ID = "qr_server"
CUSTOM_ID = "custom"


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    """Where to send a chat-completions request and how to authenticate it."""

    id: str
    base_url: str
    secret_name: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def needs_key(self) -> bool:
        return self.secret_name is not None

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + CHAT_COMPLETIONS_PATH


def _origin(url: str) -> str:
    """``https://api.openai.com/v1/`` → ``https://api.openai.com`` (no ``/v1``)."""
    url = "".join(url.split()).rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url.rstrip("/")


#: profile id → (SecretBag slot, extra headers). Ids match ``config/providers.yaml``.
_TRANSPORT_FACTS: dict[str, tuple[str, Mapping[str, str]]] = {
    "openai": ("openai", {}),
    "anthropic_compat": ("anthropic", {}),
}


def load_profile_base_urls(path: Path | None = None) -> dict[str, str]:
    """``{profile_id: base_url}`` for every ``config/providers.yaml`` row that has one."""
    file = path if path is not None else DEFAULT_PROVIDERS_FILE
    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    profiles = raw.get("profiles") if isinstance(raw, dict) else None
    if not isinstance(profiles, dict):
        raise ValueError(f"{file.name}: expected a top-level 'profiles' mapping")
    out: dict[str, str] = {}
    for pid, body in profiles.items():
        if isinstance(body, dict) and isinstance(body.get("base_url"), str):
            out[str(pid)] = _origin(body["base_url"])
    return out


def known_targets(path: Path | None = None) -> dict[str, ProviderTarget]:
    """The shipped external targets (openai / anthropic_compat)."""
    targets: dict[str, ProviderTarget] = {}
    for pid, base in load_profile_base_urls(path).items():
        secret, headers = _TRANSPORT_FACTS.get(pid, (CUSTOM_ID, {}))
        targets[pid] = ProviderTarget(
            id=pid, base_url=base, secret_name=secret, extra_headers=dict(headers)
        )
    return targets


def qr_server_target(vllm_origin: str) -> ProviderTarget:
    """The shared vLLM on loopback: no key, no extra headers (spec D-1)."""
    return ProviderTarget(id=QR_SERVER_ID, base_url=_origin(vllm_origin), secret_name=None)


def target_for(
    provider: str,
    base_url: str | None = None,
    *,
    vllm_origin: str | None = None,
    targets: Mapping[str, ProviderTarget] | None = None,
) -> ProviderTarget:
    """Resolve a ``ModelRef.provider`` (+ optional custom base URL) to a target.

    ``qr_server`` needs ``vllm_origin``; a known provider id returns its shipped
    target; a ``base_url`` that starts with a known provider's origin borrows
    that provider's transport facts; anything else is ``custom``.
    """
    if provider == QR_SERVER_ID:
        if not vllm_origin:
            raise ValueError("qr_server target needs the vLLM origin")
        return qr_server_target(vllm_origin)
    table = targets if targets is not None else known_targets()
    if provider in table:
        return table[provider]
    if base_url:
        wanted = _origin(base_url).lower()
        for target in table.values():
            if wanted.startswith(target.base_url.lower()):
                return ProviderTarget(
                    id=target.id,
                    base_url=_origin(base_url),
                    secret_name=target.secret_name,
                    extra_headers=dict(target.extra_headers),
                )
        return ProviderTarget(id=CUSTOM_ID, base_url=_origin(base_url), secret_name=CUSTOM_ID)
    raise ValueError(f"provider {provider!r} has no shipped target and no base_url was given")


__all__ = [
    "CHAT_COMPLETIONS_PATH",
    "CUSTOM_ID",
    "DEFAULT_PROVIDERS_FILE",
    "QR_SERVER_ID",
    "ProviderTarget",
    "known_targets",
    "load_profile_base_urls",
    "qr_server_target",
    "target_for",
]
