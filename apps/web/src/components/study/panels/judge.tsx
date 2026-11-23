import { needsStudyJudge } from "../../../lib/judge";
import {
  JudgeMaxTokensField, JudgeModelField, JudgeProviderField, JudgeTemperatureField, JudgeThinkingField, NarrativeModelField,
} from "../../../routes/configure/fields/instruments";
import { definePanel } from "./Panel";

export const judgePanel = definePanel(
  {
    id: "judge", section: "judge", label: "Judge",
    paths: ["judge.provider", "judge.model", "judge.max_tokens", "judge.thinking", "judge.temperature"],
  },
  (props) => <>
    <p className="field-note">Some evals are scored by a second model, the judge. Its settings are part of the pre-registration.</p>
    {needsStudyJudge(props.config, props.cards) ? null : <p className="validation-note">None of the selected evals uses the judge, so these settings will not be used and no Anthropic key is needed.</p>}
    <JudgeProviderField />
    <JudgeModelField {...props} />
    <JudgeMaxTokensField {...props} />
    <details className="settings-details">
      <summary>Advanced</summary>
      <JudgeThinkingField {...props} />
      <JudgeTemperatureField {...props} />
    </details>
  </>,
);

/** The narrative model is one of the study's models taken whole, so the panel claims every key of it. */
export const narrativePanel = definePanel(
  {
    id: "narrative", section: "judge", label: "Narrative", advanced: true,
    paths: ["id", "family", "provider", "served_name", "checkpoint"].map((key) => `narrative_model.${key}`),
  },
  (props) => <NarrativeModelField {...props} />,
);
