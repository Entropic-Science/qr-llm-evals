import { HaltSchema, HealthSummarySchema, type Halt, type HealthSummary, type RunView } from "@qr-evals/contracts";
import raw from "./checks-fixture.json";

/**
 * Halts and health summaries for tests, as the backend itself writes them.
 *
 * `checks-fixture.json` is produced by a script that drives the catalogue's `Breaker` until each stop rule
 * fires and reads `store.health.summarise` back from run folders written with the store. Nothing in it is
 * typed by hand, so a test that passes here passes on sentences and counts the harness really produces.
 * To refresh it after a change to `cards/result_checks.py` or `store/health.py`, run from the repo root:
 *
 *   python scripts/gen_checks_fixture.py
 *
 * Both records are parsed against their contracts at load, so a drifted fixture fails every test that uses it.
 */

/** One halt for every stop rule and every clause of a rule, keyed `rule/clause`. */
export const HALTS: Record<string, Halt> = Object.fromEntries(Object.entries(raw.halts).map(([key, halt]) => [key, HaltSchema.parse(halt)]));

/** Every stop rule of the catalogue, in its order. */
export const RULES: readonly string[] = raw.rules;

export type SummaryName = keyof typeof raw.summaries;
/**
 * `complete`: a finished run that was stopped twice and resumed twice, with flags and two cautions.
 * `stopped`: a paused run with one resumed stop and one open stop.
 * `legacy`: a run made before result checks. `fresh`: a run that has stored nothing yet.
 */
export const SUMMARIES = Object.fromEntries(Object.entries(raw.summaries).map(([name, summary]) => [name, HealthSummarySchema.parse(summary)])) as Record<SummaryName, HealthSummary>;

export function halt(key: string, overrides: Partial<Halt> = {}): Halt {
  const found = HALTS[key];
  if (!found) throw new Error(`no fixture halt ${key}`);
  return { ...found, ...overrides };
}

/** A run view around the given run fields. */
export function runView(run: Partial<RunView["run"]> = {}, view: Partial<Omit<RunView, "run">> = {}): RunView {
  return {
    live: false,
    subscribers: 0,
    ...view,
    run: {
      run_id: "run-fixture",
      study_id: "study-fixture",
      kind: "full",
      state: "paused",
      created_at: "2026-11-22T12:00:00Z",
      key_name: "fixture",
      host_token_hash: "0".repeat(64),
      config_hash: "1".repeat(64),
      checks_version: raw.checks_version,
      halts: [],
      ...run,
    },
  };
}
