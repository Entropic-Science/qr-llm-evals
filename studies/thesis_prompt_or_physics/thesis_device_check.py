#!/usr/bin/env python
"""The quantum device on its own, with no model attached ("Prompt or Physics?" thesis).

Before the study, the entropy source is characterised by itself: its raw bytes
are read in blocks of the size the sampler draws per token (10,000) and turned
into ``u`` exactly as qr-sampler's ``zscore_mean`` amplifier does
(``u = Φ((M − μ0) / (σ0 / √N))``). The report says what the device's natural
flaws look like, in the units the study's sampler check uses:

* byte level: mean and standard deviation (with 95% intervals), the bias of each
  of the eight bits, a chi-square test of the 256 byte frequencies, and the
  lag-1 correlation of consecutive bytes and of consecutive block means;
* ``u`` level: the KS distance of the blocks' ``u`` from uniform with its
  one-sided DKW upper bound, and the mean and standard deviation of ``z``;
* the verdict: the KS distance of ``u`` implied by the mean and standard
  deviation of the blocks' ``z`` (``z ~ N(δ, s²)``), with the upper end of its 95%
  range. This uses every block and also catches bytes that are correlated within
  a block (they change the spread of ``z``). A device whose upper end stays below
  the tolerance (0.005) is "close enough" to fair. About 200,000 blocks (2 GB)
  pin the distance to within roughly ±0.002; the direct KS bound on ``u`` needs
  far more blocks and is reported for information.

Bytes come from a file of raw device output (``--bytes``; dump them from the
entropy server's ``GetEntropy`` call, the same one qr-sampler uses) or, as a
fair reference, from the seeded lane's generator (``--philox-seed``: block ``k``
is ``Philox(key=seed, counter=[0, 0, 0, k])``, as in qr-sampler).

    python studies/thesis_prompt_or_physics/thesis_device_check.py --bytes device.bin --out device_check.json
    python studies/thesis_prompt_or_physics/thesis_device_check.py --philox-seed 42 --blocks 100000 --out reference.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

#: qr-sampler's amplifier defaults (models/envelope.py mirrors them).
POPULATION_MEAN = 127.5
POPULATION_STD = 73.61215932167728
SAMPLE_COUNT = 10_000
TOLERANCE = 0.005
#: Exact standard deviation of a uniform byte {0..255}: sqrt((256² − 1) / 12).
FAIR_BYTE_SD = math.sqrt((256**2 - 1) / 12)


def blocks_from_file(path: Path, block: int, limit: int | None) -> Iterator[np.ndarray]:
    chunk_blocks = max(1, 2_000_000 // block)
    done = 0
    with path.open("rb") as fh:
        while limit is None or done < limit:
            want = chunk_blocks if limit is None else min(chunk_blocks, limit - done)
            raw = fh.read(want * block)
            n = len(raw) // block
            if n == 0:
                return
            yield np.frombuffer(raw[: n * block], dtype=np.uint8).reshape(n, block)
            done += n


def blocks_from_philox(seed: int, block: int, n_blocks: int) -> Iterator[np.ndarray]:
    chunk = 200
    for start in range(0, n_blocks, chunk):
        rows = [
            np.frombuffer(
                np.random.Generator(np.random.Philox(key=seed, counter=[0, 0, 0, k])).bytes(block),
                dtype=np.uint8,
            )
            for k in range(start, min(n_blocks, start + chunk))
        ]
        yield np.stack(rows)


def predicted_gap(delta: float, scale: float) -> float:
    """``sup_t |Φ((t − δ)/s) − Φ(t)|``: KS distance of ``u`` when ``z ~ N(δ, s²)``."""
    t = np.linspace(-8.0, 8.0, 32_001)
    return float(np.max(np.abs(stats.norm.cdf((t - delta) / scale) - stats.norm.cdf(t))))


def characterise(
    chunks: Iterator[np.ndarray],
    *,
    mean0: float = POPULATION_MEAN,
    std0: float = POPULATION_STD,
    tolerance: float = TOLERANCE,
    alpha: float = 0.05,
) -> dict[str, Any]:
    counts = np.zeros(256, dtype=np.int64)
    lag_sum = 0.0
    lag_n = 0
    prev_last: int | None = None
    block_means: list[np.ndarray] = []
    block = None
    for chunk in chunks:
        block = chunk.shape[1]
        counts += np.bincount(chunk.ravel(), minlength=256)
        flat = chunk.ravel().astype(np.float64) - 127.5
        lag_sum += float(np.dot(flat[:-1], flat[1:]))
        lag_n += flat.size - 1
        if prev_last is not None:
            lag_sum += (prev_last - 127.5) * flat[0]
            lag_n += 1
        prev_last = int(chunk.ravel()[-1])
        block_means.append(chunk.mean(axis=1))
    if block is None:
        raise SystemExit("no complete block of bytes")
    n_bytes = int(counts.sum())
    values = np.arange(256, dtype=np.float64)
    mean = float(np.dot(counts, values) / n_bytes)
    var = float(np.dot(counts, (values - mean) ** 2) / (n_bytes - 1))
    sd = math.sqrt(var)
    fourth = float(np.dot(counts, (values - mean) ** 4) / n_bytes)
    kurt = fourth / var**2
    se_mean = sd / math.sqrt(n_bytes)
    se_sd = sd * math.sqrt(max(kurt - 1.0, 0.0) / (4.0 * n_bytes))
    zq = float(stats.norm.ppf(1 - alpha / 2))
    bits = []
    for b in range(8):
        ones = int(counts[((np.arange(256) >> b) & 1).astype(bool)].sum())
        p = ones / n_bytes
        bits.append({"bit": b, "p_one": p, "z": (p - 0.5) / math.sqrt(0.25 / n_bytes)})
    chi = stats.chisquare(counts)
    m = np.concatenate(block_means)
    sem0 = std0 / math.sqrt(block)
    z = (m - mean0) / sem0
    u = stats.norm.cdf(z)
    ks = stats.kstest(u, "uniform", method="asymp")
    dkw = math.sqrt(math.log(2 / alpha) / (2 * u.size))
    lag_means = float(np.corrcoef(m[:-1], m[1:])[0, 1]) if m.size > 2 else None

    def gap(mu: float, sigma: float) -> float:
        return predicted_gap((mu - mean0) / sem0, sigma / std0)

    # From the bytes, assuming they are independent within a block.
    corners = [
        gap(mu, sg)
        for mu in (mean - zq * se_mean, mean + zq * se_mean)
        for sg in (sd - zq * se_sd, sd + zq * se_sd)
    ]
    # From the blocks' z directly: catches within-block dependence as well.
    mean_z = float(np.mean(z))
    sd_z = float(np.std(z, ddof=1))
    se_mz = sd_z / math.sqrt(z.size)
    se_sz = sd_z / math.sqrt(2.0 * (z.size - 1))
    z_corners = [
        predicted_gap(dz, sz)
        for dz in (mean_z - zq * se_mz, mean_z + zq * se_mz)
        for sz in (sd_z - zq * se_sz, sd_z + zq * se_sz)
    ]
    predicted = predicted_gap(mean_z, sd_z)
    predicted_upper = max(z_corners)
    measured_upper = float(ks.statistic) + dkw
    return {
        "schema_version": 1,
        "block_size": int(block),
        "n_blocks": int(m.size),
        "n_bytes": n_bytes,
        "amplifier": {"population_mean": mean0, "population_std": std0},
        "bytes": {
            "mean": mean,
            "mean_ci": [mean - zq * se_mean, mean + zq * se_mean],
            "sd": sd,
            "sd_ci": [sd - zq * se_sd, sd + zq * se_sd],
            "fair_sd": FAIR_BYTE_SD,
            "bits": bits,
            "max_bit_bias": max(abs(b["p_one"] - 0.5) for b in bits),
            "chi_square": float(chi.statistic),
            "chi_square_p": float(chi.pvalue),
            "lag1_bytes": lag_sum / lag_n / var if lag_n else None,
            "lag1_block_means": lag_means,
        },
        "u": {
            "ks_distance": float(ks.statistic),
            "ks_p_exact_uniform": float(ks.pvalue),
            "dkw_margin": dkw,
            "upper_bound": measured_upper,
            "mean_z": mean_z,
            "mean_z_ci": [mean_z - zq * se_mz, mean_z + zq * se_mz],
            "sd_z": sd_z,
            "sd_z_ci": [sd_z - zq * se_sz, sd_z + zq * se_sz],
        },
        "prediction": {
            "ks_distance": predicted,
            "ks_distance_upper": predicted_upper,
            "from_bytes_if_independent": {
                "z_shift": (mean - mean0) / sem0,
                "z_scale": sd / std0,
                "ks_distance": gap(mean, sd),
                "ks_distance_upper": max(corners),
            },
            "note": "z ~ N(δ, s²); δ and s from the blocks' z (their 95% ranges give the upper end)",
        },
        "tolerance": tolerance,
        "within_tolerance": bool(predicted_upper < tolerance),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bytes", type=Path, help="file of raw device bytes")
    source.add_argument("--philox-seed", type=int, help="fair reference: the seeded lane's bytes")
    parser.add_argument("--blocks", type=int, help="blocks to read (default: the whole file)")
    parser.add_argument("--block-size", type=int, default=SAMPLE_COUNT)
    parser.add_argument("--population-mean", type=float, default=POPULATION_MEAN)
    parser.add_argument("--population-std", type=float, default=POPULATION_STD)
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.bytes is not None:
        chunks = blocks_from_file(args.bytes, args.block_size, args.blocks)
        origin = str(args.bytes)
    else:
        chunks = blocks_from_philox(args.philox_seed, args.block_size, args.blocks or 10_000)
        origin = f"philox seed {args.philox_seed}"
    report = characterise(
        chunks,
        mean0=args.population_mean,
        std0=args.population_std,
        tolerance=args.tolerance,
    )
    report["source"] = origin
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
