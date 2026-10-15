import type { CardOverrides, EvalCard, SelectionEstimate } from "@qr-evals/contracts";

export interface EstimateInputs {
  modelId: string;
  tpsByModel: Record<string, number | undefined>;
  readingTps?: number | undefined;
  tiledStreams?: number | undefined;
  sessionMin?: number | undefined;
  overrides?: Record<string, CardOverrides | undefined>;
}

const TOLD_CELLS = 3;
const HOURS = 3600;

export function tokensPerArm(card: EvalCard, override?: CardOverrides): number {
  if (card.max_tokens === 0 || card.est_tokens_per_sample === 0) return 0;
  const items = override?.items ?? card.dataset.items;
  const k = override?.k ?? card.k ?? 1;
  return items * k * card.est_tokens_per_sample * (card.told_actual ? TOLD_CELLS : 1);
}

export function estimateSelection(cards: readonly EvalCard[], inputs: EstimateInputs): SelectionEstimate {
  const readingTps = inputs.readingTps ?? 45;
  const tiledStreams = inputs.tiledStreams ?? 4;
  const sessionMin = inputs.sessionMin ?? 30;
  const modelTps = inputs.tpsByModel[inputs.modelId];
  if (modelTps === undefined || modelTps <= 0) throw new Error(`No positive throughput assumption for ${inputs.modelId}.`);
  const tiledTps = Math.min(tiledStreams * readingTps, modelTps);
  const perCard = cards.map((card) => {
    const override = inputs.overrides?.[card.id];
    const tokens = tokensPerArm(card, override);
    return {
      card_id: card.id,
      k: override?.k ?? card.k ?? null,
      items: override?.items ?? card.dataset.items,
      tokens_per_arm: tokens,
      time_unsupervised_h: tokens / modelTps / HOURS,
      observer_hours_single: tokens / readingTps / HOURS,
      observer_hours_tiled: tokens / tiledTps / HOURS,
    };
  });
  const tokens = perCard.reduce((sum, card) => sum + card.tokens_per_arm, 0);
  const singleHours = tokens / readingTps / HOURS;
  const tiledHours = tokens / tiledTps / HOURS;
  return {
    model_id: inputs.modelId,
    tokens_per_arm: tokens,
    time_unsupervised_h: tokens / modelTps / HOURS,
    observer_hours_single: singleHours,
    observer_hours_tiled: tiledHours,
    sessions_single: Math.ceil(singleHours * 60 / sessionMin),
    sessions_tiled: Math.ceil(tiledHours * 60 / sessionMin),
    per_card: perCard,
    assumptions: {
      model_id: inputs.modelId, tps_model: modelTps, reading_tps: readingTps,
      tiled_streams: tiledStreams, tiled_effective_tps: tiledTps, session_min: sessionMin,
      told_cells: TOLD_CELLS,
      formula: {
        tokens_per_arm: "items × k × est_tokens_per_sample × (told_cells if told_actual else 1)",
        time_unsupervised_h: "Σ tokens / tps_model / 3600",
        observer_hours_single: "Σ tokens / reading_tps / 3600",
        observer_hours_tiled: "Σ tokens / min(tiled_streams × reading_tps, tps_model) / 3600",
        sessions: "ceil(observer_hours × 60 / session_min)",
      },
      caveats: [
        "Token counts are registry estimates per arm; the pilot refines them.",
        "Judge calls, thinking-token overhead and engine queueing are not included.",
        "Throughput figures are assumptions shown to the user, not measurements, until readiness records them.",
      ],
    },
  };
}
