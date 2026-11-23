import { HaltSchema, type Halt, type RunView } from "@qr-evals/contracts";

/**
 * Reading a run's stops by a result check.
 *
 * A stop is a `Halt` in `run.halts`: the harness writes its sentence, its counts and what Resume recovers, so
 * nothing here describes a rule. A halt is open until the run the host resumes stamps `resumed_at` on it.
 */

export type StopRule = Halt["rule"];

export function openHalts(halts: readonly Halt[] | null | undefined): Halt[] {
  return (halts ?? []).filter((halt) => halt.resumed_at == null);
}

/** The halt a `check.stopped` event carries, or nothing when the payload is not a halt. */
export function haltFromEvent(data: unknown): Halt | null {
  if (typeof data !== "object" || data === null) return null;
  // The event is the halt plus the run it belongs to; the record itself has no `run_id`.
  const fields = { ...(data as Record<string, unknown>) };
  delete fields.run_id;
  const parsed = HaltSchema.safeParse(fields);
  return parsed.success ? parsed.data : null;
}

const sameHalt = (a: Halt, b: Halt) => a.rule === b.rule && a.at === b.at && (a.scope_id ?? null) === (b.scope_id ?? null);

/** The run view with one more halt, so a stop shows before the next read of the run record. */
export function withHalt(view: RunView, halt: Halt): RunView {
  const halts = view.run.halts ?? [];
  if (halts.some((known) => sameHalt(known, halt))) return view;
  return { ...view, run: { ...view.run, halts: [...halts, halt] } };
}

/** Where the rule fired, in words. The harness calls an eval a card. */
export function scopeText(halt: Halt): string {
  if (halt.scope === "arm") return `Arm ${halt.scope_id ?? "unknown"}`;
  if (halt.scope === "card") return `Eval ${halt.scope_id ?? "unknown"}`;
  if (halt.scope === "phase") return halt.phase === "judge" ? "The end of judging" : "The end of generation";
  return "The whole run";
}

const PHASE_TEXT: Record<Halt["phase"], string> = {
  preflight: "before the first generation",
  generation: "during generation",
  judge: "during judging",
};
export function phaseText(halt: Halt): string {
  return PHASE_TEXT[halt.phase];
}

/** The halt's tally, largest first. For `unfinished` the keys are arm and eval pairs, otherwise causes. */
export function tally(halt: Halt): [string, number][] {
  return Object.entries(halt.causes ?? {})
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export function tallyLabel(halt: Halt): string {
  return halt.rule === "unfinished" ? "Missing, by arm and eval" : "Causes, as the harness names them";
}

/** A stored time in the reader's own zone, with the stored text kept for machines. */
export function timeText(at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? at : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** The flag kinds a `sample.finished` event carries, if any. */
export function flagKindsOfEvent(data: unknown): string[] {
  const flags = typeof data === "object" && data !== null ? (data as Record<string, unknown>).flags : undefined;
  if (!Array.isArray(flags)) return [];
  return flags.flatMap((flag: unknown) => {
    const kind = typeof flag === "object" && flag !== null ? (flag as Record<string, unknown>).kind : undefined;
    return typeof kind === "string" ? [kind] : [];
  });
}
