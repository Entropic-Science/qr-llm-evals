import type { EvalCard, StudyConfig } from "@qr-evals/contracts";

/** The judge a new study starts with: the current Claude Haiku. */
export const DEFAULT_JUDGE_MODEL = "claude-haiku-5-5";

/** Models offered by name; any other exact id can be typed in. */
export const JUDGE_MODELS: readonly { id: string; label: string; note: string }[] = [
  { id: "claude-haiku-5-5", label: "Claude Haiku 5.5", note: "Fast and the cheapest. The default." },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", note: "Stronger judgement at a higher price. Needs thinking left to the model." },
  { id: "claude-opus-5-5", label: "Claude Opus 5.5", note: "The strongest and most expensive. Needs thinking left to the model." },
];

export const DEFAULT_JUDGE: StudyConfig["judge"] = {
  provider: "anthropic", model: DEFAULT_JUDGE_MODEL, temperature: null, max_tokens: 1024, thinking: "disabled", rubric_hash: null,
};

/** True when a selected eval is scored by the study's shared judge, which runs on the user's own Anthropic key. */
export function needsStudyJudge(config: Pick<StudyConfig, "cards">, cards: readonly EvalCard[]): boolean {
  const selected = new Set(config.cards.map(({ card_id }) => card_id));
  return cards.some((card) => selected.has(card.id) && card.judge?.kind === "llm" && (card.judge.model ?? "study_judge") === "study_judge");
}
