import { EvalChecklistField, PromptsTableField } from "../../../routes/configure/fields/measure";
import { definePanel } from "./Panel";

export const evalsPanel = definePanel(
  { id: "evals", section: "measures", label: "Evals", paths: ["cards[].card_id"] },
  (props) => <EvalChecklistField {...props} />,
);

export const promptsPanel = definePanel(
  { id: "prompts", section: "measures", label: "Prompts and repetitions", paths: ["cards[].overrides.k", "cards[].overrides.items"] },
  (props) => <PromptsTableField {...props} />,
);
