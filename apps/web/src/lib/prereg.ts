import type { PreregRenderRequest, PreregRenderResponse } from "@qr-evals/contracts";

import { api } from "./api";

/** The preregistration hash is defined over UTF-8 Markdown with LF line endings. */
export function normalizePreregMarkdown(markdown: string): string {
  return markdown.replace(/\r\n?/g, "\n");
}

export async function sha256Hex(bytes: BufferSource): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function preregMarkdownHash(markdown: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(normalizePreregMarkdown(markdown)));
}

/**
 * Build the exact downloadable bytes, but only after verifying them against
 * the server-authored digest. A mismatch is evidence, not a recoverable detail.
 */
export async function verifiedPreregBytes(markdown: string, expectedHash: string): Promise<Uint8Array<ArrayBuffer>> {
  const bytes = new TextEncoder().encode(normalizePreregMarkdown(markdown));
  const actualHash = await sha256Hex(bytes);
  if (actualHash !== expectedHash.toLowerCase()) {
    throw new Error(`Preregistration hash mismatch (expected ${expectedHash}, calculated ${actualHash}).`);
  }
  return bytes;
}

export async function verifiedPreregBlob(markdown: string, expectedHash: string): Promise<Blob> {
  const bytes = await verifiedPreregBytes(markdown, expectedHash);
  return new Blob([bytes], { type: "text/markdown;charset=utf-8" });
}

export function renderPreregistration(body: PreregRenderRequest): Promise<PreregRenderResponse> {
  return api.prereg(body);
}

export function preregDownloadName(title: string): string {
  const stem = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "study";
  return `${stem}-preregistration.md`;
}
