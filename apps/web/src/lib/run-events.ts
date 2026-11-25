import type { Progress, Provenance, RunView, Taint } from "@qr-evals/contracts";
import type { RunEvent } from "./api";

export interface RunEventState {
  view: RunView;
  events: RunEvent[];
  refetchRequired: boolean;
  sampleArms: Record<string, string>;
  sourceByArm: Record<string, Provenance["source_label"]>;
}

const MAX_RECENT_EVENTS = 80;

function object(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : undefined;
}

const runStates = new Set(["created", "ready", "running", "paused", "needs_keys", "analysing", "complete", "aborted"] as const);
type RunState = NonNullable<RunView["run"]["state"]>;
function isRunState(value: unknown): value is RunState {
  return typeof value === "string" && runStates.has(value as RunState);
}

export function runEventState(view: RunView): RunEventState {
  return { view, events: [], refetchRequired: false, sampleArms: {}, sourceByArm: {} };
}

export function reduceRunEvent(state: RunEventState, event: RunEvent): RunEventState {
  const data = object(event.data);
  const events = [...state.events, event].slice(-MAX_RECENT_EVENTS);
  if (event.type === "resync") return { ...state, events, refetchRequired: true };
  if (event.type === "sample.started" && typeof data?.sample_ref === "string" && typeof data.arm_id === "string") {
    return { ...state, events, sampleArms: { ...state.sampleArms, [data.sample_ref]: data.arm_id } };
  }
  if (event.type === "sample.finished" && typeof data?.sample_ref === "string") {
    const armId = state.sampleArms[data.sample_ref];
    const source = object(data.provenance)?.source_label;
    if (armId && (source === "quantum" || source === "seeded_prng" || source === "system_prng" || source === "seeded_prng_biased" || source === "resampled_prng" || source === "provider_prng")) {
      return { ...state, events, sourceByArm: { ...state.sourceByArm, [armId]: source } };
    }
  }
  if (event.type === "run.state" && isRunState(data?.state)) {
    return { ...state, events, view: { ...state.view, run: { ...state.view.run, state: data.state }, control_state: data.state } };
  }
  if (event.type === "run.progress" && data?.by_arm && typeof data.by_arm === "object") {
    const progress: Progress = { by_arm: data.by_arm as NonNullable<Progress["by_arm"]> };
    return { ...state, events, view: { ...state.view, run: { ...state.view.run, progress } } };
  }
  if (event.type === "taint" && typeof data?.kind === "string" && typeof data.at === "string") {
    const taint = data as unknown as Taint;
    const taints = [...(state.view.run.taints ?? []), taint];
    return { ...state, events, view: { ...state.view, run: { ...state.view.run, taints } } };
  }
  return { ...state, events };
}

export function clearRefetchSignal(state: RunEventState, view: RunView): RunEventState {
  return { ...state, view, refetchRequired: false };
}
