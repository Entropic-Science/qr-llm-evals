"""Build :class:`Tier0Record` lists from the engine token log and/or API logprobs (spec §2.6, D-8).

Two acquisition paths, one builder:

* ``engine_log`` — DEP-1 (qr-sampler per-request export): one JSONL line per
  token at ``QR_TOKEN_LOG_DIR/<request_id>.jsonl`` where ``request_id`` is the
  ``sample_ref`` the harness set on the request. Each line is a
  ``TokenSamplingRecord`` (``timestamp_ns, u_value, token_id, token_rank,
  token_prob, num_candidates, shannon_entropy, temperature_used, min_p_used,
  entropy_source_used, entropy_is_fallback, …``) plus ``token_index``,
  ``cdf_lo`` (F(t−1)), ``cdf_hi`` (F(t)) and ``top_entries``
  (post-truncation top candidates ``[token_id, prob]``). Everything the record
  can carry is present, including the exact ``u`` and CDF bracket of every
  token whatever its rank, so no top-k depth limits the engine path.
* ``api_logprobs`` — degraded: only vLLM ``logprobs`` / ``top_logprobs`` from
  the chat-completions stream (:class:`~qr_llm_evals.client.sse.TokenLogprob`
  or its ``as_record()`` dict). Gives ``token_id`` (real only under
  ``--return-tokens-as-token-ids``, otherwise a stable text hash), the raw
  ``logprob``, ``rank_raw`` within the returned top-k (k = the configured
  ``top_logprobs`` depth, ``QR_EVALS_TOP_LOGPROBS``; 20 by default, not a hard
  limit) and the truncated ``entropy_raw_top20``. ``u`` / ``cdf_*`` / ``p_post`` / ``entropy_post`` are
  **absent** — never approximated — so T0-a and T4-l report *unavailable*.

``entropy_raw_top20`` is derived on both paths from the API's top-k and is a
**truncated** entropy (the mass outside the returned top-k is ignored); tables
must label it as such (:data:`ENTROPY_RAW_TOP20_LABEL`). The field keeps its
historical name for contract stability; it holds the configured top-k.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qr_llm_evals.tier0.records import entropy_nats, token_id_from_api_token
from qr_llm_evals.tier0.schema import Tier0Record, TopEntry, Window

#: How every table must label the API-derived entropy column.
ENTROPY_RAW_TOP20_LABEL = (
    "entropy of the raw returned top-k mass (truncated; mass outside the returned top-k ignored)"
)

#: Engine-log fields the builder needs beyond the API (DEP-1). Missing ⇒ the
#: line is not a DEP-1 record and :func:`parse_engine_log` refuses it.
ENGINE_LOG_REQUIRED: tuple[str, ...] = ("token_index", "token_id", "timestamp_ns")
#: Sanity cap for one request's token log (a Study 1 sample is ≤ 4k tokens).
MAX_ENGINE_LOG_TOKENS = 1_000_000


class AcquisitionError(ValueError):
    """The two sources disagree or a source is malformed for this ``sample_ref``."""


@dataclass(frozen=True, slots=True)
class ApiToken:
    """Normalised view of one API logprob entry."""

    token: str
    logprob: float
    top_logprobs: tuple[tuple[str, float], ...] = ()


def engine_log_path(log_dir: Path, request_id: str) -> Path:
    """``QR_TOKEN_LOG_DIR/<request_id>.jsonl`` with ``/`` in the id made filesystem-safe."""
    safe = request_id.replace("/", "__")
    return log_dir / f"{safe}.jsonl"


def normalise_api_tokens(chunks: Iterable[Any]) -> list[ApiToken]:
    """Accept ``TokenLogprob`` objects or ``as_record()`` dicts (``top_logprobs`` as pairs or dicts)."""
    out: list[ApiToken] = []
    for item in chunks:
        if isinstance(item, Mapping):
            token = str(item["token"])
            logprob = float(item["logprob"])
            raw_tops = item.get("top_logprobs") or []
        else:
            token = str(item.token)
            logprob = float(item.logprob)
            raw_tops = list(getattr(item, "top_logprobs", ()) or ())
        tops: list[tuple[str, float]] = []
        for alt in raw_tops:
            if isinstance(alt, Mapping):
                tops.append((str(alt["token"]), float(alt["logprob"])))
            elif isinstance(alt, Sequence) and not isinstance(alt, str | bytes) and len(alt) == 2:
                tops.append((str(alt[0]), float(alt[1])))
            else:
                tops.append((str(alt.token), float(alt.logprob)))
        if logprob > 0:
            raise AcquisitionError(f"API logprob for token {token!r} is positive ({logprob})")
        out.append(ApiToken(token=token, logprob=logprob, top_logprobs=tuple(tops)))
    return out


def parse_engine_log(path: Path) -> list[dict[str, Any]]:
    """Read one DEP-1 token log, sorted by ``token_index`` and checked for gaps/duplicates."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as err:
                raise AcquisitionError(f"{path}:{lineno}: invalid JSON: {err}") from err
            if not isinstance(obj, dict):
                raise AcquisitionError(f"{path}:{lineno}: each line must be an object")
            missing = [k for k in ENGINE_LOG_REQUIRED if k not in obj]
            if missing:
                raise AcquisitionError(f"{path}:{lineno}: not a DEP-1 record, missing {missing}")
            rows.append(obj)
            if len(rows) > MAX_ENGINE_LOG_TOKENS:
                raise AcquisitionError(f"{path}: more than {MAX_ENGINE_LOG_TOKENS} tokens")
    rows.sort(key=lambda r: int(r["token_index"]))
    for expected, row in enumerate(rows):
        if int(row["token_index"]) != expected:
            raise AcquisitionError(
                f"{path}: token_index gap or duplicate at {row['token_index']} (expected {expected})"
            )
    return rows


def _rank_raw(api: ApiToken) -> int:
    """Rank within the returned top-k: number of alternatives strictly more likely.

    Equals the raw-distribution rank whenever the sampled token is inside the
    top-k; otherwise it is the lower bound ``k`` (the token was below every
    returned alternative).
    """
    return sum(1 for _, lp in api.top_logprobs if lp > api.logprob)


def _entropy_raw_top20(api: ApiToken) -> float:
    probs = [math.exp(lp) for _, lp in api.top_logprobs]
    if not any(tok == api.token for tok, _ in api.top_logprobs):
        probs.append(math.exp(api.logprob))
    return entropy_nats(probs)


def _optional_float(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f


def _optional_int(row: Mapping[str, Any], key: str) -> int | None:
    value = row.get(key)
    return None if value is None else int(value)


def _top_entries(row: Mapping[str, Any]) -> list[TopEntry] | None:
    raw = row.get("top_entries")
    if raw is None:
        return None
    return [(int(tok), float(p)) for tok, p in raw]


def _degraded_record(
    sample_ref: str,
    index: int,
    api: ApiToken,
    *,
    timestamp_ns: int,
    session_id: str | None,
    window: Window,
) -> Tier0Record:
    return Tier0Record(
        sample_ref=sample_ref,
        token_index=index,
        token_id=token_id_from_api_token(api.token),
        logprob=api.logprob,
        rank_raw=_rank_raw(api),
        entropy_raw_top20=_entropy_raw_top20(api),
        timestamp_ns=timestamp_ns,
        session_id=session_id,
        window=window,
    )


def _engine_record(
    sample_ref: str,
    index: int,
    row: Mapping[str, Any],
    api: ApiToken | None,
    *,
    session_id: str | None,
    window: Window,
) -> Tier0Record:
    if api is None and row.get("logprob") is None:
        raise AcquisitionError(
            f"{sample_ref}[{index}]: engine record has no raw logprob and no API logprobs were given"
        )
    logprob = float(row["logprob"]) if row.get("logprob") is not None else float(api.logprob)  # type: ignore[union-attr]
    p_post = _optional_float(row, "token_prob")
    return Tier0Record(
        sample_ref=sample_ref,
        token_index=index,
        token_id=int(row["token_id"]),
        logprob=logprob,
        rank_raw=_rank_raw(api) if api is not None else int(row.get("rank_raw", row["token_rank"])),
        p_post=p_post,
        rank_post=_optional_int(row, "token_rank"),
        n_candidates=_optional_int(row, "num_candidates"),
        entropy_post=_optional_float(row, "shannon_entropy"),
        entropy_raw_top20=(
            _entropy_raw_top20(api) if api is not None else float(row.get("entropy_raw_top20", 0.0))
        ),
        temperature=_optional_float(row, "temperature_used"),
        min_p=_optional_float(row, "min_p_used"),
        u=_optional_float(row, "u_value"),
        cdf_lo=_optional_float(row, "cdf_lo"),
        cdf_hi=_optional_float(row, "cdf_hi"),
        top_entries=_top_entries(row),
        timestamp_ns=int(row["timestamp_ns"]),
        session_id=session_id,
        window=window,
        entropy_source_used=(
            None if row.get("entropy_source_used") is None else str(row["entropy_source_used"])
        ),
        is_fallback=(
            None if row.get("entropy_is_fallback") is None else bool(row["entropy_is_fallback"])
        ),
    )


def build_records(
    sample_ref: str,
    api_logprobs_chunks: Iterable[Any],
    engine_log_path: Path | None = None,
    *,
    timestamp_ns: int = 0,
    session_id: str | None = None,
    window: Window = "unsupervised",
) -> list[Tier0Record]:
    """Records for one generation from the API logprobs and, when given, the engine log.

    Args:
        sample_ref: The join key (also the engine ``request_id``).
        api_logprobs_chunks: ``TokenLogprob`` objects or ``as_record()`` dicts in
            generation order (may be empty only on the engine path when the
            engine log carries ``logprob`` itself).
        engine_log_path: DEP-1 file for this request; ``None`` ⇒ degraded path.
        timestamp_ns: Timestamp stamped on every record on the degraded path
            (the API carries none per token); ignored when the engine log is used.
        session_id, window: Supervised-mode tags inherited by every record.

    Raises:
        AcquisitionError: Malformed source, or engine/API token counts disagree
            (the harness taints the sample rather than guessing an alignment).
    """
    api = normalise_api_tokens(api_logprobs_chunks)
    if engine_log_path is None:
        return [
            _degraded_record(
                sample_ref, i, tok, timestamp_ns=timestamp_ns, session_id=session_id, window=window
            )
            for i, tok in enumerate(api)
        ]
    rows = parse_engine_log(engine_log_path)
    if api and len(api) != len(rows):
        raise AcquisitionError(
            f"{sample_ref}: engine log has {len(rows)} tokens but the API returned {len(api)}"
        )
    return [
        _engine_record(
            sample_ref, i, row, api[i] if api else None, session_id=session_id, window=window
        )
        for i, row in enumerate(rows)
    ]


__all__ = [
    "ENGINE_LOG_REQUIRED",
    "ENTROPY_RAW_TOP20_LABEL",
    "AcquisitionError",
    "ApiToken",
    "build_records",
    "engine_log_path",
    "normalise_api_tokens",
    "parse_engine_log",
]
