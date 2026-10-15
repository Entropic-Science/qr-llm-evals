import { cards } from "@qr-evals/contracts";
import fixture from "../../../../packages/contracts/fixtures/estimates.json";
import { estimateSelection } from "./estimates";

describe("estimate parity fixtures", () => {
  for (const item of fixture.cases) it(item.name, () => {
    const selected = cards.filter((card) => item.card_ids.includes(card.id));
    const result = estimateSelection(selected, {
      modelId: item.model_id,
      tpsByModel: item.assumptions.tps_by_model,
      readingTps: item.assumptions.reading_tps,
      tiledStreams: item.assumptions.tiled_streams,
      sessionMin: item.assumptions.session_min,
      overrides: item.overrides,
    });
    expect(result.tokens_per_arm).toBe(item.expected.tokens_per_arm);
    expect(result.time_unsupervised_h).toBeCloseTo(item.expected.time_unsupervised_h, 12);
    expect(result.observer_hours_single).toBeCloseTo(item.expected.observer_hours_single, 12);
    expect(result.observer_hours_tiled).toBeCloseTo(item.expected.observer_hours_tiled, 12);
    expect(result.sessions_single).toBe(item.expected.sessions_single);
    expect(result.sessions_tiled).toBe(item.expected.sessions_tiled);
    for (const [cardId, tokens] of Object.entries(item.expected.per_card_tokens)) expect(result.per_card.find(({ card_id }) => card_id === cardId)?.tokens_per_arm).toBe(tokens);
    expect(result.assumptions).toHaveProperty("formula");
  });
});
