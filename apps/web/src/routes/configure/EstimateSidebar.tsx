import type { Arm } from "@qr-evals/contracts";
import { useMemo } from "react";
import { EstimateTotals, type EvidencePreview } from "../../components/EstimateTotals";
import { draftHypotheses, familySizes, metaFor, sourceCells } from "../../lib/design";
import { estimateSelection } from "../../lib/estimates";
import { recFor, type Rec } from "../../lib/recommendations";
import { useStudy } from "./StudyContext";

/**
 * The estimate sidebar of the setup: what the study costs in tokens and time and, beside it, what its design
 * would register and whether each deciding outcome has prompts enough. It reads the shared study, so it can
 * be docked next to any view of it.
 */
export function EstimateSidebar() {
  const { config, cards, recs, goTo } = useStudy();

  const modelId = config.models[0]?.id ?? "qr-llm";
  const estimate = useMemo(() => {
    const ids = new Set(config.cards.map(({ card_id }) => card_id));
    try {
      return estimateSelection(cards.filter(({ id }) => ids.has(id)), {
        modelId,
        tpsByModel: config.estimates?.tps_by_model ?? { [modelId]: 400 },
        readingTps: config.estimates?.reading_tps,
        tiledStreams: config.estimates?.tiled_streams,
        sessionMin: config.estimates?.session_min,
        overrides: Object.fromEntries(config.cards.map((selection) => [selection.card_id, selection.overrides])),
      });
    } catch {
      // The study names no throughput for its first model, so no time can be estimated.
      return null;
    }
  }, [cards, config.cards, config.estimates, modelId]);

  const evidence = useMemo<EvidencePreview>(() => {
    const hypotheses = draftHypotheses(config, cards);
    const design = config.design;
    return {
      designed: design !== null && design !== undefined,
      hypotheses: hypotheses.length,
      comparisons: sourceCells(config).length,
      outcomes: (design?.confirmatory ?? []).map((ref) => ({
        key: `${ref.card_id}/${ref.endpoint}`,
        label: metaFor(ref.card_id, ref.endpoint).label,
        rec: recFor(recs, `cards.${ref.card_id}.items`),
      })),
      families: familySizes(hypotheses),
    };
  }, [config, cards, recs]);

  const armCount = config.arms.filter((arm: Arm) => arm.availability !== "unavailable").length;
  return <EstimateTotals
    estimate={estimate}
    modelId={modelId}
    armCount={armCount}
    supervisedDisplay={config.supervised ? config.supervised.display ?? "single" : null}
    evidence={evidence}
    onOpenSetting={(rec: Rec) => goTo({ stepId: rec.stepId, path: rec.path })}
  />;
}
