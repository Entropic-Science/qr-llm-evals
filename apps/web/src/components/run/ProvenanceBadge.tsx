import type { Provenance } from "@qr-evals/contracts";
import { Badge } from "../Badge";

type SourceLabel = Provenance["source_label"];
const labels: Record<SourceLabel, string> = {
  quantum: "Quantum source",
  seeded_prng: "Seeded PRNG",
  system_prng: "System PRNG",
  seeded_prng_biased: "Biased seeded PRNG",
  provider_prng: "Provider PRNG",
};

export function ProvenanceBadge({ sourceLabel }: { sourceLabel: SourceLabel | null | undefined }) {
  if (!sourceLabel) return <Badge tone="muted">Source unavailable</Badge>;
  return <Badge tone={sourceLabel === "quantum" ? "quantum" : "control"}>{labels[sourceLabel]}</Badge>;
}
