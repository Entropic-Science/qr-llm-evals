import type { Conditioning } from "@qr-evals/contracts";

// Entropy conditioning: what the entropy server does to its raw bytes before it serves them to an arm.
//
// The rules mirror `Conditioning` in `backend/src/qr_llm_evals/models/schema.py`, which mirrors the entropy
// server's own: at most half of every digest is kept, and every block holds at least twice the bytes it yields.

export type ConditioningMode = NonNullable<Conditioning["mode"]>;
export type ConditioningHash = NonNullable<Conditioning["hash"]>;
export type ResolvedConditioning = Required<Conditioning>;

/** Digest size of each hash, in bytes. */
export const DIGEST_BYTES: Readonly<Record<ConditioningHash, number>> = { sha256: 32, sha512: 64, sha3_256: 32, blake2b: 64 };
export const HASH_LABELS: Readonly<Record<ConditioningHash, string>> = { sha256: "SHA-256", sha512: "SHA-512", sha3_256: "SHA3-256", blake2b: "BLAKE2b" };
/** What a study's mode is called. The page shows it and offers no choice: raw comes from a study file only. */
export const MODE_LABELS: Readonly<Record<ConditioningMode, string>> = {
  hash_truncate: "Hashed: uniform bytes",
  raw: "Raw: the device's measured bytes",
};
/** The largest raw block one digest is computed over. */
export const MAX_IN_BYTES = 4096;
/** Bytes the amplifier draws per token: `SAMPLE_COUNT` in `models/envelope.py`, which the harness sends with every request. */
export const BYTES_PER_TOKEN = 10_000;

/** What a new study starts with. */
export const HASHED_DEFAULT: ResolvedConditioning = { mode: "hash_truncate", hash: "sha256", in_bytes: 128, keep_bytes: 16 };

/** The conditioning a study runs under. A study without the block is from before the setting and ran on raw bytes. */
export function resolveConditioning(conditioning: Conditioning | null | undefined): ResolvedConditioning {
  if (conditioning === null || conditioning === undefined) return { ...HASHED_DEFAULT, mode: "raw" };
  return { ...HASHED_DEFAULT, ...conditioning };
}

/** Raw device bytes read per byte served: 8 at the standard parameters, 1 for raw. */
export function readRatio(conditioning: Conditioning | null | undefined): number {
  const { mode, in_bytes, keep_bytes } = resolveConditioning(conditioning);
  return mode === "raw" || !(keep_bytes >= 1) ? 1 : in_bytes / keep_bytes;
}

/** Raw device bytes read for one token of `BYTES_PER_TOKEN` bytes. */
export function rawBytesPerToken(conditioning: Conditioning | null | undefined): number {
  const { mode, in_bytes, keep_bytes } = resolveConditioning(conditioning);
  if (mode === "raw" || !(keep_bytes >= 1)) return BYTES_PER_TOKEN;
  return Math.ceil(BYTES_PER_TOKEN / keep_bytes) * in_bytes;
}

/** What the server would refuse in the block, each with the study key it is about. Raw ignores the parameters. */
export function conditioningProblems(conditioning: Conditioning | null | undefined): { message: string; path: string }[] {
  const { mode, hash, in_bytes, keep_bytes } = resolveConditioning(conditioning);
  if (mode === "raw") return [];
  const out: { message: string; path: string }[] = [];
  const half = (DIGEST_BYTES[hash] ?? 32) / 2;
  if (keep_bytes > half) {
    out.push({
      message: `Serving more than half of each digest may compromise the integrity of the signal; keep at most ${half} bytes of a ${HASH_LABELS[hash] ?? hash} digest.`,
      path: "conditioning.keep_bytes",
    });
  } else if (!Number.isInteger(keep_bytes) || keep_bytes < 1) {
    out.push({ message: "Hashed entropy keeps at least 1 whole byte of each digest.", path: "conditioning.keep_bytes" });
  }
  if (!Number.isInteger(in_bytes) || in_bytes < 2 * keep_bytes || in_bytes > MAX_IN_BYTES) {
    out.push({
      message: `Hashed entropy hashes blocks of ${in_bytes} raw bytes. A block holds at least twice the bytes kept from it (${2 * keep_bytes}) and at most ${MAX_IN_BYTES}.`,
      path: "conditioning.in_bytes",
    });
  }
  return out;
}
