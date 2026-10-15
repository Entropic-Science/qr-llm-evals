"""``Tier0Record`` — one sampled token as the sampler saw it (spec §4.4, §2.6).

The record is the union of what the two acquisition paths can provide. From
the engine's token log (``tier0_source == "engine_log"``) every field is
present, including the uniform ``u`` and the CDF interval it fell in; from vLLM
``logprobs``/``top_logprobs`` (``api_logprobs``) only the ``*_raw`` /
``top_entries`` fields are, and the endpoints that need ``u`` (T0-a) are
reported *unavailable* rather than approximated. Optional fields are therefore
the norm; the availability matrix in ``tier0/endpoints.py`` decides what each
endpoint may consume.

Records inherit ``session_id`` / ``window`` from their generation via
``sample_ref`` so supervised-mode endpoints (T4-l) can select by window.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Window = Literal["supervised", "break", "unsupervised"]
SampleRef = Annotated[str, Field(min_length=1, max_length=512)]
#: ``[token_id, probability]`` pairs of the top-k candidates after sampling transforms.
TopEntry = tuple[int, float]


class Tier0Record(BaseModel):
    """One line of ``tier0.jsonl``."""

    model_config = ConfigDict(extra="forbid")

    sample_ref: SampleRef
    token_index: int = Field(ge=0)
    token_id: int = Field(ge=0)
    logprob: float = Field(le=0, description="Raw model log-probability of the chosen token.")
    rank_raw: int = Field(ge=0, description="Rank of the chosen token in the raw distribution.")
    p_post: float | None = Field(default=None, ge=0, le=1)
    rank_post: int | None = Field(default=None, ge=0)
    n_candidates: int | None = Field(default=None, ge=1)
    entropy_post: float | None = Field(default=None, ge=0)
    entropy_raw_top20: float = Field(ge=0)
    temperature: float | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0, le=1)
    u: float | None = Field(default=None, ge=0, le=1, description="The uniform consumed.")
    cdf_lo: float | None = Field(default=None, ge=0, le=1)
    cdf_hi: float | None = Field(default=None, ge=0, le=1)
    top_entries: list[TopEntry] | None = None
    timestamp_ns: int = Field(ge=0)
    session_id: str | None = None
    window: Window = "unsupervised"
    entropy_source_used: str | None = None
    is_fallback: bool | None = None

    @model_validator(mode="after")
    def _check_interval(self) -> Tier0Record:
        if (self.cdf_lo is None) != (self.cdf_hi is None):
            raise ValueError("cdf_lo and cdf_hi must be given together")
        if self.cdf_lo is not None and self.cdf_hi is not None and self.cdf_lo > self.cdf_hi:
            raise ValueError("cdf_lo must be <= cdf_hi")
        return self


__all__ = ["SampleRef", "Tier0Record", "TopEntry", "Window"]
