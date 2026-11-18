"""The frozen study directory and its downloadable bundle (spec §4.5, D-11, D-12, R-6).

:func:`write_frozen_study` lays down the files every bundle starts with::

    study.json        {"study_id", "config_hash", "frozen_at", "config": <StudyConfig>}
    prereg.md         the rendered pre-registration (LF bytes)
    prereg.sha256     sha256 of those bytes, ``sha256sum`` format
    expectations.md   the researchers' frozen expectations text
    LICENSE-data      CC BY 4.0 (D-12)
    README.md         how to verify the hashes and re-run the analysis

:func:`iter_bundle` streams ``<study_id>.tar.zst`` of the whole study tree.
Every ``.json`` / ``.jsonl`` file passes through
:func:`strip_provider_metadata` on the way out (R-6): provider header dumps
and provider-side request ids never leave the box. The harness's own
``request_id`` (== ``sample_ref``) is *not* provider metadata and is kept -
it is the join key between generations and Tier 0 records.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import zstandard

from qr_llm_evals.models.schema import StudyConfig, canonical_json, config_hash
from qr_llm_evals.store.artifacts import iter_jsonl, read_json, write_json, write_text
from qr_llm_evals.store.paths import DataRoot, StudyDir

BUNDLE_SCHEMA_VERSION = 1
ZSTD_LEVEL = 10

#: Keys stripped (recursively) from exported JSON/JSONL, after normalising the
#: key to lower-case with ``-`` -> ``_``. Provider header dumps, provider
#: request ids and the launching account id; nothing the analysis needs.
STRIPPED_KEYS: frozenset[str] = frozenset(
    {
        "headers",
        "request_headers",
        "response_headers",
        "provider_headers",
        "raw_headers",
        "header_dump",
        "provider_request_id",
        "x_request_id",
        "cf_ray",
        "openai_organization",
        "openai_processing_ms",
        "openai_version",
        "anthropic_organization_id",
        "anthropic_request_id",
        "request_id_header",
        "owner_id",
    }
)
#: Header-shaped keys (``x-…``) are stripped wherever they appear.
STRIPPED_PREFIXES: tuple[str, ...] = ("x_",)
#: Files never exported: the rebuildable index, temp files, quarantine.
_SKIP_SUFFIXES = {".tmp", ".sqlite", ".sqlite-wal", ".sqlite-shm"}


# -- helpers -------------------------------------------------------------------


def normalise_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def prereg_sha256(markdown: str) -> str:
    """SHA-256 of the prereg Markdown as UTF-8 LF bytes (spec D-11)."""
    return hashlib.sha256(normalise_lf(markdown).encode("utf-8")).hexdigest()


def derive_study_id(study: StudyConfig) -> str:
    """Default study id: ``s_<first 12 hex of config_hash>``."""
    return f"s_{config_hash(study)[:12]}"


def _norm_key(key: str) -> str:
    return key.lower().replace("-", "_")


def _is_stripped(key: str) -> bool:
    norm = _norm_key(key)
    return norm in STRIPPED_KEYS or norm.startswith(STRIPPED_PREFIXES)


def strip_provider_metadata(obj: Any) -> Any:
    """Return a deep copy of ``obj`` with provider header / request-id keys removed."""
    if isinstance(obj, dict):
        return {
            k: strip_provider_metadata(v)
            for k, v in obj.items()
            if not (isinstance(k, str) and _is_stripped(k))
        }
    if isinstance(obj, list):
        return [strip_provider_metadata(v) for v in obj]
    return obj


def licence_data_text() -> str:
    return normalise_lf(
        resources.files("qr_llm_evals.store").joinpath("LICENSE-data").read_text(encoding="utf-8")
    )


def _readme(study_id: str, cfg_hash: str, prereg_hash: str) -> str:
    return f"""# Study bundle `{study_id}`

This directory is the complete, self-describing record of one pre-registered
study run by the qr-llm-evals harness (https://evals.entropic.science).

## Verify

- `study.json` holds the frozen study configuration; `config_hash`
  (`{cfg_hash}`) is the SHA-256 of its canonical JSON
  (sorted keys, no whitespace, UTF-8).
- `prereg.md` is the published pre-registration; `prereg.sha256`
  (`{prereg_hash}`) is `sha256sum` over its UTF-8 LF bytes:
  `sha256sum -c prereg.sha256`.
- Every `runs/<run_id>/run.json` records `config_hash` and `prereg_hash`; a
  run whose hashes differ from the files above was **not** run under this
  pre-registration.

## Layout

```
study.json · prereg.md · prereg.sha256 · expectations.md · LICENSE-data · README.md
runs/<run_id>/run.json · readiness.json · taints.jsonl
runs/<run_id>/arms/<arm_id>/cards/<card_id>/{{generations.jsonl, tier0.jsonl, judge/, scores/, inspect/}}
runs/<run_id>/sessions/<session_id>.json · questionnaires.jsonl · observers.jsonl
runs/<run_id>/analysis/<analysis_id>/{{plan.json, tables/, figures/, narrative.md, manifest.json}}
```

Withdrawn runs stay in the bundle with `withdrawn: true` in `run.json` and a
`WITHDRAWN.md` banner; nothing is deleted from a study.

## Re-run the analysis

```
pip install "qr-llm-evals[analysis]"
python -m qr_llm_evals.analysis --bundle <this directory> --run <run_id>
```

Statistics and figures are produced by scripts only; `narrative.md` is the
only LLM-written file and every number in it is checked against `tables/`.

## Licence

Raw study data in this bundle is released under CC BY 4.0 (`LICENSE-data`).
Prose (`prereg.md`, `narrative.md`) is CC BY-SA 4.0.
"""


# -- frozen study --------------------------------------------------------------


def write_frozen_study(
    root: DataRoot,
    study: StudyConfig,
    prereg_md: str,
    *,
    study_id: str | None = None,
    frozen_at: datetime | None = None,
) -> StudyDir:
    """Freeze ``study`` + its prereg under ``studies/<study_id>/``; returns the dir.

    Re-freezing the *same* config is a no-op; a different config under an
    existing ``study_id`` is refused (``FileExistsError``) - the prereg hash
    would otherwise silently stop matching the published document.
    """
    cfg_hash = config_hash(study)
    sid = study_id or derive_study_id(study)
    study_dir = root.study(sid)
    prereg_text = normalise_lf(prereg_md)
    p_hash = prereg_sha256(prereg_text)

    if study_dir.study_json.exists():
        existing = read_json(study_dir.study_json)
        if existing.get("config_hash") != cfg_hash:
            raise FileExistsError(
                f"study {sid!r} is already frozen with config_hash "
                f"{existing.get('config_hash')}; refusing to overwrite with {cfg_hash}"
            )
        if study_dir.prereg_sha256.exists():
            existing_hash = study_dir.prereg_sha256.read_text(encoding="utf-8").split()[0]
            if existing_hash != p_hash:
                raise FileExistsError(
                    f"study {sid!r} already has prereg {existing_hash}; refusing to replace"
                )
        return study_dir

    study_dir.path.mkdir(parents=True, exist_ok=True)
    at = (frozen_at or datetime.now(UTC)).isoformat(timespec="seconds")
    write_json(
        study_dir.study_json,
        {
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "study_id": sid,
            "config_hash": cfg_hash,
            "prereg_hash": p_hash,
            "frozen_at": at,
            "config": json.loads(canonical_json(study)),
        },
    )
    write_text(study_dir.prereg_md, prereg_text)
    write_text(study_dir.prereg_sha256, f"{p_hash}  prereg.md\n")
    write_text(study_dir.expectations_md, normalise_lf(study.expectations).rstrip("\n") + "\n")
    write_text(study_dir.licence_data, licence_data_text())
    write_text(study_dir.readme_md, _readme(sid, cfg_hash, p_hash))
    return study_dir


def load_frozen_study(study_dir: StudyDir) -> StudyConfig:
    """Parse ``study.json`` back into a validated :class:`StudyConfig` (hash re-checked)."""
    data = read_json(study_dir.study_json)
    study = StudyConfig.model_validate(data["config"])
    if config_hash(study) != data["config_hash"]:
        raise ValueError(f"{study_dir.study_json}: config_hash does not match its config")
    return study


# -- bundle export -------------------------------------------------------------


def _export_files(study_dir: StudyDir) -> Iterator[Path]:
    for path in sorted(p for p in study_dir.path.rglob("*") if p.is_file()):
        if path.suffix in _SKIP_SUFFIXES or path.name.endswith(".tmp"):
            continue
        yield path


def _stripped_bytes(path: Path) -> bytes | None:
    """Transformed content for JSON/JSONL files, ``None`` for files copied verbatim."""
    if path.suffix == ".json":
        data = strip_provider_metadata(json.loads(path.read_text(encoding="utf-8")))
        return (
            json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    if path.suffix == ".jsonl":
        lines = [
            json.dumps(strip_provider_metadata(row), ensure_ascii=False, allow_nan=False)
            for row in iter_jsonl(path)
        ]
        return ("".join(line + "\n" for line in lines)).encode("utf-8")
    return None


class _ZstdSink(io.RawIOBase):
    """File-like sink for ``tarfile`` that compresses into a drainable buffer."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self._cobj = zstandard.ZstdCompressor(level=level).compressobj()
        self._buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        chunk = bytes(data)
        self._buf += self._cobj.compress(chunk)
        return len(chunk)

    def drain(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out

    def finish(self) -> bytes:
        self._buf += self._cobj.flush()
        return self.drain()


def iter_bundle(
    root: DataRoot, study_id: str, *, level: int = ZSTD_LEVEL, chunk_files: int = 1
) -> Iterator[bytes]:
    """Stream ``studies/<study_id>/`` as a ``tar.zst`` (R-6 stripping applied).

    Yields compressed chunks as files are added, so a multi-GB study never
    sits in memory. Member names are ``<study_id>/<relative path>``.
    """
    study_dir = root.study(study_id)
    if not study_dir.study_json.is_file():
        raise FileNotFoundError(f"study {study_id!r} is not frozen under {root.studies_dir}")
    sink = _ZstdSink(level)
    with tarfile.open(fileobj=sink, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for n, path in enumerate(_export_files(study_dir), start=1):
            rel = path.relative_to(study_dir.path).as_posix()
            info = tarfile.TarInfo(name=f"{study_id}/{rel}")
            info.mtime = int(path.stat().st_mtime)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            content = _stripped_bytes(path)
            if content is None:
                info.size = path.stat().st_size
                with open(path, "rb") as fh:
                    tar.addfile(info, fh)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            if n % chunk_files == 0:
                chunk = sink.drain()
                if chunk:
                    yield chunk
    tail = sink.finish()
    if tail:
        yield tail


def write_bundle(root: DataRoot, study_id: str, dest: Path, *, level: int = ZSTD_LEVEL) -> Path:
    """Materialise :func:`iter_bundle` to ``dest`` (``<study_id>.tar.zst``)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        for chunk in iter_bundle(root, study_id, level=level):
            fh.write(chunk)
    return dest


def read_bundle(source: Path | bytes | Iterable[bytes]) -> dict[str, bytes]:
    """Decompress + untar a bundle into ``{member name: bytes}`` (tests, re-analysis)."""
    if isinstance(source, Path):
        raw = source.read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = b"".join(source)
    data = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            fh = tar.extractfile(member)
            if fh is not None:
                out[member.name] = fh.read()
    return out


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "STRIPPED_KEYS",
    "STRIPPED_PREFIXES",
    "ZSTD_LEVEL",
    "derive_study_id",
    "iter_bundle",
    "licence_data_text",
    "load_frozen_study",
    "normalise_lf",
    "prereg_sha256",
    "read_bundle",
    "strip_provider_metadata",
    "write_bundle",
    "write_frozen_study",
]
