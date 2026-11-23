import type { StudyConfig, StudyView } from "@qr-evals/contracts";
import { API_BASE, apiFetch, ApiError } from "./api";

export interface Comparison {
  id: string;
  kind: string;
  arms_a: string[];
  arms_b: string[];
  available: boolean;
  effect?: number;
  statistic?: number;
  p_adjusted?: number;
  p_value?: number;
  ci_lower?: number;
  ci_upper?: number;
  ci_level?: number;
  epsilon?: number | null;
  claim_supported?: boolean;
  reason?: string;
  [key: string]: unknown;
}
export interface ResultsTableData {
  card_id: string;
  endpoint: string;
  tier: number;
  role: string;
  available: boolean;
  correction: string;
  comparisons: Comparison[];
  controls: Comparison[];
  epsilon_curve: Comparison[];
  test: string;
  precision?: number;
  caption?: string;
  [key: string]: unknown;
}
/**
 * A table the analysis writes that is not an endpoint table: it describes the run and tests nothing, so it
 * has no role, tier, test, correction or comparison rows (`tier0_topk_coverage` is one).
 */
export type DescriptiveTableData = Record<string, unknown>;
/** Whether a loaded table has what the endpoint views read: comparison rows and a tier. */
export function isEndpointTable(table: unknown): table is ResultsTableData {
  if (typeof table !== "object" || table === null) return false;
  const fields = table as Record<string, unknown>;
  return Array.isArray(fields.comparisons) && typeof fields.tier === "number";
}
export interface FrozenPlan {
  claim_rule: string;
  epsilon_rule?: string;
  exclusion_rule?: string;
  endpoints?: {
    card_id: string;
    endpoint: string;
    role: string;
    tier: number;
  }[];
  [key: string]: unknown;
}
export interface Manifest {
  plan_hash?: string;
  narrative?: { published?: boolean };
  [key: string]: unknown;
}
export interface ProvenanceArm {
  arm_id: string;
  source_label: string;
  quantum?: boolean;
  samples: number;
  tainted_samples: number;
  /** Samples the clean analysis leaves out for a taint. An index written before this count has none. */
  excluded_samples?: number;
  failed_samples?: number;
}
export interface PublicRun {
  run_id: string;
  kind: string;
  state: string;
  withdrawn?: boolean;
  taints?: unknown[];
  determinism_tier?: string;
}
export interface PublicStudy extends Omit<StudyView, "study" | "runs"> {
  study: {
    config_hash?: string;
    prereg_hash?: string;
    frozen_at?: string;
    config?: Partial<StudyConfig> & { title?: string };
  };
  runs: PublicRun[];
}
export interface AnalysisIndex {
  plan: FrozenPlan;
  manifest: Manifest;
  tables: string[];
  figures: string[];
  provenance: ProvenanceArm[];
}
export interface AnalysisResults extends AnalysisIndex {
  loadedTables: Record<string, ResultsTableData>;
  narrative: string | null;
}
export function studyPath(id: string) {
  return `/api/studies/${encodeURIComponent(id)}`;
}
export function artifactPath(
  studyId: string,
  analysisId: string,
  runId: string,
  artifact: string,
) {
  return `${studyPath(studyId)}/analysis/${encodeURIComponent(analysisId)}/${artifact}?run_id=${encodeURIComponent(runId)}`;
}
export function downloadUrl(id: string, artifact: string) {
  return `${API_BASE}${studyPath(id)}/${artifact}`;
}
export async function loadText(path: string, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE}${path}`, signal ? { signal } : {});
  if (!response.ok)
    throw new ApiError(
      `Artifact unavailable (${response.status})`,
      response.status,
    );
  return response.text();
}
export function loadStudy(id: string, signal: AbortSignal) {
  return apiFetch<PublicStudy>(studyPath(id), { signal });
}
export async function loadAnalysis(
  studyId: string,
  analysisId: string,
  runId: string,
  signal: AbortSignal,
): Promise<AnalysisResults> {
  const path = (artifact: string) =>
    artifactPath(studyId, analysisId, runId, artifact);
  const index = await apiFetch<AnalysisIndex>(path("index"), { signal });
  const tables = await Promise.all(
    index.tables.map(
      async (name) =>
        [
          name,
          await apiFetch<ResultsTableData>(
            path(`tables/${encodeURIComponent(name)}.json`),
            { signal },
          ),
        ] as const,
    ),
  );
  let narrative: string | null = null;
  if (index.manifest.narrative?.published === true) {
    try {
      narrative = await loadText(path("narrative.md"), signal);
    } catch (error) {
      if (signal.aborted)
        throw error; /* Optional narrative must not hide numerical results. */
    }
  }
  return { ...index, loadedTables: Object.fromEntries(tables), narrative };
}
/**
 * How many samples the clean analysis leaves out, summed over the arm ledger; nothing when the ledger is
 * empty or any row lacks the count, so a missing count is never read as zero.
 */
export function excludedSamples(
  arms: readonly ProvenanceArm[] | undefined,
): number | undefined {
  if (!arms?.length) return undefined;
  let total = 0;
  for (const arm of arms) {
    if (typeof arm.excluded_samples !== "number") return undefined;
    total += arm.excluded_samples;
  }
  return total;
}
export function numberText(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : "Unavailable";
}
/** The caption of a figure drawn from a table that tests nothing: its own label, or its name. */
function descriptiveCaption(table: DescriptiveTableData, name: string | undefined): string {
  const label = typeof table.label === "string" ? table.label : typeof table.endpoint === "string" ? table.endpoint : (name ?? "table");
  return `${label}. Descriptive: script-generated counts, no test.`;
}
export function figureCaption(
  name: string,
  tables: Record<string, ResultsTableData>,
) {
  const match = Object.keys(tables)
    .sort((a, b) => b.length - a.length)
    .find((stem) => name === stem || name.startsWith(`${stem}--`));
  const table = match ? tables[match] : undefined;
  return (
    table?.caption ??
    (table && !isEndpointTable(table) ? descriptiveCaption(table, match) : undefined) ??
    (table
      ? `${table.card_id} | ${table.endpoint} | ${table.test} | ${table.role}, tier ${table.tier}. ${table.available ? "Script-generated estimates; unavailable contrasts are hatched, not measured zeros." : "Endpoint unavailable; no effect can be inferred."}`
      : "Script-generated figure; table caption unavailable.")
  );
}
