import type { RunView } from "@qr-evals/contracts";
import { clearRefetchSignal, reduceRunEvent, runEventState } from "./run-events";

const snapshot: RunView = {
  live: true,
  run: {
    run_id: "run_fixture",
    study_id: "study_fixture",
    kind: "full",
    state: "created",
    created_at: "2026-01-01T00:00:00Z",
    key_name: "fixture",
    host_token_hash: "0".repeat(64),
    config_hash: "1".repeat(64),
  },
};

const recorded = [
  { id: "1", type: "run.state" as const, data: { state: "running" } },
  { id: "2", type: "run.progress" as const, data: { done: 2, total: 8, by_arm: { qrng: { by_card: { poetry: { done: 2, total: 4, aborted: 0 } } } } } },
  { id: "3", type: "sample.started" as const, data: { sample_ref: "s-1", arm_id: "qrng", card_id: "poetry" } },
  { id: "4", type: "sample.finished" as const, data: { sample_ref: "s-1", provenance: { source_label: "quantum" } } },
  { id: "5", type: "taint" as const, data: { kind: "engine_fallback", sample_ref: "s-1", detail: "fallback counter advanced", at: "2026-01-01T00:01:00Z" } },
  { id: "6", type: "resync" as const, data: { dropped: 17 } },
];

describe("run event reducer", () => {
  it("reduces a recorded stream and signals a durable refetch after resync", () => {
    const reduced = recorded.reduce(reduceRunEvent, runEventState(snapshot));
    expect(reduced.view.run.state).toBe("running");
    expect(reduced.view.run.progress?.by_arm?.qrng?.by_card?.poetry?.done).toBe(2);
    expect(reduced.view.run.taints?.[0]?.kind).toBe("engine_fallback");
    expect(reduced.sourceByArm.qrng).toBe("quantum");
    expect(reduced.refetchRequired).toBe(true);
    expect(reduced.events).toHaveLength(6);
    expect(clearRefetchSignal(reduced, snapshot).refetchRequired).toBe(false);
  });
});
